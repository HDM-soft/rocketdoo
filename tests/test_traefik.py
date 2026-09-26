"""Characterization tests for traefik_cli.py and gui/api/traefik_ops.py (#143 / T8).

`rkd traefik on` has had zero tests: this pins the exact files it writes, the
exact ordered sequence of subprocess calls it issues, and the two guards a
refactor must not lose (refuses without a compose file, refuses when already
enabled). It also pins `off` and `status`, including the "not enabled" path.

`traefik_cli`, `core.compose` and the private names `gui/api/traefik_ops.py`
imports from `traefik_cli` all reach the exact same `subprocess` module
object, so a single `monkeypatch.setattr(traefik_cli.subprocess, "run", ...)`
intercepts every `docker` invocation the whole flow makes, CLI or GUI, direct
or through `core.compose.run_compose`.

Section (c) documents proposal.md's four CLI-vs-GUI divergences (D1-D4) as
`xfail(strict=True)`: `POST /api/traefik/on` never starts Traefik (D1), never
restarts the project (D2), overwrites a hand-edited `traefik.yml` the CLI
would leave alone (D3), and never marks the project `enabled` in a way
`GET /api/traefik/status` recognizes (D4, == CA8). CA7 compares the CLI's and
the GUI's captured `argv` sequences directly and is expected to fail until
T9 routes both through `core.traefik.enable()`. These stay red on purpose:
T9 removes the markers in the same commit that fixes the code, never before.
"""

import stat
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from rocketdoo import traefik_cli  # noqa: E402
from rocketdoo.gui.server import create_app  # noqa: E402
from tests.test_templates import assert_snapshot  # noqa: E402

# `_project_name()` falls back to `Path.cwd().name` without a `name:` key,
# which under `tmp_path` is a random per-run string that would make every
# snapshot and Traefik label unreproducible.
COMPOSE_YAML = "name: demo-project\nservices:\n  web:\n    image: odoo:17.0\n  db:\n    image: postgres:16\n"

# RF6.3 names both of them; a guard is easy to keep on one and lose on the other.
PRESERVED_FILES = ("traefik.yml", "docker-compose.yml")
HAND_EDITED = "# hand-edited by the user\nentryPoints: {}\n"


@pytest.fixture
def traefik_project(project_dir) -> Path:
    (project_dir / "docker-compose.yaml").write_text(COMPOSE_YAML)
    return project_dir


def _make_fake_run(recorded):
    """A `subprocess.run` double that records every call and answers the two
    inspection commands the flow depends on: no `traefik-public` network yet
    (so both CLI and GUI walk the create-network branch) and no `traefik`
    container running yet.
    """

    def fake_run(cmd, *args, **kwargs):
        recorded.append((tuple(cmd), kwargs.get("cwd")))
        if list(cmd[:3]) == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(cmd, 1)
        if list(cmd[:2]) == ["docker", "ps"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0)

    return fake_run


@pytest.fixture
def calls(monkeypatch):
    recorded = []
    monkeypatch.setattr(traefik_cli.subprocess, "run", _make_fake_run(recorded))
    return recorded


def _resolved(cwd):
    return Path(cwd).resolve() if cwd is not None else None


def _normalize_tmp(text: str, project_root: Path) -> str:
    """Replace the absolute tmp_path root with a stable marker.

    `_save_config` stores `traefik_dir` as a resolved absolute path, which
    under `tmp_path` differs on every run and would never match a recorded
    snapshot.
    """
    return text.replace(str(project_root.resolve()), "<PROJECT_ROOT>")


def _gui_client():
    """The app reads `Path.cwd()`, which `project_dir` has already moved."""
    app = create_app()
    return fastapi_testclient.TestClient(app, headers={"X-RKD-Token": app.state.rkd_token})


class TestTraefikOnGuards:
    def test_refuses_without_a_compose_file(self, calls, project_dir):
        result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])

        assert result.exit_code == 0, result.output
        assert "No docker-compose.yaml found" in result.output
        assert calls == []
        assert not (project_dir / ".rkd").exists()

    def test_refuses_when_already_enabled(self, calls, traefik_project):
        override = traefik_project / "docker-compose.override.yml"
        override.write_text("services: {}\n")

        result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])

        assert result.exit_code == 0, result.output
        assert "already enabled" in result.output
        assert calls == []
        assert override.read_text() == "services: {}\n"


