"""
RocketDoo Mail - Mailpit email testing service integration
"""

import webbrowser
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from rocketdoo.core.compose import compose_path, container_running, run_compose
from rocketdoo.core.odoo_db import (
    MAILPIT_SEQUENCE,
    MAILPIT_SERVER_NAME,
    MAILPIT_SMTP_HOST,
    databases_result,
    disable_mailpit_server,
    enable_mailpit_server,
    mail_servers,
)

console = Console()

_MARKER_START = "# rkd:mailpit"
_MARKER_END = "# /rkd:mailpit"
_MAILPIT_SMTP_PORT = 1025
_MAILPIT_WEB_PORT = 8025
_WEB_SERVICE = "web"
_CONF_PATHS = ("config/odoo.conf", "odoo.conf")
_SMTP_KEYS = frozenset({"smtp_server", "smtp_port", "smtp_ssl", "smtp_user", "smtp_password"})


# ─── file helpers ────────────────────────────────────────────────────────────


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


# Written when the file has no smtp_* keys at all. Odoo rewrites odoo.conf the
# first time a database is created from the web UI and drops every commented
# line, including the ones the scaffold ships, so on a used project there is
# nothing left to replace.
_MAILPIT_SMTP_BLOCK = (
    "smtp_server = mailpit\n",
    f"smtp_port = {_MAILPIT_SMTP_PORT}\n",
    "smtp_ssl = False\n",
)


def _insert_after_options(lines: list[str], block: tuple[str, ...]) -> list[str]:
    """Put `block` at the end of the [options] section, or of the file.

    Everything after [options] belongs to it until another section starts, so
    appending before the next header keeps the keys where Odoo reads them.
    """
    in_options = False
    for index, line in enumerate(lines):
        header = line.strip()
        if header.startswith("[") and header.endswith("]"):
            if in_options:
                return lines[:index] + list(block) + lines[index:]
            in_options = header == "[options]"

    if not in_options:
        # No [options] header at all: create one rather than write orphan keys.
        return lines + ["\n[options]\n", *block]

    tail = lines[:]
    if tail and not tail[-1].endswith("\n"):
        tail[-1] += "\n"
    return tail + list(block)


def _toggle_smtp(content: str, enable: bool) -> str:
    """Update SMTP settings in odoo.conf for mailpit on/off."""
    lines = content.splitlines(keepends=True)
    out = []
    found = False

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

        found = True

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

    if enable and not found:
        out = _insert_after_options(out, _MAILPIT_SMTP_BLOCK)

    return "".join(out)


# ─── docker helpers ───────────────────────────────────────────────────────────


# ─── command group ────────────────────────────────────────────────────────────


class MailpitError(RuntimeError):
    """Mailpit cannot be toggled in this project."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def _resolve_db(db: str | None) -> tuple[str | None, str]:
    """Resolve which database to target for the ir.mail_server write. Never prompts.

    Returns (db, error): db is None when nothing can be safely targeted, and
    error explains why. Shared by `_apply_mail_server` (on/off) and `mail
    status`, so the 0/1/N/--db logic lives in exactly one place.
    """
    databases, reason = databases_result()
    if not databases:
        return None, reason or "no databases found"

    if db:
        if db not in databases:
            return None, f"database '{db}' not found"
        return db, ""

    if len(databases) == 1:
        return databases[0], ""

    return None, f"{len(databases)} databases found - re-run with --db NAME"


def _connectivity_hint(error: str) -> str:
    """Hint shown only when the failure is about reaching the database.

    A "which database" error (multiple found, or an unknown --db) is not
    fixed by starting the project, so it gets no hint.
    """
    if error.startswith("database '") or "re-run with --db NAME" in error:
        return ""
    return "Start the project with rkd up -d and re-run rkd mail on."


def _apply_mail_server(enable: bool, db: str | None) -> dict:
    """Resolve the target database and write the Mailpit ir.mail_server.

    Never raises and never prompts: the caller may be the GUI.
    Returns {"db": str | None, "db_error": str, "db_archived": int | None}.
    """
    target, error = _resolve_db(db)
    if not target:
        return {"db": None, "db_error": error, "db_archived": None}

    if enable:
        return {"db": target, "db_error": enable_mailpit_server(target), "db_archived": None}

    archived, error = disable_mailpit_server(target)
    return {"db": target, "db_error": error, "db_archived": archived}


def _is_mailpit_server(server: dict) -> bool:
    """True when `server` is one `enable_mailpit_server`/`disable_mailpit_server` would touch."""
    return server["name"] == MAILPIT_SERVER_NAME and server["smtp_host"] == MAILPIT_SMTP_HOST


def _outranks_mailpit(server: dict) -> bool:
    """True when Odoo may pick `server` over the Rocketdoo one by sequence.

    Not a guarantee either way: `_find_mail_server` filters by `from_filter`
    before it sorts by sequence, so a server with a higher sequence than
    Mailpit can still win if its `from_filter` matches the sender. That case
    is covered by a separate, softer caveat in `mail status` (RF-3.5).
    """
    return server["active"] and server["sequence"] <= MAILPIT_SEQUENCE


def _other_active_servers(servers: list[dict]) -> list[dict]:
    """Active servers other than the Rocketdoo one, for the 'Other active' row."""
    return [s for s in servers if s["active"] and not _is_mailpit_server(s)]


def _mailpit_server_line(db: str | None, error: str) -> tuple[str, str, list[dict]]:
    """Mailpit row text, its Rich style, and the other active servers in `db`.

    Never raises: an unresolved `db` (from `_resolve_db`) or a failed query
    becomes text in the row (RF-3.6), styled the same as "nothing to worry
    about yet" so a stopped project does not read as an error.
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


