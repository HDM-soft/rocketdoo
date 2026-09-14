"""Read-only access to the project's PostgreSQL container.

The `pg_database` query and the container lookup used to live only in
`pack_environment`; the GUI module-update feature needs the same data, so
this consolidates them into a single place (like `core/compose.py` did for
`docker compose` in #137) instead of growing a third copy.
"""

import subprocess

from rocketdoo.project_info import read_docker_compose

PSQL_USER = "root"  # POSTGRES_USER set by the docker-compose template


def db_container(compose_data: dict | None = None) -> str | None:
    """Return the `db` service's container name, or None if it can't be read."""
    if compose_data is None:
        compose_data = read_docker_compose()
    try:
        return compose_data["services"]["db"]["container_name"]
    except (KeyError, TypeError):
        return None


def databases_result(compose_data: dict | None = None) -> tuple[list[str], str]:
    """List of user databases plus the reason it came back empty, if any.

    Excludes `postgres` and the templates, matching what `rkd pack` already
    queried. Any failure to reach the container yields an empty list.
    """
    container = db_container(compose_data)
    if not container:
        return [], "no database container configured"

    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                PSQL_USER,
                "-d",
                "postgres",
                "-t",
                "-c",
                "SELECT datname FROM pg_database WHERE datistemplate = false AND datname != 'postgres';",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return [], "docker not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return [], str(exc)

    if result.returncode != 0:
        return [], result.stderr.strip()

    return [line.strip() for line in result.stdout.splitlines() if line.strip()], ""


def list_databases(compose_data: dict | None = None) -> list[str]:
    """List of user databases. Use `databases_result` when the reason matters."""
    return databases_result(compose_data)[0]


def module_states(db: str) -> tuple[dict[str, str], str]:
    """Map `{module_name: state}` read from `ir_module_module` in `db`.

    `db` is not validated here: that is the trust boundary's job (the GUI
    layer), and keeping it in one place avoids the validation being
    duplicated with different criteria. If `db` isn't an Odoo database,
    psql fails and ({}, stderr) is returned.
    """
    container = db_container()
    if not container:
        return {}, "no database container configured"

    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                PSQL_USER,
                "-d",
                db,
                "-t",
                "-A",
                "-F",
                "\t",
                "-c",
                "SELECT name, state FROM ir_module_module;",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return {}, "docker not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return {}, str(exc)

    if result.returncode != 0:
        return {}, result.stderr.strip()

    states = {}
    for line in result.stdout.splitlines():
        name, _, state = line.strip().partition("\t")
        if name and state:
            states[name] = state
    return states, ""
