import click
from .scaffold import scaffold_project
from .init_project import init_project

@click.group()
@click.version_option("2.0.0b1", prog_name="Rocketdoo")
def main():
    """🚀 Rocketdoo - Framework para entornos Odoo"""
    pass

@main.command()
def scaffold():
    """Genera la estructura base del proyecto."""
    scaffold_project()

@main.command()
def init():
    """Inicia la configuración interactiva del entorno."""
    init_project()

@main.command()
def help():
    """Muestra la lista de comandos disponibles."""
    click.echo(main.get_help(click.Context(main)))
