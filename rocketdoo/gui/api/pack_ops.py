"""
GUI API — Pack / Unpack environment.
"""
import subprocess
import sys
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


def _run_rkd(*args: str, timeout: int = 300) -> dict:
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


class PackRequest(BaseModel):
    include_db: bool = True
    output_path: Optional[str] = None
    db_name: Optional[str] = None


@router.post("/pack")
async def pack(body: PackRequest):
    """Pack the current environment into a ZIP file."""
    args = ["pack"]
    if not body.include_db:
        args.append("--no-db")
    if body.output_path:
        args += ["--output", body.output_path]
    if body.db_name:
        args += ["--db-name", body.db_name]
    return _run_rkd(*args)


@router.post("/unpack")
async def unpack():
    """Unpack a previously packed environment in the current directory."""
    return _run_rkd("unpack")