def _enable_mailpit(restart_web: bool = True, db: str | None = None) -> dict:
    """Enable Mailpit in docker-compose.yaml and point odoo.conf at it.

    Shared by `rkd mail on` and the GUI endpoint so the two cannot drift.
    Returns a report of what actually changed, plus the outcome of writing
    the Mailpit ir.mail_server (`db`, `db_error`, `db_archived`); callers
    render their own output. Raises MailpitError when the project cannot
    support Mailpit at all.
    """
    compose = compose_path()
    if not compose:
        raise MailpitError("No docker-compose.yaml found.", "Run rkd init first.")

    content = compose.read_text()
    if not _has_markers(content):
        raise MailpitError(
            "Mailpit block not found in docker-compose.yaml.",
            "This project was initialized before v3. Re-run rkd scaffold to update the template.",
        )

    if _is_enabled(content):
        # Still write the mail server: this is the documented fix for a
        # first run that toggled the compose while the db container was
        # unreachable (RF-1.3). Without it, re-running `rkd mail on` after
        # `rkd up -d` would never create the record.
        return {
            "changed": False,
            "conf_found": True,
            "conf_updated": False,
            "started": False,
            "restarted": False,
            **_apply_mail_server(enable=True, db=db),
        }

    compose.write_text(_toggle_compose(content, enable=True))

    conf = _odoo_conf_path()
    conf_updated = False
    if conf:
        # Compared, not assumed: the caller reports this to the user, and it
        # used to claim the SMTP settings had been written even when nothing
        # changed.
        before = conf.read_text()
        after = _toggle_smtp(before, enable=True)
        if after != before:
            conf.write_text(after)
            conf_updated = True

    started = run_compose("up", "-d", "mailpit") == 0

    mail_server_report = _apply_mail_server(enable=True, db=db)

    restarted = False
    if restart_web and container_running(_WEB_SERVICE):
        run_compose("restart", _WEB_SERVICE)
        restarted = True

    return {
        "changed": True,
        "conf_found": conf is not None,
        "conf_updated": conf_updated,
        "started": started,
        "restarted": restarted,
        **mail_server_report,
    }


def _disable_mailpit(restart_web: bool = True, db: str | None = None) -> dict:
    """Stop Mailpit, comment its block back out and restore odoo.conf SMTP.

    Counterpart of _enable_mailpit; same contract.
    """
    compose = compose_path()
    if not compose:
        raise MailpitError("No docker-compose.yaml found.")

    content = compose.read_text()
    if not _has_markers(content):
        raise MailpitError("Mailpit block not found in docker-compose.yaml.")

    if not _is_enabled(content):
        # Same reasoning as the mirror branch in _enable_mailpit (RF-2.5).
        return {
            "changed": False,
            "conf_found": True,
            "conf_updated": False,
            "restarted": False,
            **_apply_mail_server(enable=False, db=db),
        }

    run_compose("stop", "mailpit")
    run_compose("rm", "-f", "mailpit")

    compose.write_text(_toggle_compose(content, enable=False))

    conf = _odoo_conf_path()
    conf_updated = False
    if conf:
        before = conf.read_text()
        after = _toggle_smtp(before, enable=False)
        if after != before:
            conf.write_text(after)
            conf_updated = True

    mail_server_report = _apply_mail_server(enable=False, db=db)

    restarted = False
    if restart_web and container_running(_WEB_SERVICE):
        run_compose("restart", _WEB_SERVICE)
        restarted = True

    return {
        "changed": True,
        "conf_found": conf is not None,
        "conf_updated": conf_updated,
        "restarted": restarted,
        **mail_server_report,
    }


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
        report = _enable_mailpit(db=db)
    except MailpitError as exc:
        console.print(f"\n[yellow]{exc}[/yellow]")
        if exc.hint:
            console.print(f"[dim]{exc.hint}[/dim]")
        console.print()
        return

    if not report["changed"]:
        console.print("\n[green]Mailpit is already enabled.[/green]")
        _print_enable_mail_server(report)
        console.print(f"[dim]Web UI \u2192 http://localhost:{_MAILPIT_WEB_PORT}[/dim]\n")
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
    elif report["conf_found"]:
        console.print("[green]\u2713[/green] odoo.conf already pointed at mailpit")
    else:
        console.print("[yellow]\u26a0 odoo.conf not found \u2014 update SMTP settings manually[/yellow]")

    if report["started"]:
        console.print("[green]\u2713[/green] mailpit started")
    else:
        console.print("[yellow]\u26a0 Could not start mailpit (is Docker running?)[/yellow]")

    _print_enable_mail_server(report)

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
@click.option("--db", "db", default=None, help="Database to archive the mail server in.")
def mail_off(db):
    """Disable Mailpit and restore default SMTP settings."""
    try:
        report = _disable_mailpit(db=db)
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

    console.print("[green]\u2713[/green] mailpit stopped")
    console.print("[green]\u2713[/green] docker-compose.yaml updated")
    if report["conf_updated"]:
        console.print("[green]\u2713[/green] odoo.conf SMTP settings restored to defaults")
    elif report["conf_found"]:
        console.print("[green]\u2713[/green] odoo.conf already had the defaults")
    else:
        console.print("[yellow]\u26a0 odoo.conf not found[/yellow]")

    _print_disable_mail_server(report)

    if report["restarted"]:
        console.print(f"[green]\u2713[/green] {_WEB_SERVICE} restarted")

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
