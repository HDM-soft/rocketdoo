"""The project's own requirements.txt reaching the server (#336).

A module that declares `external_dependencies` left Odoo's registry unloaded on
the server even though the build and the start finished fine:

    CRITICAL odoo.modules.module: Couldn't load module mcp_server
    ModuleNotFoundError: No module named 'authlib'
    ERROR odoo.modules.registry: Failed to load registry

rkd copies `install_dependencies.sh`, but the `scripts:` of each gitman source
run it with `cd` inside the cloned repo, so it only ever sees that repo's
requirements -- never the project's, which is where a proprietary or custom
module declares what it needs.
"""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from rocketdoo.core.instance.config_manager import REMOTE_REQUIREMENTS, REQUIREMENTS_FILE

TEMPLATES = Path(__file__).resolve().parents[1] / "rocketdoo" / "templates"


def _render(name: str, **ctx) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), keep_trailing_newline=True, undefined=StrictUndefined)
    return env.get_template(name).render(**ctx)


def _instructions(dockerfile: str) -> str:
    """The Dockerfile without its comments.

    The comments name `--break-system-packages` to explain why it is *not*
    used, so a test asserting the flag is absent has to look at what docker
    actually runs.
    """
    return "\n".join(line for line in dockerfile.splitlines() if not line.lstrip().startswith("#"))


INSTANCE_CTX = {
    "odoo_tag": "19.0",
    "use_gitman": True,
    "gitman_config_file": "gitman.yaml",
    "install_script": True,
    "ssh_hosts": ["github.com"],
    "use_enterprise": False,
    "environment": "prod",
    "needs_ssh": False,
}


class TestTheInstanceImage:
    def test_without_the_file_nothing_is_added(self):
        out = _render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=False)

        assert "requirements.txt" not in out

    def test_with_the_file_it_is_copied_and_installed(self):
        out = _render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=True)

        assert "COPY requirements.txt /opt/odoo/requirements.txt" in out
        assert "pip install --ignore-installed -r /opt/odoo/requirements.txt" in out

    def test_it_comes_after_the_gitman_layer(self):
        """Touching a dependency must not reclone the third-party repos."""
        out = _render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=True)

        assert out.index("gitman install") < out.index("COPY requirements.txt")

    def test_it_comes_before_the_user_switch(self):
        # pip needs root; after `USER odoo` the install would fail.
        out = _render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=True)

        assert out.index("COPY requirements.txt") < out.index("USER odoo")

    def test_it_removes_the_marker_instead_of_passing_the_flag(self):
        """--break-system-packages arrived in pip 23, and odoo:15.0/16.0 ship
        pip 20.3 while 17.0 ships 22.0: the flag breaks the build on three of
        the six supported series. Removing EXTERNALLY-MANAGED is a no-op where
        the file does not exist, and it is what the scaffold already does."""
        out = _render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=True)

        assert "rm -f /usr/lib/python*/EXTERNALLY-MANAGED" in out
        assert "--break-system-packages" not in _instructions(out)

    def test_the_marker_is_removed_in_the_same_layer(self):
        # The apt install above pulls python3-dev, which puts the file back, so
        # removing it in an earlier layer would do nothing.
        out = _render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=True)
        layer = out[out.index("COPY requirements.txt") :]

        assert layer.index("EXTERNALLY-MANAGED") < layer.index("pip install")


class TestTheScaffoldImage:
    """This Dockerfile is rendered once, by `rkd init`, so a Jinja conditional
    would be evaluated against a requirements.txt that does not exist yet."""

    CTX = {
        "odoo_version": "18.0",
        "use_third_party_repos": False,
        "use_enterprise": False,
        "vsc_port": "8888",
        "ssh_key_name": "",
    }

    def test_it_uses_a_glob_that_tolerates_the_file_being_absent(self):
        out = _render("Dockerfile.jinja", **self.CTX)

        assert "COPY requirements.tx[t]" in out

    def test_the_install_is_guarded(self):
        out = _render("Dockerfile.jinja", **self.CTX)

        assert "if [ -f /opt/odoo/requirements.txt ]" in out
        assert "--ignore-installed" in out

    def test_it_does_not_pass_the_flag_either(self):
        # It already removes EXTERNALLY-MANAGED in the apt layer.
        out = _render("Dockerfile.jinja", **self.CTX)

        assert "--break-system-packages" not in _instructions(out)
        assert "rm -f /usr/lib/python*/EXTERNALLY-MANAGED" in out


