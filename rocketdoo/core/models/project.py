"""Domain models for a local Rocketdoo project.

`ProjectConfig` describes what project_info.py reconstructs today by scraping
the generated Dockerfile and docker-compose.yaml. `Profile` captures what is
fixed per Odoo version, so the golden paths in #139 have something to build on
rather than rediscovering it.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Edition = Literal["Community", "Enterprise"]

SUPPORTED_ODOO_VERSIONS = ("15.0", "16.0", "17.0", "18.0", "19.0", "20.0")


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
    "20.0": Profile(
        odoo_version="20.0",
        base_distro="ubuntu-noble",
        pip_version="24.0",
        default_db_version="17",
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


class ProjectConfig(BaseModel):
    """A generated Rocketdoo project, as read back from disk.

    The field names are the contract, not a design choice made here: the GUI
    serialises this straight to JSON and the SPA reads `odoo_port`, `ssh_key`,
    `admin_passwd` and the rest by those names. Renaming one is a silent
    break of a panel no test of this module would notice (#204).

    Everything optional and nothing rejected. The source is a Dockerfile and a
    docker-compose.yaml the user can edit by hand, and a project rkd can still
    describe must not become one rkd refuses to read: `rkd info`, the GUI
    dashboard and `rkd pack` all call this on whatever is in the directory.
    The rules about what a *valid* project looks like live where the project
    is written -- `init_project.py` lowercases the name and says so, and
    `core/port_validation.py` owns the port range.

    Ports are strings because that is what the compose file says and what the
    panel prints; parsing them to int here would change the JSON the SPA
    already consumes for no gain inside this module.
    """

    model_config = ConfigDict(extra="forbid")

    project_name: str | None = None
    odoo_version: str | None = None
    odoo_edition: Edition = "Community"
    db_version: str | None = None

    odoo_container: str | None = None
    db_container: str | None = None
    odoo_port: str | None = None
    vsc_port: str | None = None
    db_port: str | None = None

    restart_policy: str | None = None
    admin_passwd: str | None = None

    use_private_repos: bool = False
    ssh_key: str | None = None
    use_third_party_repos: bool = False
    # The values are not typed on purpose: these come straight from the
    # user's gitman.yaml, and YAML reads `rev: 16.0` as a float, `rev: 17`
    # as an int and an empty `repo:` as None (measured). The Odoo branch
    # people actually write is `16.0`, so a stricter type here would
    # reject an ordinary file that rkd reads fine today.
    third_party_repos: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def profile(self) -> Profile | None:
        """The facts fixed by this project's Odoo version, or None.

        None rather than an exception, for the same reason the fields are
        optional: the version is scraped from a file the user can edit, and
        an unreadable version is not a reason to stop describing the project.
        """
        try:
            return get_profile(self.odoo_version)
        except ValueError:
            return None
