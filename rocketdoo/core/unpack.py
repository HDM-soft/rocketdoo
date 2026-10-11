"""Environment restoration: shared restore primitives, and the
non-interactive `inspect()`/`unpack()` services the GUI calls.

Moved out of `unpack_environment.py` in #143 T13, mirroring `core/pack.py`
(T11): port-conflict detection, backup lookup, SSH wiring, the
database/filestore restore chain and the post-launch health check live here
rather than in the CLI, because a caller with no terminal (the GUI) still
needs every one of them.

`unpack_environment.py`'s Click command keeps its own interactive wizard --
which questions get asked, in what order, and how the result reads on
screen -- but imports every function below instead of redefining any of
them, so `rkd unpack` and `unpack()` share one implementation of every step
that touches disk or Docker.
"""

import json
import re
import subprocess
import time
from pathlib import Path

from rocketdoo.core.compose import compose_ls, run_compose, run_compose_result
from rocketdoo.core.mailpit import is_enabled as mailpit_is_enabled
from rocketdoo.core.pack import BACKUP_FILENAME
from rocketdoo.core.port_validation import find_available_port, is_port_in_use
from rocketdoo.core.project_info import project_exists, read_docker_compose
from rocketdoo.core.service import ProgressCallback, ServiceError, reporter
from rocketdoo.core.ssh_manager import copy_key_to_build_context, inject_ssh_into_dockerfile, list_private_keys


class UnpackError(ServiceError):
    """The environment cannot be unpacked as requested."""


# ─── low-level helpers ───────────────────────────────────────────────────────


def _load_shared_meta(project_dir: Path) -> dict | None:
    """Loads rkd-shared.json, or None if it is missing or not valid JSON."""
    meta_path = project_dir / "rkd-shared.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return None


def _find_backup_files(project_dir: Path) -> tuple[Path | None, Path | None]:
    """Most recent (db dump, filestore tar) pair under rkd_backups/, each
    reported independently as None rather than the function ever guessing.
    """
    backup_dir = project_dir / "rkd_backups"
    if not backup_dir.exists():
        return None, None

    # Filtered by the same pattern pack writes, not by a looser glob: a file
    # the user happens to keep here ("db_notes.dump") sorts by name like any
    # other and could win the "newest" slot, sending pg_restore after a
    # database that does not exist -- which also skips the filestore, since
    # that step is nested under a successful restore.
    dumps = sorted((p for p in backup_dir.glob("db_*.dump") if BACKUP_FILENAME.match(p.name)), reverse=True)
    filestores = sorted((p for p in backup_dir.glob("filestore_*.tar.gz") if BACKUP_FILENAME.match(p.name)), reverse=True)
    return (dumps[0] if dumps else None), (filestores[0] if filestores else None)


def _check_ports(meta: dict, auto_accept: bool = True, *, name_taken: bool = False) -> tuple[int, int, bool]:
    """Detects port conflicts and returns the ports that should be used.

    Always non-interactive: RF3 moved the branch this used to have in
    unpack_environment.py (questionary.confirm / click.prompt, asking which
    port to pick) to the CLI, since core/ may never prompt (RF1.3).
    `auto_accept` stays a parameter only because it is part of this
    function's existing, characterised signature; the computation below is
    the same regardless of its value.

    `name_taken` says another project on this host already answers to the
    name in the compose, so these ports are its ports and they move whether
    or not something is listening on them right now (#238). `is_port_in_use`
    only sees live listeners: an original stopped with `rkd down` leaves 8069
    free, the copy took it, and the original then could not start --
    `Bind for 0.0.0.0:8069 failed: port is already allocated`. Same blind
    spot #205 closed for the Compose identity by reading the volume labels,
    which survive a `down`; here the project name is what survives.
    """
    odoo_port = int(meta.get("odoo_port") or 8069)
    vsc_port = int(meta.get("vsc_port") or 8888)
    changed = False

    if name_taken or is_port_in_use(odoo_port):
        odoo_port = find_available_port(odoo_port + 1)
        changed = True

    if name_taken or is_port_in_use(vsc_port):
        vsc_port = find_available_port(vsc_port + 1)
        changed = True

    return odoo_port, vsc_port, changed


