import yaml
from pathlib import Path
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
