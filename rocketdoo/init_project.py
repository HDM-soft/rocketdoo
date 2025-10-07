import click
import os
from jinja2 import Environment, FileSystemLoader
from rocketdoo.welcome import show_welcome
from rocketdoo.core.port_validation import validate_port, find_available_port

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def render_template(template_name, output_name, **context):
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template(template_name)
    content = template.render(**context)
    with open(output_name, "w") as f:
        f.write(content)
    click.echo(f"✅ Archivo generado: {output_name}")


def prompt_port(label, default_port):
    """Pregunta un puerto sin validar (validación se hace después)."""
    while True:
        port = click.prompt(f"{label}", default=str(default_port), show_default=True)
        try:
            port = int(port)
            if port < 1024 or port > 65535:
                click.echo("❌ El puerto debe estar entre 1024 y 65535.")
                continue
            return port
        except ValueError:
            click.echo("❌ Ingresa un número de puerto válido.")


def validate_ports(odoo_port, vsc_port):
    """Valida ambos puertos y retorna True si ambos están libres."""
    errors = []
    
    try:
        validate_port(odoo_port, "Puerto de Odoo")
    except RuntimeError as e:
        errors.append(str(e))
    
    try:
        validate_port(vsc_port, "Puerto para debug (VSC)")
    except RuntimeError as e:
        errors.append(str(e))
    
    if errors:
        click.echo("\n⚠️  Se encontraron los siguientes problemas:")
        for error in errors:
            click.echo(f"  • {error}")
        return False
    
    return True


def prompt_ports_until_valid(default_odoo=8069, default_vsc=8888):
    """Solicita puertos repetidamente hasta que ambos sean válidos."""
    while True:
        click.echo("\n📍 Configuración de puertos:")
        odoo_port = prompt_port("Puerto de Odoo", default_odoo)
        vsc_port = prompt_port("Puerto para debug (VSC)", default_vsc)
        
        click.echo("\n🔍 Validando puertos...")
        
        if validate_ports(odoo_port, vsc_port):
            click.echo("✅ Todos los puertos están disponibles!\n")
            return odoo_port, vsc_port
        
        # Si hay errores, ofrecer sugerencias
        click.echo("\n💡 Puertos disponibles sugeridos:")
        suggested_odoo = find_available_port(odoo_port + 1)
        suggested_vsc = find_available_port(vsc_port + 1)
        click.echo(f"  • Odoo: {suggested_odoo}")
        click.echo(f"  • VSC:  {suggested_vsc}")
        
        if click.confirm("\n¿Deseas usar los puertos sugeridos?", default=True):
            return suggested_odoo, suggested_vsc
        else:
            click.echo("\n⬇️  Por favor, ingresa otros puertos:\n")


def init_project():
    """Asistente de configuración inicial de Rocketdoo"""
    
    show_welcome()

    current_dir = os.path.basename(os.getcwd())
    project_name = click.prompt("Nombre del proyecto", default=current_dir)

    # Seleccionar versión de Odoo
    odoo_versions = ["15.0", "16.0", "17.0", "18.0", "19.0"]
    click.echo("\nSelecciona la versión de Odoo:")
    for i, v in enumerate(odoo_versions, start=1):
        click.echo(f"{i}. Odoo {v}")
    choice = click.prompt("Número de versión", type=click.IntRange(1, len(odoo_versions)))
    odoo_version = odoo_versions[choice - 1]

    # Seleccionar versión de PostgreSQL
    db_versions = ["13", "14", "15"]
    click.echo("\nSelecciona la versión de PostgreSQL:")
    for i, v in enumerate(db_versions, start=1):
        click.echo(f"{i}. PostgreSQL {v}")
    db_choice = click.prompt("Número de versión", type=click.IntRange(1, len(db_versions)))
    db_version = db_versions[db_choice - 1]

    # Validación de puertos (loop hasta que sean válidos)
    odoo_port, vsc_port = prompt_ports_until_valid()

    context = {
        "project_name": project_name,
        "odoo_version": odoo_version,
        "db_version": db_version,
        "odoo_port": odoo_port,
        "vsc_port": vsc_port,
        "restart": "always",
        "odoo_image": f"odoo:{odoo_version}",
        "odoo_container": f"{project_name}-odoo",
        "db_container": f"{project_name}-db",
    }

    # Renderizar los templates
    render_template("Dockerfile.jinja", "Dockerfile", **context)
    render_template("docker-compose.yaml.jinja", "docker-compose.yaml", **context)

    click.echo(f"\n🚀 Proyecto '{project_name}' configurado correctamente con Odoo {odoo_version} y PostgreSQL {db_version}.")


if __name__ == "__main__":
    init_project()