def _update_ports_in_compose(project_dir: Path, new_odoo_port: int, new_vsc_port: int) -> None:
    """Rewrites only the two port mappings docker-compose.yaml carries for
    Odoo and its debugger, in place, leaving the rest of the file untouched.

    Bytes in, bytes out, because the text helpers translate newlines on both
    sides. `read_text()` applies universal newlines and hands back a string
    with every CRLF already collapsed to LF, so remapping one port in a
    compose saved with CRLF - a Windows editor, or git's own core.autocrlf,
    on a tool whose users are largely on WSL - rewrote every line in the file.
    That is the half measured here. `write_text()` is the mirror image and
    only bites on a host where os.linesep is CRLF, which is why reverting it
    alone changes nothing on Linux and no test can pin it from here.
    Line endings are not ours to change; #196 reached the same conclusion for
    the generated workflows.
    """
    compose_path = project_dir / "docker-compose.yaml"
    if not compose_path.exists():
        compose_path = project_dir / "docker-compose.yml"
    if not compose_path.exists():
        return

    content = compose_path.read_bytes().decode()
    content = re.sub(r'"\d+:8069"', f'"{new_odoo_port}:8069"', content)
    content = re.sub(r'"\d+:8888"', f'"{new_vsc_port}:8888"', content)
    compose_path.write_bytes(content.encode())


# `\r?` because a compose saved with CRLF leaves one before the line end, and
# `[ \t]*$` does not match it: the name came back as None, so every CRLF
# compose looked like one without a `name:` and the whole conflict check
# quietly did nothing on it.
_PROJECT_NAME_LINE = re.compile(r"^name:[ \t]*(\S+)[ \t]*\r?$", re.MULTILINE)
_PROJECT_NAME_MAX_SUFFIX = 99
_VALID_PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_MAILPIT_PORTS = (1025, 8025)


def _compose_file(project_dir: Path) -> Path | None:
    for name in ("docker-compose.yaml", "docker-compose.yml"):
        candidate = project_dir / name
        if candidate.exists():
            return candidate
    return None


def _compose_project_name(project_dir: Path) -> str | None:
    """Value of the top-level `name:` line, read as text so it is the same
    expression that later rewrites it.
    """
    compose_path = _compose_file(project_dir)
    if compose_path is None:
        return None
    match = _PROJECT_NAME_LINE.search(compose_path.read_bytes().decode())
    if not match:
        return None
    # YAML quotes are not part of the name. Read raw, `name: "demo"` came back
    # as '"demo"', which never matched the manifest's parsed `demo`, so the
    # copy was taken for one already renamed and the inventory was never
    # consulted at all.
    return match.group(1).strip("\"'") or None


