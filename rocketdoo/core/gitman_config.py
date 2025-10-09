import yaml
from pathlib import Path


def generate_gitman_yaml(sources=None, output_path=None):
    """
    Genera un archivo gitman.yaml con la estructura e indentación correctas.
    """
    if output_path is None:
        output_path = Path.cwd() / "gitman.yaml"
    
    if sources is None:
        sources = []
    
    config = {
        "location": "external_addons",
        "sources": sources,
        "default_group": "",
        "groups": [],
        "scripts": [
            "sh /usr/lib/python3/dist-packages/odoo/install_dependencies.sh"
        ]
    }

    yaml_text = yaml.safe_dump(config, sort_keys=False, default_flow_style=False, indent=2)

    # 🔧 Arreglo manual: asegurar indentación correcta para `scripts`
    yaml_text = yaml_text.replace(
        "scripts:\n- ",
        "scripts:\n  - "
    )

    with open(output_path, "w") as f:
        f.write(yaml_text)

    return output_path


def update_odoo_conf_with_gitman(odoo_conf_path, gitman_sources):
    """
    Actualiza el addons_path en odoo.conf con las rutas de los repos de Gitman.
    
    Args:
        odoo_conf_path: Path al archivo odoo.conf
        gitman_sources: Lista de diccionarios con la configuración de repos
    """
    if not odoo_conf_path.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {odoo_conf_path}")
    
    lines = odoo_conf_path.read_text().splitlines()
    
    # Generar las rutas de external_addons
    new_paths = [
        f"/usr/lib/python3/dist-packages/odoo/external_addons/{source['name']}"
        for source in gitman_sources
    ]
    
    # Buscar y actualizar la línea addons_path
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith("addons_path"):
            # Extraer el valor actual
            current_value = line.split("=", 1)[1].strip()
            current_paths = [p.strip() for p in current_value.split(",")]
            
            # Agregar solo las rutas que no existan
            for new_path in new_paths:
                if new_path not in current_paths:
                    current_paths.append(new_path)
            
            # Reconstruir la línea
            lines[i] = f"addons_path = {','.join(current_paths)}"
            updated = True
            break
    
    # Si no existía addons_path, agregarla
    if not updated:
        lines.append(f"addons_path = {','.join(new_paths)}")
    
    # Guardar el archivo
    odoo_conf_path.write_text("\n".join(lines) + "\n")


def extract_repo_name_from_url(url):
    """
    Extrae el nombre del repositorio de una URL de Git.
    
    Ejemplos:
        https://github.com/ingadhoc/odoo-argentina.git -> odoo_argentina
        https://gitlab.com/user/my-repo -> my_repo
    """
    # Obtener la última parte de la URL
    url = url.rstrip('/')
    repo_name_with_ext = url.split('/')[-1]
    
    # Remover la extensión .git si existe
    if repo_name_with_ext.endswith('.git'):
        repo_name = repo_name_with_ext[:-4]
    else:
        repo_name = repo_name_with_ext
    
    # Reemplazar guiones por guiones bajos
    return repo_name.replace('-', '_')


def detect_repo_type(url):
    """
    Detecta el tipo de repositorio basándose en la URL.
    Por defecto retorna 'git'.
    """
    url_lower = url.lower()
    
    if '.git' in url_lower or 'github.com' in url_lower or 'gitlab.com' in url_lower or 'bitbucket.org' in url_lower:
        return 'git'
    
    # Puedes agregar más tipos si es necesario (svn, hg, etc.)
    return 'git'