"""Unit tests for core/addons_path.py.

A nested addons/oca/mod is invisible to Odoo unless its parent directory is
listed in addons_path too. These tests pin down discover()'s mapping from
addons/ to container paths, and ensure_addons_path()'s promise that merging
into config/odoo.conf never touches anything but the addons_path line.
"""

import os
import subprocess

import pytest
from click.testing import CliRunner

from rocketdoo.core.addons_path import CONTAINER_ADDONS_ROOT, discover, ensure_addons_path


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


def _set_addons_path(root, value):
    conf = root / "config" / "odoo.conf"
    lines = conf.read_text().splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("addons_path"):
            lines[index] = f"addons_path = {value}"
    conf.write_text("\n".join(lines) + "\n")


def _module_state(root, database, module):
    """The module's state in ir_module_module, or None if Odoo never saw it."""
    result = _compose(
        root,
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "root",
        "-d",
        database,
        "-tAc",
        f"SELECT state FROM ir_module_module WHERE name = '{module}';",
        timeout=120,
    )
    return result.stdout.strip() or None


ODOO_CONF_TEMPLATE = """[options]
addons_path = {addons_path}
data_dir = /var/lib/odoo
admin_passwd = super-secret
db_host = ps-container
db_port = 5432
db_user = root
db_password = odoo
; csv_internal_sep = ,
; db_maxconn = 64
log_handler = [':DEBUG']
log_level = debug
gevent_port = 8072
"""


def _write_conf(project_root, addons_path=CONTAINER_ADDONS_ROOT):
    config_dir = project_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    conf = config_dir / "odoo.conf"
    conf.write_text(ODOO_CONF_TEMPLATE.format(addons_path=addons_path))
    return conf


def _module(addons_dir, relative_name):
    mod = addons_dir / relative_name
    mod.mkdir(parents=True)
    (mod / "__manifest__.py").write_text("{'name': 'x', 'installable': True}\n")
    return mod


class TestDiscover:
    def test_no_addons_directory_returns_only_the_root(self, tmp_path):
        assert discover(tmp_path) == [CONTAINER_ADDONS_ROOT]

    def test_flat_module_does_not_add_a_new_entry(self, tmp_path):
        _module(tmp_path / "addons", "mod")
        assert discover(tmp_path) == [CONTAINER_ADDONS_ROOT]

    def test_module_nested_one_level_adds_its_parent(self, tmp_path):
        _module(tmp_path / "addons", "oca/mod")
        assert discover(tmp_path) == [CONTAINER_ADDONS_ROOT, f"{CONTAINER_ADDONS_ROOT}/oca"]

    def test_module_nested_two_levels_adds_its_parent(self, tmp_path):
        _module(tmp_path / "addons", "oca/sub/mod")
        assert discover(tmp_path) == [CONTAINER_ADDONS_ROOT, f"{CONTAINER_ADDONS_ROOT}/oca/sub"]

    def test_setup_directories_are_excluded(self, tmp_path):
        _module(tmp_path / "addons", "setup/mod/odoo/addons/mod")
        assert discover(tmp_path) == [CONTAINER_ADDONS_ROOT]

    def test_deduplicates_and_sorts_with_the_root_first(self, tmp_path):
        addons = tmp_path / "addons"
        _module(addons, "oca/mod_a")
        _module(addons, "oca/mod_b")
        _module(addons, "aca/mod_c")
        assert discover(tmp_path) == [
            CONTAINER_ADDONS_ROOT,
            f"{CONTAINER_ADDONS_ROOT}/aca",
            f"{CONTAINER_ADDONS_ROOT}/oca",
        ]

    def test_accepts_a_string_path(self, tmp_path):
        _module(tmp_path / "addons", "oca/mod")
        assert discover(str(tmp_path)) == [CONTAINER_ADDONS_ROOT, f"{CONTAINER_ADDONS_ROOT}/oca"]


class TestEnsureAddonsPathMissingConf:
    def test_missing_odoo_conf_reports_missing_without_writing(self, tmp_path):
        assert ensure_addons_path(tmp_path) == ("missing", [])
        assert not (tmp_path / "config" / "odoo.conf").exists()


