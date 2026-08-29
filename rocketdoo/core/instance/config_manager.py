"""
RocketDoo Instance - Configuration manager
Manages .rkd/instance.yaml for stage/prod deployments
"""

import secrets
import string
from pathlib import Path

import questionary
import yaml
from questionary import Style
from rich.console import Console
from rich.prompt import Confirm, Prompt

from rocketdoo.core.models import InstanceConfig
from rocketdoo.core.ssh_manager import list_private_keys

console = Console()

_custom_style = Style(
    [
        ("qmark", "fg:#673ab7 bold"),
        ("question", "bold"),
        ("answer", "fg:#2196f3 bold"),
        ("pointer", "fg:#673ab7 bold"),
        ("highlighted", "fg:#673ab7 bold"),
        ("selected", "fg:#4caf50"),
    ]
)

CONFIG_FILE = ".rkd/instance.yaml"

PG_PROFILES: dict[str, dict] = {
    "small": {
        "shared_buffers": "256MB",
        "effective_cache": "512MB",
        "work_mem": "8MB",
        "maintenance_work_mem": "64MB",
        "max_connections": 50,
        "wal_buffers": "8MB",
        "max_wal_size": "1GB",
    },
    "medium": {
        "shared_buffers": "512MB",
        "effective_cache": "1GB",
        "work_mem": "16MB",
        "maintenance_work_mem": "128MB",
        "max_connections": 50,
        "wal_buffers": "16MB",
        "max_wal_size": "2GB",
    },
    "large": {
        "shared_buffers": "4GB",
        "effective_cache": "10GB",
        "work_mem": "32MB",
        "maintenance_work_mem": "512MB",
        "max_connections": 200,
        "wal_buffers": "64MB",
        "max_wal_size": "4GB",
    },
}

WORKERS_BY_ENV = {"stage": 2, "prod": 4}
LOG_LEVEL_BY_ENV = {"stage": "info", "prod": "warn"}
MEMORY_BY_ENV = {
    "stage": {"hard": 1_610_612_736, "soft": 1_073_741_824},  # 1.5 / 1 GB
    "prod": {"hard": 2_684_354_560, "soft": 2_147_483_648},  # 2.5 / 2 GB
}


def _random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class InstanceConfigManager:
    def __init__(self, project_path: Path):
        self.project_path = Path(project_path)
        self.config_path = self.project_path / CONFIG_FILE

    def exists(self) -> bool:
        return self.config_path.exists()

    def load(self) -> dict:
        if not self.exists():
            raise FileNotFoundError(f"Instance config not found: {self.config_path}")
        raw = yaml.safe_load(self.config_path.read_text()) or {}
        # Normalise through the model so a file written by an older GUI (flat
        # connection fields, no `environments` wrapper) still deploys.
        return InstanceConfig.model_validate(raw).to_yaml_dict()

    def save(self, config: dict):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def get_env(self, env: str) -> dict:
        return self.load().get("environments", {}).get(env, {})

    def interactive_setup(self) -> dict:
        """Full interactive wizard. Returns the config dict that was saved."""
        console.print("[bold]Which environments do you want to configure?[/bold]")
        envs = questionary.checkbox(
            "Select environments:",
            choices=[
                questionary.Choice("stage", checked=True),
                questionary.Choice("prod", checked=True),
            ],
            style=_custom_style,
        ).ask()
        if not envs:
            raise SystemExit("No environments selected.")

        config: dict = {"environments": {}}

        for env in envs:
            console.print(f"\n[bold cyan]── {env.upper()} environment ──[/bold cyan]")
            config["environments"][env] = self._wizard_env(env)

        self.save(config)
        return config

    def _wizard_env(self, env: str) -> dict:
        dep_type = questionary.select(
            "Deployment type:",
            choices=[
                questionary.Choice("Docker (recommended)", value="docker"),
                questionary.Choice("Native  (Odoo nightly packages)", value="native"),
            ],
            style=_custom_style,
        ).ask()

        console.print("\n[bold]VPS Connection[/bold]")
        host = Prompt.ask("VPS hostname or IP")
        port = int(Prompt.ask("SSH port", default="22"))
        user = Prompt.ask("SSH user", default="ubuntu")

        auth_method = questionary.select(
            "Authentication method:",
            choices=[
                questionary.Choice("SSH key  (recommended)", value="ssh_key"),
                questionary.Choice("Password", value="password"),
            ],
            style=_custom_style,
        ).ask()

        ssh_key = ""
        password_ref = ""

        if auth_method == "ssh_key":
            available_keys = list_private_keys()
            if not available_keys:
                console.print("[yellow]⚠ No SSH keys found in ~/.ssh/[/yellow]")
                console.print("[dim]Generate one with: ssh-keygen -t ed25519[/dim]")
                ssh_key = Prompt.ask("SSH key path (full)", default="~/.ssh/id_rsa")
            else:
                console.print(f"[dim]Found {len(available_keys)} key(s) in ~/.ssh/[/dim]")
                selected = questionary.select(
                    "Select the SSH key configured on this VPS:", choices=available_keys, style=_custom_style
                ).ask()
                ssh_key = f"~/.ssh/{selected}"
        else:
            console.print("[dim]Password will be requested at deploy time or read from the env var below.[/dim]")
            env_var = f"INSTANCE_{env.upper()}_PASSWORD"
            password_ref = Prompt.ask("Password env variable name", default=env_var)
            password_ref = f"${{{password_ref}}}"

        console.print("\n[bold]Odoo[/bold]")
        odoo_version = Prompt.ask("Odoo version", default="17.0")
        odoo_tag = Prompt.ask("Docker image tag", default=odoo_version)
        domain = Prompt.ask("Domain (e.g. erp.mycompany.com)")
        traefik_email = Prompt.ask("Email for Let's Encrypt")
        db_version = Prompt.ask("PostgreSQL version", default="16")
        db_user = Prompt.ask("DB user", default=f"odoo_{env}")
        admin_passwd = Prompt.ask("Odoo master password", default=_random_password())
        use_enterprise = Confirm.ask("Use Odoo Enterprise?", default=False)
        use_gitman = Confirm.ask("Use Gitman for third-party repos?", default=False)
        gitman_config = ""
        if use_gitman:
            gitman_config = Prompt.ask("Gitman config file (local path)", default=f"gitman.{env}.yml")

        console.print("\n[bold]PostgreSQL profile[/bold]")
        pg_profile = questionary.select(
            "Resource profile:",
            choices=[
                questionary.Choice("small  — <2 GB RAM VPS", value="small"),
                questionary.Choice("medium — 4–8 GB RAM VPS", value="medium"),
                questionary.Choice("large  — 16+ GB RAM VPS", value="large"),
            ],
            style=_custom_style,
        ).ask()

        remote_path = Prompt.ask("Remote base path", default=f"/opt/odoo-{env}")

        vps_cfg: dict = {"host": host, "port": port, "user": user, "auth_method": auth_method}
        if auth_method == "ssh_key":
            vps_cfg["ssh_key"] = ssh_key
        else:
            vps_cfg["password"] = password_ref

        return {
            "type": dep_type,
            "vps": vps_cfg,
            "odoo_version": odoo_version,
            "odoo_tag": odoo_tag,
            "domain": domain,
            "traefik_email": traefik_email,
            "db_version": db_version,
            "db_user": db_user,
            "admin_passwd": admin_passwd,
            "use_enterprise": use_enterprise,
            "use_gitman": use_gitman,
            "gitman_config": gitman_config,
            "pg_profile": pg_profile,
            "remote_path": remote_path,
        }
