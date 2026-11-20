"""Scripts that seed an instance with a development database (#301)."""

import subprocess

import pytest

from rocketdoo.core.instance import db_seed

ARGS = {"source_db": "dev-db", "db_name": "odoo", "db_user": "odoo_prod", "domain": "erp.example.com"}


def docker(env="prod", **overrides):
    return db_seed.docker_restore_script("/opt/odoo-prod/prod", "/opt/odoo-prod/seed", env=env, **{**ARGS, **overrides})


def native(env="prod", **overrides):
    return db_seed.native_restore_script("/opt/odoo/seed", env=env, **{**ARGS, **overrides})


@pytest.mark.parametrize("script", [docker(), native(), docker("stage"), native("stage")])
def test_scripts_are_valid_bash_inside_braces(script):
    assert script.startswith("{\n") and script.endswith("}\n")
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)


@pytest.mark.parametrize("script", [docker(), native()])
def test_restore_drops_ownership_and_acl_and_uses_odoo_createdb_parameters(script):
    assert "--no-owner --no-acl --role=odoo_prod" in script
    assert "-E UTF8 -T template0 --lc-collate=C -O odoo_prod odoo" in script


def test_pg_restore_exit_one_is_a_warning_and_higher_is_a_failure():
    script = docker()
    assert 'if [ "$rc" -gt 1 ]' in script
    assert 'if [ "$rc" -eq 1 ]' in script
    assert "warning: pg_restore ignored some statements" in script


def test_docker_runs_inside_the_db_container_and_native_as_postgres():
    assert "cd /opt/odoo-prod/prod" in docker()
    assert "docker compose exec -T db pg_restore -U odoo_prod" in docker()
    assert "sudo -u postgres pg_restore -d odoo" in native()
    assert "-U " not in native()


@pytest.mark.parametrize("script", [docker(), native()])
def test_filestore_is_renamed_to_the_remote_database_name(script):
    assert "tar -xzf -" in script
    assert "mv /var/lib/odoo/filestore/.rkd_seed_tmp/dev-db /var/lib/odoo/filestore/odoo" in script
    assert "filestore.tar.gz" in script


def test_filestore_step_is_skipped_without_a_filestore():
    assert "tar -xzf" not in docker(has_filestore=False)
    assert "tar -xzf" not in native(has_filestore=False)


@pytest.mark.parametrize("script", [docker(), native()])
def test_mark_is_written_in_the_database_and_guarded_on_it(script):
    assert "VALUES ('rkd.seeded_from', 'dev-db'" in script
    assert "rkd.seeded_from" in script.split("already seeded")[0]
    assert script.index("already seeded") < script.index("pg_restore")
    assert "refusing to overwrite" in script


@pytest.mark.parametrize("env", ["stage", "prod"])
def test_both_environments_regenerate_the_uuid_and_archive_mailpit(env):
    sql = db_seed.neutralize_sql(env, "dev", "erp.example.com")
    assert "key = 'database.uuid'" in sql
    assert "name = 'Mailpit (rkd)' AND smtp_host = 'mailpit'" in sql
    assert "key = 'web.base.url.freeze'" in sql
    assert "'https://erp.example.com'" in sql


def test_prod_is_not_fully_neutralized_and_stage_is():
    prod = db_seed.neutralize_sql("prod", "dev", "erp.example.com")
    stage = db_seed.neutralize_sql("stage", "dev", "erp.example.com")
    assert "ir_cron" not in prod
    assert "fetchmail_server" not in prod
    assert "payment_provider" not in prod
    assert "UPDATE ir_cron SET active = false" in stage
    assert "UPDATE fetchmail_server" in stage
    assert "payment_provider" in stage


def test_base_url_is_left_alone_without_a_domain():
    assert "'web.base.url'" not in db_seed.neutralize_sql("prod", "dev")