class TestTraefikOnLocalMode:
    def test_generates_the_four_artifacts(self, calls, traefik_project):
        result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])
        assert result.exit_code == 0, result.output

        assert_snapshot(
            "traefik_on_local__traefik_compose.txt",
            (traefik_project / "traefik" / "docker-compose.yml").read_text(),
        )
        assert_snapshot(
            "traefik_on_local__traefik_yml.txt",
            (traefik_project / "traefik" / "traefik.yml").read_text(),
        )
        assert_snapshot(
            "traefik_on_local__override.txt",
            (traefik_project / "docker-compose.override.yml").read_text(),
        )
        assert_snapshot(
            "traefik_on_local__config.txt",
            _normalize_tmp((traefik_project / ".rkd" / "traefik.yaml").read_text(), traefik_project),
        )

    def test_argv_sequence(self, calls, traefik_project):
        result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])
        assert result.exit_code == 0, result.output

        traefik_dir = (traefik_project / "traefik").resolve()
        resolved = [(argv, _resolved(cwd)) for argv, cwd in calls]
        assert resolved == [
            (("docker", "network", "inspect", "traefik-public"), None),
            (("docker", "network", "create", "traefik-public"), None),
            (("docker", "compose", "up", "-d"), traefik_dir),
            (("docker", "compose", "up", "-d"), traefik_project.resolve()),
        ]

    @pytest.mark.parametrize("filename", PRESERVED_FILES)
    def test_does_not_overwrite_a_preexisting_file(self, calls, traefik_project, filename):
        """RF6.3: either file written by hand before the wizard runs (e.g.
        after a `traefik off`, which never touches traefik/) must survive.
        Both are covered: they carry different edits a user may have made.
        """
        traefik_dir = traefik_project / "traefik"
        traefik_dir.mkdir()
        (traefik_dir / filename).write_text(HAND_EDITED)

        result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])

        assert result.exit_code == 0, result.output
        assert (traefik_dir / filename).read_text() == HAND_EDITED
        assert (traefik_project / "docker-compose.override.yml").exists()
        assert (traefik_project / ".rkd" / "traefik.yaml").exists()


class TestTraefikOnProductionMode:
    def test_generates_the_four_artifacts(self, calls, traefik_project, monkeypatch):
        monkeypatch.setattr(traefik_cli.Prompt, "ask", lambda *a, **k: "ops@example.com")

        result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "production", "-d", "prod.example.com"])
        assert result.exit_code == 0, result.output

        assert_snapshot(
            "traefik_on_production__traefik_compose.txt",
            (traefik_project / "traefik" / "docker-compose.yml").read_text(),
        )
        assert_snapshot(
            "traefik_on_production__traefik_yml.txt",
            (traefik_project / "traefik" / "traefik.yml").read_text(),
        )
        assert_snapshot(
            "traefik_on_production__override.txt",
            (traefik_project / "docker-compose.override.yml").read_text(),
        )
        assert_snapshot(
            "traefik_on_production__config.txt",
            _normalize_tmp((traefik_project / ".rkd" / "traefik.yaml").read_text(), traefik_project),
        )

    def test_argv_sequence(self, calls, traefik_project, monkeypatch):
        monkeypatch.setattr(traefik_cli.Prompt, "ask", lambda *a, **k: "ops@example.com")

        result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "production", "-d", "prod.example.com"])
        assert result.exit_code == 0, result.output

        traefik_dir = (traefik_project / "traefik").resolve()
        resolved = [(argv, _resolved(cwd)) for argv, cwd in calls]
        assert resolved == [
            (("docker", "network", "inspect", "traefik-public"), None),
            (("docker", "network", "create", "traefik-public"), None),
            (("docker", "compose", "up", "-d"), traefik_dir),
            (("docker", "compose", "up", "-d"), traefik_project.resolve()),
        ]

    def test_acme_json_is_created_with_mode_600(self, calls, traefik_project, monkeypatch):
        monkeypatch.setattr(traefik_cli.Prompt, "ask", lambda *a, **k: "ops@example.com")

        result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "production", "-d", "prod.example.com"])
        assert result.exit_code == 0, result.output

        acme = traefik_project / "traefik" / "certs" / "acme.json"
        assert acme.exists()
        assert stat.S_IMODE(acme.stat().st_mode) == 0o600


class TestTraefikOff:
    def test_removes_the_override_and_restarts_the_project(self, calls, traefik_project):
        (traefik_project / "docker-compose.override.yml").write_text("services: {}\n")

        result = CliRunner().invoke(traefik_cli.traefik_off, [])

        assert result.exit_code == 0, result.output
        assert not (traefik_project / "docker-compose.override.yml").exists()
        assert calls == [(("docker", "compose", "up", "-d"), traefik_project.resolve())]

    def test_reports_not_enabled_and_touches_nothing(self, calls, traefik_project):
        result = CliRunner().invoke(traefik_cli.traefik_off, [])

        assert result.exit_code == 0, result.output
        assert "not enabled" in result.output.lower()
        assert calls == []


class TestTraefikStatus:
    def test_not_configured(self, calls, traefik_project):
        result = CliRunner().invoke(traefik_cli.traefik_status, [])

        assert result.exit_code == 0, result.output
        assert "Not configured" in result.output
        assert calls == [
            (("docker", "ps", "-q", "--filter", "name=traefik"), None),
            (("docker", "network", "inspect", "traefik-public"), None),
        ]

    def test_configured_after_on(self, calls, traefik_project):
        on_result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])
        assert on_result.exit_code == 0, on_result.output
        calls.clear()

        result = CliRunner().invoke(traefik_cli.traefik_status, [])

        assert result.exit_code == 0, result.output
        assert "demo.local" in result.output
        assert "local" in result.output
        assert "Active" in result.output


