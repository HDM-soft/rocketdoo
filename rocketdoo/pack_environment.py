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

The backup/sanitisation primitives (steps 2-7) live in `core/pack.py` (#143
T11), shared with the GUI's pack endpoint. This module keeps the wizard: the
container/database detection below decides what the user is asked, and the
questions themselves (`questionary.confirm`), which `core/` may never ask
per RF1.3.
"""

from pathlib import Path

import click
import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel

from rocketdoo.cli_output import console_progress
from rocketdoo.core.pack import MissingDatabaseError, PackError, pack

console = Console()


def _confirm_without_backup() -> bool:
    """Ask whether to go on without a database backup.

    Answers "no" when there is nobody to ask: `questionary` raises EOFError
    without a TTY, and `rkd pack` from a script or a CI job should not die
    with a traceback over a question it cannot present. `--yes` is the way to
    say yes in that setting.
    """
    try:
        return bool(questionary.confirm("Continue anyway without DB backup?", default=False).ask())
    except EOFError:
        console.print("[dim]   No terminal to ask on; use [cyan]--yes[/cyan] to pack without the backup.[/dim]")
        return False


@click.command(name="pack")
@click.option("--no-db", is_flag=True, default=False, help="Skip the database and filestore backup (environment files only).")
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(),
    help="Output path for the ZIP file (default: parent directory, named after the project).",
)
@click.option("--db-name", default=None, help="Name of the database to back up (useful when multiple databases exist).")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt and continue without a database backup if the container is unavailable.",
)
def pack_environment(no_db, output, db_name, yes):
    """
    📦 Package the development environment to share with another developer.

    Generates a ZIP with the full environment directory, a database backup,
    and the filestore — sanitizing SSH keys so they are never exposed.

    \b
    Examples:
      rkd pack                  → full backup + ZIP
      rkd pack --no-db          → environment only (no DB backup)
      rkd pack -o /tmp/my.zip   → specify output path
      rkd pack --yes            → skip the confirmation if the DB container is down
    """
    console.print()
    console.print(
        Panel(
            "[bold cyan]📦 RKD Pack — Prepare environment for sharing[/bold cyan]\n\n"
            "[dim]This process will:[/dim]\n"
            "  [green]✓[/green] Back up the database and filestore\n"
            "  [green]✓[/green] Sanitize SSH keys from the Dockerfile\n"
            "  [green]✓[/green] Generate a shareable ZIP file",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    console.print()

    project_dir = Path.cwd()

    def _pack(allow_missing_db):
        return pack(
            project_dir,
            include_db=not no_db,
            db_name=db_name,
            output=output,
            allow_missing_db=allow_missing_db,
            on_progress=console_progress(console),
        )

    try:
        try:
            report = _pack(False)
        except MissingDatabaseError as exc:
            # pack() will not decide this on its own (RF3.3) and raises before
            # writing anything, so asking and retrying costs one prompt.
            console.print(f"[yellow]⚠[/yellow]  {exc}")
            if not yes:
                console.print(
                    "[dim]   Start the environment with [cyan]rkd up -d[/cyan] before running pack with backup.[/dim]"
                )
                if not _confirm_without_backup():
                    console.print("[yellow]Operation cancelled.[/yellow]")
                    return
            report = _pack(True)
    except PackError as exc:
        console.print(f"\n[red]✗[/red] {exc}")
        if exc.hint:
            console.print(f"[dim]💡 {exc.hint}[/dim]")
        console.print()
        return

    if report["ssh_found_in_zip"]:
        console.print()
        console.print("[bold red]⚠️  SECURITY WARNING:[/bold red]")
        console.print("[red]Possible private SSH keys detected inside the ZIP:[/red]")
        for name in report["ssh_suspicious"]:
            console.print(f"  [red]• {name}[/red]")
        console.print("[dim]Please review the ZIP contents before sharing.[/dim]")

    zip_path = Path(report["zip"])
    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    if report["db_backup"]:
        db_line = "included ✓"
    elif no_db:
        db_line = "skipped (--no-db)"
    else:
        db_line = "not available"

    # Named rather than silently dropped: the ZIP is smaller than the project
    # on disk, and the one number that explains why belongs next to the count.
    stale_skipped = report.get("stale_backups_skipped") or 0
    stale_line = f" [dim](+{stale_skipped} older backup file(s) left out)[/dim]" if stale_skipped else ""

    console.print()
    console.print(
        Panel(
            f"[bold green]✅ Environment packaged successfully[/bold green]\n\n"
            f"[bold]📁 File:[/bold] [cyan]{zip_path}[/cyan]\n"
            f"[bold]📦 Files included:[/bold] {report['file_count']}{stale_line}\n"
            f"[bold]💾 Size:[/bold] {zip_size_mb:.1f} MB\n"
            f"[bold]🔐 SSH Keys:[/bold] {'excluded ✓' if report['ssh_sanitized'] else 'not applicable'}\n"
            f"[bold]💿 DB Backup:[/bold] {db_line}\n\n"
            f"[dim]Share the ZIP with the other developer.\n"
            f"The recipient should run: [cyan]rkd unpack[/cyan][/dim]",
            border_style="green",
            box=box.ROUNDED,
        )
    )
    console.print()
