"""Odoo database and module-state endpoints for the GUI.

Backs the Modules view's per-database Update button: lists the databases in
the project's PostgreSQL container and the install state of each module in
one of them.
"""

from fastapi import APIRouter

from rocketdoo.core.odoo_db import databases_result, list_databases, module_states

router = APIRouter()


@router.get("/databases")
async def get_databases():
    databases, error = databases_result()
    if error:
        return {"databases": databases, "error": error}
    return {"databases": databases}


@router.get("/module-states")
async def get_module_states(db: str):
    if db not in list_databases():
        return {"states": {}, "error": "unknown database"}
    states, error = module_states(db)
    if error:
        return {"states": states, "error": error}
    return {"states": states}