def _docker_inventory() -> dict:
    """{"ok": bool, "projects": {name: [config_files]}, "volumes": {name: project_label}}.

    Any failure in either query yields `ok: False` with empty collections, so
    a partial answer can never be read as "the name is free".
    """
    failed = {"ok": False, "projects": {}, "volumes": {}}

    entries, error = compose_ls("--all")
    if error:
        return failed

    projects = {}
    for entry in entries:
        name = entry.get("Name")
        if name:
            files = str(entry.get("ConfigFiles") or "")
            projects[name] = [f.strip() for f in files.split(",") if f.strip()]

    try:
        result = subprocess.run(
            ["docker", "volume", "ls", "--format", '{{.Name}}\t{{.Label "com.docker.compose.project"}}'],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return failed
    if result.returncode != 0:
        return failed

    volumes = {}
    for line in result.stdout.splitlines():
        name, _, label = line.partition("\t")
        if name.strip():
            volumes[name.strip()] = label.strip()

    return {"ok": True, "projects": projects, "volumes": volumes}


def _own_projects(root: Path, inventory: dict) -> set[str]:
    """Compose projects whose config file lives directly inside `root`."""
    resolved = Path(root).resolve()
    return {name for name, files in inventory["projects"].items() if any(Path(f).resolve().parent == resolved for f in files)}


def _name_taken_reason(name: str, own: set[str], inventory: dict) -> str:
    """Why `name` is already used by something that is not this copy, or ''."""
    if name in inventory["projects"] and name not in own:
        files = inventory["projects"][name]
        where = f" in {Path(files[0]).parent}" if files else ""
        return f"the Compose project '{name}' already exists{where}"
    # Matched on the label, not on the volume's name. Compose names a project
    # volume `{project}_{key}`, so a prefix test for `{name}-` never fires on a
    # real one -- and this is the half that covers the case that actually
    # destroys data: an original stopped with `rkd down` (no -v) is gone from
    # `compose ls` but its volumes are still here, and a copy that reuses the
    # project would mount them and let the restore DROP the original's
    # database. The label is what Compose puts there to answer exactly this.
    for volume, label in inventory["volumes"].items():
        if label == name and label not in own:
            return f"the Docker volume '{volume}' already belongs to the Compose project '{name}'"
    return ""


def _resolve_project_name(root: Path, current: str | None, meta: dict, inventory: dict) -> dict:
    """Decide which Compose project name the copy should use. Writes nothing
    and does not query Docker: the inventory is passed in.
    """
    if current is None:
        return {
            "name": None,
            "conflict": False,
            "reason": "the compose file has no 'name:'; renaming only the containers would damage the other copy",
            "renamable": False,
        }

    meta_name = (meta or {}).get("project_name")
    if meta_name and meta_name != current:
        return {"name": current, "conflict": False, "reason": "", "renamable": True}

    if not inventory["ok"]:
        return {
            "name": current,
            "conflict": None,
            "reason": "Docker could not be queried, so name conflicts were not checked",
            "renamable": True,
        }

    own = _own_projects(root, inventory)
    reason = _name_taken_reason(current, own, inventory)
    if not reason:
        return {"name": current, "conflict": False, "reason": "", "renamable": True}

    for suffix in range(2, _PROJECT_NAME_MAX_SUFFIX + 1):
        candidate = f"{current}-{suffix}"
        if not _name_taken_reason(candidate, own, inventory):
            return {"name": candidate, "conflict": True, "reason": reason, "renamable": True}

    return {
        "name": current,
        "conflict": True,
        "reason": f"{reason}, and every name from '{current}-2' to '{current}-{_PROJECT_NAME_MAX_SUFFIX}' is taken too",
        "renamable": True,
    }


def _rename_project_in_compose(project_dir: Path, old: str, new: str) -> bool:
    """Rewrites the Compose identity (project name, image, container names and
    named volumes) in place as text, line by line. PyYAML would drop the
    `# rkd:mailpit` markers, and every line that is not identity must stay
    byte for byte the same.

    All or nothing. A compose whose top-level `name:` does not say `old` is
    not renamed at all, not even its `container_name` lines: a copy that
    renames only its containers while sharing the project name is the
    destructive case, not a partial improvement on it. Compose then treats the
    other copy's containers as this project's, recreates them and mounts its
    volumes. Measured, not inferred. `unpack()` refuses the same case one
    level up; this guard is here so no future caller can reach past it.
    """
    compose_path = _compose_file(project_dir)
    if compose_path is None:
        return False
    if _compose_project_name(project_dir) != old:
        return False

    token = re.compile(r"(?<![A-Za-z0-9_.])" + re.escape(old) + r"(?![A-Za-z0-9_.])")
    value_line = re.compile(r"^(\s*#?\s*(?:-\s+)?(?:name|image):[ \t]*)(\S+)([ \t]*\r?\n?)$")
    body_prefix = re.compile(r"^\s*#?\s*(?:-\s+)?")

    original = compose_path.read_bytes().decode()
    lines = []
    for line in original.splitlines(keepends=True):
        body = body_prefix.sub("", line)
        if body.startswith(("name:", "image:")):
            match = value_line.match(line)
            if match and match.group(2) == old:
                line = f"{match.group(1)}{new}{match.group(3)}"
        elif body.startswith("container_name:") or body.startswith(f"{old}-"):
            line = token.sub(new, line)
        lines.append(line)

    updated = "".join(lines)
    if updated == original:
        return False
    compose_path.write_bytes(updated.encode())
    return True


def _update_db_host_in_conf(project_dir: Path, old_host: str, new_host: str) -> bool:
    """Points `db_host` at the renamed database container, but only when it
    still holds the old container name: `db` or an external host is the
    user's, not ours.
    """
    conf_path = project_dir / "config" / "odoo.conf"
    if not conf_path.exists():
        return False
    pattern = re.compile(r"^(db_host\s*=\s*)" + re.escape(old_host) + r"([ \t]*\r?)$", re.MULTILINE)
    original = conf_path.read_bytes().decode()
    updated = pattern.sub(lambda m: f"{m.group(1)}{new_host}{m.group(2)}", original)
    if updated == original:
        return False
    conf_path.write_bytes(updated.encode())
    return True


def _get_db_container_name(project_dir: Path) -> str | None:
    """Reads docker-compose and returns the database container name."""
    compose_data = read_docker_compose(project_dir)
    if compose_data:
        try:
            return compose_data["services"]["db"]["container_name"]
        except (KeyError, TypeError):
            pass
    return None


def _get_odoo_container_name(project_dir: Path) -> str | None:
    """Reads docker-compose and returns the web container name."""
    compose_data = read_docker_compose(project_dir)
    if compose_data:
        try:
            return compose_data["services"]["web"]["container_name"]
        except (KeyError, TypeError):
            pass
    return None


def _is_container_running(container_name: str | None) -> bool:
    """Checks whether a Docker container is currently running."""
    if not container_name:
        return False
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container_name], capture_output=True, text=True
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def _wait_for_postgres(db_container: str, report: ProgressCallback, max_wait: int = 60) -> bool:
    """Polls pg_isready, reporting progress every 10s (RF9.3 keeps the cadence)."""
    report(f"Waiting for PostgreSQL to be ready (max {max_wait}s)...")
    for i in range(max_wait):
        result = subprocess.run(["docker", "exec", db_container, "pg_isready", "-U", "root"], capture_output=True)
        if result.returncode == 0:
            report("PostgreSQL is ready.", "ok")
            return True
        time.sleep(1)
        if i % 10 == 9:
            report(f"Still waiting for PostgreSQL... {i + 1}s")
    return False


