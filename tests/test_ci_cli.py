"""Unit tests for `rkd ci prepare` (RF3).

A fresh git clone of a Rocketdoo project is missing config/odoo.conf and
odoo_pg_pass, both gitignored, and the Docker build fails without them
(verified by hand against `docker compose build`, not asserted here since it
needs a daemon). `rkd ci prepare` regenerates them without ever overwriting
what already exists.
"""

import os
import subprocess

import pytest
import yaml
from click.testing import CliRunner

from rocketdoo import ci_cli
from rocketdoo.core.addons_path import CONTAINER_ADDONS_ROOT


def _minimal_project(root, *, edition="Community", db_container="db-proj"):
    (root / "Dockerfile").write_text("FROM odoo:18.0\n")

    volumes = ["      - ./addons:/usr/lib/python3/dist-packages/odoo/extra-addons"]
    if edition == "Enterprise":
        volumes.append("      - ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise")

    compose = "\n".join(
        [
            "name: proj",
            "services:",
            "  web:",
            "    build: .",
            "    container_name: odoo-proj",
            "    ports:",
            '      - "8069:8069"',
            '      - "8888:8888"',
            "    volumes:",
            *volumes,
            "  db:",
            "    image: postgres:16",
            f"    container_name: {db_container}",
            "",
        ]
    )
    (root / "docker-compose.yaml").write_text(compose)


class TestPrepareCreatesWhatIsMissing:
    def test_creates_odoo_pg_pass_and_odoo_conf(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)

        result = CliRunner().invoke(ci_cli.prepare, [])

        assert result.exit_code == 0, result.output
        assert (tmp_path / "odoo_pg_pass").read_text() == "odoo\n"

        conf = tmp_path / "config" / "odoo.conf"
        assert conf.exists()
        lines = conf.read_text().splitlines()
        assert "admin_passwd = admin" in lines
        assert f"addons_path = {CONTAINER_ADDONS_ROOT}" in lines
        assert "db_host = db-proj" in lines

    def test_admin_passwd_option_is_used_for_a_new_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)

        result = CliRunner().invoke(ci_cli.prepare, ["--admin-passwd", "super-secret"])

        assert result.exit_code == 0, result.output
        conf = tmp_path / "config" / "odoo.conf"
        assert "admin_passwd = super-secret" in conf.read_text().splitlines()


class TestPrepareNeverOverwrites:
    def test_existing_odoo_conf_keeps_its_admin_passwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        (tmp_path / "odoo_pg_pass").write_text("existing-secret\n")

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        conf = config_dir / "odoo.conf"
        conf.write_text(f"[options]\naddons_path = {CONTAINER_ADDONS_ROOT}\nadmin_passwd = do-not-touch\n")
        before = conf.read_text()

        result = CliRunner().invoke(ci_cli.prepare, [])

        assert result.exit_code == 0, result.output
        assert conf.read_text() == before
        assert (tmp_path / "odoo_pg_pass").read_text() == "existing-secret\n"


class TestPrepareAppliesEnterprise:
    def test_new_odoo_conf_gets_the_enterprise_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path, edition="Enterprise")

        result = CliRunner().invoke(ci_cli.prepare, [])

        assert result.exit_code == 0, result.output
        conf = tmp_path / "config" / "odoo.conf"
        addons_line = next(line for line in conf.read_text().splitlines() if line.startswith("addons_path"))
        assert "/usr/lib/python3/dist-packages/odoo/enterprise" in addons_line


