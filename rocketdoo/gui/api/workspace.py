"""
GUI API — Workspace management.
Discover Rocketdoo projects on the host, navigate between them, and create new ones.
"""

import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from rocketdoo.core.compose import compose_ps, is_running

router = APIRouter()


# ── helpers ───────────────────────────────────────────────────────────────────


def _default_scan_dir() -> Path:
    home = Path.home()
    for candidate in ("workspace", "projects", "dev", "code", "work"):
        d = home / candidate
        if d.exists():
            return d
    return home


def _is_rkd_project(p: Path) -> bool:
    return (p / "docker-compose.yaml").exists() and (p / "Dockerfile").exists()


def _container_status(project_path: Path) -> dict:
    containers = compose_ps("--all", cwd=project_path, timeout=6)
    return {
        "total": len(containers),
        "running": sum(1 for c in containers if is_running(c)),
    }


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.get("")
async def get_workspace():
    cwd = Path.cwd()
    return {
        "path": str(cwd),
        "name": cwd.name,
        "home": str(Path.home()),
        "default_scan": str(_default_scan_dir()),
        "is_rkd_project": _is_rkd_project(cwd),
    }


class NavigateRequest(BaseModel):
    path: str


@router.post("/navigate")
async def navigate(body: NavigateRequest):
    try:
        path = Path(body.path).expanduser().resolve()
        if not path.exists():
            return {"ok": False, "error": f"Path does not exist: {body.path}"}
        if not path.is_dir():
            return {"ok": False, "error": f"Not a directory: {body.path}"}
        os.chdir(path)
        return {"ok": True, "path": str(path), "name": path.name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class MkdirRequest(BaseModel):
    name: str
    base_dir: str = ""


@router.post("/mkdir")
async def mkdir_project(body: MkdirRequest):
    try:
        base = Path(body.base_dir).expanduser().resolve() if body.base_dir else Path.cwd()
        name = body.name.strip().replace(" ", "-")
        if not name:
            return {"ok": False, "error": "Name cannot be empty"}
        new_path = base / name
        if new_path.exists():
            return {"ok": False, "error": f"'{name}' already exists in {base}"}
        new_path.mkdir(parents=True)
        return {"ok": True, "path": str(new_path), "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class DiscoverRequest(BaseModel):
    base_dir: str = ""
    max_depth: int = 3


@router.post("/discover")
async def discover_projects(body: DiscoverRequest):
    base_str = body.base_dir.strip()
    base = Path(base_str).expanduser().resolve() if base_str else _default_scan_dir()

    if not base.exists() or not base.is_dir():
        return {"projects": [], "base_dir": str(base), "total": 0, "error": "Directory not found"}

    cwd = str(Path.cwd())
    projects: list[dict] = []
    visited: set[str] = set()

    _SKIP = {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".env",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
    }

    def _scan(path: Path, depth: int) -> None:
        real = str(path.resolve())
        if real in visited or depth < 0:
            return
        visited.add(real)
        try:
            for child in sorted(path.iterdir()):
                if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP:
                    continue
                if _is_rkd_project(child):
                    status = _container_status(child)
                    projects.append(
                        {
                            "path": str(child),
                            "name": child.name,
                            "containers": status["total"],
                            "running": status["running"],
                            "is_current": str(child) == cwd,
                        }
                    )
                else:
                    _scan(child, depth - 1)
        except PermissionError:
            pass

    _scan(base, body.max_depth)
    projects.sort(key=lambda p: p["name"].lower())
    return {"projects": projects, "base_dir": str(base), "total": len(projects)}


class BrowseRequest(BaseModel):
    path: str = ""


@router.post("/browse")
async def browse_dirs(body: BrowseRequest):
    """List immediate subdirectories of a given path (for a directory picker)."""
    try:
        base = Path(body.path).expanduser().resolve() if body.path else Path.home()
        if not base.exists() or not base.is_dir():
            return {"dirs": [], "path": str(base), "error": "Not found"}
        dirs = sorted(
            [str(d) for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")],
        )
        return {"dirs": dirs, "path": str(base), "parent": str(base.parent)}
    except Exception as e:
        return {"dirs": [], "path": "", "error": str(e)}
