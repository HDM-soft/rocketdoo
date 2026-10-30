"""Environment packaging: shared backup/sanitisation primitives, and the
non-interactive `pack()` service the GUI calls.

Moved out of `pack_environment.py` in #143 T11. RF8.5's three guarantees -
sanitising the Dockerfile, scanning the finished ZIP for leaked SSH keys, and
restoring the Dockerfile even when a later step fails - live here rather than
in the CLI, because they are security guarantees, not presentation: a caller
that skips the CLI (the GUI) still needs them.

`pack_environment.py` is a Click wrapper over `pack()`: prompts and panels,
no pipeline of its own. The CLI decides what needs a user (which database,
whether to go on without a backup) and hands the answers over as arguments;
everything past that point runs here, once, for the CLI and the GUI alike.
"""

import fnmatch
import json
import re
import subprocess
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from rocketdoo.core.gitignore_manager import SENSITIVE_ENTRIES
from rocketdoo.core.odoo_db import databases_result, db_container
from rocketdoo.core.project_info import get_project_info, project_exists, read_docker_compose
from rocketdoo.core.service import ProgressCallback, ServiceError, reporter

_SSH_DOCKERFILE_PATTERNS = [
    r"^RUN mkdir -p /root/\.ssh",
    r"^COPY \./.ssh/",
    r"^RUN chmod \d+ /root/\.ssh/",
    r"^RUN echo .StrictHostKeyChecking",
]

_ALWAYS_EXCLUDE = {".ssh", "__pycache__", "node_modules", ".mypy_cache"}

# Of what gitignore_manager declares sensitive, these two have to travel or the
# shared environment does not work: docker-compose mounts odoo_pg_pass as a
# Docker secret, and Dockerfile.jinja:3 does `COPY ./config/odoo.conf` at build
# time. They are the local credentials of the very environment being handed
# over, which is the point of the ZIP.
#
# Everything else on that list stays out, and that is the direction the default
# has to point: a new entry in SENSITIVE_ENTRIES is excluded from the ZIP until
# someone adds it here on purpose. The two lists used to be unrelated -- one
# knew what a secret was, the other guessed from filenames -- and that is why
# .rkd/secrets/ travelled while the panel reported the archive clean (#245).
_SHAREABLE_SECRETS = {"odoo_pg_pass", "config/odoo.conf"}

# Not secret, but no use to anyone else: `rkd deploy run` keeps up to three
# copies of the local addons here, plus the modules that were deleted since.
# The receiver downloads the sender's backup history and cannot restore from
# it (#249).
_LOCAL_STATE = (".rkd/deploy_backups",)


def _secret_paths() -> list[str]:
    """Project-relative paths that must not leave the machine inside the ZIP."""
    return [entry.rstrip("/") for entry, _reason in SENSITIVE_ENTRIES if entry.rstrip("/") not in _SHAREABLE_SECRETS]


def _is_under(name: str, path: str) -> bool:
    """Whether a ZIP entry is `path` itself, something below it, or matches it.

    `SENSITIVE_ENTRIES` is a list of gitignore patterns, so the next entry
    someone adds may well be `*.pem` rather than a literal path. Matching only
    literal prefixes would have let it through while the comment above claimed
    the opposite -- the kind of promise that is worse than no promise.

    Case-insensitive, because `/mnt/c` is: rkd's own `mkdir(exist_ok=True)`
    writes into an existing `.rkd/Secrets` there, and the file would travel.
    Same reason `is_the_archive()` compares by identity rather than by name.
    """
    lowered, target = name.lower(), path.lower()
    if lowered == target or lowered.startswith(target + "/"):
        return True
    if not any(ch in path for ch in "*?["):
        return False
    return fnmatch.fnmatch(lowered, target) or fnmatch.fnmatch(Path(lowered).name, target)


