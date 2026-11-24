"""Per-environment Odoo tuning: the performance block of a deployed odoo.conf (#332).

Before this, `workers` and the memory limits were constants of
`core/instance/config_manager.py` and the time limits were literals in the
templates, so adapting an instance to a particular VPS meant editing rkd's
source. Editing the conf on the server is not an alternative either: both
deployers rewrite it on every deploy, with no "leave it alone if it exists"
guard, so a hand-made adjustment is overwritten in silence.

The case that motivated it: taking a customer's database to a different VPS,
where their conf raised `limit_time_cpu`/`limit_time_real` to 600/1200 for long
imports and heavy reports — values rkd replaced with 60/120 on the Docker path
and left at Odoo's own defaults on the native one.
"""

import pytest
import yaml

from rocketdoo.core.instance import preflight
from rocketdoo.core.instance.config_manager import (
    MEMORY_BY_ENV,
    RESOURCE_KEYS,
    TIME_LIMITS,
    WORKERS_BY_ENV,
    InstanceConfigManager,
    resources_for,
)
from rocketdoo.core.models import InstanceResources


class TestResourcesFor:
    def test_no_resources_gives_what_the_environment_name_implied(self):
        for env in ("stage", "prod"):
            values = resources_for(env, {})

            assert values["workers"] == WORKERS_BY_ENV[env]
            assert values["limit_memory_soft"] == MEMORY_BY_ENV[env]["soft"]
            assert values["limit_memory_hard"] == MEMORY_BY_ENV[env]["hard"]
            assert {k: values[k] for k in TIME_LIMITS} == TIME_LIMITS

    def test_it_answers_every_key_the_conf_needs(self):
        # A missing key renders as an empty Jinja variable, which Odoo rejects.
        assert set(resources_for("prod", {})) == set(RESOURCE_KEYS)

    def test_a_partial_override_only_changes_what_it_names(self):
        values = resources_for("prod", {"resources": {"limit_time_real": 1200}})

        assert values["limit_time_real"] == 1200
        assert values["limit_time_cpu"] == TIME_LIMITS["limit_time_cpu"]
        assert values["workers"] == WORKERS_BY_ENV["prod"]

    def test_an_explicit_none_is_not_an_override(self):
        # The model writes absent fields as null, and null means "the default".
        values = resources_for("prod", {"resources": {"workers": None}})

        assert values["workers"] == WORKERS_BY_ENV["prod"]

    def test_an_unknown_environment_name_falls_back_to_stage(self):
        assert resources_for("qa", {})["workers"] == WORKERS_BY_ENV["stage"]

    def test_zero_is_an_override_and_not_a_missing_value(self):
        # workers = 0 is Odoo's single-process mode, a legitimate answer.
        assert resources_for("prod", {"resources": {"workers": 0}})["workers"] == 0


class TestTheModelRoundTrip:
    def test_resources_survive_a_write_and_a_read(self, tmp_path):
        manager = InstanceConfigManager(tmp_path)
        manager.save(
            {
                "environments": {
                    "prod": {
                        "type": "native",
                        "vps": {"host": "h", "user": "u"},
                        "resources": {"workers": 3, "limit_time_real": 1200},
                    }
                }
            }
        )

        back = manager.load()["environments"]["prod"]["resources"]

        assert back["workers"] == 3
        assert back["limit_time_real"] == 1200
        # The keys not given come back as null, which resources_for reads as
        # "use the default" — not as zero.
        assert back["limit_time_cpu"] is None

    def test_an_environment_without_resources_still_loads(self, tmp_path):
        manager = InstanceConfigManager(tmp_path)
        manager.save({"environments": {"prod": {"type": "native", "vps": {"host": "h", "user": "u"}}}})

        assert manager.load()["environments"]["prod"]["resources"] == dict.fromkeys(RESOURCE_KEYS)

    def test_a_misspelled_key_is_rejected(self):
        # extra="forbid" inside resources too: a typo must fail loudly rather
        # than silently deploying the default.
        with pytest.raises(Exception, match="worker_count|extra"):
            InstanceResources.model_validate({"worker_count": 3})

    def test_it_is_written_to_the_yaml(self, tmp_path):
        manager = InstanceConfigManager(tmp_path)
        manager.save({"environments": {"prod": {"vps": {"host": "h", "user": "u"}, "resources": {"workers": 4}}}})

        raw = yaml.safe_load((tmp_path / ".rkd" / "instance.yaml").read_text())

        assert raw["environments"]["prod"]["resources"]["workers"] == 4


