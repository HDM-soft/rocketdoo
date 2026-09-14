"""Sentinel tests for the sshpass argv/env migration (#142).

Password authentication used to travel as a literal `sshpass -p <password>`
argv element, visible to any local user via `ps aux`. These tests confirm the
password now moves through `sshpass -e` / the `SSHPASS` environment variable
instead, both at the command-builder level and at each real call site, with
no VPS or Docker daemon required.
"""

import subprocess

from rocketdoo.core.deploy import vps
from rocketdoo.core.instance import deployer_docker, deployer_native, ssh_utils

SENTINEL = "RKD-SENTINEL-PASSWORD"


class TestBuildCmdAuth:
    def test_ssh_cmd_password_method_keeps_password_out_of_cmd(self):
        auth = {"method": "password", "key_path": None, "password": SENTINEL}
        cmd, env = ssh_utils.build_ssh_cmd(auth, 22, "user", "host", "echo hi")
        assert cmd[0] == "sshpass"
        assert all(SENTINEL not in item for item in cmd)
        assert env["SSHPASS"] == SENTINEL

    def test_ssh_cmd_ssh_key_method_is_unchanged(self):
        auth = {"method": "ssh_key", "key_path": "/home/user/.ssh/id_rsa", "password": None}
        cmd, env = ssh_utils.build_ssh_cmd(auth, 22, "user", "host", "echo hi")
        assert env is None
        assert "sshpass" not in cmd
        assert cmd[0] == "ssh"

    def test_rsync_cmd_password_method_keeps_password_out_of_cmd(self):
        auth = {"method": "password", "key_path": None, "password": SENTINEL}
        cmd, env = ssh_utils.build_rsync_cmd(auth, 22, "user", "host", "/local", "/remote")
        assert cmd[0] == "sshpass"
        assert all(SENTINEL not in item for item in cmd)
        assert env["SSHPASS"] == SENTINEL

    def test_rsync_cmd_ssh_key_method_is_unchanged(self):
        auth = {"method": "ssh_key", "key_path": None, "password": None}
        cmd, env = ssh_utils.build_rsync_cmd(auth, 22, "user", "host", "/local", "/remote")
        assert env is None
        assert "sshpass" not in cmd


class _RecordingRun:
    """subprocess.run stand-in that records every call and never touches the network."""

    def __init__(self):
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")


def _password_vps_config(**overrides):
    cfg = {
        "vps": {
            "host": "vps.example.com",
            "user": "deploy",
            "port": 22,
            "auth_method": "password",
            "password": SENTINEL,
        },
        "remote_path": "/opt/odoo-stage",
    }
    cfg.update(overrides)
    return cfg


class TestDockerInstanceCallSites:
    """The 3 build_ssh_cmd/build_rsync_cmd consumers in deployer_docker.py.

    check_sshpass() is stubbed so these do not depend on sshpass being
    installed on the machine running the tests (it is not on CI runners).
    """

    def _deployer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        return deployer_docker.DockerInstanceDeployer("stage", _password_vps_config(), tmp_path)

    def test_read_remote_pg_pass_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_docker.subprocess, "run", run)

        deployer._read_remote_pg_pass()

        assert len(run.calls) == 1
        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_ssh_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_docker.subprocess, "run", run)

        deployer._ssh("echo hi")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_rsync_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_docker.subprocess, "run", run)

        deployer._rsync("/local/", "/remote/")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL


class TestNativeInstanceCallSites:
    """The 2 build_ssh_cmd/build_rsync_cmd consumers in deployer_native.py."""

    def _deployer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        return deployer_native.NativeInstanceDeployer("stage", _password_vps_config(), tmp_path)

    def test_sync_addons_passes_sshpass_env(self, tmp_path, monkeypatch):
        (tmp_path / "addons").mkdir()
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_native.subprocess, "run", run)

        deployer._sync_addons()

        # _sync_addons issues a `mkdir` over ssh before the rsync call itself;
        # both must keep the password out of argv and in the env.
        assert len(run.calls) == 2
        for args, kwargs in run.calls:
            assert all(SENTINEL not in item for item in args)
            assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_ssh_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_native.subprocess, "run", run)

        deployer._ssh("echo hi")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL


def _password_vps_deployer_config(**overrides):
    cfg = {
        "connection": {
            "host": "vps.example.com",
            "user": "deploy",
            "port": 22,
            "password": SENTINEL,
        },
        "deployment_type": "docker",
        "docker": {
            "container_name": "odoo",
            "compose_path": "/opt/odoo",
            "addons_mount": "/mnt/extra-addons",
        },
    }
    cfg.update(overrides)
    return cfg


class TestVpsDeployerCallSites:
    """The 3 sshpass call sites in core/deploy/vps.py: _run_ssh_command,
    _upload_directory, _upload_file_scp.
    """

    def _deployer(self, tmp_path):
        return vps.VPSDeployer("production", _password_vps_deployer_config(), tmp_path)

    def test_run_ssh_command_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._run_ssh_command("echo hi")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_upload_directory_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._upload_directory(tmp_path / "module", "/mnt/extra-addons/module")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_upload_file_scp_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._upload_file_scp(tmp_path / "module.zip", "/mnt/extra-addons/module.zip")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_ssh_key_method_still_has_no_sshpass_env(self, tmp_path, monkeypatch):
        cfg = _password_vps_deployer_config(connection={"host": "vps.example.com", "user": "deploy", "port": 22})
        cfg["connection"]["ssh_key"] = "~/.ssh/id_rsa"
        deployer = vps.VPSDeployer("production", cfg, tmp_path)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._run_ssh_command("echo hi")

        args, kwargs = run.calls[0]
        assert "sshpass" not in args
        assert kwargs["env"] is None
