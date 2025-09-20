import os
import shutil
import click
from pathlib import Path

def scaffold_project(template="basic", force=False, verbose=False):
    """
    Crea la estructura del proyecto copiando los templates incluidos en Rocketdoo
    hacia el directorio actual del usuario.
    """

    # Ruta absoluta a la carpeta /templates dentro de rocketdoo
    templates_dir = Path(__file__).resolve().parent / "templates"

    if not templates_dir.exists():
        click.echo("❌ No se encontró la carpeta de templates dentro del paquete.")
        return

    # Directorio de destino = donde el usuario esté parado
    target_dir = Path.cwd()

    if verbose:
        click.echo(f"📂 Copiando templates desde: {templates_dir}")
        click.echo(f"➡️  Hacia: {target_dir}")

    for root, dirs, files in os.walk(templates_dir):
        rel_path = Path(root).relative_to(templates_dir)
        dest_dir = target_dir / rel_path

        # Crear directorio si no existe
        dest_dir.mkdir(parents=True, exist_ok=True)

        for file in files:
            src_file = Path(root) / file
            dest_file = dest_dir / file

            if dest_file.exists() and not force:
                click.echo(f"⚠️  Saltando {dest_file} (ya existe, usar --force para sobrescribir)")
                continue

            shutil.copy2(src_file, dest_file)
            if verbose:
                click.echo(f"✅ Copiado: {dest_file}")

    click.echo("🎉 Scaffold del proyecto creado exitosamente.")
