from pathlib import Path

from fastapi import APIRouter

from rocketdoo.core.mailpit import disable, enable, status

router = APIRouter()


def _reported(report: dict) -> dict:
    """Turn an enable()/disable() report into the endpoint's JSON response.

    `ok` used to be unconditional (RF5.b, #143): a `db_error` meant the
    compose/conf toggle happened but the ir.mail_server write did not, and
    the endpoint still answered `{"ok": true}`. The report is now returned in
    full, and `ok` reflects whether the write actually succeeded; `error`
    carries the reason, the same field the SPA already renders on failure.
    """
    result = {"ok": not report["db_error"], **report}
    if report["db_error"]:
        result["error"] = report["db_error"]
    return result


@router.get("/status")
async def mail_status():
    return status()


@router.post("/on")
async def mail_on():
    try:
        report = enable(Path.cwd())
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return _reported(report)


@router.post("/off")
async def mail_off():
    try:
        report = disable(Path.cwd())
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return _reported(report)
