"""End-to-end tests: scaffold and init produce a project Docker can parse.

`rkd init` is an interactive wizard with no non-interactive mode, so the test
drives it by answering its prompts by name. That keeps the whole real code path
under test — template rendering, port validation, .gitignore, odoo.conf — and
the resulting docker-compose.yaml is handed to `docker compose config`, which
is the only authority on whether Docker would actually accept it.
"""

import shutil
import subprocess

import pytest
import yaml

from rocketdoo.core.gitignore_manager import SENSITIVE_ENTRIES
from rocketdoo.scaffold import scaffold_project

# Answers keyed by a distinctive fragment of each prompt (init_project.py:127+).
WIZARD_ANSWERS = {
    "Project Name": "demo-project",
    "Odoo Version": "17.0",
    "Odoo Edition": "Community",
    "Use private repositories": False,
    "Use third-party repositories": False,
    "PostgreSQL version": "15",
    "Odoo master password": "test-admin-pw",
    "restart the environment": "unless-stopped",
    "Odoo Port": "8069",
    "VSC Debug Port": "8888",
}


class _Answer:
    """Stands in for a questionary prompt object."""

    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


def _lookup(message):
    for key, value in WIZARD_ANSWERS.items():
        if key.lower() in str(message).lower():
            return value
    raise AssertionError(f"Unscripted prompt: {message!r}")


@pytest.fixture
def scripted_wizard(monkeypatch):
    """Answer every prompt `rkd init` asks, by matching the prompt text."""
    import click
    import questionary

    monkeypatch.setattr(questionary, "select", lambda message, **kw: _Answer(_lookup(message)))
    monkeypatch.setattr(questionary, "confirm", lambda message, **kw: _Answer(_lookup(message)))
    monkeypatch.setattr(click, "prompt", lambda message, **kw: _lookup(message))
    monkeypatch.setattr(click, "confirm", lambda message, **kw: True)
    # show_welcome() blocks on a bare input() for "Press ENTER to start"
    monkeypatch.setattr("builtins.input", lambda *a: "")
    # Keep the port scan inside the tmpdir instead of walking the real home
    monkeypatch.setattr("rocketdoo.init_project.collect_declared_ports", lambda *a, **kw: {})
    monkeypatch.setattr("rocketdoo.core.port_validation.is_port_in_use", lambda p: False)
    monkeypatch.setattr("rocketdoo.init_project.is_port_in_use", lambda p: False, raising=False)


def _docker_compose_config(path):
    """Run `docker compose config`, the real parser, over a generated project."""
    return subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yaml", "config"],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=90,
    )


class TestScaffold:
    def test_creates_the_expected_layout(self, project_dir):
        scaffold_project()
        for expected in ("Dockerfile.jinja", "docker-compose.yaml.jinja", "config", "addons"):
            assert (project_dir / expected).exists(), f"missing {expected}"

    def test_writes_a_gitignore_covering_every_secret(self, project_dir):
        scaffold_project()
        entries = {
            ln.strip() for ln in (project_dir / ".gitignore").read_text().splitlines() if ln.strip() and not ln.startswith("#")
        }
        assert {pat for pat, _ in SENSITIVE_ENTRIES} <= entries

    def test_does_not_ship_the_gitignore_template_itself(self, project_dir):
        """.gitignore.jinja is rendered to .gitignore, never copied verbatim."""
        scaffold_project()
        assert not (project_dir / ".gitignore.jinja").exists()

    def test_preserves_an_existing_gitignore(self, project_dir):
        (project_dir / ".gitignore").write_text("# mine\nmy-own-rule/\n")
        scaffold_project()
        content = (project_dir / ".gitignore").read_text()
        assert "my-own-rule/" in content
        assert ".rkd/secrets/" in content

    def test_is_idempotent(self, project_dir):
        scaffold_project()
        scaffold_project()
        assert (project_dir / "Dockerfile.jinja").exists()


class TestInit:
    @pytest.fixture
    def initialised(self, project_dir, scripted_wizard):
        from rocketdoo.init_project import init_project

        scaffold_project()
        init_project()
        return project_dir

    def test_generates_the_core_files(self, initialised):
        for expected in ("Dockerfile", "docker-compose.yaml", "config/odoo.conf"):
            assert (initialised / expected).exists(), f"missing {expected}"

    def test_compose_is_valid_yaml_with_both_services(self, initialised):
        compose = yaml.safe_load((initialised / "docker-compose.yaml").read_text())
        assert {"web", "db"} <= set(compose["services"])

    def test_answers_reached_the_generated_files(self, initialised):
        assert "FROM odoo:17.0" in (initialised / "Dockerfile").read_text()
        assert "test-admin-pw" in (initialised / "config" / "odoo.conf").read_text()

    def test_container_names_follow_the_project_name(self, initialised):
        compose = yaml.safe_load((initialised / "docker-compose.yaml").read_text())
        names = {s.get("container_name") for s in compose["services"].values()}
        assert "odoo-demo-project" in names

    def test_secrets_are_gitignored(self, initialised):
        """The generated odoo.conf holds admin_passwd; git must not see it."""
        ignored = (initialised / ".gitignore").read_text()
        assert "config/odoo.conf" in ignored

    @pytest.mark.docker
    @pytest.mark.slow
    def test_docker_compose_accepts_the_generated_file(self, initialised, requires_docker):
        """`docker compose config` is the only authority on compose validity."""
        result = _docker_compose_config(initialised)
        assert result.returncode == 0, result.stderr

    @pytest.mark.docker
    @pytest.mark.slow
    def test_docker_resolves_the_published_ports(self, initialised, requires_docker):
        result = _docker_compose_config(initialised)
        assert result.returncode == 0, result.stderr
        resolved = yaml.safe_load(result.stdout)
        published = {int(p["published"]) for svc in resolved["services"].values() for p in svc.get("ports", [])}
        assert {8069, 8888} <= published


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker binary not installed")
def test_scaffolded_templates_are_not_valid_compose_before_init(project_dir):
    """Scaffold ships .jinja sources; docker-compose.yaml only exists after init."""
    scaffold_project()
    assert not (project_dir / "docker-compose.yaml").exists()
