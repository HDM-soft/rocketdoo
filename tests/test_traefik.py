"""Characterization tests for traefik_cli.py and gui/api/traefik_ops.py (#143 / T8, T9).

`rkd traefik on` has had zero tests: this pins the exact files it writes, the
exact ordered sequence of subprocess calls it issues, and the two guards a
refactor must not lose (refuses without a compose file, refuses when already
enabled). It also pins `off` and `status`, including the "not enabled" path.

`traefik_cli`, `core.traefik`, `core.compose` and `gui/api/traefik_ops.py` all
reach the exact same `subprocess` module object (imported plainly in each), so
a single `monkeypatch.setattr(subprocess, "run", ...)` intercepts
every `docker` invocation the whole flow makes, CLI or GUI, direct or through
`core.compose.run_compose`.

Section (c) used to document proposal.md's four CLI-vs-GUI divergences (D1-D4)
as `xfail(strict=True)`: `POST /api/traefik/on` never started Traefik (D1),
never restarted the project (D2), overwrote a hand-edited `traefik.yml` the
CLI would leave alone (D3), and never marked the project `enabled` in a way
`GET /api/traefik/status` recognized (D4, == CA8). CA7 compared the CLI's and
the GUI's captured `argv` sequences directly. #143 T9 routed both onto
`core.traefik.enable()`/`disable()`/`status()`, so the four divergences are
closed and these assertions pass without any marker.
"""

import stat
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from rocketdoo import traefik_cli  # noqa: E402
from rocketdoo.core import traefik as core_traefik  # noqa: E402
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
    monkeypatch.setattr(subprocess, "run", _make_fake_run(recorded))
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


class TestTraefikStartFailureIsReportedNotRaised:
    """RF6.5: a `docker compose up -d` that fails to start Traefik is a step
    that failed while the operation continues (RF1.4), not an impossible
    operation: `enable()` must not raise, it reports `traefik_started: False`
    through the callback and still finishes the rest of the flow (override,
    config, project restart).
    """

    def test_does_not_raise_and_reports_the_failure(self, traefik_project, monkeypatch):
        traefik_dir = (traefik_project / "traefik").resolve()
        recorded = []

        def fake_run(cmd, *args, **kwargs):
            recorded.append((tuple(cmd), kwargs.get("cwd")))
            if list(cmd[:3]) == ["docker", "network", "inspect"]:
                return subprocess.CompletedProcess(cmd, 1)
            if list(cmd[:2]) == ["docker", "ps"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if tuple(cmd) == ("docker", "compose", "up", "-d") and Path(kwargs.get("cwd")).resolve() == traefik_dir:
                return subprocess.CompletedProcess(cmd, 1)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])

        assert result.exit_code == 0, result.output
        assert "Could not start Traefik" in result.output
        assert (traefik_project / "docker-compose.override.yml").exists()
        assert (traefik_project / ".rkd" / "traefik.yaml").exists()
        # The flow must continue past the failed step: the project restart
        # (`docker compose up -d` at the project root, not traefik_dir) still
        # has to run. A version that raises instead of warning never reaches it.
        resolved = [(argv, _resolved(cwd)) for argv, cwd in recorded]
        assert (("docker", "compose", "up", "-d"), traefik_project.resolve()) in resolved, resolved


class TestTraefikOff:
    def test_removes_the_override_and_restarts_the_project(self, calls, traefik_project):
        (traefik_project / "docker-compose.override.yml").write_text("services: {}\n")

        result = CliRunner().invoke(traefik_cli.traefik_off, [])

        assert result.exit_code == 0, result.output
        assert not (traefik_project / "docker-compose.override.yml").exists()
        resolved = [(argv, _resolved(cwd)) for argv, cwd in calls]
        assert resolved == [(("docker", "compose", "up", "-d"), traefik_project.resolve())]

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

    def test_tolerates_a_config_saved_by_the_pre_t9_gui(self, calls, traefik_project):
        """D-d: a project enabled from the GUI before #143 T9 has `enabled:
        true` and no `traefik_dir` key at all (the old `_save_config({"enabled":
        True, "mode": ..., "domain": ..., "email": ...})`). `status()` must
        read it without raising and fall back to `None` for `traefik_dir`.
        """
        (traefik_project / ".rkd").mkdir()
        (traefik_project / ".rkd" / "traefik.yaml").write_text("enabled: true\nmode: local\ndomain: old.local\nemail: ''\n")
        (traefik_project / "docker-compose.override.yml").write_text("services: {}\n")

        report = core_traefik.status(traefik_project)
        assert report["mode"] == "local"
        assert report["domain"] == "old.local"
        assert report["enabled"] is True
        assert report["traefik_dir"] is None

        result = CliRunner().invoke(traefik_cli.traefik_status, [])
        assert result.exit_code == 0, result.output
        assert "old.local" in result.output


