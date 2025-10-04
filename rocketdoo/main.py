import click
from . import scaffold
from rocketdoo.cli.init import init as init_wizard  # 👈 importamos el nuevo wizard

@click.group()
def cli():
    """Rocketdoo CLI — Framework para Odoo con Docker/K8s"""
    pass

@cli.command()
def scaffold_cmd():
    """Crea un nuevo proyecto base con Rocketdoo"""
    scaffold.run()

@cli.command()
def init():
    """Inicia el asistente de configuración"""
    init_wizard()   # 👈 aquí llamamos al nuevo wizard

if __name__ == "__main__":
    cli()
