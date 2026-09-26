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

from rocketdoo.core.compose import run_compose, run_compose_result
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

    dumps = sorted(backup_dir.glob("db_*.dump"), reverse=True)
    filestores = sorted(backup_dir.glob("filestore_*.tar.gz"), reverse=True)
    return (dumps[0] if dumps else None), (filestores[0] if filestores else None)


def _check_ports(meta: dict, auto_accept: bool = True) -> tuple[int, int, bool]:
    """Detects port conflicts and returns the ports that should be used.

    Always non-interactive: RF3 moved the branch this used to have in
    unpack_environment.py (questionary.confirm / click.prompt, asking which
    port to pick) to the CLI, since core/ may never prompt (RF1.3).
    `auto_accept` stays a parameter only because it is part of this
    function's existing, characterised signature; the computation below is
    the same regardless of its value.
    """
    odoo_port = int(meta.get("odoo_port") or 8069)
    vsc_port = int(meta.get("vsc_port") or 8888)
    changed = False

    if is_port_in_use(odoo_port):
        odoo_port = find_available_port(odoo_port + 1)
        changed = True

    if is_port_in_use(vsc_port):
        vsc_port = find_available_port(vsc_port + 1)
        changed = True

    return odoo_port, vsc_port, changed


def _update_ports_in_compose(project_dir: Path, new_odoo_port: int, new_vsc_port: int) -> None:
    """Rewrites only the two port mappings docker-compose.yaml carries for
    Odoo and its debugger, in place, leaving the rest of the file untouched.
    """
    compose_path = project_dir / "docker-compose.yaml"
    if not compose_path.exists():
        compose_path = project_dir / "docker-compose.yml"
    if not compose_path.exists():
        return

    content = compose_path.read_text()
    content = re.sub(r'"\d+:8069"', f'"{new_odoo_port}:8069"', content)
    content = re.sub(r'"\d+:8888"', f'"{new_vsc_port}:8888"', content)
    compose_path.write_text(content)


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
    return run_compose_result("up", "-d", "db", cwd=project_dir)["ok"]


def _init_odoo_volume(project_dir: Path, web_container: str, report: ProgressCallback) -> bool:
    """Starts the web container briefly so Docker creates the named volume,
    then stops it immediately so Odoo never runs while the filestore below
    is being restored.
    """
    report("Starting web container to initialize volume...")
    run_compose_result("up", "-d", "web", cwd=project_dir)
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

    original_odoo_port = int(meta.get("odoo_port") or 8069)
    original_vsc_port = int(meta.get("vsc_port") or 8888)
    suggested_odoo_port, suggested_vsc_port, _ = _check_ports(meta)

    dump, filestore = _find_backup_files(root)

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
        "backups": {
            "db_dump": str(dump) if dump else None,
            "filestore": str(filestore) if filestore else None,
        },
        "uses_private_repos": bool(meta.get("uses_private_repos")),
        "ssh_keys": list_private_keys(),
    }


# ─── action functions ────────────────────────────────────────────────────────


def unpack(
    project_root: Path | str,
    *,
    ports: dict | None = None,
    ssh_key: str | None = None,
    restore: bool = True,
    build: bool = False,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Starts a previously packed environment.

    Never prompts (RF9.2): `ports` and `ssh_key` arrive already decided by
    the caller. When a database backup is present and `restore` is True, the
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

    meta = _effective_meta(_load_shared_meta(root) or {})
    original_odoo_port = int(meta.get("odoo_port") or 8069)
    original_vsc_port = int(meta.get("vsc_port") or 8888)
    ports = ports or {}
    odoo_port = int(ports.get("odoo_port", original_odoo_port))
    vsc_port = int(ports.get("vsc_port", original_vsc_port))
    ports_changed = odoo_port != original_odoo_port or vsc_port != original_vsc_port

    if ports_changed:
        _update_ports_in_compose(root, odoo_port, vsc_port)
        report("docker-compose.yaml updated with new ports.", "ok")

    warnings: list[str] = []

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
        "ssh_configured": ssh_configured,
        "db_restored": db_restored,
        "filestore_restored": filestore_restored,
        "started": started,
        "launched": launched,
        "odoo_container": odoo_container,
        "warnings": warnings,
        "logs_tail": logs_tail,
    }
