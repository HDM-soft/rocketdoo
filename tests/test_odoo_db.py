"""Unit tests for core/odoo_db.py.

`_get_db_container` and the `pg_database` query used to be duplicated in
`pack_environment`; the GUI module-update feature needs the same access, so
these are the shared implementation's edge cases: no container configured,
psql failing, and the two query shapes (database names, module states).
"""

import subprocess

from rocketdoo.core import odoo_db


def _patch_run(monkeypatch, result):
    def _run(*a, **kw):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(odoo_db.subprocess, "run", _run)


class TestDbContainer:
    def test_reads_the_container_name(self):
        compose_data = {"services": {"db": {"container_name": "proj-db-1"}}}
        assert odoo_db.db_container(compose_data) == "proj-db-1"

    def test_none_without_a_db_service(self):
        assert odoo_db.db_container({"services": {}}) is None

    def test_none_without_compose_data(self):
        assert odoo_db.db_container(None) is None

    def test_falls_back_to_read_docker_compose(self, monkeypatch):
        monkeypatch.setattr(
            odoo_db,
            "read_docker_compose",
            lambda: {"services": {"db": {"container_name": "proj-db-1"}}},
        )
        assert odoo_db.db_container() == "proj-db-1"


class TestDatabasesResult:
    def test_parses_the_psql_output(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(monkeypatch, subprocess.CompletedProcess([], 0, " dev \n demo\n", ""))
        databases, error = odoo_db.databases_result({"services": {}})
        assert databases == ["dev", "demo"]
        assert error == ""

    def test_no_container_yields_a_reason(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: None)
        databases, error = odoo_db.databases_result()
        assert databases == []
        assert error

    def test_reports_the_psql_error(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(monkeypatch, subprocess.CompletedProcess([], 1, "", "container not running"))
        databases, error = odoo_db.databases_result()
        assert databases == []
        assert "container not running" in error

    def test_reports_a_missing_docker_binary(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(monkeypatch, FileNotFoundError("docker"))
        assert odoo_db.databases_result() == ([], "docker not found")

    def test_list_databases_is_the_first_element(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "databases_result", lambda compose_data=None: (["dev"], "boom"))
        assert odoo_db.list_databases() == ["dev"]


class TestModuleStates:
    def test_parses_name_and_state_pairs(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(
            monkeypatch,
            subprocess.CompletedProcess([], 0, "sale_extension\tinstalled\nstock_custom\tuninstalled\n", ""),
        )
        states, error = odoo_db.module_states("dev")
        assert states == {"sale_extension": "installed", "stock_custom": "uninstalled"}
        assert error == ""

    def test_no_container_yields_a_reason(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: None)
        states, error = odoo_db.module_states("dev")
        assert states == {}
        assert error

    def test_a_non_odoo_database_propagates_the_psql_error(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(
            monkeypatch,
            subprocess.CompletedProcess([], 1, "", 'relation "ir_module_module" does not exist'),
        )
        states, error = odoo_db.module_states("not_odoo")
        assert states == {}
        assert "ir_module_module" in error

    def test_uses_the_given_database(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        seen = {}

        def _run(argv, **kw):
            seen["argv"] = argv
            return subprocess.CompletedProcess([], 0, "", "")

        monkeypatch.setattr(odoo_db.subprocess, "run", _run)
        odoo_db.module_states("dev")
        assert seen["argv"][:4] == ["docker", "exec", "proj-db-1", "psql"]
        assert "dev" in seen["argv"]
