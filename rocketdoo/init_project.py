import click
import os
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
import questionary
from rocketdoo.welcome import show_welcome
from rocketdoo.core.port_validation import validate_port, find_available_port
from rocketdoo.core.edition_setup import setup_enterprise_edition
from rocketdoo.core.ssh_manager import (
    list_private_keys,
    copy_key_to_build_context,
    inject_ssh_into_dockerfile
)
from rocketdoo.core.gitman_config import (
    generate_gitman_yaml,
    update_odoo_conf_with_gitman,
    extract_repo_name_from_url,
    detect_repo_type
)

# Directorios base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
CONFIG_TEMPLATE_DIR = os.path.join(TEMPLATES_DIR, "config")  # templates/config
CONFIG_OUTPUT_DIR = os.path.join(os.getcwd(), "config")       # ./config
VSCODE_TEMPLATE_DIR = os.path.join(TEMPLATES_DIR, ".vscode")   # templates/.vscode
VSCODE_OUTPUT_DIR = os.path.join(os.getcwd(), ".vscode")

def render_template(template_dir, template_name, output_name, **context):
    """Renderiza una plantilla Jinja2 en el directorio actual o de salida"""
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)
    
    output_path = os.path.join(os.getcwd(), output_name)
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    content = template.render(**context)
    with open(output_path, "w") as f:
        f.write(content)

    print(f"✅ Archivo generado: {output_path}")


def prompt_port(label, default_port):
    """Pregunta un puerto numérico válido"""
    while True:
        port = click.prompt(label, default=str(default_port), show_default=True)
        try:
            port = int(port)
            if 1024 <= port <= 65535:
                return port
            click.echo("❌ El puerto debe estar entre 1024 y 65535.")
        except ValueError:
            click.echo("❌ Ingresa un número de puerto válido.")


