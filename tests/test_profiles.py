"""Tests for the golden paths (core/models/profiles.py).

`init_project.py` used to carry two unrelated hardcoded lists — Odoo 15.0–19.0
and PostgreSQL 13–16 — so the wizard would happily generate Odoo 19 on
PostgreSQL 12, which Odoo does not support.

The per-image facts asserted here were read from the published `odoo:` images;
the PostgreSQL minimums come from Odoo's installation documentation.
"""

import pytest
import yaml
from pydantic import ValidationError

from rocketdoo.core.models.profiles import (
    GOLDEN_COMBINATIONS,
    PROFILE_DIR,
    PROFILES,
    RELEASES,
    SUPPORTED_ODOO_VERSIONS,
    GoldenPath,
    ProfileCatalog,
    check_compatibility,
    get_golden_path,
    get_release,
)


class TestReleaseFacts:
    """Facts about the published images, not preferences."""

    @pytest.mark.parametrize(
        ("version", "distro", "python", "pip"),
        [
            ("15.0", "debian-bullseye", "3.9", "20.3.4"),
            ("16.0", "debian-bullseye", "3.9", "20.3.4"),
            ("17.0", "ubuntu-jammy", "3.10", "22.0.2"),
            ("18.0", "ubuntu-noble", "3.12", "24.0"),
            ("19.0", "ubuntu-noble", "3.12", "24.0"),
        ],
    )
    def test_image_facts(self, version, distro, python, pip):
        release = get_release(version)
        assert release.base_distro == distro
        assert release.python_version == python
        assert release.pip_version == pip

    @pytest.mark.parametrize("version", ["15.0", "16.0", "17.0"])
    def test_older_images_lack_break_system_packages(self, version):
        """pip < 23. The flag that broke the build in #152."""
        assert get_release(version).supports_break_system_packages is False

    @pytest.mark.parametrize("version", ["18.0", "19.0"])
    def test_newer_images_have_break_system_packages(self, version):
        assert get_release(version).supports_break_system_packages is True

    @pytest.mark.parametrize("version", ["15.0", "16.0"])
    def test_bullseye_images_lack_pipx(self, version):
        """pipx is not packaged for Debian bullseye; the Dockerfile tolerates that."""
        assert get_release(version).has_pipx is False

    def test_three_distinct_bases_are_covered(self):
        assert len({r.base_distro for r in RELEASES.values()}) == 3

    def test_unsupported_version_names_the_supported_ones(self):
        with pytest.raises(ValueError, match="19.0"):
            get_release("13.0")


class TestPostgresCompatibility:
    @pytest.mark.parametrize("version", ["15.0", "16.0", "17.0", "18.0"])
    def test_odoo_15_to_18_require_postgres_12(self, version):
        assert get_release(version).postgres_minimum == 12

    def test_odoo_19_requires_postgres_13(self):
        """Odoo 19 raised the minimum from 12 to 13."""
        assert get_release("19.0").postgres_minimum == 13

    def test_the_old_hardcoded_pairing_is_now_rejected(self):
        """Odoo 19 + PostgreSQL 12 was reachable in the wizard before #139."""
        message = check_compatibility("19.0", "12")
        assert message is not None
        assert "13 or above" in message

    @pytest.mark.parametrize("db", ["13", "14", "15", "16", "17"])
    def test_odoo_19_accepts_13_and_above(self, db):
        assert check_compatibility("19.0", db) is None

    def test_odoo_18_accepts_postgres_12(self):
        assert check_compatibility("18.0", "12") is None

    def test_supported_list_excludes_versions_below_the_minimum(self):
        assert "12" not in get_release("19.0").supported_postgres()
        assert "12" in get_release("18.0").supported_postgres()

    def test_a_non_numeric_version_is_not_supported(self):
        assert get_release("18.0").supports_postgres("latest") is False


class TestProfileCatalog:
    def test_every_supported_version_has_both_editions(self):
        for version in SUPPORTED_ODOO_VERSIONS:
            major = version.split(".")[0]
            assert f"odoo{major}-ce" in PROFILES
            assert f"odoo{major}-ee" in PROFILES

    def test_catalog_is_loaded_from_the_yaml_files(self):
        """The YAML files are the source of truth for which profiles exist."""
        on_disk = {p.stem for p in PROFILE_DIR.glob("*.yaml")}
        assert on_disk == set(PROFILES)

    @pytest.mark.parametrize("path", sorted(PROFILE_DIR.glob("*.yaml")), ids=lambda p: p.stem)
    def test_every_profile_file_parses(self, path):
        data = yaml.safe_load(path.read_text())
        data.pop("golden", None)
        assert GoldenPath.model_validate(data).name == path.stem

    def test_profile_defaults_to_a_supported_postgres(self):
        for profile in PROFILES.values():
            assert check_compatibility(profile.odoo_version, profile.db_version) is None

    def test_editions_map_to_the_right_suffix(self):
        assert get_golden_path("odoo18-ce").edition == "Community"
        assert get_golden_path("odoo18-ee").edition == "Enterprise"

    def test_unknown_profile_lists_the_available_ones(self):
        with pytest.raises(ValueError, match="odoo18-ce"):
            get_golden_path("odoo99-ce")

    def test_golden_combinations_are_marked(self):
        for version, edition in GOLDEN_COMBINATIONS:
            major = version.split(".")[0]
            suffix = "ee" if edition == "Enterprise" else "ce"
            assert get_golden_path(f"odoo{major}-{suffix}").is_golden is True

    def test_non_golden_combinations_are_not_marked(self):
        assert get_golden_path("odoo16-ee").is_golden is False

    def test_catalog_rows_cover_every_profile(self):
        assert len(ProfileCatalog().rows()) == len(PROFILES)


