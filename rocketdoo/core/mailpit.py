"""Mailpit toggle: docker-compose block, odoo.conf SMTP settings and the
Rocketdoo ir.mail_server row.

Shared by `rkd mail on/off` and the GUI endpoint so the two cannot drift
(moved out of `mail_cli.py` in #143 so the GUI stops re-implementing it).
Presentation-free per the core/ contract (RF1.2): callers render their own
progress and error messages from the returned report or from MailpitError.
"""

from pathlib import Path

from rocketdoo.core.compose import compose_path, container_running, run_compose
from rocketdoo.core.odoo_db import (
    MAILPIT_SEQUENCE,
    MAILPIT_SERVER_NAME,
    MAILPIT_SMTP_HOST,
    databases_result,
    disable_mailpit_server,
    enable_mailpit_server,
)
from rocketdoo.core.odoo_db import (
    MAILPIT_SMTP_PORT as _MAILPIT_SMTP_PORT,
)
from rocketdoo.core.project_info import read_docker_compose
from rocketdoo.core.service import ProgressCallback, ServiceError

_MARKER_START = "# rkd:mailpit"
_MARKER_END = "# /rkd:mailpit"
_WEB_SERVICE = "web"
_CONF_PATHS = ("config/odoo.conf", "odoo.conf")
_SMTP_KEYS = frozenset({"smtp_server", "smtp_port", "smtp_ssl", "smtp_user", "smtp_password"})


class MailpitError(ServiceError):
    """Mailpit cannot be toggled in this project."""


# ─── file helpers ────────────────────────────────────────────────────────────


def _odoo_conf_path(project_root: Path | str) -> Path | None:
    for rel in _CONF_PATHS:
        p = Path(project_root) / rel
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


def _resolve_db(db: str | None, compose_data: dict | None = None) -> tuple[str | None, str]:
    """Resolve which database to target for the ir.mail_server write. Never prompts.

    Returns (db, error): db is None when nothing can be safely targeted, and
    error explains why. Shared by `_apply_mail_server` (on/off) and `mail
    status`, so the 0/1/N/--db logic lives in exactly one place.

    `compose_data` comes from the caller's project_root: without it the db
    layer falls back to the process cwd, which is the exact bug RF4.5 removed
    from _get_db_container_name -- taking a directory and reading another.
    """
    databases, reason = databases_result(compose_data)
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


def _apply_mail_server(enable: bool, db: str | None, compose_data: dict | None = None) -> dict:
    """Resolve the target database and write the Mailpit ir.mail_server.

    Never raises and never prompts: the caller may be the GUI.
    Returns {"db": str | None, "db_error": str, "db_archived": int | None}.
    """
    target, error = _resolve_db(db, compose_data)
    if not target:
        return {"db": None, "db_error": error, "db_archived": None}

    if enable:
        return {"db": target, "db_error": enable_mailpit_server(target, compose_data), "db_archived": None}

    archived, error = disable_mailpit_server(target, compose_data)
    return {"db": target, "db_error": error, "db_archived": archived}


def _is_mailpit_server(server: dict) -> bool:
    """True when `server` is one `enable_mailpit_server`/`disable_mailpit_server` would touch."""
    return server["name"] == MAILPIT_SERVER_NAME and server["smtp_host"] == MAILPIT_SMTP_HOST


def _outranks_mailpit(server: dict) -> bool:
    """True when Odoo may pick `server` over the Rocketdoo one by sequence.

    Not a guarantee either way: `_find_mail_server` filters by `from_filter`
    before it sorts by sequence, so a server with a higher sequence than
    Mailpit can still win if its `from_filter` matches the sender. That case
    is covered by a separate, softer caveat in `mail status`.
    """
    return server["active"] and server["sequence"] <= MAILPIT_SEQUENCE


def _other_active_servers(servers: list[dict]) -> list[dict]:
    """Active servers other than the Rocketdoo one, for the 'Other active' row."""
    return [s for s in servers if s["active"] and not _is_mailpit_server(s)]


