# rocketdoo/unpack_environment.py
"""
rkd unpack — Starts a development environment shared by another developer.

Steps:
  1. Detects rkd-shared.json to confirm this is a shared environment.
  2. Validates that the environment's ports are available; suggests alternatives if not.
  3. If the environment used private repos (SSH), lists the recipient's keys and
     configures the Dockerfile with the chosen one.
  4. Starts the environment with docker compose up -d.
  5. If a database backup is present, automatically restores the DB and filestore.
"""

import json
import re
import subprocess
import time
from pathlib import Path

import click
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from rocketdoo.core.port_validation import is_port_in_use, find_available_port
from rocketdoo.core.ssh_manager import list_private_keys, copy_key_to_build_context, inject_ssh_into_dockerfile
from rocketdoo.project_info import project_exists, read_docker_compose

console = Console()


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _load_shared_meta(project_dir: Path) -> dict | None:
    """Loads the rkd-shared.json file if it exists."""
    meta_path = project_dir / "rkd-shared.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            return None
    return None


def _find_backup_files(project_dir: Path) -> tuple[Path | None, Path | None]:
    """
    Searches for backup files inside the rkd_backups directory.
    Returns (db_dump_path, filestore_tar_path) — None if not found.
    """
    backup_dir = project_dir / "rkd_backups"
    if not backup_dir.exists():
        return None, None

    dumps = sorted(backup_dir.glob("db_*.dump"), reverse=True)
    filestores = sorted(backup_dir.glob("filestore_*.tar.gz"), reverse=True)

    return (dumps[0] if dumps else None), (filestores[0] if filestores else None)


def _check_ports(meta: dict, auto_accept: bool = False) -> tuple[int, int, bool]:
    """
    Verifies whether the environment's ports are available.
    Returns (final_odoo_port, final_vsc_port, had_changes).
    When auto_accept=True, silently picks the suggested port without prompting.
    """
    odoo_port = int(meta.get("odoo_port") or 8069)
    vsc_port = int(meta.get("vsc_port") or 8888)
    changed = False

    console.print("[bold]🔍 Checking port availability...[/bold]")

    if is_port_in_use(odoo_port):
        suggested = find_available_port(odoo_port + 1)
        console.print(f"  [yellow]⚠[/yellow]  Odoo port [cyan]{odoo_port}[/cyan] is already in use.")
        if auto_accept:
            console.print(f"  [dim]Auto-selecting port [green]{suggested}[/green][/dim]")
            odoo_port = suggested
        else:
            console.print(f"  [dim]Suggested port: [green]{suggested}[/green][/dim]")
            use_suggested = questionary.confirm(
                f"Use port {suggested} for Odoo instead of {odoo_port}?", default=True
            ).ask()
            odoo_port = suggested if use_suggested else click.prompt("Enter Odoo port to use", type=int, default=suggested)
        changed = True
    else:
        console.print(f"  [green]✓[/green] Odoo port [cyan]{odoo_port}[/cyan] is available.")

    if is_port_in_use(vsc_port):
        suggested_vsc = find_available_port(vsc_port + 1)
        console.print(f"  [yellow]⚠[/yellow]  VSCode port [cyan]{vsc_port}[/cyan] is already in use.")
        if auto_accept:
            console.print(f"  [dim]Auto-selecting port [green]{suggested_vsc}[/green][/dim]")
            vsc_port = suggested_vsc
        else:
            console.print(f"  [dim]Suggested port: [green]{suggested_vsc}[/green][/dim]")
            use_suggested_vsc = questionary.confirm(
                f"Use port {suggested_vsc} for VSCode instead of {vsc_port}?", default=True
            ).ask()
            vsc_port = suggested_vsc if use_suggested_vsc else click.prompt("Enter VSCode port to use", type=int, default=suggested_vsc)
        changed = True
    else:
        console.print(f"  [green]✓[/green] VSCode port [cyan]{vsc_port}[/cyan] is available.")

    return odoo_port, vsc_port, changed


