# rocketdoo/core/utils.py
import shutil
from pathlib import Path

def copy_templates(src_dir: Path, dest_dir: Path):
    """Copia un árbol de plantillas al destino."""
    for item in src_dir.iterdir():
        target = dest_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
