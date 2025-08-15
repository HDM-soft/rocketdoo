import click
import sys
from .scaffold import scaffold_project
from .init_project import init_project

# Detect the command name used to invoke the CLI
PROG_NAME = "rkd" if "rkd" in sys.argv[0] else "rocketdoo"

@click.group()
@click.version_option(version="2.0.0b1", prog_name="Rocketdoo")
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
@click.option('--template', '-t', default='basic', 
              type=click.Choice(['basic', 'advanced', 'minimal']),
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
@click.option('--odoo-version', '-o', default='16.0', 
              type=click.Choice(['14.0', '15.0', '16.0', '17.0']),
              help='Odoo version to configure for the project')
@click.pass_context
def init(ctx, docker_compose, odoo_version):
    """Initialize interactive environment configuration setup."""
    verbose = ctx.obj.get('verbose', False)
    config_file = ctx.obj.get('config')
    
    init_project(
        docker_compose=docker_compose, 
        odoo_version=odoo_version,
        verbose=verbose,
        config_file=config_file
    )

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
    click.echo("📊 Project Information:")
    click.echo("🚀 Rocketdoo v2.0.0b1")
    click.echo("📧 Support: rocketdoo@hdmsoft.com.ar")
    click.echo("🌐 Documentation: https://rocketdoo-docs.readthedocs.io/")
    
if __name__ == "__main__":
    main()