"""Unit tests for core/ssh_manager.py.

`check_sshpass` and the `${VAR}` resolution were duplicated between the module
deployer (core/deploy/vps.py) and the instance deployer
(core/instance/ssh_utils.py) before #137, with slightly different behaviour on
an unset variable. These pin down the consolidated version.
"""

from pathlib import Path

import pytest

from rocketdoo.core import ssh_manager


class TestListPrivateKeys:
    def test_lists_private_keys_only(self, tmp_path):
        (tmp_path / "id_ed25519").write_text("private")
        (tmp_path / "id_ed25519.pub").write_text("public")
        (tmp_path / "id_rsa").write_text("private")
        assert sorted(ssh_manager.list_private_keys(tmp_path)) == ["id_ed25519", "id_rsa"]

    def test_ignores_subdirectories(self, tmp_path):
        (tmp_path / "id_rsa").write_text("private")
        (tmp_path / "control").mkdir()
        assert ssh_manager.list_private_keys(tmp_path) == ["id_rsa"]

    def test_missing_directory_returns_empty(self, tmp_path):
        assert ssh_manager.list_private_keys(tmp_path / "nope") == []

    def test_empty_directory_returns_empty(self, tmp_path):
        assert ssh_manager.list_private_keys(tmp_path) == []


class TestEnvRefName:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("${VPS_PASSWORD}", "VPS_PASSWORD"),
            ("${INSTANCE_STAGE_PASSWORD}", "INSTANCE_STAGE_PASSWORD"),
            ("plain-password", None),
            ("", None),
            ("${unclosed", None),
            ("unopened}", None),
            (None, None),
        ],
    )
    def test_detects_a_reference(self, value, expected):
        assert ssh_manager.env_ref_name(value) == expected


class TestResolveEnvRef:
    def test_resolves_a_set_variable(self, monkeypatch):
        monkeypatch.setenv("RKD_TEST_SECRET", "s3cr3t")
        assert ssh_manager.resolve_env_ref("${RKD_TEST_SECRET}") == "s3cr3t"

    def test_unset_variable_resolves_to_empty(self, monkeypatch):
        """Empty, not None: callers fall back to prompting on a falsy value."""
        monkeypatch.delenv("RKD_TEST_MISSING", raising=False)
        assert ssh_manager.resolve_env_ref("${RKD_TEST_MISSING}") == ""

    def test_a_literal_password_is_untouched(self):
        assert ssh_manager.resolve_env_ref("plain-password") == "plain-password"

    def test_does_not_resolve_a_partial_reference(self):
        assert ssh_manager.resolve_env_ref("prefix-${VAR}") == "prefix-${VAR}"


class TestCheckSshpass:
    def test_true_when_present(self, monkeypatch):
        monkeypatch.setattr(ssh_manager.shutil, "which", lambda name: "/usr/bin/sshpass")
        assert ssh_manager.check_sshpass() is True

    def test_false_when_absent(self, monkeypatch):
        monkeypatch.setattr(ssh_manager.shutil, "which", lambda name: None)
        assert ssh_manager.check_sshpass() is False


class TestBuildContext:
    def test_copies_the_key_into_dot_ssh(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".ssh").mkdir(parents=True)
        (home / ".ssh" / "id_ed25519").write_text("KEY")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        project = tmp_path / "project"
        project.mkdir()
        dst = ssh_manager.copy_key_to_build_context("id_ed25519", project)

        assert dst == project / ".ssh" / "id_ed25519"
        assert dst.read_text() == "KEY"

    def test_uncomments_the_ssh_lines_of_the_dockerfile(self, tmp_path):
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(
            "FROM odoo:17.0\n#RUN mkdir -p /root/.ssh\n#COPY ./.ssh/rsa /root/.ssh/id_rsa\n#RUN chmod 700 /root/.ssh/id_rsa\n"
        )
        ssh_manager.inject_ssh_into_dockerfile(dockerfile, "id_ed25519")

        content = dockerfile.read_text()
        assert "RUN mkdir -p /root/.ssh" in content
        assert "#RUN mkdir -p /root/.ssh" not in content
        assert "COPY ./.ssh/id_ed25519 /root/.ssh/id_ed25519" in content
