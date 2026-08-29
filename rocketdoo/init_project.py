import os
from pathlib import Path

import click
import questionary
from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from rocketdoo.core.edition_setup import setup_enterprise_edition
from rocketdoo.core.gitignore_manager import ensure_gitignore
from rocketdoo.core.gitman_config import (
    detect_repo_type,
    extract_repo_name_from_url,
    generate_gitman_yaml,
    update_odoo_conf_with_gitman,
)
from rocketdoo.core.port_validation import (
    collect_declared_ports,
    find_available_port,
    get_port_reservation,
    validate_port,
)
from rocketdoo.core.ssh_manager import copy_key_to_build_context, inject_ssh_into_dockerfile, list_private_keys
from rocketdoo.welcome import show_welcome

console = Console()

# Base Directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
CONFIG_TEMPLATE_DIR = os.path.join(TEMPLATES_DIR, "config")  # templates/config
VSCODE_TEMPLATE_DIR = os.path.join(TEMPLATES_DIR, ".vscode")   # templates/.vscode

# Output paths are deliberately NOT module-level constants: os.getcwd() at
# import time freezes whatever directory the process started in, so a caller
# that imports this module and then changes directory (the GUI navigates
# between projects) would write odoo.conf into the previous project.
# render_template() resolves relative paths against the current directory.

def render_template(template_dir, template_name, output_name, **context):
    """Render a Jinja2 template in the current or output directory"""
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)

    output_path = os.path.join(os.getcwd(), output_name)
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    content = template.render(**context)
    with open(output_path, "w") as f:
        f.write(content)

    print(f"✅ File generated: {output_path}")


def prompt_port(label, default_port):
    """Ask for a valid numeric port"""
    while True:
        port = click.prompt(label, default=str(default_port), show_default=True)
        try:
            port = int(port)
            if 1024 <= port <= 65535:
                return port
            click.echo("❌ The port must be between 1024 and 65535.")
        except ValueError:
            click.echo("❌ Please enter a valid port number.")


def validate_ports(odoo_port, vsc_port):
    """Validate both ports and return True if both can be used.

    A port declared in another project's docker-compose is only a warning: that
    project is not running, so the port is free right now.
    """
    errors = []
    warnings = []

    for value, label in ((odoo_port, "Odoo Port"), (vsc_port, "VSC Debug Port")):
        try:
            validate_port(value, label)
        except RuntimeError as e:
            errors.append(str(e))
            continue
        reservation = get_port_reservation(value)
        if reservation:
            warnings.append(reservation)

    if warnings:
        click.echo("\n⚠️  Heads up:")
        for warning in warnings:
            click.echo(f"  • {warning}")

    if errors:
        click.echo("\n❌ The following ports are not available:")
        for error in errors:
            click.echo(f"  • {error}")
        return False
    return True


def prompt_ports_until_valid(default_odoo=8069, default_vsc=8888):
    """Ask for ports repeatedly until both are valid"""
    while True:
        click.echo("\n📍 Port configuration:")
        odoo_port = prompt_port("Odoo Port", default_odoo)
        vsc_port = prompt_port("VSC Debug Port", default_vsc)

        click.echo("\n🔍 Validating ports...")
        if validate_ports(odoo_port, vsc_port):
            click.echo("✅ All ports are available!\n")
            return odoo_port, vsc_port

        # If there are errors, offer suggestions
        click.echo("\n💡 Suggested available ports:")
        # Un solo barrido de los compose vecinos para ambas sugerencias
        declared = collect_declared_ports()
        suggested_odoo = find_available_port(odoo_port + 1, declared=declared)
        suggested_vsc = find_available_port(vsc_port + 1, declared=declared)
        click.echo(f"  • Odoo: {suggested_odoo}")
        click.echo(f"  • VSC:  {suggested_vsc}")

        if click.confirm("\nWould you like to use the suggested ports?", default=True):
            return suggested_odoo, suggested_vsc
        else:
            click.echo("\n⬇️  Please enter other ports:\n")


