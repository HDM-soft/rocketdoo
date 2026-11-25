"""
RocketDoo Instance - Configuration manager
Manages .rkd/instance.yaml for stage/prod deployments
"""

import copy
import re
import secrets
import string
from pathlib import Path

import questionary
import yaml
from questionary import Style
from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt

from rocketdoo.core.models import InstanceConfig
from rocketdoo.core.models.instance import NATIVE_ADDONS_PATH
from rocketdoo.core.odoo_db import databases_result
from rocketdoo.core.project_info import get_project_info, read_docker_compose
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

# The file `rkd init` writes; the wizard proposes it and the pre-flight falls back to it.
DEFAULT_GITMAN_FILE = "gitman.yaml"
# The project's own Python dependencies: what its modules declare under
# external_dependencies. Not the same as install_dependencies.sh, which the
# `scripts:` of each gitman source run with `cd` inside the cloned repo and so
# only ever sees that repo's file (#336).
REQUIREMENTS_FILE = "requirements.txt"
REMOTE_REQUIREMENTS = "/opt/odoo/requirements.txt"

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

WORKERS_BY_ENV = {"stage": 1, "prod": 2}
LOG_LEVEL_BY_ENV = {"stage": "info", "prod": "warn"}
# Odoo applies limit_memory_* to each worker, not to the whole server, so
# the totals are roughly workers x hard limit plus PostgreSQL's own share.
MEMORY_BY_ENV = {
    "stage": {"hard": 1_073_741_824, "soft": 805_306_368},  # 1 GiB / 768 MiB
    "prod": {"hard": 1_610_612_736, "soft": 1_073_741_824},  # 1.5 GiB / 1 GiB
}

# The native deployer does not tune postgresql.conf, so this is what a stock
# Debian cluster allows.
DEBIAN_PG_MAX_CONNECTIONS = 100
DEBIAN_PG_SHARED_BUFFERS = "128MB"

# Cron threads in both odoo.conf files, and in the pre-flight's memory sum.
CRON_THREADS = 1

# Odoo's own defaults are 60/120 seconds and 2**16 requests. rkd has always
# emitted 8192 on the Docker path and nothing at all on the native one, so the
# native conf fell back to Odoo's. These are now the single answer for both.
TIME_LIMITS = {"limit_time_cpu": 60, "limit_time_real": 120, "limit_request": 8192}

# The order the wizard asks them in, and the keys `resources` accepts.
RESOURCE_KEYS = (
    "workers",
    "max_cron_threads",
    "limit_memory_soft",
    "limit_memory_hard",
    "limit_time_cpu",
    "limit_time_real",
    "limit_request",
)


def resources_for(env: str, cfg: dict) -> dict:
    """The seven tuning values this environment's `odoo.conf` is written with.

    The defaults come from the environment name, as they always did; whatever
    `resources` names overrides them, and what it leaves out keeps the default.
    Both deployers and the pre-flight read this, so the conf that gets written
    and the memory the pre-flight adds up can never be two different answers.
    """
    mem = MEMORY_BY_ENV.get(env, MEMORY_BY_ENV["stage"])
    values = {
        "workers": WORKERS_BY_ENV.get(env, WORKERS_BY_ENV["stage"]),
        "max_cron_threads": CRON_THREADS,
        "limit_memory_soft": mem["soft"],
        "limit_memory_hard": mem["hard"],
        **TIME_LIMITS,
    }
    configured = cfg.get("resources") or {}
    # Only the seven, so the return value is what the docstring says even if a
    # caller hands over a dict that never went through the model.
    values.update({k: v for k, v in configured.items() if k in RESOURCE_KEYS and v is not None})
    return values


