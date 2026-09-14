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


class TestMailServers:
    def test_parses_the_rows(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(
            monkeypatch,
            subprocess.CompletedProcess([], 0, "1\tMailpit (rkd)\t1\tmailpit\tt\n2\tProd SMTP\t10\tsmtp.example.com\tf\n", ""),
        )
        servers, error = odoo_db.mail_servers("dev")
        assert error == ""
        assert servers == [
            {"id": 1, "name": "Mailpit (rkd)", "sequence": 1, "smtp_host": "mailpit", "active": True},
            {"id": 2, "name": "Prod SMTP", "sequence": 10, "smtp_host": "smtp.example.com", "active": False},
        ]

    def test_a_malformed_row_is_discarded(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(
            monkeypatch,
            subprocess.CompletedProcess([], 0, "1\tMailpit (rkd)\t1\tmailpit\tt\nnot\tenough\tfields\n", ""),
        )
        servers, error = odoo_db.mail_servers("dev")
        assert error == ""
        assert len(servers) == 1
        assert servers[0]["id"] == 1

    def test_propagates_the_psql_error(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(
            monkeypatch,
            subprocess.CompletedProcess([], 1, "", 'relation "ir_mail_server" does not exist'),
        )
        servers, error = odoo_db.mail_servers("dev")
        assert servers == []
        assert "ir_mail_server" in error

    def test_reports_a_missing_docker_binary(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(monkeypatch, FileNotFoundError("docker"))
        assert odoo_db.mail_servers("dev") == ([], "docker not found")


class TestEnableMailpitServer:
    def test_the_sql_has_the_seven_columns_and_values(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        seen = {}

        def _run(argv, **kw):
            seen["argv"] = argv
            return subprocess.CompletedProcess([], 0, "", "")

        monkeypatch.setattr(odoo_db.subprocess, "run", _run)
        error = odoo_db.enable_mailpit_server("dev")
        assert error == ""

        sql = seen["argv"][-1]
        for column in (
            "name",
            "smtp_host",
            "smtp_port",
            "smtp_encryption",
            "smtp_authentication",
            "sequence",
            "active",
        ):
            assert column in sql
        assert "'Mailpit (rkd)'" in sql
        assert "'mailpit'" in sql
        assert "1025" in sql
        assert "'none'" in sql
        assert "'login'" in sql

    def test_the_database_is_not_interpolated_in_the_sql(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        seen = {}

        def _run(argv, **kw):
            seen["argv"] = argv
            return subprocess.CompletedProcess([], 0, "", "")

        monkeypatch.setattr(odoo_db.subprocess, "run", _run)
        odoo_db.enable_mailpit_server("dev")

        assert seen["argv"][seen["argv"].index("-d") + 1] == "dev"
        assert "dev" not in seen["argv"][-1]

    def test_is_idempotent_across_two_runs(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        seen = []

        def _run(argv, **kw):
            seen.append(argv)
            return subprocess.CompletedProcess([], 0, "", "")

        monkeypatch.setattr(odoo_db.subprocess, "run", _run)
        odoo_db.enable_mailpit_server("dev")
        odoo_db.enable_mailpit_server("dev")
        assert seen[0] == seen[1]

    def test_propagates_the_psql_error(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(monkeypatch, subprocess.CompletedProcess([], 1, "", "permission denied"))
        assert odoo_db.enable_mailpit_server("dev") == "permission denied"


class TestDisableMailpitServer:
    def test_the_sql_is_an_update_with_both_conditions(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        seen = {}

        def _run(argv, **kw):
            seen["argv"] = argv
            return subprocess.CompletedProcess([], 0, "1\n", "")

        monkeypatch.setattr(odoo_db.subprocess, "run", _run)
        odoo_db.disable_mailpit_server("dev")

        sql = seen["argv"][-1]
        assert sql.strip().upper().startswith("UPDATE")
        assert "SET active = false" in sql
        assert "name = 'Mailpit (rkd)'" in sql
        assert "smtp_host = 'mailpit'" in sql

    def test_no_sql_emitted_by_the_module_contains_delete(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        seen = []

        def _run(argv, **kw):
            seen.append(argv[-1])
            return subprocess.CompletedProcess([], 0, "1\n", "")

        monkeypatch.setattr(odoo_db.subprocess, "run", _run)
        odoo_db.enable_mailpit_server("dev")
        odoo_db.disable_mailpit_server("dev")
        for sql in seen:
            assert "DELETE" not in sql.upper()

    def test_counts_the_archived_rows(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(monkeypatch, subprocess.CompletedProcess([], 0, "1\n2\n", ""))
        count, error = odoo_db.disable_mailpit_server("dev")
        assert count == 2
        assert error == ""

    def test_zero_rows_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(monkeypatch, subprocess.CompletedProcess([], 0, "", ""))
        count, error = odoo_db.disable_mailpit_server("dev")
        assert count == 0
        assert error == ""

    def test_propagates_the_psql_error(self, monkeypatch):
        monkeypatch.setattr(odoo_db, "db_container", lambda compose_data=None: "proj-db-1")
        _patch_run(monkeypatch, subprocess.CompletedProcess([], 1, "", "permission denied"))
        count, error = odoo_db.disable_mailpit_server("dev")
        assert count == 0
        assert error == "permission denied"


class TestDisableCountsRealPsqlOutput:
    """psql prints a status line even with -t -A, and it must not count as a row.

    Found running against a real database: `RETURNING id` with zero matches
    still emits "UPDATE 0" on stdout, so counting non-empty lines reported one
    archived row and `rkd mail off` claimed it had archived a record that did
    not exist. The earlier tests missed it because their mocks returned the
    idealised output, without the status line.
    """

    def _psql_returning(self, monkeypatch, stdout):
        import subprocess

        from rocketdoo.core import odoo_db

        captured = {}

        def _run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        monkeypatch.setattr(odoo_db.subprocess, "run", _run)
        monkeypatch.setattr(odoo_db, "db_container", lambda *a, **k: "db-demo")
        return captured

    def test_quiet_flag_is_passed(self, monkeypatch):
        """Without -q the status line comes back and breaks the count."""
        from rocketdoo.core.odoo_db import disable_mailpit_server

        captured = self._psql_returning(monkeypatch, "")
        disable_mailpit_server("demo")

        assert "-q" in captured["cmd"]

    def test_no_match_counts_zero(self, monkeypatch):
        from rocketdoo.core.odoo_db import disable_mailpit_server

        self._psql_returning(monkeypatch, "")
        assert disable_mailpit_server("demo")[0] == 0

    def test_one_match_counts_one(self, monkeypatch):
        from rocketdoo.core.odoo_db import disable_mailpit_server

        self._psql_returning(monkeypatch, "7\n")
        assert disable_mailpit_server("demo")[0] == 1

    def test_a_stray_status_line_is_not_counted(self, monkeypatch):
        """Belt and braces: even if -q were dropped, "UPDATE 0" is not a row."""
        from rocketdoo.core.odoo_db import disable_mailpit_server

        self._psql_returning(monkeypatch, "UPDATE 0\n")
        rows = disable_mailpit_server("demo")[0]
        assert rows == 0, "the psql status line was counted as an archived row"
