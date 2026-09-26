"""Characterization tests for `POST /api/mail/on` and `/api/mail/off` (#143 / T4).

Before this file, `/api/mail/on` had no test at all and `/api/mail/off` was
only exercised against an empty directory in `tests/test_gui_api.py`, where it
returns early without touching any file. Neither endpoint's actual effect —
the docker-compose.yaml toggle, the odoo.conf rewrite, or the sequence of
docker/psql calls — was under test. This is the net T5 (`mail_cli` ->
`core/mailpit.py`) has to pass through unmodified.

The project fixture comes from the real generator (`scaffold_project()` +
`init_from_profile()`), same reasoning as `tests/test_project_info.py`: the
compose it produces carries the real `# rkd:mailpit` markers, so the test
depends on the contract instead of a hand-written compose file that can drift
out of sync with the template.

Both endpoints call `mail_cli._enable_mailpit()`/`_disable_mailpit()` with no
`db`, so the ir.mail_server write always goes through `_resolve_db(None)`.
`databases_result`/`enable_mailpit_server`/`disable_mailpit_server` are faked
so the test never needs a live Postgres container, and `run_compose`/
`container_running` are faked so it never needs a live Docker daemon either.
All five fakes funnel into one ordered list, since RF10.2 assays the sequence
of external commands as a list, not a set.
"""

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from rocketdoo.gui.server import create_app  # noqa: E402
from rocketdoo.init_project import init_from_profile  # noqa: E402
from rocketdoo.mail_cli import _is_enabled, _toggle_compose, _toggle_smtp  # noqa: E402
from rocketdoo.scaffold import scaffold_project  # noqa: E402


def _active_smtp_keys(conf: str) -> dict[str, str]:
    values = {}
    for line in conf.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _enable_manually(project_dir):
    """Put the project in the "already enabled" state without going through
    the endpoint under test, so its call list stays clean for the assertion.
    """
    compose = project_dir / "docker-compose.yaml"
    compose.write_text(_toggle_compose(compose.read_text(), enable=True))

    conf = project_dir / "config" / "odoo.conf"
    conf.write_text(_toggle_smtp(conf.read_text(), enable=True))


@pytest.fixture
def mailpit_project(project_dir):
    """A generated Odoo 18 Community project with the Mailpit markers, disabled."""
    scaffold_project()
    init_from_profile("odoo18-ce", project_name="demo-project")
    return project_dir


@pytest.fixture
def client(project_dir):
    app = create_app()
    return fastapi_testclient.TestClient(app, headers={"X-RKD-Token": app.state.rkd_token})


@pytest.fixture
def mailpit_double(monkeypatch):
    """Fakes docker compose and the Postgres-backed ir.mail_server write.

    One monkeypatch point per external call, all captured into a single
    ordered list, so a test can assert the exact sequence the endpoint
    produces instead of one assertion per faked function.
    """
    import rocketdoo.mail_cli as mail_cli

    calls = []

    def fake_run_compose(*args, cwd=None):
        calls.append(("run_compose", args))
        return 0

    def fake_container_running(service, cwd=None):
        calls.append(("container_running", (service,)))
        return False

    def fake_databases_result(compose_data=None):
        calls.append(("databases_result", ()))
        return (["dev"], "")

    def fake_enable_mailpit_server(db):
        calls.append(("enable_mailpit_server", (db,)))
        return ""

    def fake_disable_mailpit_server(db):
        calls.append(("disable_mailpit_server", (db,)))
        return (1, "")

    monkeypatch.setattr(mail_cli, "run_compose", fake_run_compose)
    monkeypatch.setattr(mail_cli, "container_running", fake_container_running)
    monkeypatch.setattr(mail_cli, "databases_result", fake_databases_result)
    monkeypatch.setattr(mail_cli, "enable_mailpit_server", fake_enable_mailpit_server)
    monkeypatch.setattr(mail_cli, "disable_mailpit_server", fake_disable_mailpit_server)

    return calls


