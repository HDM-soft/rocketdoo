# rocketdoo/core/edition_setup.py
from pathlib import Path
import re

def enable_enterprise_in_compose(compose_path: Path):
    if not compose_path.exists():
        raise FileNotFoundError(compose_path)
    text = compose_path.read_text()
    # example: uncomment a specific commented line that maps enterprise
    new_text = text.replace('#- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise',
                            '- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise')
    compose_path.write_text(new_text)

def add_enterprise_to_odoo_conf(odoo_conf_path: Path):
    if not odoo_conf_path.exists():
        raise FileNotFoundError(odoo_conf_path)
    text = odoo_conf_path.read_text()
    addons_path = "/usr/lib/python3/dist-packages/odoo/enterprise"
    text = re.sub(r'(addons_path\s*=\s*)(.*)', r'\1\2,' + addons_path, text)
    odoo_conf_path.write_text(text)
