"""The native gitman step: the remote script, the shared path and the canonical addons_path."""

import re
from pathlib import Path

import pytest

from rocketdoo.core.instance import deployer_docker, deployer_native, gitman_remote

DOCKERFILE = Path(deployer_docker.__file__).parents[2] / "templates" / "instance" / "Dockerfile.jinja"


def script(environment="prod", hosts=(), **kw):
    return gitman_remote.install_script(environment, "sources: []\n", list(hosts), **kw)


class TestInstallScript:
    def test_gitman_is_called_by_absolute_path(self):
        assert f"\n{gitman_remote.GITMAN_VENV}/bin/gitman install " in script()

    def test_the_venv_is_never_activated(self):
        text = script(dependencies_script="#!/bin/sh\n")
        assert "activate" not in text
        assert not re.search(r"(^|\s)(source|\.)\s", text, re.M)
        assert "PATH" not in text

    def test_force_only_on_stage(self):
        assert "install --force" in script("stage")
        assert "--force" not in script("prod")

    def test_it_is_one_braced_block_and_keeps_gitman_off_stdin(self):
        text = script()
        assert text.startswith("{\n") and text.endswith("}\n")
        assert "</dev/null" in text

    def test_hosts_are_scanned_and_github_is_always_there(self):
        assert "ssh-keyscan github.com gitlab.example.com >>" in script(hosts=["gitlab.example.com", "github.com"])

    def test_the_dependency_script_goes_after_a_mkdir_of_its_directory(self):
        text = script(dependencies_script="echo hi\n")
        mkdir = f"mkdir -p {gitman_remote.GITMAN_DIR} {gitman_remote.DEPENDENCIES_DIR}"
        assert text.index(mkdir) < text.index(f"tee {gitman_remote.DEPENDENCIES_SCRIPT}")
        assert "echo hi" in text
        assert "install_dependencies.sh" not in script()

    def test_the_tree_ends_up_owned_by_odoo(self):
        assert script().endswith(f"sudo chown -R odoo: {gitman_remote.GITMAN_DIR}\n}}\n")

    def test_content_with_the_delimiter_is_refused(self):
        with pytest.raises(ValueError):
            script(dependencies_script="RKD_GITMAN_EOF")

    def test_the_pins_are_the_ones_of_the_docker_build(self):
        dockerfile = DOCKERFILE.read_text()
        for pin in ("gitman==1.4.0", "YORM==1.6.2", "PyYAML==5.4.1", "Cython<3"):
            assert pin in dockerfile and pin in script()


def test_docker_and_native_share_the_remote_directory():
    assert deployer_docker.GITMAN_DIR is gitman_remote.GITMAN_DIR
    assert deployer_native.GITMAN_DIR is gitman_remote.GITMAN_DIR
    dockerfile = DOCKERFILE.read_text()
    assert f"{gitman_remote.GITMAN_DIR}/gitman.yml" in dockerfile
    assert f"{gitman_remote.GITMAN_VENV}/bin/gitman" in dockerfile


@pytest.fixture
def native(tmp_path):
    def make(**cfg):
        base = {"type": "native", "vps": {"host": "h", "user": "u", "auth_method": "ssh_key"}, "remote_path": "/srv/a"}
        return deployer_native.NativeInstanceDeployer("stage", {**base, **cfg}, tmp_path)

    return make


class TestNativeDeployer:
    def test_the_addons_path_follows_the_canonical_order(self, native, tmp_path):
        for module in ("own", "nested/oca_mod"):
            (tmp_path / "addons" / module).mkdir(parents=True)
            (tmp_path / "addons" / module / "__manifest__.py").write_text("{'name': 'x'}")
        (tmp_path / "gitman.yaml").write_text("location: ext\nsources:\n  - repo: https://h/oca.git\n    name: oca\n")
        deployer = native(use_enterprise=True, use_gitman=True)
        assert deployer._addons_path().split(",") == [
            "/opt/odoo/enterprise",
            "/srv/a",
            "/srv/a/nested",
            "/opt/odoo/gitman/ext/oca",
        ]

    def test_without_gitman_or_enterprise_it_is_the_remote_path(self, native):
        assert native()._addons_path() == "/srv/a"

    def test_the_gitman_step_runs_after_the_syncs_and_before_the_database(self, native):
        labels = [label for label, _ in native(use_gitman=True, use_enterprise=True)._steps()]
        gitman = next(i for i, label in enumerate(labels) if "gitman" in label)
        assert gitman > max(i for i, label in enumerate(labels) if "Syncing" in label)
        assert gitman < next(i for i, label in enumerate(labels) if "database" in label)
        assert not any("gitman" in label for label, _ in native()._steps())

    def test_git_and_venv_are_installed_only_with_gitman(self, native, monkeypatch):
        sent = []
        for cfg, expected in (({}, False), ({"use_gitman": True}, True)):
            deployer = native(**cfg)
            monkeypatch.setattr(deployer, "_ssh", lambda command, *a, **k: sent.append(command) or True)
            deployer._install_system_deps()
            assert ("python3-venv" in sent[-1]) is expected

    def test_the_step_sends_the_script_with_the_agent_forwarded(self, native, tmp_path, monkeypatch):
        (tmp_path / "gitman.yaml").write_text("sources:\n  - repo: git@gitlab.example.com:o/r.git\n")
        (tmp_path / "install_dependencies.sh").write_text("echo deps\n")
        deployer = native(use_gitman=True)
        calls = []
        monkeypatch.setattr(deployer, "_ssh", lambda command, **kw: calls.append((command, kw)) or True)
        assert deployer._install_gitman() is True
        command, kw = calls[0]
        assert command == "bash -s" and kw["agent_forward"] is True
        assert "gitlab.example.com" in kw["input"] and "echo deps" in kw["input"]

    def test_a_missing_gitman_file_fails_the_step(self, native, monkeypatch):
        deployer = native(use_gitman=True)
        monkeypatch.setattr(deployer, "_ssh", lambda *a, **k: pytest.fail("nothing must be sent"))
        assert deployer._install_gitman() is False
