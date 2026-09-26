from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from rocketdoo.core import traefik as core_traefik

router = APIRouter()


@router.get("/status")
async def traefik_status():
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
async def traefik_on(body: TraefikOnRequest):
    """Enable Traefik reverse proxy (equivalent to rkd traefik on)."""
    try:
        core_traefik.enable(Path.cwd(), mode=body.mode, domain=body.domain, email=body.email or "")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/off")
async def traefik_off():
    """Disable Traefik (equivalent to rkd traefik off)."""
    try:
        core_traefik.disable(Path.cwd())
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
