"""Domain models for `.rkd/instance.yaml`.

The CLI wizard and the GUI wrote this file with two different shapes. The
wizard nested the connection under `vps:`; the GUI wrote those fields flat and
renamed two of them. The deployers read the nested shape, so anything the GUI
saved failed with `KeyError: 'vps'` at deploy time.

These models are the single definition of the file. Parsing accepts both
shapes so existing projects keep working, and writing always produces the
canonical nested one.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AuthMethod = Literal["ssh_key", "password"]
DeploymentType = Literal["docker", "native"]
PGProfileName = Literal["small", "medium", "large"]

# Flat keys the GUI used, mapped onto the canonical names.
_LEGACY_ALIASES = {
    "password_ref": "password",
    "email": "traefik_email",
}

# Keys that belong to the connection, wherever they were written.
_VPS_KEYS = ("host", "port", "user", "auth_method", "ssh_key", "password")


class VPSConnection(BaseModel):
    """How to reach the server hosting an environment."""

    model_config = ConfigDict(extra="forbid")

    host: str = ""
    port: int = 22
    user: str = "ubuntu"
    auth_method: AuthMethod = "ssh_key"
    ssh_key: str = ""
    # Literal, or a "${VAR}" reference resolved at deploy time.
    password: str = ""

    @field_validator("port")
    @classmethod
    def _port_in_range(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError(f"SSH port must be between 1 and 65535, got {value}")
        return value

    @model_validator(mode="after")
    def _credential_matches_method(self) -> "VPSConnection":
        """A key-based connection needs a key; a password one needs a password.

        Empty is allowed while a config is still being filled in — `is_complete`
        is what the deployer checks before connecting.
        """
        if self.auth_method == "ssh_key" and self.password:
            raise ValueError("auth_method is 'ssh_key' but a password was given")
        if self.auth_method == "password" and self.ssh_key:
            raise ValueError("auth_method is 'password' but an ssh_key was given")
        return self

    @property
    def is_complete(self) -> bool:
        """Whether this connection carries enough detail to attempt a deploy."""
        if not self.host:
            return False
        return bool(self.ssh_key) if self.auth_method == "ssh_key" else bool(self.password)


class InstanceEnvironment(BaseModel):
    """One deployment target — typically `stage` or `prod`."""

    model_config = ConfigDict(extra="forbid")

    type: DeploymentType = "docker"
    vps: VPSConnection = Field(default_factory=VPSConnection)

    odoo_version: str = "17.0"
    odoo_tag: str = ""
    domain: str = ""
    traefik_email: str = ""

    db_version: str = "16"
    # Empty means "derive from the environment name", which InstanceConfig
    # does below. A literal default here would be a second answer to the
    # same question: the wizard asks for `odoo_{env}`, and two
    # environments sharing one role also share one database (#248).
    db_user: str = ""
    # The database Odoo serves. Empty means "same as db_user", filled in below.
    db_name: str = ""
    admin_passwd: str = ""

    # Only meaningful with a reverse proxy in front; a native deploy has
    # none today, and trusting X-Forwarded-* without one lets any caller
    # spoof its address.
    proxy_mode: bool = False

    use_enterprise: bool = False
    use_gitman: bool = False
    gitman_config: str = ""

    pg_profile: PGProfileName = "small"
    remote_path: str = ""

    @model_validator(mode="before")
    @classmethod
    def _accept_flat_and_nested(cls, data: Any) -> Any:
        """Normalise the GUI's flat shape into the canonical nested one.

        Runs before field validation, so both of these parse identically:

            {"vps": {"host": "h"}, "odoo_version": "17.0"}   # wizard
            {"host": "h", "odoo_version": "17.0"}            # GUI
        """
        if not isinstance(data, dict):
            return data

        normalised = dict(data)

        for legacy, canonical in _LEGACY_ALIASES.items():
            if legacy in normalised:
                value = normalised.pop(legacy)
                normalised.setdefault(canonical, value)

        vps = dict(normalised.get("vps") or {})
        for key in _VPS_KEYS:
            if key in normalised:
                # An explicit vps: block wins over a stray flat key.
                vps.setdefault(key, normalised.pop(key))
            else:
                normalised.pop(key, None)
        normalised["vps"] = vps

        return normalised

    @model_validator(mode="after")
    def _fill_derived_defaults(self) -> "InstanceEnvironment":
        if not self.odoo_tag:
            self.odoo_tag = self.odoo_version
        if not self.db_name:
            self.db_name = self.db_user
        return self

    @property
    def is_deployable(self) -> bool:
        """Whether a deploy could be attempted with what is configured."""
        return self.vps.is_complete and bool(self.domain)

    def missing_fields(self) -> list[str]:
        """Which required values are still empty, for actionable errors."""
        missing = []
        if not self.vps.host:
            missing.append("vps.host")
        if self.vps.auth_method == "ssh_key" and not self.vps.ssh_key:
            missing.append("vps.ssh_key")
        if self.vps.auth_method == "password" and not self.vps.password:
            missing.append("vps.password")
        if not self.domain:
            missing.append("domain")
        return missing


class InstanceConfig(BaseModel):
    """The whole `.rkd/instance.yaml`."""

    model_config = ConfigDict(extra="forbid")

    environments: dict[str, InstanceEnvironment] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_environment_map(cls, data: Any) -> Any:
        """Accept a file that is just `{stage: {...}}`, without the wrapper key.

        The GUI's own reader assumed this shape, so a file written that way
        must not be lost.
        """
        if not isinstance(data, dict):
            return data
        if "environments" in data:
            return cls._name_the_database_roles(data)
        if data and all(isinstance(v, dict) for v in data.values()):
            return cls._name_the_database_roles({"environments": data})
        return data

    @staticmethod
    def _name_the_database_roles(data: dict) -> dict:
        """Give each environment its own role name when the file omits one.

        Only here is the environment's name known; the environment model sees
        its own fields and nothing else.
        """
        environments = data.get("environments")
        if not isinstance(environments, dict):
            return data

        named = {}
        for name, env in environments.items():
            if isinstance(env, dict) and not env.get("db_user"):
                env = {**env, "db_user": f"odoo_{name}"}
            named[name] = env
        return {**data, "environments": named}

    def get(self, env: str) -> InstanceEnvironment | None:
        return self.environments.get(env)

    def to_yaml_dict(self) -> dict:
        """The canonical nested shape, for writing back to disk."""
        return self.model_dump(mode="json")
