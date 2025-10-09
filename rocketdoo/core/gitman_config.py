import yaml
from pathlib import Path

def generate_gitman_yaml(config: dict, out_path: Path):
    # config expected to have keys: location, sources(list of dicts), default_group, groups
    out = {
        "location": config.get("location", "external_addons"),
        "sources": config.get("sources", []),
        "default_group": config.get("default_group", ""),
        "groups": config.get("groups", []),
    }
    out_path.write_text(yaml.safe_dump(out, sort_keys=False))

def update_odoo_conf_with_gitman(odoo_conf_path: Path, gitman_sources):
    # similar to your modify_odoo_conf, but clearer
    if not odoo_conf_path.exists(): raise FileNotFoundError(odoo_conf_path)
    lines = odoo_conf_path.read_text().splitlines()
    new_paths = [f"/usr/lib/python3/dist-packages/odoo/external_addons/{s['name']}" for s in gitman_sources]
    for i, l in enumerate(lines):
        if l.startswith("addons_path ="):
            cur = l.split(" = ", 1)[1]
            cur_list = cur.split(",")
            merged = cur_list + [p for p in new_paths if p not in cur_list]
            lines[i] = "addons_path = " + ",".join(merged)
            break
    else:
        lines.append("addons_path = " + ",".join(new_paths))
    odoo_conf_path.write_text("\n".join(lines) + "\n")