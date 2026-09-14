"""Smoke tests for the GUI's REST API.

The broken endpoints fixed in #136 imported their implementation lazily and
turned the resulting ImportError into an HTTP 200 ``{"ok": false}``. Importing
the module was not enough to catch that — the handler had to actually run.

These tests call every endpoint against an empty directory. They assert the
handlers execute and answer coherently, not that Docker does anything: an
endpoint reporting "no compose file" is a pass, one blowing up or reporting a
missing helper is not.
"""

import asyncio
import sys

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from rocketdoo.gui.server import create_app  # noqa: E402


@pytest.fixture
def client(project_dir):
    """A TestClient whose working directory is an empty project dir."""
    return fastapi_testclient.TestClient(create_app())


GET_ENDPOINTS = [
    "/api/project",
    "/api/project/containers",
    "/api/modules",
    "/api/mail/status",
    "/api/traefik/status",
    "/api/instances",
    "/api/workspace",
    "/api/gitman",
    "/api/odoo/databases",
]

# Endpoints that only inspect or tear down state, safe to call on an empty dir.
POST_ENDPOINTS = [
    "/api/mail/off",
    "/api/traefik/off",
]


@pytest.mark.parametrize("path", GET_ENDPOINTS)
def test_get_endpoint_responds(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


@pytest.mark.parametrize("path", POST_ENDPOINTS)
def test_post_endpoint_executes_its_handler(client, path):
    """A handler that cannot run reports a missing name; that is the failure."""
    response = client.post(path)
    assert response.status_code == 200

    body = response.json()
    error = str(body.get("error", ""))
    for symptom in ("cannot import name", "is not defined", "has no attribute"):
        assert symptom not in error, f"{path} did not execute: {error}"


def test_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "html" in response.headers["content-type"]


class TestProjectEndpoints:
    def test_reports_no_project_in_an_empty_dir(self, client):
        assert client.get("/api/project").json()["exists"] is False

    def test_containers_returns_a_list(self, client):
        assert client.get("/api/project/containers").json()["containers"] == []

    def test_modules_are_empty_without_addons(self, client):
        assert client.get("/api/modules").json()["modules"] == []

    def test_modules_finds_a_scaffolded_addon(self, client, project_dir):
        mod = project_dir / "addons" / "demo_module"
        mod.mkdir(parents=True)
        (mod / "__manifest__.py").write_text("{'name': 'Demo', 'version': '16.0.1.0.0'}\n")
        names = [m["name"] for m in client.get("/api/modules").json()["modules"]]
        assert "demo_module" in names


class TestServiceStatusEndpoints:
    def test_mail_status_reports_disabled(self, client):
        body = client.get("/api/mail/status").json()
        assert body["enabled"] is False

    def test_traefik_status_reports_disabled(self, client):
        assert client.get("/api/traefik/status").json()["enabled"] is False

    def test_instances_are_empty_without_config(self, client):
        assert client.get("/api/instances").json()["instances"] == []


class TestOdooEndpoints:
    def test_databases_reports_the_reason_without_a_container(self, client):
        body = client.get("/api/odoo/databases").json()
        assert body["databases"] == []
        assert body["error"]

    def test_unknown_database_is_rejected_before_querying_it(self, client, monkeypatch):
        def _must_not_run(db):
            raise AssertionError("module_states must not run for an unknown database")

        monkeypatch.setattr("rocketdoo.gui.api.odoo.module_states", _must_not_run)

        body = client.get("/api/odoo/module-states?db=nope").json()
        assert body["states"] == {}
        assert body["error"] == "unknown database"


class TestBuildUpdateCommand:
    """`build_update_command` is the only barrier between the browser and the
    Odoo CLI: it must reject by list membership, never by regex or escaping.
    """

    def _allow_only(self, monkeypatch, *databases):
        monkeypatch.setattr("rocketdoo.gui.api.odoo.list_databases", lambda: list(databases))

    def test_the_argv_matches_ca8_exactly(self, project_dir, addons_tree, monkeypatch):
        from rocketdoo.gui.api.odoo import build_update_command

        self._allow_only(monkeypatch, "dev")

        cmd, error = build_update_command("sale_extension", "dev")

        assert error == ""
        assert cmd == [
            "docker",
            "compose",
            "exec",
            "-T",
            "web",
            "odoo",
            "-d",
            "dev",
            "-u",
            "sale_extension",
            "--stop-after-init",
            "--log-level=info",
        ]

    def test_a_flag_disguised_as_a_module_is_rejected(self, project_dir, addons_tree, monkeypatch):
        """Rejected because it is a flag, not merely because it does not exist.

        The directory is created on purpose: without it the membership check
        rejects the name for the wrong reason and the test cannot fail.
        """
        from rocketdoo.gui.api.odoo import build_update_command

        evil = addons_tree / "--load-language=es"
        evil.mkdir()
        (evil / "__manifest__.py").write_text("{'name': 'Evil'}\n")
        self._allow_only(monkeypatch, "dev")

        cmd, error = build_update_command("--load-language=es", "dev")

        assert cmd is None
        assert error

    def test_a_flag_disguised_as_a_database_is_rejected(self, project_dir, addons_tree, monkeypatch):
        from rocketdoo.gui.api.odoo import build_update_command

        monkeypatch.setattr("rocketdoo.gui.api.odoo.list_databases", lambda *a, **k: ["--load-language=es"])

        cmd, error = build_update_command("sale_extension", "--load-language=es")

        assert cmd is None
        assert error

    def test_a_module_outside_addons_is_rejected(self, project_dir, addons_tree, monkeypatch):
        from rocketdoo.gui.api.odoo import build_update_command

        self._allow_only(monkeypatch, "dev")

        cmd, error = build_update_command("not_a_real_module", "dev")

        assert cmd is None
        assert error

    def test_a_database_outside_the_list_is_rejected(self, project_dir, addons_tree, monkeypatch):
        from rocketdoo.gui.api.odoo import build_update_command

        self._allow_only(monkeypatch, "dev")

        cmd, error = build_update_command("sale_extension", "unknown")

        assert cmd is None
        assert error


class TestStreamProcess:
    """`_stream_process` is the single implementation shared by every
    websocket route that runs a CLI command and streams its output.
    """

    class _FakeWebSocket:
        def __init__(self):
            self.sent = []
            self.closed = False

        async def send_text(self, text):
            self.sent.append(text)

        async def close(self):
            self.closed = True

    def test_streams_output_and_reports_a_clean_exit(self):
        from rocketdoo.gui.server import _stream_process

        ws = self._FakeWebSocket()
        asyncio.run(_stream_process(ws, [sys.executable, "-c", "print('hi')"]))

        assert ws.sent == ["hi", "\x00exit:0"]
        assert ws.closed is True

    def test_reports_a_non_zero_exit_code(self):
        from rocketdoo.gui.server import _stream_process

        ws = self._FakeWebSocket()
        asyncio.run(_stream_process(ws, [sys.executable, "-c", "raise SystemExit(3)"]))

        assert ws.sent[-1] == "\x00exit:3"


class TestDockerActionWebSocket:
    """Regression coverage for `/ws/docker/{action}` after extracting
    `_stream_process` (CA13): an unknown action must still error out without
    ever touching `_stream_process`.
    """

    def test_an_unknown_action_reports_error_and_exit(self, client):
        with client.websocket_connect("/ws/docker/nope") as ws:
            assert ws.receive_text() == "[error] Unknown action: nope"
            assert ws.receive_text() == "\x00exit:1"


class TestOdooUpdateWebSocket:
    """`/ws/odoo/update` validates before it ever spawns a process (CA9)."""

    def test_invalid_arguments_report_an_error_and_exit(self, client, project_dir):
        with client.websocket_connect("/ws/odoo/update?module=x&db=y") as ws:
            assert ws.receive_text().startswith("[error] ")
            assert ws.receive_text() == "\x00exit:1"

    def test_invalid_arguments_never_spawn_a_process(self, client, project_dir, monkeypatch):
        async def _must_not_run(*args, **kwargs):
            raise AssertionError("build_update_command rejected this; nothing should run")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _must_not_run)

        with client.websocket_connect("/ws/odoo/update?module=x&db=y") as ws:
            assert ws.receive_text().startswith("[error] ")
            assert ws.receive_text() == "\x00exit:1"


