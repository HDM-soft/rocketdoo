"""Unit tests for core/module_scanner.py.

The scanner decides what `rkd deploy` packages and what the GUI lists, so a
directory wrongly treated as a module is a real deployment hazard.
"""
import pytest

from rocketdoo.core.module_scanner import ModuleScanner, OdooModule, find_odoo_modules


class TestScan:
    def test_finds_modules_with_a_manifest(self, addons_tree):
        names = [m.name for m in ModuleScanner(addons_tree).scan()]
        assert names == ["legacy_module", "sale_extension", "stock_custom"]

    def test_ignores_directories_without_a_manifest(self, addons_tree):
        assert "not_a_module" not in [m.name for m in ModuleScanner(addons_tree).scan()]

    def test_excludes_the_tests_directory_itself(self, addons_tree):
        """`*/tests/*` must drop `.../tests`, not only paths nested under it.

        A tests/__manifest__.py used to be reported as a module named "tests".
        """
        assert "tests" not in [m.name for m in ModuleScanner(addons_tree).scan()]

    @pytest.mark.parametrize("junk", ["__pycache__", "node_modules"])
    def test_excludes_generated_directories(self, addons_tree, junk):
        mod = addons_tree / "sale_extension" / junk
        mod.mkdir(parents=True)
        (mod / "__manifest__.py").write_text("{'name': 'junk'}\n")
        assert junk not in [m.name for m in ModuleScanner(addons_tree).scan(force_rescan=True)]

    def test_keeps_a_module_whose_name_merely_contains_tests(self, addons_tree):
        mod = addons_tree / "sale_tests_helper"
        mod.mkdir()
        (mod / "__manifest__.py").write_text("{'name': 'helper', 'version': '16.0.1.0.0'}\n")
        assert "sale_tests_helper" in [m.name for m in ModuleScanner(addons_tree).scan(force_rescan=True)]

    def test_missing_addons_directory_returns_empty(self, tmp_path):
        assert ModuleScanner(tmp_path / "nope").scan() == []

    def test_results_are_cached_until_forced(self, addons_tree):
        scanner = ModuleScanner(addons_tree)
        first = scanner.scan()
        new = addons_tree / "added_later"
        new.mkdir()
        (new / "__manifest__.py").write_text("{'name': 'Added'}\n")

        assert len(scanner.scan()) == len(first)
        assert len(scanner.scan(force_rescan=True)) == len(first) + 1


class TestOdooModule:
    def test_reads_manifest_fields(self, addons_tree):
        mod = ModuleScanner(addons_tree).get_module_by_name("stock_custom")
        assert mod.version == "16.0.1.0.0"
        assert mod.depends == ["base", "stock"]
        assert mod.is_installable is True

    def test_installable_defaults_to_true_when_absent(self, tmp_path):
        mod_dir = tmp_path / "addons" / "minimal"
        mod_dir.mkdir(parents=True)
        (mod_dir / "__manifest__.py").write_text("{'name': 'Minimal'}\n")
        assert OdooModule(mod_dir, tmp_path / "addons").is_installable is True

    def test_non_installable_is_detected(self, addons_tree):
        mod = ModuleScanner(addons_tree).get_module_by_name("legacy_module")
        assert mod.is_installable is False

    def test_unreadable_manifest_degrades_to_empty(self, tmp_path):
        """A syntax error must not crash a scan of the whole addons tree."""
        mod_dir = tmp_path / "addons" / "broken"
        mod_dir.mkdir(parents=True)
        (mod_dir / "__manifest__.py").write_text("{'name': 'Broken',\n")
        mod = OdooModule(mod_dir, tmp_path / "addons")
        assert mod.manifest == {}
        assert any("__manifest__.py" in issue for issue in mod.validate())

    def test_hyphenated_name_is_flagged(self, tmp_path):
        mod_dir = tmp_path / "addons" / "bad-name"
        mod_dir.mkdir(parents=True)
        (mod_dir / "__manifest__.py").write_text("{'name': 'Bad'}\n")
        mod = OdooModule(mod_dir, tmp_path / "addons")
        assert mod.has_invalid_name is True
        assert any("hyphen" in issue.lower() for issue in mod.validate())

    def test_relative_path_is_relative_to_addons_root(self, addons_tree):
        mod = ModuleScanner(addons_tree).get_module_by_name("sale_extension")
        assert str(mod.relative_path) == "sale_extension"

    def test_to_dict_is_serialisable(self, addons_tree):
        import json

        data = ModuleScanner(addons_tree).get_module_by_name("sale_extension").to_dict()
        assert set(data) >= {"name", "path", "installable", "version", "depends"}
        json.dumps(data)  # the GUI returns this over HTTP


class TestScannerQueries:
    def test_get_installable_modules_filters(self, addons_tree):
        names = [m.name for m in ModuleScanner(addons_tree).get_installable_modules()]
        assert "legacy_module" not in names
        assert "sale_extension" in names

    def test_get_module_by_name_returns_none_when_absent(self, addons_tree):
        assert ModuleScanner(addons_tree).get_module_by_name("ghost") is None

    def test_validate_all_reports_only_modules_with_issues(self, addons_tree):
        results = ModuleScanner(addons_tree).validate_all()
        assert "legacy_module" in results  # flagged as non-installable

    def test_find_odoo_modules_helper_returns_dicts(self, addons_tree):
        mods = find_odoo_modules(addons_tree)
        assert all(isinstance(m, dict) for m in mods)
        assert {m["name"] for m in mods} == {"legacy_module", "sale_extension", "stock_custom"}
