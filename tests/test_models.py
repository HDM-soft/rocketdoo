"""Tests for the shared domain models.

The models exist because the CLI and the GUI disagreed on the shape of
`.rkd/instance.yaml`: the wizard nested the connection under `vps:`, the GUI
wrote those fields flat, and the deployers — which read the nested shape —
failed with `KeyError: 'vps'` on anything the GUI saved.

The round-trip tests below are the contract: whichever shape comes in, the
canonical nested shape goes out.
"""

import pytest
import yaml
from pydantic import ValidationError

from rocketdoo.core.models import (
    PROFILES,
    SUPPORTED_ODOO_VERSIONS,
    DeployConfig,
    DeploymentResult,
    InstanceConfig,
    InstanceEnvironment,
    ProjectConfig,
    VPSConnection,
    get_profile,
)

# The two shapes found in the wild, describing the same environment.
NESTED = {
    "type": "docker",
    "vps": {
        "host": "vps.example.com",
        "port": 22,
        "user": "ubuntu",
        "auth_method": "ssh_key",
        "ssh_key": "~/.ssh/id_ed25519",
    },
    "odoo_version": "17.0",
    "domain": "stage.example.com",
    "traefik_email": "ops@example.com",
    "pg_profile": "small",
    "remote_path": "/opt/odoo-stage",
}

FLAT = {
    "type": "docker",
    "host": "vps.example.com",
    "port": 22,
    "user": "ubuntu",
    "auth_method": "ssh_key",
    "ssh_key": "~/.ssh/id_ed25519",
    "odoo_version": "17.0",
    "domain": "stage.example.com",
    "email": "ops@example.com",  # the GUI's name for traefik_email
    "pg_profile": "small",
    "remote_path": "/opt/odoo-stage",
}


class TestInstanceEnvironmentTolerantReader:
    @pytest.mark.parametrize("raw", [NESTED, FLAT], ids=["nested", "flat"])
    def test_both_shapes_parse(self, raw):
        env = InstanceEnvironment.model_validate(raw)
        assert env.vps.host == "vps.example.com"
        assert env.vps.ssh_key == "~/.ssh/id_ed25519"
        assert env.traefik_email == "ops@example.com"

    def test_both_shapes_produce_the_same_model(self):
        assert InstanceEnvironment.model_validate(NESTED) == InstanceEnvironment.model_validate(FLAT)

    def test_output_is_always_nested(self):
        """The deployers read env_config['vps']; that must always exist."""
        dumped = InstanceEnvironment.model_validate(FLAT).model_dump()
        assert "vps" in dumped
        assert dumped["vps"]["host"] == "vps.example.com"
        assert "host" not in dumped

    def test_the_gui_password_alias_is_accepted(self):
        env = InstanceEnvironment.model_validate(
            {"auth_method": "password", "password_ref": "${INSTANCE_STAGE_PASSWORD}", "host": "h"}
        )
        assert env.vps.password == "${INSTANCE_STAGE_PASSWORD}"

    def test_an_explicit_vps_block_wins_over_a_stray_flat_key(self):
        env = InstanceEnvironment.model_validate({"vps": {"host": "nested.example.com"}, "host": "flat.example.com"})
        assert env.vps.host == "nested.example.com"

    def test_odoo_tag_defaults_to_the_version(self):
        assert InstanceEnvironment.model_validate({"odoo_version": "18.0"}).odoo_tag == "18.0"

    def test_an_explicit_odoo_tag_is_kept(self):
        env = InstanceEnvironment.model_validate({"odoo_version": "18.0", "odoo_tag": "18.0-custom"})
        assert env.odoo_tag == "18.0-custom"


