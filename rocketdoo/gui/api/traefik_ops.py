import yaml
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


def _load_traefik_config() -> dict:
    cfg_path = Path(".rkd") / "traefik.yaml"
    if cfg_path.exists():
        try:
            return yaml.safe_load(cfg_path.read_text()) or {}
        except Exception:
            pass
    return {}


@router.get("/status")
async def traefik_status():
    cfg = _load_traefik_config()
    override = (Path.cwd() / "docker-compose.override.yml").exists()
    traefik_compose = (Path.cwd() / "traefik" / "docker-compose.yml").exists()
    return {
        "enabled": cfg.get("enabled", False),
        "mode": cfg.get("mode", None),
        "domain": cfg.get("domain", None),
        "override_exists": override,
        "traefik_compose_exists": traefik_compose,
    }


class TraefikOnRequest(BaseModel):
    mode: str = "local"           # "local" | "production"
    domain: str = "myodoo.local"
    email: Optional[str] = ""     # required for production


@router.post("/on")
async def traefik_on(body: TraefikOnRequest):
    """Enable Traefik reverse proxy (equivalent to rkd traefik on)."""
    try:
        from rocketdoo.traefik_cli import (
            _gen_traefik_compose,
            _gen_traefik_yml,
            _gen_override,
            _create_network,
            _network_exists,
            _save_config,
            _project_name,
        )
        cwd = Path.cwd()
        traefik_dir = cwd / "traefik"
        traefik_dir.mkdir(exist_ok=True)

        (traefik_dir / "docker-compose.yml").write_text(_gen_traefik_compose(body.mode))
        (traefik_dir / "traefik.yml").write_text(_gen_traefik_yml(body.mode, body.email or ""))

        if not _network_exists():
            _create_network()

        project = _project_name()
        (cwd / "docker-compose.override.yml").write_text(_gen_override(project, body.domain, body.mode))

        _save_config({"enabled": True, "mode": body.mode, "domain": body.domain, "email": body.email or ""})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/off")
async def traefik_off():
    """Disable Traefik (equivalent to rkd traefik off)."""
    try:
        from rocketdoo.traefik_cli import _disable_traefik
        _disable_traefik()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
