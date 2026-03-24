# rocketdoo/pack_environment.py
"""
rkd pack — Packages the development environment to share with another developer.

Steps:
  1. Validates that a Rocketdoo project exists in the current directory.
  2. Backs up the active database + filestore via pg_dump inside the container.
  3. Sanitizes the Dockerfile: comments out SSH key lines to avoid exposing them.
  4. Excludes the .ssh/ directory from the ZIP (must never be shared).
  5. Creates rkd-shared.json with environment metadata (flags private repo usage).
  6. Compresses everything into a shareable ZIP file.
  7. Restores the original Dockerfile after compression.
"""

import re
import json
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

import click
import questionary
from rich.console import Console
from rich.panel import Panel
from rich import box

from rocketdoo.project_info import get_project_info, project_exists, read_docker_compose

console = Console()

# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _get_db_container(compose_data: dict) -> str | None:
    """Returns the database container name from docker-compose data."""
    try:
        return compose_data["services"]["db"]["container_name"]
    except (KeyError, TypeError):
        return None


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
            ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
            capture_output=True, text=True
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def _list_odoo_databases(db_container: str) -> list[str]:
    """Lists available databases in the PostgreSQL container."""
    try:
        result = subprocess.run(
            [
                "docker", "exec", db_container,
                "psql", "-U", "root", "-d", "postgres",
                "-t", "-c",
                "SELECT datname FROM pg_database WHERE datistemplate = false AND datname != 'postgres';"
            ],
            capture_output=True, text=True
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def _backup_database(db_container: str, db_name: str, output_path: Path) -> bool:
    """
    Runs pg_dump inside the container and saves the result to output_path.
    Returns True if the backup succeeded.
    """
    console.print(f"  [dim]Running pg_dump for '[cyan]{db_name}[/cyan]'...[/dim]")
    try:
        with open(output_path, "wb") as f:
            result = subprocess.run(
                [
                    "docker", "exec", db_container,
                    "pg_dump", "-U", "root", "--format=custom", db_name
                ],
                stdout=f,
                stderr=subprocess.PIPE
            )
        if result.returncode != 0:
            console.print(f"  [red]✗ pg_dump error:[/red] {result.stderr.decode()}")
            return False
        size_mb = output_path.stat().st_size / (1024 * 1024)
        console.print(f"  [green]✓[/green] Backup saved: [yellow]{output_path.name}[/yellow] ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        console.print(f"  [red]✗ Exception during database backup:[/red] {e}")
        return False


def _backup_filestore(odoo_container: str, db_name: str, output_path: Path) -> bool:
    """
    Copies and compresses the Odoo filestore from the container to the host.

    Supports multiple filestore layouts:
      - /var/lib/odoo/.local/share/Odoo/filestore/<db_name>
      - /var/lib/odoo/filestore/<db_name>
    """
    console.print(f"  [dim]Copying filestore from container...[/dim]")

    POSSIBLE_PATHS = [
        f"/var/lib/odoo/.local/share/Odoo/filestore/{db_name}",
        f"/var/lib/odoo/filestore/{db_name}",
    ]

    filestore_path = None
    filestore_base = None

    try:
        # ── Detect filestore path dynamically ──
        for path in POSSIBLE_PATHS:
            check = subprocess.run(
                ["docker", "exec", odoo_container, "test", "-d", path],
                capture_output=True
            )
            if check.returncode == 0:
                filestore_path = path
                filestore_base = str(Path(path).parent)
                break

        if not filestore_path:
            console.print(
                f"  [yellow]⚠[/yellow]  Filestore not found for database "
                f"[cyan]{db_name}[/cyan] in any known location — skipping."
            )
            return True  # Not fatal

        console.print(f"  [dim]Detected filestore path:[/dim] [cyan]{filestore_path}[/cyan]")

        # ── Compress filestore ──
        with open(output_path, "wb") as f:
            result = subprocess.run(
                [
                    "docker", "exec", odoo_container,
                    "tar", "-czf", "-", "-C",
                    filestore_base,
                    db_name
                ],
                stdout=f,
                stderr=subprocess.PIPE
            )

        if result.returncode != 0:
            console.print(f"  [red]✗ Error copying filestore:[/red] {result.stderr.decode()}")
            return False

        size_mb = output_path.stat().st_size / (1024 * 1024)
        console.print(
            f"  [green]✓[/green] Filestore compressed: "
            f"[yellow]{output_path.name}[/yellow] ({size_mb:.1f} MB)"
        )

        return True

    except Exception as e:
        console.print(f"  [red]✗ Exception during filestore backup:[/red] {e}")
        return False


def _sanitize_dockerfile(dockerfile_path: Path) -> str:
    """
    Reads the Dockerfile, comments out all SSH key related lines,
    and returns the ORIGINAL content so it can be restored afterwards.
    Writes the sanitized version to disk.
    """
    original_content = dockerfile_path.read_text()

    ssh_patterns = [
        r"^RUN mkdir -p /root/\.ssh",
        r"^COPY \./.ssh/",
        r"^RUN chmod \d+ /root/\.ssh/",
        r"^RUN echo .StrictHostKeyChecking",
    ]

    sanitized_lines = []
    for line in original_content.splitlines():
        stripped = line.strip()
        is_ssh_line = any(re.match(pat, stripped) for pat in ssh_patterns)
        if is_ssh_line:
            sanitized_lines.append(f"# [RKD-SANITIZED] {line}")
        else:
            sanitized_lines.append(line)

    dockerfile_path.write_text("\n".join(sanitized_lines))
    return original_content


def _restore_dockerfile(dockerfile_path: Path, original_content: str):
    """Restores the Dockerfile to its original content."""
    dockerfile_path.write_text(original_content)


def _verify_no_ssh_in_zip(zip_path: Path) -> list[str]:
    """
    Safety double-check: scans the ZIP for files that look like private SSH keys.
    Returns a list of suspicious file names found.
    """
    suspicious = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            basename = Path(name).name
            if any([
                basename.startswith("id_rsa") and not basename.endswith(".pub"),
                basename.startswith("id_ed25519") and not basename.endswith(".pub"),
                basename.startswith("id_ecdsa") and not basename.endswith(".pub"),
                "/.ssh/" in name and not name.endswith(".pub"),
            ]):
                suspicious.append(name)
    return suspicious


def _create_zip(project_dir: Path, zip_path: Path, backup_dir: Path, exclude_dirs: list[str]) -> int:
    """
    Creates the ZIP archive of the full environment.
    Excludes: .ssh/, __pycache__, node_modules, and any extra dirs provided.
    Includes the backup directory contents under rkd_backups/.
    Returns the number of files included.
    """
    ALWAYS_EXCLUDE = {".ssh", "__pycache__", "node_modules", ".mypy_cache"}
    excluded = ALWAYS_EXCLUDE | set(exclude_dirs)

    file_count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in project_dir.rglob("*"):
            rel = item.relative_to(project_dir)
            if set(rel.parts) & excluded:
                continue
            if item.is_file():
                zf.write(item, rel)
                file_count += 1

        if backup_dir.exists() and not backup_dir.is_relative_to(project_dir):
            for bf in backup_dir.iterdir():
                if bf.is_file():
                    zf.write(bf, Path("rkd_backups") / bf.name)
                    file_count += 1

    return file_count


# ─────────────────────────────────────────────────────────────
# Main command
# ─────────────────────────────────────────────────────────────

@click.command(name="pack")
@click.option("--no-db", is_flag=True, default=False,
              help="Skip the database and filestore backup (environment files only).")
@click.option("--output", "-o", default=None, type=click.Path(),
              help="Output path for the ZIP file (default: parent directory, named after the project).")
@click.option("--db-name", default=None,
              help="Name of the database to back up (useful when multiple databases exist).")
def pack_environment(no_db, output, db_name):
    """
    📦 Package the development environment to share with another developer.

    Generates a ZIP with the full environment directory, a database backup,
    and the filestore — sanitizing SSH keys so they are never exposed.

    \b
    Examples:
      rkd pack                  → full backup + ZIP
      rkd pack --no-db          → environment only (no DB backup)
      rkd pack -o /tmp/my.zip   → specify output path
    """
    console.print()
    console.print(Panel(
        "[bold cyan]📦 RKD Pack — Prepare environment for sharing[/bold cyan]\n\n"
        "[dim]This process will:[/dim]\n"
        "  [green]✓[/green] Back up the database and filestore\n"
        "  [green]✓[/green] Sanitize SSH keys from the Dockerfile\n"
        "  [green]✓[/green] Generate a shareable ZIP file",
        border_style="cyan",
        box=box.ROUNDED
    ))
    console.print()

    # ── 1. Validate project ──
    if not project_exists():
        console.print("[red]✗[/red] No Rocketdoo project detected in this directory.")
        console.print("[dim]💡 Run [cyan]rkd init[/cyan] first.[/dim]")
        return

    project_dir = Path.cwd()
    project_info = get_project_info()
    project_name = project_info.get("project_name") or project_dir.name
    compose_data = read_docker_compose()

    console.print(f"[bold]Project detected:[/bold] [cyan]{project_name}[/cyan]")
    console.print(f"[bold]Odoo:[/bold] {project_info.get('odoo_version', 'unknown')} "
                  f"({project_info.get('odoo_edition', 'Community')})")
    console.print()

    # ── 2. Database and filestore backup ──
    backup_dir = project_dir / "rkd_backups"
    db_backup_path = None
    fs_backup_path = None

    if not no_db:
        db_container = _get_db_container(compose_data) if compose_data else None
        odoo_container = _get_odoo_container(compose_data) if compose_data else None

        if not db_container:
            console.print("[yellow]⚠[/yellow]  Could not detect the database container.")
            console.print("[dim]   Continuing without DB backup. Use [cyan]--no-db[/cyan] to suppress this warning.[/dim]")
        elif not _is_container_running(db_container):
            console.print(f"[yellow]⚠[/yellow]  Container [cyan]{db_container}[/cyan] is not running.")
            console.print("[dim]   Start the environment with [cyan]rkd up -d[/cyan] before running pack with backup.[/dim]")
            if not questionary.confirm("Continue anyway without DB backup?", default=False).ask():
                console.print("[yellow]Operation cancelled.[/yellow]")
                return
        else:
            console.print("[bold]💾 Database backup:[/bold]")
            available_dbs = _list_odoo_databases(db_container)

            if not available_dbs:
                console.print("  [yellow]⚠[/yellow]  No Odoo databases found.")
            else:
                if db_name and db_name in available_dbs:
                    selected_db = db_name
                elif len(available_dbs) == 1:
                    selected_db = available_dbs[0]
                    console.print(f"  Database detected: [cyan]{selected_db}[/cyan]")
                else:
                    selected_db = questionary.select(
                        "Select the database to back up:",
                        choices=available_dbs
                    ).ask()

                backup_dir.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                db_backup_path = backup_dir / f"db_{selected_db}_{timestamp}.dump"
                fs_backup_path = backup_dir / f"filestore_{selected_db}_{timestamp}.tar.gz"

                db_ok = _backup_database(db_container, selected_db, db_backup_path)
                if not db_ok:
                    db_backup_path = None

                if odoo_container and _is_container_running(odoo_container):
                    _backup_filestore(odoo_container, selected_db, fs_backup_path)
                else:
                    console.print(f"  [yellow]⚠[/yellow]  Odoo container [cyan]{odoo_container}[/cyan] is not running — filestore skipped.")
                    fs_backup_path = None

    # ── 3. Detect SSH key usage ──
    uses_ssh = project_info.get("use_private_repos", False)
    ssh_key_name = project_info.get("ssh_key")

    if uses_ssh:
        console.print()
        console.print("[bold]🔐 SSH keys detected:[/bold]")
        console.print(f"  Key in use: [yellow]{ssh_key_name or 'detected in Dockerfile'}[/yellow]")
        console.print("  [dim]SSH keys will be excluded from the ZIP.[/dim]")

    # ── 4. Sanitize Dockerfile ──
    dockerfile_path = project_dir / "Dockerfile"
    original_dockerfile = None

    if dockerfile_path.exists() and uses_ssh:
        console.print()
        console.print("[bold]🧹 Sanitizing Dockerfile...[/bold]")
        original_dockerfile = _sanitize_dockerfile(dockerfile_path)
        console.print("  [green]✓[/green] SSH lines commented out in the Dockerfile for the ZIP.")

    # ── 5. Write rkd-shared.json metadata ──
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
    }

    meta_path = project_dir / "rkd-shared.json"
    meta_path.write_text(json.dumps(shared_meta, indent=2, ensure_ascii=False))
    console.print()
    console.print("[dim]📋 Environment metadata written to rkd-shared.json[/dim]")

    # ── 6. Create ZIP ──
    console.print()
    console.print("[bold]🗜️  Creating ZIP...[/bold]")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if output:
        zip_path = Path(output)
    else:
        zip_path = project_dir.parent / f"{project_name}_rkd_shared_{timestamp}.zip"

    try:
        file_count = _create_zip(
            project_dir=project_dir,
            zip_path=zip_path,
            backup_dir=backup_dir,
            exclude_dirs=[".ssh"]
        )
    except Exception as e:
        console.print(f"[red]✗ Error creating ZIP:[/red] {e}")
        if original_dockerfile:
            _restore_dockerfile(dockerfile_path, original_dockerfile)
        meta_path.unlink(missing_ok=True)
        return

    # ── 7. Restore original Dockerfile ──
    if original_dockerfile:
        _restore_dockerfile(dockerfile_path, original_dockerfile)
        console.print("[dim]  Original Dockerfile restored.[/dim]")

    # Clean up temporary metadata from working directory
    meta_path.unlink(missing_ok=True)

    # ── 8. SSH safety verification ──
    suspicious = _verify_no_ssh_in_zip(zip_path)
    if suspicious:
        console.print()
        console.print("[bold red]⚠️  SECURITY WARNING:[/bold red]")
        console.print("[red]Possible private SSH keys detected inside the ZIP:[/red]")
        for s in suspicious:
            console.print(f"  [red]• {s}[/red]")
        console.print("[dim]Please review the ZIP contents before sharing.[/dim]")

    # ── 9. Final summary ──
    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    console.print()
    console.print(Panel(
        f"[bold green]✅ Environment packaged successfully[/bold green]\n\n"
        f"[bold]📁 File:[/bold] [cyan]{zip_path}[/cyan]\n"
        f"[bold]📦 Files included:[/bold] {file_count}\n"
        f"[bold]💾 Size:[/bold] {zip_size_mb:.1f} MB\n"
        f"[bold]🔐 SSH Keys:[/bold] {'excluded ✓' if uses_ssh else 'not applicable'}\n"
        f"[bold]💿 DB Backup:[/bold] {'included ✓' if db_backup_path else ('skipped (--no-db)' if no_db else 'not available')}\n\n"
        f"[dim]Share the ZIP with the other developer.\n"
        f"The recipient should run: [cyan]rkd unpack[/cyan][/dim]",
        border_style="green",
        box=box.ROUNDED
    ))
    console.print()