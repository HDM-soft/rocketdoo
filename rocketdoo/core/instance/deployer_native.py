"""
RocketDoo Instance - Native deployer
Installs Odoo via nightly packages on a bare VPS (no Docker).

Install flow:
  1. Install system dependencies + PostgreSQL
  2. Add Odoo nightly apt repository
  3. apt install odoo
  4. Configure /etc/odoo/odoo.conf
  5. rsync extra-addons to remote addons path
  6. Enable + start odoo systemd service
  7. Configure Traefik (optional)
"""
import subprocess
from pathlib import Path

from rich.console import Console

from .config_manager import LOG_LEVEL_BY_ENV, MEMORY_BY_ENV, WORKERS_BY_ENV
from .secrets_store import ADMIN_PASSWD, load, random_password, save
from .ssh_utils import build_rsync_cmd, build_ssh_cmd, resolve_auth

console = Console()

# Odoo nightly repository base URL
_NIGHTLY_KEY_URL = 'https://nightly.odoo.com/odoo.key'
_NIGHTLY_DEB_URL = 'https://nightly.odoo.com/{version}/nightly/deb/'


class NativeInstanceDeployer:
    """
    Deploys Odoo natively (systemd service) on a Debian/Ubuntu VPS.

    Remote structure:
        /etc/odoo/odoo.conf
        /opt/odoo/extra-addons/     ← rsync'd from local
        /opt/odoo/enterprise/       ← if use_enterprise
    """

    def __init__(self, env: str, env_config: dict, project_path: Path):
        self.env = env
        self.cfg = env_config
        self.project_path = Path(project_path)

        vps = env_config['vps']
        self.host = vps['host']
        self.port = vps.get('port', 22)
        self.user = vps['user']
        self.auth = resolve_auth(vps, env, project_path)

        self.odoo_version = env_config.get('odoo_version', '17.0')
        self.remote_addons = env_config.get('remote_path', '/opt/odoo/extra-addons')
        self.odoo_conf_path = '/etc/odoo/odoo.conf'
        self.service_name = 'odoo'

    # ─── public API ──────────────────────────────────────────────────────────

    def deploy(self, dry_run: bool = False) -> bool:
        """Run full native deployment. Returns True on success."""
        console.print(f'\n[bold cyan]Native deploy {self.env.upper()} → {self.host}[/bold cyan]\n')

        if dry_run:
            self._show_plan()
            return True

        steps = [
            ('Installing system dependencies',   self._install_system_deps),
            ('Adding Odoo nightly repository',   self._add_odoo_repo),
            ('Installing Odoo',                  self._install_odoo),
            ('Configuring odoo.conf',            self._configure_odoo),
            ('Syncing extra-addons',             self._sync_addons),
            ('Enabling Odoo service',            self._enable_service),
        ]

        for label, fn in steps:
            console.print(f'[dim]{label}...[/dim]')
            if not fn():
                console.print(f'[red]✗ Failed: {label}[/red]')
                return False
            console.print(f'[green]✓[/green] {label}')

        return True

    # ─── install steps ────────────────────────────────────────────────────────

    def _install_system_deps(self) -> bool:
        cmd = (
            'apt-get update -qq && '
            'apt-get install -y --no-install-recommends '
            'curl wget gnupg2 apt-transport-https postgresql postgresql-client'
        )
        return self._ssh(f'sudo bash -c "{cmd}"')

    def _add_odoo_repo(self) -> bool:
        major = self.odoo_version.split('.')[0]
        key_cmd = (
            f'wget -q -O - {_NIGHTLY_KEY_URL} | '
            'gpg --dearmor -o /usr/share/keyrings/odoo-archive-keyring.gpg'
        )
        repo_url = _NIGHTLY_DEB_URL.format(version=self.odoo_version)
        repo_cmd = (
            f'echo "deb [signed-by=/usr/share/keyrings/odoo-archive-keyring.gpg] '
            f'{repo_url} ./" | tee /etc/apt/sources.list.d/odoo.list'
        )
        return (
            self._ssh(f'sudo bash -c \'{key_cmd}\'') and
            self._ssh(f'sudo bash -c \'{repo_cmd}\'') and
            self._ssh('sudo apt-get update -qq && sudo apt-get install -y odoo')
        )

    def _install_odoo(self) -> bool:
        return self._ssh('sudo apt-get install -y odoo')

    def _resolve_admin_passwd(self) -> str:
        """
        Return the Odoo master password, generating one only on first deploy.

        Reused from the local secret store so it stays stable across deploys
        and remains recoverable (it used to default to the literal 'admin').
        """
        configured = self.cfg.get('admin_passwd')
        if configured:
            return configured

        stored = load(self.project_path, self.env).get(ADMIN_PASSWD)
        if stored:
            return stored

        generated = random_password()
        path = save(self.project_path, self.env, {ADMIN_PASSWD: generated})
        console.print(f'[green]✔[/green] Generated admin_passwd, stored in {path}')
        return generated

    def _configure_odoo(self) -> bool:
        db_user = self.cfg.get('db_user', f'odoo_{self.env}')
        admin_passwd = self._resolve_admin_passwd()
        workers = WORKERS_BY_ENV.get(self.env, 2)
        log_level = LOG_LEVEL_BY_ENV.get(self.env, 'info')
        mem = MEMORY_BY_ENV[self.env]
        use_enterprise = self.cfg.get('use_enterprise', False)

        addons_path = self.remote_addons
        if use_enterprise:
            addons_path += ',/opt/odoo/enterprise'

        conf = (
            '[options]\n'
            f'addons_path = {addons_path}\n'
            'data_dir = /var/lib/odoo\n'
            f'admin_passwd = {admin_passwd}\n'
            f'db_host = localhost\n'
            f'db_port = 5432\n'
            f'db_user = {db_user}\n'
            'db_password = False\n'
            'db_maxconn = 64\n'
            f'log_level = {log_level}\n'
            'logfile = /var/log/odoo/odoo-server.log\n'
            f'workers = {workers}\n'
            'gevent_port = 8072\n'
            f'limit_memory_hard = {mem["hard"]}\n'
            f'limit_memory_soft = {mem["soft"]}\n'
            'proxy_mode = True\n'
            'list_db = False\n'
        )

        # Write conf via heredoc on remote
        escaped = conf.replace("'", "'\"'\"'")
        return self._ssh(
            f"sudo bash -c 'cat > {self.odoo_conf_path} << \\'ENDCONF\\'\n{conf}\nENDCONF'"
        )

    def _sync_addons(self) -> bool:
        addons_dir = self.project_path / 'addons'
        if not addons_dir.exists():
            console.print('[yellow]  No local addons/ directory found — skipping sync[/yellow]')
            return True

        self._ssh(f'sudo mkdir -p {self.remote_addons} && sudo chown {self.user} {self.remote_addons}')
        cmd = build_rsync_cmd(
            self.auth, self.port, self.user, self.host,
            f'{addons_dir}/', f'{self.remote_addons}/'
        )
        result = subprocess.run(cmd)
        return result.returncode == 0

    def _enable_service(self) -> bool:
        return self._ssh(
            f'sudo systemctl enable {self.service_name} && '
            f'sudo systemctl restart {self.service_name}'
        )

    # ─── helpers ─────────────────────────────────────────────────────────────

    def _ssh(self, command: str, timeout: int = 300) -> bool:
        cmd = build_ssh_cmd(self.auth, self.port, self.user, self.host, command)
        return subprocess.run(cmd, timeout=timeout).returncode == 0

    def _show_plan(self):
        console.print('\n[bold]DRY-RUN — Steps that would be executed:[/bold]')
        steps = [
            'apt-get install system dependencies + postgresql',
            f'Add Odoo {self.odoo_version} nightly apt repository',
            'apt-get install odoo',
            f'Write {self.odoo_conf_path}',
            f'rsync addons/ → {self.remote_addons}',
            f'systemctl enable + restart {self.service_name}',
        ]
        for i, s in enumerate(steps, 1):
            console.print(f'  [cyan]{i}.[/cyan] {s}')
        console.print()

    def health_check(self) -> bool:
        domain = self.cfg.get('domain', '')
        if not domain:
            return False
        return self._ssh(f'curl -sf --max-time 10 https://{domain}/web/health -o /dev/null')
