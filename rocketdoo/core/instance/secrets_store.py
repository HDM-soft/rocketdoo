"""
RocketDoo Instance - persistent secret storage.

Instance passwords must survive across deploys. PostgreSQL only applies
POSTGRES_PASSWORD_FILE while initialising the cluster, so generating a fresh
odoo_pg_pass on a later deploy leaves Odoo unable to authenticate against its
own database — and the value was previously discarded with the temporary build
directory, so it could not be recovered either.

Secrets live in .rkd/secrets/instance_{env}_db.env (0600), alongside the VPS
credentials written by ssh_utils.resolve_auth().
"""
import secrets
import stat
import string
from pathlib import Path

PG_PASS = 'ODOO_PG_PASS'
ADMIN_PASSWD = 'ODOO_ADMIN_PASSWD'

_HEADER = '# Rocketdoo instance secrets - do not commit\n'


def random_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def secrets_file(project_path: Path, env: str) -> Path:
    return Path(project_path) / '.rkd' / 'secrets' / f'instance_{env}_db.env'


def load(project_path: Path, env: str) -> dict[str, str]:
    """Return the stored secrets for an environment ({} if none yet)."""
    path = secrets_file(project_path, env)
    if not path.exists():
        return {}

    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        values[key.strip()] = value.strip()
    return values


def save(project_path: Path, env: str, values: dict[str, str]) -> Path:
    """Merge values into the environment's secret file and return its path."""
    path = secrets_file(project_path, env)
    path.parent.mkdir(parents=True, exist_ok=True)

    merged = {**load(project_path, env), **values}
    body = ''.join(f'{key}={value}\n' for key, value in sorted(merged.items()))
    path.write_text(_HEADER + body)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path
