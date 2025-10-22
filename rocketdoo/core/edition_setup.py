# rocketdoo/core/edition_setup.py
from pathlib import Path
import re


def enable_enterprise_in_compose(compose_path: Path):
    """Uncomment the enterprise line in docker-compose.yaml"""
    if not compose_path.exists():
        raise FileNotFoundError(f"File not found: {compose_path}")
    
    text = compose_path.read_text()

    # Uncomment the enterprise line
    new_text = text.replace(
        '#- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise',
        '- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise'
    )
    
    compose_path.write_text(new_text)
    print(f"✅ Enterprise configuration enabled in {compose_path.name}")


def add_enterprise_to_odoo_conf(odoo_conf_path: Path):
    """Add the enterprise path to the addons_path in odoo.conf"""
    if not odoo_conf_path.exists():
        raise FileNotFoundError(f"File not found: {odoo_conf_path}")
    
    text = odoo_conf_path.read_text()
    enterprise_path = "/usr/lib/python3/dist-packages/odoo/enterprise"

    # Search for the addons_path line and add enterprise
    if 'addons_path' in text:
        # If addons_path already exists, add enterprise
        pattern = r'(addons_path\s*=\s*)([^\n]+)'
        
        def replace_addons(match):
            prefix = match.group(1)
            current_paths = match.group(2).strip()

            # Avoid duplicates
            if enterprise_path in current_paths:
                return match.group(0)

            # Add enterprise to the beginning of the addons_path
            return f"{prefix}{enterprise_path},{current_paths}"
        
        text = re.sub(pattern, replace_addons, text)
    else:
        # If addons_path does not exist, add it
        text += f"\naddons_path = {enterprise_path}\n"
    
    odoo_conf_path.write_text(text)
    print(f"✅ Enterprise path added to {odoo_conf_path.name}")


def check_enterprise_folder(project_root: Path) -> bool:
    """
    Check if the enterprise folder exists at the project root.
    
    Args:
        project_root: Project root path

    Returns:
        True if the folder exists, False if it does not
    """
    enterprise_path = project_root / "enterprise"
    return enterprise_path.exists() and enterprise_path.is_dir()


def setup_enterprise_edition(project_root: Path):
    """
    Configure the project to use Odoo Enterprise.
    
    Args:
        project_root: Project root path where docker-compose.yaml and config/ are located
    """
    compose_path = project_root / "docker-compose.yaml"
    odoo_conf_path = project_root / "config" / "odoo.conf"
    
    try:
        # Check if the enterprise folder exists
        has_enterprise_folder = check_enterprise_folder(project_root)

        # Enable in docker-compose
        if compose_path.exists():
            enable_enterprise_in_compose(compose_path)

        # Add to odoo.conf
        if odoo_conf_path.exists():
            add_enterprise_to_odoo_conf(odoo_conf_path)

        print("\n📦 Enterprise edition configured successfully")

        # Warning if the enterprise folder does not exist
        if not has_enterprise_folder:
            print("\n⚠️  IMPORTANT! The ‘enterprise/’ folder was not found.")
            print("📁 Don't forget to add the 'enterprise' folder with the Odoo Enterprise modules")
            print("💡 You can obtain it from:")
            print("   • Official Odoo repository (requires subscription)")
            print("   • git clone https://github.com/odoo/enterprise.git")
        else:
            print("✅ 'enterprise/' folder found")

    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        raise
    except Exception as e:
        print(f"❌ Error configuring Enterprise: {e}")
        raise