def _secret_candidates(name: str) -> list[str]:
    """The entry's own path, plus the tail starting at any `.rkd` component.

    The exclusion is anchored at the project root, and that is deliberate:
    matching the bare component `secrets` would drop an addon's own
    `secrets/` directory. But `.rkd` is a name rkd reserves for itself, so
    `addons/mi_modulo/.rkd/secrets/instance_prod.env` is rkd's credential
    wherever it sits -- and `rkd instance init` run one directory too deep
    writes exactly that, somewhere the generated .gitignore does not cover
    either (#246).
    """
    parts = name.split("/")
    tails = ["/".join(parts[i:]) for i, part in enumerate(parts) if part.lower() == ".rkd"]
    return [name, *tails]


def _matches_secret(name: str, secret_paths: Iterable[str]) -> bool:
    """Whether this entry is one of the paths rkd declares secret."""
    candidates = _secret_candidates(name)
    return any(_is_under(candidate, path) for candidate in candidates for path in secret_paths)


# The names pack writes under rkd_backups/. Public because core/unpack.py
# reads back what this module writes, and the two drifting apart is its own
# class of bug: the writer owns the format.
BACKUP_FILENAME = re.compile(r"^(db|filestore)_.+_\d{8}_\d{6}\.(dump|tar\.gz)$")

_FILESTORE_PATH_TEMPLATES = [
    "/var/lib/odoo/.local/share/Odoo/filestore/{db}",
    "/var/lib/odoo/filestore/{db}",
]


class PackError(ServiceError):
    """The environment cannot be packaged as requested."""


# ─── low-level helpers ───────────────────────────────────────────────────────


class MissingDatabaseError(PackError):
    """The database cannot be backed up: no container, or it is not running.

    Its own type so a caller with a user in front of it can offer to go on
    without a backup, which is a decision pack() will not take on its own
    (RF3.3). Raised before anything is written, so retrying with
    allow_missing_db=True costs nothing.
    """


def _get_odoo_container(compose_data: dict) -> str | None:
    """Returns the Odoo web container name from docker-compose data."""
    try:
        return compose_data["services"]["web"]["container_name"]
    except (KeyError, TypeError):
        return None