class TestEnsureAddonsPathOk:
    def test_root_only_conf_with_no_addons_is_already_ok(self, tmp_path):
        _write_conf(tmp_path)
        assert ensure_addons_path(tmp_path) == ("ok", [])

    def test_flat_module_needs_no_update(self, tmp_path):
        _module(tmp_path / "addons", "mod")
        _write_conf(tmp_path)
        assert ensure_addons_path(tmp_path) == ("ok", [])

    def test_second_run_is_a_no_op(self, tmp_path):
        _module(tmp_path / "addons", "oca/mod")
        _write_conf(tmp_path)
        ensure_addons_path(tmp_path)
        assert ensure_addons_path(tmp_path) == ("ok", [])


class TestEnsureAddonsPathUpdated:
    def test_nested_module_adds_its_parent(self, tmp_path):
        _module(tmp_path / "addons", "oca/mod")
        _write_conf(tmp_path)

        action, changes = ensure_addons_path(tmp_path)

        assert action == "updated"
        assert changes == [f"+{CONTAINER_ADDONS_ROOT}/oca"]
        conf = tmp_path / "config" / "odoo.conf"
        addons_line = next(line for line in conf.read_text().splitlines() if line.startswith("addons_path"))
        assert addons_line == f"addons_path = {CONTAINER_ADDONS_ROOT},{CONTAINER_ADDONS_ROOT}/oca"

    def test_preserves_foreign_entries_and_their_order(self, tmp_path):
        _module(tmp_path / "addons", "oca/mod")
        enterprise = "/usr/lib/python3/dist-packages/odoo/enterprise"
        external = "/usr/lib/python3/dist-packages/odoo/external_addons/x"
        _write_conf(tmp_path, addons_path=f"{enterprise},{CONTAINER_ADDONS_ROOT},{external}")

        action, _ = ensure_addons_path(tmp_path)

        assert action == "updated"
        conf = tmp_path / "config" / "odoo.conf"
        addons_line = next(line for line in conf.read_text().splitlines() if line.startswith("addons_path"))
        assert addons_line == (f"addons_path = {enterprise},{CONTAINER_ADDONS_ROOT},{CONTAINER_ADDONS_ROOT}/oca,{external}")

    def test_pruning_a_deleted_subdirectory(self, tmp_path):
        oca = tmp_path / "addons" / "oca"
        _module(tmp_path / "addons", "oca/mod")
        enterprise = "/usr/lib/python3/dist-packages/odoo/enterprise"
        _write_conf(tmp_path, addons_path=f"{enterprise},{CONTAINER_ADDONS_ROOT},{CONTAINER_ADDONS_ROOT}/oca")

        import shutil

        shutil.rmtree(oca)

        action, changes = ensure_addons_path(tmp_path)

        assert action == "updated"
        assert changes == [f"-{CONTAINER_ADDONS_ROOT}/oca"]
        conf = tmp_path / "config" / "odoo.conf"
        addons_line = next(line for line in conf.read_text().splitlines() if line.startswith("addons_path"))
        assert addons_line == f"addons_path = {enterprise},{CONTAINER_ADDONS_ROOT}"

    def test_missing_addons_path_line_adds_one(self, tmp_path):
        _module(tmp_path / "addons", "oca/mod")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        conf = config_dir / "odoo.conf"
        conf.write_text("[options]\nadmin_passwd = super-secret\n")

        action, _ = ensure_addons_path(tmp_path)

        assert action == "updated"
        lines = conf.read_text().splitlines()
        assert lines[0] == "[options]"
        assert lines[1] == "admin_passwd = super-secret"
        assert lines[2] == f"addons_path = {CONTAINER_ADDONS_ROOT},{CONTAINER_ADDONS_ROOT}/oca"

    def test_only_the_addons_path_line_changes(self, tmp_path):
        _module(tmp_path / "addons", "oca/mod")
        conf = _write_conf(tmp_path)
        before = conf.read_text().splitlines()

        ensure_addons_path(tmp_path)

        after = conf.read_text().splitlines()
        assert len(before) == len(after)
        for i, (old_line, new_line) in enumerate(zip(before, after)):
            if old_line.startswith("addons_path"):
                assert new_line != old_line
                continue
            assert new_line == old_line, f"line {i} changed unexpectedly"

    def test_a_sibling_sharing_the_prefix_is_not_pruned(self, tmp_path):
        """A path next to the managed root is the user's, not ours.

        .../extra-addons-private starts with the managed root's string but is
        a different directory. Claiming it as managed deleted it from the
        user's config on the next rkd up.
        """
        sibling = f"{CONTAINER_ADDONS_ROOT}-private"
        conf = _write_conf(tmp_path, addons_path=f"{CONTAINER_ADDONS_ROOT},{sibling}")
        _module(tmp_path / "addons", "oca/mod_a")

        action, _ = ensure_addons_path(tmp_path)

        assert action == "updated"
        assert sibling in conf.read_text()

    def test_a_comma_in_a_directory_name_never_enters_the_line(self, tmp_path):
        """addons_path is one comma-separated line, so a comma would split.

        The bogus half then looks like a foreign entry and is kept, so every
        run appends another one and the line never converges.
        """
        conf = _write_conf(tmp_path)
        _module(tmp_path / "addons", "a,b/mod")

        first = ensure_addons_path(tmp_path)
        second = ensure_addons_path(tmp_path)

        assert first == ("ok", [])
        assert second == ("ok", [])
        assert "a,b" not in conf.read_text()

    def test_a_newline_in_a_directory_name_never_enters_the_line(self, tmp_path):
        conf = _write_conf(tmp_path)
        _module(tmp_path / "addons", "we\nird/mod")

        ensure_addons_path(tmp_path)

        assert len([ln for ln in conf.read_text().splitlines() if ln.startswith("addons_path")]) == 1

    def test_an_unwritable_conf_is_reported_not_raised(self, tmp_path):
        """rkd up calls this before starting; a read-only config cannot stop it."""
        conf = _write_conf(tmp_path)
        _module(tmp_path / "addons", "oca/mod_a")
        conf.chmod(0o444)
        try:
            action, changes = ensure_addons_path(tmp_path)
        finally:
            conf.chmod(0o644)

        assert action == "failed"
        assert changes

    def test_a_similarly_named_key_is_not_the_addons_path(self, tmp_path):
        conf = _write_conf(tmp_path)
        conf.write_text(conf.read_text() + f"addons_path_backup = {CONTAINER_ADDONS_ROOT}/old\n")
        _module(tmp_path / "addons", "oca/mod_a")

        ensure_addons_path(tmp_path)

        text = conf.read_text()
        assert f"addons_path_backup = {CONTAINER_ADDONS_ROOT}/old" in text
        assert len([ln for ln in text.splitlines() if ln.startswith("addons_path ")]) == 1

    def test_crlf_line_endings_survive(self, tmp_path):
        """A config written on Windows must not be rewritten wholesale."""
        conf = _write_conf(tmp_path)
        conf.write_bytes(conf.read_text().replace("\n", "\r\n").encode())
        before = conf.read_bytes().count(b"\r\n")
        _module(tmp_path / "addons", "oca/mod_a")

        action, _ = ensure_addons_path(tmp_path)

        assert action == "updated"
        assert conf.read_bytes().count(b"\r\n") == before
        assert b"extra-addons/oca" in conf.read_bytes()

    def test_lf_line_endings_stay_lf(self, tmp_path):
        conf = _write_conf(tmp_path)
        _module(tmp_path / "addons", "oca/mod_a")

        ensure_addons_path(tmp_path)

        assert b"\r" not in conf.read_bytes()

    def test_accepts_a_string_path(self, tmp_path):
        _module(tmp_path / "addons", "oca/mod")
        _write_conf(tmp_path)
        action, _ = ensure_addons_path(str(tmp_path))
        assert action == "updated"


