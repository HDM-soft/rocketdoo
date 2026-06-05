import yaml
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()

TRAEFIK_CONFIG = Path.cwd() / ".rkd" / "traefik.yaml"


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


@router.post("/off")
async def traefik_off():
    try:
        from rocketdoo.traefik_cli import _disable_traefik
        _disable_traefik()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
