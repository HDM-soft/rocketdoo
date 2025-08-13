# rocketdoo/core/ssh_manager.py
from pathlib import Path
import shutil, os

def list_private_keys(ssh_dir: Path = Path.home() / ".ssh"):
    if not ssh_dir.exists(): return []
    return [p.name for p in ssh_dir.iterdir() if p.is_file() and not p.name.endswith('.pub')]

def copy_key_to_build_context(key_name: str, dockerfile_dir: Path):
    src = Path.home() / ".ssh" / key_name
    dst_dir = dockerfile_dir / ".ssh"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / key_name
    shutil.copy2(src, dst)
    return dst

def inject_ssh_into_dockerfile(dockerfile_path: Path, key_name: str):
    text = dockerfile_path.read_text()
    text = text.replace("#RUN mkdir -p /root/.ssh", "RUN mkdir -p /root/.ssh")
    text = text.replace("#COPY ./.ssh/rsa /root/.ssh/id_rsa",
                        f"COPY ./.ssh/{key_name} /root/.ssh/{key_name}")
    text = text.replace("#RUN chmod 700 /root/.ssh/id_rsa",
                        f"RUN chmod 700 /root/.ssh/{key_name}")
    text = text.replace('#RUN echo "StrictHostKeyChecking no" >> /root/.ssh/config',
                        'RUN echo "StrictHostKeyChecking no" >> /root/.ssh/config')
    dockerfile_path.write_text(text)
