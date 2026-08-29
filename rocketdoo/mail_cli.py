"""
RocketDoo Mail - Mailpit email testing service integration
"""

import subprocess
import webbrowser
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

_MARKER_START = "# rkd:mailpit"
_MARKER_END = "# /rkd:mailpit"
_MAILPIT_SMTP_PORT = 1025
_MAILPIT_WEB_PORT = 8025
_WEB_SERVICE = "web"
_COMPOSE_NAMES = ("docker-compose.yaml", "docker-compose.yml")
_CONF_PATHS = ("config/odoo.conf", "odoo.conf")
_SMTP_KEYS = frozenset({"smtp_server", "smtp_port", "smtp_ssl", "smtp_user", "smtp_password"})


# ─── file helpers ────────────────────────────────────────────────────────────


def _compose_path() -> Path | None:
    for name in _COMPOSE_NAMES:
        p = Path.cwd() / name
        if p.exists():
            return p
    return None


def _odoo_conf_path() -> Path | None:
    for rel in _CONF_PATHS:
        p = Path.cwd() / rel
        if p.exists():
            return p
    return None


# ─── toggle logic ────────────────────────────────────────────────────────────


def _has_markers(content: str) -> bool:
    return _MARKER_START in content


def _is_enabled(content: str) -> bool:
    """Return True if the mailpit block is uncommented."""
    in_block = False
    for line in content.splitlines():
        s = line.strip()
        if s == _MARKER_START:
            in_block = True
        elif s == _MARKER_END:
            in_block = False
        elif in_block and s and not s.startswith("#"):
            return True
    return False


def _toggle_compose(content: str, enable: bool) -> str:
    """Comment/uncomment every rkd:mailpit block in the compose file."""
    lines = content.splitlines(keepends=True)
    out = []
    in_block = False

    for line in lines:
        rline = line.rstrip("\n")
        stripped = rline.strip()

        if stripped == _MARKER_START:
            in_block = True
            out.append(line)
            continue
        if stripped == _MARKER_END:
            in_block = False
            out.append(line)
            continue
        if not in_block:
            out.append(line)
            continue

        indent = rline[: len(rline) - len(rline.lstrip())]
        rest = rline.lstrip()

        if enable:
            # Remove the leading '#' to uncomment
            if rest.startswith("#"):
                out.append(indent + rest[1:] + "\n")
            else:
                out.append(line)
        else:
            # Add '#' after indent to comment out
            if rest and not rest.startswith("#"):
                out.append(indent + "#" + rest + "\n")
            else:
                out.append(line)

    return "".join(out)


def _toggle_smtp(content: str, enable: bool) -> str:
    """Update SMTP settings in odoo.conf for mailpit on/off."""
    lines = content.splitlines(keepends=True)
    out = []

    for line in lines:
        stripped = line.strip()
        # Normalize: strip leading `;` comment marker (odoo.conf style)
        normalized = stripped.lstrip("; ").strip()

        if "=" not in normalized:
            out.append(line)
            continue

        key = normalized.split("=")[0].strip()
        if key not in _SMTP_KEYS:
            out.append(line)
            continue

        if enable:
            if key == "smtp_server":
                out.append("smtp_server = mailpit\n")
            elif key == "smtp_port":
                out.append(f"smtp_port = {_MAILPIT_SMTP_PORT}\n")
            elif key == "smtp_ssl":
                out.append("smtp_ssl = False\n")
            else:
                out.append(f"; {normalized}\n")
        else:
            if key == "smtp_server":
                out.append("; smtp_server = localhost\n")
            elif key == "smtp_port":
                out.append("; smtp_port = 25\n")
            elif key == "smtp_ssl":
                out.append("; smtp_ssl = False\n")
            else:
                out.append(f"; {normalized}\n")

    return "".join(out)


# ─── docker helpers ───────────────────────────────────────────────────────────


def _run_compose(*args: str) -> int:
    result = subprocess.run(["docker", "compose", *args], cwd=Path.cwd())
    return result.returncode


def _container_running(service: str) -> bool:
    result = subprocess.run(["docker", "compose", "ps", "-q", service], capture_output=True, text=True, cwd=Path.cwd())
    return bool(result.stdout.strip())


# ─── command group ────────────────────────────────────────────────────────────


