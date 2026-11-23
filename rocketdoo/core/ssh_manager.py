# rocketdoo/core/ssh_manager.py
"""SSH helpers shared by the Docker build context and the remote deployers."""

import os
import shutil
from pathlib import Path


def resolve_key_path(value: str) -> str:
    """Absolute path of a configured `ssh_key`.

    A bare name means a key in ~/.ssh/: that is what every rkd wizard lists and
    what the user types back. `expanduser()` alone leaves it as a path relative
    to the current directory, so the deploy failed with "SSH key not found" for
    a key that was sitting in ~/.ssh/ all along (#314).
    """
    if not value:
        return ""
    expanded = os.path.expanduser(value)
    if os.path.dirname(expanded):
        return expanded
    return str(Path.home() / ".ssh" / expanded)


# What OpenSSH keeps in ~/.ssh/ that is not a key. Matched on the name up to
# the first dot, case-insensitively, so known_hosts.old and config.bak.20261118
# go with it and `Config` on a case-insensitive filesystem does too (#318).
# Offering these in a key picker produces a configuration that fails at deploy
# time, which is exactly what #314 was about.
_RESERVED_NAMES = frozenset(
    {
        "config",
        "known_hosts",
        "known_hosts2",
        "authorized_keys",
        # The documented default of AuthorizedKeysFile is the pair
        # `.ssh/authorized_keys .ssh/authorized_keys2`.
        "authorized_keys2",
        # gpg.ssh.allowedSignersFile, increasingly common with SSH commit signing.
        "allowed_signers",
        "environment",
        "rc",
    }
)
# .pub is a public key; .ppk is PuTTY's own format, which `ssh -i` does not read.
_NOT_KEY_SUFFIXES = (".pub", ".ppk")


def list_private_keys(ssh_dir: Path = Path.home() / ".ssh"):
    if not ssh_dir.exists():
        return []
    keys = []
    for p in ssh_dir.iterdir():
        # An empty stem is a dotfile (.DS_Store, .bash_history): a private key
        # whose name starts with a dot does not happen in practice.
        stem = p.name.split(".", 1)[0].lower()
        if p.is_file() and stem and stem not in _RESERVED_NAMES and not p.name.endswith(_NOT_KEY_SUFFIXES):
            keys.append(p.name)
    return keys


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


def sshpass_wrap(password: str | None, argv: list[str]) -> tuple[list[str], dict | None]:
    """Return (argv, env) for a command that may need password authentication.

    The password travels in the SSHPASS environment variable of the child
    process, never as an argv element readable through `ps`. Without a
    password the command is returned untouched and env is None, which makes
    subprocess inherit the parent environment exactly as before.
    """
    if not password:
        return argv, None
    return ["sshpass", "-e", *argv], {**os.environ, "SSHPASS": password}
