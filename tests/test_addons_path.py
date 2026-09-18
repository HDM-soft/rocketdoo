"""Unit tests for core/addons_path.py.

A nested addons/oca/mod is invisible to Odoo unless its parent directory is
listed in addons_path too. These tests pin down discover()'s mapping from
addons/ to container paths, and ensure_addons_path()'s promise that merging
into config/odoo.conf never touches anything but the addons_path line.
"""

from rocketdoo.core.addons_path import CONTAINER_ADDONS_ROOT, discover, ensure_addons_path

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

    def test_accepts_a_string_path(self, tmp_path):
        _module(tmp_path / "addons", "oca/mod")
        _write_conf(tmp_path)
        action, _ = ensure_addons_path(str(tmp_path))
        assert action == "updated"
