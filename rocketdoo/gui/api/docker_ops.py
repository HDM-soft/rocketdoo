import subprocess
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from rocketdoo.core.addons_path import ensure_addons_path
from rocketdoo.core.compose import run_compose_result

router = APIRouter()


@router.post("/up")
async def docker_up():
    ensure_addons_path(Path.cwd())
    return run_compose_result("up", "-d")


@router.post("/down")
async def docker_down():
    return run_compose_result("down")


@router.post("/restart")
async def docker_restart():
    return run_compose_result("restart")


@router.post("/stop")
async def docker_stop():
    return run_compose_result("stop")


@router.post("/build")
async def docker_build():
    return run_compose_result("build", timeout=300)


class ServiceAction(BaseModel):
    service: str


@router.post("/service/start")
async def service_start(body: ServiceAction):
    return run_compose_result("start", body.service)


@router.post("/service/stop")
async def service_stop(body: ServiceAction):
    return run_compose_result("stop", body.service)


@router.post("/service/restart")
async def service_restart(body: ServiceAction):
    return run_compose_result("restart", body.service)


@router.get("/logs/{container_name}")
async def get_logs(container_name: str, tail: int = 200):
    """Returns last N lines of logs for a container (non-streaming)."""
    result = subprocess.run(
        ["docker", "logs", "--tail", str(tail), container_name],
        capture_output=True,
        text=True,
        timeout=15,
    )
    lines = (result.stdout + result.stderr).splitlines()
    return {"lines": lines}
