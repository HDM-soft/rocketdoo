"""
RocketDoo Instance - Docker deployer
Transfers files and deploys a full Odoo instance on a dockerized VPS.

Build flow (requires SSH agent for gitman private repos):
  Local → rsync files → VPS
  VPS   → DOCKER_BUILDKIT=1 docker compose build --ssh default
  VPS   → docker compose up -d
"""
import os
import secrets
import shutil
import string
import subprocess
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from .config_manager import (
    LOG_LEVEL_BY_ENV,
    MEMORY_BY_ENV,
    PG_PROFILES,
    WORKERS_BY_ENV,
)
from .ssh_utils import build_rsync_cmd, build_ssh_cmd, resolve_auth

console = Console()

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / 'templates' / 'instance'


def _random_password(n: int = 20) -> str:
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


class DockerInstanceDeployer:
    """
    Deploys a full Odoo instance (stage or prod) to a VPS running Docker.

    Remote directory structure created at `remote_path`:
        ├── Dockerfile.{env}
        ├── extra-addons/          ← rsync'd from local addons/
        ├── {env}/
        │   ├── docker-compose.yml
        │   ├── odoo_pg_pass
        │   └── config/
        │       └── odoo.conf
        └── gitman.{env}.yml       ← optional
    """

    def __init__(self, env: str, env_config: dict, project_path: Path):
        self.env = env
        self.cfg = env_config
        self.project_path = Path(project_path)

        vps = env_config['vps']
        self.host = vps['host']
        self.port = vps.get('port', 22)
        self.user = vps['user']
        self.remote_path = env_config.get('remote_path', f'/opt/odoo-{env}')
        self.auth = resolve_auth(vps, env, project_path)

        self.pg = PG_PROFILES[env_config.get('pg_profile', 'medium')]
        self.project_name = self._detect_project_name()
        self.slug = self.project_name.replace('-', '_').replace(' ', '_')

    # ─── public API ──────────────────────────────────────────────────────────

    def deploy(self, dry_run: bool = False) -> bool:
        """Run full deployment. Returns True on success."""
        console.print(f'\n[bold cyan]Deploying {self.env.upper()} to {self.host}[/bold cyan]\n')

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            console.print('[dim]Step 1/5 — Rendering configuration files...[/dim]')
            self._render_templates(tmp_path)
            console.print('[green]✓[/green] Configuration files rendered')

            if dry_run:
                console.print('\n[yellow]DRY-RUN: Skipping transfer and build.[/yellow]')
                self._show_generated_files(tmp_path)
                return True

            console.print('[dim]Step 2/5 — Preparing remote directory...[/dim]')
            if not self._prepare_remote():
                return False
            console.print('[green]✓[/green] Remote directory ready')

            console.print('[dim]Step 3/5 — Transferring files...[/dim]')
            if not self._transfer(tmp_path):
                return False
            console.print('[green]✓[/green] Files transferred')

            console.print('[dim]Step 4/5 — Building Docker image (this may take a while)...[/dim]')
            if not self._build():
                return False
            console.print('[green]✓[/green] Image built')

            console.print('[dim]Step 5/5 — Starting services...[/dim]')
            if not self._start():
                return False
            console.print('[green]✓[/green] Services started')

        return True

    # ─── template rendering ───────────────────────────────────────────────────

    def _render_templates(self, tmp: Path):
        """Render all Jinja2 templates into a temporary directory."""
        jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        db_user = self.cfg.get('db_user', f'odoo_{self.env}')
        use_enterprise = self.cfg.get('use_enterprise', False)
        use_gitman = self.cfg.get('use_gitman', False)
        gitman_config = self.cfg.get('gitman_config', 'gitman.yml')
        admin_passwd = self.cfg.get('admin_passwd', _random_password())

        ctx = {
            'environment': self.env,
            'project_name': self.project_name,
            'slug': f'{self.slug}_{self.env}',
            'odoo_tag': self.cfg.get('odoo_tag', self.cfg.get('odoo_version', '17.0')),
            'domain': self.cfg.get('domain', ''),
            'traefik_email': self.cfg.get('traefik_email', ''),
            'db_user': db_user,
            'db_version': str(self.cfg.get('db_version', '16')),
            'use_enterprise': use_enterprise,
            'use_gitman': use_gitman,
            'gitman_config_file': gitman_config,
            'pg': self.pg,
            # odoo.conf specific
            'admin_passwd': admin_passwd,
            'workers': WORKERS_BY_ENV.get(self.env, 2),
            'log_level': LOG_LEVEL_BY_ENV.get(self.env, 'info'),
            'limit_memory_hard': MEMORY_BY_ENV[self.env]['hard'],
            'limit_memory_soft': MEMORY_BY_ENV[self.env]['soft'],
        }

        # Dockerfile.{env}
        dockerfile = jinja_env.get_template('Dockerfile.jinja').render(**ctx)
        (tmp / f'Dockerfile.{self.env}').write_text(dockerfile)

        # {env}/docker-compose.yml
        env_dir = tmp / self.env
        env_dir.mkdir()
        compose = jinja_env.get_template('docker-compose.jinja').render(**ctx)
        (env_dir / 'docker-compose.yml').write_text(compose)

        # {env}/config/odoo.conf
        config_dir = env_dir / 'config'
        config_dir.mkdir()
        odoo_conf = jinja_env.get_template('odoo.conf.jinja').render(**ctx)
        (config_dir / 'odoo.conf').write_text(odoo_conf)

        # {env}/odoo_pg_pass
        pg_pass = _random_password(32)
        (env_dir / 'odoo_pg_pass').write_text(pg_pass)

        # gitman config (if enabled and local file exists)
        if use_gitman and gitman_config:
            local_gitman = self.project_path / gitman_config
            if local_gitman.exists():
                shutil.copy(local_gitman, tmp / gitman_config)
            else:
                console.print(f'[yellow]⚠ Gitman config {gitman_config} not found locally — skipping[/yellow]')

    def _show_generated_files(self, tmp: Path):
        console.print('\n[bold]Generated files (dry-run):[/bold]')
        for f in sorted(tmp.rglob('*')):
            if f.is_file():
                console.print(f'  [cyan]{f.relative_to(tmp)}[/cyan]')

    # ─── SSH / transfer helpers ───────────────────────────────────────────────

    def _ssh(self, command: str, agent_forward: bool = False, timeout: int = 600) -> bool:
        cmd = build_ssh_cmd(
            self.auth, self.port, self.user, self.host,
            command, agent_forward=agent_forward
        )
        return subprocess.run(cmd, timeout=timeout).returncode == 0

    def _rsync(self, local: str, remote: str, extra_opts: list[str] | None = None) -> bool:
        cmd = build_rsync_cmd(
            self.auth, self.port, self.user, self.host,
            local, remote, extra_opts
        )
        return subprocess.run(cmd).returncode == 0

    # ─── deployment steps ────────────────────────────────────────────────────

    def _prepare_remote(self) -> bool:
        dirs = [
            self.remote_path,
            f'{self.remote_path}/extra-addons',
            f'{self.remote_path}/{self.env}/config',
        ]
        return self._ssh(f'mkdir -p {" ".join(dirs)}')

    def _transfer(self, tmp: Path) -> bool:
        remote = f'{self.remote_path}/'

        # Dockerfile.{env}
        dockerfile = tmp / f'Dockerfile.{self.env}'
        if not self._rsync(str(dockerfile), f'{self.remote_path}/Dockerfile.{self.env}'):
            console.print(f'[red]✗ Failed to transfer Dockerfile.{self.env}[/red]')
            return False

        # {env}/ directory (compose + config + pg_pass)
        env_dir = tmp / self.env
        if not self._rsync(f'{env_dir}/', f'{self.remote_path}/{self.env}/'):
            console.print(f'[red]✗ Failed to transfer {self.env}/ config[/red]')
            return False

        # gitman config (if present in tmp)
        gitman_config = self.cfg.get('gitman_config', '')
        if gitman_config:
            local_gitman = tmp / gitman_config
            if local_gitman.exists():
                if not self._rsync(str(local_gitman), f'{self.remote_path}/{gitman_config}'):
                    console.print('[yellow]⚠ Failed to transfer gitman config[/yellow]')

        # extra-addons (from project's addons/ dir)
        addons_dir = self.project_path / 'addons'
        if addons_dir.exists():
            console.print('[dim]  Syncing extra-addons...[/dim]')
            if not self._rsync(f'{addons_dir}/', f'{self.remote_path}/extra-addons/'):
                console.print('[yellow]⚠ Failed to sync extra-addons[/yellow]')

        return True

    def _build(self) -> bool:
        build_cmd = (
            f'cd {self.remote_path}/{self.env} && '
            f'DOCKER_BUILDKIT=1 docker compose build --ssh default'
        )
        ok = self._ssh(build_cmd, agent_forward=True, timeout=1800)
        if not ok:
            console.print('[red]✗ Docker build failed.[/red]')
            console.print('[dim]  Make sure your SSH agent is running and has the required keys loaded.[/dim]')
            console.print('[dim]  Check with: ssh-add -l[/dim]')
        return ok

    def _start(self) -> bool:
        start_cmd = f'cd {self.remote_path}/{self.env} && docker compose up -d'
        return self._ssh(start_cmd)

    # ─── utils ───────────────────────────────────────────────────────────────

    def _detect_project_name(self) -> str:
        try:
            import yaml as _yaml
            for name in ('docker-compose.yaml', 'docker-compose.yml'):
                p = self.project_path / name
                if p.exists():
                    data = _yaml.safe_load(p.read_text())
                    if data and data.get('name'):
                        return data['name']
        except Exception:
            pass
        return self.project_path.name

    def health_check(self) -> bool:
        """Verify Odoo is responding on the VPS."""
        domain = self.cfg.get('domain', '')
        if not domain:
            return False
        cmd = f'curl -sf --max-time 10 https://{domain}/web/health -o /dev/null'
        return self._ssh(cmd)
