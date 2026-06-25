import subprocess
from fastapi import APIRouter
from fastapi.responses import JSONResponse

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
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json", "--all"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"containers": [], "error": result.stderr.strip()}

        import json
        containers = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return {"containers": containers}
    except FileNotFoundError:
        return {"containers": [], "error": "docker not found"}
    except Exception as e:
        return {"containers": [], "error": str(e)}