class TestMailOnHappyPath:
    def test_reports_ok(self, client, mailpit_project, mailpit_double):
        response = client.post("/api/mail/on")

        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_the_compose_block_is_uncommented(self, client, mailpit_project, mailpit_double):
        client.post("/api/mail/on")

        compose = (mailpit_project / "docker-compose.yaml").read_text()
        assert _is_enabled(compose) is True
        assert "  mailpit:\n" in compose

    def test_odoo_conf_points_at_mailpit(self, client, mailpit_project, mailpit_double):
        client.post("/api/mail/on")

        conf = (mailpit_project / "config" / "odoo.conf").read_text()
        active = _active_smtp_keys(conf)
        assert active["smtp_server"] == "mailpit"
        assert active["smtp_port"] == "1025"
        assert active["smtp_ssl"] == "False"

    def test_the_argv_sequence(self, client, mailpit_project, mailpit_double):
        client.post("/api/mail/on")

        assert mailpit_double == [
            ("run_compose", ("up", "-d", "mailpit")),
            ("databases_result", ()),
            ("enable_mailpit_server", ("dev",)),
            ("container_running", ("web",)),
        ]


class TestMailOnIsIdempotent:
    def test_a_second_call_does_not_toggle_the_compose_file_again(self, client, mailpit_project, mailpit_double):
        client.post("/api/mail/on")
        first = (mailpit_project / "docker-compose.yaml").read_text()
        mailpit_double.clear()

        response = client.post("/api/mail/on")

        assert response.json() == {"ok": True}
        assert (mailpit_project / "docker-compose.yaml").read_text() == first

    def test_a_second_call_still_writes_the_mail_server(self, client, mailpit_project, mailpit_double):
        client.post("/api/mail/on")
        mailpit_double.clear()

        client.post("/api/mail/on")

        assert mailpit_double == [
            ("databases_result", ()),
            ("enable_mailpit_server", ("dev",)),
        ]


class TestMailOffHappyPath:
    def test_reports_ok(self, client, mailpit_project, mailpit_double):
        _enable_manually(mailpit_project)

        response = client.post("/api/mail/off")

        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_the_compose_block_is_commented_back_out(self, client, mailpit_project, mailpit_double):
        _enable_manually(mailpit_project)

        client.post("/api/mail/off")

        compose = (mailpit_project / "docker-compose.yaml").read_text()
        assert _is_enabled(compose) is False
        assert "#mailpit:" in compose

    def test_odoo_conf_is_restored(self, client, mailpit_project, mailpit_double):
        _enable_manually(mailpit_project)

        client.post("/api/mail/off")

        conf = (mailpit_project / "config" / "odoo.conf").read_text()
        active = _active_smtp_keys(conf)
        assert "smtp_server" not in active
        assert "; smtp_server = localhost" in conf

    def test_the_argv_sequence(self, client, mailpit_project, mailpit_double):
        _enable_manually(mailpit_project)

        client.post("/api/mail/off")

        assert mailpit_double == [
            ("run_compose", ("stop", "mailpit")),
            ("run_compose", ("rm", "-f", "mailpit")),
            ("databases_result", ()),
            ("disable_mailpit_server", ("dev",)),
            ("container_running", ("web",)),
        ]


class TestMailOffOnAProjectThatNeverEnabledIt:
    def test_reports_ok_without_touching_the_compose_file(self, client, mailpit_project, mailpit_double):
        before = (mailpit_project / "docker-compose.yaml").read_text()

        response = client.post("/api/mail/off")

        assert response.json() == {"ok": True}
        assert (mailpit_project / "docker-compose.yaml").read_text() == before

    def test_no_docker_command_runs(self, client, mailpit_project, mailpit_double):
        client.post("/api/mail/off")

        assert mailpit_double == [
            ("databases_result", ()),
            ("disable_mailpit_server", ("dev",)),
        ]


class TestNoComposeFile:
    def test_mail_on_reports_the_reason(self, client, project_dir, mailpit_double):
        response = client.post("/api/mail/on")

        assert response.status_code == 200
        assert response.json() == {"ok": False, "error": "No docker-compose.yaml found."}
        assert mailpit_double == []

    def test_mail_off_reports_the_reason(self, client, project_dir, mailpit_double):
        response = client.post("/api/mail/off")

        assert response.status_code == 200
        assert response.json() == {"ok": False, "error": "No docker-compose.yaml found."}
        assert mailpit_double == []