class TestInstancesRoundTrip:
    """The GUI must be able to save a config its own reader and the deployers accept.

    Before #138 this round trip was broken in both directions: the GUI wrote
    the connection flat while the deployers read it nested (`KeyError: 'vps'`),
    and the listing endpoint did not descend into `environments`, so it showed
    a phantom instance literally named "environments".
    """

    PAYLOAD = {
        "environments": {
            "stage": {
                "type": "docker",
                "host": "vps.example.com",
                "user": "ubuntu",
                "ssh_key": "~/.ssh/id_ed25519",
                "odoo_version": "17.0",
                "domain": "stage.example.com",
                "email": "ops@example.com",
            }
        }
    }

    def test_saving_reports_success(self, client):
        assert client.post("/api/instances/init", json=self.PAYLOAD).json()["ok"] is True

    def test_the_saved_file_is_in_the_canonical_nested_shape(self, client, project_dir):
        import yaml

        client.post("/api/instances/init", json=self.PAYLOAD)
        saved = yaml.safe_load((project_dir / ".rkd" / "instance.yaml").read_text())
        stage = saved["environments"]["stage"]
        assert stage["vps"]["host"] == "vps.example.com"
        assert "host" not in stage

    def test_the_gui_can_read_back_what_it_saved(self, client):
        client.post("/api/instances/init", json=self.PAYLOAD)
        instances = client.get("/api/instances").json()["instances"]
        assert [i["env"] for i in instances] == ["stage"]
        assert instances[0]["host"] == "vps.example.com"

    def test_no_phantom_environments_entry(self, client):
        client.post("/api/instances/init", json=self.PAYLOAD)
        names = [i["env"] for i in client.get("/api/instances").json()["instances"]]
        assert "environments" not in names

    def test_the_gui_field_names_are_translated(self, client, project_dir):
        import yaml

        client.post("/api/instances/init", json=self.PAYLOAD)
        stage = yaml.safe_load((project_dir / ".rkd" / "instance.yaml").read_text())["environments"]["stage"]
        assert stage["traefik_email"] == "ops@example.com"

    def test_an_admin_password_is_always_written(self, client, project_dir):
        """The GUI form does not ask for one; odoo.conf still needs it."""
        import yaml

        client.post("/api/instances/init", json=self.PAYLOAD)
        stage = yaml.safe_load((project_dir / ".rkd" / "instance.yaml").read_text())["environments"]["stage"]
        assert stage["admin_passwd"]

    def test_the_deployer_accepts_the_saved_config(self, client, project_dir):
        """The end of the round trip: this used to raise KeyError: 'vps'."""
        from rocketdoo.core.instance.config_manager import InstanceConfigManager
        from rocketdoo.core.instance.deployer_docker import DockerInstanceDeployer

        client.post("/api/instances/init", json=self.PAYLOAD)
        env_cfg = InstanceConfigManager(project_dir).get_env("stage")
        deployer = DockerInstanceDeployer("stage", env_cfg, project_dir)
        assert deployer.host == "vps.example.com"

    def test_an_incomplete_environment_is_reported_not_silently_saved(self, client):
        response = client.post(
            "/api/instances/init",
            json={"environments": {"stage": {"type": "docker", "host": ""}}},
        )
        body = response.json()
        assert body["ok"] is True
        assert "vps.host" in body["incomplete"]["stage"]