class MailpitError(RuntimeError):
    """Mailpit cannot be toggled in this project."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def _enable_mailpit(restart_web: bool = True) -> dict:
    """Enable Mailpit in docker-compose.yaml and point odoo.conf at it.

    Shared by `rkd mail on` and the GUI endpoint so the two cannot drift.
    Returns a report of what actually changed; callers render their own output.
    Raises MailpitError when the project cannot support Mailpit at all.
    """
    compose = _compose_path()
    if not compose:
        raise MailpitError("No docker-compose.yaml found.", "Run rkd init first.")

    content = compose.read_text()
    if not _has_markers(content):
        raise MailpitError(
            "Mailpit block not found in docker-compose.yaml.",
            "This project was initialized before v3. Re-run rkd scaffold to update the template.",
        )

    if _is_enabled(content):
        return {"changed": False, "conf_updated": False, "started": False, "restarted": False}

    compose.write_text(_toggle_compose(content, enable=True))

    conf = _odoo_conf_path()
    conf_updated = False
    if conf:
        conf.write_text(_toggle_smtp(conf.read_text(), enable=True))
        conf_updated = True

    started = _run_compose("up", "-d", "mailpit") == 0

    restarted = False
    if restart_web and _container_running(_WEB_SERVICE):
        _run_compose("restart", _WEB_SERVICE)
        restarted = True

    return {"changed": True, "conf_updated": conf_updated, "started": started, "restarted": restarted}


def _disable_mailpit(restart_web: bool = True) -> dict:
    """Stop Mailpit, comment its block back out and restore odoo.conf SMTP.

    Counterpart of _enable_mailpit; same contract.
    """
    compose = _compose_path()
    if not compose:
        raise MailpitError("No docker-compose.yaml found.")

    content = compose.read_text()
    if not _has_markers(content):
        raise MailpitError("Mailpit block not found in docker-compose.yaml.")

    if not _is_enabled(content):
        return {"changed": False, "conf_updated": False, "restarted": False}

    _run_compose("stop", "mailpit")
    _run_compose("rm", "-f", "mailpit")

    compose.write_text(_toggle_compose(content, enable=False))

    conf = _odoo_conf_path()
    conf_updated = False
    if conf:
        conf.write_text(_toggle_smtp(conf.read_text(), enable=False))
        conf_updated = True

    restarted = False
    if restart_web and _container_running(_WEB_SERVICE):
        _run_compose("restart", _WEB_SERVICE)
        restarted = True

    return {"changed": True, "conf_updated": conf_updated, "restarted": restarted}


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
def mail_on():
    """Enable Mailpit for outgoing email testing."""
    try:
        report = _enable_mailpit()
    except MailpitError as exc:
        console.print(f"\n[yellow]{exc}[/yellow]")
        if exc.hint:
            console.print(f"[dim]{exc.hint}[/dim]")
        console.print()
        return

    if not report["changed"]:
        console.print(
            f"\n[green]Mailpit is already enabled.[/green]\n[dim]Web UI \u2192 http://localhost:{_MAILPIT_WEB_PORT}[/dim]\n"
        )
        return

    console.print()
    console.print(
        Panel(
            "[bold cyan]Enabling Mailpit[/bold cyan]\n\n"
            "[dim]SMTP testing service \u2014 all outgoing emails will be captured[/dim]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    console.print()

    console.print("[green]\u2713[/green] docker-compose.yaml updated")
    if report["conf_updated"]:
        console.print(f"[green]\u2713[/green] odoo.conf \u2192 smtp_server = mailpit, smtp_port = {_MAILPIT_SMTP_PORT}")
    else:
        console.print("[yellow]\u26a0 odoo.conf not found \u2014 update SMTP settings manually[/yellow]")

    if report["started"]:
        console.print("[green]\u2713[/green] mailpit started")
    else:
        console.print("[yellow]\u26a0 Could not start mailpit (is Docker running?)[/yellow]")

    if report["restarted"]:
        console.print(f"[green]\u2713[/green] {_WEB_SERVICE} restarted")

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
def mail_off():
    """Disable Mailpit and restore default SMTP settings."""
    try:
        report = _disable_mailpit()
    except MailpitError as exc:
        console.print(f"\n[yellow]{exc}[/yellow]\n")
        return

    if not report["changed"]:
        console.print("\n[dim]Mailpit is already disabled.[/dim]\n")
        return

    console.print()
    console.print(Panel("[bold cyan]Disabling Mailpit[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    console.print()

    console.print("[green]\u2713[/green] mailpit stopped")
    console.print("[green]\u2713[/green] docker-compose.yaml updated")
    if report["conf_updated"]:
        console.print("[green]\u2713[/green] odoo.conf SMTP settings restored to defaults")
    else:
        console.print("[yellow]\u26a0 odoo.conf not found[/yellow]")

    if report["restarted"]:
        console.print(f"[green]\u2713[/green] {_WEB_SERVICE} restarted")

    console.print()
    console.print("[dim]Mailpit disabled. Emails are no longer captured locally.[/dim]\n")


@mail.command(name="status")
def mail_status():
    """Show current Mailpit status."""
    compose = _compose_path()

    configured = False
    enabled = False
    running = False

    if compose:
        content = compose.read_text()
        configured = _has_markers(content)
        enabled = configured and _is_enabled(content)
        running = enabled and _container_running("mailpit")

    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_column("Key", style="cyan bold", width=22)
    table.add_column("Value")

    table.add_row("Configured in compose", "[green]Yes[/green]" if configured else "[red]No — run rkd scaffold[/red]")
    table.add_row("Enabled", "[green]Yes[/green]" if enabled else "[yellow]No — run rkd mail on[/yellow]")
    table.add_row("Container running", "[green]Yes[/green]" if running else "[dim]No[/dim]")

    if running:
        table.add_row("Web UI", f"[cyan underline]http://localhost:{_MAILPIT_WEB_PORT}[/cyan underline]")
        table.add_row("SMTP", f"localhost:{_MAILPIT_SMTP_PORT}")

    console.print()
    console.print(
        Panel(table, title="[bold cyan]Mailpit Status[/bold cyan]", border_style="cyan", box=box.ROUNDED, padding=(1, 2))
    )
    console.print()


@mail.command(name="open")
def mail_open():
    """Open Mailpit web UI in the browser."""
    if not _container_running("mailpit"):
        console.print("\n[yellow]Mailpit is not running.[/yellow] Run [cyan bold]rkd mail on[/cyan bold] first.\n")
        return

    url = f"http://localhost:{_MAILPIT_WEB_PORT}"
    webbrowser.open(url)
    console.print(f"\n[green]✓[/green] Opened [cyan underline]{url}[/cyan underline]\n")
