"""
RocketDoo CI - regenerate the gitignored files a Docker build needs
"""

import os
import sys
from pathlib import Path

import click
from rich.console import Console

from rocketdoo.core.addons_path import ensure_addons_path
from rocketdoo.core.edition_setup import add_enterprise_to_odoo_conf
from rocketdoo.core.gitman_config import update_odoo_conf_with_gitman
from rocketdoo.init_project import CONFIG_TEMPLATE_DIR, render_template
from rocketdoo.project_info import get_project_info, project_exists

console = Console()

PG_PASS_TEMPLATE = Path(__file__).resolve().parent / "templates" / "odoo_pg_pass"


@click.group(name="ci")
def ci():
    """CI/CD helpers for a generated Rocketdoo project."""


@ci.command()
@click.option(
    "--admin-passwd",
    default=None,
    help="admin_passwd for a newly created config/odoo.conf (default: admin)",
)
def prepare(admin_passwd):
    """Recreate the gitignored files a Docker build needs, without overwriting them."""
    if not project_exists():
        console.print("[red]No Rocketdoo project detected in this directory.[/red]")
        sys.exit(1)

    project_root = Path.cwd()
    created = []
    kept = []

    pg_pass = project_root / "odoo_pg_pass"
    if pg_pass.exists():
        kept.append("odoo_pg_pass")
    else:
        pg_pass.write_text(PG_PASS_TEMPLATE.read_text())
        created.append("odoo_pg_pass")

    odoo_conf = project_root / "config" / "odoo.conf"
    if odoo_conf.exists():
        kept.append("config/odoo.conf")
    else:
        info = get_project_info()
        context = {**info, "admin_passwd": admin_passwd or "admin"}
        render_template(CONFIG_TEMPLATE_DIR, "odoo.conf.jinja", os.path.join("config", "odoo.conf"), **context)
        created.append("config/odoo.conf")

        if info["odoo_edition"] == "Enterprise":
            add_enterprise_to_odoo_conf(odoo_conf)

        if info["third_party_repos"]:
            update_odoo_conf_with_gitman(odoo_conf, info["third_party_repos"])

    action, changes = ensure_addons_path(project_root)

    if created:
        console.print(f"[green]Created:[/green] {', '.join(created)}")
    if "config/odoo.conf" in created and not admin_passwd:
        console.print("[yellow]admin_passwd set to the default; pass --admin-passwd to change it.[/yellow]")
    if kept:
        console.print(f"[dim]Already present, kept as-is:[/dim] {', '.join(kept)}")
    if action == "updated":
        console.print(f"[green]addons_path updated:[/green] {', '.join(changes)}")
