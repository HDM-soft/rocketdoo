"""Creating the remote directories when the SSH user cannot write the parent.

The fallback is one remote command, so it is executed here against stub
binaries: asserting on its text would not prove which branch runs when.
"""

import os
import subprocess

import pytest

pytestmark = pytest.mark.usefixtures("green_preflight")

from rocketdoo.core.instance import deployer_docker, deployer_native, ssh_utils
from tests.test_instance_docker import _config

_STUBS = {
    "mkdir": '#!/bin/sh\necho "mkdir $*" >> "$RKD_LOG"\n[ -z "$RKD_PLAIN_MKDIR_FAILS" ]\n',
    "sudo": (
        '#!/bin/sh\necho "sudo $*" >> "$RKD_LOG"\n'
        '[ "$2" = mkdir ] && [ -n "$RKD_SUDO_MKDIR_FAILS" ] && exit 1\n'
        '[ "$2" = chown ] && [ -n "$RKD_SUDO_CHOWN_FAILS" ] && exit 1\n'
        "exit 0\n"
    ),
}

_REAL_RUN = subprocess.run
DOCKER_OWNER = '"$(id -u):$(id -g)"'


def _bash(tmp_path, command: str, **state) -> tuple[subprocess.CompletedProcess, list[str]]:
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    for name, body in _STUBS.items():
        path = stubs / name
        path.write_text(body)
        path.chmod(0o755)
    log = tmp_path / "commands.log"
    env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}", "RKD_LOG": str(log)}
    env["RKD_PLAIN_MKDIR_FAILS"] = "1" if state.get("plain_fails") else ""
    env["RKD_SUDO_MKDIR_FAILS"] = "1" if state.get("sudo_mkdir_fails") else ""
    env["RKD_SUDO_CHOWN_FAILS"] = "1" if state.get("sudo_chown_fails") else ""
    result = _REAL_RUN(["bash", "-c", command], env=env, capture_output=True, text=True)
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def writable(tmp_path) -> str:
    path = tmp_path / "writable"
    path.mkdir()
    return str(path)


@pytest.fixture
def unwritable(tmp_path) -> str:
    return str(tmp_path / "missing")


@pytest.mark.parametrize("owner", ["ubuntu", DOCKER_OWNER], ids=["native", "docker"])
class TestTheFallback:
    @staticmethod
    def _command(target: str, owner: str) -> str:
        return ssh_utils.ensure_dirs_command([target, f"{target}/extra-addons"], target, owner)

    @staticmethod
    def _owner_in_log(owner: str) -> str:
        return f"{os.getuid()}:{os.getgid()}" if owner == DOCKER_OWNER else owner

    def test_a_writable_parent_never_calls_sudo(self, tmp_path, writable, owner):
        result, log = _bash(tmp_path, self._command(writable, owner))

        assert result.returncode == 0
        assert not any(line.startswith("sudo") for line in log)

    def test_a_root_owned_parent_falls_back_to_sudo_and_hands_over_the_tree(self, tmp_path, unwritable, owner):
        result, log = _bash(tmp_path, self._command(unwritable, owner), plain_fails=True)

        assert result.returncode == 0
        assert log[0].startswith("mkdir ")
        assert log[1] == f"sudo -n mkdir -p {unwritable} {unwritable}/extra-addons"
        assert log[2] == f"sudo -n chown -R {self._owner_in_log(owner)} {unwritable}"

    def test_directories_that_exist_but_are_not_writable_are_handed_over(self, tmp_path, unwritable, owner):
        """`mkdir -p` returns 0 on an existing root-owned directory: the state
        after the user ran `sudo mkdir -p` by hand."""
        result, log = _bash(tmp_path, self._command(unwritable, owner))

        assert result.returncode == 0
        assert log[-1] == f"sudo -n chown -R {self._owner_in_log(owner)} {unwritable}"

    @pytest.mark.parametrize("failing", ["sudo_mkdir_fails", "sudo_chown_fails"])
    def test_without_passwordless_sudo_it_fails_and_says_what_to_do(self, tmp_path, unwritable, owner, failing):
        result, _ = _bash(tmp_path, self._command(unwritable, owner), plain_fails=True, **{failing: True})

        assert result.returncode == 1
        assert "passwordless sudo" in result.stderr
        assert "remote_path" in result.stderr


