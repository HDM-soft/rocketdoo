import subprocess
import asyncio
from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


def _run(cmd: list[str], timeout: int = 60) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": r.returncode == 0,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": "docker not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "command timed out"}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


@router.post("/up")
async def docker_up():
    return _run(["docker", "compose", "up", "-d"])


@router.post("/down")
async def docker_down():
    return _run(["docker", "compose", "down"])


@router.post("/restart")
async def docker_restart():
    return _run(["docker", "compose", "restart"])


@router.post("/stop")
async def docker_stop():
    return _run(["docker", "compose", "stop"])


@router.post("/build")
async def docker_build():
    return _run(["docker", "compose", "build"], timeout=300)


class ServiceAction(BaseModel):
    service: str


@router.post("/service/start")
async def service_start(body: ServiceAction):
    return _run(["docker", "compose", "start", body.service])


@router.post("/service/stop")
async def service_stop(body: ServiceAction):
    return _run(["docker", "compose", "stop", body.service])


@router.post("/service/restart")
async def service_restart(body: ServiceAction):
    return _run(["docker", "compose", "restart", body.service])


@router.get("/logs/{container_name}")
async def get_logs(container_name: str, tail: int = 200):
    """Returns last N lines of logs for a container (non-streaming)."""
    result = subprocess.run(
        ["docker", "logs", "--tail", str(tail), container_name],
        capture_output=True, text=True, timeout=15,
    )
    lines = (result.stdout + result.stderr).splitlines()
    return {"lines": lines}
