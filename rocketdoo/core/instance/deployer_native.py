"""
RocketDoo Instance - Native deployer
Installs Odoo via nightly packages on a bare VPS (no Docker).

Install flow:
  1. Install system dependencies + PostgreSQL
  2. Add Odoo nightly apt repository
  3. apt install odoo
  4. Create the PostgreSQL role Odoo authenticates as
  5. Configure /etc/odoo/odoo.conf
  6. rsync extra-addons to remote addons path
  7. Create and initialise the database Odoo serves
  8. Enable + start odoo systemd service
"""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from .config_manager import LOG_LEVEL_BY_ENV, MEMORY_BY_ENV, WORKERS_BY_ENV
from .secrets_store import ADMIN_LOGIN_PASSWD, ADMIN_PASSWD, PG_PASS, load, random_password, save
from .ssh_utils import build_rsync_cmd, build_ssh_cmd, resolve_auth

console = Console()

# Odoo nightly repository base URL
_NIGHTLY_KEY_URL = "https://nightly.odoo.com/odoo.key"
_NIGHTLY_DEB_URL = "https://nightly.odoo.com/{version}/nightly/deb/"

# The role and database names come from instance.yaml and end up inside an SQL
# statement and a remote shell command. Restricting them to a plain identifier
# is what makes both safe, instead of a quoting rule per destination.
# `fullmatch`, not `$`, which also matches before a trailing newline: a
# name ending in one would split the remote script into two commands.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")

