"""Characterization tests for project_info.py and `rkd info` (#143 / T2).

`rocketdoo/project_info.py` has 279 lines and, before this file, zero dedicated
tests. T3 moves it to `core/project_info.py` verbatim; this file is the net
that catches any observable change during that move.

Projects are built through the real generator (`scaffold_project()` +
`init_from_profile()`) instead of writing Dockerfile/docker-compose.yaml/
odoo.conf by hand. `init_from_profile()` always disables SSH and gitman
(`use_private_repos`, `use_third_party_repos` forced to False in
init_project.py), so those two dimensions are layered on top with the same
production helpers the interactive wizard uses (`inject_ssh_into_dockerfile`,
`generate_gitman_yaml`) rather than hand-rolled file contents. This keeps the
fixture honest about what a real project looks like while staying independent
of the wizard's prompt-answering machinery, and ties it only to low-level
building blocks the refactor plan does not touch.
"""

import pytest

from rocketdoo.core.gitman_config import generate_gitman_yaml
from rocketdoo.core.ssh_manager import inject_ssh_into_dockerfile
from rocketdoo.init_project import init_from_profile
from rocketdoo.project_info import (
    detect_enterprise_edition,
    detect_ssh_key_usage,
    get_project_info,
    project_exists,
)
from rocketdoo.scaffold import scaffold_project

EXPECTED_KEY_TYPES = {
    "project_name": str,
    "odoo_version": str,
    "odoo_edition": str,
    "db_version": str,
    "odoo_container": str,
    "db_container": str,
    "odoo_port": str,
    "vsc_port": str,
    "restart_policy": str,
    "use_private_repos": bool,
    "ssh_key": (str, type(None)),
    "use_third_party_repos": bool,
    "third_party_repos": list,
    "db_port": (str, type(None)),
    "admin_passwd": (str, type(None)),
}


@pytest.fixture
def community_project(project_dir):
    """A generated Odoo 18 Community project: no enterprise, no SSH, no gitman."""
    scaffold_project()
    init_from_profile("odoo18-ce", project_name="demo-project")
    return project_dir


@pytest.fixture
def enterprise_project(project_dir):
    """A generated Odoo 19 Enterprise project."""
    scaffold_project()
    init_from_profile("odoo19-ee", project_name="demo-project")
    return project_dir


class TestGetProjectInfoContract:
    """CA4: the dict's keys and types are the contract T3 must preserve."""

    def test_keys_match_exactly(self, community_project):
        info = get_project_info()
        assert set(info) == set(EXPECTED_KEY_TYPES)

    def test_every_value_has_the_declared_type(self, community_project):
        info = get_project_info()
        for key, expected_type in EXPECTED_KEY_TYPES.items():
            assert isinstance(info[key], expected_type), f"{key}: {type(info[key])!r}"

    def test_values_for_a_full_community_project(self, community_project):
        info = get_project_info()
        assert info["project_name"] == "demo-project"
        assert info["odoo_version"] == "18.0"
        assert info["odoo_edition"] == "Community"
        assert info["db_version"] == "16"
        assert info["odoo_container"] == "odoo-demo-project"
        assert info["db_container"] == "db-demo-project"
        assert info["odoo_port"] == "8069"
        assert info["vsc_port"] == "8888"
        assert info["restart_policy"] == "unless-stopped"
        assert info["admin_passwd"] == "admin"
        assert info["use_private_repos"] is False
        assert info["ssh_key"] is None
        assert info["use_third_party_repos"] is False
        assert info["third_party_repos"] == []
        assert info["db_port"] is None


class TestEnterpriseDetection:
    """One test per branch of detect_enterprise_edition()."""

    def test_community_profile_is_not_enterprise(self, community_project):
        assert detect_enterprise_edition() is False
        assert get_project_info()["odoo_edition"] == "Community"

    def test_enterprise_profile_is_detected(self, enterprise_project):
        assert detect_enterprise_edition() is True
        assert get_project_info()["odoo_edition"] == "Enterprise"


