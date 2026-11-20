"""Seeding an instance with a database taken from the developer's machine.

Pure, like `proxy.py` and `gitman_remote.py`: each function returns the
`bash -s` script a deployer sends, and none of them touches a connection.

The dump and the filestore tarball are expected under `seed_dir` on the
server, under the names DUMP_FILE and FILESTORE_FILE; shipping them is the
caller's job. The scripts are wrapped in braces for the reason documented in
`deployer_docker._ensure_database`: bash reads a compound command whole, so
nothing `docker compose` does with the stdin it inherits can eat the rest.

The idempotence mark lives in the seeded database, never in a local file: a
file under `.rkd/` is lost on a clean clone of the project, and the next
deploy would restore the stale development dump over the customer's
production. In the database, the mark disappears exactly when it should --
when the database does.
"""

import shlex

from .config_manager import invalid_identifiers

DUMP_FILE = "db.dump"
FILESTORE_FILE = "filestore.tar.gz"
FILESTORE_ROOT = "/var/lib/odoo/filestore"
SEEDED_MARK = "rkd.seeded_from"
RESTORE_WARNING = "warning: pg_restore ignored some statements"
STATE_PREFIX = "rkd_seed_state="

_SQL_HEREDOC = "RKD_SQL_EOF"
_ENVIRONMENTS = ("stage", "prod")

_MAILPIT_NAME = "Mailpit (rkd)"
_MAILPIT_HOST = "mailpit"

_NEW_UUID = "uuid_in(md5(random()::text || clock_timestamp()::text)::cstring)::text"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def neutralize_sql(env: str, source_db: str, domain: str = "") -> str:
    """The SQL that makes a restored development database safe to run on `env`.

    The two lists are different on purpose. Towards stage the whole database
    is neutralized: crons, outgoing and incoming mail and payment providers
    are switched off, because stage must not act on the world. Towards prod
    the list is short, because a full neutralize would switch off the crons
    of the instance that is being brought into service. What is kept in both:

    - `database.uuid` is regenerated, or seeding the same database into stage
      and prod would give two instances the same identity;
    - the Mailpit `ir.mail_server` is archived: it points at the host
      `mailpit`, which does not exist on the server, and every outgoing mail
      would fail without a sound;
    - `web.base.url` is set to the domain and `web.base.url.freeze` is
      dropped, so Odoo does not keep building links to localhost.

    Everything runs in one transaction together with the idempotence mark.
    """
    if env not in _ENVIRONMENTS:
        raise ValueError(f"unknown environment {env!r}: expected one of {_ENVIRONMENTS}")
    if "\n" in domain or _SQL_HEREDOC in domain or _SQL_HEREDOC in source_db or "\n" in source_db:
        raise ValueError("domain and source database must be single-line values")

    statements = [
        f"UPDATE ir_config_parameter SET value = {_NEW_UUID}, write_date = now() WHERE key = 'database.uuid';",
        "UPDATE ir_mail_server SET active = false, write_date = now() "
        f"WHERE name = {_literal(_MAILPIT_NAME)} AND smtp_host = {_literal(_MAILPIT_HOST)};",
        "DELETE FROM ir_config_parameter WHERE key = 'web.base.url.freeze';",
    ]
    if domain:
        statements.append(
            f"UPDATE ir_config_parameter SET value = {_literal('https://' + domain)}, write_date = now() "
            "WHERE key = 'web.base.url';"
        )
    if env == "stage":
        statements.append(
            "DO $rkd$ BEGIN\n"
            "  IF to_regclass('ir_cron') IS NOT NULL THEN UPDATE ir_cron SET active = false; END IF;\n"
            "  IF to_regclass('ir_mail_server') IS NOT NULL THEN UPDATE ir_mail_server SET active = false; END IF;\n"
            "  IF to_regclass('fetchmail_server') IS NOT NULL THEN\n"
            "    UPDATE fetchmail_server SET active = false, state = 'draft';\n"
            "  END IF;\n"
            "  IF to_regclass('payment_provider') IS NOT NULL THEN\n"
            "    UPDATE payment_provider SET state = 'disabled' WHERE state <> 'disabled';\n"
            "  END IF;\n"
            "  IF to_regclass('payment_acquirer') IS NOT NULL THEN\n"
            "    UPDATE payment_acquirer SET state = 'disabled' WHERE state <> 'disabled';\n"
            "  END IF;\n"
            "  DELETE FROM ir_config_parameter WHERE key = 'database.enterprise_code';\n"
            "END $rkd$;"
        )
    statements.append(
        "INSERT INTO ir_config_parameter (key, value, create_date, write_date) "
        f"VALUES ({_literal(SEEDED_MARK)}, {_literal(source_db)}, now(), now());"
    )
    return "\n".join(statements) + "\n"


