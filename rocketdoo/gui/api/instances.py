from pathlib import Path

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


def _load_instances() -> dict:
    cfg = Path(".rkd") / "instance.yaml"
    if cfg.exists():
        try:
            return yaml.safe_load(cfg.read_text()) or {}
        except Exception:
            pass
    return {}


@router.get("")
async def list_instances():
    data = _load_instances()
    envs = []
    for env_name, cfg in data.items():
        if not isinstance(cfg, dict):
            continue
        envs.append({
            "env": env_name,
            "type": cfg.get("type", "docker"),
            "host": cfg.get("host", ""),
            "domain": cfg.get("domain", ""),
            "odoo_version": cfg.get("odoo_version", ""),
            "remote_path": cfg.get("remote_path", ""),
            "user": cfg.get("user", ""),
            "port": cfg.get("port", 22),
        })
    return {"instances": envs}


class EnvConfig(BaseModel):
    type: str = "docker"
    host: str = ""
    port: int = 22
    user: str = "ubuntu"
    auth_method: str = "ssh_key"
    ssh_key: str = ""
    password_ref: str = ""
    odoo_version: str = "17.0"
    odoo_tag: str = "17.0"
    domain: str = ""
    email: str = ""
    db_user: str = "odoo"
    db_version: str = "16"
    pg_profile: str = "small"
    remote_path: str = ""


class InstanceInitRequest(BaseModel):
    environments: dict[str, EnvConfig]


@router.post("/init")
async def init_instances(body: InstanceInitRequest):
    """Save instance configuration (.rkd/instance.yaml) from GUI form data."""
    try:
        from rocketdoo.core.instance.config_manager import InstanceConfigManager
        manager = InstanceConfigManager(Path.cwd())
        config = {"environments": {env: cfg.model_dump() for env, cfg in body.environments.items()}}
        manager.save(config)
        return {"ok": True, "environments": list(body.environments.keys())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class DeployRequest(BaseModel):
    env: str
    dry_run: bool = False


@router.post("/deploy")
async def deploy_instance(body: DeployRequest):
    try:
        from rocketdoo.core.instance.config_manager import InstanceConfigManager
        manager = InstanceConfigManager()
        if not manager.exists():
            return {"ok": False, "error": "No instance.yaml found. Run rkd instance init first."}
        env_cfg = manager.get_env(body.env)
        project_path = Path.cwd()
        deploy_type = env_cfg.get("type", "docker")
        if deploy_type == "docker":
            from rocketdoo.core.instance.deployer_docker import DockerInstanceDeployer
            deployer = DockerInstanceDeployer(body.env, env_cfg, project_path)
        else:
            from rocketdoo.core.instance.deployer_native import NativeInstanceDeployer
            deployer = NativeInstanceDeployer(body.env, env_cfg, project_path)
        ok = deployer.deploy(dry_run=body.dry_run)
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}
