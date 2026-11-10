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

import re

import pytest

from rocketdoo.core.gitman_config import generate_gitman_yaml
from rocketdoo.core.project_info import (
    detect_enterprise_edition,
    detect_ssh_key_usage,
    get_project_info,
    project_exists,
)
from rocketdoo.core.ssh_manager import inject_ssh_into_dockerfile
from rocketdoo.init_project import init_from_profile
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
    """CA4: the keys and types T3 had to preserve -- and #204 after it.

    Measured on `model_dump()` because that is what `gui/api/project.py`
    serialises for the SPA, which reads `odoo_port`, `ssh_key`, `admin_passwd`
    and the rest by name. Renaming a field of `ProjectConfig` breaks a panel
    that no test of the model would notice.
    """

    def test_keys_match_exactly(self, community_project):
        assert set(get_project_info().model_dump()) == set(EXPECTED_KEY_TYPES)

    def test_every_value_has_the_declared_type(self, community_project):
        info = get_project_info().model_dump()
        for key, expected_type in EXPECTED_KEY_TYPES.items():
            assert isinstance(info[key], expected_type), f"{key}: {type(info[key])!r}"

    def test_values_for_a_full_community_project(self, community_project):
        info = get_project_info()
        assert info.project_name == "demo-project"
        assert info.odoo_version == "18.0"
        assert info.odoo_edition == "Community"
        assert info.db_version == "16"
        assert info.odoo_container == "odoo-demo-project"
        assert info.db_container == "db-demo-project"
        assert info.odoo_port == "8069"
        assert info.vsc_port == "8888"
        assert info.restart_policy == "unless-stopped"
        assert info.admin_passwd == "admin"
        assert info.use_private_repos is False
        assert info.ssh_key is None
        assert info.use_third_party_repos is False
        assert info.third_party_repos == []
        assert info.db_port is None


class TestEnterpriseDetection:
    """One test per branch of detect_enterprise_edition()."""

    def test_community_profile_is_not_enterprise(self, community_project):
        assert detect_enterprise_edition() is False
        assert get_project_info().odoo_edition == "Community"

    def test_enterprise_profile_is_detected(self, enterprise_project):
        assert detect_enterprise_edition() is True
        assert get_project_info().odoo_edition == "Enterprise"


class TestSshKeyDetection:
    """One test per branch of detect_ssh_key_usage()."""

    def test_no_ssh_key_by_default(self, community_project):
        assert detect_ssh_key_usage() is None
        info = get_project_info()
        assert info.use_private_repos is False
        assert info.ssh_key is None

    def test_an_injected_ssh_key_is_detected(self, community_project):
        inject_ssh_into_dockerfile(community_project / "Dockerfile", "id_rsa_deploy")
        assert detect_ssh_key_usage() == "id_rsa_deploy"
        info = get_project_info()
        assert info.use_private_repos is True
        assert info.ssh_key == "id_rsa_deploy"


class TestThirdPartyRepos:
    def test_no_gitman_file(self, community_project):
        info = get_project_info()
        assert info.use_third_party_repos is False
        assert info.third_party_repos == []

    def test_gitman_with_sources(self, community_project):
        generate_gitman_yaml(
            sources=[{"repo": "git@github.com:acme/oca.git", "name": "oca", "rev": "18.0", "type": "git"}],
        )
        info = get_project_info()
        assert info.use_third_party_repos is True
        assert info.third_party_repos == [{"name": "oca", "repo": "git@github.com:acme/oca.git", "rev": "18.0"}]

    def test_gitman_with_an_empty_sources_list(self, community_project):
        """A gitman.yaml with no sources reads as 'no third-party repos', file or not."""
        generate_gitman_yaml(sources=[])
        info = get_project_info()
        assert info.use_third_party_repos is False
        assert info.third_party_repos == []


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
        assert info.project_name in output
        assert info.odoo_version in output
        assert info.odoo_edition in output
        assert info.odoo_container in output
        assert info.db_container in output
        assert info.odoo_port in output
        assert info.db_version in output
        assert info.restart_policy in output
        assert f"http://localhost:{info.odoo_port}" in output

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


class TestWhatTheGuiSendsToTheSpa:
    """The endpoint is where a renamed field stops being an internal detail.

    `gui/api/project.py` serialises this straight into the response and
    `index.html` reads the keys by name -- `ssh_key`, `odoo_port`,
    `admin_passwd`, `project_name`. Nothing in the model or in the CLI would
    notice the panel going blank, so the contract is pinned here, over the
    endpoint itself (#204).
    """

    def _body(self):
        import asyncio

        from rocketdoo.gui.api.project import get_project

        return asyncio.run(get_project())

    def test_the_response_carries_the_whole_contract(self, community_project):
        assert set(self._body()) == set(EXPECTED_KEY_TYPES) | {"exists"}

    def test_the_values_survive_serialisation(self, community_project):
        body = self._body()

        assert body["exists"] is True
        assert body["project_name"] == "demo-project"
        assert body["odoo_port"] == "8069"
        assert body["ssh_key"] is None

    def test_a_directory_with_no_project_says_so(self, project_dir):
        assert self._body() == {"exists": False}


