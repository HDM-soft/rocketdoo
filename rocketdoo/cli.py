import click
import sys
from .scaffold import scaffold_project
from .init_project import init_project

# Detectar el nombre con que fue llamado el comando
PROG_NAME = "rkd" if "rkd" in sys.argv[0] else "rocketdoo"

@click.group()
@click.version_option(version="2.0.0b1", prog_name="Rocketdoo")
@click.option('-v', '--verbose', is_flag=True, help='Modo verbose para más detalles')
@click.option('--config', '-c', type=click.Path(), help='Archivo de configuración personalizado')
@click.pass_context
def main(ctx, verbose, config):
    """🚀 Rocketdoo - Framework para entornos Odoo"""
    # Guardar contexto para usar en otros comandos
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    ctx.obj['config'] = config
    ctx.obj['prog_name'] = PROG_NAME

@main.command()
@click.option('--template', '-t', default='basic', 
              type=click.Choice(['basic', 'advanced', 'minimal']),
              help='Plantilla a usar para el scaffold')
@click.option('--force', '-f', is_flag=True, help='Sobrescribir archivos existentes')
@click.pass_context
def scaffold(ctx, template, force):
    """Genera la estructura base del proyecto."""
    verbose = ctx.obj.get('verbose', False)
    
    if verbose:
        click.echo(f"🔍 Modo verbose activado")
        click.echo(f"📋 Usando plantilla: {template}")
        click.echo(f"💪 Forzar sobrescritura: {force}")
    
    scaffold_project(template=template, force=force, verbose=verbose)

@main.command()
@click.option('--docker-compose', '-d', is_flag=True, help='Generar docker-compose.yml')
@click.option('--odoo-version', '-o', default='16.0', 
              type=click.Choice(['14.0', '15.0', '16.0', '17.0']),
              help='Versión de Odoo a configurar')
@click.pass_context
def init(ctx, docker_compose, odoo_version):
    """Inicia la configuración interactiva del entorno."""
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
    """Muestra ayuda detallada para comandos específicos."""
    if command_name:
        # Mostrar ayuda de un comando específico
        cmd = main.get_command(click.Context(main), command_name)
        if cmd:
            click.echo(cmd.get_help(click.Context(cmd)))
        else:
            click.echo(f"❌ Comando '{command_name}' no encontrado")
            click.echo("Comandos disponibles:")
            for name in main.list_commands(click.Context(main)):
                click.echo(f"  {name}")
    else:
        # Mostrar ayuda general
        click.echo(main.get_help(click.Context(main)))

# Comando adicional para mostrar información del proyecto
@main.command()
def info():
    """Muestra información detallada sobre el proyecto actual."""
    click.echo("📊 Información del proyecto:")
    click.echo("🚀 Rocketdoo v2.0.0b1")
    click.echo("📧 Soporte: horaciomontano@hdmsoft.com.ar")
    click.echo("🌐 Documentación: https://rocketdoo.dev")
    
if __name__ == "__main__":
    main()