def _wait_for_odoo_volume(odoo_container: str, report: ProgressCallback, max_wait: int = 90) -> bool:
    """Polls for /var/lib/odoo, reporting progress every 15s (RF9.3 keeps the
    cadence). Replaces a fixed sleep: the volume may take a few seconds to
    mount, especially on first run, and restoring the filestore against a
    path that is not there yet would silently fail.
    """
    for i in range(max_wait):
        result = subprocess.run(["docker", "exec", odoo_container, "test", "-d", "/var/lib/odoo"], capture_output=True)
        if result.returncode == 0:
            report("Odoo volume is ready.", "ok")
            return True
        time.sleep(1)
        if i % 15 == 14:
            report(f"Still waiting for the Odoo volume... {i + 1}s")
    report(f"Odoo volume not ready after {max_wait}s.", "warn")
    return False


def _restore_database(db_container: str, dump_path: Path, report: ProgressCallback) -> str | None:
    """Restores the PostgreSQL dump into the container.

    Returns the restored database name, or None if it failed.
    """
    stem = dump_path.stem
    parts = stem.split("_")
    db_name = "_".join(parts[1:-2]) if len(parts) >= 4 else (parts[1] if len(parts) > 1 else "odoo_restored")

    report(f"Restoring database {db_name}...")

    try:
        copy_result = subprocess.run(
            ["docker", "cp", str(dump_path), f"{db_container}:/tmp/rkd_restore.dump"], capture_output=True, text=True
        )
        if copy_result.returncode != 0:
            report(f"Error copying dump: {copy_result.stderr}", "warn")
            return None

        subprocess.run(
            [
                "docker",
                "exec",
                db_container,
                "psql",
                "-U",
                "root",
                "-d",
                "postgres",
                "-c",
                f'DROP DATABASE IF EXISTS "{db_name}";',
            ],
            capture_output=True,
        )
        create_result = subprocess.run(
            [
                "docker",
                "exec",
                db_container,
                "psql",
                "-U",
                "root",
                "-d",
                "postgres",
                "-c",
                f'CREATE DATABASE "{db_name}" OWNER root;',
            ],
            capture_output=True,
            text=True,
        )
        if create_result.returncode != 0:
            report(f"Error creating database: {create_result.stderr}", "warn")
            return None

        restore_result = subprocess.run(
            [
                "docker",
                "exec",
                db_container,
                "pg_restore",
                "-U",
                "root",
                "-d",
                db_name,
                "--no-owner",
                "--role=root",
                "/tmp/rkd_restore.dump",
            ],
            capture_output=True,
            text=True,
        )

        if restore_result.returncode == 0:
            report(f"Database {db_name} restored successfully.", "ok")
            return db_name

        # returncode 1 = restored, but pg_restore ignored some statements.
        # These are often benign (missing roles/extensions), but can also hide
        # real data loss, so they are surfaced instead of a silent success.
        if restore_result.returncode == 1:
            stderr = (restore_result.stderr or "").strip()
            report(f"Database {db_name} restored with warnings (pg_restore ignored some statements).", "warn")
            if stderr:
                for line in stderr.splitlines()[-15:]:
                    report(line, "warn")
            return db_name

        # returncode > 1 = fatal failure
        report(f"Restore error: {restore_result.stderr[:500]}", "warn")
        return None

    except Exception as exc:
        report(f"Exception during restore: {exc}", "warn")
        return None


