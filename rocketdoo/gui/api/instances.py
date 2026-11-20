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
                "seed_database": env.seed_database,
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
    # Empty, like the model: InstanceConfig derives it from the
    # environment name, and a form that posts nothing must not overwrite
    # what the file already says (#248).
    db_user: str = ""
    db_name: str = ""
    db_version: str = "16"
    admin_passwd: str = ""
    use_enterprise: bool = False
    use_gitman: bool = False
    gitman_config: str = ""
    pg_profile: str = "small"
    proxy_mode: bool = False
    remote_path: str = ""
    seed_database: str = ""


class InstanceInitRequest(BaseModel):
    environments: dict[str, EnvConfig]


@router.post("/init")
async def init_instances(body: InstanceInitRequest):
    """Save instance configuration (.rkd/instance.yaml) from GUI form data."""
    try:
        from rocketdoo.core.instance.config_manager import InstanceConfigManager, merge_environments
        from rocketdoo.core.instance.secrets_store import random_password
        from rocketdoo.core.project_info import project_exists

        # Same guard as `rkd instance init` (#246), and this endpoint writes
        # more than a config: without an admin_passwd in the form it
        # generates one, so an empty directory ended up holding a credential
        # that no .gitignore covers.
        if not project_exists(Path.cwd()):
            return {"ok": False, "error": f"No Rocketdoo project in {Path.cwd()}. Open a project first."}

        raw = {}
        for env, cfg in body.environments.items():
            values = cfg.model_dump()
            # The wizard always sets one; without it the rendered odoo.conf
            # would ship an empty admin_passwd.
            if not values.get("admin_passwd"):
                values["admin_passwd"] = random_password()
            raw[env] = values

        config = InstanceConfig.model_validate({"environments": raw})
        manager = InstanceConfigManager(Path.cwd())
        existing = manager.load() if manager.exists() else {}
        manager.save(merge_environments(existing, config.to_yaml_dict()["environments"]))
        return {
            "ok": True,
            "environments": list(config.environments),
            "incomplete": {name: env.missing_fields() for name, env in config.environments.items() if not env.is_deployable},
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


class SeedRequest(BaseModel):
    env: str
    seed_database: str = ""


@router.post("/seed")
def set_seed_database(body: SeedRequest):
    """Change only the local database an environment is seeded from."""
    from rocketdoo.core.instance.config_manager import InstanceConfigManager
    from rocketdoo.core.odoo_db import list_databases

    try:
        manager = InstanceConfigManager(Path.cwd())
        if not manager.exists():
            return {"ok": False, "error": "No instance.yaml found. Run rkd instance init first."}
        config = manager.load()
        if body.env not in config["environments"]:
            return {"ok": False, "error": f"Environment '{body.env}' is not configured in instance.yaml."}
        if body.seed_database and body.seed_database not in list_databases():
            return {"ok": False, "error": f"Unknown local database '{body.seed_database}'."}
        config["environments"][body.env]["seed_database"] = body.seed_database
        manager.save(config)
        return {"ok": True, "env": body.env, "seed_database": body.seed_database}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class DeployRequest(BaseModel):
    env: str
    dry_run: bool = False


@router.post("/deploy")
def deploy_instance(body: DeployRequest):
    result = {"ok": False, "dry_run": body.dry_run, "env": body.env}
    try:
        from rocketdoo.core.instance.config_manager import InstanceConfigManager

        project_path = Path.cwd()
        manager = InstanceConfigManager(project_path)
        if not manager.exists():
            return {**result, "error": "No instance.yaml found. Run rkd instance init first."}
        env_cfg = manager.get_env(body.env)
        if not env_cfg:
            return {**result, "error": f"Environment '{body.env}' is not configured in instance.yaml."}
        if env_cfg.get("type", "docker") == "docker":
            from rocketdoo.core.instance.deployer_docker import DockerInstanceDeployer

            deployer = DockerInstanceDeployer(body.env, env_cfg, project_path)
        else:
            from rocketdoo.core.instance.deployer_native import NativeInstanceDeployer

            deployer = NativeInstanceDeployer(body.env, env_cfg, project_path)
        ok = deployer.deploy(dry_run=body.dry_run)
        blocking = [c for c in deployer.checks if c["level"] == "block" and not c["ok"]]
        result.update(ok=ok, checks=deployer.checks)
        if not ok and blocking:
            result["error"] = "; ".join(f"{c['name']}: {c['detail']}" for c in blocking)
        return result
    except Exception as e:
        return {**result, "error": str(e)}