class TestGoldenPathValidation:
    def test_rejects_an_unsupported_odoo_version(self):
        with pytest.raises(ValidationError):
            GoldenPath(name="x", description="x", odoo_version="13.0")

    def test_rejects_an_incompatible_postgres(self):
        with pytest.raises(ValidationError, match="13 or above"):
            GoldenPath(name="x", description="x", odoo_version="19.0", db_version="12")

    def test_defaults_to_the_recommended_postgres(self):
        assert GoldenPath(name="x", description="x", odoo_version="18.0").db_version == "16"

    def test_rejects_an_unknown_edition(self):
        with pytest.raises(ValidationError):
            GoldenPath(name="x", description="x", odoo_version="18.0", edition="Ultimate")


class TestProfileNotes:
    def test_enterprise_warns_about_the_addons_directory(self):
        notes = get_golden_path("odoo18-ee").notes()
        assert any("enterprise" in n.lower() for n in notes)

    def test_community_does_not_warn_about_enterprise(self):
        notes = get_golden_path("odoo18-ce").notes()
        assert not any("subscription" in n.lower() for n in notes)

    def test_non_golden_says_it_is_best_effort(self):
        assert any("best-effort" in n for n in get_golden_path("odoo16-ee").notes())

    def test_golden_does_not_say_best_effort(self):
        assert not any("best-effort" in n for n in get_golden_path("odoo18-ce").notes())

    def test_odoo_19_on_old_postgres_warns_about_pgvector(self):
        """Odoo 19's AI features need pgvector, which ships for PostgreSQL 15+."""
        profile = GoldenPath(name="x", description="x", odoo_version="19.0", db_version="13")
        assert any("pgvector" in n for n in profile.notes())

    def test_odoo_19_on_current_postgres_does_not_warn(self):
        assert not any("pgvector" in n for n in get_golden_path("odoo19-ce").notes())


class TestRenderContext:
    def test_context_carries_the_profile_values(self):
        context = get_golden_path("odoo18-ce").to_context("demo", "pw")
        assert context["odoo_version"] == "18.0"
        assert context["db_version"] == "16"
        assert context["odoo_image"] == "odoo:18.0"
        assert context["admin_passwd"] == "pw"

    def test_container_names_follow_the_project(self):
        context = get_golden_path("odoo18-ce").to_context("demo", "pw")
        assert context["odoo_container"] == "odoo-demo"
        assert context["db_container"] == "db-demo"

    @pytest.mark.parametrize("name", sorted(PROFILES))
    def test_every_profile_renders_a_valid_compose(self, name):
        """A profile that cannot render is not a golden path."""
        from tests.test_templates import render

        context = get_golden_path(name).to_context("demo", "pw")
        context.setdefault("use_third_party_repos", False)
        compose = yaml.safe_load(render("docker-compose.yaml.jinja", **context))
        assert {"web", "db"} <= set(compose["services"])
        assert compose["services"]["db"]["image"] == f"postgres:{get_golden_path(name).db_version}"

    @pytest.mark.parametrize("name", sorted(PROFILES))
    def test_every_profile_renders_the_right_dockerfile(self, name):
        from tests.test_templates import render

        profile = get_golden_path(name)
        context = profile.to_context("demo", "pw")
        context.setdefault("use_third_party_repos", False)
        assert f"FROM odoo:{profile.odoo_version}" in render("Dockerfile.jinja", **context)


class TestProfilesArePackaged:
    """The profiles must ship inside the installed package.

    They live under templates/, which package-data covers, but nothing else
    would notice if that stopped being true until `rkd profiles list` came back
    empty for an installed user.
    """

    def test_the_profile_directory_is_inside_the_package(self):
        from rocketdoo.core.models import profiles as module

        package_root = __import__("rocketdoo").__path__[0]
        assert str(PROFILE_DIR).startswith(package_root)
        assert module.PROFILE_DIR.is_dir()

    def test_ten_profiles_are_shipped(self):
        assert len(list(PROFILE_DIR.glob("*.yaml"))) == 10

    def test_the_readme_documents_the_policy(self):
        readme = (PROFILE_DIR / "README.md").read_text()
        assert "golden" in readme and "best effort" in readme