def _update_ports_in_compose(project_dir: Path, new_odoo_port: int, new_vsc_port: int):
    """
    Updates port mappings in docker-compose.yaml using plain text replacement
    to preserve the original file format.
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
    console.print(f"  [green]✓[/green] docker-compose.yaml updated with new ports.")


def _configure_ssh(project_dir: Path, meta: dict, key_name: str | None = None) -> bool:
    """
    Guides the recipient through configuring their own SSH key for the environment.
    If key_name is provided, uses it directly without interactive prompts.
    Returns True if configured successfully, False if skipped or failed.
    """
    console.print()
    console.print("[bold]🔐 SSH configuration for private repositories:[/bold]")

    if key_name:
        console.print(f"  [dim]Using key: [cyan]{key_name}[/cyan][/dim]")
        try:
            dockerfile_path = project_dir / "Dockerfile"
            copy_key_to_build_context(key_name, project_dir)
            if dockerfile_path.exists():
                inject_ssh_into_dockerfile(dockerfile_path, key_name)
                console.print(f"  [green]✓[/green] Dockerfile configured with key [cyan]{key_name}[/cyan]")
            return True
        except Exception as e:
            console.print(f"  [red]✗ Error configuring SSH:[/red] {e}")
            return False

    original_key = meta.get("ssh_key_name")
    if original_key:
        console.print(f"  [dim]The original environment used key: [yellow]{original_key}[/yellow][/dim]")

    console.print()
    available_keys = list_private_keys()

    if not available_keys:
        console.print("  [yellow]⚠[/yellow]  No SSH keys found in ~/.ssh/")
        console.print("  [dim]Generate one with: [cyan]ssh-keygen -t rsa -b 4096[/cyan][/dim]")
        skip = questionary.confirm(
            "Continue without SSH? (private repos will not work)", default=False
        ).ask()
        return not skip

    console.print(f"  [dim]Found {len(available_keys)} SSH key(s) available.[/dim]")

    selected_key = questionary.select(
        "Select your SSH key for private repositories:",
        choices=available_keys
    ).ask()

    if not selected_key:
        return False

    try:
        dockerfile_path = project_dir / "Dockerfile"
        console.print(f"  [dim]Copying key [cyan]{selected_key}[/cyan] to build context...[/dim]")
        copy_key_to_build_context(selected_key, project_dir)

        if dockerfile_path.exists():
            inject_ssh_into_dockerfile(dockerfile_path, selected_key)
            console.print(f"  [green]✓[/green] Dockerfile configured with key [cyan]{selected_key}[/cyan]")
        else:
            console.print("  [yellow]⚠[/yellow]  Dockerfile not found.")

        return True
    except Exception as e:
        console.print(f"  [red]✗ Error configuring SSH:[/red] {e}")
        return False


def _get_db_container_name(project_dir: Path) -> str | None:
    """Reads docker-compose and returns the database container name."""
    compose_data = read_docker_compose()
    if compose_data:
        try:
            return compose_data["services"]["db"]["container_name"]
        except (KeyError, TypeError):
            pass
    return None


def _get_odoo_container_name(project_dir: Path) -> str | None:
    """Reads docker-compose and returns the web container name."""
    compose_data = read_docker_compose()
    if compose_data:
        try:
            return compose_data["services"]["web"]["container_name"]
        except (KeyError, TypeError):
            pass
    return None


def _wait_for_postgres(db_container: str, max_wait: int = 60) -> bool:
    """Waits until PostgreSQL is ready to accept connections."""
    console.print(f"  [dim]Waiting for PostgreSQL to be ready (max {max_wait}s)...[/dim]")
    for i in range(max_wait):
        result = subprocess.run(
            ["docker", "exec", db_container, "pg_isready", "-U", "root"],
            capture_output=True
        )
        if result.returncode == 0:
            console.print(f"  [green]✓[/green] PostgreSQL is ready.")
            return True
        time.sleep(1)
        if i % 10 == 9:
            console.print(f"  [dim]  ...{i + 1}s[/dim]")
    return False


def _wait_for_odoo_volume(odoo_container: str, max_wait: int = 90) -> bool:
    """
    Waits until /var/lib/odoo is mounted and accessible inside the Odoo container.

    Replaces time.sleep(5): the Docker volume may take several seconds to become
    available after the container starts, especially on first run. Without this
    check the filestore restore would silently fail writing to a non-existent path.
    """
    console.print(f"  [dim]Waiting for Odoo volume to be ready (max {max_wait}s)...[/dim]")
    for i in range(max_wait):
        result = subprocess.run(
            ["docker", "exec", odoo_container, "test", "-d", "/var/lib/odoo"],
            capture_output=True
        )
        if result.returncode == 0:
            console.print(f"  [green]✓[/green] Odoo volume is ready.")
            return True
        time.sleep(1)
        if i % 15 == 14:
            console.print(f"  [dim]  ...{i + 1}s[/dim]")
    console.print(f"  [yellow]⚠[/yellow]  Odoo volume not ready after {max_wait}s.")
    return False


def _restore_database(db_container: str, dump_path: Path) -> str | None:
    """
    Restores the PostgreSQL dump into the container.
    Returns the restored database name, or None if it failed.
    """
    stem = dump_path.stem
    parts = stem.split("_")
    db_name = "_".join(parts[1:-2]) if len(parts) >= 4 else (parts[1] if len(parts) > 1 else "odoo_restored")

    console.print(f"  [dim]Restoring database [cyan]{db_name}[/cyan]...[/dim]")

    try:
        copy_result = subprocess.run(
            ["docker", "cp", str(dump_path), f"{db_container}:/tmp/rkd_restore.dump"],
            capture_output=True, text=True
        )
        if copy_result.returncode != 0:
            console.print(f"  [red]✗ Error copying dump:[/red] {copy_result.stderr}")
            return None

        subprocess.run(
            ["docker", "exec", db_container,
             "psql", "-U", "root", "-d", "postgres", "-c",
             f"DROP DATABASE IF EXISTS \"{db_name}\";"],
            capture_output=True
        )
        create_result = subprocess.run(
            ["docker", "exec", db_container,
             "psql", "-U", "root", "-d", "postgres", "-c",
             f"CREATE DATABASE \"{db_name}\" OWNER root;"],
            capture_output=True, text=True
        )
        if create_result.returncode != 0:
            console.print(f"  [red]✗ Error creating database:[/red] {create_result.stderr}")
            return None

        restore_result = subprocess.run(
            ["docker", "exec", db_container,
             "pg_restore", "-U", "root", "-d", db_name,
             "--no-owner", "--role=root",
             "/tmp/rkd_restore.dump"],
            capture_output=True, text=True
        )

        # pg_restore may return warnings (returncode 1) but still succeed
        if restore_result.returncode not in (0, 1):
            console.print(f"  [red]✗ Restore error:[/red] {restore_result.stderr[:500]}")
            return None

        console.print(f"  [green]✓[/green] Database [cyan]{db_name}[/cyan] restored successfully.")
        return db_name

    except Exception as e:
        console.print(f"  [red]✗ Exception during restore:[/red] {e}")
        return None


def _restore_filestore(odoo_container: str, filestore_tar: Path, db_name: str) -> bool:
    """Restores the Odoo filestore into the web container (supports multiple layouts)."""
    console.print(f"  [dim]Restoring filestore for [cyan]{db_name}[/cyan]...[/dim]")

    POSSIBLE_BASES = [
        "/var/lib/odoo/.local/share/Odoo/filestore",
        "/var/lib/odoo/filestore",
    ]

    try:
        # ── 1. Copy tar to container ──
        copy_result = subprocess.run(
            ["docker", "cp", str(filestore_tar), f"{odoo_container}:/tmp/rkd_filestore.tar.gz"],
            capture_output=True, text=True
        )
        if copy_result.returncode != 0:
            console.print(f"  [yellow]⚠[/yellow]  Could not copy filestore: {copy_result.stderr}")
            return False

        # ── 2. Detect filestore base ──
        filestore_base = None

        for base in POSSIBLE_BASES:
            check = subprocess.run(
                ["docker", "exec", odoo_container, "test", "-d", base],
                capture_output=True
            )
            if check.returncode == 0:
                filestore_base = base
                break

        # fallback → usar layout simple
        if not filestore_base:
            filestore_base = "/var/lib/odoo/filestore"
            console.print(
                f"  [yellow]⚠[/yellow]  Filestore base not found. Using fallback: "
                f"[cyan]{filestore_base}[/cyan]"
            )

        console.print(f"  [dim]Using filestore base:[/dim] [cyan]{filestore_base}[/cyan]")

        # ── 3. Ensure base exists ──
        subprocess.run(
            ["docker", "exec", odoo_container, "mkdir", "-p", filestore_base],
            capture_output=True
        )

        # ── 4. Clean existing filestore (VERY IMPORTANT) ──
        console.print(f"  [dim]Cleaning existing filestore for {db_name}...[/dim]")
        subprocess.run(
            ["docker", "exec", odoo_container, "rm", "-rf", f"{filestore_base}/{db_name}"],
            capture_output=True
        )

        # ── 5. Extract tar ──
        extract_result = subprocess.run(
            [
                "docker", "exec", odoo_container,
                "tar", "-xzf", "/tmp/rkd_filestore.tar.gz",
                "-C", filestore_base
            ],
            capture_output=True, text=True
        )

        # tar exits 1 on non-fatal warnings (extended headers, etc.) — treat as success
        if extract_result.returncode > 1:
            console.print(f"  [yellow]⚠[/yellow]  Error extracting filestore: {extract_result.stderr}")
            return False

        # ── 6. Fix permissions ──
        subprocess.run(
            [
                "docker", "exec", odoo_container,
                "chown", "-R", "odoo:odoo", f"{filestore_base}/{db_name}"
            ],
            capture_output=True
        )

        console.print(f"  [green]✓[/green] Filestore restored successfully.")
        return True

    except Exception as e:
        console.print(f"  [yellow]⚠[/yellow]  Exception while restoring filestore: {e}")
        return False


def _launch_environment(build: bool = False):
    """Runs docker compose up -d (with optional --build flag)."""
    cmd = ["docker", "compose", "up", "-d"]
    if build:
        cmd.append("--build")
    console.print()
    console.print(f"[bold]🚀 Starting environment:[/bold] [dim]{' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd)
    return result.returncode == 0


def _launch_db_only():
    """Starts only the db service to allow database restoration."""
    console.print("[dim]  Starting database service only...[/dim]")
    result = subprocess.run(
        ["docker", "compose", "up", "-d", "db"],
        capture_output=True, text=True
    )
    return result.returncode == 0


def _init_odoo_volume(web_container: str) -> bool:
    """
    Starts the web container briefly so Docker creates the named volume,
    then stops it immediately so Odoo never runs while we restore the filestore.
    """
    console.print("[dim]  Starting web container to initialize volume...[/dim]")
    subprocess.run(["docker", "compose", "up", "-d", "web"], capture_output=True)
    time.sleep(3)
    ready = False
    for _ in range(30):
        result = subprocess.run(
            ["docker", "exec", web_container, "test", "-d", "/var/lib/odoo"],
            capture_output=True
        )
        if result.returncode == 0:
            ready = True
            break
        time.sleep(1)
    subprocess.run(["docker", "compose", "stop", "web"], capture_output=True)
    return ready


# ─────────────────────────────────────────────────────────────
# Main command
# ─────────────────────────────────────────────────────────────

@click.command(name="unpack")
@click.option("--no-restore", is_flag=True, default=False,
              help="Skip automatic database restoration.")
@click.option("--build", is_flag=True, default=False,
              help="Rebuild the Docker image before starting (recommended on first run).")
@click.option("--ssh-key", "ssh_key", default=None,
              help="SSH key name from ~/.ssh/ to use (skips interactive selection).")
@click.option("--no-ssh", "no_ssh", is_flag=True, default=False,
              help="Skip SSH configuration entirely (for environments without private repos).")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Auto-accept port conflict suggestions without prompting.")
def unpack_environment(no_restore, build, ssh_key, no_ssh, yes):
    """
    📥 Start a development environment shared by another developer.

    Run this inside the unzipped environment directory.
    Automatically detects shared environments, validates ports,
    configures your own SSH keys, and restores the database.

    \b
    Examples:
      rkd unpack              → full setup (recommended)
      rkd unpack --no-restore → skip DB restore
      rkd unpack --build      → rebuild Docker image
    """
    console.print()
    console.print(Panel(
        "[bold cyan]📥 RKD Unpack — Start shared environment[/bold cyan]\n\n"
        "[dim]This process will:[/dim]\n"
        "  [green]✓[/green] Detect and validate the shared environment\n"
        "  [green]✓[/green] Check port availability\n"
        "  [green]✓[/green] Configure your SSH key if using private repos\n"
        "  [green]✓[/green] Start the environment and restore the database\n"
        "  [green]✓[/green] Restore the filestore (Odoo stopped to avoid conflicts)",
        border_style="cyan",
        box=box.ROUNDED
    ))
    console.print()

    project_dir = Path.cwd()

    # ── 1. Detect project and metadata ──
    if not project_exists():
        console.print("[red]✗[/red] No Rocketdoo project found in this directory.")
        console.print("[dim]Make sure you are inside the unzipped environment directory.[/dim]")
        return

    meta = _load_shared_meta(project_dir)

    if meta and meta.get("rkd_shared"):
        console.print("[green]✓[/green] Shared environment detected ([dim]rkd-shared.json[/dim])")
        console.print()

        info_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
        info_table.add_column("", style="cyan bold", width=22)
        info_table.add_column("", style="green")
        info_table.add_row("📦 Project", meta.get("project_name", "unknown"))
        info_table.add_row("🐳 Odoo", f"{meta.get('odoo_version', '?')} ({meta.get('odoo_edition', 'Community')})")
        info_table.add_row("🗄️  PostgreSQL", str(meta.get("db_version", "?")))
        info_table.add_row("🔐 Private repos", "Yes" if meta.get("uses_private_repos") else "No")
        info_table.add_row("💾 DB Backup", "Included ✓" if meta.get("has_db_backup") else "Not included")
        console.print(Panel(info_table, title="[bold]📋 Environment to start[/bold]",
                            border_style="dim", box=box.ROUNDED))
        console.print()
    else:
        console.print("[yellow]⚠[/yellow]  rkd-shared.json not found.")
        console.print("[dim]This looks like a Rocketdoo project but was not packaged with [cyan]rkd pack[/cyan].[/dim]")
        if not questionary.confirm("Continue anyway?", default=False).ask():
            return
        meta = {}

    # ── 2. Check and adjust ports ──
    console.print()
    new_odoo_port, new_vsc_port, ports_changed = _check_ports(meta, auto_accept=yes)

    if ports_changed:
        console.print()
        console.print("[dim]  Updating docker-compose.yaml with new ports...[/dim]")
        _update_ports_in_compose(project_dir, new_odoo_port, new_vsc_port)

    # ── 3. Configure SSH if the environment used private repos ──
    uses_private_repos = meta.get("uses_private_repos", False)

    if no_ssh:
        console.print("[dim]  SSH configuration skipped (--no-ssh).[/dim]")
    elif ssh_key:
        console.print()
        _configure_ssh(project_dir, meta, key_name=ssh_key)
    elif uses_private_repos:
        console.print()
        console.print(Panel(
            "This environment was set up with [bold]private repositories[/bold].\n"
            "You need to configure [bold]your own SSH key[/bold] for it to work correctly.",
            border_style="yellow",
            box=box.ROUNDED
        ))
        wants_ssh = questionary.confirm(
            "Do you use private repositories and want to configure your SSH key?", default=True
        ).ask()
        if wants_ssh:
            ssh_ok = _configure_ssh(project_dir, meta)
            if not ssh_ok:
                console.print("[yellow]⚠[/yellow]  SSH not configured. Private repos may not work.")
    elif not meta:
        wants_ssh = questionary.confirm(
            "Does this environment use private repositories? (requires SSH key)", default=False
        ).ask()
        if wants_ssh:
            _configure_ssh(project_dir, {})

    # ── 4. Locate backup files ──
    db_dump, filestore_tar = _find_backup_files(project_dir)
    has_backup = db_dump is not None

    # ── 5. Start the environment ──
    console.print()
    if has_backup and not no_restore:
        console.print("[bold]💾 Database backup found.[/bold]")
        console.print("[dim]  Strategy: start DB → restore → start full environment[/dim]")
        console.print()

        db_up = _launch_db_only()
        if not db_up:
            console.print("[red]✗ Could not start the database service.[/red]")
            return

        db_container = _get_db_container_name(project_dir)
        if db_container:
            pg_ready = _wait_for_postgres(db_container)
            if pg_ready:
                console.print()
                console.print("[bold]💾 Restoring database:[/bold]")
                restored_db = _restore_database(db_container, db_dump)

                if restored_db and filestore_tar:
                    console.print()
                    console.print("[bold]🗂️  Restoring filestore:[/bold]")

                    odoo_container = _get_odoo_container_name(project_dir)
                    if not odoo_container:
                        console.print(
                            "[yellow]⚠[/yellow]  Cannot determine Odoo container name "
                            "— filestore restore skipped.\n"
                            "[dim]  Check that docker-compose.yaml has container_name set on the web service.[/dim]"
                        )
                    else:
                        volume_ready = _init_odoo_volume(odoo_container)
                        if volume_ready:
                            console.print(f"  [green]✓[/green] Volume ready. Restoring filestore (Odoo is stopped)...")
                            _restore_filestore(odoo_container, filestore_tar, restored_db)
                        else:
                            console.print(
                                "[yellow]⚠[/yellow]  Skipping filestore restore: "
                                "Odoo volume was not ready in time.\n"
                                "[dim]  Try running the restore manually after the environment is up.[/dim]"
                            )

        _launch_environment(build=build)
    else:
        _launch_environment(build=build)

    # ── 6. Final summary ──
    console.print()
    console.print(Panel(
        f"[bold green]✅ Environment is ready[/bold green]\n\n"
        f"[bold]🌐 Odoo:[/bold] [cyan underline]http://localhost:{new_odoo_port}[/cyan underline]\n"
        f"[bold]🐛 Debug:[/bold] port [cyan]{new_vsc_port}[/cyan]\n\n"
        f"[dim]Useful commands:\n"
        f"  [cyan]rkd status[/cyan]   → check container status\n"
        f"  [cyan]rkd logs[/cyan]     → view logs\n"
        f"  [cyan]rkd info[/cyan]     → project information[/dim]",
        border_style="green",
        box=box.ROUNDED
    ))
    console.print()