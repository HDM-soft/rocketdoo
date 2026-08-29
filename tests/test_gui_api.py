"""Smoke tests for the GUI's REST API.

The broken endpoints fixed in #136 imported their implementation lazily and
turned the resulting ImportError into an HTTP 200 ``{"ok": false}``. Importing
the module was not enough to catch that — the handler had to actually run.

These tests call every endpoint against an empty directory. They assert the
handlers execute and answer coherently, not that Docker does anything: an
endpoint reporting "no compose file" is a pass, one blowing up or reporting a
missing helper is not.
"""

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
