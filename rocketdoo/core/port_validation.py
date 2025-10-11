# rocketdoo/core/port_validation.py
import socket
import subprocess
import platform
import sys
import os
import yaml
from pathlib import Path

def is_port_in_use(port: int) -> bool:
    """
    Verifica si un puerto está en uso usando el método más confiable según el SO.
    """
    # Primero intenta con socket (rápido para conexiones activas)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(('localhost', port)) == 0:
            return True
    
    # Si socket no detectó nada, usa comandos del SO para verificar LISTEN
    system = platform.system()
    
    try:
        if system == "Windows":
            return _check_port_windows(port)
        elif system in ["Linux", "Darwin"]:  # Darwin es macOS
            return _check_port_unix(port)
    except Exception as e:
        print(f"⚠️  Advertencia al verificar puerto {port}: {e}", file=sys.stderr)
        return True
    
    return False


def _check_port_windows(port: int) -> bool:
    """Verifica si un puerto está en uso en Windows usando netstat."""
    try:
        output = subprocess.check_output(
            ['netstat', '-ano', '/p', 'TCP'],
            stderr=subprocess.DEVNULL,
            text=True
        )
        for line in output.splitlines():
            if f':{port}' in line and 'LISTENING' in line:
                return True
    except subprocess.CalledProcessError:
        pass
    return False


def _check_port_unix(port: int) -> bool:
    """Verifica si un puerto está en uso en Linux/macOS usando lsof."""
    try:
        output = subprocess.check_output(
            ['lsof', '-i', f':{port}'],
            stderr=subprocess.DEVNULL,
            text=True
        )
        return len(output.strip()) > 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Si lsof no está disponible, intenta con netstat
        try:
            output = subprocess.check_output(
                ['netstat', '-tlnp'],
                stderr=subprocess.DEVNULL,
                text=True
            )
            for line in output.splitlines():
                if f':{port}' in line and 'LISTEN' in line:
                    return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return False


def _check_port_in_docker_files(port: int) -> tuple[bool, str]:
    """
    Busca el puerto en archivos docker-compose.yaml en directorios hermanos/padres.
    Retorna (está_en_uso, proyecto_encontrado)
    """
    # Buscar en directorios hermanos y padres
    search_paths = [
        Path.home() / "rocketdoo",  # En home
        Path.cwd().parent,           # Directorio padre
        Path.cwd().parent.parent,    # Abuelo
    ]
    
    for search_path in search_paths:
        if not search_path.exists():
            continue
            
        for docker_file in search_path.rglob("docker-compose.yaml"):
            try:
                with open(docker_file, 'r', encoding='utf-8') as f:
                    content = yaml.safe_load(f)
                    if not content or 'services' not in content:
                        continue
                    
                    for service_name, service_config in content.get('services', {}).items():
                        if 'ports' not in service_config:
                            continue
                        
                        ports = service_config['ports']
                        if isinstance(ports, list):
                            for port_mapping in ports:
                                # Parsear "8069:8069" o "8069:8069/tcp"
                                if isinstance(port_mapping, str):
                                    host_port = port_mapping.split(':')[0].split('/')[0]
                                    try:
                                        if int(host_port) == port:
                                            project_name = docker_file.parent.name
                                            return True, project_name
                                    except ValueError:
                                        pass
            except Exception as e:
                # Ignorar archivos que no se puedan leer
                pass
    
    return False, ""


def is_port_used_by_rocketdoo(port: int) -> bool:
    """Detecta si un puerto está siendo usado por un contenedor Rocketdoo."""
    try:
        output = subprocess.check_output(
            ['docker', 'ps', '--format', '{{.Names}} {{.Ports}}'],
            stderr=subprocess.DEVNULL,
            text=True
        )
        for line in output.splitlines():
            if 'rocketdoo' in line.lower() and str(port) in line:
                return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return False


def find_available_port(start=1024, end=65535):
    """Busca un puerto disponible comenzando desde 'start'."""
    for p in range(start, end):
        if not is_port_in_use(p) and not _check_port_in_docker_files(p)[0]:
            return p
    raise RuntimeError("No available ports found")


def validate_port(port, label="port"):
    """Valida si un puerto está disponible."""
    try:
        port = int(str(port).strip())
    except Exception:
        raise ValueError(f"Invalid {label} ({port})")
    
    if port < 1024 or port > 65535:
        raise ValueError(f"{label} must be between 1024 and 65535")
    
    # Verificar si está en uso actualmente
    if is_port_in_use(port):
        if is_port_used_by_rocketdoo(port):
            raise RuntimeError(f"Port {port} is used by another Rocketdoo project")
        raise RuntimeError(f"Port {port} is in use by another application")
    
    # Verificar si está reservado en otros docker-compose.yaml
    port_in_docker_file, project_name = _check_port_in_docker_files(port)
    if port_in_docker_file:
        raise RuntimeError(f"Port {port} is reserved in project '{project_name}'")
    
    return port