class TestCORSPolicy:
    """The GUI API must only be callable from the page it serves.

    These endpoints drive Docker and browse the filesystem. Binding to
    127.0.0.1 is no protection on its own: the request comes from the user's
    own browser, so any site they visit while `rkd gui` runs is already on
    localhost as far as the server is concerned. With allow_origins=["*"] and
    allow_credentials=True, Starlette echoes the caller's Origin back and the
    browser lets that site read the response.
    """

    EVIL = "https://malicious.example"

    def test_a_foreign_origin_cannot_read_responses(self, client):
        response = client.get("/api/workspace", headers={"Origin": self.EVIL})
        allowed = response.headers.get("access-control-allow-origin")
        assert allowed != self.EVIL
        assert allowed != "*"

    def test_a_foreign_origin_preflight_is_rejected(self, client):
        response = client.options(
            "/api/docker/down",
            headers={
                "Origin": self.EVIL,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code >= 400

    @pytest.mark.parametrize("origin", ["http://localhost:8070", "http://127.0.0.1:8070"])
    def test_the_gui_own_origins_are_allowed(self, client, origin):
        response = client.get("/api/workspace", headers={"Origin": origin})
        assert response.headers.get("access-control-allow-origin") == origin

    def test_the_wildcard_is_never_used(self, client):
        """allow_origins=["*"] is what made this exploitable."""
        response = client.get("/api/workspace", headers={"Origin": "http://localhost:8070"})
        assert response.headers.get("access-control-allow-origin") != "*"

    def test_the_spa_is_still_served(self, client):
        """Same-origin requests need no CORS; the page itself must still load."""
        assert client.get("/").status_code == 200


class TestLocalOrigins:
    def test_defaults_to_both_loopback_names(self):
        from rocketdoo.gui.server import local_origins

        assert local_origins() == ["http://localhost:8070", "http://127.0.0.1:8070"]

    def test_follows_a_custom_port(self):
        """`rkd gui --port 9090` must allow the port the user actually opens."""
        from rocketdoo.gui.server import local_origins

        assert local_origins(port=9090) == ["http://localhost:9090", "http://127.0.0.1:9090"]

    def test_an_explicit_external_host_is_added(self):
        from rocketdoo.gui.server import local_origins

        assert "http://192.168.1.10:8070" in local_origins("192.168.1.10", 8070)

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "0.0.0.0", "::1"])
    def test_loopback_hosts_add_nothing_extra(self, host):
        from rocketdoo.gui.server import local_origins

        assert len(local_origins(host)) == 2

    def test_no_origin_is_a_wildcard(self):
        from rocketdoo.gui.server import local_origins

        assert "*" not in local_origins("192.168.1.10", 9090)


def test_the_app_honours_the_port_it_was_created_with():
    """`rkd gui --port N` passes N through, so CORS matches the real URL."""
    from rocketdoo.gui.server import create_app

    client = fastapi_testclient.TestClient(create_app(port=9090))
    response = client.get("/api/workspace", headers={"Origin": "http://localhost:9090"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:9090"