def _restore_filestore(
    odoo_container: str,
    filestore_tar: Path,
    db_name: str,
    filestore_base_from_meta: str | None,
    report: ProgressCallback,
) -> bool:
    """Restores the Odoo filestore into the web container (supports multiple
    layouts). Uses filestore_base_from_meta if provided (from
    rkd-shared.json), otherwise detects it.
    """
    possible_bases = [
        "/var/lib/odoo/.local/share/Odoo/filestore",
        "/var/lib/odoo/filestore",
    ]

    report(f"Restoring filestore for {db_name}...")

    try:
        copy_result = subprocess.run(
            ["docker", "cp", str(filestore_tar), f"{odoo_container}:/tmp/rkd_filestore.tar.gz"],
            capture_output=True,
            text=True,
        )
        if copy_result.returncode != 0:
            report(f"Could not copy filestore: {copy_result.stderr}", "warn")
            return False

        filestore_base = None

        if filestore_base_from_meta:
            check = subprocess.run(
                ["docker", "exec", odoo_container, "test", "-d", filestore_base_from_meta], capture_output=True
            )
            if check.returncode == 0:
                filestore_base = filestore_base_from_meta
                report(f"Using filestore base from metadata: {filestore_base}")

        if not filestore_base:
            for base in possible_bases:
                check = subprocess.run(["docker", "exec", odoo_container, "test", "-d", base], capture_output=True)
                if check.returncode == 0:
                    filestore_base = base
                    break

        if not filestore_base:
            filestore_base = "/var/lib/odoo/filestore"
            report(f"Filestore base not found. Using fallback: {filestore_base}", "warn")

        report(f"Using filestore base: {filestore_base}")

        subprocess.run(["docker", "exec", odoo_container, "mkdir", "-p", filestore_base], capture_output=True)

        report(f"Cleaning existing filestore for {db_name}...")
        subprocess.run(["docker", "exec", odoo_container, "rm", "-rf", f"{filestore_base}/{db_name}"], capture_output=True)

        extract_result = subprocess.run(
            ["docker", "exec", odoo_container, "tar", "-xzf", "/tmp/rkd_filestore.tar.gz", "-C", filestore_base],
            capture_output=True,
            text=True,
        )

        # tar exits 1 on non-fatal warnings (extended headers, etc.) - treat as success
        if extract_result.returncode > 1:
            report(f"Error extracting filestore: {extract_result.stderr}", "warn")
            return False

        subprocess.run(
            ["docker", "exec", odoo_container, "chown", "-R", "odoo:odoo", f"{filestore_base}/{db_name}"], capture_output=True
        )

        report("Filestore restored successfully.", "ok")
        return True

    except Exception as exc:
        report(f"Exception while restoring filestore: {exc}", "warn")
        return False


