"""Characterization tests for docker_cli.py (#143 / T6, updated by T7).

Pins the exact argv each of the 8 commands hands to subprocess.run today,
plus the three things a refactor can change without touching argv: the
child's working directory, whether its output still reaches the terminal,
and check=True. T7 routes the six `docker compose` commands (up, restart,
down, status, stop, pause) plus `build --rebuild` through
`core.compose.run_compose`, which still calls `subprocess.run` under the
hood, so these asserts hold unchanged before and after.

None of the 8 redirect output today, so all 8 inherit the parent's stdio
identically, and that stays true after T7: `run_compose` always inherits
stdio, so the whole CLI keeps behaving this way. `run_compose_result`, the
capturing sibling, is only used by the GUI (RF5.1), never by `docker_cli.py`.
"""

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rocketdoo import docker_cli
from rocketdoo.core import compose
from rocketdoo.core.addons_path import CONTAINER_ADDONS_ROOT


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_run(cmd, *args, **kwargs):
        cwd = kwargs.get("cwd")
        recorded.append(
            {
                "argv": list(cmd),
                # cwd=None and an explicit cwd equal to the process cwd land the
                # child in the same directory; core/compose.run_compose() passes
                # the latter. Only a *different* directory is a change.
                "cwd": None if cwd is None or Path(cwd) == Path.cwd() else Path(cwd),
                # Any of the three takes the child's output away from the
                # terminal, not just capture_output.
                "redirected": bool(
                    kwargs.get("capture_output") or kwargs.get("stdout") is not None or kwargs.get("stderr") is not None
                ),
                # check=False and an absent check both mean "do not raise";
                # only opting in is a change.
                "check": bool(kwargs.get("check")),
            }
        )
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(docker_cli, "ensure_docker_installed", lambda: None)
    monkeypatch.setattr(docker_cli.subprocess, "run", fake_run)
    return recorded


class TestUp:
    def test_plain(self, calls, project_dir):
        result = CliRunner().invoke(docker_cli.up, [])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "compose", "up"], "cwd": None, "redirected": False, "check": False}]

    def test_detached(self, calls, project_dir):
        result = CliRunner().invoke(docker_cli.up, ["-d"])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "compose", "up", "-d"], "cwd": None, "redirected": False, "check": False}]


class TestRestart:
    def test_plain(self, calls, project_dir):
        result = CliRunner().invoke(docker_cli.restart, [])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "compose", "restart"], "cwd": None, "redirected": False, "check": False}]

    def test_with_timeout(self, calls, project_dir):
        result = CliRunner().invoke(docker_cli.restart, ["-t", "30"])
        assert result.exit_code == 0
        assert calls == [
            {
                "argv": ["docker", "compose", "restart", "-t", "30"],
                "cwd": None,
                "redirected": False,
                "check": False,
            }
        ]

    def test_with_service(self, calls, project_dir):
        result = CliRunner().invoke(docker_cli.restart, ["web"])
        assert result.exit_code == 0
        assert calls == [
            {
                "argv": ["docker", "compose", "restart", "web"],
                "cwd": None,
                "redirected": False,
                "check": False,
            }
        ]


class TestDown:
    def test_plain(self, calls):
        result = CliRunner().invoke(docker_cli.down, [])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "compose", "down"], "cwd": None, "redirected": False, "check": False}]

    def test_with_volumes(self, calls):
        result = CliRunner().invoke(docker_cli.down, ["-v"])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "compose", "down", "-v"], "cwd": None, "redirected": False, "check": False}]


class TestStatus:
    def test_plain(self, calls):
        result = CliRunner().invoke(docker_cli.status, [])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "compose", "ps"], "cwd": None, "redirected": False, "check": False}]


class TestStop:
    def test_plain(self, calls):
        result = CliRunner().invoke(docker_cli.stop, [])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "compose", "stop"], "cwd": None, "redirected": False, "check": False}]


class TestPause:
    def test_plain(self, calls):
        result = CliRunner().invoke(docker_cli.pause, [])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "compose", "pause"], "cwd": None, "redirected": False, "check": False}]


class TestLogs:
    def test_plain_without_container_only_warns(self, calls):
        """No container, no -f: `docker` is never invoked."""
        result = CliRunner().invoke(docker_cli.logs, [])
        assert result.exit_code == 0
        assert calls == []
        assert "must specify the container" in result.output

    def test_follow_without_container_still_only_warns(self, calls):
        """`-f` alone does not invoke docker either: a container is still
        required to reach subprocess.run. Worth pinning explicitly since it
        is easy to assume `-f` is enough on its own.
        """
        result = CliRunner().invoke(docker_cli.logs, ["-f"])
        assert result.exit_code == 0
        assert calls == []
        assert "must specify the container" in result.output

    def test_with_container(self, calls):
        result = CliRunner().invoke(docker_cli.logs, ["web"])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "logs", "web"], "cwd": None, "redirected": False, "check": False}]

    def test_follow_with_container(self, calls):
        result = CliRunner().invoke(docker_cli.logs, ["-f", "web"])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "logs", "-f", "web"], "cwd": None, "redirected": False, "check": False}]


