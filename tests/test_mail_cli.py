"""Tests for the Mailpit toggle (mail_cli.py).

`rkd mail on` reported `✓ odoo.conf → smtp_server = mailpit` while writing
nothing. `_toggle_smtp` only *replaced* keys that were already in the file, and
Odoo rewrites odoo.conf the first time a database is created from the web UI,
dropping every commented line — including the smtp_* ones the scaffold ships.
On any project that had been used, there was nothing left to replace.

Verified against Odoo 18 before the fix: the send failed with
ConnectionRefusedError (Odoo tried localhost:25) and Mailpit received nothing.
"""

import pytest

from rocketdoo.mail_cli import _MAILPIT_SMTP_PORT, _toggle_smtp

# What Odoo leaves behind after creating a database from the web UI: no
# commented lines, admin_passwd hashed.
REWRITTEN_BY_ODOO = (
    "[options]\n"
    "addons_path = /usr/lib/python3/dist-packages/odoo/extra-addons\n"
    "admin_passwd = $pbkdf2-sha512$600000$abc\n"
    "db_host = db-demo\n"
    "gevent_port = 8072\n"
)

# What the scaffold ships: the keys are there, commented out.
AS_SCAFFOLDED = (
    "[options]\n"
    "admin_passwd = admin\n"
    "; smtp_password = False\n"
    "; smtp_port = 25\n"
    "; smtp_server = localhost\n"
    "; smtp_ssl = False\n"
)


def _active_keys(conf: str) -> dict[str, str]:
    values = {}
    for line in conf.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


class TestEnableOnARewrittenConf:
    """The case that was broken: no smtp_* keys left to replace."""

    def test_the_keys_are_added(self):
        result = _toggle_smtp(REWRITTEN_BY_ODOO, enable=True)
        active = _active_keys(result)
        assert active["smtp_server"] == "mailpit"
        assert active["smtp_port"] == str(_MAILPIT_SMTP_PORT)
        assert active["smtp_ssl"] == "False"

    def test_the_existing_settings_survive(self):
        result = _toggle_smtp(REWRITTEN_BY_ODOO, enable=True)
        active = _active_keys(result)
        assert active["db_host"] == "db-demo"
        assert active["admin_passwd"].startswith("$pbkdf2")
        assert active["gevent_port"] == "8072"

    def test_the_content_actually_changes(self):
        """The bug was that this returned the input untouched."""
        assert _toggle_smtp(REWRITTEN_BY_ODOO, enable=True) != REWRITTEN_BY_ODOO

    def test_the_keys_land_inside_options(self):
        result = _toggle_smtp(REWRITTEN_BY_ODOO, enable=True)
        assert result.index("[options]") < result.index("smtp_server")


class TestEnableOnAScaffoldedConf:
    """The path that already worked must keep working."""

    def test_the_commented_keys_are_replaced_not_duplicated(self):
        result = _toggle_smtp(AS_SCAFFOLDED, enable=True)
        assert result.count("smtp_server") == 1
        assert _active_keys(result)["smtp_server"] == "mailpit"

    def test_unrelated_smtp_keys_stay_commented(self):
        result = _toggle_smtp(AS_SCAFFOLDED, enable=True)
        assert "smtp_password" not in _active_keys(result)


class TestDisable:
    @pytest.mark.parametrize("conf", [REWRITTEN_BY_ODOO, AS_SCAFFOLDED])
    def test_mailpit_is_not_left_active(self, conf):
        enabled = _toggle_smtp(conf, enable=True)
        disabled = _toggle_smtp(enabled, enable=False)
        assert "mailpit" not in _active_keys(disabled).get("smtp_server", "")

    def test_the_cycle_is_idempotent(self):
        once = _toggle_smtp(_toggle_smtp(REWRITTEN_BY_ODOO, True), False)
        twice = _toggle_smtp(_toggle_smtp(once, True), False)
        assert once == twice

    def test_disable_does_not_add_keys_to_a_conf_without_them(self):
        """`mail off` on a project that never enabled it changes nothing."""
        assert _toggle_smtp(REWRITTEN_BY_ODOO, enable=False) == REWRITTEN_BY_ODOO


