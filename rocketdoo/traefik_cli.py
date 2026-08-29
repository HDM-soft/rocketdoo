"""
RocketDoo Traefik - Reverse proxy integration
rkd traefik on/off/status/guide
"""

import subprocess
from pathlib import Path

import click
import questionary
import yaml
from questionary import Style
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

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

_NETWORK = "traefik-public"
_OVERRIDE_FILE = "docker-compose.override.yml"
_CONFIG_FILE = ".rkd/traefik.yaml"
_COMPOSE_NAMES = ("docker-compose.yaml", "docker-compose.yml")
_DEFAULT_TRAEFIK_DIR = "./traefik"


# ─── helpers ─────────────────────────────────────────────────────────────────


def _is_wsl2() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


def _compose_path() -> Path | None:
    for name in _COMPOSE_NAMES:
        p = Path.cwd() / name
        if p.exists():
            return p
    return None


def _project_name() -> str:
    for name in _COMPOSE_NAMES:
        p = Path.cwd() / name
        if p.exists():
            try:
                data = yaml.safe_load(p.read_text())
                if data and data.get("name"):
                    return data["name"]
            except Exception:
                pass
    return Path.cwd().name


def _load_config() -> dict:
    p = Path.cwd() / _CONFIG_FILE
    if p.exists():
        try:
            return yaml.safe_load(p.read_text()) or {}
        except Exception:
            pass
    return {}


def _save_config(config: dict):
    p = Path.cwd() / _CONFIG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _network_exists() -> bool:
    r = subprocess.run(["docker", "network", "inspect", _NETWORK], capture_output=True)
    return r.returncode == 0


def _create_network():
    subprocess.run(["docker", "network", "create", _NETWORK], capture_output=True)


def _override_exists() -> bool:
    return (Path.cwd() / _OVERRIDE_FILE).exists()


