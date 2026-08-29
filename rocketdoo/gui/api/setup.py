"""
GUI API — Project setup (scaffold + init without interactive wizard).
Replicates the logic of init_project.py accepting a JSON body instead of
questionary prompts, so the frontend can drive the full wizard.
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

# ── helpers ──────────────────────────────────────────────────────────────────

def _templates_dir() -> Path:
    return Path(__file__).parent.parent.parent / "templates"


def _render(template_dir: Path, template_name: str, output_path: Path, **ctx):
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    tpl = env.get_template(template_name)
    content = tpl.render(**ctx)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("/ssh-keys")
async def get_ssh_keys():
    """List SSH private keys available in ~/.ssh/"""
    try:
        from rocketdoo.core.ssh_manager import list_private_keys
        return {"keys": list_private_keys()}
    except Exception as e:
        return {"keys": [], "error": str(e)}


@router.get("/port/check")
async def check_port(port: int):
    """Check if a port is available on the local machine."""
    try:
        from rocketdoo.core.port_validation import is_port_in_use
        return {"port": port, "available": not is_port_in_use(port)}
    except Exception:
        return {"port": port, "available": True}


@router.get("/port/suggest")
async def suggest_port(start: int = 8069):
    """Return the first free port starting from *start*."""
    try:
        from rocketdoo.core.port_validation import find_available_port
        return {"port": find_available_port(start)}
    except Exception:
        return {"port": start}


class ScaffoldRequest(BaseModel):
    template: str = "basic"
    force: bool = False


@router.post("/scaffold")
async def scaffold(body: ScaffoldRequest):
    """Run rkd scaffold (copies base templates to cwd)."""
    try:
        from rocketdoo.scaffold import scaffold_project
        scaffold_project(template=body.template, force=body.force, verbose=False)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class GitmanSource(BaseModel):
    repo: str
    name: str
    rev: str
    type: str = "git"


class InitRequest(BaseModel):
    project_name: str
    odoo_version: str = "18.0"
    odoo_edition: str = "Community"
    db_version: str = "16"
    admin_passwd: str = "admin"
    odoo_port: int = 8069
    vsc_port: int = 8888
    restart: str = "unless-stopped"
    use_private_repos: bool = False
    ssh_key_name: Optional[str] = None
    use_third_party_repos: bool = False
    gitman_sources: list[GitmanSource] = []


@router.post("/init")
async def init_project(body: InitRequest):
    """
    Initialize a Rocketdoo project without the interactive questionary wizard.
    Accepts all wizard answers as JSON and calls the same underlying
    template rendering / setup functions that init_project.py uses.
    """
    try:
        cwd = Path.cwd()
        tpl = _templates_dir()

        project_name = body.project_name.lower().strip()
        context = {
            "project_name":        project_name,
            "odoo_version":        body.odoo_version,
            "odoo_edition":        body.odoo_edition,
            "db_version":          body.db_version,
            "odoo_port":           body.odoo_port,
            "vsc_port":            body.vsc_port,
            "restart":             body.restart,
            "odoo_image":          f"odoo:{body.odoo_version}",
            "odoo_container":      f"odoo-{project_name}",
            "db_container":        f"db-{project_name}",
            "admin_passwd":        body.admin_passwd,
            "use_private_repos":   body.use_private_repos,
            "ssh_key_name":        body.ssh_key_name,
            "use_third_party_repos": body.use_third_party_repos,
        }

        _render(tpl,                  "Dockerfile.jinja",          cwd / "Dockerfile",                     **context)
        _render(tpl,                  "docker-compose.yaml.jinja", cwd / "docker-compose.yaml",             **context)
        _render(tpl / "config",       "odoo.conf.jinja",           cwd / "config" / "odoo.conf",            **context)
        _render(tpl / ".vscode",      "launch.json.jinja",         cwd / ".vscode" / "launch.json",         **context)

        if body.odoo_edition == "Enterprise":
            from rocketdoo.core.edition_setup import setup_enterprise_edition
            setup_enterprise_edition(cwd)

        if body.use_private_repos and body.ssh_key_name:
            from rocketdoo.core.ssh_manager import copy_key_to_build_context, inject_ssh_into_dockerfile
            copy_key_to_build_context(body.ssh_key_name, cwd)
            inject_ssh_into_dockerfile(cwd / "Dockerfile", body.ssh_key_name)

        if body.use_third_party_repos and body.gitman_sources:
            from rocketdoo.core.gitman_config import generate_gitman_yaml, update_odoo_conf_with_gitman
            sources = [s.model_dump() for s in body.gitman_sources]
            generate_gitman_yaml(sources=sources, output_path=cwd / "gitman.yaml")
            conf = cwd / "config" / "odoo.conf"
            if conf.exists():
                update_odoo_conf_with_gitman(conf, sources)

        return {"ok": True, "project_name": project_name}

    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "detail": traceback.format_exc()}