class TestUpSyncsAddonsPathFirst:
    """`rkd up` (RF2) must leave addons_path in sync before docker ever runs."""

    def test_addons_path_is_updated_before_docker_compose_is_invoked(self, tmp_path, monkeypatch):
        from rocketdoo import docker_cli

        monkeypatch.chdir(tmp_path)
        _module(tmp_path / "addons", "oca/mod")
        conf = _write_conf(tmp_path)
        monkeypatch.setattr(docker_cli, "ensure_docker_installed", lambda: None)

        conf_seen_by_docker = {}

        def fake_run(cmd, *args, **kwargs):
            conf_seen_by_docker["addons_path"] = conf.read_text()

        monkeypatch.setattr(docker_cli.subprocess, "run", fake_run)

        result = CliRunner().invoke(docker_cli.up, [])

        assert result.exit_code == 0
        assert f"{CONTAINER_ADDONS_ROOT}/oca" in conf_seen_by_docker["addons_path"]
        assert "addons_path updated" in result.output

    def test_stays_silent_when_addons_path_is_already_up_to_date(self, tmp_path, monkeypatch):
        from rocketdoo import docker_cli

        monkeypatch.chdir(tmp_path)
        _write_conf(tmp_path)
        monkeypatch.setattr(docker_cli, "ensure_docker_installed", lambda: None)
        monkeypatch.setattr(docker_cli.subprocess, "run", lambda *a, **k: None)

        result = CliRunner().invoke(docker_cli.up, [])

        assert result.exit_code == 0
        assert "addons_path" not in result.output


