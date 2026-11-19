"""
RocketDoo Instance - Full Odoo instance deployment
rkd instance init / deploy / status
"""

from pathlib import Path

import click
import yaml
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

console = Console()


@click.group(name="instance")
def instance():
    """Deploy complete Odoo instances to VPS (stage / prod).

    \b
    Supports two deployment types:
      docker  — Transfers Dockerfile + compose, builds on the VPS
      native  — Installs Odoo via nightly apt packages

    \b
    Examples:

    \b
    # Configure environments interactively
    rkd instance init

    \b
    # Deploy stage environment
    rkd instance deploy --env stage

    \b
    # Deploy prod (dry-run first)
    rkd instance deploy --env prod --dry-run
    rkd instance deploy --env prod

    \b
    # Check status of deployed instances
    rkd instance status
    """
    pass


@instance.command(name="init")
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Ignore the existing configuration and start over: environments not configured again are dropped",
)
def instance_init(force):
    """Interactive wizard to configure stage / prod deployment targets.

    Saves configuration to .rkd/instance.yaml. Re-running it keeps the
    environments already configured and the deployment type they use; only
    the ones you choose are added or reconfigured.
    """
    from rocketdoo.core.instance.config_manager import InstanceConfigManager
    from rocketdoo.core.project_info import project_exists

    project_path = Path.cwd()

    # Both wizards write credentials under .rkd/. Run one directory too deep
    # and they land somewhere the generated .gitignore does not cover and
    # `rkd pack` used to ship (#246): refuse rather than scatter them.
    if not project_exists(project_path):
        console.print("\n[red]No Rocketdoo project in this directory.[/red]")
        console.print(f"[dim]{project_path} has no Dockerfile and docker-compose.yaml next to each other.[/dim]")
        console.print("[dim]Run this from the project root: what it writes under .rkd/ are credentials,[/dim]")
        console.print("[dim]and a copy below the root is neither gitignored nor kept out of `rkd pack`.[/dim]\n")
        # Non-zero, so `rkd deploy init && rkd deploy run` does not carry on
        # as if a configuration had been written.
        raise SystemExit(1)

    mgr = InstanceConfigManager(project_path)

    before = {}
    if mgr.exists():
        try:
            before = mgr.load()["environments"]
        except (ValueError, yaml.YAMLError) as exc:
            if not force:
                console.print(f"\n[red]Cannot read {mgr.config_path}:[/red] {exc}")
                console.print("[dim]Fix the file, or run rkd instance init --force to start over.[/dim]\n")
                raise SystemExit(1)

    console.print()
    console.print(
        Panel(
            "[bold cyan]Instance Deployment Wizard[/bold cyan]\n\n"
            "[dim]Configure stage / production environments for VPS deployment[/dim]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    console.print()

    try:
        config = mgr.interactive_setup(fresh=force)
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]Cancelled.[/yellow]\n")
        return
    except ValueError as exc:
        console.print(f"\n[red]The answers do not make a valid configuration; nothing was written:[/red] {exc}\n")
        raise SystemExit(1)

    after = config.get("environments", {})
    lines = []
    for env, cfg in after.items():
        state = "added" if env not in before else "kept" if before[env] == cfg else "updated"
        lines.append(f"  [cyan]{env}[/cyan]  {state}")
    lines += [f"  [cyan]{env}[/cyan]  removed" for env in before if env not in after]
    changed = any(not line.endswith("kept") for line in lines)
    title = "Configuration saved" if changed else "Configuration unchanged"
    console.print()
    console.print(
        Panel(
            f"[bold green]{title}[/bold green]\n\n"
            f"[dim]File:[/dim] [cyan]{mgr.config_path}[/cyan]\n"
            + "\n".join(lines)
            + "\n\n[dim]Deploy with:[/dim] [cyan bold]rkd instance deploy --env stage[/cyan bold]",
            border_style="green",
            box=box.ROUNDED,
        )
    )
    console.print()


@instance.command(name="deploy")
@click.option("--env", "-e", required=True, type=click.Choice(["stage", "prod"]), help="Environment to deploy")
@click.option("--dry-run", is_flag=True, help="Render and show generated files without deploying")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def instance_deploy(env, dry_run, yes):
    """Deploy a full Odoo instance to a VPS.

    \b
    For dockerized deployments the build uses --ssh default (BuildKit).
    Make sure your SSH agent is running and has the required keys:

    \b
      eval $(ssh-agent)
      ssh-add ~/.ssh/id_rsa
    """
    from rocketdoo.core.instance.config_manager import InstanceConfigManager
    from rocketdoo.core.instance.deployer_docker import DockerInstanceDeployer
    from rocketdoo.core.instance.deployer_native import NativeInstanceDeployer

    project_path = Path.cwd()
    mgr = InstanceConfigManager(project_path)

    if not mgr.exists():
        console.print("\n[red]No instance.yaml found. Run rkd instance init first.[/red]\n")
        return

    try:
        env_config = mgr.get_env(env)
    except Exception as e:
        console.print(f"\n[red]Error loading config: {e}[/red]\n")
        return

    if not env_config:
        console.print(f'\n[red]Environment "{env}" not found in instance.yaml.[/red]')
        console.print("[dim]Run rkd instance init to add it.[/dim]\n")
        return

    dep_type = env_config.get("type", "docker")
    host = env_config.get("vps", {}).get("host", "?")
    domain = env_config.get("domain", "?")

    # ── Banner ──
    console.print()
    if dry_run:
        console.print(
            Panel("[bold yellow]DRY-RUN MODE — no changes will be made[/bold yellow]", border_style="yellow", box=box.ROUNDED)
        )
        console.print()

    console.print(
        Panel(
            f"[bold cyan]Deploying {env.upper()}[/bold cyan]\n\n"
            f"[dim]Type   :[/dim] {dep_type}\n"
            f"[dim]Host   :[/dim] {host}\n"
            f"[dim]Domain :[/dim] {domain}",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    # ── Confirmation ──
    if not yes and not dry_run:
        console.print()
        if env == "prod":
            console.print("[yellow]⚠  You are deploying to PRODUCTION.[/yellow]")
        if not Confirm.ask(f"Deploy {env} to {host}?", default=False):
            console.print("[dim]Cancelled.[/dim]\n")
            return

    # ── Run deployer ──
    try:
        if dep_type == "docker":
            deployer = DockerInstanceDeployer(env, env_config, project_path)
        elif dep_type == "native":
            deployer = NativeInstanceDeployer(env, env_config, project_path)
        else:
            console.print(f"\n[red]Unknown deployment type: {dep_type}[/red]\n")
            return

        success = deployer.deploy(dry_run=dry_run)

    except KeyboardInterrupt:
        console.print("\n[yellow]Deployment cancelled.[/yellow]\n")
        return
    except Exception as e:
        console.print(f"\n[red]Deployment error: {e}[/red]\n")
        if click.get_current_context().obj and click.get_current_context().obj.get("verbose"):
            import traceback

            console.print("[dim]" + traceback.format_exc() + "[/dim]")
        return

    # ── Result ──
    console.print()
    if dry_run:
        if success:
            body = (
                "[bold green]Dry-run complete — nothing was deployed[/bold green]\n\n"
                "[dim]The VPS was read, never written. Passwords are generated by the real run.[/dim]\n"
                f"[dim]Deploy for real:[/dim] [cyan]rkd instance deploy --env {env}[/cyan]"
            )
        else:
            body = (
                "[bold red]Dry-run found blocking problems[/bold red]\n\n"
                "[dim]Fix the checks marked above and run the dry-run again.[/dim]"
            )
        console.print(Panel(body, border_style="green" if success else "red", box=box.ROUNDED))
    elif success:
        if dep_type == "native":
            # Nothing stands in front of a native deploy until #211: the
            # domain has no certificate and nothing listens on 443, so
            # promising https there sends the operator to a dead URL.
            body = (
                f"[bold green]Deployment successful![/bold green]\n\n"
                f"[dim]Environment:[/dim] {env}\n"
                f"[dim]URL        :[/dim] [cyan underline]http://{host}:8069[/cyan underline]\n"
                f"[dim]Admin      :[/dim] admin, password in .rkd/secrets/instance_{env}_db.env\n\n"
                "[dim]Plain HTTP on port 8069: a native deploy has no reverse proxy yet.[/dim]"
            )
        else:
            body = (
                f"[bold green]Deployment successful![/bold green]\n\n"
                f"[dim]Environment:[/dim] {env}\n"
                f"[dim]URL        :[/dim] [cyan underline]https://{domain}[/cyan underline]\n"
                f"[dim]Admin      :[/dim] admin, password in .rkd/secrets/instance_{env}_db.env\n\n"
                "[dim]It may take a few minutes for Let's Encrypt to issue the certificate.[/dim]"
            )
        console.print(Panel(body, border_style="green", box=box.DOUBLE))
    else:
        console.print(
            Panel(
                "[bold red]Deployment failed[/bold red]\n\n"
                "[dim]Review the output above for details.[/dim]\n"
                "[dim]Run with [/dim][cyan]rkd -v instance deploy[/cyan][dim] for verbose output.[/dim]",
                border_style="red",
                box=box.ROUNDED,
            )
        )
    console.print()


@instance.command(name="traefik")
@click.option("--env", "-e", required=True, type=click.Choice(["stage", "prod"]), help="Environment whose VPS gets Traefik")
@click.option("--traefik-dir", default=None, help="Directory on the VPS (default: /opt/traefik)")
@click.option("--email", default=None, help="Let's Encrypt email (default: traefik_email of the environment)")
def instance_traefik(env, traefik_dir, email):
    """Install Traefik on the VPS of an environment (Docker deploys only).

    \b
    The Odoo container only exposes its port; Traefik is what answers on
    80/443 and gets the certificate. Safe to run again: files you tuned and
    the certificates already issued are kept.
    """
    from rocketdoo.cli_output import console_progress
    from rocketdoo.core.instance import proxy
    from rocketdoo.core.instance.config_manager import InstanceConfigManager
    from rocketdoo.core.instance.ssh_utils import resolve_auth
    from rocketdoo.core.service import ServiceError

    project_path = Path.cwd()
    mgr = InstanceConfigManager(project_path)
    if not mgr.exists():
        console.print("\n[red]No instance.yaml found. Run rkd instance init first.[/red]\n")
        raise SystemExit(1)
    try:
        env_config = mgr.get_env(env)
    except Exception as e:
        console.print(f"\n[red]Error loading config: {e}[/red]\n")
        raise SystemExit(1)
    if not env_config:
        console.print(f'\n[red]Environment "{env}" not found in instance.yaml.[/red]\n')
        raise SystemExit(1)
    if env_config.get("type", "docker") == "native":
        console.print(
            f'\n[red]"{env}" is a native deploy.[/red] It has no way to use a reverse proxy yet (#211): '
            "its Odoo answers on port 8069. Use type docker to get Traefik.\n"
        )
        raise SystemExit(1)
    email = email or env_config.get("traefik_email", "")
    if not email:
        console.print(f"\n[red]No Let's Encrypt email.[/red] Pass --email, or set traefik_email for {env}.\n")
        raise SystemExit(1)

    vps = env_config["vps"]
    directory = traefik_dir or proxy.TRAEFIK_DIR
    try:
        auth = resolve_auth(vps, env, project_path)
        report = proxy.install(auth, vps, traefik_dir=directory, email=email, on_progress=console_progress(console))
    except (ServiceError, ValueError) as e:
        console.print(f"\n[red]{e}[/red]\n")
        raise SystemExit(1)

    console.print()
    if report["traefik_foreign"]:
        attached = "is" if report["foreign_attached"] == "yes" else "is [bold]not[/bold]"
        console.print(
            Panel(
                f"[yellow]Another Traefik is running ({report['traefik_foreign']}); nothing was changed.[/yellow]\n\n"
                f"It {attached} attached to the traefik-public network. "
                "Odoo is routed only if it is, with a certificate resolver named letsencrypt.",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
    elif report["ports_taken"]:
        console.print(
            Panel(
                f"[bold red]Port {', '.join(report['ports_taken'])} is already taken on the VPS "
                "by something that is not this Traefik.[/bold red]\n\nNothing was written. Free it and run this again.",
                border_style="red",
                box=box.ROUNDED,
            )
        )
    else:
        ok = report["started"]
        console.print(
            Panel(
                f"[bold {'green' if ok else 'red'}]Traefik {'is running' if ok else 'did not start'}[/bold "
                f"{'green' if ok else 'red'}]\n\n"
                f"[dim]Directory    :[/dim] {directory}\n"
                f"[dim]Wrote        :[/dim] {', '.join(report['wrote']) or '-'}\n"
                f"[dim]Kept         :[/dim] {', '.join(report['kept']) or '-'}\n"
                f"[dim]Ports checked:[/dim] {report['ports_checked']}",
                border_style="green" if ok else "red",
                box=box.ROUNDED,
            )
        )
    console.print()
    # Non-zero when nothing ends up serving this Odoo, so the command can be
    # chained: a foreign proxy off the network routes nothing either.
    if report["ports_taken"] or not (report["started"] or report["foreign_attached"] == "yes"):
        raise SystemExit(1)


@instance.command(name="status")
def instance_status():
    """Show configuration status of all configured environments."""
    from rocketdoo.core.instance.config_manager import InstanceConfigManager

    project_path = Path.cwd()
    mgr = InstanceConfigManager(project_path)

    if not mgr.exists():
        console.print("\n[yellow]No instance.yaml found.[/yellow]")
        console.print("[dim]Run [cyan bold]rkd instance init[/cyan bold] to configure environments.[/dim]\n")
        return

    try:
        config = mgr.load()
    except Exception as e:
        console.print(f"\n[red]Error reading config: {e}[/red]\n")
        return

    environments = config.get("environments", {})
    if not environments:
        console.print("\n[yellow]No environments configured.[/yellow]\n")
        return

    table = Table(show_header=True, box=box.ROUNDED, padding=(0, 1), expand=True)
    table.add_column("Env", style="cyan bold", width=8)
    table.add_column("Type", width=8)
    table.add_column("Host", style="yellow")
    table.add_column("Domain", style="green")
    table.add_column("Odoo", width=8)
    table.add_column("PG Profile", width=10)
    table.add_column("Enterprise", justify="center", width=10)

    for env_name, env_cfg in environments.items():
        vps = env_cfg.get("vps", {})
        table.add_row(
            env_name.upper(),
            env_cfg.get("type", "docker"),
            vps.get("host", "—"),
            env_cfg.get("domain") or "—",
            env_cfg.get("odoo_version", "—"),
            env_cfg.get("pg_profile") if env_cfg.get("type", "docker") == "docker" else "—",
            "[green]Yes[/green]" if env_cfg.get("use_enterprise") else "[dim]No[/dim]",
        )

    console.print()
    console.print(
        Panel(
            table,
            title=f"[bold cyan]Instance Configuration[/bold cyan]  [dim]{mgr.config_path}[/dim]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 1),
        )
    )

    # Deploy hints
    console.print()
    for env_name in environments:
        console.print(f"  [dim]Deploy {env_name}:[/dim] [cyan bold]rkd instance deploy --env {env_name}[/cyan bold]")
    console.print()
