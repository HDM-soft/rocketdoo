"""Environment packaging: shared backup/sanitisation primitives, and the
non-interactive `pack()` service the GUI calls.

Moved out of `pack_environment.py` in #143 T11. RF8.5's three guarantees -
sanitising the Dockerfile, scanning the finished ZIP for leaked SSH keys, and
restoring the Dockerfile even when a later step fails - live here rather than
in the CLI, because they are security guarantees, not presentation: a caller
that skips the CLI (the GUI) still needs them.

`pack_environment.py`'s Click command keeps its own interactive orchestration
(the container/database detection its tests characterise call-by-call), but
imports every function below instead of defining them, so `rkd pack` and
`pack()` share one implementation of every step that touches disk or Docker.
"""

import json
import re
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

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


def _verify_no_ssh_in_zip(zip_path: Path) -> list[str]:
    """Safety double-check: scans the ZIP for files that look like private SSH keys."""
    suspicious = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            basename = Path(name).name
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


def _create_zip(project_dir: Path, zip_path: Path, exclude_dirs: list[str]) -> int:
    """Creates the ZIP archive of the full environment.

    Excludes: .ssh/, __pycache__, node_modules, and any extra dirs provided.
    `rkd_backups/`, like every other project subdirectory, is picked up by the
    `rglob` walk below - it never needed a dedicated branch. Returns the
    number of files included.
    """
    excluded = _ALWAYS_EXCLUDE | set(exclude_dirs)

    file_count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in project_dir.rglob("*"):
            rel = item.relative_to(project_dir)
            if set(rel.parts) & excluded:
                continue
            if item.is_file():
                zf.write(item, rel)
                file_count += 1

    return file_count


# ─── read functions ──────────────────────────────────────────────────────────


def list_databases(project_root: Path | str | None = None) -> tuple[list[str], str]:
    """User databases in the project's PostgreSQL container, plus the reason
    the list came back empty, if any. See `core.odoo_db.databases_result`.
    """
    root = Path(project_root) if project_root else Path.cwd()
    return databases_result(read_docker_compose(root))


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

    try:
        file_count = _create_zip(project_dir=root, zip_path=zip_path, exclude_dirs=[".ssh"])
    except Exception as exc:
        if original_dockerfile is not None:
            _restore_dockerfile(dockerfile_path, original_dockerfile)
        meta_path.unlink(missing_ok=True)
        raise PackError(f"Error creating the ZIP: {exc}") from exc

    if original_dockerfile is not None:
        _restore_dockerfile(dockerfile_path, original_dockerfile)
        report("Original Dockerfile restored.", "ok")

    meta_path.unlink(missing_ok=True)

    ssh_found_in_zip = _verify_no_ssh_in_zip(zip_path)
    if ssh_found_in_zip:
        message = "Possible private SSH keys detected inside the ZIP."
        report(message, "warn")
        warnings.append(message)

    return {
        "zip": str(zip_path),
        "project_name": project_name,
        "db_name": selected_db,
        "db_backup": db_backup_path is not None and db_backup_path.exists(),
        "filestore_backup": fs_backup_path is not None and fs_backup_path.exists(),
        "ssh_sanitized": original_dockerfile is not None,
        "ssh_found_in_zip": bool(ssh_found_in_zip),
        "ssh_suspicious": list(ssh_found_in_zip),
        "file_count": file_count,
        "warnings": warnings,
    }
