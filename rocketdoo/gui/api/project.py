from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from rocketdoo.core.compose import compose_ps_result

router = APIRouter()


@router.get("")
def get_project():
    """Returns project info and running container statuses."""
    try:
        from rocketdoo.core.project_info import get_project_info, project_exists

        # One read of the cwd, passed to both: a navigate() in another thread
        # could otherwise answer for a project that is not the one it checked.
        root = Path.cwd()
        exists = project_exists(root)
        if not exists:
            return {"exists": False}
        # `model_dump()` and not the model itself: the SPA reads these keys
        # by name, so what goes over the wire stays the flat object it has
        # always been, with `exists` added beside it (#204).
        return {**get_project_info(root).model_dump(), "exists": True}
    except Exception as e:
        return JSONResponse({"exists": False, "error": str(e)}, status_code=200)


@router.get("/containers")
def get_containers():
    """Returns docker ps output for the current project."""
    containers, error = compose_ps_result("--all")
    if error:
        return {"containers": containers, "error": error}
    return {"containers": containers}
