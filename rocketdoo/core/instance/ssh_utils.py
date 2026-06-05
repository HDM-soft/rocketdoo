"""
RocketDoo Instance - SSH authentication utilities
Shared by DockerInstanceDeployer and NativeInstanceDeployer.

Mirrors the auth pattern from core/deploy/vps.py:
  - ssh_key  → -i key_path
  - password → sshpass -p <pass>  (requires sshpass installed)
"""
import os
import shutil
import stat
from getpass import getpass
from pathlib import Path

from rich.console import Console

console = Console()


def _check_sshpass() -> bool:
    return shutil.which('sshpass') is not None


def resolve_auth(vps_cfg: dict, env: str, project_path: Path) -> dict:
    """
    Return a resolved auth dict ready for use in SSH/rsync commands.

    Input  (from instance.yaml):
        auth_method: ssh_key | password
        ssh_key:     ~/.ssh/my_key       (if ssh_key)
        password:    ${VAR} | literal    (if password)

    Output:
        {
            'method':   'ssh_key' | 'password',
            'key_path': '/home/user/.ssh/my_key',  # expanded, or None
            'password': 'actual_password',          # or None
        }
    """
    method = vps_cfg.get('auth_method', 'ssh_key')

    if method == 'ssh_key':
        raw_key = vps_cfg.get('ssh_key', '~/.ssh/id_rsa')
        key_path = os.path.expanduser(raw_key)
        if not Path(key_path).exists():
            console.print(f'[yellow]⚠ SSH key not found: {key_path}[/yellow]')
        return {'method': 'ssh_key', 'key_path': key_path, 'password': None}

    # password method
    password = vps_cfg.get('password', '')

    # Resolve env var reference  (e.g. ${INSTANCE_STAGE_PASSWORD})
    if password.startswith('${') and password.endswith('}'):
        var_name = password[2:-1]
        password = os.environ.get(var_name, '')
        if not password:
            console.print(f'[yellow]Environment variable {var_name} not set.[/yellow]')

    # Interactive prompt if still empty
    if not password:
        console.print('[yellow]🔐 VPS password required.[/yellow]')
        password = getpass(f'Password for {vps_cfg.get("user")}@{vps_cfg.get("host")}: ')
        if not password:
            raise ValueError('Password authentication selected but no password provided.')

        # Persist securely for this session
        secrets_dir = project_path / '.rkd' / 'secrets'
        secrets_dir.mkdir(parents=True, exist_ok=True)
        secret_file = secrets_dir / f'instance_{env}.env'
        secret_file.write_text(f'INSTANCE_{env.upper()}_PASSWORD={password}\n')
        secret_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        console.print(f'[green]✔[/green] Password stored in {secret_file}')

    if not _check_sshpass():
        raise RuntimeError(
            "Password authentication requires 'sshpass'.\n"
            "Install it with: sudo apt install sshpass"
        )

    return {'method': 'password', 'key_path': None, 'password': password}


def ssh_prefix(auth: dict) -> list[str]:
    """
    Return the command prefix for SSH/rsync based on auth method.

    ssh_key  → []                          (key added via ssh_opts)
    password → ['sshpass', '-p', '<pass>']
    """
    if auth['method'] == 'password':
        return ['sshpass', '-p', auth['password']]
    return []


def ssh_opts(auth: dict, port: int, agent_forward: bool = False) -> list[str]:
    """
    Return SSH -o/-i/-A options (without the leading 'ssh').

    Combine with ssh_prefix() for password auth.
    """
    opts = [
        '-p', str(port),
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
    ]
    if auth['method'] == 'ssh_key' and auth['key_path']:
        opts += ['-i', auth['key_path']]
    if agent_forward:
        opts.append('-A')
    return opts


def build_ssh_cmd(auth: dict, port: int, user: str, host: str,
                  command: str, agent_forward: bool = False) -> list[str]:
    """Full SSH command list ready for subprocess.run()."""
    return [
        *ssh_prefix(auth),
        'ssh',
        *ssh_opts(auth, port, agent_forward),
        f'{user}@{host}',
        command,
    ]


def build_rsync_cmd(auth: dict, port: int, user: str, host: str,
                    local: str, remote: str,
                    extra_opts: list[str] | None = None) -> list[str]:
    """rsync command list ready for subprocess.run()."""
    ssh_e = 'ssh ' + ' '.join(ssh_opts(auth, port))
    return [
        *ssh_prefix(auth),
        'rsync', '-avz', '--delete',
        '-e', ssh_e,
        *(extra_opts or []),
        local,
        f'{user}@{host}:{remote}',
    ]