def _clear_generated_assets(db_container: str, db_name: str, report: ProgressCallback) -> None:
    """Removes generated web asset bundles (ir_attachment rows whose URL
    starts with /web/assets/) from the restored database.

    After a restore, the asset bundles registered in the database point to
    filestore files that no longer match the recipient's code (different
    addons, enterprise version, etc.) or were replaced by the filestore
    restore. Odoo regenerates these bundles on demand, so deleting the stale
    records forces a clean rebuild and prevents the 500 errors on
    /web/assets/... that render the login page non-functional.
    """
    report("Clearing stale web asset bundles...")
    result = subprocess.run(
        [
            "docker",
            "exec",
            db_container,
            "psql",
            "-U",
            "root",
            "-d",
            db_name,
            "-c",
            "DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%';",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        report("Stale asset bundles cleared (Odoo will regenerate them).", "ok")
    else:
        report(f"Could not clear asset bundles: {result.stderr.strip()[:200]}", "warn")


def _launch_environment(project_dir: Path, build: bool, report: ProgressCallback) -> bool:
    """Runs docker compose up -d (with optional --build), inheriting stdio:
    a rebuild's own output is what the user watches for, the same exception
    RF5.4 already carves out for `rkd up`/`rkd logs -f`.
    """
    args = ["up", "-d"] + (["--build"] if build else [])
    report(f"Starting environment: docker compose {' '.join(args)}")
    return run_compose(*args, cwd=project_dir) == 0


def _launch_db_only(project_dir: Path, report: ProgressCallback) -> bool:
    """Starts only the db service to allow database restoration."""
    report("Starting database service only...")
    # No timeout: on the machine receiving a shared environment this is the
    # call that pulls the PostgreSQL image, which the pre-#143 code also let
    # run uncapped. A cap here kills the pull and the restore never happens.
    return run_compose_result("up", "-d", "db", cwd=project_dir, timeout=None)["ok"]


def _init_odoo_volume(project_dir: Path, web_container: str, report: ProgressCallback) -> bool:
    """Starts the web container briefly so Docker creates the named volume,
    then stops it immediately so Odoo never runs while the filestore below
    is being restored.
    """
    report("Starting web container to initialize volume...")
    # Same reason as _launch_db_only, more so: on a first unpack this builds
    # the project image (apt-get, pipx), which takes minutes.
    run_compose_result("up", "-d", "web", cwd=project_dir, timeout=None)
    time.sleep(3)
    ready = False
    for _ in range(30):
        result = subprocess.run(["docker", "exec", web_container, "test", "-d", "/var/lib/odoo"], capture_output=True)
        if result.returncode == 0:
            ready = True
            break
        time.sleep(1)
    run_compose_result("stop", "web", cwd=project_dir)
    return ready


def _capture_logs_tail(container_name: str, lines: int = 30) -> list[str]:
    """The last lines of a container's combined stdout/stderr, for a failure
    report to show without a caller needing its own terminal (RF9.4).
    """
    result = subprocess.run(["docker", "logs", "--tail", str(lines), container_name], capture_output=True, text=True)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return output.splitlines() if output else []


# ─── read functions ──────────────────────────────────────────────────────────


def _effective_meta(raw_meta: dict) -> dict:
    """The manifest a caller may trust: `{}` unless it carries `rkd_shared`.

    A file that exists but was not written by `rkd pack` (or failed to
    parse) must not leak stray keys like a stale `odoo_port` into the ports
    or SSH decisions below - the same reset unpack_environment.py already
    did before this epic, once meta.get("rkd_shared") came back false.
    """
    return raw_meta if raw_meta.get("rkd_shared") else {}


def inspect(project_root: Path | str | None = None) -> dict:
    """Everything unpack_environment.py needs to decide what to ask.

    Never writes anything and never prompts (RF9.2): a missing or corrupt
    rkd-shared.json reads as `has_meta: False` rather than raising (case 7 of
    spec.md) -- the CLI decides whether to continue anyway.
    """
    root = Path(project_root) if project_root else Path.cwd()
    raw_meta = _load_shared_meta(root) or {}
    has_meta = bool(raw_meta.get("rkd_shared"))
    meta = _effective_meta(raw_meta)

    dump, filestore = _find_backup_files(root)

    # The project has to be resolved before the ports: whether its name is
    # taken is what decides if these ports belong to someone else (#238).
    current_name = _compose_project_name(root)
    inventory = _docker_inventory() if current_name else {"ok": False, "projects": {}, "volumes": {}}
    resolved = _resolve_project_name(root, current_name, meta, inventory)

    original_odoo_port = int(meta.get("odoo_port") or 8069)
    original_vsc_port = int(meta.get("vsc_port") or 8888)
    # `conflict` is None when Docker could not answer: no guessing, fall back
    # to what is listening.
    ports_reserved = bool(resolved["conflict"])
    suggested_odoo_port, suggested_vsc_port, _ = _check_ports(meta, name_taken=ports_reserved)

    return {
        "meta": raw_meta,
        "has_meta": has_meta,
        "port_conflicts": {
            "odoo_port": suggested_odoo_port != original_odoo_port,
            "vsc_port": suggested_vsc_port != original_vsc_port,
        },
        "suggested_ports": {
            "odoo_port": suggested_odoo_port,
            "vsc_port": suggested_vsc_port,
        },
        # True when the ports moved because another project claims them, not
        # because something is listening. The CLI says so instead of
        # reporting a port "already in use" that anyone can see is free.
        "ports_reserved": ports_reserved,
        "backups": {
            "db_dump": str(dump) if dump else None,
            "filestore": str(filestore) if filestore else None,
        },
        "uses_private_repos": bool(meta.get("uses_private_repos")),
        "ssh_keys": list_private_keys(),
        "project": {
            "name": current_name,
            "suggested_name": resolved["name"],
            "conflict": resolved["conflict"],
            "reason": resolved["reason"],
            "renamable": resolved["renamable"],
        },
        "mailpit_port_conflict": mailpit_is_enabled(root) and any(is_port_in_use(p) for p in _MAILPIT_PORTS),
    }


# ─── action functions ────────────────────────────────────────────────────────


def unpack(
    project_root: Path | str,
    *,
    ports: dict | None = None,
    project_name: str | None = None,
    ssh_key: str | None = None,
    restore: bool = True,
    build: bool = False,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Starts a previously packed environment.

    Never prompts (RF9.2): `ports` and `ssh_key` arrive already decided by
    the caller (`project_name` too: the Compose project name the copy should
    take, or None to keep the one in the compose). When a database backup is present and `restore` is True, the
    database and filestore are restored before the full environment starts;
    `restore=False` (`--no-restore`) skips that whole branch even when a
    backup exists, since a step failing mid-restore must never look like it
    happened (RF1.4).
    """
    root = Path(project_root)
    report = reporter(on_progress)

    if not project_exists(root):
        raise UnpackError(
            "No Rocketdoo project detected in this directory.",
            "Make sure you are inside the unzipped environment directory.",
        )

    if project_name is not None and not _VALID_PROJECT_NAME.match(project_name):
        raise UnpackError(
            f"'{project_name}' is not a valid Compose project name.",
            "Use lowercase letters, digits, '-' and '_', starting with a letter or digit.",
        )

    meta = _effective_meta(_load_shared_meta(root) or {})
    original_odoo_port = int(meta.get("odoo_port") or 8069)
    original_vsc_port = int(meta.get("vsc_port") or 8888)
    ports = ports or {}
    odoo_port = int(ports.get("odoo_port", original_odoo_port))
    vsc_port = int(ports.get("vsc_port", original_vsc_port))
    ports_changed = odoo_port != original_odoo_port or vsc_port != original_vsc_port

    warnings: list[str] = []

    if ports_changed:
        _update_ports_in_compose(root, odoo_port, vsc_port)
        report("docker-compose.yaml updated with new ports.", "ok")

    current_name = _compose_project_name(root)
    renamed_from = None
    if project_name and current_name is None:
        message = "The compose file has no 'name:', so the project was not renamed."
        report(message, "warn")
        warnings.append(message)
    elif project_name and project_name != current_name:
        old_db_host = _get_db_container_name(root)
        if _rename_project_in_compose(root, current_name, project_name):
            renamed_from = current_name
            report(f"Compose project renamed from '{current_name}' to '{project_name}'.", "ok")
            new_db_host = _get_db_container_name(root)
            if old_db_host and new_db_host:
                if _update_db_host_in_conf(root, old_db_host, new_db_host):
                    report(f"config/odoo.conf db_host updated to {new_db_host}.", "ok")
            else:
                message = "Could not read the renamed database container name; check db_host in config/odoo.conf."
                report(message, "warn")
                warnings.append(message)
            leftovers = []
            if (root / "docker-compose.override.yml").exists():
                leftovers.append(
                    "docker-compose.override.yml still carries the Traefik labels of the original copy; review them."
                )
            if mailpit_is_enabled(root):
                leftovers.append("Mailpit is enabled: ports 1025 and 8025 are not remapped and may clash with the other copy.")
            for message in leftovers:
                report(message, "warn")
                warnings.append(message)

    ssh_configured = False
    if ssh_key:
        try:
            dockerfile_path = root / "Dockerfile"
            copy_key_to_build_context(ssh_key, root)
            if dockerfile_path.exists():
                inject_ssh_into_dockerfile(dockerfile_path, ssh_key)
            ssh_configured = True
            report(f"Dockerfile configured with key {ssh_key}.", "ok")
        except Exception as exc:
            message = f"Error configuring SSH: {exc}"
            report(message, "warn")
            warnings.append(message)

    db_dump, filestore_tar = _find_backup_files(root)
    if db_dump is not None and meta.get("has_db_backup") is False:
        # The manifest states this environment was packed without a database,
        # so the dump under rkd_backups/ is left over from an earlier pack of
        # the same project and restoring it would hand the recipient data the
        # sender never meant to send (#225). Packs from 3.8.1 on no longer
        # ship it; this covers the ZIPs already in circulation.
        #
        # Only an explicit False counts. A manifest without the key is older
        # or hand-made and has no opinion, and there the dump is trusted.
        message = (
            f"The manifest says this environment was packed without a database, so {db_dump.name} "
            "is left over from an earlier pack and is not restored. Restore it by hand if you meant to receive it."
        )
        report(message, "warn")
        warnings.append(message)
        db_dump = None

    db_restored = False
    filestore_restored = False

    if db_dump is not None and restore:
        report("Database backup found. Strategy: start DB, restore, then start the full environment.")

        db_container = _get_db_container_name(root)
        if not _launch_db_only(root, report):
            message = "Could not start the database service."
            report(message, "warn")
            warnings.append(message)
        elif db_container and _wait_for_postgres(db_container, report):
            restored_db = _restore_database(db_container, db_dump, report)
            db_restored = restored_db is not None

            if restored_db and filestore_tar:
                odoo_container = _get_odoo_container_name(root)
                if not odoo_container:
                    message = (
                        "Cannot determine the Odoo container name - filestore restore skipped. "
                        "Check that docker-compose.yaml has container_name set on the web service."
                    )
                    report(message, "warn")
                    warnings.append(message)
                else:
                    volume_ready = _init_odoo_volume(root, odoo_container, report)
                    if volume_ready:
                        filestore_restored = _restore_filestore(
                            odoo_container, filestore_tar, restored_db, meta.get("filestore_base"), report
                        )
                    else:
                        message = "Skipping filestore restore: the Odoo volume was not ready in time."
                        report(message, "warn")
                        warnings.append(message)

            if restored_db:
                _clear_generated_assets(db_container, restored_db, report)

    launched = _launch_environment(root, build, report)

    # docker compose up -d returning 0 does not guarantee Odoo booted: the web
    # container can crash right after start (missing addons, build issues,
    # etc.), so the volume needs a moment before the check below means anything.
    time.sleep(3)
    odoo_container = _get_odoo_container_name(root)
    web_running = _is_container_running(odoo_container)
    started = bool(launched and web_running)

    logs_tail: list[str] = []
    if not started and odoo_container:
        logs_tail = _capture_logs_tail(odoo_container)

    return {
        "ports": {"odoo_port": odoo_port, "vsc_port": vsc_port},
        "ports_changed": ports_changed,
        "project_name": _compose_project_name(root),
        "project_renamed": renamed_from is not None,
        "renamed_from": renamed_from,
        "ssh_configured": ssh_configured,
        "db_restored": db_restored,
        "filestore_restored": filestore_restored,
        "started": started,
        "launched": launched,
        "odoo_container": odoo_container,
        "warnings": warnings,
        "logs_tail": logs_tail,
    }