class TestBuild:
    def test_plain(self, calls):
        result = CliRunner().invoke(docker_cli.build, [])
        assert result.exit_code == 0
        assert calls == [{"argv": ["docker", "build", "."], "cwd": None, "redirected": False, "check": True}]

    def test_with_tag(self, calls):
        result = CliRunner().invoke(docker_cli.build, ["-t", "my-image:latest"])
        assert result.exit_code == 0
        assert calls == [
            {
                "argv": ["docker", "build", "-t", "my-image:latest", "."],
                "cwd": None,
                "redirected": False,
                "check": True,
            }
        ]

    def test_rebuild(self, calls, project_dir):
        result = CliRunner().invoke(docker_cli.build, ["--rebuild"])
        assert result.exit_code == 0
        assert calls == [
            {
                "argv": ["docker", "compose", "up", "-d", "--build"],
                "cwd": None,
                "redirected": False,
                "check": True,
            }
        ]


class TestStdioInheritance:
    """CA10: `up` without `-d` and `logs -f` must keep inheriting stdio.

    The whole CLI does, before and after T7: `run_compose` never captures.
    Only the GUI's `run_compose_result` does that (RF5.1), so these two in
    particular -- the commands the user watches run live -- are the ones
    that can never be pointed at it.
    """

    def test_up_without_detached_keeps_stdio(self, calls, project_dir):
        CliRunner().invoke(docker_cli.up, [])
        assert calls[-1]["redirected"] is False

    def test_logs_follow_keeps_stdio(self, calls):
        CliRunner().invoke(docker_cli.logs, ["-f", "web"])
        assert calls[-1]["redirected"] is False


class TestEnsureDockerInstalledMissing:
    """Shared by all 8 commands: verified once through `up`."""

    def test_exit_code_is_one(self, monkeypatch, project_dir):
        monkeypatch.setattr(compose.shutil, "which", lambda name: None)
        monkeypatch.setattr(docker_cli.subprocess, "run", lambda *a, **k: pytest.fail("docker must not run"))

        result = CliRunner().invoke(docker_cli.up, [])

        assert result.exit_code == 1


class TestSyncAddonsPathRunsBeforeCompose:
    """up, restart and build --rebuild must have already rewritten
    config/odoo.conf by the time docker compose runs, or Odoo starts blind
    to whatever changed under addons/. Mirrors
    tests/test_addons_path.py::TestUpSyncsAddonsPathFirst for the two other
    commands that also call sync_addons_path().
    """

    def _project_with_new_module(self, project_dir):
        conf_dir = project_dir / "config"
        conf_dir.mkdir()
        conf = conf_dir / "odoo.conf"
        conf.write_text(f"[options]\naddons_path = {CONTAINER_ADDONS_ROOT}\n")
        module = project_dir / "addons" / "oca" / "mod"
        module.mkdir(parents=True)
        (module / "__manifest__.py").write_text("{'name': 'x', 'installable': True}\n")
        return conf

    def _capture_addons_path_at_call_time(self, monkeypatch, conf):
        seen = {}

        def fake_run(cmd, *args, **kwargs):
            seen["addons_path"] = conf.read_text()
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(docker_cli, "ensure_docker_installed", lambda: None)
        monkeypatch.setattr(docker_cli.subprocess, "run", fake_run)
        return seen

    def test_up(self, project_dir, monkeypatch):
        conf = self._project_with_new_module(project_dir)
        seen = self._capture_addons_path_at_call_time(monkeypatch, conf)

        result = CliRunner().invoke(docker_cli.up, [])

        assert result.exit_code == 0
        assert f"{CONTAINER_ADDONS_ROOT}/oca" in seen["addons_path"]

    def test_restart(self, project_dir, monkeypatch):
        conf = self._project_with_new_module(project_dir)
        seen = self._capture_addons_path_at_call_time(monkeypatch, conf)

        result = CliRunner().invoke(docker_cli.restart, [])

        assert result.exit_code == 0
        assert f"{CONTAINER_ADDONS_ROOT}/oca" in seen["addons_path"]

    def test_build_rebuild(self, project_dir, monkeypatch):
        conf = self._project_with_new_module(project_dir)
        seen = self._capture_addons_path_at_call_time(monkeypatch, conf)

        result = CliRunner().invoke(docker_cli.build, ["--rebuild"])

        assert result.exit_code == 0
        assert f"{CONTAINER_ADDONS_ROOT}/oca" in seen["addons_path"]