class TestOptionsSectionHandling:
    def test_keys_go_before_a_following_section(self):
        conf = "[options]\ndb_host = db\n\n[queue_job]\nchannels = root:2\n"
        result = _toggle_smtp(conf, enable=True)
        assert result.index("smtp_server") < result.index("[queue_job]")

    def test_an_options_header_is_created_when_missing(self):
        """Orphan keys outside a section would be ignored by Odoo."""
        result = _toggle_smtp("db_host = db\n", enable=True)
        assert "[options]" in result
        assert result.index("[options]") < result.index("smtp_server")

    def test_a_missing_trailing_newline_does_not_glue_lines(self):
        result = _toggle_smtp("[options]\ndb_host = db", enable=True)
        assert "db_host = dbsmtp_server" not in result
        assert _active_keys(result)["db_host"] == "db"


class TestReportHonesty:
    """The report drives what the user is told, so it must be measured."""

    def test_conf_updated_is_false_when_nothing_changes(self, tmp_path, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        conf = tmp_path / "odoo.conf"
        conf.write_text(_toggle_smtp(REWRITTEN_BY_ODOO, enable=True))

        compose = tmp_path / "docker-compose.yaml"
        compose.write_text("# rkd:mailpit\n  mailpit:\n    image: x\n# /rkd:mailpit\n")

        monkeypatch.setattr(mail_cli, "compose_path", lambda *a, **k: compose)
        monkeypatch.setattr(mail_cli, "_odoo_conf_path", lambda *a, **k: conf)
        monkeypatch.setattr(mail_cli, "_has_markers", lambda c: True)
        monkeypatch.setattr(mail_cli, "_is_enabled", lambda c: False)
        monkeypatch.setattr(mail_cli, "_toggle_compose", lambda c, enable: c)
        monkeypatch.setattr(mail_cli, "run_compose", lambda *a, **k: 0)
        monkeypatch.setattr(mail_cli, "container_running", lambda *a, **k: False)
        monkeypatch.setattr(mail_cli, "_apply_mail_server", lambda *a, **k: {"db": None, "db_error": "", "db_archived": None})

        report = mail_cli._enable_mailpit()

        assert report["conf_found"] is True
        assert report["conf_updated"] is False, "nothing changed; must not claim it did"

    def test_conf_updated_is_true_when_the_keys_are_added(self, tmp_path, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        conf = tmp_path / "odoo.conf"
        conf.write_text(REWRITTEN_BY_ODOO)

        compose = tmp_path / "docker-compose.yaml"
        compose.write_text("# rkd:mailpit\n# /rkd:mailpit\n")

        monkeypatch.setattr(mail_cli, "compose_path", lambda *a, **k: compose)
        monkeypatch.setattr(mail_cli, "_odoo_conf_path", lambda *a, **k: conf)
        monkeypatch.setattr(mail_cli, "_has_markers", lambda c: True)
        monkeypatch.setattr(mail_cli, "_is_enabled", lambda c: False)
        monkeypatch.setattr(mail_cli, "_toggle_compose", lambda c, enable: c)
        monkeypatch.setattr(mail_cli, "run_compose", lambda *a, **k: 0)
        monkeypatch.setattr(mail_cli, "container_running", lambda *a, **k: False)
        monkeypatch.setattr(mail_cli, "_apply_mail_server", lambda *a, **k: {"db": None, "db_error": "", "db_archived": None})

        report = mail_cli._enable_mailpit()

        assert report["conf_updated"] is True
        assert _active_keys(conf.read_text())["smtp_server"] == "mailpit"


class TestResolveDb:
    """Database selection: 0/1/N/--db, shared by _apply_mail_server (CA-18 to CA-22)."""

    def test_no_databases_yields_the_reason(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "databases_result", lambda: ([], "no database container configured"))
        db, error = mail_cli._resolve_db(None)
        assert db is None
        assert error == "no database container configured"

    def test_no_databases_falls_back_to_a_generic_reason(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "databases_result", lambda: ([], ""))
        db, error = mail_cli._resolve_db(None)
        assert db is None
        assert error == "no databases found"

    def test_a_single_database_is_auto_selected(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "databases_result", lambda: (["dev"], ""))
        db, error = mail_cli._resolve_db(None)
        assert db == "dev"
        assert error == ""

    def test_two_or_more_without_db_is_ambiguous(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "databases_result", lambda: (["dev", "demo"], ""))
        db, error = mail_cli._resolve_db(None)
        assert db is None
        assert "--db" in error
        assert "2" in error

    def test_a_valid_db_flag_is_used(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "databases_result", lambda: (["dev", "demo"], ""))
        db, error = mail_cli._resolve_db("demo")
        assert db == "demo"
        assert error == ""

    def test_an_unknown_db_flag_is_rejected(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "databases_result", lambda: (["dev"], ""))
        db, error = mail_cli._resolve_db("nope")
        assert db is None
        assert "nope" in error


class TestConnectivityHint:
    def test_a_selection_error_gets_no_hint(self):
        import rocketdoo.mail_cli as mail_cli

        assert mail_cli._connectivity_hint("2 databases found - re-run with --db NAME") == ""
        assert mail_cli._connectivity_hint("database 'nope' not found") == ""

    def test_a_connectivity_error_gets_the_hint(self):
        import rocketdoo.mail_cli as mail_cli

        assert "rkd up -d" in mail_cli._connectivity_hint("no database container configured")


class TestApplyMailServer:
    """CA-1, CA-5, CA-18 to CA-22: resolve + write, no query without a target."""

    def test_enable_reports_the_resolved_database(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "_resolve_db", lambda db: ("dev", ""))
        monkeypatch.setattr(mail_cli, "enable_mailpit_server", lambda db: "")

        report = mail_cli._apply_mail_server(enable=True, db=None)
        assert report == {"db": "dev", "db_error": "", "db_archived": None}

    def test_enable_propagates_the_write_error(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "_resolve_db", lambda db: ("dev", ""))
        monkeypatch.setattr(mail_cli, "enable_mailpit_server", lambda db: "permission denied")

        report = mail_cli._apply_mail_server(enable=True, db=None)
        assert report["db_error"] == "permission denied"

    def test_disable_reports_the_archived_count(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "_resolve_db", lambda db: ("dev", ""))
        monkeypatch.setattr(mail_cli, "disable_mailpit_server", lambda db: (1, ""))

        report = mail_cli._apply_mail_server(enable=False, db=None)
        assert report == {"db": "dev", "db_error": "", "db_archived": 1}

    def test_no_target_never_calls_enable_mailpit_server(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "_resolve_db", lambda db: (None, "2 databases found - re-run with --db NAME"))

        def _boom(db):
            raise AssertionError("must not query without a resolved database")

        monkeypatch.setattr(mail_cli, "enable_mailpit_server", _boom)
        monkeypatch.setattr(mail_cli, "disable_mailpit_server", _boom)

        report = mail_cli._apply_mail_server(enable=True, db=None)
        assert report == {"db": None, "db_error": "2 databases found - re-run with --db NAME", "db_archived": None}

    def test_no_target_never_calls_disable_mailpit_server(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "_resolve_db", lambda db: (None, "database 'nope' not found"))

        def _boom(db):
            raise AssertionError("must not query without a resolved database")

        monkeypatch.setattr(mail_cli, "disable_mailpit_server", _boom)

        report = mail_cli._apply_mail_server(enable=False, db="nope")
        assert report == {"db": None, "db_error": "database 'nope' not found", "db_archived": None}


class TestEnableMailpitWritesTheMailServer:
    """RF-1.3 and CA-4: the write must run in both branches of _enable_mailpit."""

    def _setup(self, tmp_path, monkeypatch, enabled):
        import rocketdoo.mail_cli as mail_cli

        compose = tmp_path / "docker-compose.yaml"
        compose.write_text("# rkd:mailpit\n# /rkd:mailpit\n")

        monkeypatch.setattr(mail_cli, "compose_path", lambda *a, **k: compose)
        monkeypatch.setattr(mail_cli, "_odoo_conf_path", lambda *a, **k: None)
        monkeypatch.setattr(mail_cli, "_has_markers", lambda c: True)
        monkeypatch.setattr(mail_cli, "_is_enabled", lambda c: enabled)
        monkeypatch.setattr(mail_cli, "_toggle_compose", lambda c, enable: c)
        monkeypatch.setattr(mail_cli, "run_compose", lambda *a, **k: 0)
        monkeypatch.setattr(mail_cli, "container_running", lambda *a, **k: False)
        return mail_cli

    def test_the_write_runs_when_the_compose_changes(self, tmp_path, monkeypatch):
        mail_cli = self._setup(tmp_path, monkeypatch, enabled=False)
        calls = []
        monkeypatch.setattr(
            mail_cli,
            "_apply_mail_server",
            lambda enable, db: calls.append((enable, db)) or {"db": "dev", "db_error": "", "db_archived": None},
        )

        report = mail_cli._enable_mailpit()

        assert calls == [(True, None)]
        assert report["db"] == "dev"
        assert report["db_error"] == ""

    def test_the_write_also_runs_when_mailpit_was_already_enabled(self, tmp_path, monkeypatch):
        """RF-1.3: the documented fix for a first run that could not reach the database."""
        mail_cli = self._setup(tmp_path, monkeypatch, enabled=True)
        calls = []
        monkeypatch.setattr(
            mail_cli,
            "_apply_mail_server",
            lambda enable, db: calls.append((enable, db)) or {"db": "dev", "db_error": "", "db_archived": None},
        )

        report = mail_cli._enable_mailpit()

        assert report["changed"] is False
        assert calls == [(True, None)]
        assert report["db"] == "dev"

    def test_the_db_flag_is_forwarded(self, tmp_path, monkeypatch):
        mail_cli = self._setup(tmp_path, monkeypatch, enabled=False)
        calls = []
        monkeypatch.setattr(
            mail_cli,
            "_apply_mail_server",
            lambda enable, db: calls.append((enable, db)) or {"db": db, "db_error": "", "db_archived": None},
        )

        mail_cli._enable_mailpit(db="demo")

        assert calls == [(True, "demo")]

    def test_two_runs_are_idempotent(self, tmp_path, monkeypatch):
        mail_cli = self._setup(tmp_path, monkeypatch, enabled=True)
        monkeypatch.setattr(
            mail_cli, "_apply_mail_server", lambda enable, db: {"db": "dev", "db_error": "", "db_archived": None}
        )

        first = mail_cli._enable_mailpit()
        second = mail_cli._enable_mailpit()

        assert first == second


class TestDisableMailpitWritesTheMailServer:
    """RF-2.5: mirrors the enable-side write-in-both-branches fix."""

    def _setup(self, tmp_path, monkeypatch, enabled):
        import rocketdoo.mail_cli as mail_cli

        compose = tmp_path / "docker-compose.yaml"
        compose.write_text("# rkd:mailpit\n# /rkd:mailpit\n")

        monkeypatch.setattr(mail_cli, "compose_path", lambda *a, **k: compose)
        monkeypatch.setattr(mail_cli, "_odoo_conf_path", lambda *a, **k: None)
        monkeypatch.setattr(mail_cli, "_has_markers", lambda c: True)
        monkeypatch.setattr(mail_cli, "_is_enabled", lambda c: enabled)
        monkeypatch.setattr(mail_cli, "_toggle_compose", lambda c, enable: c)
        monkeypatch.setattr(mail_cli, "run_compose", lambda *a, **k: 0)
        monkeypatch.setattr(mail_cli, "container_running", lambda *a, **k: False)
        return mail_cli

    def test_the_write_runs_when_mailpit_gets_disabled(self, tmp_path, monkeypatch):
        mail_cli = self._setup(tmp_path, monkeypatch, enabled=True)
        calls = []
        monkeypatch.setattr(
            mail_cli,
            "_apply_mail_server",
            lambda enable, db: calls.append((enable, db)) or {"db": "dev", "db_error": "", "db_archived": 1},
        )

        report = mail_cli._disable_mailpit()

        assert calls == [(False, None)]
        assert report["db_archived"] == 1

    def test_the_write_also_runs_when_mailpit_was_already_disabled(self, tmp_path, monkeypatch):
        mail_cli = self._setup(tmp_path, monkeypatch, enabled=False)
        calls = []
        monkeypatch.setattr(
            mail_cli,
            "_apply_mail_server",
            lambda enable, db: calls.append((enable, db)) or {"db": "dev", "db_error": "", "db_archived": 0},
        )

        report = mail_cli._disable_mailpit()

        assert report["changed"] is False
        assert calls == [(False, None)]


class TestOutranksMailpit:
    """CA-15: mechanical priority check, independent of from_filter."""

    def test_active_with_sequence_at_or_below_mailpit_outranks(self):
        import rocketdoo.mail_cli as mail_cli

        assert mail_cli._outranks_mailpit({"active": True, "sequence": 1}) is True
        assert mail_cli._outranks_mailpit({"active": True, "sequence": 0}) is True

    def test_active_with_a_higher_sequence_does_not_outrank(self):
        import rocketdoo.mail_cli as mail_cli

        assert mail_cli._outranks_mailpit({"active": True, "sequence": 10}) is False

    def test_an_archived_server_never_outranks(self):
        import rocketdoo.mail_cli as mail_cli

        assert mail_cli._outranks_mailpit({"active": False, "sequence": 1}) is False


class TestMailpitServerLine:
    """CA-14: the four states of the Mailpit row. CA-16: self-exclusion from 'others'."""

    def _mailpit_row(self, mail_cli, active, sequence=1):
        return {
            "id": 1,
            "name": mail_cli.MAILPIT_SERVER_NAME,
            "sequence": sequence,
            "smtp_host": mail_cli.MAILPIT_SMTP_HOST,
            "active": active,
        }

    def _other_row(self, name="Prod SMTP", sequence=10, active=True):
        return {"id": 2, "name": name, "sequence": sequence, "smtp_host": "smtp.prod.example.com", "active": active}

    def test_active(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "mail_servers", lambda db: ([self._mailpit_row(mail_cli, active=True)], ""))

        line, style, others = mail_cli._mailpit_server_line("dev", "")

        assert line == "Active (sequence 1) in dev"
        assert style == "green"
        assert others == []

    def test_archived(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "mail_servers", lambda db: ([self._mailpit_row(mail_cli, active=False)], ""))

        line, style, others = mail_cli._mailpit_server_line("dev", "")

        assert line == "Archived in dev"
        assert style == "dim"
        assert others == []

    def test_not_created(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "mail_servers", lambda db: ([], ""))

        line, style, others = mail_cli._mailpit_server_line("dev", "")

        assert line == "Not created — run rkd mail on"
        assert style == "dim"
        assert others == []

    def test_not_checked_on_a_query_error(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "mail_servers", lambda db: ([], "permission denied"))

        line, style, others = mail_cli._mailpit_server_line("dev", "")

        assert line == "Not checked — permission denied"
        assert style == "dim"
        assert others == []

    def test_not_checked_without_a_resolved_database(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        def _boom(db):
            raise AssertionError("must not query without a resolved database")

        monkeypatch.setattr(mail_cli, "mail_servers", _boom)

        line, style, others = mail_cli._mailpit_server_line(None, "no database container configured")

        assert line == "Not checked — no database container configured"
        assert style == "dim"
        assert others == []

    def test_mailpit_is_never_listed_as_another_server(self, monkeypatch):
        import rocketdoo.mail_cli as mail_cli

        other = self._other_row()
        monkeypatch.setattr(mail_cli, "mail_servers", lambda db: ([self._mailpit_row(mail_cli, active=True), other], ""))

        _, _, others = mail_cli._mailpit_server_line("dev", "")

        assert others == [other]


class TestFormatOtherServers:
    def test_no_servers_reads_none(self):
        import rocketdoo.mail_cli as mail_cli

        assert mail_cli._format_other_servers([]) == "None"

    def test_servers_are_named_with_their_sequence(self):
        import rocketdoo.mail_cli as mail_cli

        others = [{"name": "Prod SMTP", "sequence": 10}, {"name": "Odoo Online", "sequence": 20}]
        assert mail_cli._format_other_servers(others) == '"Prod SMTP" (sequence 10), "Odoo Online" (sequence 20)'


class TestMailStatusCommand:
    """CA-17: mail status never fails on a database error, and warns about conflicts."""

    def test_a_database_error_lands_in_the_row_without_crashing(self, monkeypatch):
        from click.testing import CliRunner
        from rich.console import Console

        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "console", Console(width=200))
        monkeypatch.setattr(mail_cli, "compose_path", lambda *a, **k: None)
        monkeypatch.setattr(mail_cli, "_resolve_db", lambda db: (None, "no database container configured"))

        result = CliRunner().invoke(mail_cli.mail, ["status"])
        output = " ".join(result.output.split())

        assert result.exit_code == 0
        assert "no database container configured" in output
        assert "Mailpit mail server" in output

    def test_a_server_that_outranks_mailpit_triggers_the_strong_warning(self, monkeypatch):
        from click.testing import CliRunner
        from rich.console import Console

        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "console", Console(width=200))
        monkeypatch.setattr(mail_cli, "compose_path", lambda *a, **k: None)
        monkeypatch.setattr(mail_cli, "_resolve_db", lambda db: ("dev", ""))
        monkeypatch.setattr(
            mail_cli,
            "mail_servers",
            lambda db: (
                [
                    self._mailpit_row(mail_cli),
                    {"id": 2, "name": "Prod SMTP", "sequence": 1, "smtp_host": "smtp.prod.example.com", "active": True},
                ],
                "",
            ),
        )

        result = CliRunner().invoke(mail_cli.mail, ["status"])
        output = " ".join(result.output.split())

        assert result.exit_code == 0
        assert "has priority over Mailpit" in output

    def test_other_active_servers_without_priority_show_the_soft_caveat(self, monkeypatch):
        from click.testing import CliRunner
        from rich.console import Console

        import rocketdoo.mail_cli as mail_cli

        monkeypatch.setattr(mail_cli, "console", Console(width=200))
        monkeypatch.setattr(mail_cli, "compose_path", lambda *a, **k: None)
        monkeypatch.setattr(mail_cli, "_resolve_db", lambda db: ("dev", ""))
        monkeypatch.setattr(
            mail_cli,
            "mail_servers",
            lambda db: (
                [
                    self._mailpit_row(mail_cli),
                    {"id": 2, "name": "Prod SMTP", "sequence": 10, "smtp_host": "smtp.prod.example.com", "active": True},
                ],
                "",
            ),
        )

        result = CliRunner().invoke(mail_cli.mail, ["status"])
        output = " ".join(result.output.split())

        assert result.exit_code == 0
        assert "from_filter" in output
        assert "has priority over Mailpit" not in output

    def _mailpit_row(self, mail_cli):
        return {
            "id": 1,
            "name": mail_cli.MAILPIT_SERVER_NAME,
            "sequence": 1,
            "smtp_host": mail_cli.MAILPIT_SMTP_HOST,
            "active": True,
        }
