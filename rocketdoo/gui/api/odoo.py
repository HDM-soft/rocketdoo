"""Odoo database and module-state endpoints for the GUI.

Backs the Modules view's per-database Update button: lists the databases in
the project's PostgreSQL container, the install state of each module in one
of them, and builds the `docker compose exec` command that runs the update.
"""

from pathlib import Path

from fastapi import APIRouter

from rocketdoo.core.module_scanner import ModuleScanner
from rocketdoo.core.odoo_db import databases_result, list_databases, module_states

router = APIRouter()

ODOO_SERVICE = "web"  # service name from the docker-compose template


# Not `async def`: these call subprocess.run, which would block the event
# loop. Starlette runs sync handlers in a threadpool instead.
@router.get("/databases")
def get_databases():
    databases, error = databases_result()
    if error:
        return {"databases": databases, "error": error}
    return {"databases": databases}


@router.get("/module-states")
def get_module_states(db: str):
    if db not in list_databases():
        return {"states": {}, "error": "unknown database"}
    states, error = module_states(db)
    if error:
        return {"states": states, "error": error}
    return {"states": states}


def _known_modules() -> set[str]:
    """Every module name found under addons/.

    Same scanner and addons_path as GET /api/modules, but without its
    `installable` filter: whether a module can be updated is decided by its
    state in ir_module_module, not by its manifest.
    """
    scanner = ModuleScanner(addons_path=Path.cwd() / "addons")
    return {m.name for m in scanner.scan(force_rescan=True)}


def build_update_command(module: str, db: str) -> tuple[list[str] | None, str]:
    """Build the `docker compose exec` argv for updating `module` in `db`.

    Validates both by membership in the real lists, never by regex or
    escaping: this is the only barrier between the browser and the Odoo CLI.
    """
    # A directory literally named "--load-language=es" would otherwise pass
    # the membership check below and reach the Odoo CLI as a flag. Membership
    # is still the real barrier; this only closes the one case where an
    # attacker also controls addons/.
    if module.startswith("-") or db.startswith("-"):
        return None, "invalid name"
    if db not in list_databases():
        return None, "unknown database"
    if module not in _known_modules():
        return None, "unknown module"
    return [
        "docker",
        "compose",
        "exec",
        "-T",
        ODOO_SERVICE,
        "odoo",
        "-d",
        db,
        "-u",
        module,
        "--stop-after-init",
        "--log-level=info",
    ], ""
