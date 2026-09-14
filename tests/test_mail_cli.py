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

        report = mail_cli._enable_mailpit()

        assert report["conf_updated"] is True
        assert _active_keys(conf.read_text())["smtp_server"] == "mailpit"
