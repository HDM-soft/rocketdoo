import click
import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box
from .scaffold import scaffold_project
from .init_project import init_project
from .project_info import get_project_info, project_exists
from rocketdoo import __version__
from rocketdoo.docker_cli import docker, up, down, status, stop, pause, logs, build

# Detect the command name used to invoke the CLI
PROG_NAME = "rkd" if "rkd" in sys.argv[0] else "rocketdoo"

console = Console()


@click.group()
@click.version_option(version=f"{__version__}", prog_name="Rocketdoo") # version declared on rocketdoo/core/__init__.py and pyproject.toml
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose mode for detailed output')
@click.option('--config', '-c', type=click.Path(), help='Path to custom configuration file')
@click.pass_context
def main(ctx, verbose, config):
    """🚀 Rocketdoo - Odoo Development Framework"""
    # Store context for use in other commands
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    ctx.obj['config'] = config
    ctx.obj['prog_name'] = PROG_NAME


@main.command()
@click.option('--template', '-t', default='basic', type=click.Choice(['basic', 'advanced', 'minimal']),
              help='Template to use for scaffolding the project structure')
@click.option('--force', '-f', is_flag=True, help='Overwrite existing files without confirmation')
@click.pass_context
def scaffold(ctx, template, force):
    """Generate base project structure and configuration files."""
    verbose = ctx.obj.get('verbose', False)
    if verbose:
        click.echo(f"🔍 Verbose mode enabled")
        click.echo(f"📋 Using template: {template}")
        click.echo(f"💪 Force overwrite: {force}")
    
    scaffold_project(template=template, force=force, verbose=verbose)


@main.command()
@click.option('--docker-compose', '-d', is_flag=True, help='Generate docker-compose.yml configuration')
@click.option('--odoo-version', '-o', default='16.0', type=click.Choice(['14.0', '15.0', '16.0', '17.0']),
              help='Odoo version to configure for the project')
@click.pass_context
def init(ctx, docker_compose, odoo_version):
    """Initialize interactive environment configuration setup."""
    verbose = ctx.obj.get('verbose', False)
    config_file = ctx.obj.get('config')
    
    init_project()


@main.command()
@click.argument('command_name', required=False)
def help(command_name):
    """Display detailed help information for specific commands."""
    if command_name:
        # Show help for a specific command
        cmd = main.get_command(click.Context(main), command_name)
        if cmd:
            click.echo(cmd.get_help(click.Context(cmd)))
        else:
            click.echo(f"❌ Command '{command_name}' not found")
            click.echo("Available commands:")
            for name in main.list_commands(click.Context(main)):
                click.echo(f"  {name}")
    else:
        # Show general help
        click.echo(main.get_help(click.Context(main)))