class TestInfoWarnsWithoutWriting:
    """`rkd info` (RF2) only reads: it must never call ensure_addons_path()."""

    def _minimal_project(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM odoo:18.0\n")
        (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
        return _write_conf(tmp_path)

    def test_warns_about_a_nested_module_and_leaves_odoo_conf_untouched(self, tmp_path, monkeypatch):
        from rocketdoo import cli

        monkeypatch.chdir(tmp_path)
        conf = self._minimal_project(tmp_path)
        _module(tmp_path / "addons", "oca/mod")
        before = conf.read_text()

        result = CliRunner().invoke(cli.info, [])

        assert result.exit_code == 0
        assert f"{CONTAINER_ADDONS_ROOT}/oca" in result.output
        assert conf.read_text() == before

    def test_says_nothing_when_addons_path_already_covers_everything(self, tmp_path, monkeypatch):
        from rocketdoo import cli

        monkeypatch.chdir(tmp_path)
        self._minimal_project(tmp_path)

        result = CliRunner().invoke(cli.info, [])

        assert result.exit_code == 0
        assert "addons_path" not in result.output


@pytest.mark.docker
@pytest.mark.slow
class TestAgainstRealOdoo:
    """The only authority on whether Odoo sees a nested module.

    Odoo does not fail when a module passed to -i is not on the addons_path:
    it logs "Modules loaded" and exits 0 with the module simply absent. That
    silent no-op is why these assertions read ir_module_module instead of the
    exit code, which stays 0 either way.
    """

    MODULE = "rkd_probe"

    @pytest.fixture(scope="class")
    @classmethod
    def project(cls, tmp_path_factory, docker_available):
        if not docker_available:
            pytest.skip("Docker daemon not available")

        from rocketdoo.init_project import init_from_profile
        from rocketdoo.scaffold import scaffold_project

        root = tmp_path_factory.mktemp("rkdci")
        cwd = os.getcwd()
        os.chdir(root)
        try:
            scaffold_project()
            init_from_profile("odoo18-ce")
        finally:
            os.chdir(cwd)

        nested = root / "addons" / "oca" / cls.MODULE
        nested.mkdir(parents=True)
        (nested / "__manifest__.py").write_text(
            f'{{"name": "{cls.MODULE}", "version": "18.0.1.0.0", "depends": ["base"], "installable": True}}\n'
        )
        (nested / "__init__.py").write_text("")

        _compose(root, "build", "web", timeout=1800)
        _compose(root, "up", "-d", "db", timeout=300)
        try:
            yield root
        finally:
            _compose(root, "down", "-v", timeout=300, check=False)

    def _install(self, root, database):
        _compose(
            root,
            "run",
            "--rm",
            "-T",
            "web",
            "odoo",
            "-d",
            database,
            "-i",
            self.MODULE,
            "--stop-after-init",
            "--without-demo=all",
            timeout=1800,
        )
        return _module_state(root, database, self.MODULE)

    def test_nested_module_is_invisible_without_the_fix(self, project):
        """Control: this is the bug the fix exists for."""
        _set_addons_path(project, CONTAINER_ADDONS_ROOT)
        assert self._install(project, "probe_before") is None

    def test_nested_module_installs_after_ensure_addons_path(self, project):
        action, _ = ensure_addons_path(project)
        assert action == "updated"
        assert self._install(project, "probe_after") == "installed"
