"""
GUI API — Module deployment (rkd deploy group).
"""

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ValidationError

from rocketdoo.core.models import DeployConfig

router = APIRouter()


def _deploy_yaml_path() -> Path:
    return Path.cwd() / ".rkd" / "deploy.yaml"


@router.get("/config")
def get_deploy_config():
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
def get_targets():
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
def deploy_init(body: DeployInitRequest):
    """Create or overwrite .rkd/deploy.yaml with the provided target config."""
    import yaml

    from rocketdoo.core.project_info import project_exists

    # Same guard as `rkd deploy init` (#246). Here it matters more: the CLI
    # needs a `cd` too far, while the GUI reaches any directory through
    # /api/workspace/navigate and this endpoint is a button.
    if not project_exists(Path.cwd()):
        return {"ok": False, "error": f"No Rocketdoo project in {Path.cwd()}. Open a project before configuring a deploy."}

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
                        "host": body.host,
                        "user": body.user,
                        "port": body.ssh_port,
                        "ssh_key": body.ssh_key,
                    },
                    "deployment_type": body.deployment_type,
                }
            },
            "backup": {"enabled": True, "keep_last": 3, "path": ".rkd/deploy_backups"},
            "validations": {"check_manifest": True, "check_python_syntax": True},
            "logging": {"level": "INFO", "file": ".rkd/deploy.log", "console": True},
        }
        # Validate before writing: an unusable deploy.yaml saved here would
        # only surface later, at deploy time.
        parsed = DeployConfig.model_validate(config)
        errors = parsed.validation_errors()
        if errors:
            return {"ok": False, "error": "; ".join(errors)}

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True))
        return {"ok": True}
    except ValidationError as e:
        return {"ok": False, "error": "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class DeployRunRequest(BaseModel):
    target: str
    modules: list[str] = []
    dry_run: bool = False
    skip_backup: bool = False
    skip_validation: bool = False


@router.post("/run")
def deploy_run(body: DeployRunRequest):
    """Execute a module deployment to the given target.

    Goes through BaseDeployer.execute(), the same entry point the CLI uses, and
    not straight to deploy_modules(): execute() validates the configuration and
    the modules, takes the backup, runs the pre-deploy connectivity check and
    rolls back on failure. Calling deploy_modules() direct meant a deploy.yaml
    without a credential deployed from here and was rejected by the CLI, and a
    deploy from the GUI took no backup however `backup.enabled` read (#322).
    """
    try:
        from rocketdoo.core.deploy.config_manager import DeployConfigManager
        from rocketdoo.core.module_scanner import ModuleScanner

        # One read, like every other handler: a navigate() in another thread
        # must not land between the config and the scan (#287).
        project_path = Path.cwd()
        config_manager = DeployConfigManager(project_path)

        if not config_manager.config_exists():
            return {"ok": False, "error": "deploy.yaml not found. Run deploy init first."}

        config = config_manager.load()
        target_config = config.get("targets", {}).get(body.target)
        if not target_config:
            return {"ok": False, "error": f"Target '{body.target}' not found in deploy.yaml."}

        target_type = target_config.get("type", "vps")
        if target_type != "vps":
            return {"ok": False, "error": f"Target type '{target_type}' not yet supported via GUI."}

        # Before building the deployer, and not after: VPSDeployer's constructor
        # calls getpass() when auth_method is password and the variable is not
        # exported, which from here prompts on the *server's* terminal and
        # persists what is typed. A simulation must not do that.
        if body.dry_run:
            errors = config_manager.validate(config)
            return {
                "ok": not errors,
                "dry_run": True,
                "errors": errors,
                "message": "Configuration is valid." if not errors else "Configuration has errors.",
            }

        from rocketdoo.core.deploy.vps import VPSDeployer

        scanner = ModuleScanner(project_path / config.get("modules", {}).get("base_path", "addons"))
        installable = scanner.get_installable_modules()
        if body.modules:
            selected = [m for m in installable if m.name in body.modules]
            missing = sorted(set(body.modules) - {m.name for m in selected})
            if missing:
                return {"ok": False, "error": f"Modules not found: {', '.join(missing)}"}
        else:
            selected = installable
        if not selected:
            return {"ok": False, "error": "No installable modules found to deploy."}

        # to_dict(), not the names: prepare_modules() takes dicts and reads the
        # path out of each one.
        deployer = VPSDeployer(body.target, target_config, project_path)
        result = deployer.execute([m.to_dict() for m in selected])
        return {
            "ok": bool(getattr(result, "success", False)),
            "message": getattr(result, "message", ""),
            "modules": [m.name for m in selected],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/validate")
def deploy_validate():
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
