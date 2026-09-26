"""
RocketDoo Mail - Mailpit email testing service integration
"""

import webbrowser

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from rocketdoo.core.compose import compose_path, container_running
from rocketdoo.core.mailpit import (
    _MAILPIT_SMTP_PORT,
    _WEB_SERVICE,
    MailpitError,
    _connectivity_hint,
    _has_markers,
    _is_enabled,
    _is_mailpit_server,
    _other_active_servers,
    _outranks_mailpit,
    _resolve_db,
    disable,
    enable,
)

# MAILPIT_SMTP_HOST is not used below: tests/test_mail_cli.py reads it off
# this module as mail_cli.MAILPIT_SMTP_HOST, so it stays re-exported here.
from rocketdoo.core.odoo_db import MAILPIT_SERVER_NAME, MAILPIT_SMTP_HOST, mail_servers  # noqa: F401

console = Console()

_MAILPIT_WEB_PORT = 8025


# ─── status presentation helpers ─────────────────────────────────────────────


def _mailpit_server_line(db: str | None, error: str) -> tuple[str, str, list[dict]]:
    """Mailpit row text, its Rich style, and the other active servers in `db`.

    Never raises: an unresolved `db` (from `_resolve_db`) or a failed query
    becomes text in the row, styled the same as "nothing to worry about yet"
    so a stopped project does not read as an error.
    """
    if not db:
        return f"Not checked — {error}", "dim", []

    servers, query_error = mail_servers(db)
    if query_error:
        return f"Not checked — {query_error}", "dim", []

    matches = [s for s in servers if _is_mailpit_server(s)]
    active = next((s for s in matches if s["active"]), None)
    others = _other_active_servers(servers)

    if active:
        return f"Active (sequence {active['sequence']}) in {db}", "green", others
    if matches:
        return f"Archived in {db}", "dim", others
    return "Not created — run rkd mail on", "dim", others


def _format_other_servers(others: list[dict]) -> str:
    if not others:
        return "None"
    return ", ".join(f'"{s["name"]}" (sequence {s["sequence"]})' for s in others)


def _print_enable_mail_server(report: dict) -> None:
    if report["db_error"]:
        console.print(f"[yellow]⚠ mail server not configured: {report['db_error']}[/yellow]")
        hint = _connectivity_hint(report["db_error"])
        if hint:
            console.print(f"[dim]{hint}[/dim]")
    else:
        console.print(f'[green]✓[/green] mail server "{MAILPIT_SERVER_NAME}" ready in database {report["db"]}')


def _print_disable_mail_server(report: dict) -> None:
    if report["db_error"]:
        console.print(f"[yellow]⚠ mail server not configured: {report['db_error']}[/yellow]")
        hint = _connectivity_hint(report["db_error"])
        if hint:
            console.print(f"[dim]{hint}[/dim]")
    elif report["db_archived"]:
        console.print(f'[green]✓[/green] mail server "{MAILPIT_SERVER_NAME}" archived in database {report["db"]}')
    else:
        console.print(f"[dim]no Rocketdoo mail server found in database {report['db']}[/dim]")


# ─── command group ────────────────────────────────────────────────────────────


@click.group(name="mail")
def mail():
    """Manage Mailpit email testing service.

    \b
    Examples:

    \b
    # Enable Mailpit (start service + configure Odoo SMTP)
    rkd mail on

    \b
    # Disable Mailpit (stop service + restore SMTP defaults)
    rkd mail off

    \b
    # Show current status
    rkd mail status

    \b
    # Open Mailpit web UI in browser
    rkd mail open
    """
    pass


