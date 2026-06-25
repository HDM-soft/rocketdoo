# rocketdoo/core/config_loader.py
import yaml
from pathlib import Path

def get_config_path(base_dir: Path = None) -> Path:
    if base_dir is None:
        base_dir = Path.cwd()
    return base_dir / "rocketdoo.yml"

def load_config(base_dir: Path = None) -> dict:
    cfg_path = get_config_path(base_dir)
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

def save_config(config: dict, base_dir: Path = None):
    cfg_path = get_config_path(base_dir)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
