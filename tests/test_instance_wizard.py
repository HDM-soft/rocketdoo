"""The `rkd instance init` wizard: one type per file, and re-running it adds instead of replacing."""

import pytest
import yaml
from click.testing import CliRunner

from rocketdoo.core.instance import config_manager
from rocketdoo.core.instance.config_manager import InstanceConfigManager, merge_environments
from rocketdoo.instance_cli import instance


class Script:
    """Answers each prompt from a table keyed by a fragment of its text and records what was asked."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def answer(self, text, default):
        self.asked.append(text)
        for fragment, value in self.answers.items():
            if fragment in text:
                return value
        return default

    def asked_about(self, fragment):
        return any(fragment in text for text in self.asked)


class FakeSelect:
    def __init__(self, script, text, **_):
        self.value = script.answer(text, None)

    def ask(self):
        return self.value


@pytest.fixture(autouse=True)
def local_databases(monkeypatch):
    found = {"result": (["dev_db", "other_db"], "")}
    monkeypatch.setattr(config_manager, "databases_result", lambda compose: found["result"])
    return found


@pytest.fixture
def wizard(tmp_path, monkeypatch):
    def run(answers, fresh=False, existing=None):
        script = Script(answers)
        if existing is not None:
            path = tmp_path / ".rkd" / "instance.yaml"
            path.parent.mkdir(exist_ok=True)
            path.write_text(yaml.safe_dump(existing))
        monkeypatch.setattr(config_manager.questionary, "select", lambda text, **kw: FakeSelect(script, text, **kw))
        monkeypatch.setattr(config_manager.Prompt, "ask", lambda text, default=None, **kw: script.answer(text, default))
        monkeypatch.setattr(config_manager.Confirm, "ask", lambda text, default=False, **kw: script.answer(text, default))
        mgr = InstanceConfigManager(tmp_path)
        return mgr, script, mgr.interactive_setup(fresh=fresh)

    return run


BASE = {
    "Deployment type": "docker",
    "VPS hostname": "stage.example.com",
    "Authentication method": "password",
    "Domain": "stage.example.com",
    "Email": "ops@example.com",
    "Resource profile": "small",
}

PROD = {
    "type": "docker",
    "vps": {"host": "prod.example.com", "port": 22, "user": "ubuntu", "auth_method": "password", "password": "${P}"},
    "domain": "erp.example.com",
    "db_user": "odoo_prod",
}


def test_merge_environments_adds_and_replaces_without_touching_the_rest():
    existing = {"environments": {"stage": {"a": 1}, "prod": {"b": 2}}}
    merged = merge_environments(existing, {"stage": {"a": 9}, "extra": {"c": 3}})
    assert merged == {"environments": {"stage": {"a": 9}, "prod": {"b": 2}, "extra": {"c": 3}}}
    assert existing == {"environments": {"stage": {"a": 1}, "prod": {"b": 2}}}
    assert merged["environments"]["prod"] is not existing["environments"]["prod"]


def test_merge_environments_on_an_empty_base():
    assert merge_environments({}, {"prod": {"x": 1}}) == {"environments": {"prod": {"x": 1}}}


def test_configuring_only_stage_leaves_prod_identical(wizard, tmp_path):
    (tmp_path / ".rkd").mkdir()
    (tmp_path / ".rkd" / "instance.yaml").write_text(yaml.safe_dump({"environments": {"prod": PROD}}))
    prod_before = InstanceConfigManager(tmp_path).load()["environments"]["prod"]

    mgr, _, config = wizard({**BASE, "Reconfigure prod": False})

    assert config["environments"]["prod"] == prod_before
    assert mgr.load()["environments"]["prod"] == prod_before
    assert mgr.load()["environments"]["stage"]["vps"]["host"] == "stage.example.com"


def test_type_is_not_asked_when_the_file_already_has_environments(wizard):
    _, script, config = wizard({**BASE, "Reconfigure prod": False}, existing={"environments": {"prod": PROD}})

    assert not script.asked_about("Deployment type")
    assert config["environments"]["stage"]["type"] == "docker"


def test_type_is_asked_once_for_a_new_file_and_applies_to_every_environment(wizard):
    _, script, config = wizard({**BASE, "Deployment type": "native"})

    assert sum("Deployment type" in text for text in script.asked) == 1
    assert {env["type"] for env in config["environments"].values()} == {"native"}


def test_native_is_not_asked_what_it_does_not_use(wizard):
    _, script, config = wizard({**BASE, "Deployment type": "native"})

    for fragment in ("Domain", "Email", "PostgreSQL version", "Docker image tag", "Resource profile"):
        assert not script.asked_about(fragment)
    stage = config["environments"]["stage"]
    assert "domain" not in stage and "pg_profile" not in stage
    assert stage["remote_path"] == "/opt/odoo/extra-addons"
    assert script.asked_about("Remote addons path")


def test_native_is_asked_about_gitman_and_saves_it(wizard):
    _, script, config = wizard({**BASE, "Deployment type": "native", "Use Gitman": True})

    assert script.asked_about("Gitman config file")
    stage = config["environments"]["stage"]
    assert stage["use_gitman"] is True and stage["gitman_config"] == "gitman.yaml"
    assert "pg_profile" not in stage and "db_version" not in stage


def test_docker_is_asked_the_full_set(wizard):
    _, script, _ = wizard({**BASE})

    for fragment in ("Domain", "Email", "PostgreSQL version", "Gitman", "Resource profile", "Remote base path"):
        assert script.asked_about(fragment)


def test_enterprise_source_is_saved(wizard):
    mgr, _, config = wizard({**BASE, "Use Odoo Enterprise": True, "How should Enterprise": "clone"})

    assert config["environments"]["stage"]["enterprise_source"] == "clone"
    assert mgr.load()["environments"]["stage"]["enterprise_source"] == "clone"


@pytest.mark.parametrize("has_local, expected", [(True, "rsync"), (False, "clone")])
def test_enterprise_source_default_follows_the_local_directory(tmp_path, monkeypatch, has_local, expected):
    if has_local:
        (tmp_path / "enterprise").mkdir()
    seen = {}

    def select(text, **kw):
        seen[text] = kw.get("default")
        return FakeSelect(Script({}), text)

    monkeypatch.setattr(config_manager.questionary, "select", select)
    monkeypatch.setattr(config_manager.Prompt, "ask", lambda text, default=None, **kw: default or "x")
    monkeypatch.setattr(config_manager.Confirm, "ask", lambda text, default=False, **kw: "Enterprise" in text)
    InstanceConfigManager(tmp_path)._wizard_env("stage", "native")

    assert seen["How should Enterprise reach the server?"] == expected


def test_gitman_default_is_the_file_rkd_init_writes(wizard):
    _, _, config = wizard({**BASE, "Use Gitman": True})

    assert config["environments"]["stage"]["gitman_config"] == "gitman.yaml"


def test_choosing_nothing_on_an_existing_file_does_not_rewrite_it(wizard, tmp_path):
    existing = {"environments": {"prod": PROD}}
    mgr, _, config = wizard({"Reconfigure prod": False, "Configure a stage": False}, existing=existing)
    content = mgr.config_path.read_text()
    mtime = mgr.config_path.stat().st_mtime_ns

    mgr.interactive_setup()

    assert mgr.config_path.read_text() == content
    assert mgr.config_path.stat().st_mtime_ns == mtime
    assert list(config["environments"]) == ["prod"]


def test_choosing_nothing_without_a_file_cancels(wizard):
    with pytest.raises(SystemExit):
        wizard({"Configure a stage": False, "Configure a prod": False})


def test_fresh_ignores_the_existing_file(wizard):
    mgr, script, config = wizard({**BASE, "Configure a prod": False}, fresh=True, existing={"environments": {"prod": PROD}})

    assert script.asked_about("Deployment type")
    assert list(config["environments"]) == ["stage"]
    assert list(mgr.load()["environments"]) == ["stage"]


NATIVE_PROD = {
    **PROD,
    "type": "native",
    "domain": "",
    "proxy_mode": True,
    "remote_path": "/srv/addons",
}


def test_a_cancelled_select_cannot_write_an_unreadable_file(wizard, tmp_path):
    mgr = InstanceConfigManager(tmp_path)
    (tmp_path / ".rkd").mkdir()
    mgr.config_path.write_text(yaml.safe_dump({"environments": {"prod": PROD}}))
    content = mgr.config_path.read_text()

    with pytest.raises(ValueError):
        wizard({**BASE, "Authentication method": None}, existing={"environments": {"prod": PROD}})

    assert mgr.config_path.read_text() == content


def test_save_refuses_what_the_model_cannot_read_back(tmp_path):
    mgr = InstanceConfigManager(tmp_path)

    with pytest.raises(ValueError):
        mgr.save({"environments": {"stage": {"type": None}}})

    assert not mgr.exists()


def test_mixed_types_are_named_and_each_environment_keeps_its_own(wizard, capsys):
    existing = {"environments": {"stage": {**PROD, "domain": "s.example.com"}, "prod": NATIVE_PROD}}
    mgr, script, config = wizard({**BASE, "Reconfigure stage": False, "Reconfigure prod": True}, existing=existing)

    out = capsys.readouterr().out
    assert "stage: docker" in out and "prod: native" in out and "not supported" in out
    prod = mgr.load()["environments"]["prod"]
    assert prod["type"] == "native"
    assert prod["proxy_mode"] is True
    assert mgr.load()["environments"]["stage"]["type"] == "docker"
    assert not script.asked_about("Deployment type")


def test_reconfiguring_keeps_keys_the_wizard_does_not_ask(wizard):
    mgr, _, _ = wizard(
        {**BASE, "Reconfigure prod": True, "Configure a stage": False}, existing={"environments": {"prod": NATIVE_PROD}}
    )

    assert mgr.load()["environments"]["prod"]["proxy_mode"] is True


def test_wizard_defaults_come_from_the_project(wizard, monkeypatch):
    class Info:
        odoo_version = "19.0"
        db_version = "15"

    monkeypatch.setattr(config_manager, "get_project_info", lambda root: Info())
    _, _, config = wizard({**BASE})

    stage = config["environments"]["stage"]
    assert stage["odoo_version"] == "19.0"
    assert stage["db_version"] == "15"


@pytest.mark.parametrize("kind, expected", [("native", "/opt/odoo/extra-addons"), ("docker", "/opt/odoo-prod")])
def test_an_empty_remote_path_is_derived_from_the_type(tmp_path, kind, expected):
    (tmp_path / ".rkd").mkdir()
    env = {"type": kind, "vps": {"host": "h", "auth_method": "password", "password": "${P}"}}
    (tmp_path / ".rkd" / "instance.yaml").write_text(yaml.safe_dump({"environments": {"prod": env}}))

    assert InstanceConfigManager(tmp_path).load()["environments"]["prod"]["remote_path"] == expected


def _init(tmp_path, monkeypatch, *args):
    (tmp_path / "Dockerfile").write_text("FROM odoo:19.0\n")
    (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_manager.questionary, "select", lambda text, **kw: FakeSelect(Script(BASE), text))
    monkeypatch.setattr(config_manager.Prompt, "ask", lambda text, default=None, **kw: Script(BASE).answer(text, default))
    monkeypatch.setattr(
        config_manager.Confirm, "ask", lambda text, default=False, **kw: "Configure a prod" not in text and default
    )
    return CliRunner().invoke(instance, ["init", *args])


def test_panel_reports_added_kept_and_updated(tmp_path, monkeypatch):
    (tmp_path / ".rkd").mkdir()
    (tmp_path / ".rkd" / "instance.yaml").write_text(yaml.safe_dump({"environments": {"prod": PROD}}))

    result = _init(tmp_path, monkeypatch)

    assert "stage  added" in result.output
    assert "prod  kept" in result.output


def test_force_starts_over_and_panel_reports_removed(tmp_path, monkeypatch):
    (tmp_path / ".rkd").mkdir()
    (tmp_path / ".rkd" / "instance.yaml").write_text(yaml.safe_dump({"environments": {"prod": PROD}}))

    result = _init(tmp_path, monkeypatch, "--force")

    assert "stage  added" in result.output
    assert "prod  removed" in result.output
    assert list(InstanceConfigManager(tmp_path).load()["environments"]) == ["stage"]


def test_a_syntactically_broken_file_is_reported_not_a_traceback(tmp_path, monkeypatch):
    (tmp_path / ".rkd").mkdir()
    (tmp_path / ".rkd" / "instance.yaml").write_text("environments: [unclosed")

    result = _init(tmp_path, monkeypatch)

    assert result.exit_code == 1
    assert "Cannot read" in result.output


class TestSeedDatabase:
    def test_none_is_the_default_and_the_local_databases_are_offered(self, monkeypatch, tmp_path):
        offered = {}

        def select(text, **kw):
            if "Database for" in text:
                offered["values"] = [c.value for c in kw["choices"]]
                offered["default"] = kw["default"].value
            return FakeSelect(Script(BASE), text)

        monkeypatch.setattr(config_manager.questionary, "select", select)
        monkeypatch.setattr(config_manager.Prompt, "ask", lambda text, default=None, **kw: Script(BASE).answer(text, default))
        monkeypatch.setattr(config_manager.Confirm, "ask", lambda text, default=False, **kw: default)
        InstanceConfigManager(tmp_path).interactive_setup()

        assert offered["values"] == ["", "dev_db", "other_db"]
        assert offered["default"] == ""

    @pytest.mark.parametrize("kind", ["docker", "native"])
    def test_the_chosen_database_is_saved_for_both_types(self, wizard, kind):
        _, _, config = wizard({**BASE, "Deployment type": kind, "Database for": "dev_db"})

        assert config["environments"]["stage"]["seed_database"] == "dev_db"

    def test_without_a_choice_the_field_is_empty(self, wizard):
        _, _, config = wizard({**BASE})

        assert config["environments"]["stage"]["seed_database"] == ""

    def test_without_a_database_container_it_says_why_and_does_not_ask(self, wizard, local_databases, capsys):
        local_databases["result"] = ([], "no database container configured")

        _, script, config = wizard({**BASE})

        assert not script.asked_about("Database for")
        assert config["environments"]["stage"]["seed_database"] == ""
        assert "no database container configured" in capsys.readouterr().out


def test_instance_yaml_without_the_field_still_loads_and_the_field_round_trips(tmp_path):
    from rocketdoo.core.models import InstanceConfig

    old = {"environments": {"prod": {"type": "docker", "vps": {"host": "h"}, "domain": "d.example.com"}}}
    assert InstanceConfig(**old).environments["prod"].seed_database == ""

    mgr = InstanceConfigManager(tmp_path)
    old["environments"]["prod"]["seed_database"] = "dev_db"
    mgr.save(old)
    assert mgr.load()["environments"]["prod"]["seed_database"] == "dev_db"