@mail.command(name="on")
@click.option("--db", "db", default=None, help="Database to write the mail server to.")
def mail_on(db):
    """Enable Mailpit for outgoing email testing."""
    try:
        report = enable(db=db)
    except MailpitError as exc:
        console.print(f"\n[yellow]{exc}[/yellow]")
        if exc.hint:
            console.print(f"[dim]{exc.hint}[/dim]")
        console.print()
        return

    if not report["changed"]:
        console.print("\n[green]Mailpit is already enabled.[/green]")
        _print_enable_mail_server(report)
        console.print(f"[dim]Web UI → http://localhost:{_MAILPIT_WEB_PORT}[/dim]\n")
        return

    console.print()
    console.print(
        Panel(
            "[bold cyan]Enabling Mailpit[/bold cyan]\n\n"
            "[dim]SMTP testing service — all outgoing emails will be captured[/dim]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    console.print()

    console.print("[green]✓[/green] docker-compose.yaml updated")
    if report["conf_updated"]:
        console.print(f"[green]✓[/green] odoo.conf → smtp_server = mailpit, smtp_port = {_MAILPIT_SMTP_PORT}")
    elif report["conf_found"]:
        console.print("[green]✓[/green] odoo.conf already pointed at mailpit")
    else:
        console.print("[yellow]⚠ odoo.conf not found — update SMTP settings manually[/yellow]")

    if report["started"]:
        console.print("[green]✓[/green] mailpit started")
    else:
        console.print("[yellow]⚠ Could not start mailpit (is Docker running?)[/yellow]")

    _print_enable_mail_server(report)

    if report["restarted"]:
        console.print(f"[green]✓[/green] {_WEB_SERVICE} restarted")

    console.print()
    console.print(
        Panel(
            "[bold green]Mailpit is ready[/bold green]\n\n"
            f"[dim]Web UI :[/dim]  [cyan underline]http://localhost:{_MAILPIT_WEB_PORT}[/cyan underline]\n"
            f"[dim]SMTP   :[/dim]  localhost:{_MAILPIT_SMTP_PORT}\n\n"
            "[dim]All outgoing emails from Odoo will be captured here instead of being sent.[/dim]",
            border_style="green",
            box=box.ROUNDED,
        )
    )
    console.print()


@mail.command(name="off")
@click.option("--db", "db", default=None, help="Database to archive the mail server in.")
def mail_off(db):
    """Disable Mailpit and restore default SMTP settings."""
    try:
        report = disable(db=db)
    except MailpitError as exc:
        console.print(f"\n[yellow]{exc}[/yellow]\n")
        return

    if not report["changed"]:
        console.print("\n[dim]Mailpit is already disabled.[/dim]")
        _print_disable_mail_server(report)
        console.print()
        return

    console.print()
    console.print(Panel("[bold cyan]Disabling Mailpit[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    console.print()

    console.print("[green]✓[/green] mailpit stopped")
    console.print("[green]✓[/green] docker-compose.yaml updated")
    if report["conf_updated"]:
        console.print("[green]✓[/green] odoo.conf SMTP settings restored to defaults")
    elif report["conf_found"]:
        console.print("[green]✓[/green] odoo.conf already had the defaults")
    else:
        console.print("[yellow]⚠ odoo.conf not found[/yellow]")

    _print_disable_mail_server(report)

    if report["restarted"]:
        console.print(f"[green]✓[/green] {_WEB_SERVICE} restarted")

    console.print()
    console.print("[dim]Mailpit disabled. Emails are no longer captured locally.[/dim]\n")


@mail.command(name="status")
@click.option("--db", "db", default=None, help="Database to check the mail server in.")
def mail_status(db):
    """Show current Mailpit status."""
    compose = compose_path()

    configured = False
    enabled = False
    running = False

    if compose:
        content = compose.read_text()
        configured = _has_markers(content)
        enabled = configured and _is_enabled(content)
        running = enabled and container_running("mailpit")

    target, error = _resolve_db(db)
    mailpit_line, mailpit_style, others = _mailpit_server_line(target, error)

    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_column("Key", style="cyan bold", width=22)
    table.add_column("Value")

    table.add_row("Configured in compose", "[green]Yes[/green]" if configured else "[red]No — run rkd scaffold[/red]")
    table.add_row("Enabled", "[green]Yes[/green]" if enabled else "[yellow]No — run rkd mail on[/yellow]")
    table.add_row("Container running", "[green]Yes[/green]" if running else "[dim]No[/dim]")

    if running:
        table.add_row("Web UI", f"[cyan underline]http://localhost:{_MAILPIT_WEB_PORT}[/cyan underline]")
        table.add_row("SMTP", f"localhost:{_MAILPIT_SMTP_PORT}")

    table.add_row("Mailpit mail server", f"[{mailpit_style}]{mailpit_line}[/{mailpit_style}]")
    table.add_row("Other active servers", _format_other_servers(others))

    console.print()
    console.print(
        Panel(table, title="[bold cyan]Mailpit Status[/bold cyan]", border_style="cyan", box=box.ROUNDED, padding=(1, 2))
    )

    outranking = [s for s in others if _outranks_mailpit(s)]
    if outranking:
        names = ", ".join(f'"{s["name"]}"' for s in outranking)
        console.print(f"[yellow]⚠ {names} has priority over Mailpit[/yellow]")
    elif others:
        console.print("[dim]Odoo may still pick one of them if its from_filter matches the sender.[/dim]")

    console.print()


@mail.command(name="open")
def mail_open():
    """Open Mailpit web UI in the browser."""
    if not container_running("mailpit"):
        console.print("\n[yellow]Mailpit is not running.[/yellow] Run [cyan bold]rkd mail on[/cyan bold] first.\n")
        return

    url = f"http://localhost:{_MAILPIT_WEB_PORT}"
    webbrowser.open(url)
    console.print(f"\n[green]✓[/green] Opened [cyan underline]{url}[/cyan underline]\n")