def validate_ports(odoo_port, vsc_port):
    """Valida ambos puertos y retorna True si ambos están libres"""
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
    """Solicita puertos repetidamente hasta que ambos sean válidos"""
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

    # Selección de versiones con menú interactivo
    odoo_versions = ["15.0", "16.0", "17.0", "18.0", "19.0"]
    click.echo("\n📦 Selecciona la versión de Odoo (usa ↑↓ y ENTER):")
    odoo_version = questionary.select(
        "Versión de Odoo:", choices=odoo_versions, default="18.0"
    ).ask()

    # ========== PREGUNTA: EDICIÓN DE ODOO ==========
    click.echo("\n🏢 Selecciona la edición de Odoo (usa ↑↓ y ENTER):")
    odoo_edition = questionary.select(
        "Edición de Odoo:",
        choices=["Community", "Enterprise"],
        default="Community"
    ).ask()

    # ========== PREGUNTA: REPOSITORIOS PRIVADOS ==========
    click.echo("\n🔐 ¿Quieres usar repositorios privados?")
    use_private_repos = questionary.confirm(
        "¿Usar repositorios privados?",
        default=False
    ).ask()
    
    selected_ssh_key = None
    if use_private_repos:
        available_keys = list_private_keys()
        
        if not available_keys:
            click.echo("\n⚠️  No se encontraron claves SSH en ~/.ssh/")
            click.echo("💡 Genera una clave SSH primero con: ssh-keygen -t rsa -b 4096")
            use_private_repos = False
        else:
            click.echo(f"\n🔑 Se encontraron {len(available_keys)} clave(s) SSH disponible(s)")
            selected_ssh_key = questionary.select(
                "Selecciona la clave SSH a usar:",
                choices=available_keys
            ).ask()

    # ========== PREGUNTA: REPOSITORIOS DE TERCEROS ==========
    click.echo("\n📚 ¿Deseas usar repositorios de terceros (con Gitman)?")
    use_third_party_repos = questionary.confirm(
        "¿Usar repositorios de terceros?",
        default=False
    ).ask()
    
    gitman_sources = []
    if use_third_party_repos:
        click.echo("\n📝 Configuraremos repositorios de terceros con Gitman")
        click.echo("💡 Podrás agregar más repositorios después editando gitman.yaml")
        
        # Preguntar si quiere agregar repositorios ahora
        add_repos_now = questionary.confirm(
            "¿Deseas agregar repositorios ahora?",
            default=False
        ).ask()
        
        if add_repos_now:
            while True:
                click.echo("\n" + "="*50)
                repo_url = click.prompt(
                    "URL del repositorio (presiona Enter sin texto para terminar)", 
                    default="",
                    show_default=False
                )
                
                # Si el usuario presiona Enter sin texto, salir del loop
                if not repo_url.strip():
                    if gitman_sources:
                        click.echo("✅ Repositorios configurados exitosamente")
                    else:
                        click.echo("ℹ️  No se agregaron repositorios")
                    break
                
                # Extraer el nombre automáticamente usando la función de gitman_config
                try:
                    repo_name = extract_repo_name_from_url(repo_url)
                except Exception:
                    repo_name = "custom-repo"
                
                # La rev es automáticamente la versión de Odoo seleccionada
                repo_rev = odoo_version
                
                # Determinar el tipo usando la función de gitman_config
                repo_type = detect_repo_type(repo_url)
                
                # ORDEN CORRECTO para gitman.yaml: repo, name, rev, type
                gitman_sources.append({
                    "repo": repo_url,
                    "name": repo_name,
                    "rev": repo_rev,
                    "type": repo_type,
                })
                
                click.echo(f"✅ Repositorio '{repo_name}' agregado")
                click.echo(f"   URL: {repo_url}")
                click.echo(f"   Branch: {repo_rev}")
                
                if not questionary.confirm("¿Agregar otro repositorio?", default=False).ask():
                    break

    db_versions = ["13", "14", "15"]
    click.echo("\n📦 Selecciona la versión de PostgreSQL (usa ↑↓ y ENTER):")
    db_version = questionary.select(
        "Versión de PostgreSQL:", choices=db_versions, default="14"
    ).ask()

    # Pregunta por la contraseña maestra
    admin_passwd = click.prompt(
        "Contraseña maestra de Odoo", default="admin", hide_input=False
    )
    
    restart_policy = questionary.select(
        "\n♻️  ¿Cómo desea reiniciar el ambiente?",
        choices=["no", "always", "unless-stopped"],
        default="unless-stopped"
    ).ask()
    
    # Validación de puertos
    odoo_port, vsc_port = prompt_ports_until_valid()

    # Nombres de contenedores coherentes
    odoo_container = f"odoo-{project_name}"
    db_container = f"db-{project_name}"

    # Contexto para los templates
    context = {
        "project_name": project_name,
        "odoo_version": odoo_version,
        "odoo_edition": odoo_edition,
        "db_version": db_version,
        "odoo_port": odoo_port,
        "vsc_port": vsc_port,
        "restart": restart_policy,
        "odoo_image": f"odoo:{odoo_version}",
        "odoo_container": odoo_container,
        "db_container": db_container,
        "admin_passwd": admin_passwd,
        "use_private_repos": use_private_repos,
        "ssh_key_name": selected_ssh_key,
        "use_third_party_repos": use_third_party_repos,
    }

    # === Generar archivos ===
    render_template(TEMPLATES_DIR, "Dockerfile.jinja", "Dockerfile", **context)
    render_template(TEMPLATES_DIR, "docker-compose.yaml.jinja", "docker-compose.yaml", **context)

    # Generar config/odoo.conf desde templates/config/odoo.conf.jinja
    conf_template = "odoo.conf.jinja"
    conf_output = os.path.join(CONFIG_OUTPUT_DIR, "odoo.conf")
    render_template(CONFIG_TEMPLATE_DIR, conf_template, conf_output, **context)
    
    vscode_template = "launch.json.jinja"
    vscode_output = os.path.join(VSCODE_OUTPUT_DIR, "launch.json")
    render_template(VSCODE_TEMPLATE_DIR, vscode_template, vscode_output, **context)

    # ========== CONFIGURAR ENTERPRISE SI FUE SELECCIONADO ==========
    if odoo_edition == "Enterprise":
        click.echo("\n🔧 Configurando Odoo Enterprise...")
        try:
            project_root = Path(os.getcwd())
            setup_enterprise_edition(project_root)
        except Exception as e:
            click.echo(f"\n⚠️  Advertencia: No se pudo configurar Enterprise completamente: {e}")
            click.echo("Puedes configurarlo manualmente después.")

    # ========== CONFIGURAR SSH SI SE ELIGIÓ USAR REPOS PRIVADOS ==========
    if use_private_repos and selected_ssh_key:
        click.echo("\n🔐 Configurando acceso a repositorios privados...")
        try:
            project_root = Path(os.getcwd())
            dockerfile_path = project_root / "Dockerfile"
            
            # Copiar la clave SSH al contexto de build
            click.echo(f"📋 Copiando clave SSH: {selected_ssh_key}")
            copy_key_to_build_context(selected_ssh_key, project_root)
            
            # Modificar el Dockerfile para usar la clave SSH
            if dockerfile_path.exists():
                click.echo("📝 Actualizando Dockerfile...")
                inject_ssh_into_dockerfile(dockerfile_path, selected_ssh_key)
                click.echo("✅ Configuración SSH completada")
                click.echo("💡 Recuerda: Agrega tu clave pública a GitHub/GitLab/Bitbucket")
            else:
                click.echo("⚠️  No se encontró el Dockerfile")
                
        except Exception as e:
            click.echo(f"\n⚠️  Advertencia: No se pudo configurar SSH completamente: {e}")
            click.echo("Puedes configurarlo manualmente después.")

    # ========== CONFIGURAR GITMAN SI SE ELIGIÓ USAR REPOS DE TERCEROS ==========
    if use_third_party_repos:
        click.echo("\n📚 Configurando Gitman para repositorios de terceros...")
        try:
            project_root = Path(os.getcwd())
            gitman_path = project_root / "gitman.yaml"
            odoo_conf_path = project_root / "config" / "odoo.conf"
            
            click.echo(f"📝 Generando {gitman_path.name}...")
            # Pasar los parámetros necesarios a la función
            generate_gitman_yaml(sources=gitman_sources, output_path=gitman_path)
            click.echo(f"✅ Archivo {gitman_path.name} creado")
            
            # Actualizar odoo.conf si hay repositorios configurados
            if gitman_sources and odoo_conf_path.exists():
                click.echo("📝 Actualizando odoo.conf con rutas de external_addons...")
                update_odoo_conf_with_gitman(odoo_conf_path, gitman_sources)
                click.echo("✅ Configuración de addons_path actualizada")
            elif gitman_sources:
                click.echo("⚠️  No se encontró odoo.conf, se omitió la actualización de addons_path")
            
            click.echo("\n💡 Próximos pasos:")
            click.echo("   1. Asegúrate de tener gitman instalado: pip install gitman")
            click.echo("   2. Para instalar los repositorios ejecuta: gitman install")
            if use_private_repos and selected_ssh_key:
                click.echo("   3. Verifica que tu clave SSH esté agregada a GitHub/GitLab")
            
        except Exception as e:
            click.echo(f"\n⚠️  Advertencia: No se pudo configurar Gitman completamente: {e}")
            click.echo("Puedes configurarlo manualmente después.")

    # Resumen final
    click.echo("\n" + "="*60)
    click.echo(f"🚀 Proyecto '{project_name}' configurado correctamente")
    click.echo("="*60)
    click.echo(f"\n📦 Odoo {odoo_version} ({odoo_edition}) + PostgreSQL {db_version}")
    click.echo(f"🌐 Puerto Odoo: {odoo_port}")
    click.echo(f"🐛 Puerto Debug: {vsc_port}")
    
    if use_private_repos and selected_ssh_key:
        click.echo(f"\n🔐 SSH configurado con clave: {selected_ssh_key}")
        click.echo("   ⚠️  Recuerda agregar tu clave pública a tu proveedor Git")
    
    if odoo_edition == "Enterprise":
        click.echo("\n🏢 Edición Enterprise habilitada")
    
    if use_third_party_repos:
        if gitman_sources:
            click.echo(f"\n📚 Gitman configurado con {len(gitman_sources)} repositorio(s):")
            for source in gitman_sources:
                click.echo(f"   • {source['name']} ({source['rev']})")
        else:
            click.echo("\n📚 Gitman configurado (sin repositorios iniciales)")
            click.echo("   💡 Edita gitman.yaml para agregar repositorios")
    
    click.echo("\n✨ ¡Listo para comenzar!")
    click.echo("   Ejecuta: docker-compose up -d")


if __name__ == "__main__":
    init_project()