class TestSshKeyDetection:
    """One test per branch of detect_ssh_key_usage()."""

    def test_no_ssh_key_by_default(self, community_project):
        assert detect_ssh_key_usage() is None
        info = get_project_info()
        assert info["use_private_repos"] is False
        assert info["ssh_key"] is None

    def test_an_injected_ssh_key_is_detected(self, community_project):
        inject_ssh_into_dockerfile(community_project / "Dockerfile", "id_rsa_deploy")
        assert detect_ssh_key_usage() == "id_rsa_deploy"
        info = get_project_info()
        assert info["use_private_repos"] is True
        assert info["ssh_key"] == "id_rsa_deploy"


class TestThirdPartyRepos:
    def test_no_gitman_file(self, community_project):
        info = get_project_info()
        assert info["use_third_party_repos"] is False
        assert info["third_party_repos"] == []

    def test_gitman_with_sources(self, community_project):
        generate_gitman_yaml(
            sources=[{"repo": "git@github.com:acme/oca.git", "name": "oca", "rev": "18.0", "type": "git"}],
        )
        info = get_project_info()
        assert info["use_third_party_repos"] is True
        assert info["third_party_repos"] == [{"name": "oca", "repo": "git@github.com:acme/oca.git", "rev": "18.0"}]

    def test_gitman_with_an_empty_sources_list(self, community_project):
        """A gitman.yaml with no sources reads as 'no third-party repos', file or not."""
        generate_gitman_yaml(sources=[])
        info = get_project_info()
        assert info["use_third_party_repos"] is False
        assert info["third_party_repos"] == []


class TestProjectExists:
    def test_full_project(self, community_project):
        assert project_exists() is True

    def test_missing_dockerfile(self, community_project):
        (community_project / "Dockerfile").unlink()
        assert project_exists() is False

    def test_missing_compose(self, community_project):
        (community_project / "docker-compose.yaml").unlink()
        assert project_exists() is False

    def test_empty_directory(self, project_dir):
        assert project_exists() is False


class TestInfoCommand:
    """`rkd info`, compared by substring per RF10.3: the épica moves prints on purpose."""

    def test_reports_project_details(self, community_project, monkeypatch):
        from click.testing import CliRunner
        from rich.console import Console

        import rocketdoo.cli as cli

        monkeypatch.setattr(cli, "console", Console(width=200))
        info = get_project_info()

        result = CliRunner().invoke(cli.main, ["info"])
        output = " ".join(result.output.split())

        assert result.exit_code == 0
        assert info["project_name"] in output
        assert info["odoo_version"] in output
        assert info["odoo_edition"] in output
        assert info["odoo_container"] in output
        assert info["db_container"] in output
        assert info["odoo_port"] in output
        assert info["db_version"] in output
        assert info["restart_policy"] in output
        assert f"http://localhost:{info['odoo_port']}" in output

    def test_reports_third_party_repos(self, community_project, monkeypatch):
        from click.testing import CliRunner
        from rich.console import Console

        import rocketdoo.cli as cli

        generate_gitman_yaml(
            sources=[{"repo": "https://github.com/OCA/server-tools", "name": "oca", "rev": "18.0", "type": "git"}],
        )
        monkeypatch.setattr(cli, "console", Console(width=200))

        result = CliRunner().invoke(cli.main, ["info"])
        output = " ".join(result.output.split())

        assert result.exit_code == 0
        assert "Third-Party Repositories" in output
        assert "oca" in output

    def test_reports_ssh_key_in_use(self, community_project, monkeypatch):
        from click.testing import CliRunner
        from rich.console import Console

        import rocketdoo.cli as cli

        inject_ssh_into_dockerfile(community_project / "Dockerfile", "id_rsa_deploy")
        monkeypatch.setattr(cli, "console", Console(width=200))

        result = CliRunner().invoke(cli.main, ["info"])
        output = " ".join(result.output.split())

        assert result.exit_code == 0
        assert "id_rsa_deploy" in output

    def test_no_project_detected(self, project_dir, monkeypatch):
        from click.testing import CliRunner
        from rich.console import Console

        import rocketdoo.cli as cli

        monkeypatch.setattr(cli, "console", Console(width=200))

        result = CliRunner().invoke(cli.main, ["info"])
        output = " ".join(result.output.split())

        assert result.exit_code == 0
        assert "No Rocketdoo project detected" in output
        assert "rocketdoo init" in output
