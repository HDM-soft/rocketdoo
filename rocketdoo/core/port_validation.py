# rocketdoo/core/port_validation.py
import socket
import subprocess

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def is_port_used_by_rocketdoo(port: int) -> bool:
    try:
        out = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}} {{.Ports}}']).decode()
        for line in out.splitlines():
            if 'rocketdoo' in line and str(port) in line:
                return True
    except Exception:
        return False
    return False

def find_available_port(start=1024, end=65535):
    for p in range(start, end):
        if not is_port_in_use(p):
            return p
    raise RuntimeError("No available ports found")

def validate_port(port, label="port"):
    try:
        port = int(str(port).strip())
    except Exception:
        raise ValueError(f"Invalid {label} ({port})")
    if is_port_in_use(port):
        # optional: detect rocketdoo usage
        if is_port_used_by_rocketdoo(port):
            raise RuntimeError(f"Port {port} is used by another Rocketdoo project")
        raise RuntimeError(f"Port {port} is in use by another application")
    return port