class TestGuiOnDivergesFromCli:
    """proposal.md D1-D3: `POST /api/traefik/on` reimplements the CLI's steps
    instead of calling into them, so it never starts Traefik, never restarts
    the project, and overwrites files the CLI would leave alone. Each assert
    expresses the CLI's (desired) behavior against the GUI; T9 removes these
    markers in the same commit that unifies both onto `core.traefik.enable()`.
    """

    @pytest.mark.xfail(strict=True, reason="D1: the GUI never runs `docker compose up -d` inside traefik/ (#143 T9)")
    def test_d1_starts_traefik(self, calls, traefik_project):
        client = _gui_client()
        response = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"})
        assert response.json()["ok"] is True

        traefik_dir = (traefik_project / "traefik").resolve()
        resolved = [(argv, _resolved(cwd)) for argv, cwd in calls]
        assert (("docker", "compose", "up", "-d"), traefik_dir) in resolved, resolved

    @pytest.mark.xfail(strict=True, reason="D2: the GUI never restarts the project with `docker compose up -d` (#143 T9)")
    def test_d2_restarts_the_project(self, calls, traefik_project):
        client = _gui_client()
        response = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"})
        assert response.json()["ok"] is True

        resolved = [(argv, _resolved(cwd)) for argv, cwd in calls]
        assert (("docker", "compose", "up", "-d"), traefik_project.resolve()) in resolved, resolved

    @pytest.mark.xfail(strict=True, reason="D3: the GUI overwrites both traefik/ files unconditionally (#143 T9)")
    @pytest.mark.parametrize("filename", PRESERVED_FILES)
    def test_d3_preserves_a_hand_edited_file(self, calls, traefik_project, filename):
        traefik_dir = traefik_project / "traefik"
        traefik_dir.mkdir()
        (traefik_dir / filename).write_text(HAND_EDITED)

        client = _gui_client()
        response = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"})
        assert response.json()["ok"] is True

        assert (traefik_dir / filename).read_text() == HAND_EDITED


class TestGuiStatusDivergesFromCli:
    """proposal.md D4 / CA8: the CLI's `.rkd/traefik.yaml` never carries an
    `enabled` key, but `GET /api/traefik/status` reads `cfg.get("enabled",
    False)` — so the GUI reports a project enabled by the CLI as disabled.
    """

    @pytest.mark.xfail(strict=True, reason="D4: status reads a cfg['enabled'] key the CLI never writes (#143 T9 / CA8)")
    def test_d4_reports_enabled_after_cli_on(self, calls, traefik_project):
        on_result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])
        assert on_result.exit_code == 0, on_result.output

        client = _gui_client()
        body = client.get("/api/traefik/status").json()
        assert body["enabled"] is True


def _relative_cwd(cwd, root: Path):
    return None if cwd is None else Path(cwd).resolve().relative_to(root.resolve())


class TestArgvParityAcrossCliAndGui:
    """CA7: run the same `on` request through the CLI and through the GUI,
    against two independent project directories, and compare the captured
    `argv` sequences (cwd expressed relative to each project root, so the
    two different tmp_path roots do not themselves break the comparison).
    """

    @pytest.mark.xfail(strict=True, reason="CA7: D1+D2 leave the GUI's argv sequence short of the CLI's (#143 T9)")
    def test_ca7_cli_and_gui_issue_the_same_calls(self, monkeypatch, tmp_path):
        cli_dir = tmp_path / "cli-project"
        gui_dir = tmp_path / "gui-project"
        cli_dir.mkdir()
        gui_dir.mkdir()
        (cli_dir / "docker-compose.yaml").write_text(COMPOSE_YAML)
        (gui_dir / "docker-compose.yaml").write_text(COMPOSE_YAML)

        cli_calls = []
        monkeypatch.chdir(cli_dir)
        monkeypatch.setattr(traefik_cli.subprocess, "run", _make_fake_run(cli_calls))
        cli_result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])
        assert cli_result.exit_code == 0, cli_result.output

        gui_calls = []
        monkeypatch.chdir(gui_dir)
        monkeypatch.setattr(traefik_cli.subprocess, "run", _make_fake_run(gui_calls))
        client = _gui_client()
        response = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"})
        assert response.json()["ok"] is True

        cli_normalized = [(argv, _relative_cwd(cwd, cli_dir)) for argv, cwd in cli_calls]
        gui_normalized = [(argv, _relative_cwd(cwd, gui_dir)) for argv, cwd in gui_calls]
        assert cli_normalized == gui_normalized
