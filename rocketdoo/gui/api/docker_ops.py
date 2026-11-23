import subprocess
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from rocketdoo.core.addons_path import ensure_addons_path
from rocketdoo.core.compose import run_compose_result

router = APIRouter()


@router.post("/up")
def docker_up():
    # One read: a navigate() in another thread must not land between the
    # addons_path sync and the compose call, which would start a project with
    # another one's addons_path (#287).
    root = Path.cwd()
    ensure_addons_path(root)
    return run_compose_result("up", "-d", cwd=root)


@router.post("/down")
def docker_down():
    return run_compose_result("down")


@router.post("/restart")
def docker_restart():
    return run_compose_result("restart")


@router.post("/stop")
def docker_stop():
    return run_compose_result("stop")


@router.post("/build")
def docker_build():
    return run_compose_result("build", timeout=300)


class ServiceAction(BaseModel):
    service: str


@router.post("/service/start")
def service_start(body: ServiceAction):
    return run_compose_result("start", body.service)


@router.post("/service/stop")
def service_stop(body: ServiceAction):
    return run_compose_result("stop", body.service)


@router.post("/service/restart")
def service_restart(body: ServiceAction):
    return run_compose_result("restart", body.service)


@router.get("/logs/{container_name}")
def get_logs(container_name: str, tail: int = 200):
    """Returns last N lines of logs for a container (non-streaming)."""
    result = subprocess.run(
        ["docker", "logs", "--tail", str(tail), container_name],
        capture_output=True,
        text=True,
        timeout=15,
    )
    lines = (result.stdout + result.stderr).splitlines()
    return {"lines": lines}
