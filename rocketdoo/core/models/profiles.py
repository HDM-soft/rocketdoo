"""Golden paths: the Odoo/PostgreSQL/edition combinations Rocketdoo supports.

Before this, `init_project.py` carried two hardcoded lists — Odoo 15.0–19.0 and
PostgreSQL 13–16 — with nothing connecting them, so the wizard happily produced
Odoo 19 on PostgreSQL 13 despite Odoo 19 requiring 13.0 or above, and never
warned about combinations Odoo does not support at all.

Two distinct notions of "supported" live here:

  golden    the combination CI actually renders and builds
  possible  within Odoo's stated requirements, best effort

The per-image facts (base distro, pip, Python) were read from the published
`odoo:` images rather than assumed; the PostgreSQL minimums come from Odoo's
own installation documentation.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Edition = Literal["Community", "Enterprise"]

# PostgreSQL majors Rocketdoo will generate a compose file for.
KNOWN_POSTGRES = ("12", "13", "14", "15", "16", "17")

# pgvector, which Odoo 19's AI features need, ships for PostgreSQL 15 and up.
PGVECTOR_MINIMUM = 15


class OdooRelease(BaseModel):
    """What is fixed for one Odoo version, independent of edition."""

    model_config = ConfigDict(frozen=True)

    odoo_version: str
    base_distro: str
    python_version: str
    pip_version: str
    # Odoo's documented minimum, as a major version number.
    postgres_minimum: int
    postgres_recommended: str

    @property
    def image(self) -> str:
        return f"odoo:{self.odoo_version}"

    @property
    def supports_break_system_packages(self) -> bool:
        """pip grew --break-system-packages in 23.0."""
        return int(self.pip_version.split(".")[0]) >= 23

    @property
    def has_pipx(self) -> bool:
        """pipx is not packaged for Debian bullseye (odoo:15.0 and 16.0)."""
        return self.base_distro != "debian-bullseye"

    def supported_postgres(self) -> list[str]:
        return [v for v in KNOWN_POSTGRES if int(v) >= self.postgres_minimum]

    def supports_postgres(self, db_version: str) -> bool:
        try:
            return int(str(db_version).split(".")[0]) >= self.postgres_minimum
        except ValueError:
            return False


# Base distro, Python and pip read from the published images.
# PostgreSQL minimums from Odoo's install documentation: 15-18 require 12.0 or
# above; 19 raised it to 13.0.
RELEASES: dict[str, OdooRelease] = {
    "15.0": OdooRelease(
        odoo_version="15.0",
        base_distro="debian-bullseye",
        python_version="3.9",
        pip_version="20.3.4",
        postgres_minimum=12,
        postgres_recommended="14",
    ),
    "16.0": OdooRelease(
        odoo_version="16.0",
        base_distro="debian-bullseye",
        python_version="3.9",
        pip_version="20.3.4",
        postgres_minimum=12,
        postgres_recommended="14",
    ),
    "17.0": OdooRelease(
        odoo_version="17.0",
        base_distro="ubuntu-jammy",
        python_version="3.10",
        pip_version="22.0.2",
        postgres_minimum=12,
        postgres_recommended="15",
    ),
    "18.0": OdooRelease(
        odoo_version="18.0",
        base_distro="ubuntu-noble",
        python_version="3.12",
        pip_version="24.0",
        postgres_minimum=12,
        postgres_recommended="16",
    ),
    "19.0": OdooRelease(
        odoo_version="19.0",
        base_distro="ubuntu-noble",
        python_version="3.12",
        pip_version="24.0",
        postgres_minimum=13,
        postgres_recommended="16",
    ),
}

SUPPORTED_ODOO_VERSIONS = tuple(RELEASES)

# Combinations CI renders and builds on every release PR.
GOLDEN_COMBINATIONS = (
    ("15.0", "Community"),
    ("18.0", "Community"),
    ("19.0", "Enterprise"),
)


class GoldenPath(BaseModel):
    """A named, ready-to-use Odoo environment definition."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    odoo_version: str
    edition: Edition = "Community"
    db_version: str = ""
    odoo_port: int = 8069
    vsc_port: int = 8888
    restart: str = "unless-stopped"

    @model_validator(mode="after")
    def _check_and_fill(self) -> "GoldenPath":
        release = RELEASES.get(self.odoo_version)
        if release is None:
            supported = ", ".join(SUPPORTED_ODOO_VERSIONS)
            raise ValueError(f"Unsupported Odoo version {self.odoo_version!r}. Supported: {supported}")
        if not self.db_version:
            object.__setattr__(self, "db_version", release.postgres_recommended)
        elif not release.supports_postgres(self.db_version):
            raise ValueError(
                f"Odoo {self.odoo_version} requires PostgreSQL {release.postgres_minimum} or above, got {self.db_version}"
            )
        return self

    @property
    def release(self) -> OdooRelease:
        return RELEASES[self.odoo_version]

    @property
    def is_golden(self) -> bool:
        return (self.odoo_version, self.edition) in GOLDEN_COMBINATIONS

    @property
    def uses_enterprise(self) -> bool:
        return self.edition == "Enterprise"

    def notes(self) -> list[str]:
        """Things worth telling the user before generating this environment."""
        messages = []
        if self.uses_enterprise:
            messages.append(
                "Enterprise requires an ./enterprise directory with the Odoo Enterprise addons (subscription required)."
            )
        if self.odoo_version == "19.0" and int(self.db_version) < PGVECTOR_MINIMUM:
            messages.append(
                f"Odoo 19's AI features need the pgvector extension, which ships for "
                f"PostgreSQL {PGVECTOR_MINIMUM} and above; this profile uses {self.db_version}."
            )
        if not self.is_golden:
            messages.append("This combination is supported on a best-effort basis; CI does not build it.")
        return messages

    def to_context(self, project_name: str, admin_passwd: str) -> dict:
        """The render context `rkd init` feeds to the Jinja templates."""
        return {
            "project_name": project_name,
            "odoo_version": self.odoo_version,
            "odoo_edition": self.edition,
            "db_version": self.db_version,
            "odoo_port": self.odoo_port,
            "vsc_port": self.vsc_port,
            "restart": self.restart,
            "odoo_image": self.release.image,
            "odoo_container": f"odoo-{project_name}",
            "db_container": f"db-{project_name}",
            "admin_passwd": admin_passwd,
        }


PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "templates" / "profiles"


def _load_catalog() -> dict[str, GoldenPath]:
    """Read the profiles shipped under templates/profiles/.

    The YAML files are the source of truth for which combinations exist, so a
    user can drop another one in without touching the package.
    """
    catalog: dict[str, GoldenPath] = {}
    if not PROFILE_DIR.is_dir():
        return catalog
    for path in sorted(PROFILE_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        data.pop("golden", None)  # derived from GOLDEN_COMBINATIONS, not declared
        data.setdefault("name", path.stem)
        catalog[data["name"]] = GoldenPath.model_validate(data)
    return catalog


PROFILES: dict[str, GoldenPath] = _load_catalog()


def get_golden_path(name: str) -> GoldenPath:
    """Look up a profile by name, e.g. "odoo18-ce"."""
    try:
        return PROFILES[name]
    except KeyError:
        available = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {name!r}. Available: {available}") from None


def get_release(odoo_version: str) -> OdooRelease:
    try:
        return RELEASES[odoo_version]
    except KeyError:
        supported = ", ".join(SUPPORTED_ODOO_VERSIONS)
        raise ValueError(f"Unsupported Odoo version {odoo_version!r}. Supported: {supported}") from None


def check_compatibility(odoo_version: str, db_version: str) -> str | None:
    """Return an explanation if this pairing is unsupported, else None."""
    release = get_release(odoo_version)
    if release.supports_postgres(db_version):
        return None
    return (
        f"Odoo {odoo_version} requires PostgreSQL {release.postgres_minimum} or above; "
        f"PostgreSQL {db_version} is not supported. "
        f"Supported here: {', '.join(release.supported_postgres())}."
    )


class ProfileCatalog(BaseModel):
    """The whole matrix, for `rkd profiles list` and the docs."""

    model_config = ConfigDict(frozen=True)

    profiles: dict[str, GoldenPath] = Field(default_factory=lambda: dict(PROFILES))

    def rows(self) -> list[dict]:
        rows = []
        for name, profile in self.profiles.items():
            release = profile.release
            rows.append(
                {
                    "name": name,
                    "odoo_version": profile.odoo_version,
                    "edition": profile.edition,
                    "db_version": profile.db_version,
                    "postgres_supported": ", ".join(release.supported_postgres()),
                    "base_distro": release.base_distro,
                    "python": release.python_version,
                    "golden": profile.is_golden,
                }
            )
        return sorted(rows, key=lambda r: (r["odoo_version"], r["edition"]))
