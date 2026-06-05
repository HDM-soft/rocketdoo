"""
GUI API — Module deployment (rkd deploy group).
"""
import subprocess
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


def _deploy_yaml_path() -> Path:
    return Path.cwd() / ".rkd" / "deploy.yaml"


@router.get("/config")
async def get_deploy_config():
    """Read and return deploy.yaml content."""
    import yaml
    p = _deploy_yaml_path()
    if not p.exists():
        return {"exists": False, "config": None}
    try:
        return {"exists": True, "config": yaml.safe_load(p.read_text())}
    except Exception as e:
        return {"exists": True, "config": None, "error": str(e)}


@router.get("/targets")
async def get_targets():
    """Return list of configured deploy targets."""
    import yaml
    p = _deploy_yaml_path()
    if not p.exists():
        return {"targets": []}
    try:
        data = yaml.safe_load(p.read_text()) or {}
        targets_cfg = data.get("targets", {})
        targets = [
            {
                "name": name,
                "type": cfg.get("type", "vps"),
                "host": cfg.get("connection", {}).get("host", ""),
            }
            for name, cfg in targets_cfg.items()
            if isinstance(cfg, dict)
        ]
        return {"targets": targets}
    except Exception as e:
        return {"targets": [], "error": str(e)}


class DeployInitRequest(BaseModel):
    target_name: str = "production"
    target_type: str = "vps"
    host: str = ""
    user: str = "ubuntu"
    ssh_port: int = 22
    ssh_key: str = ""
    deployment_type: str = "docker"
    force: bool = False


@router.post("/init")
async def deploy_init(body: DeployInitRequest):
    """Create or overwrite .rkd/deploy.yaml with the provided target config."""
    import yaml
    p = _deploy_yaml_path()
    if p.exists() and not body.force:
        return {"ok": False, "error": "deploy.yaml already exists. Use force=true to overwrite."}
    try:
        config = {
            "modules": {
                "auto_detect": True,
                "base_path": "addons",
                "exclude_patterns": ["__pycache__", "*.pyc", ".git"],
            },
            "targets": {
                body.target_name: {
                    "type": body.target_type,
                    "connection": {
                        "host":    body.host,
                        "user":    body.user,
                        "port":    body.ssh_port,
                        "ssh_key": body.ssh_key,
                    },
                    "deployment_type": body.deployment_type,
                }
            },
            "backup": {"enabled": True, "keep_last": 3, "path": ".rkd/deploy_backups"},
            "validations": {"check_manifest": True, "check_python_syntax": True},
            "logging": {"level": "INFO", "file": ".rkd/deploy.log", "console": True},
        }
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class DeployRunRequest(BaseModel):
    target: str
    modules: list[str] = []
    dry_run: bool = False
    skip_backup: bool = False
    skip_validation: bool = False


@router.post("/run")
async def deploy_run(body: DeployRunRequest):
    """Execute a module deployment to the given target."""
    try:
        from rocketdoo.core.deploy.config_manager import DeployConfigManager
        from rocketdoo.core.module_scanner import ModuleScanner

        project_path = Path.cwd()
        config_manager = DeployConfigManager(project_path)

        if not config_manager.config_exists():
            return {"ok": False, "error": "deploy.yaml not found. Run deploy init first."}

        config = config_manager.load_config()
        target_config = config.get("targets", {}).get(body.target)
        if not target_config:
            return {"ok": False, "error": f"Target '{body.target}' not found in deploy.yaml."}

        target_type = target_config.get("type", "vps")
        if target_type == "vps":
            from rocketdoo.core.deploy.vps import VPSDeployer
            deployer = VPSDeployer(body.target, target_config, project_path)
        else:
            return {"ok": False, "error": f"Target type '{target_type}' not yet supported via GUI."}

        if body.dry_run:
            return {"ok": True, "dry_run": True, "message": "Dry run: configuration looks valid."}

        if body.modules:
            modules = body.modules
        else:
            scanner = ModuleScanner(base_path=project_path / "addons")
            modules = [m.name for m in scanner.scan() if m.is_installable]

        result = deployer.deploy_modules(modules)
        return {"ok": getattr(result, "success", True), "result": str(result)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/validate")
async def deploy_validate():
    """Validate all Odoo modules (manifest + Python syntax)."""
    try:
        from rocketdoo.core.module_scanner import ModuleScanner
        scanner = ModuleScanner(base_path=Path.cwd() / "addons")
        modules = scanner.scan()
        errors = {}
        for m in modules:
            e = m.validate()
            if e:
                errors[m.name] = e
        return {"ok": len(errors) == 0, "errors": errors, "total": len(modules)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