# ─── action functions ────────────────────────────────────────────────────────


def enable(
    project_root: Path | str | None = None,
    *,
    restart_web: bool = True,
    db: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Enable Mailpit in docker-compose.yaml and point odoo.conf at it.

    Returns a report of what actually changed, plus the outcome of writing
    the Mailpit ir.mail_server (`db`, `db_error`, `db_archived`); callers
    render their own output. Raises MailpitError when the project cannot
    support Mailpit at all.
    """
    root = Path(project_root) if project_root else Path.cwd()
    compose = compose_path(root)
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
        # unreachable. Without it, re-running `rkd mail on` after
        # `rkd up -d` would never create the record.
        return {
            "changed": False,
            "conf_found": True,
            "conf_updated": False,
            "started": False,
            "restarted": False,
            **_apply_mail_server(enable=True, db=db, compose_data=read_docker_compose(root)),
        }

    compose.write_text(_toggle_compose(content, enable=True))

    conf = _odoo_conf_path(root)
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

    started = run_compose("up", "-d", "mailpit", cwd=root) == 0

    mail_server_report = _apply_mail_server(enable=True, db=db, compose_data=read_docker_compose(root))

    restarted = False
    if restart_web and container_running(_WEB_SERVICE, cwd=root):
        run_compose("restart", _WEB_SERVICE, cwd=root)
        restarted = True

    return {
        "changed": True,
        "conf_found": conf is not None,
        "conf_updated": conf_updated,
        "started": started,
        "restarted": restarted,
        **mail_server_report,
    }


def disable(
    project_root: Path | str | None = None,
    *,
    restart_web: bool = True,
    db: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Stop Mailpit, comment its block back out and restore odoo.conf SMTP.

    Counterpart of enable(); same contract.
    """
    root = Path(project_root) if project_root else Path.cwd()
    compose = compose_path(root)
    if not compose:
        raise MailpitError("No docker-compose.yaml found.")

    content = compose.read_text()
    if not _has_markers(content):
        raise MailpitError("Mailpit block not found in docker-compose.yaml.")

    if not _is_enabled(content):
        # Same reasoning as the mirror branch in enable().
        return {
            "changed": False,
            "conf_found": True,
            "conf_updated": False,
            "restarted": False,
            **_apply_mail_server(enable=False, db=db, compose_data=read_docker_compose(root)),
        }

    run_compose("stop", "mailpit", cwd=root)
    run_compose("rm", "-f", "mailpit", cwd=root)

    compose.write_text(_toggle_compose(content, enable=False))

    conf = _odoo_conf_path(root)
    conf_updated = False
    if conf:
        before = conf.read_text()
        after = _toggle_smtp(before, enable=False)
        if after != before:
            conf.write_text(after)
            conf_updated = True

    mail_server_report = _apply_mail_server(enable=False, db=db, compose_data=read_docker_compose(root))

    restarted = False
    if restart_web and container_running(_WEB_SERVICE, cwd=root):
        run_compose("restart", _WEB_SERVICE, cwd=root)
        restarted = True

    return {
        "changed": True,
        "conf_found": conf is not None,
        "conf_updated": conf_updated,
        "restarted": restarted,
        **mail_server_report,
    }


# Pre-#143 names, kept for tests/test_mail_cli.py and any external caller
# that still imports the private spelling.
_enable_mailpit = enable
_disable_mailpit = disable


# ─── read functions ──────────────────────────────────────────────────────────


def is_enabled(project_root: Path | str | None = None) -> bool:
    """Whether the Mailpit block is currently uncommented in docker-compose.yaml."""
    compose = compose_path(project_root)
    if not compose:
        return False
    return _is_enabled(compose.read_text())


def status(project_root: Path | str | None = None) -> dict:
    """`{"enabled", "running"}`, what the GUI status endpoint needs."""
    return {
        "enabled": is_enabled(project_root),
        "running": container_running("mailpit", cwd=Path(project_root) if project_root else None),
    }
