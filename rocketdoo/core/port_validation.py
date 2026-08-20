# rocketdoo/core/port_validation.py
import re
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


# Directorios que nunca contienen proyectos rocketdoo y encarecen el barrido
_PRUNED_DIRS = {
    'node_modules', '__pycache__', 'venv', 'site-packages', 'dist', 'build',
    'filestore', 'sessions', 'external_addons', 'enterprise', 'postgresql',
}

# Nombres de archivo de compose reconocidos
_COMPOSE_NAMES = ('docker-compose.yaml', 'docker-compose.yml', 'compose.yaml', 'compose.yml')

# Niveles máximos a descender desde cada raíz de búsqueda
_MAX_SCAN_DEPTH = 3


def _host_port(port_mapping) -> int | None:
    """
    Extrae el puerto del host de una entrada de 'ports' de docker-compose.

    Soporta "8069:8069", "8069:8069/tcp", "127.0.0.1:8069:8069", el entero 8069
    y la forma larga {'published': 8069, 'target': 8069}. Devuelve None cuando la
    entrada no reserva un puerto del host (p. ej. "8069" a secas, que publica en
    un puerto aleatorio) o cuando no es un número (p. ej. "${ODOO_PORT}:8069").
    """
    if isinstance(port_mapping, bool):
        return None
    if isinstance(port_mapping, int):
        return port_mapping
    if isinstance(port_mapping, dict):
        try:
            return int(port_mapping.get('published'))
        except (TypeError, ValueError):
            return None
    if not isinstance(port_mapping, str):
        return None

    parts = port_mapping.split(':')
    if len(parts) < 2:
        # Sólo puerto del contenedor: Docker asigna un puerto de host aleatorio
        return None
    try:
        return int(parts[-2].split('/')[0].strip())
    except ValueError:
        return None


def _iter_compose_files(exclude_dir=None, max_depth: int = _MAX_SCAN_DEPTH):
    """
    Recorre los archivos de compose de proyectos hermanos/padres.

    Excluye el proyecto actual (o 'exclude_dir'), poda directorios ocultos y
    pesados, limita la profundidad y no devuelve el mismo archivo dos veces
    aunque las raíces de búsqueda se solapen.
    """
    cwd = Path.cwd().resolve()
    try:
        excluded = Path(exclude_dir).resolve() if exclude_dir else cwd
    except OSError:
        excluded = cwd

    roots = []
    for candidate in (Path.home() / "rocketdoo", cwd.parent, cwd.parent.parent):
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)

    seen = set()
    for root in roots:
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            current = Path(dirpath)

            # No volver a entrar al proyecto actual: sus puertos son propios,
            # no un conflicto (esto es lo que hacía que 'rkd init' no fuera
            # re-ejecutable).
            if current == excluded or excluded in current.parents:
                dirnames[:] = []
                continue

            if len(current.parts) - root_depth >= max_depth:
                dirnames[:] = []
            else:
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith('.') and d not in _PRUNED_DIRS
                ]

            for name in _COMPOSE_NAMES:
                if name not in filenames:
                    continue
                try:
                    compose_file = (current / name).resolve()
                except OSError:
                    continue
                if compose_file not in seen:
                    seen.add(compose_file)
                    yield compose_file


def collect_declared_ports(exclude_dir=None) -> dict[int, str]:
    """
    Devuelve {puerto_host: nombre_de_proyecto} leyendo cada compose una sola vez.

    Un puerto declarado acá NO significa que esté ocupado: puede pertenecer a un
    proyecto detenido. Sirve para advertir y para sugerir puertos sin colisión
    futura.
    """
    declared: dict[int, str] = {}
    for compose_file in _iter_compose_files(exclude_dir):
        try:
            with open(compose_file, 'r', encoding='utf-8') as f:
                content = yaml.safe_load(f)
        except Exception:
            # Compose ilegible o YAML inválido: no es asunto nuestro
            continue

        if not isinstance(content, dict):
            continue
        services = content.get('services')
        if not isinstance(services, dict):
            continue

        project_name = compose_file.parent.name
        for service_config in services.values():
            if not isinstance(service_config, dict):
                continue
            ports = service_config.get('ports')
            if not isinstance(ports, list):
                continue
            for port_mapping in ports:
                host_port = _host_port(port_mapping)
                if host_port is not None:
                    declared.setdefault(host_port, project_name)
    return declared


def _check_port_in_docker_files(port: int, exclude_dir=None) -> tuple[bool, str]:
    """
    Compatibilidad: (está_declarado, proyecto) para un puerto puntual.

    Excluye el proyecto actual. Preferir collect_declared_ports() cuando haya que
    consultar varios puertos.
    """
    project_name = collect_declared_ports(exclude_dir).get(int(port), "")
    return bool(project_name), project_name


def get_port_reservation(port: int, exclude_dir=None) -> str | None:
    """
    Advertencia (no error) si otro proyecto declara el puerto en su compose.

    El puerto está libre ahora — el proyecto que lo declara está detenido —, pero
    habrá conflicto si se levantan los dos a la vez.
    """
    declared, project_name = _check_port_in_docker_files(port, exclude_dir)
    if not declared:
        return None
    return (
        f"Port {port} is also declared by project '{project_name}' (not running now). "
        f"Both projects cannot run at the same time on this port."
    )


def get_port_publisher(port: int) -> str | None:
    """Nombre del contenedor Docker que publica el puerto, o None."""
    try:
        output = subprocess.check_output(
            ['docker', 'ps', '--format', '{{.Names}}\t{{.Ports}}'],
            stderr=subprocess.DEVNULL,
            text=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    # El puerto del host aparece antes de '->': "0.0.0.0:8069->8069/tcp".
    # El límite de dígitos evita que 8069 matchee 18069.
    pattern = re.compile(rf'(?<![0-9]){port}->')
    for line in output.splitlines():
        name, _, ports = line.partition('\t')
        if pattern.search(ports):
            return name
    return None


def is_port_used_by_rocketdoo(port: int) -> bool:
    """Compatibilidad: True si algún contenedor Docker está publicando el puerto."""
    return get_port_publisher(port) is not None


def find_available_port(start=1024, end=65535, exclude_dir=None, declared=None):
    """
    Primer puerto libre desde 'start', evitando los declarados por otros proyectos.

    'declared' permite reutilizar el resultado de collect_declared_ports() y no
    releer los compose por cada puerto candidato.
    """
    if declared is None:
        declared = collect_declared_ports(exclude_dir)

    for p in range(max(int(start), 1024), int(end)):
        if p in declared:
            continue
        if not is_port_in_use(p):
            return p
    raise RuntimeError("No available ports found")


def validate_port(port, label="port"):
    """
    Valida un puerto y lo devuelve normalizado.

    Sólo falla si el puerto está realmente ocupado. Un puerto declarado en el
    compose de otro proyecto detenido no es un error: ver get_port_reservation().
    """
    try:
        port = int(str(port).strip())
    except Exception:
        raise ValueError(f"Invalid {label} ({port})")

    if port < 1024 or port > 65535:
        raise ValueError(f"{label} must be between 1024 and 65535")

    if is_port_in_use(port):
        container = get_port_publisher(port)
        if container:
            raise RuntimeError(f"Port {port} is already published by container '{container}'")
        raise RuntimeError(f"Port {port} is in use by another application")

    return port
