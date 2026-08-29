"""Shared domain models.

One definition of each config schema, used by the CLI, the GUI/API and the
deployers alike. Before these existed the GUI and the deployers disagreed on
the shape of `.rkd/instance.yaml`, and anything the GUI wrote could not be
deployed.
"""

from rocketdoo.core.models.deploy import (
    DeployConfig,
    DeploymentResult,
    DeployTarget,
    ModulesConfig,
    OdooSHConfig,
    TargetConnection,
)
from rocketdoo.core.models.instance import (
    InstanceConfig,
    InstanceEnvironment,
    VPSConnection,
)
from rocketdoo.core.models.project import (
    PROFILES,
    SUPPORTED_ODOO_VERSIONS,
    Profile,
    ProjectConfig,
    ProjectPorts,
    get_profile,
)

__all__ = [
    "PROFILES",
    "SUPPORTED_ODOO_VERSIONS",
    "DeployConfig",
    "DeployTarget",
    "DeploymentResult",
    "InstanceConfig",
    "InstanceEnvironment",
    "ModulesConfig",
    "OdooSHConfig",
    "Profile",
    "ProjectConfig",
    "ProjectPorts",
    "TargetConnection",
    "VPSConnection",
    "get_profile",
]