class TestGuiOnDivergesFromCli:
    """proposal.md D1-D3, closed by #143 T9: `POST /api/traefik/on` used to
    reimplement the CLI's steps instead of calling into them, so it never
    started Traefik, never restarted the project, and overwrote files the CLI
    would leave alone. Now both routes go through `core.traefik.enable()`.
    """

    def test_d1_starts_traefik(self, calls, traefik_project):
        client = _gui_client()
        response = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"})
        assert response.json()["ok"] is True

        traefik_dir = (traefik_project / "traefik").resolve()
        resolved = [(argv, _resolved(cwd)) for argv, cwd in calls]
        assert (("docker", "compose", "up", "-d"), traefik_dir) in resolved, resolved

    def test_d2_restarts_the_project(self, calls, traefik_project):
        client = _gui_client()
        response = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"})
        assert response.json()["ok"] is True

        resolved = [(argv, _resolved(cwd)) for argv, cwd in calls]
        assert (("docker", "compose", "up", "-d"), traefik_project.resolve()) in resolved, resolved

    @pytest.mark.parametrize("filename", PRESERVED_FILES)
    def test_d3_preserves_a_hand_edited_file(self, calls, traefik_project, filename):
        traefik_dir = traefik_project / "traefik"
        traefik_dir.mkdir()
        (traefik_dir / filename).write_text(HAND_EDITED)

        client = _gui_client()
        response = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"})
        assert response.json()["ok"] is True

        assert (traefik_dir / filename).read_text() == HAND_EDITED


class TestGuiOnGuards:
    """The guards `enable()` enforces are the GUI's only ones: unlike the CLI,
    nothing runs before the request reaches core. The fifth CLI-vs-GUI
    divergence found while writing T8 was exactly this -- the endpoint used to
    overwrite the override of an already-configured project without a word.
    """

    def test_refuses_when_already_enabled(self, calls, traefik_project):
        (traefik_project / "docker-compose.override.yml").write_text("services: {}\n")

        client = _gui_client()
        body = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"}).json()

        assert body["ok"] is False
        assert "already enabled" in body["error"]
        assert (traefik_project / "docker-compose.override.yml").read_text() == "services: {}\n"
        assert calls == []

    def test_refuses_without_a_compose_file(self, calls, project_dir):
        client = _gui_client()
        body = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"}).json()

        assert body["ok"] is False
        assert calls == []
        assert not (project_dir / ".rkd").exists()


class TestEnableHonoursProjectRoot:
    """RF1.1: `enable()` takes the project root as an argument, so a relative
    `traefik_dir` belongs to that project and not to wherever the calling
    process happens to be standing. `.resolve()` on its own would silently
    anchor it to the process cwd -- the assumption this module exists to drop.
    """

    def test_a_relative_traefik_dir_lands_inside_the_project(self, monkeypatch, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "docker-compose.yaml").write_text(COMPOSE_YAML)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        recorded = []
        monkeypatch.setattr(subprocess, "run", _make_fake_run(recorded))

        report = core_traefik.enable(project, mode="local", domain="demo.local")

        assert Path(report["traefik_dir"]) == (project / "traefik").resolve()
        assert (project / "traefik" / "traefik.yml").exists()
        assert not (elsewhere / "traefik").exists()


class TestGuiStatusDivergesFromCli:
    """proposal.md D4 / CA8, closed by #143 T9: the CLI's `.rkd/traefik.yaml`
    never carries an `enabled` key. `GET /api/traefik/status` used to read
    `cfg.get("enabled", False)` and always report the GUI's view as disabled;
    it now derives `enabled` from `core.traefik.status()`, like the CLI does.
    """

    def test_d4_reports_enabled_after_cli_on(self, calls, traefik_project):
        on_result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])
        assert on_result.exit_code == 0, on_result.output

        client = _gui_client()
        body = client.get("/api/traefik/status").json()
        assert body["enabled"] is True


def _relative_cwd(cwd, root: Path):
    return None if cwd is None else Path(cwd).resolve().relative_to(root.resolve())


class TestArgvParityAcrossCliAndGui:
    """CA7, closed by #143 T9: run the same `on` request through the CLI and
    through the GUI, against two independent project directories, and compare
    the captured `argv` sequences (cwd expressed relative to each project
    root, so the two different tmp_path roots do not themselves break the
    comparison). Both routes now issue the exact same sequence.
    """

    def test_ca7_cli_and_gui_issue_the_same_calls(self, monkeypatch, tmp_path):
        cli_dir = tmp_path / "cli-project"
        gui_dir = tmp_path / "gui-project"
        cli_dir.mkdir()
        gui_dir.mkdir()
        (cli_dir / "docker-compose.yaml").write_text(COMPOSE_YAML)
        (gui_dir / "docker-compose.yaml").write_text(COMPOSE_YAML)

        cli_calls = []
        monkeypatch.chdir(cli_dir)
        monkeypatch.setattr(subprocess, "run", _make_fake_run(cli_calls))
        cli_result = CliRunner().invoke(traefik_cli.traefik_on, ["-m", "local", "-d", "demo.local"])
        assert cli_result.exit_code == 0, cli_result.output

        gui_calls = []
        monkeypatch.chdir(gui_dir)
        monkeypatch.setattr(subprocess, "run", _make_fake_run(gui_calls))
        client = _gui_client()
        response = client.post("/api/traefik/on", json={"mode": "local", "domain": "demo.local"})
        assert response.json()["ok"] is True

        cli_normalized = [(argv, _relative_cwd(cwd, cli_dir)) for argv, cwd in cli_calls]
        gui_normalized = [(argv, _relative_cwd(cwd, gui_dir)) for argv, cwd in gui_calls]
        assert cli_normalized == gui_normalized