def init_project():
    """Initial configuration assistant for Rocketdoo"""

    show_welcome()


    current_dir = os.path.basename(os.getcwd())
    project_name = click.prompt("Project Name", default=current_dir)

    # Convert to lowercase and warn if it was changed
    original_name = project_name
    project_name = project_name.lower()

    if original_name != project_name:
        console.print("\n[yellow]⚠️  Project names must be in lowercase![/yellow]")
        console.print(f"[dim]Converting: '{original_name}' → '{project_name}'[/dim]")

        if not click.confirm(f"\nUse '{project_name}' as project name?", default=True):
            project_name = click.prompt("Enter a new project name (lowercase)", type=str).lower()

        console.print("[dim]💡 Docker project names must be in lowercase[/dim]\n")


    # Version selection with interactive menu
    odoo_versions = ["15.0", "16.0", "17.0", "18.0", "19.0"]
    click.echo("\n📦 Select Odoo version (use ↑↓ and ENTER):")
    odoo_version = questionary.select(
        "Odoo Version:", choices=odoo_versions, default="18.0"
    ).ask()

    # ========== QUESTION: ODOO EDITION ==========
    click.echo("\n🏢 Select Odoo edition (use ↑↓ and ENTER):")
    odoo_edition = questionary.select(
        "Odoo Edition:",
        choices=["Community", "Enterprise"],
        default="Community"
    ).ask()

    # ========== QUESTION: PRIVATE REPOSITORIES ==========
    click.echo("\n🔐 Do you want to use private repositories?")
    use_private_repos = questionary.confirm(
        "Use private repositories?",
        default=False
    ).ask()

    selected_ssh_key = None
    if use_private_repos:
        available_keys = list_private_keys()

        if not available_keys:
            click.echo("\n⚠️  No SSH keys found in ~/.ssh/")
            click.echo("💡 Generate an SSH key first with: ssh-keygen -t rsa -b 4096")
            use_private_repos = False
        else:
            click.echo(f"\n🔑 Found {len(available_keys)} available SSH key(s)")
            selected_ssh_key = questionary.select(
                "Select SSH key to use:",
                choices=available_keys
            ).ask()

    # ========== QUESTION: THIRD-PARTY REPOSITORIES ==========
    click.echo("\n📚 Do you want to use third-party repositories (with Gitman)?")
    use_third_party_repos = questionary.confirm(
        "Use third-party repositories?",
        default=False
    ).ask()

    gitman_sources = []
    if use_third_party_repos:
        click.echo("\n📝 Configuring third-party repositories with Gitman")
        click.echo("💡 You can add more repositories later by editing gitman.yaml.")

        # Ask if you want to add repositories now
        add_repos_now = questionary.confirm(
            "Would you like to add repositories now?",
            default=False
        ).ask()

        if add_repos_now:
            while True:
                click.echo("\n" + "="*50)
                repo_url = click.prompt(
                    "URL of the repository (press Enter without text to finish)",
                    default="",
                    show_default=False
                )

                # If the user presses Enter without text, exit the loop.
                if not repo_url.strip():
                    if gitman_sources:
                        click.echo("✅ Repositories successfully configured")
                    else:
                        click.echo("ℹ️  No repositories were added")
                    break

                # Automatically extract the name using the gitman_config function
                try:
                    repo_name = extract_repo_name_from_url(repo_url)
                except Exception:
                    repo_name = "custom-repo"

                # The rev is automatically the selected Odoo version.
                repo_rev = odoo_version

                # Determine the type using the gitman_config function
                repo_type = detect_repo_type(repo_url)

                # CORRECT ORDER for gitman.yaml: repo, name, rev, type
                gitman_sources.append({
                    "repo": repo_url,
                    "name": repo_name,
                    "rev": repo_rev,
                    "type": repo_type,
                })

                click.echo(f"✅ Repository '{repo_name}' added")
                click.echo(f"   URL: {repo_url}")
                click.echo(f"   Branch: {repo_rev}")

                if not questionary.confirm("Would you like to add another repository?", default=False).ask():
                    break

    db_versions = ["13", "14", "15", "16"]
    click.echo("\n📦 Select the PostgreSQL version (use ↑↓ and ENTER):")
    db_version = questionary.select(
        "PostgreSQL version:", choices=db_versions, default="16"
    ).ask()

    # Ask for the master password
    admin_passwd = click.prompt(
        "Odoo master password", default="admin", hide_input=False
    )

    restart_policy = questionary.select(
        "\n♻️  How would you like to restart the environment?",
        choices=["no", "always", "unless-stopped"],
        default="unless-stopped"
    ).ask()

    # Ports validations
    odoo_port, vsc_port = prompt_ports_until_valid()

    # Containers names
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

    # === Generate files ===
    # First, so the files rendered below (odoo.conf carries admin_passwd) are
    # never exposed to a `git add .` in between
    action, entries = ensure_gitignore(Path(os.getcwd()))
    if action == "created":
        click.echo("🔒 .gitignore created (keeps secrets out of git)")
    elif action == "appended":
        click.echo(f"🔒 .gitignore updated with {len(entries)} secret rule(s)")

    render_template(TEMPLATES_DIR, "Dockerfile.jinja", "Dockerfile", **context)
    render_template(TEMPLATES_DIR, "docker-compose.yaml.jinja", "docker-compose.yaml", **context)

    # Generate config/odoo.conf from templates/config/odoo.conf.jinja
    conf_template = "odoo.conf.jinja"
    conf_output = os.path.join("config", "odoo.conf")
    render_template(CONFIG_TEMPLATE_DIR, conf_template, conf_output, **context)

    vscode_template = "launch.json.jinja"
    vscode_output = os.path.join(".vscode", "launch.json")
    render_template(VSCODE_TEMPLATE_DIR, vscode_template, vscode_output, **context)

    # ========== CONFIGURE ENTERPRISE IF SELECTED ==========
    if odoo_edition == "Enterprise":
        click.echo("\n🔧 Configuring Odoo Enterprise...")
        try:
            project_root = Path(os.getcwd())
            setup_enterprise_edition(project_root)
        except Exception as e:
            click.echo(f"\n⚠️  Warning: Could not fully configure Enterprise: {e}")
            click.echo("You can configure it manually later.")

    # ========== CONFIGURE SSH IF SELECTED ==========
    if use_private_repos and selected_ssh_key:
        click.echo("\n🔐 Configuring access to private repositories...")
        try:
            project_root = Path(os.getcwd())
            dockerfile_path = project_root / "Dockerfile"

            # Copy the SSH key to the build context
            click.echo(f"📋 Copying SSH key: {selected_ssh_key}")
            copy_key_to_build_context(selected_ssh_key, project_root)

            # Modify the Dockerfile to use the SSH key
            if dockerfile_path.exists():
                click.echo("📝 Updating Dockerfile...")
                inject_ssh_into_dockerfile(dockerfile_path, selected_ssh_key)
                click.echo("✅ SSH configuration completed")
                click.echo("💡 Remember: Add your public key to GitHub/GitLab/Bitbucket")
            else:
                click.echo("⚠️  The Dockerfile was not found.")

        except Exception as e:
            click.echo(f"\n⚠️  Warning: SSH could not be fully configured: {e}")
            click.echo("You can configure it manually later.")

    # ========== CONFIGURE GITMAN IF SELECTED ==========
    if use_third_party_repos:
        click.echo("\n📚 Configuring Gitman for third-party repositories...")
        try:
            project_root = Path(os.getcwd())
            gitman_path = project_root / "gitman.yaml"
            odoo_conf_path = project_root / "config" / "odoo.conf"

            click.echo(f"📝 Generating {gitman_path.name}...")
            # Pass the necessary parameters to the function
            generate_gitman_yaml(sources=gitman_sources, output_path=gitman_path)
            click.echo(f"✅ Filed {gitman_path.name} created")

            # Update odoo.conf if there are configured repositories
            if gitman_sources and odoo_conf_path.exists():
                click.echo("📝 Updating odoo.conf with external_addons paths...")
                update_odoo_conf_with_gitman(odoo_conf_path, gitman_sources)
                click.echo("✅ Addons_path configuration updated")
            elif gitman_sources:
                click.echo("⚠️  odoo.conf not found, addons_path update skipped")

            click.echo("\n💡 Next steps:")
            click.echo("   1. Make sure you have gitman installed: pip install gitman")
            click.echo("   2. To install the repositories run: gitman install")
            if use_private_repos and selected_ssh_key:
                click.echo("   3. Verify that your SSH key is added to GitHub/GitLab")

        except Exception as e:
            click.echo(f"\n⚠️  Warning: Gitman could not be fully configured: {e}")
            click.echo("You can configure it manually later.")

    # Final summary
    click.echo("\n" + "="*60)
    click.echo(f"🚀 Project '{project_name}' configured successfully")
    click.echo("="*60)
    click.echo(f"\n📦 Odoo {odoo_version} ({odoo_edition}) + PostgreSQL {db_version}")
    click.echo(f"🌐 Odoo Port: {odoo_port}")
    click.echo(f"🐛 VSC Debug Port: {vsc_port}")

    if use_private_repos and selected_ssh_key:
        click.echo(f"\n🔐 SSH configured with key: {selected_ssh_key}")
        click.echo("   ⚠️  Remember to add your public key to your Git provider.")

    if odoo_edition == "Enterprise":
        click.echo("\n🏢 Enterprise Edition available")

    if use_third_party_repos:
        if gitman_sources:
            click.echo(f"\n📚 Gitman configured with {len(gitman_sources)} repository(ies):")
            for source in gitman_sources:
                click.echo(f"   • {source['name']} ({source['rev']})")
        else:
            click.echo("\n📚 Gitman configured (without initial repositories)")
            click.echo("   💡 Edit gitman.yaml to add repositories")

    click.echo("\n✨ Ready to Start")
    click.echo("   Run: rocketdoo up -d")


if __name__ == "__main__":
    init_project()
