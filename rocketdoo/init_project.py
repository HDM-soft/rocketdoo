import os
import click
from rocketdoo.welcome import show_welcome


def init_project():
    """Wizard de configuración inicial de Rocketdoo"""
    
    # 1. Mostrar pantalla de bienvenida
    show_welcome()
    
    # 2. Esperar ENTER para continuar
    click.prompt("Presiona ENTER para comenzar...", default="", show_default=False)
    
    # 3. Preguntar nombre del proyecto
    current_dir = os.path.basename(os.getcwd())  # nombre de la carpeta actual
    project_name = click.prompt(
        "Nombre del proyecto",
        default=current_dir
    )

    click.echo(f"\n🚀 Proyecto configurado con el nombre: {project_name}")
