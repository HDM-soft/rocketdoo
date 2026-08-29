from fastapi import APIRouter

from rocketdoo.core.compose import compose_path, container_running

router = APIRouter()

MARKER_START = "# rkd:mailpit"
MARKER_END = "# /rkd:mailpit"


def _mailpit_enabled() -> bool:
    path = compose_path()
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
    return container_running("mailpit")


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
