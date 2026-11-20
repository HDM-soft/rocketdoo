"""Access to the project's PostgreSQL container: reads and Mailpit writes.

The `pg_database` query and the container lookup used to live only in
`pack_environment`; the GUI module-update feature needs the same data, so
this consolidates them into a single place (like `core/compose.py` did for
`docker compose` in #137) instead of growing a third copy. `rkd mail on/off`
also writes the Rocketdoo `ir.mail_server` row here (#176), so this module is
no longer read-only.
"""

import subprocess
from pathlib import Path

from rocketdoo.core.project_info import read_docker_compose
from rocketdoo.core.service import ProgressCallback

PSQL_USER = "root"  # POSTGRES_USER set by the docker-compose template

MAILPIT_SERVER_NAME = "Mailpit (rkd)"
MAILPIT_SMTP_HOST = "mailpit"
MAILPIT_SMTP_PORT = 1025
MAILPIT_SEQUENCE = 1  # Odoo's default is 10: Mailpit must win the tie


def db_container(compose_data: dict | None = None) -> str | None:
    """Return the `db` service's container name, or None if it can't be read."""
    if compose_data is None:
        compose_data = read_docker_compose()
    try:
        return compose_data["services"]["db"]["container_name"]
    except (KeyError, TypeError):
        return None