def _traefik_running() -> bool:
    r = subprocess.run(["docker", "ps", "-q", "--filter", "name=traefik"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _run_compose(*args: str) -> int:
    return subprocess.run(["docker", "compose", *args], cwd=Path.cwd()).returncode


# ─── content generators ───────────────────────────────────────────────────────


def _gen_traefik_compose(mode: str) -> str:
    https_port = '\n      - "443:443"' if mode == "production" else ""
    acme_vol = "\n      - ./certs/acme.json:/certs/acme.json" if mode == "production" else ""
    return f"""\
name: traefik

services:
  traefik:
    image: traefik:v2.11
    container_name: traefik
    restart: always
    ports:
      - "80:80"{https_port}
    volumes:
      - ./traefik.yml:/etc/traefik/traefik.yml
      - /var/run/docker.sock:/var/run/docker.sock:ro{acme_vol}
    networks:
      - traefik-public

networks:
  traefik-public:
    external: true
"""


def _gen_traefik_yml(mode: str, email: str = "") -> str:
    if mode == "local":
        return """\
entryPoints:
  web:
    address: ":80"

providers:
  docker:
    exposedByDefault: false
    network: traefik-public

api:
  dashboard: false
"""
    return f"""\
entryPoints:
  web:
    address: ":80"
  websecure:
    address: ":443"

certificatesResolvers:
  letsencrypt:
    acme:
      email: {email}
      storage: /certs/acme.json
      httpChallenge:
        entryPoint: web

providers:
  docker:
    exposedByDefault: false
    network: traefik-public

api:
  dashboard: false
"""


def _gen_override(project: str, domain: str, mode: str) -> str:
    slug = project.replace("-", "_").replace(" ", "_")

    if mode == "local":
        labels = (
            f'      - "traefik.enable=true"\n'
            f'      - "traefik.docker.network=traefik-public"\n'
            f'      - "traefik.http.routers.{slug}.rule=Host(`{domain}`)"\n'
            f'      - "traefik.http.routers.{slug}.entrypoints=web"\n'
            f'      - "traefik.http.services.{slug}-svc.loadbalancer.server.port=8069"\n'
            f'      - "traefik.http.routers.{slug}-lp.rule=Host(`{domain}`) && (PathPrefix(`/longpolling`) || PathPrefix(`/websocket`))"\n'
            f'      - "traefik.http.routers.{slug}-lp.entrypoints=web"\n'
            f'      - "traefik.http.services.{slug}-lp-svc.loadbalancer.server.port=8072"'
        )
    else:
        labels = (
            f'      - "traefik.enable=true"\n'
            f'      - "traefik.docker.network=traefik-public"\n'
            f'      - "traefik.http.routers.{slug}-http.rule=Host(`{domain}`)"\n'
            f'      - "traefik.http.routers.{slug}-http.entrypoints=web"\n'
            f'      - "traefik.http.routers.{slug}-http.middlewares=redirect-https"\n'
            f'      - "traefik.http.middlewares.redirect-https.redirectscheme.scheme=https"\n'
            f'      - "traefik.http.routers.{slug}.rule=Host(`{domain}`)"\n'
            f'      - "traefik.http.routers.{slug}.entrypoints=websecure"\n'
            f'      - "traefik.http.routers.{slug}.tls.certresolver=letsencrypt"\n'
            f'      - "traefik.http.routers.{slug}.service={slug}-svc"\n'
            f'      - "traefik.http.services.{slug}-svc.loadbalancer.server.port=8069"\n'
            f'      - "traefik.http.routers.{slug}-lp.rule=Host(`{domain}`) && (PathPrefix(`/longpolling`) || PathPrefix(`/websocket`))"\n'
            f'      - "traefik.http.routers.{slug}-lp.entrypoints=websecure"\n'
            f'      - "traefik.http.routers.{slug}-lp.tls.certresolver=letsencrypt"\n'
            f'      - "traefik.http.routers.{slug}-lp.service={slug}-lp-svc"\n'
            f'      - "traefik.http.services.{slug}-lp-svc.loadbalancer.server.port=8072"'
        )

    return f"""\
# Generated by rkd traefik on — disable with: rkd traefik off
services:
  web:
    networks:
      - traefik-public
      - traefik-internal
    labels:
{labels}

  db:
    networks:
      - traefik-internal

networks:
  traefik-public:
    external: true
  traefik-internal:
    driver: bridge
"""


# ─── command group ────────────────────────────────────────────────────────────


def _disable_traefik(restart: bool = True) -> bool:
    """Remove the Traefik override and bring the project back on direct ports.

    Shared by `rkd traefik off` and the GUI endpoint. Returns False when the
    project was not connected to Traefik in the first place.
    """
    override = Path.cwd() / _OVERRIDE_FILE
    if not override.exists():
        return False

    override.unlink()
    if restart:
        _run_compose("up", "-d")
    return True


@click.group(name="traefik")
def traefik():
    """Manage Traefik reverse proxy integration.

    \b
    Examples:

    \b
    # Enable Traefik for this project (wizard: domain, mode)
    rkd traefik on

    \b
    # Disable Traefik for this project
    rkd traefik off

    \b
    # Show status
    rkd traefik status

    \b
    # Show guide to configure local domains (/etc/hosts / WSL2)
    rkd traefik guide
    """
    pass


@traefik.command(name="on")
@click.option("--domain", "-d", default=None, help="Domain to expose this project on")
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["local", "production"]),
    default=None,
    help="local (HTTP) or production (HTTPS + Let's Encrypt)",
)
@click.option(
    "--traefik-dir", default=_DEFAULT_TRAEFIK_DIR, show_default=True, help="Directory for the shared Traefik service"
)
def traefik_on(domain, mode, traefik_dir):
    """Enable Traefik reverse proxy for this Odoo project.

    \b
    Generates:
      traefik/               Shared Traefik service (docker-compose + config)
      docker-compose.override.yml  Traefik labels + networks for this project
      .rkd/traefik.yaml      Saved configuration

    \b
    After enabling, add the domain to /etc/hosts:
      rkd traefik guide
    """
    if not _compose_path():
        console.print("\n[red]No docker-compose.yaml found. Run rkd init first.[/red]\n")
        return

    if _override_exists():
        console.print(
            "\n[yellow]Traefik is already enabled for this project.[/yellow]\n"
            "[dim]Run [cyan bold]rkd traefik off[/cyan bold] first to reconfigure.[/dim]\n"
        )
        return

    project = _project_name()
    existing = _load_config()

    console.print()
    console.print(
        Panel(
            "[bold cyan]Traefik Setup[/bold cyan]\n\n[dim]Reverse proxy with domain routing for Odoo[/dim]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    console.print()

    # ── Mode ──
    if not mode:
        mode = (
            existing.get("mode")
            or questionary.select(
                "Deployment mode:",
                choices=[
                    questionary.Choice("Local dev  — HTTP, custom domain via /etc/hosts", value="local"),
                    questionary.Choice("Production — HTTPS with Let's Encrypt certificate", value="production"),
                ],
                style=_custom_style,
            ).ask()
        )
        if not mode:
            return

    # ── Domain ──
    if not domain:
        default_domain = existing.get("domain") or f"{project}.local"
        domain = Prompt.ask("Domain", default=default_domain)

    # ── Email (prod only) ──
    email = ""
    if mode == "production":
        email = existing.get("email") or Prompt.ask("Email for Let's Encrypt notifications")

    traefik_path = Path(traefik_dir).resolve()
    console.print()

    # ── Generate Traefik service files ──
    traefik_path.mkdir(parents=True, exist_ok=True)

    compose_file = traefik_path / "docker-compose.yml"
    if not compose_file.exists():
        compose_file.write_text(_gen_traefik_compose(mode))
        console.print(f"[green]✓[/green] {compose_file.relative_to(Path.cwd())} generated")
    else:
        console.print(f"[dim]  {compose_file.relative_to(Path.cwd())} already exists, skipping[/dim]")

    traefik_yml = traefik_path / "traefik.yml"
    if not traefik_yml.exists():
        traefik_yml.write_text(_gen_traefik_yml(mode, email))
        console.print(f"[green]✓[/green] {traefik_yml.relative_to(Path.cwd())} generated")
    else:
        console.print(f"[dim]  {traefik_yml.relative_to(Path.cwd())} already exists, skipping[/dim]")

    if mode == "production":
        certs_dir = traefik_path / "certs"
        certs_dir.mkdir(exist_ok=True)
        acme = certs_dir / "acme.json"
        if not acme.exists():
            acme.touch()
            acme.chmod(0o600)
            console.print(f"[green]✓[/green] {acme.relative_to(Path.cwd())} created (chmod 600)")

    # ── Docker network ──
    if not _network_exists():
        console.print(f'[dim]Creating docker network "{_NETWORK}"...[/dim]')
        _create_network()
        console.print(f'[green]✓[/green] Network "{_NETWORK}" created')
    else:
        console.print(f'[dim]  Network "{_NETWORK}" already exists[/dim]')

    # ── Project override ──
    (Path.cwd() / _OVERRIDE_FILE).write_text(_gen_override(project, domain, mode))
    console.print(f"[green]✓[/green] {_OVERRIDE_FILE} generated")

    # ── Save config ──
    _save_config(
        {
            "mode": mode,
            "domain": domain,
            "email": email,
            "traefik_dir": str(traefik_path),
        }
    )
    console.print(f"[green]✓[/green] Config saved to {_CONFIG_FILE}")

    # ── Start Traefik ──
    console.print("\n[dim]Starting Traefik...[/dim]")
    rc = subprocess.run(["docker", "compose", "up", "-d"], cwd=traefik_path).returncode
    if rc != 0:
        console.print("[yellow]⚠ Could not start Traefik (is Docker running?)[/yellow]")
    else:
        console.print("[green]✓[/green] Traefik started")

    # ── Restart Odoo with new override ──
    console.print("[dim]Restarting Odoo with Traefik integration...[/dim]")
    _run_compose("up", "-d")
    console.print("[green]✓[/green] Project restarted")

    # ── Summary ──
    scheme = "https" if mode == "production" else "http"
    if mode == "local":
        hint = (
            f"[dim]Add to /etc/hosts →[/dim] [cyan]127.0.0.1  {domain}[/cyan]\n"
            "[dim]Full guide:[/dim] [cyan bold]rkd traefik guide[/cyan bold]"
        )
    else:
        hint = "[dim]Let's Encrypt will provision the certificate on first request.[/dim]"

    console.print()
    console.print(
        Panel(
            f"[bold green]Traefik enabled[/bold green]\n\n"
            f"[dim]Domain  :[/dim] [cyan underline]{scheme}://{domain}[/cyan underline]\n"
            f"[dim]Mode    :[/dim] {mode}\n"
            f"[dim]Project :[/dim] {project}\n\n"
            f"{hint}",
            border_style="green",
            box=box.ROUNDED,
        )
    )
    console.print()


@traefik.command(name="off")
def traefik_off():
    """Disconnect this project from Traefik (restores direct port access)."""
    override = Path.cwd() / _OVERRIDE_FILE
    if not override.exists():
        console.print("\n[dim]Traefik is not enabled for this project.[/dim]\n")
        return

    console.print()
    console.print(Panel("[bold cyan]Disabling Traefik[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    console.print()

    console.print("[dim]Restarting project without Traefik...[/dim]")
    _disable_traefik()
    console.print(f"[green]\u2713[/green] Removed {_OVERRIDE_FILE}")
    console.print("[green]\u2713[/green] Project restarted")

    console.print("\n[dim]Traefik disabled. Project is accessible via direct ports again.[/dim]\n")


@traefik.command(name="status")
def traefik_status():
    """Show Traefik integration status for the current project."""
    config = _load_config()
    override = _override_exists()
    running = _traefik_running()
    network = _network_exists()

    mode = config.get("mode", "—")
    domain = config.get("domain", "—")
    scheme = "https" if mode == "production" else "http"

    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_column("Key", style="cyan bold", width=26)
    table.add_column("Value")

    table.add_row(
        "Project override", "[green]Active[/green]" if override else "[yellow]Not configured — run rkd traefik on[/yellow]"
    )
    table.add_row(f'Network "{_NETWORK}"', "[green]Exists[/green]" if network else "[red]Missing[/red]")
    table.add_row("Traefik container", "[green]Running[/green]" if running else "[dim]Stopped[/dim]")

    if config:
        table.add_row("Mode", mode)
        table.add_row("URL", f"[cyan underline]{scheme}://{domain}[/cyan underline]" if domain != "—" else "—")
        if config.get("traefik_dir"):
            table.add_row("Traefik dir", config["traefik_dir"])

    console.print()
    console.print(
        Panel(table, title="[bold cyan]Traefik Status[/bold cyan]", border_style="cyan", box=box.ROUNDED, padding=(1, 2))
    )
    console.print()


@traefik.command(name="guide")
def traefik_guide():
    """Show step-by-step guide to configure local domains.

    \b
    Covers:
      - Linux /etc/hosts
      - WSL2: both WSL2 and Windows hosts files
    """
    config = _load_config()
    domain = config.get("domain", "myproject.local")
    is_wsl = _is_wsl2()

    console.print()
    console.print(
        Panel(
            "[bold cyan]Local Domain Setup Guide[/bold cyan]\n\n"
            f"[dim]Domain:[/dim] [cyan bold]{domain}[/cyan bold]"
            + ("\n[yellow]WSL2 environment detected[/yellow]" if is_wsl else ""),
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    # ── Linux / WSL2 /etc/hosts ──
    console.print()
    console.print("[bold]Step 1[/bold] — Add to [cyan]/etc/hosts[/cyan] (Linux / WSL2 terminal)\n")
    console.print(f'  [dim]$[/dim] [green]echo "127.0.0.1  {domain}" | sudo tee -a /etc/hosts[/green]\n')
    console.print(f"  [dim]Verify:[/dim] [green]ping -c 1 {domain}[/green]\n")

    # ── WSL2: Windows hosts file ──
    if is_wsl:
        win_hosts = r"C:\Windows\System32\drivers\etc\hosts"
        console.print("[bold]Step 2[/bold] — Add to [cyan]Windows hosts file[/cyan] (required for browser access in WSL2)\n")
        console.print("  Open [bold]PowerShell as Administrator[/bold] and run:\n")
        console.print(f"  [green]Add-Content -Path '{win_hosts}' -Value '127.0.0.1  {domain}'[/green]\n")
        console.print("  [dim]Or open the file manually:[/dim]")
        console.print(f"  [green]notepad.exe {win_hosts}[/green]")
        console.print(f"  [dim]And add the line:[/dim] [cyan]127.0.0.1  {domain}[/cyan]\n")
        console.print("[bold]Step 3[/bold] — Verify from Windows\n")
        console.print(f"  [dim]Open browser:[/dim] [cyan underline]http://{domain}[/cyan underline]")
        console.print(f"  [dim]Or PowerShell:[/dim] [green]Test-Connection {domain}[/green]\n")
    else:
        console.print("[bold]Step 2[/bold] — Verify\n")
        console.print(f"  [dim]Open browser:[/dim] [cyan underline]http://{domain}[/cyan underline]\n")

    # ── Notes ──
    console.print("[bold]Notes[/bold]\n")
    console.print("  • [dim]/etc/hosts changes take effect immediately (no restart needed)[/dim]")
    console.print("  • [dim]Windows hosts file may require a browser restart to take effect[/dim]")
    console.print("  • [dim]Traefik must be running:[/dim] [cyan]rkd traefik status[/cyan]")
    console.print("  • [dim]To undo: remove the line with[/dim] [cyan]sudo nano /etc/hosts[/cyan]")
    console.print()