class TestInstanceEnvironmentValidation:
    def test_rejects_an_unknown_deployment_type(self):
        with pytest.raises(ValidationError):
            InstanceEnvironment.model_validate({"type": "kubernetes"})

    def test_rejects_an_unknown_pg_profile(self):
        with pytest.raises(ValidationError):
            InstanceEnvironment.model_validate({"pg_profile": "enormous"})

    def test_rejects_an_unknown_field(self):
        """A typo should fail loudly rather than being silently ignored."""
        with pytest.raises(ValidationError):
            InstanceEnvironment.model_validate({"odoo_verison": "17.0"})

    @pytest.mark.parametrize("port", [0, 70000, -1])
    def test_rejects_an_out_of_range_port(self, port):
        with pytest.raises(ValidationError):
            VPSConnection(port=port)

    def test_rejects_a_password_on_a_key_based_connection(self):
        with pytest.raises(ValidationError, match="ssh_key"):
            VPSConnection(auth_method="ssh_key", password="hunter2")

    def test_rejects_a_key_on_a_password_connection(self):
        with pytest.raises(ValidationError, match="password"):
            VPSConnection(auth_method="password", ssh_key="~/.ssh/id_rsa")


class TestDeployability:
    def test_a_complete_environment_is_deployable(self):
        assert InstanceEnvironment.model_validate(NESTED).is_deployable is True

    def test_missing_fields_are_named(self):
        env = InstanceEnvironment.model_validate({"type": "docker"})
        assert set(env.missing_fields()) == {"vps.host", "vps.ssh_key", "domain"}

    def test_a_password_environment_needs_a_password_not_a_key(self):
        env = InstanceEnvironment.model_validate({"host": "h", "domain": "d", "auth_method": "password"})
        assert env.missing_fields() == ["vps.password"]


class TestInstanceConfigRoundTrip:
    def test_yaml_round_trip_is_stable(self, tmp_path):
        """Writing and re-reading must not change the config."""
        config = InstanceConfig.model_validate({"environments": {"stage": NESTED}})
        path = tmp_path / "instance.yaml"
        path.write_text(yaml.dump(config.to_yaml_dict()))

        reloaded = InstanceConfig.model_validate(yaml.safe_load(path.read_text()))
        assert reloaded == config

    def test_a_flat_file_converges_on_the_canonical_shape(self, tmp_path):
        """A file written by the old GUI is readable and re-saved nested."""
        path = tmp_path / "instance.yaml"
        path.write_text(yaml.dump({"environments": {"stage": FLAT}}))

        config = InstanceConfig.model_validate(yaml.safe_load(path.read_text()))
        path.write_text(yaml.dump(config.to_yaml_dict()))

        rewritten = yaml.safe_load(path.read_text())
        assert "vps" in rewritten["environments"]["stage"]
        assert InstanceConfig.model_validate(rewritten) == config

    def test_accepts_a_file_without_the_environments_wrapper(self):
        config = InstanceConfig.model_validate({"stage": NESTED})
        assert config.get("stage").vps.host == "vps.example.com"

    def test_empty_config_has_no_environments(self):
        assert InstanceConfig.model_validate({}).environments == {}

    def test_get_returns_none_for_an_unknown_environment(self):
        assert InstanceConfig.model_validate({"environments": {}}).get("prod") is None


class TestDeployConfig:
    VPS_TARGET = {
        "type": "vps",
        "deployment_type": "docker",
        "connection": {"host": "h", "user": "u"},
    }

    def test_a_complete_config_validates(self):
        config = DeployConfig.model_validate({"modules": {}, "targets": {"prod": self.VPS_TARGET}})
        assert config.validation_errors() == []

    def test_reports_a_missing_modules_section(self):
        config = DeployConfig.model_validate({"targets": {"prod": self.VPS_TARGET}})
        assert any("modules" in e for e in config.validation_errors())

    def test_reports_no_targets(self):
        config = DeployConfig.model_validate({"modules": {}, "targets": {}})
        assert any("target" in e.lower() for e in config.validation_errors())

    def test_errors_name_the_target_and_the_field(self):
        config = DeployConfig.model_validate({"modules": {}, "targets": {"prod": {"type": "vps"}}})
        errors = config.validation_errors()
        assert any("prod" in e and "connection" in e for e in errors)
        assert any("prod" in e and "deployment_type" in e for e in errors)

    def test_rejects_both_credentials_on_one_connection(self):
        with pytest.raises(ValidationError):
            DeployConfig.model_validate(
                {
                    "targets": {
                        "prod": {
                            "type": "vps",
                            "deployment_type": "docker",
                            "connection": {"host": "h", "user": "u", "ssh_key": "k", "password": "p"},
                        }
                    }
                }
            )

    def test_yaml_round_trip(self, tmp_path):
        config = DeployConfig.model_validate({"modules": {}, "targets": {"prod": self.VPS_TARGET}})
        path = tmp_path / "deploy.yaml"
        path.write_text(yaml.dump(config.model_dump(mode="json")))
        assert DeployConfig.model_validate(yaml.safe_load(path.read_text())) == config


