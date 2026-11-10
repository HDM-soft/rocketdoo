"""GUI API — Gitman external repos CRUD."""

from pathlib import Path
from typing import List

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from rocketdoo.core.project_info import as_text

router = APIRouter()


def _gitman_path() -> Path:
    return Path.cwd() / "gitman.yaml"


@router.get("")
async def get_gitman():
    """Read gitman.yaml and return sources list."""
    gp = _gitman_path()
    if not gp.exists():
        return {"exists": False, "sources": [], "location": "external_addons"}
    try:
        import yaml

        data = yaml.safe_load(gp.read_text()) or {}
        raw = data.get("sources", [])
        # As text, because gitman.yaml is the user's file and `yaml.safe_load`
        # is YAML 1.1: `rev: 16.0` -- the way an Odoo branch gets written --
        # comes back as a float and an empty `repo:` as None. Handed over raw,
        # the card showed `16` for a branch called `16.0`, the edit form died
        # on `.trim()` of a Number, and `POST /api/gitman` rejected the very
        # object this endpoint had just produced (#263).
        sources = [
            {
                "name": as_text(s.get("name", "")) or "",
                "repo": as_text(s.get("repo", "")) or "",
                "rev": as_text(s.get("rev", "main")) or "main",
                "type": as_text(s.get("type", "git")) or "git",
            }
            for s in raw
        ]
        return {
            "exists": True,
            "location": data.get("location", "external_addons"),
            "sources": sources,
        }
    except Exception as e:
        return JSONResponse({"exists": False, "sources": [], "error": str(e)}, status_code=200)


class GitSource(BaseModel):
    name: str
    repo: str
    rev: str = "main"
    type: str = "git"


class GitmanSave(BaseModel):
    sources: List[GitSource]
    location: str = "external_addons"


@router.post("")
async def save_gitman(body: GitmanSave):
    """Write gitman.yaml with the provided sources."""
    try:
        from rocketdoo.core.gitman_config import generate_gitman_yaml

        sources = [s.model_dump() for s in body.sources]
        generate_gitman_yaml(sources=sources, output_path=_gitman_path())
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)