class TestPrepareAppliesGitman:
    def test_new_odoo_conf_gets_the_gitman_paths(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        (tmp_path / "gitman.yaml").write_text(
            "location: external_addons\n"
            "sources:\n"
            "  - repo: https://github.com/oca/oca-addon.git\n"
            "    name: oca-addon\n"
            '    rev: "18.0"\n'
        )

        result = CliRunner().invoke(ci_cli.prepare, [])

        assert result.exit_code == 0, result.output
        conf = tmp_path / "config" / "odoo.conf"
        addons_line = next(line for line in conf.read_text().splitlines() if line.startswith("addons_path"))
        assert "/usr/lib/python3/dist-packages/odoo/external_addons/oca-addon" in addons_line


class TestPrepareAbortsWithoutAProject:
    def test_empty_directory_aborts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(ci_cli.prepare, [])

        assert result.exit_code == 1
        assert not (tmp_path / "config").exists()
        assert not (tmp_path / "odoo_pg_pass").exists()


class TestCiHelp:
    def test_group_help_lists_prepare(self):
        result = CliRunner().invoke(ci_cli.ci, ["--help"])
        assert result.exit_code == 0
        assert "prepare" in result.output

    def test_prepare_help(self):
        result = CliRunner().invoke(ci_cli.prepare, ["--help"])
        assert result.exit_code == 0
        assert "--admin-passwd" in result.output


class TestModules:
    """`rkd ci modules` feeds `odoo -i`, so its stdout must be clean and exact."""

    def _module(self, addons, relative, *, installable=True):
        mod = addons / relative
        mod.mkdir(parents=True)
        (mod / "__init__.py").write_text("")
        (mod / "__manifest__.py").write_text(
            f'{{"name": "{mod.name}", "version": "18.0.1.0.0", "depends": ["base"], "installable": {installable}}}\n'
        )

    def _run(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        return CliRunner().invoke(ci_cli.modules, [])

    def test_flat_and_nested_modules_are_listed_sorted(self, tmp_path, monkeypatch):
        addons = tmp_path / "addons"
        self._module(addons, "zeta")
        self._module(addons, "oca/alpha")

        result = self._run(tmp_path, monkeypatch)

        assert result.exit_code == 0
        assert result.stdout.strip() == "alpha,zeta"

    def test_non_installable_modules_are_skipped(self, tmp_path, monkeypatch):
        addons = tmp_path / "addons"
        self._module(addons, "good")
        self._module(addons, "legacy", installable=False)

        assert self._run(tmp_path, monkeypatch).stdout.strip() == "good"

    def test_modules_odoo_cannot_reach_are_skipped(self, tmp_path, monkeypatch):
        """setup/ is excluded from discover(), so Odoo never sees what lives there."""
        addons = tmp_path / "addons"
        self._module(addons, "real")
        self._module(addons, "setup/packaged")

        assert self._run(tmp_path, monkeypatch).stdout.strip() == "real"

    def test_names_odoo_cannot_import_are_skipped(self, tmp_path, monkeypatch):
        """The output becomes `odoo -i $MODULES`, and Odoo imports modules as
        Python packages, so a directory that is not an identifier can only be
        noise on that command line. Same guard as build_update_command.
        """
        addons = tmp_path / "addons"
        self._module(addons, "good")
        for odd in ("--load-language=es", "with-dash", "2leading_digit"):
            self._module(addons, odd)

        assert self._run(tmp_path, monkeypatch).stdout.strip() == "good"

    def test_no_modules_prints_nothing_and_exits_zero(self, tmp_path, monkeypatch):
        (tmp_path / "addons").mkdir()

        result = self._run(tmp_path, monkeypatch)

        assert result.exit_code == 0
        assert result.stdout.strip() == ""

    def test_missing_addons_directory_is_not_an_error(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)

        assert result.exit_code == 0
        assert result.stdout.strip() == ""


def _workflow_path(root):
    return root / ".github" / "workflows" / "rkd-ci.yml"


class TestInitCreatesTheWorkflow:
    def test_creates_the_workflow_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        assert "Created" in result.output
        parsed = yaml.safe_load(_workflow_path(tmp_path).read_text())
        assert set(parsed["jobs"]) == {"lint", "install"}

    def test_empty_directory_aborts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(ci_cli.init, [])

        assert result.exit_code == 1
        assert not (tmp_path / ".github").exists()

    def test_group_help_lists_init(self):
        result = CliRunner().invoke(ci_cli.ci, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output

    def test_init_help(self):
        result = CliRunner().invoke(ci_cli.init, ["--help"])
        assert result.exit_code == 0
        assert "--install-trigger" in result.output
        assert "--force" in result.output


class TestInitStateTransitions:
    """The four states RF5 defines: created, ok, modified, overwritten."""

    def test_second_run_reports_ok_and_does_not_rewrite(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        workflow = _workflow_path(tmp_path)

        first = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])
        assert first.exit_code == 0, first.output
        mtime = workflow.stat().st_mtime_ns
        content = workflow.read_text()

        second = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert second.exit_code == 0, second.output
        assert "already up to date" in second.output
        assert workflow.stat().st_mtime_ns == mtime
        assert workflow.read_text() == content

    def test_hand_edited_file_is_reported_as_modified_and_not_touched(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        workflow = _workflow_path(tmp_path)
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: hand-edited\njobs: {}\n")

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        assert "--force" in result.output
        assert workflow.read_text() == "name: hand-edited\njobs: {}\n"

    def test_foreign_file_without_the_marker_is_also_reported_as_modified(self, tmp_path, monkeypatch):
        """A file `rkd ci init` never wrote gets the same treatment as an edited one."""
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        workflow = _workflow_path(tmp_path)
        workflow.parent.mkdir(parents=True)
        foreign = "name: someone else's workflow\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps: []\n"
        workflow.write_text(foreign)

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        assert "--force" in result.output
        assert workflow.read_text() == foreign

    def test_force_overwrites_a_modified_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        workflow = _workflow_path(tmp_path)
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: hand-edited\njobs: {}\n")

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request", "--force"])

        assert result.exit_code == 0, result.output
        assert "Overwritten" in result.output
        assert "lint" in yaml.safe_load(workflow.read_text())["jobs"]


class TestInitUnsupportedInstall:
    def test_enterprise_has_no_install_job_and_states_the_reason(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path, edition="Enterprise")

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        content = _workflow_path(tmp_path).read_text()
        assert set(yaml.safe_load(content)["jobs"]) == {"lint"}
        assert "Enterprise addons are not public" in content
        assert "Enterprise addons are not public" in result.output

    def test_private_repos_has_no_install_job_and_states_the_reason(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "COPY .ssh/id_rsa /root/.ssh/id_rsa\n")

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        content = _workflow_path(tmp_path).read_text()
        assert set(yaml.safe_load(content)["jobs"]) == {"lint"}
        assert "private SSH key" in content


class TestInitDefaultBranchFallback:
    """PyYAML's safe_load reads the unquoted `on:` key as the boolean True (YAML 1.1)."""

    def test_outside_a_git_repo_falls_back_to_main(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        parsed = yaml.safe_load(_workflow_path(tmp_path).read_text())
        assert parsed[True]["push"]["branches"] == ["main"]

    def test_inside_a_git_repo_uses_the_current_branch(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-q", "-b", "trunk"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=test@rkd.local", "-c", "user.name=rkd", "commit", "-q", "-m", "initial"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        parsed = yaml.safe_load(_workflow_path(tmp_path).read_text())
        assert parsed[True]["push"]["branches"] == ["trunk"]


class TestInitRkdSpecFallback:
    def test_dev_version_yields_an_empty_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        monkeypatch.setattr(ci_cli, "__version__", "dev")

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        assert 'pip install "rocketdoo" ruff' in _workflow_path(tmp_path).read_text()

    def test_release_version_yields_a_pinned_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        monkeypatch.setattr(ci_cli, "__version__", "3.5.0")

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        assert 'pip install "rocketdoo~=3.5" ruff' in _workflow_path(tmp_path).read_text()


class TestInitRuffTargetFallback:
    def test_unknown_odoo_version_falls_back_to_py310(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        (tmp_path / "Dockerfile").write_text("FROM odoo:99.0\n")

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

        assert result.exit_code == 0, result.output
        assert "--target-version py310" in _workflow_path(tmp_path).read_text()


class TestInitInstallTrigger:
    @pytest.mark.parametrize("trigger", ci_cli.INSTALL_TRIGGERS)
    def test_install_job_presence_matches_the_trigger(self, tmp_path, monkeypatch, trigger):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)

        result = CliRunner().invoke(ci_cli.init, ["--install-trigger", trigger])

        assert result.exit_code == 0, result.output
        parsed = yaml.safe_load(_workflow_path(tmp_path).read_text())
        if trigger == "never":
            assert "install" not in parsed["jobs"]
        else:
            assert "install" in parsed["jobs"]

    def test_no_flag_without_a_terminal_defaults_to_pull_request(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)

        result = CliRunner().invoke(ci_cli.init, [])

        assert result.exit_code == 0, result.output
        parsed = yaml.safe_load(_workflow_path(tmp_path).read_text())
        assert "pull_request" in parsed["jobs"]["install"]["if"]

    def test_interactive_terminal_prompts_for_the_trigger(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        monkeypatch.setattr(ci_cli, "_stdin_is_interactive", lambda: True)

        result = CliRunner().invoke(ci_cli.init, [], input="push\n")

        assert result.exit_code == 0, result.output
        assert "When should the install job run" in result.output
        parsed = yaml.safe_load(_workflow_path(tmp_path).read_text())
        assert parsed["jobs"]["install"]["if"] == "github.event_name != 'workflow_dispatch'"

    def test_yes_flag_skips_the_prompt_even_in_a_terminal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _minimal_project(tmp_path)
        monkeypatch.setattr(ci_cli, "_stdin_is_interactive", lambda: True)

        result = CliRunner().invoke(ci_cli.init, ["--yes"])

        assert result.exit_code == 0, result.output
        assert "When should the install job run" not in result.output
        parsed = yaml.safe_load(_workflow_path(tmp_path).read_text())
        assert "pull_request" in parsed["jobs"]["install"]["if"]


def _compose(root, *args, timeout, check=True):
    result = subprocess.run(
        ["docker", "compose", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check:
        assert result.returncode == 0, f"docker compose {' '.join(args)}\n{result.stderr[-2000:]}"
    return result


@pytest.mark.docker
@pytest.mark.slow
class TestPrepareAgainstRealDockerBuild:
    """The gate this command exists for: a clean clone must build again.

    Without `rkd ci prepare`, `docker compose build` fails on this same
    project with "COPY ./config/odoo.conf: not found" (verified by hand).
    """

    @pytest.fixture(scope="class")
    @classmethod
    def cloned_project(cls, tmp_path_factory, docker_available):
        if not docker_available:
            pytest.skip("Docker daemon not available")

        from rocketdoo.init_project import init_from_profile
        from rocketdoo.scaffold import scaffold_project

        root = tmp_path_factory.mktemp("rkdciprepare")
        cwd = os.getcwd()
        os.chdir(root)
        try:
            scaffold_project()
            init_from_profile("odoo18-ce")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.email=test@rkd.local", "-c", "user.name=rkd", "commit", "-q", "-m", "initial"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "clean", "-xdf"], cwd=root, check=True, capture_output=True)
        finally:
            os.chdir(cwd)

        try:
            yield root
        finally:
            _compose(root, "down", "-v", timeout=300, check=False)

    def test_prepare_then_build_succeeds(self, cloned_project):
        assert not (cloned_project / "config" / "odoo.conf").exists()
        assert not (cloned_project / "odoo_pg_pass").exists()

        cwd = os.getcwd()
        os.chdir(cloned_project)
        try:
            result = CliRunner().invoke(ci_cli.prepare, [])
        finally:
            os.chdir(cwd)

        assert result.exit_code == 0, result.output
        assert (cloned_project / "config" / "odoo.conf").exists()
        assert (cloned_project / "odoo_pg_pass").exists()

        _compose(cloned_project, "build", "web", timeout=1800)


class TestInitWarnsAboutPrivateGitmanSources:
    """A gitman source cloned over SSH needs a key the runner does not have."""

    def _project(self, tmp_path, gitman_yaml=None):
        (tmp_path / "Dockerfile").write_text("FROM odoo:18.0\n")
        (tmp_path / "docker-compose.yaml").write_text("services:\n  web:\n    image: demo\n")
        (tmp_path / "addons").mkdir()
        if gitman_yaml is not None:
            (tmp_path / "gitman.yaml").write_text(gitman_yaml)
        return tmp_path

    def _init(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        return CliRunner().invoke(ci_cli.init, ["--install-trigger", "pull_request"])

    def test_ssh_sources_are_named(self, tmp_path, monkeypatch):
        self._project(
            tmp_path,
            "location: external_addons\nsources:\n"
            "  - name: private\n    repo: git@github.com:acme/private.git\n    rev: '18.0'\n"
            "  - name: public\n    repo: https://github.com/OCA/server-tools.git\n    rev: '18.0'\n",
        )

        output = self._init(tmp_path, monkeypatch).output

        assert "git@github.com:acme/private.git" in output
        assert "OCA/server-tools" not in output

    def test_no_gitman_file_means_no_warning(self, tmp_path, monkeypatch):
        self._project(tmp_path)

        assert "no key for" not in self._init(tmp_path, monkeypatch).output

    def test_a_broken_gitman_file_does_not_crash_init(self, tmp_path, monkeypatch):
        self._project(tmp_path, "sources: [unclosed\n")

        result = self._init(tmp_path, monkeypatch)

        assert result.exit_code == 0