class TestDeploymentResult:
    def test_is_truthy_on_success(self):
        assert bool(DeploymentResult(success=True, message="ok")) is True

    def test_is_falsy_on_failure(self):
        assert bool(DeploymentResult(success=False, message="no")) is False

    def test_prints_a_status_prefix(self):
        assert "SUCCESS" in str(DeploymentResult(success=True, message="ok"))
        assert "FAILED" in str(DeploymentResult(success=False, message="no"))

    def test_details_default_to_empty(self):
        assert DeploymentResult(success=True, message="ok").details == {}

    def test_the_deploy_package_exports_the_same_class(self):
        """core/deploy/base.py re-exports it; both must be the same symbol."""
        from rocketdoo.core.deploy.base import DeploymentResult as FromBase

        assert FromBase is DeploymentResult


class TestProfiles:
    @pytest.mark.parametrize("version", SUPPORTED_ODOO_VERSIONS)
    def test_every_supported_version_has_a_profile(self, version):
        assert get_profile(version).odoo_version == version

    def test_an_unsupported_version_raises_with_the_supported_list(self):
        with pytest.raises(ValueError, match="19.0"):
            get_profile("13.0")

    def test_image_name(self):
        assert get_profile("17.0").image == "odoo:17.0"

    @pytest.mark.parametrize("version", ["15.0", "16.0", "17.0"])
    def test_older_images_cannot_use_break_system_packages(self, version):
        """The pip flag that broke the build in #152 (pip < 23)."""
        assert PROFILES[version].supports_break_system_packages is False

    @pytest.mark.parametrize("version", ["18.0", "19.0"])
    def test_newer_images_support_break_system_packages(self, version):
        assert PROFILES[version].supports_break_system_packages is True

    def test_the_profiles_span_three_distinct_bases(self):
        """One build target would not have caught #152."""
        assert len({p.base_distro for p in PROFILES.values()}) == 3

    def test_profiles_are_immutable(self):
        with pytest.raises(ValidationError):
            get_profile("17.0").odoo_version = "18.0"


class TestProjectConfig:
    def test_container_names_derive_from_the_project(self):
        config = ProjectConfig(project_name="demo")
        assert config.odoo_container == "odoo-demo"
        assert config.db_container == "db-demo"

    def test_explicit_container_names_are_kept(self):
        config = ProjectConfig(project_name="demo", odoo_container="custom")
        assert config.odoo_container == "custom"

    def test_rejects_an_uppercase_project_name(self):
        """Docker Compose rejects these, so `rkd init` lowercases them."""
        with pytest.raises(ValidationError, match="lowercase"):
            ProjectConfig(project_name="MyProject")

    def test_exposes_the_profile_for_its_version(self):
        assert ProjectConfig(project_name="demo", odoo_version="15.0").profile.base_distro == "debian-bullseye"

    @pytest.mark.parametrize("port", [80, 70000])
    def test_rejects_privileged_or_out_of_range_ports(self, port):
        with pytest.raises(ValidationError):
            ProjectConfig(project_name="demo", ports={"odoo": port})