class TestBothPathsEmitTheSameSeven:
    """The Docker template fixed its own time limits and the native conf
    declared none, so the two generators of the same file disagreed."""

    CUSTOM = {"workers": 3, "limit_time_cpu": 600, "limit_time_real": 1200, "limit_request": 16384}

    def test_the_native_conf_carries_them(self, tmp_path, monkeypatch):
        from rocketdoo.core.instance import deployer_native

        cfg = {
            "type": "native",
            "vps": {"host": "h", "user": "u", "auth_method": "ssh_key", "ssh_key": "~/.ssh/k"},
            "db_user": "odoo_prod",
            "db_name": "odoo_prod",
            "remote_path": "/opt/odoo/extra-addons",
            "resources": self.CUSTOM,
        }
        deployer = deployer_native.NativeInstanceDeployer("prod", cfg, tmp_path)
        captured = {}
        monkeypatch.setattr(deployer, "_ssh", lambda command, **kw: captured.update(conf=kw.get("input")) or True)

        deployer._configure_odoo()

        for key, value in self.CUSTOM.items():
            assert f"{key} = {value}\n" in captured["conf"]

    def test_the_docker_conf_carries_them(self):
        from jinja2 import Environment, FileSystemLoader

        from rocketdoo.core.instance.config_manager import resources_for as resolve

        env = Environment(loader=FileSystemLoader("rocketdoo/templates"), keep_trailing_newline=True)
        ctx = {
            "addons_path": "/mnt/extra-addons",
            "admin_passwd": "x",
            "db_user": "u",
            "db_password": "p",
            "db_name": "d",
            "db_maxconn": 11,
            "log_level": "warn",
            **resolve("prod", {"resources": self.CUSTOM}),
        }

        conf = env.get_template("instance/odoo.conf.jinja").render(**ctx)

        for key, value in self.CUSTOM.items():
            assert f"{key} = {value}\n" in conf


class TestThePreflightChecksWhatGetsDeployed:
    def test_a_configuration_that_does_not_fit_is_caught(self):
        """The shape measured on a real customer conf: workers from the CPU
        formula, memory left at Odoo's own 2 GiB default."""
        cfg = {
            "type": "native",
            "vps": {"host": "h", "user": "u"},
            "db_user": "o",
            "db_name": "o",
            "resources": {"workers": 5, "limit_memory_soft": 2147483648},
        }
        local = {"invalid_identifiers": [], "native_host_clash": []}
        remote = {"reachable": "yes", "mem_kb": str(4 * 1024 * 1024), "disk_kb": "99999999"}

        checks = {c["name"]: c for c in preflight.evaluate(cfg, local, remote, env="prod")}

        assert checks["memory"]["ok"] is False
        assert "16512 MiB" in checks["memory"]["detail"]
        assert "resources" in checks["memory"]["hint"]

    def test_the_same_server_holds_a_sized_down_configuration(self):
        cfg = {
            "type": "native",
            "vps": {"host": "h", "user": "u"},
            "db_user": "o",
            "db_name": "o",
            "resources": {"workers": 2, "limit_memory_soft": 536870912},
        }
        local = {"invalid_identifiers": [], "native_host_clash": []}
        remote = {"reachable": "yes", "mem_kb": str(4 * 1024 * 1024), "disk_kb": "99999999"}

        checks = {c["name"]: c for c in preflight.evaluate(cfg, local, remote, env="prod")}

        assert checks["memory"]["ok"] is True