def _psql(db: str, sql: str, *flags: str, compose_data: dict | None = None) -> tuple[str, str]:
    """Run `sql` against `db` via `docker exec ... psql`. Returns (stdout, error).

    A non-empty error means the query did not run: stdout is not meaningful.
    Shared by every query/write in this module so the argv and the error
    translation (missing docker, unreachable container, psql failure) live in
    one place.
    """
    container = db_container(compose_data)
    if not container:
        return "", "no database container configured"

    try:
        result = subprocess.run(
            ["docker", "exec", container, "psql", "-U", PSQL_USER, "-d", db, *flags, "-c", sql],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return "", "docker not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return "", str(exc)

    if result.returncode != 0:
        return "", result.stderr.strip()

    return result.stdout, ""


def databases_result(compose_data: dict | None = None) -> tuple[list[str], str]:
    """List of user databases plus the reason it came back empty, if any.

    Excludes `postgres` and the templates, matching what `rkd pack` already
    queried. Any failure to reach the container yields an empty list.
    """
    stdout, error = _psql(
        "postgres",
        "SELECT datname FROM pg_database WHERE datistemplate = false AND datname != 'postgres';",
        "-t",
        compose_data=compose_data,
    )
    if error:
        return [], error

    return [line.strip() for line in stdout.splitlines() if line.strip()], ""


def list_databases(compose_data: dict | None = None) -> list[str]:
    """List of user databases. Use `databases_result` when the reason matters."""
    return databases_result(compose_data)[0]


def pg_major_version(compose_data: dict | None = None) -> int:
    """Major version of the project's PostgreSQL server, or 0 if it cannot be read.

    Asked of the running server rather than read from the compose image tag:
    the tag can be `latest` or carry a suffix, and the dump is made by the
    pg_dump that ships in this very container.
    """
    stdout, error = _psql("postgres", "SHOW server_version_num;", "-t", "-A", compose_data=compose_data)
    digits = stdout.strip()
    return int(digits) // 10000 if not error and digits.isdigit() else 0


def database_bytes(db: str, compose_data: dict | None = None) -> int:
    """On-disk size of `db`; an upper bound for its compressed dump. 0 if unreadable."""
    stdout, error = _psql(db, "SELECT pg_database_size(current_database());", "-t", "-A", compose_data=compose_data)
    digits = stdout.strip()
    return int(digits) if not error and digits.isdigit() else 0


def filestore_bytes(odoo_container: str, db: str) -> int:
    """Size of the filestore of `db` inside the Odoo container; 0 if absent or unreadable."""
    for template in FILESTORE_PATH_TEMPLATES:
        try:
            result = subprocess.run(
                ["docker", "exec", odoo_container, "du", "-sk", template.format(db=db)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return 0
        head = result.stdout.split("\t")[0].strip()
        if result.returncode == 0 and head.isdigit():
            return int(head) * 1024
    return 0


def module_states(db: str) -> tuple[dict[str, str], str]:
    """Map `{module_name: state}` read from `ir_module_module` in `db`.

    `db` is not validated here: that is the trust boundary's job (the GUI
    layer), and keeping it in one place avoids the validation being
    duplicated with different criteria. If `db` isn't an Odoo database,
    psql fails and ({}, stderr) is returned.
    """
    stdout, error = _psql(db, "SELECT name, state FROM ir_module_module;", "-t", "-A", "-F", "\t")
    if error:
        return {}, error

    states = {}
    for line in stdout.splitlines():
        name, _, state = line.strip().partition("\t")
        if name and state:
            states[name] = state
    return states, ""


def mail_servers(db: str) -> tuple[list[dict], str]:
    """Every ir.mail_server row in `db`, ordered the way Odoo orders them.

    All rows are returned, active or not, so `rkd mail status` can tell
    "archived" from "never created" with a single query.
    """
    stdout, error = _psql(
        db,
        "SELECT id, name, sequence, smtp_host, active FROM ir_mail_server ORDER BY sequence, id;",
        "-t",
        "-A",
        "-F",
        "\t",
    )
    if error:
        return [], error

    servers = []
    for line in stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            continue
        raw_id, name, raw_sequence, smtp_host, raw_active = fields
        try:
            server_id = int(raw_id)
            sequence = int(raw_sequence)
        except ValueError:
            continue
        servers.append(
            {
                "id": server_id,
                "name": name,
                "sequence": sequence,
                "smtp_host": smtp_host,
                "active": raw_active == "t",
            }
        )
    return servers, ""


def enable_mailpit_server(db: str, compose_data: dict | None = None) -> str:
    """Create or reactivate the Rocketdoo mail server. Returns '' on success.

    Matched by name + smtp_host, not by id: there is no UNIQUE constraint on
    name to key an UPSERT off. Idempotent by construction, so callers can run
    it unconditionally instead of tracking local state.
    """
    sql = (
        "WITH updated AS ("
        "UPDATE ir_mail_server "
        f"SET active = true, smtp_port = {MAILPIT_SMTP_PORT}, sequence = {MAILPIT_SEQUENCE}, write_date = now() "
        f"WHERE name = '{MAILPIT_SERVER_NAME}' AND smtp_host = '{MAILPIT_SMTP_HOST}' "
        "RETURNING id"
        ") "
        "INSERT INTO ir_mail_server "
        "(name, smtp_host, smtp_port, smtp_encryption, smtp_authentication, sequence, active, create_date, write_date) "
        f"SELECT '{MAILPIT_SERVER_NAME}', '{MAILPIT_SMTP_HOST}', {MAILPIT_SMTP_PORT}, 'none', 'login', "
        f"{MAILPIT_SEQUENCE}, true, now(), now() "
        "WHERE NOT EXISTS (SELECT 1 FROM updated);"
    )
    _, error = _psql(db, sql, compose_data=compose_data)
    return error


def disable_mailpit_server(db: str, compose_data: dict | None = None) -> tuple[int, str]:
    """Archive the Rocketdoo mail server. Returns (rows archived, error).

    Only rows matching both name and smtp_host are touched, and only
    archived: never DELETE, so a record a user renamed or repurposed is
    never at risk even if it collides on one of the two conditions.
    """
    sql = (
        "UPDATE ir_mail_server SET active = false, write_date = now() "
        f"WHERE name = '{MAILPIT_SERVER_NAME}' AND smtp_host = '{MAILPIT_SMTP_HOST}' "
        "RETURNING id;"
    )
    # -q suppresses psql's status line: without it, `RETURNING id` with zero
    # rows still prints "UPDATE 0", which counts as a row and makes the command
    # report an archive that never happened.
    stdout, error = _psql(db, sql, "-q", "-t", "-A", compose_data=compose_data)
    if error:
        return 0, error

    # Count returned ids, not non-empty lines: psql's status line ("UPDATE 0")
    # is not a row, and counting it reports an archive that never happened.
    return len([line for line in stdout.splitlines() if line.strip().isdigit()]), ""


FILESTORE_PATH_TEMPLATES = [
    "/var/lib/odoo/.local/share/Odoo/filestore/{db}",
    "/var/lib/odoo/filestore/{db}",
]


def dump_database(db_container_name: str, db_name: str, output_path: Path, report: ProgressCallback) -> bool:
    """Runs pg_dump inside the container and saves the result to output_path.

    Returns True if the backup succeeded.
    """
    report(f"Running pg_dump for '{db_name}'...")
    try:
        with open(output_path, "wb") as f:
            result = subprocess.run(
                ["docker", "exec", db_container_name, "pg_dump", "-U", "root", "--format=custom", db_name],
                stdout=f,
                stderr=subprocess.PIPE,
            )
        if result.returncode != 0:
            report(f"pg_dump error: {result.stderr.decode()}", "warn")
            return False
        size_mb = output_path.stat().st_size / (1024 * 1024)
        report(f"Backup saved: {output_path.name} ({size_mb:.1f} MB)", "ok")
        return True
    except Exception as exc:
        report(f"Exception during database backup: {exc}", "warn")
        return False


def dump_filestore(odoo_container: str, db_name: str, output_path: Path, report: ProgressCallback) -> tuple[bool, str | None]:
    """Copies and compresses the Odoo filestore from the container to the host.

    Returns (success, filestore_base). Supports multiple filestore layouts.
    """
    report("Copying filestore from container...")

    filestore_path = None
    filestore_base = None

    try:
        for template in FILESTORE_PATH_TEMPLATES:
            path = template.format(db=db_name)
            check = subprocess.run(["docker", "exec", odoo_container, "test", "-d", path], capture_output=True)
            if check.returncode == 0:
                filestore_path = path
                filestore_base = str(Path(path).parent)
                break

        if not filestore_path:
            report(f"Filestore not found for database {db_name} in any known location - skipping.", "warn")
            return True, None  # Not fatal

        report(f"Detected filestore path: {filestore_path}")

        with open(output_path, "wb") as f:
            result = subprocess.run(
                ["docker", "exec", odoo_container, "tar", "-czf", "-", "-C", filestore_base, db_name],
                stdout=f,
                stderr=subprocess.PIPE,
            )

        if result.returncode != 0:
            report(f"Error copying filestore: {result.stderr.decode()}", "warn")
            return False, filestore_base

        size_mb = output_path.stat().st_size / (1024 * 1024)
        report(f"Filestore compressed: {output_path.name} ({size_mb:.1f} MB)", "ok")
        return True, filestore_base

    except Exception as exc:
        report(f"Exception during filestore backup: {exc}", "warn")
        return False, None