def _filestore_snippet(source_db: str, db_name: str) -> str:
    """The `sh -c` body that unpacks the tarball on stdin as `db_name`.

    The tarball holds a directory named after the source database, and
    `ir_attachment.store_fname` is relative to the directory of the database,
    so the directory is renamed to the remote name instead of copied.
    """
    tmp_dir = f"{FILESTORE_ROOT}/.rkd_seed_tmp"
    root, tmp = shlex.quote(FILESTORE_ROOT), shlex.quote(tmp_dir)
    target = shlex.quote(f"{FILESTORE_ROOT}/{db_name}")
    return (
        f"set -e; mkdir -p {root}; rm -rf {tmp}; mkdir {tmp}; tar -xzf - -C {tmp}; "
        f"rm -rf {target}; mv {shlex.quote(f'{tmp_dir}/{source_db}')} {target}; rmdir {tmp}"
    )


def _script(
    *,
    prefix: str,
    auth: str,
    filestore_runner: str,
    prologue: str,
    seed_dir: str,
    env: str,
    source_db: str,
    db_name: str,
    db_user: str,
    domain: str,
    has_filestore: bool,
) -> str:
    bad = invalid_identifiers(db_name, db_user)
    if bad:
        raise ValueError(f"not valid PostgreSQL identifiers: {', '.join(bad)}")
    sql = neutralize_sql(env, source_db, domain)

    db, user = shlex.quote(db_name), shlex.quote(db_user)
    dump = shlex.quote(f"{seed_dir}/{DUMP_FILE}")
    tarball = shlex.quote(f"{seed_dir}/{FILESTORE_FILE}")
    psql = f"{prefix} psql{auth}"
    in_db = f"{psql} -d {db} -tAc"
    marked = f"SELECT 1 FROM ir_config_parameter WHERE key = {_literal(SEEDED_MARK)}"
    initialised = "SELECT 1 FROM ir_module_module WHERE name = 'base' AND state = 'installed'"
    drop = f"{prefix} dropdb{auth} --if-exists {db}"

    lines = [
        "{",
        "set -e",
        prologue,
        f'fail() {{ {drop} || true; exit "$1"; }}',
        f"exists=$({psql} -d postgres -tAc {shlex.quote(f'SELECT 1 FROM pg_database WHERE datname = {_literal(db_name)}')})",
        'if [ -n "$exists" ]; then',
        f'  if [ -n "$({in_db} {shlex.quote(marked)} 2>/dev/null)" ]; then',
        '    echo "database already seeded: left alone"',
        "    exit 0",
        "  fi",
        f'  if [ -n "$({in_db} {shlex.quote(initialised)} 2>/dev/null)" ]; then',
        '    echo "database exists, is initialised and was not seeded: refusing to overwrite it" >&2',
        "    exit 3",
        "  fi",
        f"  {drop}",
        "fi",
    ]
    if has_filestore:
        lines.append(f"{filestore_runner} {shlex.quote(_filestore_snippet(source_db, db_name))} < {tarball}")
    lines += [
        f"{prefix} createdb{auth} -E UTF8 -T template0 --lc-collate=C -O {user} {db}",
        # Exit 1 is "restored, some statements ignored" -- usually the roles and
        # ACLs --no-owner/--no-acl already skip -- so it is a warning and not a
        # failure, but never silent: it can also hide real data loss. Above 1
        # nothing usable was restored, and the database is dropped so a rerun
        # starts clean instead of tripping the guard above.
        "set +e",
        f"{prefix} pg_restore{auth} -d {db} --no-owner --no-acl --role={user} < {dump}",
        "rc=$?",
        "set -e",
        'if [ "$rc" -gt 1 ]; then echo "pg_restore failed" >&2; fail "$rc"; fi',
        f'if [ "$rc" -eq 1 ]; then echo "{RESTORE_WARNING}" >&2; fi',
        f"{psql} -d {db} -v ON_ERROR_STOP=1 -1 -q -f - <<'{_SQL_HEREDOC}' || fail 1",
        sql.rstrip("\n"),
        _SQL_HEREDOC,
        "}",
    ]
    return "\n".join(lines) + "\n"


