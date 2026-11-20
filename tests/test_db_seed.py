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
