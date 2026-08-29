"""Domain models for `.rkd/deploy.yaml` and deployment outcomes.

The deploy.yaml schema was described in two places that had drifted: the
validator in core/deploy/config_manager.py and the template under
templates/deploy/. These models are the single definition, and they produce
messages that name the field rather than the rule that failed.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TargetType = Literal["vps", "odoo-sh"]
DeploymentType = Literal["docker", "native"]


class TargetConnection(BaseModel):
    """SSH details for a VPS target."""

    model_config = ConfigDict(extra="allow")

    host: str = ""
    user: str = ""
    port: int = 22
    ssh_key: str = ""
    # Literal, or a "${VAR}" reference resolved at deploy time.
    password: str = ""

    @model_validator(mode="after")
    def _one_credential_only(self) -> "TargetConnection":
        if self.ssh_key and self.password:
            raise ValueError("connection defines both ssh_key and password; use one")
        return self


class OdooSHConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_id: str = ""
    branch: str = ""


class DeployTarget(BaseModel):
    """One entry under `targets:` in deploy.yaml."""

    model_config = ConfigDict(extra="allow")

    type: TargetType
    connection: TargetConnection | None = None
    deployment_type: DeploymentType | None = None
    odoo_sh: OdooSHConfig | None = None

    def validation_errors(self, name: str) -> list[str]:
        """Human-readable problems with this target, named by field.

        Mirrors what DeployConfigManager.validate() reported, so existing
        messages keep their shape.
        """
        errors: list[str] = []
        if self.type == "vps":
            if self.connection is None:
                errors.append(f"Target '{name}': missing 'connection' section")
            else:
                for field in ("host", "user"):
                    if not getattr(self.connection, field):
                        errors.append(f"Target '{name}': missing connection.{field}")
            if self.deployment_type is None:
                errors.append(f"Target '{name}': missing 'deployment_type'")
        elif self.type == "odoo-sh":
            if self.odoo_sh is None:
                errors.append(f"Target '{name}': missing 'odoo_sh' section")
            else:
                for field in ("project_id", "branch"):
                    if not getattr(self.odoo_sh, field):
                        errors.append(f"Target '{name}': missing odoo_sh.{field}")
        return errors


class ModulesConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    auto_detect: bool = True
    base_path: str = "addons"
    exclude_patterns: list[str] = Field(default_factory=list)


class DeployConfig(BaseModel):
    """The whole deploy.yaml."""

    model_config = ConfigDict(extra="allow")

    modules: ModulesConfig | None = None
    targets: dict[str, DeployTarget] = Field(default_factory=dict)

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if self.modules is None:
            errors.append("Missing 'modules' section")
        if not self.targets:
            errors.append("No deployment targets configured")
        for name, target in self.targets.items():
            errors.extend(target.validation_errors(name))
        return errors


class DeploymentResult(BaseModel):
    """Outcome of a deployment step.

    Was a plain class in core/deploy/base.py; kept truthy and printable so the
    existing `if result:` and `print(result)` call sites behave the same.
    """

    model_config = ConfigDict(extra="allow")

    success: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)

    def __bool__(self) -> bool:
        return self.success

    def __str__(self) -> str:
        status = "✅ SUCCESS" if self.success else "❌ FAILED"
        return f"{status}: {self.message}"
