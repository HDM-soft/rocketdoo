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

    def test_no_modules_prints_nothing_and_exits_zero(self, tmp_path, monkeypatch):
        (tmp_path / "addons").mkdir()

        result = self._run(tmp_path, monkeypatch)

        assert result.exit_code == 0
        assert result.stdout.strip() == ""

    def test_missing_addons_directory_is_not_an_error(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)

        assert result.exit_code == 0
        assert result.stdout.strip() == ""


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