class TestAGitmanFileWrittenByHand:
    """`gitman.yaml` is the user's file and `yaml.safe_load` gives back what
    it says: a float for `rev: 16.0`, an int for `rev: 17`, None for an empty
    `repo:`. An Odoo branch is exactly what gets written as `16.0`, so the
    natural spelling ended `rkd info` in a traceback -- Rich refuses to
    render a float, and `len(None)` has no answer (#262).

    The reader keeps the values as they are; `rkd info` renders them.
    """

    def _info_output(self, monkeypatch):
        from click.testing import CliRunner
        from rich.console import Console

        import rocketdoo.cli as cli

        monkeypatch.setattr(cli, "console", Console(width=200))
        result = CliRunner().invoke(cli.main, ["info"])
        return result

    def test_an_unquoted_rev_does_not_end_the_command(self, community_project, monkeypatch):
        (community_project / "gitman.yaml").write_text(
            "sources:\n  - name: oca\n    repo: https://github.com/OCA/web\n    rev: 16.0\n"
        )

        result = self._info_output(monkeypatch)

        assert result.exit_code == 0, result.output
        output = " ".join(result.output.split())
        assert "oca" in output
        assert "16.0" in output

    def test_a_source_without_a_repo_does_not_end_it_either(self, community_project, monkeypatch):
        (community_project / "gitman.yaml").write_text("sources:\n  - name: roto\n    repo:\n    rev: 17\n")

        result = self._info_output(monkeypatch)

        assert result.exit_code == 0, result.output
        assert "roto" in " ".join(result.output.split())

    def test_the_reader_does_not_coerce_them(self, community_project):
        """Rendering is the CLI's job: what the file says travels to the GUI,
        which serialises it to JSON without touching it."""
        (community_project / "gitman.yaml").write_text("sources:\n  - name: oca\n    repo: x\n    rev: 16.0\n")

        assert get_project_info().third_party_repos[0]["rev"] == 16.0


class TestAProjectRkdItselfWrote:
    """The reader has to survive what the generator produces.

    `rkd init` offers three restart policies and one of them is `no`, which
    the generated compose wrote unquoted until #204. `yaml.safe_load` is YAML
    1.1, so that line reads back as the boolean False -- and a reader that
    validates types turned rkd's own output into a project rkd refuses to
    read: `rkd info`, `rkd pack` and the GUI panel at once, the last one
    showing "no project found" with the reason in a key the SPA never prints.

    Every fixture here is built with `init_from_profile()`, which always
    writes `unless-stopped`; that is why nothing caught it.
    """

    @pytest.mark.parametrize("policy", ["no", "always", "unless-stopped"])
    def test_every_restart_policy_the_wizard_offers_reads_back(self, community_project, policy):
        compose = community_project / "docker-compose.yaml"
        # Unquoted on purpose: it is what every project generated before the
        # template was fixed has on disk, and those keep working.
        compose.write_text(re.sub(r"^(\s+)restart: .*$", rf"\g<1>restart: {policy}", compose.read_text(), flags=re.M))

        assert get_project_info().restart_policy == policy

    def test_rkd_info_prints_it(self, community_project, monkeypatch):
        """3.9.2 read `restart: no` as False and dropped the row in silence,
        so the panel said nothing about a policy the user had chosen."""
        from click.testing import CliRunner
        from rich.console import Console

        import rocketdoo.cli as cli

        compose = community_project / "docker-compose.yaml"
        compose.write_text(re.sub(r"^(\s+)restart: .*$", r"\g<1>restart: no", compose.read_text(), flags=re.M))
        monkeypatch.setattr(cli, "console", Console(width=200))

        result = CliRunner().invoke(cli.main, ["info"])

        assert result.exit_code == 0, result.output
        assert "Restart Policy" in " ".join(result.output.split())

    @pytest.mark.parametrize(
        ("line", "field", "expected"),
        [
            ("name: 2024", "project_name", "2024"),
            ("    container_name: 12345", "odoo_container", "12345"),
        ],
    )
    def test_a_number_where_a_name_goes_is_read_as_text(self, community_project, line, field, expected):
        """Same class, by hand instead of by wizard: YAML gives an int and
        the panel wants something to print."""
        compose = community_project / "docker-compose.yaml"
        key = line.strip().split(":")[0]
        compose.write_text(re.sub(rf"^(\s*){key}: .*$", line, compose.read_text(), count=1, flags=re.M))

        assert getattr(get_project_info(), field) == expected
