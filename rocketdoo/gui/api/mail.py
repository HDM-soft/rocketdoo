import subprocess
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

MARKER_START = "# rkd:mailpit"
MARKER_END = "# /rkd:mailpit"


def _compose_path() -> Path | None:
    for name in ("docker-compose.yaml", "docker-compose.yml"):
        p = Path.cwd() / name
        if p.exists():
            return p
    return None


def _mailpit_enabled() -> bool:
    path = _compose_path()
    if not path:
        return False
    content = path.read_text()
    in_block = False
    for line in content.splitlines():
        if MARKER_START in line:
            in_block = True
            continue
        if MARKER_END in line:
            break
        if in_block and line.strip() and not line.strip().startswith("#"):
            return True
    return False


def _mailpit_running() -> bool:
    try:
        r = subprocess.run(
            ["docker", "compose", "ps", "--format", "json", "mailpit"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        import json

        for line in r.stdout.strip().splitlines():
            try:
                data = json.loads(line)
                state = data.get("State", data.get("Status", "")).lower()
                if "running" in state or "up" in state:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


@router.get("/status")
async def mail_status():
    return {
        "enabled": _mailpit_enabled(),
        "running": _mailpit_running(),
    }


@router.post("/on")
async def mail_on():
    try:
        from rocketdoo.mail_cli import _enable_mailpit

        _enable_mailpit()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/off")
async def mail_off():
    try:
        from rocketdoo.mail_cli import _disable_mailpit

        _disable_mailpit()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
