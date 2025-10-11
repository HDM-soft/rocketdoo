
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
import click

def render_template(template_name: str, output_path: str, context: dict):
    """
    Renderiza un template Jinja ubicado en rocketdoo/templates y lo guarda con los valores del contexto.
    """
    templates_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(templates_dir))
    template = env.get_template(template_name)

    # Renderizar con contexto
    content = template.render(context)

    # Guardar archivo final
    output_file = Path(output_path)
    output_file.write_text(content, encoding="utf-8")

    click.echo(f"✅ Generado: {output_file}")
