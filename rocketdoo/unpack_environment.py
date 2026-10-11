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

The restore chain (steps 2-5) lives in `core/unpack.py` (#143 T13), shared
with the GUI's unpack endpoint. This module keeps the wizard: `inspect()`
tells it what to ask, the questions themselves (`questionary`) resolve every
decision up front, and `unpack()` receives the result already decided -
never a rich console, an interactive prompt, or a project it needs to ask
about, per RF1.3.
"""

from pathlib import Path

import click
import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from rocketdoo.cli_output import console_progress
from rocketdoo.core.project_info import project_exists
from rocketdoo.core.unpack import UnpackError, inspect, unpack

console = Console()


def _resolve_port(label: str, current_port: int, suggested_port: int, *, auto_accept: bool) -> int:
    """Asks which port to use once `inspect()` already flagged a conflict."""
    console.print(f"  [yellow]⚠[/yellow]  {label} port [cyan]{current_port}[/cyan] is already in use.")
    if auto_accept:
        console.print(f"  [dim]Auto-selecting port [green]{suggested_port}[/green][/dim]")
        return suggested_port

    console.print(f"  [dim]Suggested port: [green]{suggested_port}[/green][/dim]")
    use_suggested = questionary.confirm(
        f"Use port {suggested_port} for {label} instead of {current_port}?", default=True
    ).ask()
    return suggested_port if use_suggested else click.prompt(f"Enter {label} port to use", type=int, default=suggested_port)


def _decide_ports(info: dict, *, auto_accept: bool) -> dict:
    """Turns `inspect()`'s port_conflicts/suggested_ports into the final,
    already-decided ports `unpack()` will apply.
    """
    console.print("[bold]🔍 Checking port availability...[/bold]")

    meta = info["meta"] if info["has_meta"] else {}
    odoo_port = int(meta.get("odoo_port") or 8069)
    vsc_port = int(meta.get("vsc_port") or 8888)

    if info["port_conflicts"]["odoo_port"]:
        odoo_port = _resolve_port("Odoo", odoo_port, info["suggested_ports"]["odoo_port"], auto_accept=auto_accept)
    else:
        console.print(f"  [green]✓[/green] Odoo port [cyan]{odoo_port}[/cyan] is available.")

    if info["port_conflicts"]["vsc_port"]:
        vsc_port = _resolve_port("VSCode", vsc_port, info["suggested_ports"]["vsc_port"], auto_accept=auto_accept)
    else:
        console.print(f"  [green]✓[/green] VSCode port [cyan]{vsc_port}[/cyan] is available.")

    return {"odoo_port": odoo_port, "vsc_port": vsc_port}


def _confirm_rename(suggested: str) -> bool:
    """Answers "no" when there is nobody to ask: `questionary` raises EOFError
    without a TTY, and a scripted `rkd unpack` should not die with a traceback
    over it. `--yes` is the way to say yes in that setting.
    """
    try:
        return bool(questionary.confirm(f"Rename this copy to {suggested}?", default=True).ask())
    except EOFError:
        console.print("  [dim]No terminal to ask on; use [cyan]--yes[/cyan] to accept the rename.[/dim]")
        return False


def _decide_project_name(info: dict, *, auto_accept: bool) -> str | None:
    """Turns `inspect()`'s `project` into the name `unpack()` will apply, or
    None to keep the one in the compose.
    """
    console.print()
    console.print("[bold]🏷️  Checking the Compose project name...[/bold]")

    project = info["project"]
    if project["conflict"] is None:
        console.print(f"  [yellow]⚠[/yellow]  Name not verified: {project['reason']}.")
        return None
    if not project["conflict"]:
        if project["name"]:
            console.print(f"  [green]✓[/green] Project name [cyan]{project['name']}[/cyan] is available.")
        else:
            # Without this the header printed with nothing under it. A compose
            # with no `name:` cannot be renamed at all, and the reason is the
            # one thing worth saying here.
            console.print(f"  [yellow]⚠[/yellow]  Not checked: {project['reason']}.")
        return None

    suggested = project["suggested_name"]
    console.print(f"  [yellow]⚠[/yellow]  {project['name']}: {project['reason']}.")
    if suggested == project["name"]:
        console.print(
            "  [yellow]⚠[/yellow]  No free name found. The copy keeps the project name, "
            "so `docker compose up` will take over the other copy's containers and volumes."
        )
        return None
    if auto_accept:
        console.print(f"  [dim]Auto-selecting project name [green]{suggested}[/green][/dim]")
        return suggested
    if _confirm_rename(suggested):
        return suggested
    # Not "it will fail": sharing `name:` is not a conflict for Compose, it is
    # adoption. `up` treats the other copy's containers as this project's,
    # recreates them and mounts its volumes. Measured. Saying "probably fail"
    # would read as a harmless retry.
    console.print(
        "  [yellow]⚠[/yellow]  Keeping the name. `docker compose up` here will recreate the other copy's "
        "containers and mount its volumes, which can overwrite its database."
    )
    return None


def _select_ssh_key(meta: dict, available_keys: list[str]) -> str | None:
    """Interactive key picker for when the caller has no `--ssh-key` value.

    `unpack()` may never do this itself (RF1.3): it only receives the name
    this returns, or None to skip SSH entirely.
    """
    console.print()
    console.print("[bold]🔐 SSH configuration for private repositories:[/bold]")

    original_key = meta.get("ssh_key_name")
    if original_key:
        console.print(f"  [dim]The original environment used key: [yellow]{original_key}[/yellow][/dim]")

    console.print()

    if not available_keys:
        console.print("  [yellow]⚠[/yellow]  No SSH keys found in ~/.ssh/")
        console.print("  [dim]Generate one with: [cyan]ssh-keygen -t rsa -b 4096[/cyan][/dim]")
        questionary.confirm("Continue without SSH? (private repos will not work)", default=False).ask()
        return None

    console.print(f"  [dim]Found {len(available_keys)} SSH key(s) available.[/dim]")
    return questionary.select("Select your SSH key for private repositories:", choices=available_keys).ask()


def _decide_ssh_key(info: dict, *, ssh_key_option: str | None, no_ssh: bool) -> str | None:
    """Resolves every SSH question up front: the key to configure, or None
    to skip SSH configuration entirely (RF3.1).
    """
    if no_ssh:
        console.print("[dim]  SSH configuration skipped (--no-ssh).[/dim]")
        return None

    if ssh_key_option:
        console.print()
        console.print(f"  [dim]Using key: [cyan]{ssh_key_option}[/cyan][/dim]")
        return ssh_key_option

    meta = info["meta"] if info["has_meta"] else {}

    if info["uses_private_repos"]:
        console.print()
        console.print(
            Panel(
                "This environment was set up with [bold]private repositories[/bold].\n"
                "You need to configure [bold]your own SSH key[/bold] for it to work correctly.",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
        wants_ssh = questionary.confirm(
            "Do you use private repositories and want to configure your SSH key?", default=True
        ).ask()
        if not wants_ssh:
            return None
        selected_key = _select_ssh_key(meta, info["ssh_keys"])
        if not selected_key:
            console.print("[yellow]⚠[/yellow]  SSH not configured. Private repos may not work.")
        return selected_key

    if not info["has_meta"]:
        wants_ssh = questionary.confirm(
            "Does this environment use private repositories? (requires SSH key)", default=False
        ).ask()
        if wants_ssh:
            return _select_ssh_key(meta, info["ssh_keys"])

    return None


# ─────────────────────────────────────────────────────────────
# Main command
# ─────────────────────────────────────────────────────────────


@click.command(name="unpack")
@click.option("--no-restore", is_flag=True, default=False, help="Skip automatic database restoration.")
@click.option(
    "--build", is_flag=True, default=False, help="Rebuild the Docker image before starting (recommended on first run)."
)
@click.option("--ssh-key", "ssh_key", default=None, help="SSH key name from ~/.ssh/ to use (skips interactive selection).")
@click.option(
    "--no-ssh",
    "no_ssh",
    is_flag=True,
    default=False,
    help="Skip SSH configuration entirely (for environments without private repos).",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Auto-accept port conflict suggestions without prompting.")
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
    console.print(
        Panel(
            "[bold cyan]📥 RKD Unpack — Start shared environment[/bold cyan]\n\n"
            "[dim]This process will:[/dim]\n"
            "  [green]✓[/green] Detect and validate the shared environment\n"
            "  [green]✓[/green] Check port availability\n"
            "  [green]✓[/green] Configure your SSH key if using private repos\n"
            "  [green]✓[/green] Start the environment and restore the database\n"
            "  [green]✓[/green] Restore the filestore (Odoo stopped to avoid conflicts)",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    console.print()

    project_dir = Path.cwd()

    # Checked before any prompt: unpack() refuses a directory that is not a
    # project (UnpackError), and walking the user through the metadata, the
    # port review and the SSH questions only to refuse at the end is a worse
    # way to say the same thing.
    if not project_exists(project_dir):
        console.print("[red]✗[/red] No Rocketdoo project found in this directory.")
        console.print("[dim]💡 Extract the shared ZIP first, then run [cyan]rkd unpack[/cyan] inside it.[/dim]")
        return

    info = inspect(project_dir)

    # ── 1. Detect project and metadata ──
    if info["has_meta"]:
        meta = info["meta"]
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
        console.print(Panel(info_table, title="[bold]📋 Environment to start[/bold]", border_style="dim", box=box.ROUNDED))
        console.print()
    else:
        console.print("[yellow]⚠[/yellow]  rkd-shared.json not found.")
        console.print("[dim]This looks like a Rocketdoo project but was not packaged with [cyan]rkd pack[/cyan].[/dim]")
        if not questionary.confirm("Continue anyway?", default=False).ask():
            return

    # ── 2. Check and adjust ports ──
    console.print()
    ports = _decide_ports(info, auto_accept=yes)

    project_name = _decide_project_name(info, auto_accept=yes)
    if info["mailpit_port_conflict"]:
        console.print(
            "  [yellow]⚠[/yellow]  Mailpit is enabled and its ports (1025/8025) are in use; "
            "run [cyan]rkd mail off[/cyan] in one of the two copies."
        )

    # ── 3. Configure SSH if the environment used private repos ──
    resolved_ssh_key = _decide_ssh_key(info, ssh_key_option=ssh_key, no_ssh=no_ssh)

    # ── 4-6. Start the environment, restore the database, verify it booted ──
    console.print()
    try:
        report = unpack(
            project_dir,
            ports=ports,
            project_name=project_name,
            ssh_key=resolved_ssh_key,
            restore=not no_restore,
            build=build,
            on_progress=console_progress(console),
        )
    except UnpackError as exc:
        console.print(f"\n[red]✗[/red] {exc}")
        if exc.hint:
            console.print(f"[dim]💡 {exc.hint}[/dim]")
        console.print()
        return

    console.print()
    if report["project_renamed"]:
        console.print(
            f"[dim]Compose project renamed: [cyan]{report['renamed_from']}[/cyan] → "
            f"[cyan]{report['project_name']}[/cyan] (this is the name in rkd status and docker logs).[/dim]"
        )
    if report["started"]:
        console.print(
            Panel(
                f"[bold green]✅ Environment is ready[/bold green]\n\n"
                f"[bold]🌐 Odoo:[/bold] [cyan underline]http://localhost:{report['ports']['odoo_port']}[/cyan underline]\n"
                f"[bold]🐛 Debug:[/bold] port [cyan]{report['ports']['vsc_port']}[/cyan]\n\n"
                f"[dim]Useful commands:\n"
                f"  [cyan]rkd status[/cyan]   → check container status\n"
                f"  [cyan]rkd logs[/cyan]     → view logs\n"
                f"  [cyan]rkd info[/cyan]     → project information[/dim]",
                border_style="green",
                box=box.ROUNDED,
            )
        )
    else:
        odoo_container = report["odoo_container"]
        reason = (
            "docker compose up -d failed"
            if not report["launched"]
            else f"the web container ({odoo_container or 'web'}) is not running"
        )
        console.print(
            Panel(
                f"[bold red]⚠️  The environment did not start cleanly[/bold red]\n\n"
                f"[dim]Reason: {reason}.[/dim]\n\n"
                f"[bold]Diagnose with:[/bold]\n"
                f"  [cyan]rkd status[/cyan]                      → container status\n"
                f"  [cyan]docker logs {odoo_container or '<web>'}[/cyan]   → Odoo startup errors\n"
                f"  [cyan]rkd unpack --build[/cyan]              → rebuild the image and retry",
                border_style="red",
                box=box.ROUNDED,
            )
        )
        if report["logs_tail"]:
            output = "\n".join(report["logs_tail"])
            console.print()
            console.print("[dim]── Last lines of web container output ──[/dim]")
            console.print(f"[dim]{output[-2000:]}[/dim]")
    console.print()