def _state_script(*, prefix: str, auth: str, prologue: str, db_name: str) -> str:
    db = shlex.quote(db_name)
    psql = f"{prefix} psql{auth}"
    in_db = f"{psql} -d {db} -tAc"
    exists_sql = shlex.quote(f"SELECT 1 FROM pg_database WHERE datname = {_literal(db_name)}")
    marked = shlex.quote(f"SELECT 1 FROM ir_config_parameter WHERE key = {_literal(SEEDED_MARK)}")
    initialised = shlex.quote("SELECT 1 FROM ir_module_module WHERE name = 'base' AND state = 'installed'")
    return "\n".join(
        [
            "{",
            "set -e",
            prologue,
            f"exists=$({psql} -d postgres -tAc {exists_sql})",
            "state=absent",
            'if [ -n "$exists" ]; then',
            "  state=empty",
            f'  if [ -n "$({in_db} {marked} 2>/dev/null)" ]; then state=seeded',
            f'  elif [ -n "$({in_db} {initialised} 2>/dev/null)" ]; then state=initialised',
            "  fi",
            "fi",
            f'echo "{STATE_PREFIX}$state"',
            "}",
            "",
        ]
    )


def parse_state(stdout: str) -> str:
    """`absent`, `empty`, `seeded` or `initialised`; `` if the script did not answer."""
    for line in stdout.splitlines():
        if line.startswith(STATE_PREFIX):
            return line[len(STATE_PREFIX) :].strip()
    return ""


def docker_state_script(compose_dir: str, *, db_name: str, db_user: str, ready_retries: int = 60) -> str:
    """Start the `db` service, wait for it, and say what state `db_name` is in.

    Asked before anything is dumped or shipped, so that a redeploy of an
    already seeded instance costs one SSH round trip instead of a transfer.
    """
    wait = (
        f"for _ in $(seq 1 {ready_retries}); do "
        f"if docker compose exec -T db pg_isready -U {shlex.quote(db_user)} -q; then break; fi; sleep 2; done"
    )
    return _state_script(
        prefix="docker compose exec -T db",
        auth=f" -U {shlex.quote(db_user)}",
        prologue=f"cd {shlex.quote(compose_dir)}\ndocker compose up -d db\n{wait}",
        db_name=db_name,
    )


def native_state_script(*, db_name: str) -> str:
    return _state_script(prefix="sudo -u postgres", auth="", prologue=":", db_name=db_name)


def docker_restore_script(
    compose_dir: str,
    seed_dir: str,
    *,
    env: str,
    source_db: str,
    db_name: str,
    db_user: str,
    domain: str = "",
    has_filestore: bool = True,
) -> str:
    """Restore the seed into the `db` service of the compose at `compose_dir`."""
    return _script(
        prefix="docker compose exec -T db",
        auth=f" -U {shlex.quote(db_user)}",
        filestore_runner="docker compose run --rm -T --no-deps -l traefik.enable=false --entrypoint sh web -c",
        prologue=f"cd {shlex.quote(compose_dir)}",
        seed_dir=seed_dir,
        env=env,
        source_db=source_db,
        db_name=db_name,
        db_user=db_user,
        domain=domain,
        has_filestore=has_filestore,
    )


def native_restore_script(
    seed_dir: str,
    *,
    env: str,
    source_db: str,
    db_name: str,
    db_user: str,
    domain: str = "",
    has_filestore: bool = True,
) -> str:
    """Restore the seed into the system PostgreSQL, as the `postgres` user."""
    return _script(
        prefix="sudo -u postgres",
        auth="",
        filestore_runner="sudo -u odoo sh -c",
        prologue=":",
        seed_dir=seed_dir,
        env=env,
        source_db=source_db,
        db_name=db_name,
        db_user=db_user,
        domain=domain,
        has_filestore=has_filestore,
    )
