# rocketdoo/core/edition_setup.py
import re
from pathlib import Path


def enable_enterprise_in_compose(compose_path: Path):
    """Uncomment the enterprise line in docker-compose.yaml"""
    if not compose_path.exists():
        raise FileNotFoundError(f"File not found: {compose_path}")

    text = compose_path.read_text()

    # Uncomment the enterprise line
    new_text = text.replace(
        "#- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise",
        "- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise",
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
    if "addons_path" in text:
        # If addons_path already exists, add enterprise
        pattern = r"(addons_path\s*=\s*)([^\n]+)"

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


# Modules Odoo Enterprise always ships. Finding none of them means the
# directory is not an Enterprise checkout, whatever else is in it.
_ENTERPRISE_MARKERS = ("web_enterprise", "account_accountant", "sale_subscription")


def validate_enterprise_setup(project_root: Path) -> list[str]:
    """Problems that would stop an Enterprise project from starting.

    check_enterprise_folder() only answers whether the directory exists, so an
    empty `enterprise/` read as ready and Odoo then failed to find the addons.
    Returns actionable messages, empty when the setup looks usable.
    """
    project_root = Path(project_root)
    enterprise = project_root / "enterprise"

    if not enterprise.exists():
        return [
            "The 'enterprise/' directory is missing. Odoo Enterprise addons are not "
            "public: clone them with your subscription credentials "
            "(git clone git@github.com:odoo/enterprise.git enterprise)."
        ]

    if not enterprise.is_dir():
        return ["'enterprise' exists but is not a directory."]

    modules = [d for d in enterprise.iterdir() if d.is_dir() and (d / "__manifest__.py").exists()]
    if not modules:
        return [
            "The 'enterprise/' directory has no Odoo modules in it. A clone that "
            "failed for lack of credentials leaves it empty, and Odoo will start "
            "without the Enterprise addons."
        ]

    issues = []
    names = {d.name for d in modules}
    if not names & set(_ENTERPRISE_MARKERS):
        issues.append(
            f"'enterprise/' holds {len(modules)} module(s) but none of the ones "
            f"Odoo Enterprise always ships ({', '.join(_ENTERPRISE_MARKERS)}). "
            "Check that it is really an Enterprise checkout."
        )
    return issues


def enterprise_addons_count(project_root: Path) -> int:
    """How many Odoo modules the enterprise/ directory holds."""
    enterprise = Path(project_root) / "enterprise"
    if not enterprise.is_dir():
        return 0
    return sum(1 for d in enterprise.iterdir() if d.is_dir() and (d / "__manifest__.py").exists())


def setup_enterprise_edition(project_root: Path):
    """
    Configure the project to use Odoo Enterprise.

    Args:
        project_root: Project root path where docker-compose.yaml and config/ are located
    """
    compose_path = project_root / "docker-compose.yaml"
    odoo_conf_path = project_root / "config" / "odoo.conf"

    try:
        # Enable in docker-compose
        if compose_path.exists():
            enable_enterprise_in_compose(compose_path)

        # Add to odoo.conf
        if odoo_conf_path.exists():
            add_enterprise_to_odoo_conf(odoo_conf_path)

        print("\n📦 Enterprise edition configured successfully")

        # Report what would actually stop Odoo from starting, not just whether
        # the directory exists.
        issues = validate_enterprise_setup(project_root)
        if issues:
            print("\n⚠️  IMPORTANT! The Enterprise setup is incomplete:")
            for issue in issues:
                print(f"   • {issue}")
        else:
            count = enterprise_addons_count(project_root)
            print(f"✅ 'enterprise/' found with {count} module(s)")

    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        raise
    except Exception as e:
        print(f"❌ Error configuring Enterprise: {e}")
        raise