@main.command()
def info():
    """Display detailed information about the current project and framework."""
    
    # Check if a project exists
    if not project_exists():
        console.print("\n[yellow]⚠️  No Rocketdoo project detected in this directory[/yellow]")
        console.print("[dim]💡 Run 'rocketdoo init' to create a new project[/dim]\n")
        
        # Show only basic framework information
        console.print(Panel(
            f"[bold cyan]🚀 Rocketdoo {__version__}[/bold cyan]\n\n"
            "[bold]Odoo Development Framework[/bold]\n\n"
            "📧 [dim]Support:[/dim] rocketdoo@hdmsoft.com.ar\n"
            "🌐 [dim]Documentation:[/dim] https://rkd-docs.readthedocs.io/",
            title="[bold magenta]Rocketdoo Framework[/bold magenta]",
            border_style="magenta",
            box=box.ROUNDED
        ))
        return
    
    # Get project information
    project_info = get_project_info()
    
    # Create main configuration table
    table = Table(
        show_header=False,
        box=box.SIMPLE,
        padding=(0, 2),
        expand=True
    )
    table.add_column("Property", style="cyan bold", width=25)
    table.add_column("Value", style="green")
    
    # Project name
    if project_info['project_name']:
        table.add_row("📦 Project Name", project_info['project_name'])
    
    # Odoo version and edition - ALWAYS show if we have the info
    if project_info['odoo_version']:
        table.add_row("🐳 Odoo Version", project_info['odoo_version'])
    
    # Always show edition (default is Community)
    table.add_row("📦 Odoo Edition", project_info['odoo_edition'])
    
    # PostgreSQL version
    if project_info['db_version']:
        table.add_row("🗄️  PostgreSQL Version", project_info['db_version'])
    
    # Containers
    if project_info['odoo_container']:
        table.add_row("📦 Web Container", project_info['odoo_container'])
    
    if project_info['db_container']:
        table.add_row("📦 DB Container", project_info['db_container'])
    
    # Ports
    if project_info['odoo_port']:
        table.add_row("🌐 Odoo Port", project_info['odoo_port'])
    
    if project_info['vsc_port']:
        table.add_row("🐛 VSCode Port", project_info['vsc_port'])
    
    if project_info['db_port']:
        table.add_row("🗄️  PostgreSQL Port", project_info['db_port'])
    
    # Restart policy
    if project_info['restart_policy']:
        restart_emoji = {
            'no': '🚫',
            'always': '🔄',
            'unless-stopped': '⏸️',
            'on-failure': '⚠️'
        }.get(project_info['restart_policy'], '♻️')
        table.add_row(f"{restart_emoji} Restart Policy", project_info['restart_policy'])
    
    # Master password (partially hidden)
    if project_info['admin_passwd']:
        passwd = project_info['admin_passwd']
        # Hide password except first 2 characters
        masked_passwd = passwd[:2] + '*' * (len(passwd) - 2) if len(passwd) > 2 else '****'
        table.add_row("🔑 Master Password", masked_passwd)
    
    # SSH / Private repositories
    if project_info['use_private_repos'] and project_info['ssh_key']:
        table.add_row("🔐 Private Repositories", f"✅ (key: {project_info['ssh_key']})")
    else:
        table.add_row("🔐 Private Repositories", "❌")
    
    # Show main panel
    console.print()
    console.print(Panel(
        table,
        title="[bold cyan]📊 Project Configuration[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 2)
    ))
    
    # Third-party repositories (Gitman)
    if project_info['use_third_party_repos'] and project_info['third_party_repos']:
        repos_table = Table(
            show_header=True,
            box=box.SIMPLE_HEAD,
            padding=(0, 1),
            expand=True
        )
        repos_table.add_column("📚 Name", style="yellow bold", no_wrap=True)
        repos_table.add_column("🔗 Repository", style="blue", overflow="fold")
        repos_table.add_column("🏷️  Branch", style="green", justify="center")
        
        for repo in project_info['third_party_repos']:
            # Shorten URL if too long
            repo_url = repo['repo']
            if len(repo_url) > 60:
                repo_url = repo_url[:57] + "..."
            
            repos_table.add_row(
                repo['name'],
                repo_url,
                repo['rev']
            )
        
        console.print()
        console.print(Panel(
            repos_table,
            title=f"[bold magenta]📚 Third-Party Repositories ({len(project_info['third_party_repos'])})[/bold magenta]",
            border_style="magenta",
            box=box.ROUNDED,
            padding=(1, 1)
        ))
    
    # Footer with framework information
    console.print()
    footer_text = Text()
    footer_text.append("🚀 ", style="bold")
    footer_text.append(f"Rocketdoo {__version__}", style="bold cyan")
    footer_text.append(" | ", style="dim")
    footer_text.append("📧 ", style="bold")
    footer_text.append("rocketdoo@hdmsoft.com.ar", style="dim")
    footer_text.append(" | ", style="dim")
    footer_text.append("🌐 ", style="bold")
    footer_text.append("https://rkd-docs.readthedocs.io", style="dim blue underline")
    
    console.print(Panel(
        footer_text,
        border_style="dim",
        box=box.ROUNDED
    ))
    console.print()

# ============================================================
# 🚀 Register Docker commands as Rocketdoo subcommands
# ============================================================
# Complete subgroup
main.add_command(docker, name="docker")

# Direct alias
main.add_command(up)
main.add_command(down)
main.add_command(status)
main.add_command(stop)
main.add_command(pause)
main.add_command(logs)
main.add_command(build)

if __name__ == "__main__":
    main()