# Odoo's default HTTP port, which neither conf sets explicitly, and how
# many two-second rounds to give it to answer after a restart.
_HTTP_PORT = 8069
_HTTP_RETRIES = 45


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

        vps = env_config["vps"]
        self.host = vps["host"]
        self.port = vps.get("port", 22)
        self.user = vps["user"]
        self.auth = resolve_auth(vps, env, project_path)

        self.odoo_version = env_config.get("odoo_version", "17.0")
        self.remote_addons = env_config.get("remote_path", "/opt/odoo/extra-addons")
        self.odoo_conf_path = "/etc/odoo/odoo.conf"
        self.service_name = "odoo"

        self.db_user = env_config.get("db_user") or f"odoo_{env}"
        self.db_name = env_config.get("db_name") or self.db_user

    # ─── public API ──────────────────────────────────────────────────────────

    def deploy(self, dry_run: bool = False) -> bool:
        """Run full native deployment. Returns True on success."""
        console.print(f"\n[bold cyan]Native deploy {self.env.upper()} → {self.host}[/bold cyan]\n")

        invalid = [name for name in (self.db_user, self.db_name) if not _IDENTIFIER.fullmatch(name)]
        if invalid:
            console.print(f"[red]✗ Not a valid PostgreSQL identifier: {', '.join(invalid)}[/red]")
            console.print(
                "[dim]db_user and db_name may hold up to 63 letters, digits and underscores, and cannot start with a digit.[/dim]"
            )
            return False

        if dry_run:
            self._show_plan()
            return True

        for label, fn in self._steps():
            console.print(f"[dim]{label}...[/dim]")
            if not fn():
                console.print(f"[red]✗ Failed: {label}[/red]")
                return False
            console.print(f"[green]✓[/green] {label}")

        return True

    def _steps(self) -> list[tuple[str, Callable[[], bool]]]:
        """The deployment, in order.

        `deploy()` runs this list and `_show_plan()` prints it, so a dry run
        cannot describe a sequence other than the one that would execute.
        """
        return [
            ("Installing system dependencies + PostgreSQL", self._install_system_deps),
            (f"Adding Odoo {self.odoo_version} nightly repository", self._add_odoo_repo),
            ("Installing Odoo", self._install_odoo),
            (f"Ensuring PostgreSQL role {self.db_user}", self._ensure_db_role),
            (f"Writing {self.odoo_conf_path}", self._configure_odoo),
            (f"Syncing extra-addons → {self.remote_addons}", self._sync_addons),
            (f"Ensuring database {self.db_name}", self._ensure_database),
            (f"Enabling Odoo service ({self.service_name})", self._enable_service),
            ("Verifying Odoo answers", self._verify_odoo_answers),
        ]

    # ─── install steps ────────────────────────────────────────────────────────

    def _install_system_deps(self) -> bool:
        cmd = (
            "apt-get update -qq && "
            "apt-get install -y --no-install-recommends "
            "curl wget gnupg2 apt-transport-https postgresql postgresql-client"
        )
        return self._ssh(f'sudo bash -c "{cmd}"')

    def _add_odoo_repo(self) -> bool:
        key_cmd = f"wget -q -O - {_NIGHTLY_KEY_URL} | gpg --dearmor -o /usr/share/keyrings/odoo-archive-keyring.gpg"
        repo_url = _NIGHTLY_DEB_URL.format(version=self.odoo_version)
        repo_cmd = (
            f'echo "deb [signed-by=/usr/share/keyrings/odoo-archive-keyring.gpg] '
            f'{repo_url} ./" | tee /etc/apt/sources.list.d/odoo.list'
        )
        return self._ssh(f"sudo bash -c '{key_cmd}'") and self._ssh(f"sudo bash -c '{repo_cmd}'")

    def _install_odoo(self) -> bool:
        return self._ssh("sudo apt-get update -qq && sudo apt-get install -y odoo")

    def _resolve_admin_passwd(self) -> str:
        """
        Return the Odoo master password, generating one only on first deploy.

        Reused from the local secret store so it stays stable across deploys
        and remains recoverable (it used to default to the literal 'admin').
        """
        configured = self.cfg.get("admin_passwd")
        if configured:
            return configured

        stored = load(self.project_path, self.env).get(ADMIN_PASSWD)
        if stored:
            return stored

        generated = random_password()
        path = save(self.project_path, self.env, {ADMIN_PASSWD: generated})
        console.print(f"[green]✔[/green] Generated admin_passwd, stored in {path}")
        return generated

    def _resolve_db_password(self) -> str:
        """
        Return the PostgreSQL password of this environment's role.

        Generated once and kept in the local secret store, like the master
        password: the role is created with it and odoo.conf declares it, so a
        redeploy that invented a new one would leave Odoo unable to
        authenticate against its own database.
        """
        stored = load(self.project_path, self.env).get(PG_PASS)
        if stored:
            return stored

        generated = random_password()
        path = save(self.project_path, self.env, {PG_PASS: generated})
        console.print(f"[green]✔[/green] Generated database password, stored in {path}")
        return generated

    def _ensure_db_role(self) -> bool:
        """
        Create the role odoo.conf authenticates as, or realign its password.

        The Debian package creates a role named `odoo`, while the wizard's
        default user is `odoo_{env}`, which nothing else ever created: the
        deploy reported success and left an Odoo that could not connect.
        Realigning an existing role is what makes a redeploy work after the
        secret file was regenerated.
        """
        quoted = self._resolve_db_password().replace("'", "''")
        sql = (
            "DO $rkd$\n"
            "BEGIN\n"
            f"  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{self.db_user}') THEN\n"
            f"    RAISE NOTICE 'role % already existed: realigning its password', '{self.db_user}';\n"
            f"    ALTER ROLE \"{self.db_user}\" WITH LOGIN CREATEDB PASSWORD '{quoted}';\n"
            "  ELSE\n"
            f"    RAISE NOTICE 'creating role %', '{self.db_user}';\n"
            f"    CREATE ROLE \"{self.db_user}\" WITH LOGIN CREATEDB PASSWORD '{quoted}';\n"
            "  END IF;\n"
            "END\n"
            "$rkd$;\n"
        )
        # The password travels by stdin for the same reason the conf body does:
        # it never becomes an argv element, on either end of the connection.
        return self._ssh("sudo -u postgres psql -v ON_ERROR_STOP=1 -q -f -", input=sql)

    def _configure_odoo(self) -> bool:
        admin_passwd = self._resolve_admin_passwd()
        db_password = self._resolve_db_password()
        workers = WORKERS_BY_ENV.get(self.env, 2)
        log_level = LOG_LEVEL_BY_ENV.get(self.env, "info")
        mem = MEMORY_BY_ENV[self.env]
        use_enterprise = self.cfg.get("use_enterprise", False)
        # Nothing stands in front of a native deploy today (Traefik for this
        # path is #211), and with proxy_mode on Odoo trusts the X-Forwarded-*
        # headers of whoever reaches it directly.
        proxy_mode = bool(self.cfg.get("proxy_mode", False))

        addons_path = self.remote_addons
        if use_enterprise:
            addons_path += ",/opt/odoo/enterprise"

        conf = (
            "[options]\n"
            f"addons_path = {addons_path}\n"
            "data_dir = /var/lib/odoo\n"
            f"admin_passwd = {admin_passwd}\n"
            "db_host = localhost\n"
            "db_port = 5432\n"
            f"db_user = {self.db_user}\n"
            f"db_password = {db_password}\n"
            f"db_name = {self.db_name}\n"
            "db_maxconn = 64\n"
            f"log_level = {log_level}\n"
            "logfile = /var/log/odoo/odoo-server.log\n"
            f"workers = {workers}\n"
            "gevent_port = 8072\n"
            f"limit_memory_hard = {mem['hard']}\n"
            f"limit_memory_soft = {mem['soft']}\n"
            f"proxy_mode = {proxy_mode}\n"
            "list_db = False\n"
        )

        # The conf body (including admin_passwd) travels through stdin rather
        # than being embedded in the command string: it never touches argv,
        # so it cannot appear in `ps aux` and cannot be affected by shell
        # quoting rules regardless of what a value contains. It now also
        # carries the database password, so the file is restricted to the
        # service user instead of keeping tee's world-readable default.
        command = (
            f"sudo tee {self.odoo_conf_path} > /dev/null"
            f" && sudo chown odoo: {self.odoo_conf_path}"
            f" && sudo chmod 640 {self.odoo_conf_path}"
        )
        return self._ssh(command, input=conf)

    def _sync_addons(self) -> bool:
        addons_dir = self.project_path / "addons"
        if not addons_dir.exists():
            console.print("[yellow]  No local addons/ directory found — skipping sync[/yellow]")
            return True

        self._ssh(f"sudo mkdir -p {self.remote_addons} && sudo chown {self.user} {self.remote_addons}")
        cmd, env = build_rsync_cmd(self.auth, self.port, self.user, self.host, f"{addons_dir}/", f"{self.remote_addons}/")
        result = subprocess.run(cmd, env=env)
        return result.returncode == 0

    def _resolve_admin_login_password(self) -> str:
        """
        Return the password of Odoo's `admin` user, generating it once.

        Not the master password: this one is the login. Installing `base`
        leaves `admin` with the literal "admin" from
        `base/data/res_users_data.xml`, and the only code that replaces it is
        the web database manager, which `list_db = False` disables. Creating
        the database without setting this would publish an Odoo on port
        8069 whose credentials are in Odoo's own source.
        """
        stored = load(self.project_path, self.env).get(ADMIN_LOGIN_PASSWD)
        if stored:
            return stored

        generated = random_password()
        path = save(self.project_path, self.env, {ADMIN_LOGIN_PASSWD: generated})
        console.print(f"[green]✔[/green] Generated the password of the admin user, stored in {path}")
        return generated

    def _ensure_database(self) -> bool:
        """
        Create the database Odoo will serve, install `base`, and set the
        password of its `admin` user.

        With `list_db = False` there is no web database manager, so nothing
        else would ever create it: the deploy used to finish with Odoo running
        and no database to serve. Both halves are guarded separately, so a
        redeploy neither recreates the database nor reinstalls base, and a
        database left empty by an interrupted first attempt gets initialised
        on the next run instead of being skipped as already existing.

        The database is created with Odoo's own parameters rather than the
        cluster's defaults: `template1` is mutable and its encoding follows
        the cluster, so a VPS whose cluster came up `SQL_ASCII` would get a
        database that corrupts every non-ASCII translation `base` loads.

        The service is stopped before initialising because the Debian package
        starts it on install: its cron thread opens a registry on every
        database it can see, including this one while the schema is still
        being written. It stops only on the run that initialises, so a
        redeploy leaves a live instance alone until the restart in the next
        step.

        The script travels by stdin rather than as one `bash -c` argument:
        quoting an SQL literal inside a shell command inside an SSH command
        is three levels of escaping to get right, and getting it wrong here
        means an unrunnable deploy. The heredoc that carries the password
        keeps it out of argv on the far end, the way the conf body is kept
        out of it, and `< /dev/null` keeps the install from reading the
        script bash is being fed -- today the guard happens to protect it,
        because bash reads a whole `if` block before running any of it, but
        that is an accident of where the line sits.
        """
        # Every piece is built inside this one expression, including the
        # password: a secret routed through an intermediate variable is
        # exactly the shape tests/test_secrets_hardening.py watches for,
        # because that is how it would reach an argv by accident.
        script = (
            "set -e\n"
            f"if ! sudo -u postgres psql -tAc \"SELECT 1 FROM pg_database WHERE datname = '{self.db_name}'\""
            " | grep -q 1; then\n"
            f'  sudo -u postgres createdb -E UTF8 -T template0 --lc-collate=C -O "{self.db_user}" "{self.db_name}"\n'
            "fi\n"
            f'if [ -z "$(sudo -u postgres psql -d "{self.db_name}"'
            ' -tAc "SELECT to_regclass(\'public.ir_module_module\')")" ]; then\n'
            f"  sudo systemctl stop {self.service_name}\n"
            f'  sudo -u odoo odoo -c {self.odoo_conf_path} -d "{self.db_name}" -i base'
            " --workers=0 --max-cron-threads=0 --stop-after-init --logfile=/dev/stdout < /dev/null\n"
            f'  sudo -u odoo odoo shell -c {self.odoo_conf_path} -d "{self.db_name}"'
            " --logfile=/dev/stdout <<'RKD_PY'\n"
            f"env.ref('base.user_admin').write({{'password': '{self._resolve_admin_login_password()}'}})\n"
            "env.cr.commit()\n"
            "RKD_PY\n"
            "fi\n"
        )
        # Installing base on a fresh database is minutes, not seconds.
        return self._ssh("bash -s", timeout=1800, input=script)

    def _enable_service(self) -> bool:
        return self._ssh(f"sudo systemctl enable {self.service_name} && sudo systemctl restart {self.service_name}")

    def _verify_odoo_answers(self) -> bool:
        """
        Confirm the deploy delivered something that works.

        `systemctl restart` on a Type=simple unit returns as soon as the
        process forks, so it reports success for an Odoo that dies seconds
        later on a database it cannot authenticate against -- which is the
        exact failure #248 is about, and the one path a redeploy would
        otherwise never exercise, because both halves of the database step
        are skipped once the database exists. An answer on /web/login proves
        the conf, the role, the password and the database at once.
        """
        script = (
            f"for _ in $(seq 1 {_HTTP_RETRIES}); do\n"
            f"  if curl -sf -o /dev/null http://127.0.0.1:{_HTTP_PORT}/web/login; then exit 0; fi\n"
            "  sleep 2\n"
            "done\n"
            "echo 'Odoo did not answer; last lines of its log:' >&2\n"
            "sudo tail -n 30 /var/log/odoo/odoo-server.log >&2 || true\n"
            "exit 1\n"
        )
        return self._ssh("bash -s", timeout=180, input=script)

    # ─── helpers ─────────────────────────────────────────────────────────────

    def _ssh(self, command: str, timeout: int = 300, input: str | None = None) -> bool:
        cmd, env = build_ssh_cmd(self.auth, self.port, self.user, self.host, command)
        try:
            return subprocess.run(cmd, timeout=timeout, env=env, input=input, text=True).returncode == 0
        except subprocess.TimeoutExpired:
            # A step that installs a module can legitimately take half an
            # hour; what must not happen is the whole deploy ending in a
            # traceback that says nothing about the remote process, which is
            # still running.
            console.print(
                f"[red]Timed out after {timeout}s waiting for the VPS. The remote command may still be running.[/red]"
            )
            return False

    def _show_plan(self):
        console.print("\n[bold]DRY-RUN — Steps that would be executed:[/bold]")
        for i, (label, _fn) in enumerate(self._steps(), 1):
            console.print(f"  [cyan]{i}.[/cyan] {label}")
        console.print()

    def health_check(self) -> bool:
        domain = self.cfg.get("domain", "")
        if not domain:
            return False
        return self._ssh(f"curl -sf --max-time 10 https://{domain}/web/health -o /dev/null")
