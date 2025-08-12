# rocketdoo/main.py
import click
from pathlib import Path
from . core import port_validation, edition_setup, ssh_manager, gitman_config, utils
from importlib.resources import files
import shutil
from jinja2 import Environment, FileSystemLoader

@click.group()
def cli():
    """Rocketdoo CLI"""
    pass

@cli.command()
@click.argument("dest", default=".", type=click.Path(file_okay=False))
def scaffold(dest):
    """Create a new Rocketdoo project scaffold interactively."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    project_name = click.prompt("Project name", default="my_project")
    odoo_version = click.prompt("Odoo version", default="18.0")
    odoo_port = click.prompt("Odoo port", default=8069, type=int)
    vsc_port = click.prompt("VSCode port", default=8888, type=int)

    # validate ports
    port_validation.validate_port(odoo_port, "Odoo")
    port_validation.validate_port(vsc_port, "VSCode")

    # create project config (source of truth)
    cfg = {
        "project_name": project_name,
        "odoo_version": odoo_version,
        "odoo_port": odoo_port,
        "vsc_port": vsc_port,
    }
    utils.save_project_config(cfg, dest / "rocketdoo.yml")

    # copy templates from package 'templates' folder
    src_templates = files("rocketdoo").joinpath("templates")
    # copy each file/dir
    for item in src_templates.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)

    # render jinja templates inside dest if needed
    env = Environment(loader=FileSystemLoader(str(dest)))
    for tpl_path in dest.rglob("*.jinja"):
        tpl = env.get_template(str(tpl_path.relative_to(dest)))
        content = tpl.render(cfg)
        out_path = tpl_path.with_suffix("")  # remove .jinja
        out_path.write_text(content)
        tpl_path.unlink()  # remove template

    click.echo(f"Rocketdoo project scaffolded at {dest}")

@cli.command()
def version():
    click.echo("Rocketdoo 2.0.0-beta")

if __name__ == "__main__":
    cli()
