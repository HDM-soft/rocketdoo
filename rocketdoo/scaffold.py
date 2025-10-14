import os
import shutil
import click
from pathlib import Path

def scaffold_project(template="basic", force=False, verbose=False):
    """
    Create the project structure by copying the templates included in Rocketdoo
    to the user's current directory.
    """

    # Absolute path to the /templates folder within rocketdoo
    templates_dir = Path(__file__).resolve().parent / "templates"

    if not templates_dir.exists():
        click.echo("❌ The templates folder was not found inside the package.")
        return

    # Target directory = where the user is currently located
    target_dir = Path.cwd()

    if verbose:
        click.echo(f"📂 Copying templates from: {templates_dir}")
        click.echo(f"➡️  To: {target_dir}")

    for root, dirs, files in os.walk(templates_dir):
        rel_path = Path(root).relative_to(templates_dir)
        dest_dir = target_dir / rel_path

        # Create directory if it does not exist
        dest_dir.mkdir(parents=True, exist_ok=True)

        for file in files:
            src_file = Path(root) / file
            dest_file = dest_dir / file

            if dest_file.exists() and not force:
                click.echo(f"⚠️  Skipping {dest_file} (already exists, use --force to overwrite)")
                continue

            shutil.copy2(src_file, dest_file)
            if verbose:
                click.echo(f"✅ Copied: {dest_file}")

    click.echo("🎉 Project scaffold created successfully.")
