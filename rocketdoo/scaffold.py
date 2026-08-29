import shutil
from pathlib import Path

import click

from rocketdoo.core.gitignore_manager import ensure_gitignore

# Rendered into .gitignore instead of being copied verbatim
GITIGNORE_TEMPLATE = ".gitignore.jinja"

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

    # Usar shutil.copytree con dirs_exist_ok para copiar todo incluyendo ocultos
    try:
        for item in templates_dir.iterdir():
            src = templates_dir / item.name
            dest = target_dir / item.name

            if item.name == GITIGNORE_TEMPLATE:
                # Handled after the loop so credentials are covered from the start
                continue

            if src.is_dir():
                # Copy entire directory (including hidden files)
                if dest.exists():
                    if force:
                        if verbose:
                            click.echo(f"🔄 Overwriting directory: {dest}")
                        shutil.rmtree(dest)
                        shutil.copytree(src, dest)
                    else:
                        click.echo(f"⚠️  Skipping {dest} (already exists, use --force to overwrite)")
                else:
                    shutil.copytree(src, dest)
                    if verbose:
                        click.echo(f"✅ Copied directory: {dest}")
            else:
                # Copy individual file
                if dest.exists() and not force:
                    click.echo(f"⚠️  Skipping {dest} (already exists, use --force to overwrite)")
                    continue

                shutil.copy2(src, dest)
                if verbose:
                    click.echo(f"✅ Copied file: {dest}")

        action, entries = ensure_gitignore(target_dir)
        if action == "created":
            click.echo("🔒 .gitignore created (keeps secrets out of git)")
        elif action == "appended":
            click.echo(f"🔒 .gitignore updated with {len(entries)} secret rule(s)")
        elif verbose:
            click.echo("🔒 .gitignore already covers Rocketdoo secrets")

        click.echo("🎉 Project scaffold created successfully.")

    except Exception as e:
        click.echo(f"❌ Error during scaffolding: {e}")
