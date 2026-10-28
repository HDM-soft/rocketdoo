"""Mailpit toggle: docker-compose block, odoo.conf SMTP settings and the
Rocketdoo ir.mail_server row.

Shared by `rkd mail on/off` and the GUI endpoint so the two cannot drift
(moved out of `mail_cli.py` in #143 so the GUI stops re-implementing it).
Presentation-free per the core/ contract (RF1.2): callers render their own
progress and error messages from the returned report or from MailpitError.
"""

import re
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
from rocketdoo.core.port_validation import _host_port, collect_declared_ports, find_available_port, is_port_in_use
from rocketdoo.core.project_info import read_docker_compose
from rocketdoo.core.service import ProgressCallback, ServiceError

_MARKER_START = "# rkd:mailpit"
_MARKER_END = "# /rkd:mailpit"
_WEB_SERVICE = "web"
_CONF_PATHS = ("config/odoo.conf", "odoo.conf")
_MAILPIT_WEB_TARGET = 8025
_PORT_TARGETS = (_MAILPIT_SMTP_PORT, _MAILPIT_WEB_TARGET)
_PORT_KEY = {_MAILPIT_SMTP_PORT: "smtp_port", _MAILPIT_WEB_TARGET: "web_port"}
_PORT_LINE = re.compile(r"""^(?P<head>[ \t]*#?[ \t]*-[ \t]*["']?)(?P<host>\d+)(?P<tail>:(?P<target>\d+)["']?[ \t]*\r?)$""")
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


def _port_match(line: str) -> re.Match | None:
    """Match a published-port line of the block, commented or not, for a Mailpit target."""
    match = _PORT_LINE.match(line.rstrip("\n"))
    if match and int(match["target"]) in _PORT_TARGETS:
        return match
    return None


def _rewrite_port(line: str, ports: dict[int, int], done: set[int]) -> str:
    """Swap the host side of a port line; the container side and the rest stay as written.

    Only the first line per container port, which is the one `_block_ports`
    reads. Rewriting every match while reading only the first meant a
    hand-edited block with two mappings for 1025 got both host sides set to
    the same chosen port: Compose deduplicates those rather than refusing
    them (measured), so the second mapping the user wrote disappeared without
    a word (#249).
    """
    match = _port_match(line)
    if not match or int(match["target"]) not in ports:
        return line
    if int(match["target"]) in done:
        return line
    done.add(int(match["target"]))
    newline = "\n" if line.endswith("\n") else ""
    return f"{match['head']}{ports[int(match['target'])]}{match['tail']}{newline}"


def _block_lines(content: str):
    in_block = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == _MARKER_START:
            in_block = True
        elif stripped == _MARKER_END:
            in_block = False
        elif in_block:
            yield line


def _block_ports(content: str) -> dict[int, int]:
    """{container port: published host port} declared by the block, commented or not.

    A host side outside 1-65535 is not a port, so it reads as absent and the
    caller falls back to the default for that mapping. The compose is a file
    the user edits by hand: `- "70000:1025"` used to reach `is_port_in_use()`,
    whose `connect_ex` raises `OverflowError` outside the try, and `rkd mail
    on` died with a bare traceback on a file that worked before.
    """
    ports: dict[int, int] = {}
    for line in _block_lines(content):
        match = _port_match(line)
        if match and 0 < int(match["host"]) <= 65535:
            ports.setdefault(int(match["target"]), int(match["host"]))
    return ports


def _ports_report(ports: dict[int, int]) -> dict:
    return {key: ports.get(target) for target, key in _PORT_KEY.items()}


def _own_published_ports(project_root: Path) -> set[int]:
    """Host ports this project already publishes, outside the Mailpit block."""
    compose = read_docker_compose(project_root) or {}
    services = compose.get("services")
    if not isinstance(services, dict):
        return set()
    own: set[int] = set()
    for service in services.values():
        if not isinstance(service, dict):
            continue
        for mapping in service.get("ports") or []:
            host = _host_port(mapping)
            if host:
                own.add(host)
    return own


def _choose_ports(current: dict[int, int], project_root: Path) -> tuple[dict[int, int], dict[int, int]]:
    """Keep each declared host port when free, otherwise move to the next free one.

    Free means nothing listens on it and no sibling project declares it, so a
    stopped sibling with Mailpit enabled is avoided too.

    Returns the chosen ports and, for the ones that had to move, the host port
    the compose declared before. A single flag made the caller say "the
    default Mailpit ports were taken" when one of two moved, and when the one
    that moved was never the default to begin with (#249).
    """
    if not current:
        return {}, {}
    declared = collect_declared_ports(exclude_dir=project_root, base=project_root)
    # `exclude_dir` poda este proyecto del barrido, asi que sus propios puertos
    # publicados -- el de Odoo, el del debugger -- quedaban invisibles y Mailpit
    # podia elegir justo uno de ellos: con Odoo en 8026 y el 8025 ocupado,
    # elegia 8026 y el `rkd up` siguiente fallaba, despues de informar exito.
    declared.update(dict.fromkeys(_own_published_ports(project_root), "this project"))
    chosen: dict[int, int] = {}
    moved: dict[int, int] = {}
    for target, host in sorted(current.items()):
        declared_host = host
        if host in declared or is_port_in_use(host):
            try:
                host = find_available_port(host + 1, declared=declared)
            except RuntimeError as exc:
                raise MailpitError(
                    f"No free host port for Mailpit (container port {target}).",
                    "Free a port or edit the ports of the rkd:mailpit block in docker-compose.yaml.",
                ) from exc
            moved[target] = declared_host
        declared[host] = "mailpit"
        chosen[target] = host
    return chosen, moved


def _toggle_compose(content: str, enable: bool, ports: dict[int, int] | None = None) -> str:
    """Comment/uncomment every rkd:mailpit block in the compose file.

    With `ports`, the host side of the Mailpit port lines is rewritten as the
    block is uncommented.
    """
    lines = content.splitlines(keepends=True)
    out = []
    in_block = False
    rewritten: set[int] = set()

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
                uncommented = indent + rest[1:] + "\n"
                out.append(_rewrite_port(uncommented, ports, rewritten) if ports else uncommented)
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

    content = compose.read_bytes().decode()
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
            **_ports_report(_block_ports(content)),
            "ports_moved": False,
            "ports_changed": {},
            **_apply_mail_server(enable=True, db=db, compose_data=read_docker_compose(root)),
        }

    chosen, ports_moved = _choose_ports(_block_ports(content), root)
    compose.write_bytes(_toggle_compose(content, enable=True, ports=chosen).encode())

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
        **_ports_report(chosen),
        "ports_moved": bool(ports_moved),
        "ports_changed": {_PORT_KEY[target]: previous for target, previous in ports_moved.items()},
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

    content = compose.read_bytes().decode()
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

    compose.write_bytes(_toggle_compose(content, enable=False).encode())

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
    return _is_enabled(compose.read_bytes().decode())


def published_ports(project_root: Path | str | None = None) -> dict:
    """Host ports the block publishes, `{"smtp_port", "web_port"}`; None when absent."""
    compose = compose_path(project_root)
    if not compose:
        return _ports_report({})
    return _ports_report(_block_ports(compose.read_bytes().decode()))


def status(project_root: Path | str | None = None) -> dict:
    """`{"enabled", "running", "smtp_port", "web_port"}`, what the GUI status endpoint needs."""
    return {
        "enabled": is_enabled(project_root),
        **published_ports(project_root),
        "running": container_running("mailpit", cwd=Path(project_root) if project_root else None),
    }
