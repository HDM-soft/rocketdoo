"""The seeding step of a deploy: dump locally, ship, restore on the server.

Shared by the two deployers; what differs between them (how to run a script,
which restore script to send) arrives as arguments. The scripts themselves
live in `db_seed`.
"""

import shlex
import tempfile
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from rocketdoo.cli_output import console_progress
from rocketdoo.core.odoo_db import db_container, dump_database, dump_filestore
from rocketdoo.core.project_info import read_docker_compose

from .db_seed import DUMP_FILE, FILESTORE_FILE, RESTORE_WARNING, parse_state

RunScript = Callable[[str, int], tuple[int, str, str]]

_STATE_TIMEOUT = 600
_RESTORE_TIMEOUT = 7200


def seed_dir(env: str) -> str:
    """Where the dump waits on the server: disk-backed, and outside any build context."""
    return f"/var/tmp/rkd-seed-{env}"


def _warning(detail: str, hint: str) -> dict:
    return {"name": "seed restore", "level": "warn", "ok": False, "detail": detail, "hint": hint}


def seed(
    *,
    project_path: Path,
    env: str,
    source_db: str,
    db_name: str,
    state_script: str,
    restore_script: Callable[[bool], str],
    run: RunScript,
    ssh: Callable[[str], bool],
    rsync: Callable[[str, str], bool],
    console: Console,
    before_restore: str = "",
) -> tuple[bool, list[dict]]:
    """Seed `db_name` on the server from the local `source_db`.

    Returns (ok, warnings). A database that already carries the seed mark is
    left alone without dumping or shipping anything: that is what makes a
    redeploy safe, and it is decided on the server because the mark lives in
    the database.
    """
    rc, out, err = run(state_script, _STATE_TIMEOUT)
    if err:
        console.print(err, markup=False, end="")
    state = parse_state(out) if rc == 0 else ""
    if not state:
        console.print("[red]✗ Could not read the state of the database on the server[/red]")
        return False, []
    if state == "seeded":
        console.print(f"[dim]  Database {db_name} was already seeded: left alone.[/dim]")
        return True, []
    if state == "initialised":
        console.print(
            f"[red]✗ Database {db_name} on the server is already initialised and was not seeded from a local "
            "database. Restoring over it would destroy what it holds; drop it on the server to seed it.[/red]"
        )
        return False, []

    compose = read_docker_compose(project_path) or {}
    report = console_progress(console)
    local_db_container = db_container(compose)
    try:
        odoo_container = compose["services"]["web"]["container_name"]
    except (KeyError, TypeError):
        odoo_container = ""
    if not local_db_container or not odoo_container:
        console.print("[red]✗ The local compose does not name the db and web containers to dump from[/red]")
        return False, []

    remote_dir = seed_dir(env)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dump, tarball = Path(tmp) / DUMP_FILE, Path(tmp) / FILESTORE_FILE
            if not dump_database(local_db_container, source_db, dump, report):
                return False, []
            filestore_ok, _ = dump_filestore(odoo_container, source_db, tarball, report)
            if not filestore_ok:
                console.print("[red]✗ Without its filestore every attachment of the database would be broken[/red]")
                return False, []
            has_filestore = tarball.is_file() and tarball.stat().st_size > 0
            console.print("[dim]  Transferring the dump to the server...[/dim]")
            if not rsync(f"{tmp}/", f"{remote_dir}/"):
                console.print("[red]✗ Failed to transfer the dump[/red]")
                return False, []

        rc, out, err = run(before_restore + restore_script(has_filestore), _RESTORE_TIMEOUT)
    finally:
        ssh(f"rm -rf {shlex.quote(remote_dir)}")
    if out:
        console.print(out, markup=False, end="")
    if err:
        console.print(err, markup=False, end="")
    if rc != 0:
        console.print("[red]✗ The restore failed[/red]")
        return False, []
    if RESTORE_WARNING in err:
        return True, [
            _warning(
                f"pg_restore reported errors restoring '{source_db}' as '{db_name}' and ignored some statements",
                "Usually roles and ACLs that --no-owner/--no-acl skip, but it can also be lost data: read the "
                "pg_restore output above and compare the instance with the local database.",
            )
        ]
    return True, []