def _is_container_running(container_name: str) -> bool:
    """Checks whether a Docker container is currently running."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container_name], capture_output=True, text=True
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def _backup_database(db_container_name: str, db_name: str, output_path: Path, report: ProgressCallback) -> bool:
    """Runs pg_dump inside the container and saves the result to output_path.

    Returns True if the backup succeeded.
    """
    report(f"Running pg_dump for '{db_name}'...")
    try:
        with open(output_path, "wb") as f:
            result = subprocess.run(
                ["docker", "exec", db_container_name, "pg_dump", "-U", "root", "--format=custom", db_name],
                stdout=f,
                stderr=subprocess.PIPE,
            )
        if result.returncode != 0:
            report(f"pg_dump error: {result.stderr.decode()}", "warn")
            return False
        size_mb = output_path.stat().st_size / (1024 * 1024)
        report(f"Backup saved: {output_path.name} ({size_mb:.1f} MB)", "ok")
        return True
    except Exception as exc:
        report(f"Exception during database backup: {exc}", "warn")
        return False


def _backup_filestore(
    odoo_container: str, db_name: str, output_path: Path, report: ProgressCallback
) -> tuple[bool, str | None]:
    """Copies and compresses the Odoo filestore from the container to the host.

    Returns (success, filestore_base). Supports multiple filestore layouts.
    """
    report("Copying filestore from container...")

    filestore_path = None
    filestore_base = None

    try:
        for template in _FILESTORE_PATH_TEMPLATES:
            path = template.format(db=db_name)
            check = subprocess.run(["docker", "exec", odoo_container, "test", "-d", path], capture_output=True)
            if check.returncode == 0:
                filestore_path = path
                filestore_base = str(Path(path).parent)
                break

        if not filestore_path:
            report(f"Filestore not found for database {db_name} in any known location - skipping.", "warn")
            return True, None  # Not fatal

        report(f"Detected filestore path: {filestore_path}")

        with open(output_path, "wb") as f:
            result = subprocess.run(
                ["docker", "exec", odoo_container, "tar", "-czf", "-", "-C", filestore_base, db_name],
                stdout=f,
                stderr=subprocess.PIPE,
            )

        if result.returncode != 0:
            report(f"Error copying filestore: {result.stderr.decode()}", "warn")
            return False, filestore_base

        size_mb = output_path.stat().st_size / (1024 * 1024)
        report(f"Filestore compressed: {output_path.name} ({size_mb:.1f} MB)", "ok")
        return True, filestore_base

    except Exception as exc:
        report(f"Exception during filestore backup: {exc}", "warn")
        return False, None


def _sanitize_dockerfile(dockerfile_path: Path) -> str:
    """Comments out SSH key lines and returns the ORIGINAL content for restoring later."""
    original_content = dockerfile_path.read_text()

    sanitized_lines = []
    for line in original_content.splitlines():
        stripped = line.strip()
        is_ssh_line = any(re.match(pat, stripped) for pat in _SSH_DOCKERFILE_PATTERNS)
        sanitized_lines.append(f"# [RKD-SANITIZED] {line}" if is_ssh_line else line)

    dockerfile_path.write_text("\n".join(sanitized_lines))
    return original_content


def _restore_dockerfile(dockerfile_path: Path, original_content: str) -> None:
    """Restores the Dockerfile to its original content."""
    dockerfile_path.write_text(original_content)


def _scan_zip_for_secrets(zip_path: Path) -> list[str]:
    """Last check before the ZIP is handed over: anything in it that should not be.

    Two questions, because neither covers the other. The path list answers
    "did something rkd itself calls a secret end up in here", which is how
    .rkd/secrets/ travelled while this function reported the archive clean
    (#245): it only knew about SSH key filenames.

    The name heuristics answer a narrower question than it is tempting to
    claim: not "is there a private key in here" but "is there one under a
    name OpenSSH hands out". A key copied in as `notas.txt` passes, and so
    does a hard link to one outside the project (#246). Reading the contents
    would answer the real question; this does not.
    """
    secret_paths = _secret_paths()
    suspicious = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            basename = Path(name).name
            if _matches_secret(name, secret_paths):
                suspicious.append(name)
                continue
            if any(
                [
                    basename.startswith("id_rsa") and not basename.endswith(".pub"),
                    basename.startswith("id_ed25519") and not basename.endswith(".pub"),
                    basename.startswith("id_ecdsa") and not basename.endswith(".pub"),
                    "/.ssh/" in name and not name.endswith(".pub"),
                ]
            ):
                suspicious.append(name)
    return suspicious


def _create_zip(
    project_dir: Path,
    zip_path: Path,
    exclude_dirs: list[str],
    exclude_files: Iterable[Path] = (),
    exclude_paths: Iterable[str] = (),
    exclude_state: Iterable[str] = (),
) -> tuple[int, list[str], list[str]]:
    """Creates the ZIP archive of the full environment.

    Excludes: .ssh/, __pycache__, node_modules, any extra dirs provided, and
    the individual files in `exclude_files`. `rkd_backups/`, like every other
    project subdirectory, is picked up by the `rglob` walk below - it never
    needed a dedicated branch. Returns the number of files included, the
    symbolic links left out, and the entries kept out for being secret --
    which the caller reports, while `exclude_state` is dropped in silence:
    the receiver loses nothing they could have used.

    `exclude_files` is matched on the path relative to the project, not on
    file identity the way the output archive is: these paths are enumerated
    from this same tree, under this same spelling, rather than typed by a
    caller who could name the file some other way.
    """
    excluded = _ALWAYS_EXCLUDE | set(exclude_dirs)
    excluded_rel = {Path(item).relative_to(project_dir) for item in exclude_files}
    # By relative path, not by component name: excluding the component "secrets"
    # would drop any directory of that name anywhere in the project, including
    # one belonging to an addon.
    excluded_paths = tuple(exclude_paths)
    state_paths = tuple(exclude_state)

    def is_the_archive(item: Path) -> bool:
        """Whether this entry is the archive being written, under any name.

        An output path inside the project is reached by the walk below, and
        writing the archive into itself makes zipfile read a source whose
        EOF recedes as fast as it is read: every chunk read back is a chunk
        just appended. On compressible content the deflate ratio outruns the
        read and the ZIP merely carries a corrupt copy of itself; on content
        that does not compress - the dump and the filestore under
        rkd_backups/ - nothing ends the loop and the file grows until the
        disk is full.

        Identity rather than path text, because the same growing file is
        reachable under more than one name: a symlink or a hard link to it,
        or the same directory entry spelled with different case on a
        case-insensitive filesystem (WSL over /mnt/c, where this is normally
        run). All three reopen the same hole.
        """
        try:
            return item.samefile(zip_path)
        except OSError:
            return False

    entries = list(project_dir.rglob("*"))

    # A hard link is not a link as far as the walk is concerned: no
    # `is_symlink()`, its own name outside any excluded path, and the same
    # bytes. `ln .rkd/secrets/instance_prod.env notas.txt` hands the password
    # over exactly like the symbolic version did, so the secrets are also
    # identified by what they are and not only by where they sit -- the same
    # reason `is_the_archive()` compares by identity. An inode of 0, which
    # some filesystems report, is left out: it would match everything.
    secret_inodes = set()
    for item in entries:
        if not _matches_secret(item.relative_to(project_dir).as_posix(), excluded_paths):
            continue
        if item.is_symlink() or not item.is_file():
            continue
        stat = item.stat()
        if stat.st_ino:
            secret_inodes.add((stat.st_dev, stat.st_ino))

    file_count = 0
    skipped_links: list[str] = []
    excluded_secrets: list[str] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in entries:
            rel = item.relative_to(project_dir)
            if rel in excluded_rel or set(rel.parts) & excluded:
                continue
            if _matches_secret(rel.as_posix(), excluded_paths):
                if item.is_file():
                    excluded_secrets.append(rel.as_posix())
                continue
            if _matches_secret(rel.as_posix(), state_paths):
                continue
            if item.is_symlink():
                # `zf.write()` follows the link and stores the target's bytes
                # under the link's name, so `ln -s
                # .rkd/secrets/instance_prod.env notas.txt` hands the password
                # over as `notas.txt` -- past the exclusion, which sees the
                # link's own path, and past the scanner, which only ever sees
                # the names inside the archive. The target does not even have
                # to be in the project: `ln -s ~/.ssh/id_rsa addons/clave`
                # shipped a private key (#246).
                #
                # A linked directory was already dropped here, because the
                # walk does not descend into one; now it is at least named.
                skipped_links.append(rel.as_posix())
                continue
            if item.is_file() and not is_the_archive(item):
                stat = item.stat()
                if (stat.st_dev, stat.st_ino) in secret_inodes:
                    continue
                zf.write(item, rel)
                file_count += 1

    return file_count, skipped_links, excluded_secrets


def _stale_backups(backup_dir: Path, keep: set[Path | None]) -> list[Path]:
    """Backups left behind by earlier packs, which must not travel.

    `rkd_backups/` grows by one (dump, filestore) pair per pack and nothing
    removes them. `core/unpack.py` restores the newest pair and ignores the
    rest, so every older pair is weight the recipient downloads and never
    reads - gigabytes of it on a real database.

    With `include_db=False` it is not only weight: `unpack()` decides whether
    to restore from the dump existing, not from the manifest, so a pack that
    carried no database on purpose arrived with an older one.

    Only the names this module writes are recognised. The directory is rkd's;
    the files in it are not necessarily, and dropping one the user put there
    would be silent data loss in the shared copy.
    """
    if not backup_dir.is_dir():
        return []
    return sorted(
        item for item in backup_dir.iterdir() if item.is_file() and item not in keep and BACKUP_FILENAME.match(item.name)
    )


# ─── read functions ──────────────────────────────────────────────────────────


# ─── action functions ────────────────────────────────────────────────────────


def pack(
    project_root: Path | str,
    *,
    include_db: bool = True,
    db_name: str | None = None,
    output: Path | str | None = None,
    allow_missing_db: bool = False,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Package the project into a shareable ZIP.

    Never prompts (RF8.2/RF8.3): a missing or unreachable database container
    raises unless `allow_missing_db` is True, and 2+ databases with no
    `db_name` raises telling the caller to pass one - never picks the first
    one, never asks. The Dockerfile is sanitised before zipping and restored
    right after, even when zip creation itself fails.
    """
    root = Path(project_root)
    report = reporter(on_progress)

    if not project_exists(root):
        raise PackError("No Rocketdoo project detected in this directory.", "Run rkd init first.")

    project_info = get_project_info(root)
    project_name = project_info.get("project_name") or root.name
    compose_data = read_docker_compose(root)
    report(f"Project detected: {project_name}")
    report(f"Odoo: {project_info.get('odoo_version', 'unknown')} ({project_info.get('odoo_edition', 'Community')})")

    backup_dir = root / "rkd_backups"
    db_backup_path = None
    fs_backup_path = None
    # Initialised here, not inside the nested branch that assigns it: with
    # include_db=False, or an instance with no databases yet, none of the
    # branches below run, and the manifest further down reads it anyway
    # (this is the exact UnboundLocalError pack_environment.py used to hit).
    filestore_base = None
    selected_db = None
    warnings: list[str] = []

    if include_db:
        db_container_name = db_container(compose_data) if compose_data else None
        odoo_container = _get_odoo_container(compose_data) if compose_data else None
        if not db_container_name:
            missing_reason = "Could not detect the database container."
        elif not _is_container_running(db_container_name):
            missing_reason = f"Database container '{db_container_name}' is not running."
        else:
            missing_reason = None

        if missing_reason:
            if not allow_missing_db:
                raise MissingDatabaseError(
                    missing_reason,
                    "Start the environment with rkd up -d, or set allow_missing_db=True to skip the backup.",
                )
            report(f"{missing_reason} Continuing without a database backup.", "warn")
            warnings.append(missing_reason)
        else:
            available_dbs, db_list_error = databases_result(compose_data)

            if not available_dbs:
                message = f"Could not list databases: {db_list_error}" if db_list_error else "No Odoo databases found."
                report(message, "warn")
                warnings.append(message)
            else:
                if db_name:
                    if db_name not in available_dbs:
                        raise PackError(f"Database '{db_name}' not found.", f"Available: {', '.join(available_dbs)}")
                    selected_db = db_name
                elif len(available_dbs) == 1:
                    selected_db = available_dbs[0]
                    report(f"Database detected: {selected_db}")
                else:
                    raise PackError(
                        f"{len(available_dbs)} databases found: {', '.join(available_dbs)}.",
                        "Re-run with --db-name NAME.",
                    )

                backup_dir.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                db_backup_path = backup_dir / f"db_{selected_db}_{timestamp}.dump"
                fs_backup_path = backup_dir / f"filestore_{selected_db}_{timestamp}.tar.gz"

                if not _backup_database(db_container_name, selected_db, db_backup_path, report):
                    db_backup_path = None

                if odoo_container and _is_container_running(odoo_container):
                    fs_ok, filestore_base = _backup_filestore(odoo_container, selected_db, fs_backup_path, report)
                    if not fs_ok:
                        fs_backup_path = None
                else:
                    report(f"Odoo container {odoo_container} is not running - filestore skipped.", "warn")
                    fs_backup_path = None

    uses_ssh = project_info.get("use_private_repos", False)
    ssh_key_name = project_info.get("ssh_key")

    dockerfile_path = root / "Dockerfile"
    original_dockerfile = None
    if uses_ssh:
        report(f"SSH keys detected (key in use: {ssh_key_name or 'detected in Dockerfile'}); they stay out of the ZIP.")
    if dockerfile_path.exists() and uses_ssh:
        original_dockerfile = _sanitize_dockerfile(dockerfile_path)
        report("SSH lines commented out in the Dockerfile for the ZIP.", "ok")

    shared_meta = {
        "rkd_shared": True,
        "packed_at": datetime.now().isoformat(),
        "project_name": project_name,
        "odoo_version": project_info.get("odoo_version"),
        "odoo_edition": project_info.get("odoo_edition", "Community"),
        "db_version": project_info.get("db_version"),
        "odoo_port": project_info.get("odoo_port"),
        "vsc_port": project_info.get("vsc_port"),
        "uses_private_repos": uses_ssh,
        "ssh_key_name": ssh_key_name,
        "has_db_backup": db_backup_path is not None and db_backup_path.exists(),
        "has_filestore_backup": fs_backup_path is not None and fs_backup_path.exists(),
        "filestore_base": filestore_base,
    }
    meta_path = root / "rkd-shared.json"
    meta_path.write_text(json.dumps(shared_meta, indent=2, ensure_ascii=False))
    report("Environment metadata written to rkd-shared.json.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = Path(output) if output else root.parent / f"{project_name}_rkd_shared_{timestamp}.zip"

    # A filestore with no dump beside it is dead weight: unpack() nests the
    # whole restore, filestore included, under the dump existing. That is the
    # shape left behind when pg_dump fails mid-pack, so this run's own
    # filestore is only kept when its dump survived.
    shipped = {db_backup_path, fs_backup_path} if db_backup_path else set()
    stale = _stale_backups(backup_dir, shipped)
    if stale:
        report(f"{len(stale)} backup file(s) left out of the ZIP; unpack only restores the newest pair.")

    try:
        file_count, skipped_links, excluded_secrets = _create_zip(
            project_dir=root,
            zip_path=zip_path,
            exclude_dirs=[".ssh"],
            exclude_files=stale,
            exclude_paths=_secret_paths(),
            exclude_state=_LOCAL_STATE,
        )
    except Exception as exc:
        if original_dockerfile is not None:
            _restore_dockerfile(dockerfile_path, original_dockerfile)
        meta_path.unlink(missing_ok=True)
        raise PackError(f"Error creating the ZIP: {exc}") from exc

    if original_dockerfile is not None:
        _restore_dockerfile(dockerfile_path, original_dockerfile)
        report("Original Dockerfile restored.", "ok")

    meta_path.unlink(missing_ok=True)

    if excluded_secrets:
        # Said out loud, like the older backups are (#249): the panel used to
        # report only what it found inside, so a ZIP that correctly kept the
        # sender's VPS credentials out looked exactly like one that had none.
        report(f"{len(excluded_secrets)} file(s) rkd treats as secret stayed out of the ZIP.", "ok")

    if skipped_links:
        message = f"{len(skipped_links)} symbolic link(s) left out of the ZIP; their target is not packaged."
        report(message, "warn")
        warnings.append(message)

    secrets_in_zip = _scan_zip_for_secrets(zip_path)
    if secrets_in_zip:
        message = "Files rkd treats as secrets were found inside the ZIP."
        report(message, "warn")
        warnings.append(message)

    return {
        "zip": str(zip_path),
        "project_name": project_name,
        "db_name": selected_db,
        "db_backup": db_backup_path is not None and db_backup_path.exists(),
        "filestore_backup": fs_backup_path is not None and fs_backup_path.exists(),
        "ssh_sanitized": original_dockerfile is not None,
        "secrets_found_in_zip": bool(secrets_in_zip),
        "secrets_suspicious": list(secrets_in_zip),
        "symlinks_skipped": skipped_links,
        "secrets_excluded": excluded_secrets,
        "file_count": file_count,
        "stale_backups_skipped": len(stale),
        "warnings": warnings,
    }
