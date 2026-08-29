"""Tests for core/edition_setup.py.

`check_enterprise_folder` only answers whether `enterprise/` exists, so an
empty directory — exactly what a clone that failed for lack of credentials
leaves behind — read as ready, and Odoo then started without the Enterprise
addons. `validate_enterprise_setup` reports what would actually break.
"""

import pytest

from rocketdoo.core.edition_setup import (
    add_enterprise_to_odoo_conf,
    check_enterprise_folder,
    enable_enterprise_in_compose,
    enterprise_addons_count,
    validate_enterprise_setup,
)


def _module(root, name):
    mod = root / "enterprise" / name
    mod.mkdir(parents=True)
    (mod / "__manifest__.py").write_text(f"{{'name': '{name}'}}\n")
    return mod


class TestValidateEnterpriseSetup:
    def test_missing_directory_explains_how_to_get_it(self, tmp_path):
        issues = validate_enterprise_setup(tmp_path)
        assert len(issues) == 1
        assert "subscription credentials" in issues[0]

    def test_empty_directory_is_reported(self, tmp_path):
        """The failure mode a credential-less clone leaves behind."""
        (tmp_path / "enterprise").mkdir()
        issues = validate_enterprise_setup(tmp_path)
        assert len(issues) == 1
        assert "no Odoo modules" in issues[0]

    def test_a_real_checkout_passes(self, tmp_path):
        _module(tmp_path, "web_enterprise")
        _module(tmp_path, "account_accountant")
        assert validate_enterprise_setup(tmp_path) == []

    def test_modules_without_the_known_markers_are_flagged(self, tmp_path):
        _module(tmp_path, "my_custom_module")
        issues = validate_enterprise_setup(tmp_path)
        assert len(issues) == 1
        assert "web_enterprise" in issues[0]

    def test_a_file_named_enterprise_is_reported(self, tmp_path):
        (tmp_path / "enterprise").write_text("not a directory")
        assert "not a directory" in validate_enterprise_setup(tmp_path)[0]

    def test_directories_without_a_manifest_do_not_count(self, tmp_path):
        (tmp_path / "enterprise" / "docs").mkdir(parents=True)
        assert "no Odoo modules" in validate_enterprise_setup(tmp_path)[0]


class TestEnterpriseAddonsCount:
    def test_counts_only_modules(self, tmp_path):
        _module(tmp_path, "web_enterprise")
        _module(tmp_path, "account_accountant")
        (tmp_path / "enterprise" / "not_a_module").mkdir()
        assert enterprise_addons_count(tmp_path) == 2

    def test_zero_without_the_directory(self, tmp_path):
        assert enterprise_addons_count(tmp_path) == 0


class TestCheckEnterpriseFolder:
    def test_still_reports_mere_existence(self, tmp_path):
        """Kept for compatibility; validate_enterprise_setup is the real check."""
        assert check_enterprise_folder(tmp_path) is False
        (tmp_path / "enterprise").mkdir()
        assert check_enterprise_folder(tmp_path) is True


class TestComposeAndConf:
    def test_uncomments_the_enterprise_volume(self, tmp_path):
        compose = tmp_path / "docker-compose.yaml"
        compose.write_text(
            "services:\n  web:\n    volumes:\n      #- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise\n"
        )
        enable_enterprise_in_compose(compose)
        content = compose.read_text()
        assert "- ./enterprise:" in content
        assert "#- ./enterprise:" not in content

    def test_missing_compose_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            enable_enterprise_in_compose(tmp_path / "nope.yaml")

    def test_adds_the_enterprise_path_to_addons_path(self, tmp_path):
        conf = tmp_path / "odoo.conf"
        conf.write_text("[options]\naddons_path = /mnt/extra-addons\n")
        add_enterprise_to_odoo_conf(conf)
        assert "dist-packages/odoo/enterprise" in conf.read_text()
        assert "/mnt/extra-addons" in conf.read_text()

    def test_does_not_duplicate_the_enterprise_path(self, tmp_path):
        conf = tmp_path / "odoo.conf"
        conf.write_text("[options]\naddons_path = /mnt/extra-addons\n")
        add_enterprise_to_odoo_conf(conf)
        add_enterprise_to_odoo_conf(conf)
        assert conf.read_text().count("dist-packages/odoo/enterprise") == 1

    def test_creates_addons_path_when_absent(self, tmp_path):
        conf = tmp_path / "odoo.conf"
        conf.write_text("[options]\ndb_host = db\n")
        add_enterprise_to_odoo_conf(conf)
        assert "addons_path" in conf.read_text()
