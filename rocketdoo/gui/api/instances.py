from pathlib import Path

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

from rocketdoo.core.models import InstanceConfig

router = APIRouter()


def _load_instances() -> InstanceConfig:
    """Read .rkd/instance.yaml, whichever shape it was written in.

    Returns an empty config when the file is missing or unreadable: the GUI
    renders "no instances configured" rather than an error page.
    """
    cfg = Path(".rkd") / "instance.yaml"
    if not cfg.exists():
        return InstanceConfig()
    try:
        return InstanceConfig.model_validate(yaml.safe_load(cfg.read_text()) or {})
    except Exception:
        return InstanceConfig()


@router.get("")
async def list_instances():
    config = _load_instances()
    return {
        "instances": [
            {
                "env": name,
                "type": env.type,
                "host": env.vps.host,
                "domain": env.domain,
                "odoo_version": env.odoo_version,
                "remote_path": env.remote_path,
                "user": env.vps.user,
                "port": env.vps.port,
                "deployable": env.is_deployable,
                "missing": env.missing_fields(),
            }
            for name, env in config.environments.items()
        ]
    }


class EnvConfig(BaseModel):
    """The GUI form. Field names match what the form has always posted.

    InstanceEnvironment accepts this flat shape and normalises it, so the file
    on disk ends up in the same nested form the CLI wizard writes.
    """

    type: str = "docker"
    host: str = ""
    port: int = 22
    user: str = "ubuntu"
    auth_method: str = "ssh_key"
    ssh_key: str = ""
    password_ref: str = ""
    odoo_version: str = "17.0"
    odoo_tag: str = ""
    domain: str = ""
    email: str = ""
    db_user: str = "odoo"
    db_version: str = "16"
    admin_passwd: str = ""
    use_enterprise: bool = False
    use_gitman: bool = False
    gitman_config: str = ""
    pg_profile: str = "small"
    remote_path: str = ""


class InstanceInitRequest(BaseModel):
    environments: dict[str, EnvConfig]


@router.post("/init")
async def init_instances(body: InstanceInitRequest):
    """Save instance configuration (.rkd/instance.yaml) from GUI form data."""
    try:
        from rocketdoo.core.instance.config_manager import InstanceConfigManager
        from rocketdoo.core.instance.secrets_store import random_password

        raw = {}
        for env, cfg in body.environments.items():
            values = cfg.model_dump()
            # The wizard always sets one; without it the rendered odoo.conf
            # would ship an empty admin_passwd.
            if not values.get("admin_passwd"):
                values["admin_passwd"] = random_password()
            values.setdefault("remote_path", "")
            if not values["remote_path"]:
                values["remote_path"] = f"/opt/odoo-{env}"
            raw[env] = values

        config = InstanceConfig.model_validate({"environments": raw})
        InstanceConfigManager(Path.cwd()).save(config.to_yaml_dict())
        return {
            "ok": True,
            "environments": list(config.environments),
            "incomplete": {name: env.missing_fields() for name, env in config.environments.items() if not env.is_deployable},
        }
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
