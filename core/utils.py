# rocketdoo/core/utils.py
from pathlib import Path
import yaml

def save_project_config(config: dict, dest: Path):
    dest.write_text(yaml.safe_dump(config, sort_keys=False))

def load_project_config(path: Path):
    if not path.exists(): return {}
    return yaml.safe_load(path.read_text())
