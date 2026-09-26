"""
RocketDoo Traefik - Reverse proxy integration
rkd traefik on/off/status/guide
"""

from pathlib import Path

import click
import questionary
from questionary import Style
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from rocketdoo.cli_output import console_progress
from rocketdoo.core import traefik as core_traefik
from rocketdoo.core.compose import compose_path

console = Console()


def _is_wsl2() -> bool:
    """Only `traefik guide` needs this: which hosts file the user must edit."""
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


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

_DEFAULT_TRAEFIK_DIR = "./traefik"


# ─── command group ────────────────────────────────────────────────────────────


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
@click.option("--email", default=None, help="Email for Let's Encrypt notifications (production mode)")
def traefik_on(domain, mode, traefik_dir, email):
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
    root = Path.cwd()

    if not compose_path(root):
        console.print("\n[red]No docker-compose.yaml found. Run rkd init first.[/red]\n")
        return

    if core_traefik.override_exists(root):
        console.print(
            "\n[yellow]Traefik is already enabled for this project.[/yellow]\n"
            "[dim]Run [cyan bold]rkd traefik off[/cyan bold] first to reconfigure.[/dim]\n"
        )
        return

    project = core_traefik.project_name(root)
    existing = core_traefik.load_config(root)

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
    if mode == "production" and not email:
        email = existing.get("email") or Prompt.ask("Email for Let's Encrypt notifications")

    console.print()

    try:
        report = core_traefik.enable(
            root,
            mode=mode,
            domain=domain,
            email=email or "",
            traefik_dir=traefik_dir,
            on_progress=console_progress(console),
        )
    except core_traefik.TraefikError as exc:
        console.print(f"\n[red]{exc}[/red]")
        if exc.hint:
            console.print(f"[dim]{exc.hint}[/dim]")
        console.print()
        return

    # ── Summary ──
    scheme = "https" if report["mode"] == "production" else "http"
    if report["mode"] == "local":
        hint = (
            f"[dim]Add to /etc/hosts →[/dim] [cyan]127.0.0.1  {report['domain']}[/cyan]\n"
            "[dim]Full guide:[/dim] [cyan bold]rkd traefik guide[/cyan bold]"
        )
    else:
        hint = "[dim]Let's Encrypt will provision the certificate on first request.[/dim]"

    console.print()
    console.print(
        Panel(
            f"[bold green]Traefik enabled[/bold green]\n\n"
            f"[dim]Domain  :[/dim] [cyan underline]{scheme}://{report['domain']}[/cyan underline]\n"
            f"[dim]Mode    :[/dim] {report['mode']}\n"
            f"[dim]Project :[/dim] {report['project']}\n\n"
            f"{hint}",
            border_style="green",
            box=box.ROUNDED,
        )
    )
    console.print()


@traefik.command(name="off")
def traefik_off():
    """Disconnect this project from Traefik (restores direct port access)."""
    root = Path.cwd()

    if not core_traefik.override_exists(root):
        console.print("\n[dim]Traefik is not enabled for this project.[/dim]\n")
        return

    console.print()
    console.print(Panel("[bold cyan]Disabling Traefik[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    console.print()

    core_traefik.disable(root, on_progress=console_progress(console))

    console.print("\n[dim]Traefik disabled. Project is accessible via direct ports again.[/dim]\n")


@traefik.command(name="status")
def traefik_status():
    """Show Traefik integration status for the current project."""
    report = core_traefik.status(Path.cwd())

    mode = report["mode"] or "—"
    domain = report["domain"] or "—"
    scheme = "https" if mode == "production" else "http"

    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_column("Key", style="cyan bold", width=26)
    table.add_column("Value")

    table.add_row(
        "Project override",
        "[green]Active[/green]" if report["override_exists"] else "[yellow]Not configured — run rkd traefik on[/yellow]",
    )
    table.add_row(
        f'Network "{core_traefik.NETWORK}"', "[green]Exists[/green]" if report["network_exists"] else "[red]Missing[/red]"
    )
    table.add_row("Traefik container", "[green]Running[/green]" if report["traefik_running"] else "[dim]Stopped[/dim]")

    if report["configured"]:
        table.add_row("Mode", mode)
        table.add_row("URL", f"[cyan underline]{scheme}://{domain}[/cyan underline]" if domain != "—" else "—")
        if report["traefik_dir"]:
            table.add_row("Traefik dir", report["traefik_dir"])

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
    config = core_traefik.load_config()
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
