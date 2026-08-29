# rocketdoo/core/ssh_manager.py
"""SSH helpers shared by the Docker build context and the remote deployers."""

import os
import shutil
from pathlib import Path


def list_private_keys(ssh_dir: Path = Path.home() / ".ssh"):
    if not ssh_dir.exists():
        return []
    return [p.name for p in ssh_dir.iterdir() if p.is_file() and not p.name.endswith(".pub")]


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
    text = text.replace("#COPY ./.ssh/rsa /root/.ssh/id_rsa", f"COPY ./.ssh/{key_name} /root/.ssh/{key_name}")
    text = text.replace("#RUN chmod 700 /root/.ssh/id_rsa", f"RUN chmod 700 /root/.ssh/{key_name}")
    text = text.replace(
        '#RUN echo "StrictHostKeyChecking no" >> /root/.ssh/config', 'RUN echo "StrictHostKeyChecking no" >> /root/.ssh/config'
    )
    dockerfile_path.write_text(text)


def check_sshpass() -> bool:
    """Whether `sshpass` is installed, which password authentication requires."""
    return shutil.which("sshpass") is not None


def env_ref_name(value: str) -> str | None:
    """The variable name in a `${VAR}` reference, or None if it is not one.

    deploy.yaml and instance.yaml both let a password be written as
    `${VPS_PASSWORD}` instead of in plain text.
    """
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return value[2:-1]
    return None


def resolve_env_ref(value: str) -> str:
    """Resolve a `${VAR}` reference against the environment.

    A value that is not a reference comes back unchanged. An unset variable
    resolves to an empty string so callers can fall back to prompting.
    """
    name = env_ref_name(value)
    if name is None:
        return value
    return os.environ.get(name, "")
