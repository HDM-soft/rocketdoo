"""
GUI API — Pack / Unpack environment.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from rocketdoo.core import pack as core_pack
from rocketdoo.core.ssh_manager import list_private_keys

router = APIRouter()


def _run_rkd(*args: str, timeout: int = 600) -> dict:
    """Run an rkd CLI subcommand via subprocess and capture output."""
    cmd = [sys.executable, "-m", "rocketdoo.cli"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(Path.cwd()))
        return {
            "ok": r.returncode == 0,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "Timed out after waiting too long."}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


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
async def unpack_info():
    """Return metadata from rkd-shared.json and available SSH keys for the unpack wizard."""
    cwd = Path.cwd()
    shared_json = cwd / "rkd-shared.json"
    meta = {}
    if shared_json.exists():
        try:
            meta = json.loads(shared_json.read_text())
        except Exception:
            pass
    return {
        "meta": meta,
        "ssh_keys": list_private_keys(),
        "shared_json_found": shared_json.exists(),
    }


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
    `async def` + subprocess.run did.
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
        return {"ok": False, "stdout": "\n".join(messages), "stderr": str(exc)}

    return {"ok": True, "stdout": "\n".join(messages), "stderr": ""}


@router.post("/unpack")
async def unpack(body: UnpackRequest = UnpackRequest()):
    """Unpack a previously packed environment in the current directory."""
    args = ["unpack", "--yes"]
    if body.ssh_key:
        args += ["--ssh-key", body.ssh_key]
    elif not body.use_ssh:
        args.append("--no-ssh")
    return _run_rkd(*args)
