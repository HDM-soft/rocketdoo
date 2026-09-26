from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from rocketdoo.core import traefik as core_traefik
from rocketdoo.core.service import ServiceError

router = APIRouter()


def _failed(exc: ServiceError) -> dict:
    """A refusal, with the half of it that says how to fix it.

    RF1.4 splits an error into message and hint precisely so the caller can
    show both; dropping the hint leaves the GUI user with "Traefik is already
    enabled for this project." and no "Run rkd traefik off first."
    """
    return {"ok": False, "error": str(exc), "hint": exc.hint}


@router.get("/status")
def traefik_status():
    result = core_traefik.status(Path.cwd())
    return {
        "enabled": result["enabled"],
        "mode": result["mode"],
        "domain": result["domain"],
        "override_exists": result["override_exists"],
        "traefik_compose_exists": (Path.cwd() / "traefik" / "docker-compose.yml").exists(),
    }


class TraefikOnRequest(BaseModel):
    mode: str = "local"  # "local" | "production"
    domain: str = "myodoo.local"
    email: Optional[str] = ""  # required for production


@router.post("/on")
def traefik_on(body: TraefikOnRequest):
    """Enable Traefik reverse proxy (equivalent to rkd traefik on).

    A plain `def`, not `async`: enable() runs two `docker compose up -d`
    synchronously, which on a cold machine pulls an image and rebuilds the
    project. Inside a coroutine that would freeze the whole GUI, log
    streaming included, for as long as it takes.
    """
    try:
        report = core_traefik.enable(Path.cwd(), mode=body.mode, domain=body.domain, email=body.email or "")
    except ServiceError as exc:
        return _failed(exc)

    # RF6.5 routes "Docker is not running" through these two fields rather
    # than an exception, so answering a flat {"ok": true} here would report a
    # proxy that never started as enabled -- the same lie RF5.b removed from
    # the Mailpit endpoint.
    started = report["traefik_started"] and report["project_restarted"]
    result = {"ok": started, **report}
    if not started:
        result["error"] = (
            "Traefik could not be started."
            if not report["traefik_started"]
            else "The project could not be restarted with the new override."
        )
        result["hint"] = "Check that Docker is running, then re-run rkd traefik on."
    return result


@router.post("/off")
def traefik_off():
    """Disable Traefik (equivalent to rkd traefik off)."""
    try:
        removed = core_traefik.disable(Path.cwd())
    except ServiceError as exc:
        return _failed(exc)
    return {"ok": True, "removed": removed}
