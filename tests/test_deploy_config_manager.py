"""Unit tests for core/deploy/config_manager.py.

deploy.yaml decides where modules get shipped, so validation gaps here surface
as a deployment against the wrong host or with half a connection block.
"""

import pytest
import yaml

from rocketdoo.core.deploy.config_manager import DeployConfigManager


@pytest.fixture
def manager(tmp_path) -> DeployConfigManager:
    return DeployConfigManager(tmp_path)


VPS_TARGET = {
    "type": "vps",
    "deployment_type": "docker",
    "connection": {"host": "vps.example.com", "user": "odoo", "port": 22},
}
ODOO_SH_TARGET = {
    "type": "odoo-sh",
    "odoo_sh": {"project_id": "abc123", "branch": "production"},
}


class TestPaths:
    def test_config_lives_under_dot_rkd(self, manager, tmp_path):
        assert manager.config_path == tmp_path / ".rkd" / "deploy.yaml"

    def test_config_exists_is_false_before_saving(self, manager):
        assert manager.config_exists() is False


class TestLoadSave:
    def test_save_creates_the_directory(self, manager):
        manager.save({"targets": {}})
        assert manager.config_exists()

    def test_roundtrip_preserves_content(self, manager):
        config = manager.get_default_config()
        config["targets"]["production"] = VPS_TARGET
        manager.save(config)
        assert manager.load() == config

    def test_save_writes_readable_yaml(self, manager):
        manager.save({"targets": {"prod": VPS_TARGET}})
        assert yaml.safe_load(manager.config_path.read_text())["targets"]["prod"]["type"] == "vps"

    def test_load_without_a_file_raises(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.load()

    def test_load_of_an_empty_file_returns_empty_dict(self, manager):
        manager.config_dir.mkdir(parents=True)
        manager.config_path.write_text("")
        assert manager.load() == {}

    def test_load_of_broken_yaml_raises_valueerror(self, manager):
        manager.config_dir.mkdir(parents=True)
        manager.config_path.write_text("targets: [unclosed\n")
        with pytest.raises(ValueError, match="Error loading configuration"):
            manager.load()


class TestValidate:
    def test_default_config_has_no_targets_yet(self, manager):
        """A fresh default config is incomplete on purpose: no target set up."""
        errors = manager.validate(manager.get_default_config())
        assert any("target" in e.lower() for e in errors)

    def test_a_complete_vps_config_validates(self, manager):
        config = manager.get_default_config()
        config["targets"]["production"] = VPS_TARGET
        assert manager.validate(config) == []

    def test_a_complete_odoo_sh_config_validates(self, manager):
        config = manager.get_default_config()
        config["targets"]["staging"] = ODOO_SH_TARGET
        assert manager.validate(config) == []

    @pytest.mark.parametrize("section", ["modules", "targets"])
    def test_missing_top_level_section_is_reported(self, manager, section):
        config = manager.get_default_config()
        config["targets"]["production"] = VPS_TARGET
        del config[section]
        assert any(section in e for e in manager.validate(config))

    def test_target_without_a_type_is_reported(self, manager):
        config = manager.get_default_config()
        config["targets"]["mystery"] = {"connection": {"host": "h", "user": "u"}}
        assert any("type" in e for e in manager.validate(config))

    @pytest.mark.parametrize("field", ["host", "user"])
    def test_vps_missing_connection_field_is_reported(self, manager, field):
        target = {**VPS_TARGET, "connection": dict(VPS_TARGET["connection"])}
        del target["connection"][field]
        config = manager.get_default_config()
        config["targets"]["production"] = target
        assert any(f"connection.{field}" in e for e in manager.validate(config))

    def test_vps_without_a_connection_section_is_reported(self, manager):
        target = {k: v for k, v in VPS_TARGET.items() if k != "connection"}
        config = manager.get_default_config()
        config["targets"]["production"] = target
        assert any("connection" in e for e in manager.validate(config))

    def test_vps_without_deployment_type_is_reported(self, manager):
        target = {k: v for k, v in VPS_TARGET.items() if k != "deployment_type"}
        config = manager.get_default_config()
        config["targets"]["production"] = target
        assert any("deployment_type" in e for e in manager.validate(config))

    @pytest.mark.parametrize("field", ["project_id", "branch"])
    def test_odoo_sh_missing_field_is_reported(self, manager, field):
        target = {"type": "odoo-sh", "odoo_sh": dict(ODOO_SH_TARGET["odoo_sh"])}
        del target["odoo_sh"][field]
        config = manager.get_default_config()
        config["targets"]["staging"] = target
        assert any(f"odoo_sh.{field}" in e for e in manager.validate(config))

    def test_errors_name_the_offending_target(self, manager):
        config = manager.get_default_config()
        config["targets"]["staging"] = {"type": "vps"}
        assert all("staging" in e for e in manager.validate(config))


class TestTargets:
    @pytest.fixture
    def configured(self, manager):
        config = manager.get_default_config()
        config["targets"]["production"] = VPS_TARGET
        manager.save(config)
        return manager

    def test_add_target_persists(self, configured):
        configured.add_target("staging", ODOO_SH_TARGET)
        assert configured.get_target("staging") == ODOO_SH_TARGET

    def test_list_targets(self, configured):
        configured.add_target("staging", ODOO_SH_TARGET)
        assert sorted(configured.list_targets()) == ["production", "staging"]

    def test_get_unknown_target_returns_none(self, configured):
        assert configured.get_target("ghost") is None

    def test_remove_target_persists(self, configured):
        configured.remove_target("production")
        assert configured.list_targets() == []

    def test_removing_an_unknown_target_raises(self, configured):
        with pytest.raises(ValueError, match="not found"):
            configured.remove_target("ghost")


class TestTemplates:
    @pytest.mark.parametrize("name", ["basic", "advanced"])
    def test_templates_produce_a_valid_config(self, manager, name):
        manager.create_from_template(name)
        assert manager.validate(manager.load()) == []

    def test_unknown_template_falls_back_to_basic(self, manager):
        manager.create_from_template("does-not-exist")
        assert manager.validate(manager.load()) == []