def db_maxconn(pg_max_connections: int, workers: int) -> int:
    """Connections each Odoo process may open without exhausting PostgreSQL.

    The extra 2 are the cron and gevent processes; 5 are left for admin use.
    """
    return max(8, (pg_max_connections - 5) // (workers + 2))


def _random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# Role and database names reach psql inside an SQL statement and a remote
# shell command, in both deployers. Restricting them to a plain identifier is
# what makes both safe, instead of a quoting rule per destination. `fullmatch`
# rather than `$`, which also matches before a trailing newline (#248).
DB_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")


def invalid_identifiers(*names: str) -> list[str]:
    """The given names that PostgreSQL could not take as a plain identifier."""
    return [name for name in names if not DB_IDENTIFIER.fullmatch(name)]


def merge_environments(existing: dict, updates: dict) -> dict:
    """The configuration `existing` with each environment in `updates` added or replaced.

    Environments that `updates` does not name are carried over untouched.
    """
    return {
        **copy.deepcopy(existing),
        "environments": {**copy.deepcopy(existing.get("environments", {})), **copy.deepcopy(updates)},
    }


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
        """Write the configuration, refusing one the model cannot read back.

        A cancelled `questionary` prompt returns None instead of raising, so
        a half-answered wizard reaches here with `None` in required fields.
        """
        InstanceConfig.model_validate(config)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def get_env(self, env: str) -> dict:
        return self.load().get("environments", {}).get(env, {})

    def interactive_setup(self, fresh: bool = False) -> dict:
        """Interactive wizard. Returns the configuration that is on disk after it.

        Environments already configured are kept unless the user chooses to
        reconfigure them; `fresh` ignores the existing file and starts over.
        """
        existing = {"environments": {}} if fresh or not self.exists() else self.load()
        known = existing["environments"]

        types = {env: cfg["type"] for env, cfg in known.items()}
        if len(set(types.values())) > 1:
            listed = ", ".join(f"{env}: {kind}" for env, kind in types.items())
            console.print(
                f"[yellow]This file mixes deployment types ({listed}). That combination is not supported: "
                "the deployers differ in features (gitman, PostgreSQL tuning, proxy), not only in packaging. "
                "Each environment keeps its own type; nothing is converted.[/yellow]"
            )
        elif types:
            console.print(
                f"[dim]Existing configuration uses {next(iter(types.values()))}; "
                "run with --force to change it and start over.[/dim]"
            )

        selected = []
        for env in ("stage", "prod"):
            if env in known:
                host = known[env]["vps"]["host"]
                domain = known[env].get("domain")
                where = ", ".join(
                    part for part in (f"host {host}" if host else "", f"domain {domain}" if domain else "") if part
                )
                if Confirm.ask(f"Reconfigure {env}?" + (f" ({where})" if where else ""), default=False):
                    selected.append(env)
            elif Confirm.ask(f"Configure a {env} environment?", default=True):
                selected.append(env)

        if not selected:
            if not known:
                raise SystemExit("No environments selected.")
            console.print("[dim]Nothing changed.[/dim]")
            return existing

        new_type = next(iter(types.values()), None)
        updates: dict = {}
        for env in selected:
            if env in known:
                dep_type = types[env]
            else:
                if new_type is None:
                    new_type = questionary.select(
                        "Deployment type (applies to every new environment):",
                        choices=[
                            questionary.Choice("Docker (recommended)", value="docker"),
                            questionary.Choice("Native  (Odoo nightly packages)", value="native"),
                        ],
                        style=_custom_style,
                    ).ask()
                dep_type = new_type
            console.print(f"\n[bold cyan]── {env.upper()} environment ──[/bold cyan]")
            updates[env] = {**known.get(env, {}), **self._wizard_env(env, dep_type, known.get(env))}

        config = merge_environments(existing, updates)
        self.save(config)
        return config

    def _ask_seed_database(self, env: str) -> str:
        """Which local database to restore into `env`; empty for a new one from scratch."""
        console.print("\n[bold]Database[/bold]")
        console.print(
            "[dim]Recommended: start from scratch. The deploy creates an empty database, installs base and "
            "gives you the password of the admin user.[/dim]"
        )
        databases, reason = databases_result(read_docker_compose(self.project_path) or {})
        if not databases:
            if reason:
                console.print(f"[yellow]No local database to offer: {reason}[/yellow]")
            return ""
        choices = [questionary.Choice("None - a new database from scratch  (recommended)", value="")]
        choices += [
            questionary.Choice(f"Restore local database '{name}' (with its filestore)", value=name) for name in databases
        ]
        return questionary.select(f"Database for {env}:", choices=choices, default=choices[0], style=_custom_style).ask() or ""

    @staticmethod
    def _wizard_resources(env: str, current: dict) -> dict:
        """The performance block of this environment's odoo.conf.

        One question first, defaulting to no: the values rkd derives from the
        environment name fit a small VPS and most projects never need to touch
        them. Saying yes shows each one with the effective value as its default,
        so answering through leaves the configuration unchanged.
        """
        defaults = resources_for(env, {"resources": current})
        console.print(
            f"\n[dim]Performance: {defaults['workers']} workers, "
            f"{defaults['limit_memory_soft'] // 1024 // 1024} MiB soft limit, "
            f"{defaults['limit_time_real']}s request limit.[/dim]"
        )
        # Not bool(current): load() materialises the seven keys as null for
        # every environment, so that was True for any existing one -- seven
        # prompts nobody asked for, and the defaults pinned into the file.
        tuned = any(value is not None for value in current.values())
        if not Confirm.ask("Tune Odoo's performance settings for this server?", default=tuned):
            # Not {}: an environment that had values and now declines keeps
            # them, the same way every other answered-through question does.
            return dict(current)

        console.print(
            "[dim]Memory limits are per worker, in bytes. Time limits are seconds; Odoo's own\n"
            "defaults (60/120) cut a large import or a heavy report in half.[/dim]"
        )
        # IntPrompt and not Prompt: it re-asks on a typo instead of accepting it,
        # and there is no conversion left to get wrong. Neither returns None on
        # Ctrl-C -- it raises, and instance_cli catches it before anything is
        # written.
        return {key: IntPrompt.ask(f"  {key}", default=defaults[key]) for key in RESOURCE_KEYS}

    def _wizard_env(self, env: str, dep_type: str, known: dict | None = None) -> dict:
        docker = dep_type == "docker"
        project = get_project_info(self.project_path)

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
                # A cancelled select returns None, and f"~/.ssh/{None}" validates
                # as a str: the deploy then fails on a key named "None".
                ssh_key = f"~/.ssh/{selected}" if selected else Prompt.ask("SSH key path (full)", default="~/.ssh/id_rsa")
        else:
            console.print("[dim]Password will be requested at deploy time or read from the env var below.[/dim]")
            env_var = f"INSTANCE_{env.upper()}_PASSWORD"
            password_ref = Prompt.ask("Password env variable name", default=env_var)
            password_ref = f"${{{password_ref}}}"

        console.print("\n[bold]Odoo[/bold]")
        odoo_version = Prompt.ask("Odoo version", default=project.odoo_version or "17.0")
        odoo_tag = domain = traefik_email = db_version = ""
        if docker:
            odoo_tag = Prompt.ask("Docker image tag", default=odoo_version)
            domain = Prompt.ask("Domain (e.g. erp.mycompany.com)")
            traefik_email = Prompt.ask("Email for Let's Encrypt")
            db_version = Prompt.ask("PostgreSQL version", default=project.db_version or "16")
        while True:
            db_user = Prompt.ask("DB user", default=f"odoo_{env}")
            db_name = Prompt.ask("Database name", default=db_user)
            invalid = invalid_identifiers(db_user, db_name)
            if not invalid:
                break
            # Checked here as well as at deploy time: otherwise a name
            # PostgreSQL cannot take is only reported minutes into a deploy.
            console.print(f"[red]Not a valid PostgreSQL identifier: {', '.join(invalid)}[/red]")
            console.print("[dim]Up to 63 letters, digits and underscores, and cannot start with a digit.[/dim]")
        generated_passwd = _random_password()
        admin_passwd = Prompt.ask("Odoo master password", password=True, show_default=False, default=generated_passwd)
        if admin_passwd == generated_passwd:
            console.print(f"Generated master password: [bold]{generated_passwd}[/bold]")

        seed_database = self._ask_seed_database(env)

        use_enterprise = Confirm.ask("Use Odoo Enterprise?", default=False)
        enterprise_source = "rsync"
        if use_enterprise:
            has_local = (self.project_path / "enterprise").is_dir()
            enterprise_source = questionary.select(
                "How should Enterprise reach the server?",
                choices=[
                    questionary.Choice(
                        "Transfer the local enterprise/ directory (hundreds of MB on the first deploy)",
                        value="rsync",
                    ),
                    questionary.Choice(
                        "Clone odoo/enterprise on the VPS using your forwarded SSH agent "
                        "(no credential is stored on the server; requires ssh-add and repository access)",
                        value="clone",
                    ),
                ],
                default="rsync" if has_local else "clone",
                style=_custom_style,
            ).ask()
            if enterprise_source == "rsync" and not has_local:
                console.print("[yellow]⚠ There is no enterprise/ directory in this project to transfer.[/yellow]")

        use_gitman = Confirm.ask("Use Gitman for third-party repos?", default=False)
        gitman_config = ""
        if use_gitman:
            gitman_config = Prompt.ask("Gitman config file (local path)", default=DEFAULT_GITMAN_FILE)

        pg_profile = ""
        if docker:
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

        resources = self._wizard_resources(env, (known or {}).get("resources") or {})

        if docker:
            remote_path = Prompt.ask("Remote base path", default=f"/opt/odoo-{env}")
        else:
            remote_path = Prompt.ask("Remote addons path", default=NATIVE_ADDONS_PATH)

        vps_cfg: dict = {"host": host, "port": port, "user": user, "auth_method": auth_method}
        if auth_method == "ssh_key":
            vps_cfg["ssh_key"] = ssh_key
        else:
            vps_cfg["password"] = password_ref

        result = {
            "type": dep_type,
            "vps": vps_cfg,
            "odoo_version": odoo_version,
            "db_user": db_user,
            "db_name": db_name,
            "admin_passwd": admin_passwd,
            "seed_database": seed_database,
            "use_enterprise": use_enterprise,
            "enterprise_source": enterprise_source,
            "use_gitman": use_gitman,
            "gitman_config": gitman_config,
            "resources": resources,
            "remote_path": remote_path,
        }
        if docker:
            result.update(
                odoo_tag=odoo_tag,
                domain=domain,
                traefik_email=traefik_email,
                db_version=db_version,
                pg_profile=pg_profile,
            )
        return result
