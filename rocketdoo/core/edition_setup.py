# rocketdoo/core/edition_setup.py
from pathlib import Path
import re


def enable_enterprise_in_compose(compose_path: Path):
    """Descomenta la línea de enterprise en docker-compose.yaml"""
    if not compose_path.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {compose_path}")
    
    text = compose_path.read_text()
    
    # Descomentar la línea de enterprise
    new_text = text.replace(
        '#- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise',
        '- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise'
    )
    
    compose_path.write_text(new_text)
    print(f"✅ Configuración Enterprise habilitada en {compose_path.name}")


def add_enterprise_to_odoo_conf(odoo_conf_path: Path):
    """Agrega la ruta de enterprise al addons_path en odoo.conf"""
    if not odoo_conf_path.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {odoo_conf_path}")
    
    text = odoo_conf_path.read_text()
    enterprise_path = "/usr/lib/python3/dist-packages/odoo/enterprise"
    
    # Buscar la línea de addons_path y agregar enterprise
    if 'addons_path' in text:
        # Si ya existe addons_path, agregar enterprise
        pattern = r'(addons_path\s*=\s*)([^\n]+)'
        
        def replace_addons(match):
            prefix = match.group(1)
            current_paths = match.group(2).strip()
            
            # Evitar duplicados
            if enterprise_path in current_paths:
                return match.group(0)
            
            # Agregar enterprise al inicio del addons_path
            return f"{prefix}{enterprise_path},{current_paths}"
        
        text = re.sub(pattern, replace_addons, text)
    else:
        # Si no existe addons_path, agregarlo
        text += f"\naddons_path = {enterprise_path}\n"
    
    odoo_conf_path.write_text(text)
    print(f"✅ Ruta Enterprise agregada a {odoo_conf_path.name}")


def check_enterprise_folder(project_root: Path) -> bool:
    """
    Verifica si existe la carpeta enterprise en la raíz del proyecto.
    
    Args:
        project_root: Ruta raíz del proyecto
        
    Returns:
        True si existe la carpeta, False si no existe
    """
    enterprise_path = project_root / "enterprise"
    return enterprise_path.exists() and enterprise_path.is_dir()


def setup_enterprise_edition(project_root: Path):
    """
    Configura el proyecto para usar Odoo Enterprise.
    
    Args:
        project_root: Ruta raíz del proyecto donde están docker-compose.yaml y config/
    """
    compose_path = project_root / "docker-compose.yaml"
    odoo_conf_path = project_root / "config" / "odoo.conf"
    
    try:
        # Verificar si existe la carpeta enterprise
        has_enterprise_folder = check_enterprise_folder(project_root)
        
        # Habilitar en docker-compose
        if compose_path.exists():
            enable_enterprise_in_compose(compose_path)
        
        # Agregar a odoo.conf
        if odoo_conf_path.exists():
            add_enterprise_to_odoo_conf(odoo_conf_path)
        
        print("\n📦 Edición Enterprise configurada correctamente")
        
        # Advertencia si no existe la carpeta enterprise
        if not has_enterprise_folder:
            print("\n⚠️  ¡IMPORTANTE! No se encontró la carpeta 'enterprise/'")
            print("📁 No te olvides de agregar la carpeta 'enterprise' con los módulos de Odoo Enterprise")
            print("💡 Puedes obtenerla de:")
            print("   • Repositorio oficial de Odoo (requiere suscripción)")
            print("   • git clone https://github.com/odoo/enterprise.git")
        else:
            print("✅ Carpeta 'enterprise/' encontrada")
        
    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        raise
    except Exception as e:
        print(f"❌ Error al configurar Enterprise: {e}")
        raise