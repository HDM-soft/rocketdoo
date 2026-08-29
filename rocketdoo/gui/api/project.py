from fastapi import APIRouter
from fastapi.responses import JSONResponse

from rocketdoo.core.compose import compose_ps_result

router = APIRouter()


@router.get("")
async def get_project():
    """Returns project info and running container statuses."""
    try:
        from rocketdoo.project_info import get_project_info, project_exists

        exists = project_exists()
        if not exists:
            return {"exists": False}
        info = get_project_info()
        info["exists"] = True
        return info
    except Exception as e:
        return JSONResponse({"exists": False, "error": str(e)}, status_code=200)


@router.get("/containers")
async def get_containers():
    """Returns docker ps output for the current project."""
    containers, error = compose_ps_result("--all")
    if error:
        return {"containers": containers, "error": error}
    return {"containers": containers}
