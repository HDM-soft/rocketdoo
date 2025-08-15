# rocketdoo/scaffold.py
import os
import stat
import click
from jinja2 import Template

# Contenido base de install_dependecies.sh
INSTALL_DEPENDENCIES = """#!/bin/bash

file_to_check="$PWD/requirements.txt"

if [ -f "$file_to_check" ]; then
    echo "The file $file_to_check exists. Installing python dependencies"
    pip install -r "$file_to_check" 2>&1
    exit_code=$?
    if [ $exit_code -ne 0 ]; then
      echo "Error: Unable to install at least one dependency of $file_to_check.\\n"
      echo "$output"
      echo "Please log into the container and install them manually or modify the dockerfile or docker compose."
      exit 0
    fi
else
    echo "The file $file_to_check does not exist."
fi
"""

# Plantilla Jinja2 de docker-compose
DOCKER_COMPOSE_TEMPLATE = """services:
  web:
    build: .
    restart: {{ restart }}
    image: {{ odoo_image }}
    container_name: {{ odoo_container }}
    depends_on:
      - db
    ports:
      - "{{ odoo_port }}:8069"
      - "{{ vsc_port }}:8888"
    volumes:
      - {{ project_name }}-web-data:/var/lib/odoo
      - ./config:/etc/odoo
      - ./addons:/usr/lib/python3/dist-packages/odoo/extra-addons
      #- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise
      - ./.vscode:/usr/lib/python3/dist-packages/odoo/.vscode
  db:
    restart: {{ restart }}
    image: postgres:{{ db_version }}
    container_name: {{ db_container }}
    environment:
      - POSTGRES_DB=postgres
      - POSTGRES_PASSWORD_FILE=/run/secrets/postgresql_password
      - POSTGRES_USER=root
      - PGDATA=/var/lib/postgresql/data/pgdata
    volumes:
      - {{ project_name }}-db-data:/var/lib/postgresql/data/pgdata
    secrets:
      - postgresql_password
volumes:
  {{ project_name }}-web-data:
  {{ project_name }}-db-data:

secrets:
  postgresql_password:
    file: odoo_pg_pass
"""

@click.command()
@click.option("--project-name", prompt="Nombre del proyecto", help="Nombre base para el proyecto.")
@click.option("--odoo-image", default="odoo:17", help="Imagen de Odoo a usar.")
@click.option("--odoo-container", default="odoo_web", help="Nombre del contenedor de Odoo.")
@click.option("--db-version", default="15", help="Versión de PostgreSQL.")
@click.option("--db-container", default="odoo_db", help="Nombre del contenedor de la base de datos.")
@click.option("--odoo-port", default="8069", help="Puerto para Odoo.")
@click.option("--vsc-port", default="8888", help="Puerto para VSCode server.")
@click.option("--restart", default="always", help="Política de reinicio de contenedores.")
def scaffold_project(project_name, odoo_image, odoo_container, db_version, db_container, odoo_port, vsc_port, restart):
    """🏗️ Genera la estructura base del proyecto Rocketdoo"""
    click.secho(f"🚀 Creando estructura para {project_name}...", fg="green", bold=True)

    # Crear carpetas base
    dirs = ["config", "addons", ".vscode"]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        click.secho(f"📁 Carpeta creada: {d}", fg="cyan")

    # Guardar install_dependecies.sh
    install_path = "install_dependecies.sh"
    with open(install_path, "w") as f:
        f.write(INSTALL_DEPENDENCIES)
    os.chmod(install_path, os.stat(install_path).st_mode | stat.S_IEXEC)
    click.secho(f"🛠️ Script creado: {install_path}", fg="yellow")

    # Generar docker-compose.yaml
    template = Template(DOCKER_COMPOSE_TEMPLATE)
    docker_compose_content = template.render(
        project_name=project_name,
        odoo_image=odoo_image,
        odoo_container=odoo_container,
        db_version=db_version,
        db_container=db_container,
        odoo_port=odoo_port,
        vsc_port=vsc_port,
        restart=restart
    )
    with open("docker-compose.yaml", "w") as f:
        f.write(docker_compose_content)
    click.secho("🐳 docker-compose.yaml generado con éxito", fg="blue")

    click.secho("✅ Proyecto inicializado correctamente", fg="green", bold=True)


if __name__ == "__main__":
    scaffold_project()