def test_literals_are_escaped():
    sql = db_seed.neutralize_sql("prod", "o'brien", "a'b.com")
    assert "'o''brien'" in sql
    assert "https://a''b.com" in sql


@pytest.mark.parametrize("build", [docker, native])
@pytest.mark.parametrize("field", ["db_name", "db_user"])
def test_invalid_identifiers_are_rejected_before_generating(build, field):
    with pytest.raises(ValueError, match="identifiers"):
        build(**{field: 'x"; DROP DATABASE y; --'})


def test_unknown_environment_is_rejected():
    with pytest.raises(ValueError):
        db_seed.neutralize_sql("qa", "dev")


def test_multiline_values_cannot_break_the_heredoc():
    with pytest.raises(ValueError):
        db_seed.neutralize_sql("prod", "dev", "a.com\nRKD_SQL_EOF")


class TestTheGuardRunsForReal:
    """The guard decides whether a client's production gets overwritten, so it
    is exercised by running the script against fake binaries rather than by
    reading it: a `psql` that answers, a `createdb`/`pg_restore`/`dropdb` that
    record whether they were reached.
    """

    def _run(self, tmp_path, *, exists, marked, initialised):
        """Run the native script with a psql whose answers are scripted."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "calls.log"
        answers = {
            "pg_database": "1" if exists else "",
            "ir_config_parameter": "1" if marked else "",
            "ir_module_module": "1" if initialised else "",
        }
        psql = '#!/bin/sh\necho psql >> %s\nfor a in "$@"; do\n' % log
        for needle, answer in answers.items():
            psql += f'  case "$a" in *{needle}*) echo "{answer}"; exit 0;; esac\n'
        psql += "done\nexit 0\n"
        (bin_dir / "psql").write_text(psql)
        for name in ("createdb", "pg_restore", "dropdb", "sudo", "tar", "sh"):
            if name == "sudo":
                body = (
                    '#!/bin/sh\necho sudo >> %s\nwhile [ "${1#-}" != "$1" ] || [ "$1" = postgres ] || [ "$1" = odoo ]; do shift; done\nexec "$@"\n'
                    % log
                )
            elif name == "sh":
                continue
            else:
                body = f"#!/bin/sh\necho {name} >> {log}\nexit 0\n"
            (bin_dir / name).write_text(body)
        for f in bin_dir.iterdir():
            f.chmod(0o755)

        (tmp_path / "db.dump").write_text("")
        script = db_seed.native_restore_script(str(tmp_path), env="prod", has_filestore=False, **ARGS)
        result = subprocess.run(
            ["bash", "-s"],
            input=script,
            text=True,
            capture_output=True,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        )
        calls = log.read_text().split() if log.exists() else []
        return result, calls

    def test_an_initialised_database_that_was_not_seeded_is_refused(self, tmp_path):
        """Someone else's production: the one case that must never be dropped."""
        result, calls = self._run(tmp_path, exists=True, marked=False, initialised=True)

        assert result.returncode == 3
        assert "refusing to overwrite" in result.stderr
        assert "dropdb" not in calls
        assert "pg_restore" not in calls

    def test_an_already_seeded_database_is_left_alone(self, tmp_path):
        """The second deploy: no transfer, no restore, no neutralization."""
        result, calls = self._run(tmp_path, exists=True, marked=True, initialised=True)

        assert result.returncode == 0
        assert "dropdb" not in calls
        assert "pg_restore" not in calls

    def test_an_empty_leftover_database_is_dropped_and_restored(self, tmp_path):
        """A first attempt that died before installing anything."""
        result, calls = self._run(tmp_path, exists=True, marked=False, initialised=False)

        assert result.returncode == 0
        assert "dropdb" in calls
        assert "pg_restore" in calls

    def test_a_fresh_server_restores_without_dropping_anything(self, tmp_path):
        result, calls = self._run(tmp_path, exists=False, marked=False, initialised=False)

        assert result.returncode == 0
        assert "createdb" in calls
        assert "pg_restore" in calls