class TestTheDockerDeployer:
    @pytest.fixture
    def project(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM odoo:19.0\n")
        (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
        (tmp_path / "addons").mkdir()
        return tmp_path

    @staticmethod
    def _deployer(root):
        from rocketdoo.core.instance.deployer_docker import DockerInstanceDeployer

        cfg = {
            "type": "docker",
            "vps": {"host": "h", "user": "u", "auth_method": "ssh_key", "ssh_key": "~/.ssh/k"},
            "db_user": "odoo_prod",
            "db_name": "odoo_prod",
            "domain": "x.example.com",
            "remote_path": "/opt/odoo-prod",
        }
        return DockerInstanceDeployer("prod", cfg, root)

    def test_the_file_travels_in_the_build_context(self, project, tmp_path):
        (project / REQUIREMENTS_FILE).write_text("authlib>=1.6.12\n")
        out = tmp_path / "ctx"
        out.mkdir()

        self._deployer(project)._render_templates(out, dry_run=True)

        assert (out / REQUIREMENTS_FILE).read_text() == "authlib>=1.6.12\n"
        assert "COPY requirements.txt" in (out / "Dockerfile.prod").read_text()

    def test_a_project_without_it_is_untouched(self, project, tmp_path):
        out = tmp_path / "ctx"
        out.mkdir()

        self._deployer(project)._render_templates(out, dry_run=True)

        assert not (out / REQUIREMENTS_FILE).exists()
        assert "requirements.txt" not in (out / "Dockerfile.prod").read_text()

    @pytest.mark.parametrize("use_gitman", [False, True])
    def test_it_actually_travels_to_the_vps(self, project, tmp_path, monkeypatch, use_gitman):
        """Copying it into tmp is not enough, and this is the test that was
        missing: `_transfer` sends named files one at a time, never the whole
        temporary directory. Without its own rsync the build dies on
        `failed to compute cache key: "/requirements.txt": not found`, and the
        message rkd prints next points at the SSH agent instead.

        Parametrised over gitman because the copy sits outside that branch: a
        project with a requirements.txt and no third-party repos has to get it
        too.
        """
        (project / REQUIREMENTS_FILE).write_text("authlib>=1.6.12\n")
        if use_gitman:
            (project / "gitman.yaml").write_text("location: external_addons\nsources: []\n")
        deployer = self._deployer(project)
        deployer.cfg["use_gitman"] = use_gitman
        out = tmp_path / "ctx"
        out.mkdir()
        deployer._render_templates(out, dry_run=True)
        sent = []
        monkeypatch.setattr(deployer, "_rsync", lambda src, dest, *a: sent.append(dest) or True)
        monkeypatch.setattr(deployer, "_ssh", lambda *a, **kw: True)

        assert deployer._transfer(out) is True

        assert f"{deployer.remote_path}/{REQUIREMENTS_FILE}" in sent

    def test_a_project_without_it_sends_nothing_extra(self, project, tmp_path, monkeypatch):
        deployer = self._deployer(project)
        out = tmp_path / "ctx"
        out.mkdir()
        deployer._render_templates(out, dry_run=True)
        sent = []
        monkeypatch.setattr(deployer, "_rsync", lambda src, dest, *a: sent.append(dest) or True)
        monkeypatch.setattr(deployer, "_ssh", lambda *a, **kw: True)

        assert deployer._transfer(out) is True

        assert not [dest for dest in sent if REQUIREMENTS_FILE in dest]

    def test_a_failed_transfer_fails_the_step(self, project, tmp_path, monkeypatch):
        # A build that cannot find the file in its context is worse than a
        # deploy that stops here saying why.
        (project / REQUIREMENTS_FILE).write_text("authlib\n")
        deployer = self._deployer(project)
        out = tmp_path / "ctx"
        out.mkdir()
        deployer._render_templates(out, dry_run=True)
        monkeypatch.setattr(deployer, "_ssh", lambda *a, **kw: True)
        monkeypatch.setattr(deployer, "_rsync", lambda src, dest, *a: REQUIREMENTS_FILE not in dest)

        assert deployer._transfer(out) is False

    def test_the_dockerignore_does_not_exclude_it(self, project, tmp_path, monkeypatch):
        (project / REQUIREMENTS_FILE).write_text("authlib\n")
        deployer = self._deployer(project)
        out = tmp_path / "ctx"
        out.mkdir()
        deployer._render_templates(out, dry_run=True)
        monkeypatch.setattr(deployer, "_rsync", lambda *a, **kw: True)
        monkeypatch.setattr(deployer, "_ssh", lambda *a, **kw: True)
        deployer._transfer(out)

        assert REQUIREMENTS_FILE not in (out / ".dockerignore").read_text()

    def test_it_does_not_depend_on_gitman(self, project, tmp_path):
        """The copy sits outside the gitman branch: a project with a
        requirements.txt and no third-party repos still gets it."""
        (project / REQUIREMENTS_FILE).write_text("defusedxml\n")
        out = tmp_path / "ctx"
        out.mkdir()

        self._deployer(project)._render_templates(out, dry_run=True)

        assert (out / REQUIREMENTS_FILE).exists()


class TestTheNativeDeployer:
    @staticmethod
    def _deployer(root):
        from rocketdoo.core.instance.deployer_native import NativeInstanceDeployer

        cfg = {
            "type": "native",
            "vps": {"host": "h", "user": "u", "auth_method": "ssh_key", "ssh_key": "~/.ssh/k"},
            "db_user": "odoo_prod",
            "db_name": "odoo_prod",
            "remote_path": "/opt/odoo/extra-addons",
        }
        return NativeInstanceDeployer("prod", cfg, root)

    def test_the_step_appears_only_with_the_file(self, tmp_path):
        labels = [label for label, _ in self._deployer(tmp_path)._steps()]
        assert not [one for one in labels if REQUIREMENTS_FILE in one]

        (tmp_path / REQUIREMENTS_FILE).write_text("authlib\n")
        labels = [label for label, _ in self._deployer(tmp_path)._steps()]

        assert [one for one in labels if REQUIREMENTS_FILE in one]

    def test_it_runs_before_the_database_and_the_restart(self, tmp_path):
        """The module's dependencies have to be there when installing `base`
        touches it, and certainly before the service restarts."""
        (tmp_path / REQUIREMENTS_FILE).write_text("authlib\n")
        labels = [label for label, _ in self._deployer(tmp_path)._steps()]
        where = next(i for i, one in enumerate(labels) if REQUIREMENTS_FILE in one)

        assert where < next(i for i, one in enumerate(labels) if "database" in one.lower())
        assert where < next(i for i, one in enumerate(labels) if "service" in one.lower())

    def test_it_transfers_and_installs_into_the_system_interpreter(self, tmp_path, monkeypatch):
        (tmp_path / REQUIREMENTS_FILE).write_text("authlib\n")
        deployer = self._deployer(tmp_path)
        seen = {}
        monkeypatch.setattr(deployer, "_rsync", lambda src, dest, *a: seen.update(rsync=(src, dest)) or True)
        monkeypatch.setattr(deployer, "_ssh", lambda command, **kw: seen.update(ssh=command) or True)

        assert deployer._install_requirements() is True

        assert seen["rsync"][1] == REMOTE_REQUIREMENTS
        assert "EXTERNALLY-MANAGED" in seen["ssh"]
        assert f"pip install --ignore-installed -r {REMOTE_REQUIREMENTS}" in seen["ssh"]
        assert "--break-system-packages" not in seen["ssh"]

    def test_a_failed_transfer_fails_the_step(self, tmp_path, monkeypatch):
        (tmp_path / REQUIREMENTS_FILE).write_text("authlib\n")
        deployer = self._deployer(tmp_path)
        monkeypatch.setattr(deployer, "_rsync", lambda *a, **kw: False)
        monkeypatch.setattr(deployer, "_ssh", lambda *a, **kw: pytest.fail("must not install what did not arrive"))

        assert deployer._install_requirements() is False
