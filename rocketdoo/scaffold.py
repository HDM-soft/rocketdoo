import os
import shutil
import click
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

def scaffold_project(template="basic", force=False, verbose=False):
    """
    Crea la estructura del proyecto copiando los templates de Rocketdoo
    y renderizando archivos .jinja con variables contextuales.
    """

    templates_dir = Path(__file__).resolve().parent / "templates"

    if not templates_dir.exists():
        click.echo("❌ La carpeta templates no se encontró dentro del paquete.")
        return

    target_dir = Path.cwd()

    # Contexto de variables para renderizar (puedes ampliarlo según tus necesidades)
    context = {
        "odoo_version": "17.0",
        "vsc_port": "8069",
    }

    env = Environment(loader=FileSystemLoader(str(templates_dir)))

    for root, dirs, files in os.walk(templates_dir):
        rel_path = Path(root).relative_to(templates_dir)
        dest_dir = target_dir / rel_path
        dest_dir.mkdir(parents=True, exist_ok=True)

        for file in files:
            src_file = Path(root) / file

            # Si es plantilla Jinja, renderizar
            if file.endswith(".jinja"):
                template = env.get_template(str(rel_path / file))
                rendered_content = template.render(context)

                dest_file = dest_dir / file.replace(".jinja", "")
                with open(dest_file, "w", encoding="utf-8") as f:
                    f.write(rendered_content)

                if verbose:
                    click.echo(f"🧩 Rendered template: {dest_file}")

            # Si es YAML, reescribirlo limpio
            elif file.endswith((".yml", ".yaml")):
                with open(src_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                with open(dest_dir / file, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, indent=2, width=120)
                if verbose:
                    click.echo(f"🧾 YAML normalized: {dest_dir / file}")

            # Archivos normales
            else:
                dest_file = dest_dir / file
                shutil.copy2(src_file, dest_file)
                if verbose:
                    click.echo(f"✅ Copied: {dest_file}")

    click.echo("🎉 Project scaffold created successfully.")
