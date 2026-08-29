"""Unit tests for core/compose.py.

These helpers were duplicated across `rkd mail`, `rkd traefik` and three GUI
endpoints before #137. Now that one implementation serves all of them, its
edge cases — no docker, no compose file, unparseable output — matter more.
"""

import subprocess

import pytest

from rocketdoo.core import compose


class TestComposePath:
    def test_finds_docker_compose_yaml(self, project_dir):
        (project_dir / "docker-compose.yaml").write_text("services: {}\n")
        assert compose.compose_path() == project_dir / "docker-compose.yaml"

    @pytest.mark.parametrize("name", compose.COMPOSE_NAMES)
    def test_recognises_every_supported_name(self, project_dir, name):
        (project_dir / name).write_text("services: {}\n")
        assert compose.compose_path() == project_dir / name

    def test_prefers_docker_compose_yaml_over_the_alternatives(self, project_dir):
        for name in compose.COMPOSE_NAMES:
            (project_dir / name).write_text("services: {}\n")
        assert compose.compose_path().name == "docker-compose.yaml"

    def test_returns_none_without_a_compose_file(self, project_dir):
        assert compose.compose_path() is None

    def test_accepts_an_explicit_base(self, tmp_path):
        (tmp_path / "compose.yml").write_text("services: {}\n")
        assert compose.compose_path(tmp_path) == tmp_path / "compose.yml"


class TestIsRunning:
    @pytest.mark.parametrize(
        "entry",
        [
            {"State": "running"},
            {"State": "Running"},
            {"Status": "Up 3 minutes"},
            {"State": "", "Status": "Up (healthy)"},
        ],
    )
    def test_running_states(self, entry):
        assert compose.is_running(entry) is True

    @pytest.mark.parametrize(
        "entry",
        [
            {"State": "exited"},
            {"Status": "Exited (0) 2 minutes ago"},
            {"State": "created"},
            {},
        ],
    )
    def test_not_running_states(self, entry):
        assert compose.is_running(entry) is False


class TestComposePs:
    @staticmethod
    def _result(stdout="", returncode=0, stderr=""):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)

    def _patch(self, monkeypatch, result):
        def _run(*a, **kw):
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(compose.subprocess, "run", _run)

    def test_parses_newline_delimited_objects(self, monkeypatch):
        self._patch(monkeypatch, self._result('{"Name":"web"}\n{"Name":"db"}\n'))
        assert [c["Name"] for c in compose.compose_ps()] == ["web", "db"]

    def test_parses_a_json_array(self, monkeypatch):
        """Some Compose versions emit an array rather than NDJSON."""
        self._patch(monkeypatch, self._result('[{"Name":"web"},{"Name":"db"}]'))
        assert [c["Name"] for c in compose.compose_ps()] == ["web", "db"]

    def test_skips_unparseable_lines(self, monkeypatch):
        self._patch(monkeypatch, self._result('{"Name":"web"}\nnot json\n{"Name":"db"}\n'))
        assert len(compose.compose_ps()) == 2

    def test_empty_output_yields_no_containers(self, monkeypatch):
        self._patch(monkeypatch, self._result(""))
        assert compose.compose_ps() == []

    def test_failure_yields_no_containers(self, monkeypatch):
        self._patch(monkeypatch, self._result("", returncode=1, stderr="no configuration file"))
        assert compose.compose_ps() == []

    def test_missing_docker_yields_no_containers(self, monkeypatch):
        self._patch(monkeypatch, FileNotFoundError("docker"))
        assert compose.compose_ps() == []

    def test_timeout_yields_no_containers(self, monkeypatch):
        self._patch(monkeypatch, subprocess.TimeoutExpired(cmd="docker", timeout=1))
        assert compose.compose_ps() == []


class TestComposePsResult:
    """The variant that reports *why* it came back empty, for the GUI."""

    def _patch(self, monkeypatch, result):
        def _run(*a, **kw):
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(compose.subprocess, "run", _run)

    def test_success_reports_no_error(self, monkeypatch):
        self._patch(
            monkeypatch,
            subprocess.CompletedProcess([], 0, '{"Name":"web"}\n', ""),
        )
        containers, error = compose.compose_ps_result()
        assert len(containers) == 1
        assert error == ""

    def test_reports_the_compose_error(self, monkeypatch):
        self._patch(
            monkeypatch,
            subprocess.CompletedProcess([], 1, "", "no configuration file provided"),
        )
        containers, error = compose.compose_ps_result()
        assert containers == []
        assert "no configuration file" in error

    def test_reports_a_missing_docker_binary(self, monkeypatch):
        self._patch(monkeypatch, FileNotFoundError("docker"))
        assert compose.compose_ps_result() == ([], "docker not found")


class TestRunCompose:
    def test_returns_the_exit_code(self, monkeypatch):
        monkeypatch.setattr(compose.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 7))
        assert compose.run_compose("up", "-d") == 7

    def test_passes_the_arguments_through(self, monkeypatch):
        seen = {}

        def _run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess([], 0)

        monkeypatch.setattr(compose.subprocess, "run", _run)
        compose.run_compose("restart", "web")
        assert seen["cmd"] == ["docker", "compose", "restart", "web"]


class TestContainerRunning:
    def test_true_when_the_service_is_up(self, monkeypatch):
        monkeypatch.setattr(compose, "compose_ps", lambda *a, **kw: [{"State": "running"}])
        assert compose.container_running("mailpit") is True

    def test_false_when_the_service_is_stopped(self, monkeypatch):
        monkeypatch.setattr(compose, "compose_ps", lambda *a, **kw: [{"State": "exited"}])
        assert compose.container_running("mailpit") is False

    def test_false_when_the_service_is_absent(self, monkeypatch):
        monkeypatch.setattr(compose, "compose_ps", lambda *a, **kw: [])
        assert compose.container_running("mailpit") is False
