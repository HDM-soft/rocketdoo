"""
RocketDoo Instance - Full Odoo instance deployment
rkd instance init / deploy / status
"""
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

console = Console()


@click.group(name='instance')
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


@instance.command(name='init')
@click.option('--force', '-f', is_flag=True, help='Overwrite existing configuration')
def instance_init(force):
    """Interactive wizard to configure stage / prod deployment targets.

    Saves configuration to .rkd/instance.yaml
    """
    from rocketdoo.core.instance.config_manager import InstanceConfigManager

    project_path = Path.cwd()
    mgr = InstanceConfigManager(project_path)

    if mgr.exists() and not force:
        console.print('\n[yellow]instance.yaml already exists.[/yellow]')
        if not Confirm.ask('Overwrite?', default=False):
            console.print('[dim]Cancelled.[/dim]\n')
            return

    console.print()
    console.print(Panel(
        '[bold cyan]Instance Deployment Wizard[/bold cyan]\n\n'
        '[dim]Configure stage / production environments for VPS deployment[/dim]',
        border_style='cyan', box=box.ROUNDED
    ))
    console.print()

    try:
        config = mgr.interactive_setup()
    except (KeyboardInterrupt, SystemExit):
        console.print('\n[yellow]Cancelled.[/yellow]\n')
        return

    envs = list(config.get('environments', {}).keys())
    console.print()
    console.print(Panel(
        f'[bold green]Configuration saved[/bold green]\n\n'
        f'[dim]File:[/dim]         [cyan]{mgr.config_path}[/cyan]\n'
        f'[dim]Environments:[/dim] [cyan]{", ".join(envs)}[/cyan]\n\n'
        '[dim]Deploy with:[/dim] [cyan bold]rkd instance deploy --env stage[/cyan bold]',
        border_style='green', box=box.ROUNDED
    ))
    console.print()


@instance.command(name='deploy')
@click.option('--env', '-e', required=True,
              type=click.Choice(['stage', 'prod']),
              help='Environment to deploy')
@click.option('--dry-run', is_flag=True,
              help='Render and show generated files without deploying')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation prompt')
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
        console.print('\n[red]No instance.yaml found. Run rkd instance init first.[/red]\n')
        return

    try:
        env_config = mgr.get_env(env)
    except Exception as e:
        console.print(f'\n[red]Error loading config: {e}[/red]\n')
        return

    if not env_config:
        console.print(f'\n[red]Environment "{env}" not found in instance.yaml.[/red]')
        console.print('[dim]Run rkd instance init to add it.[/dim]\n')
        return

    dep_type = env_config.get('type', 'docker')
    host = env_config.get('vps', {}).get('host', '?')
    domain = env_config.get('domain', '?')

    # ── Banner ──
    console.print()
    if dry_run:
        console.print(Panel(
            '[bold yellow]DRY-RUN MODE — no changes will be made[/bold yellow]',
            border_style='yellow', box=box.ROUNDED
        ))
        console.print()

    console.print(Panel(
        f'[bold cyan]Deploying {env.upper()}[/bold cyan]\n\n'
        f'[dim]Type   :[/dim] {dep_type}\n'
        f'[dim]Host   :[/dim] {host}\n'
        f'[dim]Domain :[/dim] {domain}',
        border_style='cyan', box=box.ROUNDED
    ))

    # ── Confirmation ──
    if not yes and not dry_run:
        console.print()
        if env == 'prod':
            console.print('[yellow]⚠  You are deploying to PRODUCTION.[/yellow]')
        if not Confirm.ask(f'Deploy {env} to {host}?', default=False):
            console.print('[dim]Cancelled.[/dim]\n')
            return

    # ── Run deployer ──
    try:
        if dep_type == 'docker':
            deployer = DockerInstanceDeployer(env, env_config, project_path)
        elif dep_type == 'native':
            deployer = NativeInstanceDeployer(env, env_config, project_path)
        else:
            console.print(f'\n[red]Unknown deployment type: {dep_type}[/red]\n')
            return

        success = deployer.deploy(dry_run=dry_run)

    except KeyboardInterrupt:
        console.print('\n[yellow]Deployment cancelled.[/yellow]\n')
        return
    except Exception as e:
        console.print(f'\n[red]Deployment error: {e}[/red]\n')
        if click.get_current_context().obj and click.get_current_context().obj.get('verbose'):
            import traceback
            console.print('[dim]' + traceback.format_exc() + '[/dim]')
        return

    # ── Result ──
    console.print()
    if success:
        scheme = 'https'
        console.print(Panel(
            f'[bold green]Deployment successful![/bold green]\n\n'
            f'[dim]Environment:[/dim] {env}\n'
            f'[dim]URL        :[/dim] [cyan underline]{scheme}://{domain}[/cyan underline]\n\n'
            '[dim]It may take a few minutes for Let\'s Encrypt to issue the certificate.[/dim]',
            border_style='green', box=box.DOUBLE
        ))
    else:
        console.print(Panel(
            f'[bold red]Deployment failed[/bold red]\n\n'
            '[dim]Review the output above for details.[/dim]\n'
            '[dim]Run with [/dim][cyan]rkd -v instance deploy[/cyan][dim] for verbose output.[/dim]',
            border_style='red', box=box.ROUNDED
        ))
    console.print()


@instance.command(name='status')
def instance_status():
    """Show configuration status of all configured environments."""
    from rocketdoo.core.instance.config_manager import InstanceConfigManager

    project_path = Path.cwd()
    mgr = InstanceConfigManager(project_path)

    if not mgr.exists():
        console.print('\n[yellow]No instance.yaml found.[/yellow]')
        console.print('[dim]Run [cyan bold]rkd instance init[/cyan bold] to configure environments.[/dim]\n')
        return

    try:
        config = mgr.load()
    except Exception as e:
        console.print(f'\n[red]Error reading config: {e}[/red]\n')
        return

    environments = config.get('environments', {})
    if not environments:
        console.print('\n[yellow]No environments configured.[/yellow]\n')
        return

    table = Table(show_header=True, box=box.ROUNDED, padding=(0, 1), expand=True)
    table.add_column('Env', style='cyan bold', width=8)
    table.add_column('Type', width=8)
    table.add_column('Host', style='yellow')
    table.add_column('Domain', style='green')
    table.add_column('Odoo', width=8)
    table.add_column('PG Profile', width=10)
    table.add_column('Enterprise', justify='center', width=10)

    for env_name, env_cfg in environments.items():
        vps = env_cfg.get('vps', {})
        table.add_row(
            env_name.upper(),
            env_cfg.get('type', 'docker'),
            vps.get('host', '—'),
            env_cfg.get('domain', '—'),
            env_cfg.get('odoo_version', '—'),
            env_cfg.get('pg_profile', '—'),
            '[green]Yes[/green]' if env_cfg.get('use_enterprise') else '[dim]No[/dim]',
        )

    console.print()
    console.print(Panel(
        table,
        title=f'[bold cyan]Instance Configuration[/bold cyan]  '
              f'[dim]{mgr.config_path}[/dim]',
        border_style='cyan', box=box.ROUNDED, padding=(1, 1)
    ))

    # Deploy hints
    console.print()
    for env_name in environments:
        console.print(
            f'  [dim]Deploy {env_name}:[/dim] '
            f'[cyan bold]rkd instance deploy --env {env_name}[/cyan bold]'
        )
    console.print()
