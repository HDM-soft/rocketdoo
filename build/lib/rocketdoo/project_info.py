"""
Módulo para leer y extraer información de configuración del proyecto Rocketdoo
"""
import os
import yaml
from pathlib import Path
from typing import Dict, Optional, List


def read_docker_compose() -> Optional[Dict]:
    """Lee el archivo docker-compose.yaml"""
    compose_path = Path.cwd() / "docker-compose.yaml"
    if not compose_path.exists():
        compose_path = Path.cwd() / "docker-compose.yml"
    
    if compose_path.exists():
        try:
            with open(compose_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception:
            return None
    return None


def read_gitman() -> Optional[Dict]:
    """Lee el archivo gitman.yaml"""
    gitman_path = Path.cwd() / "gitman.yaml"
    if gitman_path.exists():
        try:
            with open(gitman_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception:
            return None
    return None


def read_odoo_conf() -> Optional[Dict]:
    """Lee el archivo config/odoo.conf"""
    conf_path = Path.cwd() / "config" / "odoo.conf"
    if conf_path.exists():
        try:
            config = {}
            with open(conf_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = value.strip()
            return config
        except Exception:
            return None
    return None


def read_dockerfile() -> Optional[str]:
    """Reads the Dockerfile"""
    dockerfile_path = Path.cwd() / "Dockerfile"
    if dockerfile_path.exists():
        try:
            with open(dockerfile_path, 'r') as f:
                return f.read()
        except Exception:
            return None
    return None


def extract_odoo_version_from_dockerfile() -> Optional[str]:
    """Extracts Odoo version from Dockerfile FROM instruction"""
    dockerfile = read_dockerfile()
    if dockerfile:
        for line in dockerfile.split('\n'):
            line = line.strip()
            # Check for FROM odoo:X.X or FROM odoo:X.X-something
            if line.startswith('FROM') and 'odoo:' in line.lower():
                try:
                    # Extract version after 'odoo:'
                    # Examples: FROM odoo:18.0 or FROM odoo:18.0-alpine
                    parts = line.lower().split('odoo:')
                    if len(parts) > 1:
                        version_part = parts[1].split()[0]  # Get first part after odoo:
                        # Remove any non-version characters (like -alpine, -slim, etc)
                        version = version_part.split('-')[0].strip()
                        return version
                except Exception as e:
                    print(f"Debug: Error parsing Dockerfile line: {line}, error: {e}")
    return None


def detect_ssh_key_usage() -> Optional[str]:
    """Detects if an SSH key is being used in Dockerfile"""
    dockerfile = read_dockerfile()
    if dockerfile:
        # Search for lines mentioning SSH keys
        for line in dockerfile.split('\n'):
            if 'COPY' in line and ('.ssh' in line or 'id_' in line):
                # Extract file name
                parts = line.split()
                if len(parts) >= 3:
                    ssh_file = parts[1]
                    return os.path.basename(ssh_file)
    return None


def detect_enterprise_edition() -> bool:
    """Detects if Enterprise edition is enabled by checking docker-compose volumes"""
    compose_data = read_docker_compose()
    if compose_data and 'services' in compose_data:
        web_service = compose_data['services'].get('web')
        if web_service:
            volumes = web_service.get('volumes', [])
            for volume in volumes:
                if isinstance(volume, str):
                    # Check if enterprise volume exists and is NOT commented
                    volume_stripped = volume.strip()
                    if 'enterprise' in volume_stripped.lower():
                        # If line doesn't start with #, it's uncommented (Enterprise)
                        if not volume_stripped.startswith('#'):
                            return True
    
    # Also check in Dockerfile for enterprise references
    dockerfile = read_dockerfile()
    if dockerfile:
        for line in dockerfile.split('\n'):
            line_stripped = line.strip()
            # Look for lines that copy or reference enterprise and are not commented
            if 'enterprise' in line_stripped.lower() and not line_stripped.startswith('#'):
                # Common patterns: COPY enterprise, RUN ... enterprise, etc.
                if any(keyword in line_stripped.upper() for keyword in ['COPY', 'ADD', 'RUN']):
                    return True
    
    return False


def get_project_info() -> Dict:
    """Collects all project information"""
    info = {
        "project_name": None,
        "odoo_version": None,
        "odoo_edition": "Community",  # Default
        "db_version": None,
        "odoo_container": None,
        "db_container": None,
        "odoo_port": None,
        "vsc_port": None,
        "restart_policy": None,
        "use_private_repos": False,
        "ssh_key": None,
        "use_third_party_repos": False,
        "third_party_repos": [],
        "db_port": None,
        "admin_passwd": None
    }
    
    # 1. READ ODOO VERSION FROM DOCKERFILE (priority)
    dockerfile_version = extract_odoo_version_from_dockerfile()
    if dockerfile_version:
        info['odoo_version'] = dockerfile_version
    
    # 2. DETECT ENTERPRISE EDITION
    if detect_enterprise_edition():
        info['odoo_edition'] = 'Enterprise'
    
    # 3. READ docker-compose.yaml
    compose_data = read_docker_compose()
    if compose_data:
        # Project name - from "name" key in docker-compose
        if 'name' in compose_data:
            info['project_name'] = compose_data['name']
        
        # Services
        if 'services' in compose_data:
            services = compose_data['services']
            
            # Find "web" (Odoo) and "db" (PostgreSQL) services
            web_service = services.get('web')
            db_service = services.get('db')
            
            # === WEB SERVICE (ODOO) INFO ===
            if web_service:
                # Container name
                info['odoo_container'] = web_service.get('container_name', 'odoo-unknown')
                
                # Restart policy
                info['restart_policy'] = web_service.get('restart', 'no')
                
                # Ports - format "8069:8069" or "8888:8888"
                ports = web_service.get('ports', [])
                port_list = []
                for port_mapping in ports:
                    port_str = str(port_mapping)
                    if ':' in port_str:
                        # Extract first port (before colon)
                        host_port = port_str.split(':')[0].strip()
                        port_list.append(host_port)
                
                # First port is Odoo, second is VSCode debug
                if len(port_list) >= 1:
                    info['odoo_port'] = port_list[0]
                if len(port_list) >= 2:
                    info['vsc_port'] = port_list[1]
                
                # Odoo version - from "image" field (fallback if not in Dockerfile)
                if not info['odoo_version']:
                    image = web_service.get('image', '')
                    if 'odoo:' in image.lower():
                        # Format: "odoo:18.0" or "test2" (custom image)
                        if ':' in image:
                            version_part = image.split(':')[1].split('-')[0].strip()
                            # Only use if it looks like a version (contains numbers and dots)
                            if any(char.isdigit() for char in version_part):
                                info['odoo_version'] = version_part
            
            # === DB SERVICE (POSTGRESQL) INFO ===
            if db_service:
                # Container name
                info['db_container'] = db_service.get('container_name', 'db-unknown')
                
                # PostgreSQL version - from "image" field
                image = db_service.get('image', '')
                if 'postgres:' in image:
                    # Format: "postgres:14" -> extract "14"
                    version = image.split('postgres:')[1].split('-')[0].strip()
                    info['db_version'] = version
                
                # PostgreSQL port (if exposed)
                ports = db_service.get('ports', [])
                if ports:
                    for port_mapping in ports:
                        port_str = str(port_mapping)
                        if ':' in port_str:
                            host_port = port_str.split(':')[0].strip()
                            info['db_port'] = host_port
                            break
    
    # 4. READ odoo.conf to get admin_passwd
    odoo_conf = read_odoo_conf()
    if odoo_conf:
        info['admin_passwd'] = odoo_conf.get('admin_passwd', None)
    
    # 5. DETECT SSH USAGE
    ssh_key = detect_ssh_key_usage()
    if ssh_key:
        info['use_private_repos'] = True
        info['ssh_key'] = ssh_key
    
    # 6. READ gitman.yaml
    gitman_data = read_gitman()
    if gitman_data and 'sources' in gitman_data:
        sources = gitman_data['sources']
        if sources and len(sources) > 0:
            info['use_third_party_repos'] = True
            info['third_party_repos'] = [
                {
                    'name': src.get('name', 'unknown'),
                    'repo': src.get('repo', ''),
                    'rev': src.get('rev', '')
                }
                for src in sources
            ]
    
    return info


def project_exists() -> bool:
    """Checks if a Rocketdoo project exists in the current directory"""
    required_files = ['docker-compose.yaml', 'Dockerfile']
    for file in required_files:
        if not (Path.cwd() / file).exists() and not (Path.cwd() / file.replace('.yaml', '.yml')).exists():
            if file == 'docker-compose.yaml':
                continue
            return False
    return (Path.cwd() / 'docker-compose.yaml').exists() or (Path.cwd() / 'docker-compose.yml').exists()