class TestTheDeployers:
    def _deploy_with_remote_script(self, tmp_path, monkeypatch, **state):
        """Whole deploy; the directory script really runs, the rest answers 0."""
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        calls = []

        def run(args, **kwargs):
            command = args[-1]
            calls.append(command)
            if "sudo -n mkdir" in command:
                result, _ = _bash(tmp_path, command, **state)
                return subprocess.CompletedProcess(args, result.returncode)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(deployer_docker.subprocess, "run", run)
        deployer = deployer_docker.DockerInstanceDeployer("prod", _config(remote_path=str(tmp_path / "missing")), tmp_path)
        return deployer.deploy(), calls

    def test_a_failed_plain_mkdir_no_longer_stops_the_deploy(self, tmp_path, monkeypatch):
        ok, calls = self._deploy_with_remote_script(tmp_path, monkeypatch, plain_fails=True)

        assert ok
        assert any("docker compose build" in c for c in calls)

    def test_without_sudo_the_deploy_stops_at_step_2(self, tmp_path, monkeypatch):
        ok, calls = self._deploy_with_remote_script(tmp_path, monkeypatch, plain_fails=True, sudo_mkdir_fails=True)

        assert not ok
        assert not any("docker compose build" in c for c in calls)

    def test_a_failed_network_creation_is_reported(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_docker.DockerInstanceDeployer("prod", _config(), tmp_path)
        answers = iter([True, False])
        monkeypatch.setattr(deployer, "_ssh", lambda *a, **kw: next(answers))

        assert deployer._prepare_remote() is False
        assert "traefik-public" in capsys.readouterr().out

    def _capture(self, monkeypatch, module, deployer) -> list[str]:
        sent = []
        monkeypatch.setattr(deployer, "_ssh", lambda cmd, *a, **kw: sent.append(cmd) or True)
        monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0))
        return sent

    def test_docker_prepares_the_remote_with_one_script_that_chowns_remote_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_docker.DockerInstanceDeployer("prod", _config(), tmp_path)
        sent = self._capture(monkeypatch, deployer_docker, deployer)

        assert deployer._prepare_remote()
        script = sent[0]
        assert script.index("mkdir -p") < script.index("sudo -n mkdir")
        assert 'chown -R "$(id -u):$(id -g)" /opt/odoo-prod' in script

    def test_docker_reports_a_failed_prepare(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_docker.DockerInstanceDeployer("prod", _config(), tmp_path)
        monkeypatch.setattr(deployer, "_ssh", lambda *a, **kw: False)

        assert deployer._prepare_remote() is False

    def test_native_syncs_addons_without_requiring_sudo_up_front(self, tmp_path, monkeypatch):
        (tmp_path / "addons").mkdir()
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_native.NativeInstanceDeployer("prod", _config(), tmp_path)
        sent = self._capture(monkeypatch, deployer_native, deployer)

        assert deployer._sync_addons()
        script = sent[0]
        assert script.index("mkdir -p") < script.index("sudo -n mkdir")
        assert "sudo -n chown -R ubuntu /opt/odoo-prod" in script

    def test_native_fails_the_step_when_the_directory_cannot_be_created(self, tmp_path, monkeypatch):
        (tmp_path / "addons").mkdir()
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_native.NativeInstanceDeployer("prod", _config(), tmp_path)
        monkeypatch.setattr(deployer, "_ssh", lambda *a, **kw: False)

        assert deployer._sync_addons() is False
