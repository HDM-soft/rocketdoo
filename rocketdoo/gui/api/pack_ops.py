"""
GUI API — Pack / Unpack environment.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from rocketdoo.core import pack as core_pack
from rocketdoo.core import unpack as core_unpack

router = APIRouter()


@router.get("/status")
async def pack_status():
    """Check pack/unpack prerequisites in current directory."""
    cwd = Path.cwd()
    shared_json = cwd / "rkd-shared.json"
    compose_exists = (cwd / "docker-compose.yaml").exists() or (cwd / "docker-compose.yml").exists()
    return {
        "project_exists": compose_exists,
        "shared_json_found": shared_json.exists(),
    }


@router.get("/unpack-info")
def unpack_info():
    """Return metadata from rkd-shared.json and available SSH keys for the
    unpack wizard, via core.unpack.inspect() (#143 T13) instead of reading
    rkd-shared.json a second time with its own parsing.
    """
    cwd = Path.cwd()
    info = core_unpack.inspect(cwd)
    return {
        "meta": info["meta"],
        "ssh_keys": info["ssh_keys"],
        # Plain existence, like /status reports it: the SPA gates the unpack
        # button on that endpoint's flag, and two answers to the same
        # question under the same key is a trap. `meta` already comes back
        # empty when the file is there but unusable.
        "shared_json_found": (cwd / "rkd-shared.json").exists(),
    }


def _with_hint(exc) -> str:
    """The message plus the half that says how to fix it.

    ServiceError splits the two (RF1.4) so the caller can present both; this
    endpoint reports through a single `stderr` string, so they travel joined
    rather than the hint being dropped.
    """
    return f"{exc} {exc.hint}".strip() if exc.hint else str(exc)


class PackRequest(BaseModel):
    include_db: bool = True
    output_path: Optional[str] = None
    db_name: Optional[str] = None
    # Same default as the CLI: a ZIP silently missing the backup the user
    # asked for is worse than an error saying the container is down.
    allow_missing_db: bool = False


class UnpackRequest(BaseModel):
    ssh_key: Optional[str] = None
    use_ssh: bool = False


@router.post("/pack")
def pack(body: PackRequest):
    """Pack the current environment into a ZIP file.

    Calls core.pack.pack() directly instead of shelling out to `rkd pack`
    (#143 T11): a plain `def` here runs in FastAPI's threadpool, so a slow
    pg_dump no longer blocks the whole GUI event loop the way the previous
    `async def` shelling out to a child process did.
    """
    messages: list[str] = []

    def _collect(message: str, level: str = "info") -> None:
        messages.append(message)

    try:
        core_pack.pack(
            Path.cwd(),
            include_db=body.include_db,
            db_name=body.db_name,
            output=body.output_path,
            allow_missing_db=body.allow_missing_db,
            on_progress=_collect,
        )
    except core_pack.PackError as exc:
        return {"ok": False, "stdout": "\n".join(messages), "stderr": _with_hint(exc)}

    return {"ok": True, "stdout": "\n".join(messages), "stderr": ""}


@router.post("/unpack")
def unpack(body: UnpackRequest = UnpackRequest()):
    """Unpack a previously packed environment in the current directory.

    Calls core.unpack.unpack() directly instead of shelling out to `rkd
    unpack` (#143 T13), for the same reason `pack` above does: a `def` here
    runs in FastAPI's threadpool, so a restore that takes minutes never
    blocks the event loop. Port conflicts are auto-accepted -- the same
    behaviour `--yes` gives the CLI -- since there is no terminal here to ask.
    """
    info = core_unpack.inspect(Path.cwd())
    messages: list[str] = []

    def _collect(message: str, level: str = "info") -> None:
        messages.append(message)

    try:
        report = core_unpack.unpack(
            Path.cwd(),
            ports=info["suggested_ports"],
            ssh_key=body.ssh_key if body.use_ssh else None,
            on_progress=_collect,
        )
    except core_unpack.UnpackError as exc:
        return {"ok": False, "stdout": "\n".join(messages), "stderr": _with_hint(exc)}

    stderr = "" if report["started"] else "\n".join(report["logs_tail"])
    return {"ok": report["started"], "stdout": "\n".join(messages), "stderr": stderr}
