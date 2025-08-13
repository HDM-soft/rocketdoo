import click
from . import scaffold, init_config

@click.group()
def cli():
    """Rocketdoo CLI — Framework para Odoo con Docker/K8s"""
    pass

@cli.command()
def scaffold():
    """Crea un nuevo proyecto base con Rocketdoo"""
    scaffold.run()

@cli.command()
def init():
    """Inicia el asistente de configuración"""
    init_config.run()

if __name__ == "__main__":
    cli()
