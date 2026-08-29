"""Domain models for a local Rocketdoo project.

`ProjectConfig` describes what project_info.py reconstructs today by scraping
the generated Dockerfile and docker-compose.yaml. `Profile` captures what is
fixed per Odoo version, so the golden paths in #139 have something to build on
rather than rediscovering it.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Edition = Literal["Community", "Enterprise"]

SUPPORTED_ODOO_VERSIONS = ("15.0", "16.0", "17.0", "18.0", "19.0")


class Profile(BaseModel):
    """What is fixed for a given Odoo version.

    `base_distro` and `pip_version` are not cosmetic: the odoo images span
    three bases with three pip versions, which is why a pip flag can work on
    one and break on another (issue #152). Anything generating a Dockerfile
    needs to know which it is targeting.
    """

    model_config = ConfigDict(frozen=True)

    odoo_version: str
    base_distro: str
    pip_version: str
    default_db_version: str
    supports_break_system_packages: bool
    has_pipx: bool

    @property
    def image(self) -> str:
        return f"odoo:{self.odoo_version}"


# pip 23 introduced --break-system-packages; the older bases predate it.
# Removing EXTERNALLY-MANAGED works everywhere, which is what the template does.
PROFILES: dict[str, Profile] = {
    "15.0": Profile(
        odoo_version="15.0",
        base_distro="debian-bullseye",
        pip_version="20.3",
        default_db_version="13",
        supports_break_system_packages=False,
        has_pipx=False,
    ),
    "16.0": Profile(
        odoo_version="16.0",
        base_distro="debian-bullseye",
        pip_version="20.3",
        default_db_version="14",
        supports_break_system_packages=False,
        has_pipx=False,
    ),
    "17.0": Profile(
        odoo_version="17.0",
        base_distro="ubuntu-jammy",
        pip_version="22.0",
        default_db_version="15",
        supports_break_system_packages=False,
        has_pipx=True,
    ),
    "18.0": Profile(
        odoo_version="18.0",
        base_distro="ubuntu-noble",
        pip_version="24.0",
        default_db_version="16",
        supports_break_system_packages=True,
        has_pipx=True,
    ),
    "19.0": Profile(
        odoo_version="19.0",
        base_distro="ubuntu-noble",
        pip_version="24.0",
        default_db_version="16",
        supports_break_system_packages=True,
        has_pipx=True,
    ),
}


def get_profile(odoo_version: str) -> Profile:
    """The profile for an Odoo version.

    Raises ValueError for an unsupported version rather than guessing: the
    generated Dockerfile depends on getting the base image right.
    """
    try:
        return PROFILES[odoo_version]
    except KeyError:
        supported = ", ".join(SUPPORTED_ODOO_VERSIONS)
        raise ValueError(f"Unsupported Odoo version {odoo_version!r}. Supported: {supported}") from None


class ProjectPorts(BaseModel):
    model_config = ConfigDict(extra="allow")

    odoo: int = 8069
    vscode: int = 8888

    @field_validator("odoo", "vscode")
    @classmethod
    def _unprivileged(cls, value: int) -> int:
        if not 1024 <= value <= 65535:
            raise ValueError(f"Port must be between 1024 and 65535, got {value}")
        return value


class ProjectConfig(BaseModel):
    """A generated Rocketdoo project, as read back from disk."""

    model_config = ConfigDict(extra="allow")

    project_name: str
    odoo_version: str = "17.0"
    odoo_edition: Edition = "Community"
    db_version: str = "16"

    odoo_container: str = ""
    db_container: str = ""
    ports: ProjectPorts = Field(default_factory=ProjectPorts)

    uses_ssh: bool = False
    ssh_key_name: str = ""
    third_party_repos: list[str] = Field(default_factory=list)
    restart: str = "unless-stopped"

    @field_validator("project_name")
    @classmethod
    def _docker_requires_lowercase(cls, value: str) -> str:
        """Docker Compose rejects uppercase project names."""
        if value != value.lower():
            raise ValueError(f"Project name must be lowercase, got {value!r}")
        return value

    @property
    def profile(self) -> Profile:
        return get_profile(self.odoo_version)

    def model_post_init(self, _context) -> None:
        if not self.odoo_container:
            self.odoo_container = f"odoo-{self.project_name}"
        if not self.db_container:
            self.db_container = f"db-{self.project_name}"
