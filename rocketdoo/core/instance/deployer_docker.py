"""
RocketDoo Instance - Docker deployer
Transfers files and deploys a full Odoo instance on a dockerized VPS.

Build flow (requires SSH agent for gitman private repos):
  Local → rsync files → VPS
  VPS   → DOCKER_BUILDKIT=1 docker compose build --ssh default
  VPS   → docker compose up -d
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from rocketdoo.core.traefik import NETWORK as TRAEFIK_NETWORK

from .config_manager import (
    LOG_LEVEL_BY_ENV,
    MEMORY_BY_ENV,
    PG_PROFILES,
    WORKERS_BY_ENV,
    invalid_identifiers,
)
from .secrets_store import ADMIN_LOGIN_PASSWD, ADMIN_PASSWD, PG_PASS, load, random_password, save
from .ssh_utils import build_rsync_cmd, build_ssh_cmd, resolve_auth

console = Console()

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates" / "instance"

# Odoo's HTTP port inside the container, and how many two-second rounds
# to give PostgreSQL and Odoo to answer.
_HTTP_PORT = 8069
_DB_READY_RETRIES = 60
_HTTP_RETRIES = 45

# Written once the admin user's password has been replaced, so a step
# that failed is retried instead of skipped for good.
_ADMIN_PASSWORD_MARK = "rkd.admin_password_set"


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

        vps = env_config["vps"]
        self.host = vps["host"]
        self.port = vps.get("port", 22)
        self.user = vps["user"]
        self.remote_path = env_config.get("remote_path", f"/opt/odoo-{env}")
        self.auth = resolve_auth(vps, env, project_path)

        self.db_user = env_config.get("db_user") or f"odoo_{env}"
        self.db_name = env_config.get("db_name") or self.db_user

        self.pg = PG_PROFILES[env_config.get("pg_profile", "medium")]
        self.project_name = self._detect_project_name()
        self.slug = self.project_name.replace("-", "_").replace(" ", "_")

    # ─── public API ──────────────────────────────────────────────────────────

    def deploy(self, dry_run: bool = False) -> bool:
        """Run full deployment. Returns True on success."""
        console.print(f"\n[bold cyan]Deploying {self.env.upper()} to {self.host}[/bold cyan]\n")

        invalid = invalid_identifiers(self.db_user, self.db_name)
        if invalid:
            console.print(f"[red]✗ Not a valid PostgreSQL identifier: {', '.join(invalid)}[/red]")
            console.print(
                "[dim]db_user and db_name may hold up to 63 letters, digits and underscores, "
                "and cannot start with a digit.[/dim]"
            )
            return False

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            console.print("[dim]Step 1/7 — Rendering configuration files...[/dim]")
            self._render_templates(tmp_path, allow_remote_lookup=not dry_run)
            console.print("[green]✓[/green] Configuration files rendered")

            if dry_run:
                console.print("\n[yellow]DRY-RUN: Skipping transfer and build.[/yellow]")
                self._show_generated_files(tmp_path)
                console.print(
                    f"[dim]A real run would also create the database [bold]{self.db_name}[/bold], "
                    "install base in it and set the admin password.[/dim]\n"
                )
                return True

            console.print("[dim]Step 2/7 — Preparing remote directory...[/dim]")
            if not self._prepare_remote():
                return False
            console.print("[green]✓[/green] Remote directory ready")

            console.print("[dim]Step 3/7 — Transferring files...[/dim]")
            if not self._transfer(tmp_path):
                return False
            console.print("[green]✓[/green] Files transferred")

            console.print("[dim]Step 4/7 — Building Docker image (this may take a while)...[/dim]")
            if not self._build():
                return False
            console.print("[green]✓[/green] Image built")

            console.print(f"[dim]Step 5/7 — Ensuring database {self.db_name}...[/dim]")
            if not self._ensure_database():
                return False
            console.print("[green]✓[/green] Database ready")

            console.print("[dim]Step 6/7 — Starting services...[/dim]")
            if not self._start():
                return False
            console.print("[green]✓[/green] Services started")

            console.print("[dim]Step 7/7 — Verifying Odoo answers...[/dim]")
            if not self._verify_odoo_answers():
                console.print("[red]✗ Odoo did not answer after the deploy[/red]")
                return False
            console.print("[green]✓[/green] Odoo answers")

        return True

    # ─── secrets ─────────────────────────────────────────────────────────────

    def _resolve_secrets(self, allow_remote_lookup: bool = True) -> dict[str, str]:
        """
        Resolve this environment's passwords, reusing any that already exist.

        PostgreSQL sets the role password when it initialises the cluster and
        ignores POSTGRES_PASSWORD_FILE afterwards, so a regenerated password on
        a second deploy would lock Odoo out of the database.

        Precedence: local secret store → password already on the VPS → new one.
        """
        stored = load(self.project_path, self.env)
        pg_pass = stored.get(PG_PASS)
        admin_passwd = self.cfg.get("admin_passwd") or stored.get(ADMIN_PASSWD)

        if not pg_pass and allow_remote_lookup:
            pg_pass = self._read_remote_pg_pass()
            if pg_pass:
                console.print("[dim]  Reusing odoo_pg_pass already deployed on the VPS[/dim]")

        generated = []
        if not pg_pass:
            pg_pass = random_password(32)
            generated.append("odoo_pg_pass")
        if not admin_passwd:
            admin_passwd = random_password()
            generated.append("admin_passwd")

        path = save(self.project_path, self.env, {PG_PASS: pg_pass, ADMIN_PASSWD: admin_passwd})
        if generated:
            console.print(f"[green]✔[/green] Generated {' and '.join(generated)}")
        console.print(f"[dim]  Secrets stored in {path}[/dim]")

        return {"pg_pass": pg_pass, "admin_passwd": admin_passwd}

    def _read_remote_pg_pass(self) -> str | None:
        """Return odoo_pg_pass from a previous deploy, or None if unavailable."""
        remote_file = f"{self.remote_path}/{self.env}/odoo_pg_pass"
        cmd, env = build_ssh_cmd(
            self.auth,
            self.port,
            self.user,
            self.host,
            f"cat {remote_file} 2>/dev/null",
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        except (subprocess.SubprocessError, OSError):
            return None
        return result.stdout.strip() or None

    # ─── template rendering ───────────────────────────────────────────────────

    def _render_templates(self, tmp: Path, allow_remote_lookup: bool = True):
        """Render all Jinja2 templates into a temporary directory."""
        jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        creds = self._resolve_secrets(allow_remote_lookup=allow_remote_lookup)

        use_enterprise = self.cfg.get("use_enterprise", False)
        use_gitman = self.cfg.get("use_gitman", False)
        gitman_config = self.cfg.get("gitman_config", "gitman.yml")
        admin_passwd = creds["admin_passwd"]

        ctx = {
            "environment": self.env,
            "project_name": self.project_name,
            "slug": f"{self.slug}_{self.env}",
            "odoo_tag": self.cfg.get("odoo_tag", self.cfg.get("odoo_version", "17.0")),
            "domain": self.cfg.get("domain", ""),
            "traefik_email": self.cfg.get("traefik_email", ""),
            "db_user": self.db_user,
            "db_name": self.db_name,
            # The conf used to say `db_password = False`, and the image's
            # entrypoint only forwards --db_password when the conf does not
            # define it: the secret the compose mounts never reached Odoo
            # (#250). Declared here rather than left out so the password
            # does not become a command-line argument on the VPS, visible
            # in `ps` to every user on the host (#142).
            "db_password": creds["pg_pass"],
            "db_version": str(self.cfg.get("db_version", "16")),
            "use_enterprise": use_enterprise,
            "use_gitman": use_gitman,
            "gitman_config_file": gitman_config,
            "pg": self.pg,
            # odoo.conf specific
            "admin_passwd": admin_passwd,
            "workers": WORKERS_BY_ENV.get(self.env, 2),
            "log_level": LOG_LEVEL_BY_ENV.get(self.env, "info"),
            "limit_memory_hard": MEMORY_BY_ENV[self.env]["hard"],
            "limit_memory_soft": MEMORY_BY_ENV[self.env]["soft"],
        }

        # Dockerfile.{env}
        dockerfile = jinja_env.get_template("Dockerfile.jinja").render(**ctx)
        (tmp / f"Dockerfile.{self.env}").write_text(dockerfile)

        # {env}/docker-compose.yml
        env_dir = tmp / self.env
        env_dir.mkdir()
        compose = jinja_env.get_template("docker-compose.jinja").render(**ctx)
        (env_dir / "docker-compose.yml").write_text(compose)

        # {env}/config/odoo.conf
        config_dir = env_dir / "config"
        config_dir.mkdir()
        odoo_conf = jinja_env.get_template("odoo.conf.jinja").render(**ctx)
        (config_dir / "odoo.conf").write_text(odoo_conf)

        # {env}/odoo_pg_pass — reused across deploys, never regenerated
        (env_dir / "odoo_pg_pass").write_text(creds["pg_pass"])

        # gitman config (if enabled and local file exists)
        if use_gitman and gitman_config:
            local_gitman = self.project_path / gitman_config
            if local_gitman.exists():
                shutil.copy(local_gitman, tmp / gitman_config)
            else:
                console.print(f"[yellow]⚠ Gitman config {gitman_config} not found locally — skipping[/yellow]")

    def _show_generated_files(self, tmp: Path):
        console.print("\n[bold]Generated files (dry-run):[/bold]")
        for f in sorted(tmp.rglob("*")):
            if f.is_file():
                console.print(f"  [cyan]{f.relative_to(tmp)}[/cyan]")

    # ─── SSH / transfer helpers ───────────────────────────────────────────────

    def _ssh(self, command: str, agent_forward: bool = False, timeout: int = 600, input: str | None = None) -> bool:
        cmd, env = build_ssh_cmd(self.auth, self.port, self.user, self.host, command, agent_forward=agent_forward)
        try:
            return subprocess.run(cmd, timeout=timeout, env=env, input=input, text=True).returncode == 0
        except subprocess.TimeoutExpired:
            # A build or a module install can legitimately take half an hour;
            # what must not happen is the deploy ending in a traceback that
            # says nothing about the remote process, which is still running.
            console.print(
                f"[red]Timed out after {timeout}s waiting for the VPS. The remote command may still be running.[/red]"
            )
            return False

    def _rsync(self, local: str, remote: str, extra_opts: list[str] | None = None) -> bool:
        cmd, env = build_rsync_cmd(self.auth, self.port, self.user, self.host, local, remote, extra_opts)
        return subprocess.run(cmd, env=env).returncode == 0

    # ─── deployment steps ────────────────────────────────────────────────────

    def _prepare_remote(self) -> bool:
        dirs = [
            self.remote_path,
            f"{self.remote_path}/extra-addons",
            f"{self.remote_path}/{self.env}/config",
        ]
        if not self._ssh(f"mkdir -p {' '.join(dirs)}"):
            return False

        # The compose declares this network as external, so Compose refuses to
        # create it: on a VPS that has never run Traefik, every step from the
        # database onwards fails with "network traefik-public declared as
        # external, but could not be found".
        return self._ssh(
            f"docker network inspect {TRAEFIK_NETWORK} >/dev/null 2>&1 || docker network create {TRAEFIK_NETWORK}"
        )

    def _transfer(self, tmp: Path) -> bool:
        # Dockerfile.{env}
        dockerfile = tmp / f"Dockerfile.{self.env}"
        if not self._rsync(str(dockerfile), f"{self.remote_path}/Dockerfile.{self.env}"):
            console.print(f"[red]✗ Failed to transfer Dockerfile.{self.env}[/red]")
            return False

        # {env}/ directory (compose + config + pg_pass)
        env_dir = tmp / self.env
        if not self._rsync(f"{env_dir}/", f"{self.remote_path}/{self.env}/"):
            console.print(f"[red]✗ Failed to transfer {self.env}/ config[/red]")
            return False

        # The PostgreSQL password arrives with rsync's default permissions,
        # readable by every user on the VPS. Docker reads it as root to mount
        # the secret, so restricting it costs nothing.
        self._ssh(f"chmod 600 {self.remote_path}/{self.env}/odoo_pg_pass")

        # gitman config (if present in tmp)
        gitman_config = self.cfg.get("gitman_config", "")
        if gitman_config:
            local_gitman = tmp / gitman_config
            if local_gitman.exists():
                if not self._rsync(str(local_gitman), f"{self.remote_path}/{gitman_config}"):
                    console.print("[yellow]⚠ Failed to transfer gitman config[/yellow]")

        # extra-addons (from project's addons/ dir)
        addons_dir = self.project_path / "addons"
        if addons_dir.exists():
            console.print("[dim]  Syncing extra-addons...[/dim]")
            if not self._rsync(f"{addons_dir}/", f"{self.remote_path}/extra-addons/"):
                console.print("[yellow]⚠ Failed to sync extra-addons[/yellow]")

        return True

    def _build(self) -> bool:
        build_cmd = f"cd {self.remote_path}/{self.env} && DOCKER_BUILDKIT=1 docker compose build --ssh default"
        ok = self._ssh(build_cmd, agent_forward=True, timeout=1800)
        if not ok:
            console.print("[red]✗ Docker build failed.[/red]")
            console.print("[dim]  Make sure your SSH agent is running and has the required keys loaded.[/dim]")
            console.print("[dim]  Check with: ssh-add -l[/dim]")
        return ok

    def _start(self) -> bool:
        start_cmd = f"cd {self.remote_path}/{self.env} && docker compose up -d"
        return self._ssh(start_cmd)

    def _resolve_admin_login_password(self) -> str:
        """
        Return the password of Odoo's `admin` user, generating it once.

        Not the master password: this one is the login. Installing `base`
        leaves `admin` with the literal "admin" from
        `base/data/res_users_data.xml`, and the only code that replaces it is
        the web database manager, which `list_db = False` disables. Creating
        the database without setting this would publish an Odoo behind the
        instance's own domain with credentials that are in Odoo's source.
        """
        stored = load(self.project_path, self.env).get(ADMIN_LOGIN_PASSWD)
        if stored:
            return stored

        generated = random_password()
        path = save(self.project_path, self.env, {ADMIN_LOGIN_PASSWD: generated})
        console.print(f"[green]✔[/green] Generated the password of the admin user, stored in {path}")
        # It is applied by the database step, which only touches a database
        # whose admin password rkd never set. Saying so here is the
        # difference between a stored credential and a stored guess: the
        # secret file is gitignored, so a deploy from another clone lands
        # exactly in that case.
        console.print("[dim]  Applied only to a database rkd initialises; an existing one keeps its own.[/dim]")
        return generated

    def _ensure_database(self) -> bool:
        """
        Create the database Odoo will serve, install `base`, and set the
        password of its `admin` user.

        The compose creates the *role* by construction (POSTGRES_USER), but
        nothing ever created the database, and `list_db = False` means there
        is no web database manager to make one: the deploy finished with Odoo
        running and nothing to serve (#250). Both halves are guarded
        separately, so a redeploy neither recreates the database nor
        reinstalls base, and a database left empty by an interrupted first
        attempt gets initialised on the next run.

        The database is created with Odoo's own parameters rather than the
        cluster's defaults, for the same reason as the native path: the image
        initialises its cluster from the host's locale, and `template1` is
        mutable.

        `exec -T` and `run -T` keep Docker from allocating a TTY, which would
        wrap psql's output in carriage returns and make the emptiness checks
        below answer wrongly.
        """
        return self._ssh(
            "bash -s",
            timeout=1800,
            input=(
                # The braces are load-bearing: bash reads a compound command
                # whole before running any of it, and `docker compose` reads
                # the stdin it inherits -- which here is the script itself.
                # Without them `up -d db` swallows everything below it and the
                # step reports success having done nothing. Verified.
                "{\n"
                "set -e\n"
                f'cd "{self.remote_path}/{self.env}"\n'
                "docker compose up -d db\n"
                f"for _ in $(seq 1 {_DB_READY_RETRIES}); do\n"
                f'  if docker compose exec -T db pg_isready -U "{self.db_user}" -q; then break; fi\n'
                "  sleep 2\n"
                "done\n"
                # -d postgres, the database the compose asks the image to
                # create: without it psql defaults to one named after the
                # role, which is the database being looked for.
                f"if ! docker compose exec -T db psql -U {self.db_user} -d postgres"
                f" -tAc \"SELECT 1 FROM pg_database WHERE datname = '{self.db_name}'\" | grep -q 1; then\n"
                f"  docker compose exec -T db createdb -U {self.db_user}"
                f' -E UTF8 -T template0 --lc-collate=C -O "{self.db_user}" "{self.db_name}"\n'
                "fi\n"
                # `base` installed, not merely the table existing: Odoo
                # commits ir_module_module seconds into the install and
                # finishes minutes later, so an interrupted first attempt
                # would otherwise look done forever. psql's own error on a
                # database without the table is stderr, and an empty answer
                # here means "install it" either way.
                f'if [ -z "$(docker compose exec -T db psql -U {self.db_user} -d "{self.db_name}" -tAc'
                " \"SELECT 1 FROM ir_module_module WHERE name = 'base' AND state = 'installed'\" 2>/dev/null)\" ]; then\n"
                f'  docker compose run --rm -T -l traefik.enable=false web odoo -d "{self.db_name}" -i base'
                " --workers=0 --max-cron-threads=0 --stop-after-init --logfile= < /dev/null\n"
                "fi\n"
                # Its own guard, and a mark of its own: an install that
                # succeeded while this step failed must be retried, or the
                # admin user keeps the password `base` ships with.
                f'if [ -z "$(docker compose exec -T db psql -U {self.db_user} -d "{self.db_name}" -tAc'
                f' "SELECT 1 FROM ir_config_parameter WHERE key = \'{_ADMIN_PASSWORD_MARK}\'" 2>/dev/null)" ]; then\n'
                f'  docker compose run --rm -T -l traefik.enable=false web odoo shell -d "{self.db_name}"'
                " --logfile= <<'RKD_PY'\n"
                f"env.ref('base.user_admin').write({{'password': '{self._resolve_admin_login_password()}'}})\n"
                f"env['ir.config_parameter'].sudo().set_param('{_ADMIN_PASSWORD_MARK}', '1')\n"
                "env.cr.commit()\n"
                "RKD_PY\n"
                "fi\n"
                "}\n"
            ),
        )

    def _verify_odoo_answers(self) -> bool:
        """
        Confirm the deploy delivered something that works.

        `docker compose up -d` returns once the containers are created, which
        it does just as happily for an Odoo that exits a second later against
        a database it cannot authenticate against -- the failure #250 is
        about. Asked from inside the container, the check does not depend on
        Traefik, DNS or a certificate being ready.
        """
        return self._ssh(
            "bash -s",
            timeout=300,
            input=(
                # Braced for the same reason as the step above, and here it
                # decides the verdict: `docker compose logs` would otherwise
                # eat the `exit 1` below it and the deploy would call a
                # silent Odoo a success.
                "{\n"
                f'cd "{self.remote_path}/{self.env}"\n'
                f"for _ in $(seq 1 {_HTTP_RETRIES}); do\n"
                f"  if docker compose exec -T web curl -sf -o /dev/null http://127.0.0.1:{_HTTP_PORT}/web/login; then\n"
                "    exit 0\n"
                "  fi\n"
                "  sleep 2\n"
                "done\n"
                "echo 'Odoo did not answer; last lines of the web container log:' >&2\n"
                "docker compose logs --tail 30 web >&2 || true\n"
                "exit 1\n"
                "}\n"
            ),
        )

    # ─── utils ───────────────────────────────────────────────────────────────

    def _detect_project_name(self) -> str:
        try:
            import yaml as _yaml

            for name in ("docker-compose.yaml", "docker-compose.yml"):
                p = self.project_path / name
                if p.exists():
                    data = _yaml.safe_load(p.read_text())
                    if data and data.get("name"):
                        return data["name"]
        except Exception:
            pass
        return self.project_path.name

    def health_check(self) -> bool:
        """Verify Odoo is responding on the VPS."""
        domain = self.cfg.get("domain", "")
        if not domain:
            return False
        cmd = f"curl -sf --max-time 10 https://{domain}/web/health -o /dev/null"
        return self._ssh(cmd)
