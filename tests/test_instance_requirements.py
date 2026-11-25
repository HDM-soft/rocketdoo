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

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from rocketdoo.core.instance.config_manager import REQUIREMENTS_FILE, REQUIREMENTS_TARGET

TEMPLATES = Path(__file__).resolve().parents[1] / "rocketdoo" / "templates"
# The scaffold copies it next to install_dependencies.sh, for the reason
# test_the_glob_shares_a_copy_with_a_source_that_always_exists explains.
SCAFFOLD_REQ = "/usr/lib/python3/dist-packages/odoo/requirements.txt"


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
    "requirements_target": REQUIREMENTS_TARGET,
}


class TestTheInstanceImage:
    def test_without_the_file_nothing_is_added(self):
        out = _render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=False)

        assert "requirements.txt" not in out

    def test_with_the_file_it_is_copied_and_installed(self):
        out = _render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=True)

        assert f"COPY requirements.txt {REQUIREMENTS_TARGET}" in out
        assert f"pip install --ignore-installed -r {REQUIREMENTS_TARGET}" in out

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

        assert "requirements.tx[t]" in out

    def test_the_glob_shares_a_copy_with_a_source_that_always_exists(self):
        """A COPY whose only source is a glob with no matches is rejected by the
        classic builder ("COPY failed: no source files were specified"), and
        `docker compose build` honours DOCKER_BUILDKIT=0 and falls back to it —
        so `rkd build --rebuild` broke every project without the file. With a
        source that always exists alongside, both builders accept it."""
        out = _render("Dockerfile.jinja", **self.CTX)

        copy = next(line for line in out.splitlines() if "requirements.tx[t]" in line)

        assert "install_dependencies.sh" in copy
        assert copy.index("install_dependencies.sh") < copy.index("requirements.tx[t]")
        assert _instructions(out).count("install_dependencies.sh") == 1, "two COPYs again"

    def test_the_install_is_guarded(self):
        out = _render("Dockerfile.jinja", **self.CTX)

        assert f"if [ -f {SCAFFOLD_REQ} ]" in out
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

    @pytest.mark.parametrize("seeded", [False, True])
    def test_it_runs_before_the_database_and_the_restart(self, tmp_path, seeded):
        """The order is load-bearing, and more so when seeding.

        `_ensure_database` does not only run `-i base`: when the mark is
        missing it runs `odoo shell`, which loads the registry — and a restored
        database brings the project's modules already **installed**, so that
        shell imports their Python and needs these dependencies. The step can
        not merely go before the database: it has to.
        """
        (tmp_path / REQUIREMENTS_FILE).write_text("authlib\n")
        deployer = self._deployer(tmp_path)
        if seeded:
            deployer.cfg["seed_database"] = "dev_db"
            deployer.seed_database = "dev_db"
        labels = [label for label, _ in deployer._steps()]
        where = next(i for i, one in enumerate(labels) if REQUIREMENTS_FILE in one)

        assert where < next(i for i, one in enumerate(labels) if "database" in one.lower())
        assert where < next(i for i, one in enumerate(labels) if "service" in one.lower())

    @staticmethod
    def _record(deployer, monkeypatch, results=None):
        calls = []

        def _ssh(command, **kw):
            calls.append((command, kw.get("input")))
            return True if results is None else results.pop(0)

        monkeypatch.setattr(deployer, "_ssh", _ssh)
        monkeypatch.setattr(deployer, "_rsync", lambda *a, **kw: pytest.fail("rsync cannot reach a root-owned path"))
        return calls

    def test_the_file_is_written_by_stdin_under_sudo(self, tmp_path, monkeypatch):
        """Not rsync: this is the only transfer of the native deployer whose
        destination lives under a root-owned directory that nothing else
        creates. rsync neither makes the directory nor uses sudo, so with the
        wizard's default user (`ubuntu`) it fails with "change_dir failed" or
        "Permission denied". `_configure_odoo` already writes a root-owned file
        this way."""
        (tmp_path / REQUIREMENTS_FILE).write_text("authlib>=1.6.12\n")
        deployer = self._deployer(tmp_path)
        calls = self._record(deployer, monkeypatch)

        assert deployer._install_requirements() is True

        write, install, smoke = calls
        assert smoke[0] == 'sudo python3 -c "import odoo.addons.base"'
        assert "sudo mkdir -p /opt/odoo" in write[0]
        assert f"sudo tee {REQUIREMENTS_TARGET}" in write[0]
        assert write[1] == "authlib>=1.6.12\n"
        assert "EXTERNALLY-MANAGED" in install[0]
        assert f"pip install --ignore-installed -r {REQUIREMENTS_TARGET}" in install[0]
        assert "--break-system-packages" not in install[0]

    def test_a_failed_write_does_not_try_to_install(self, tmp_path, monkeypatch):
        (tmp_path / REQUIREMENTS_FILE).write_text("authlib\n")
        deployer = self._deployer(tmp_path)
        calls = self._record(deployer, monkeypatch, results=[False])

        assert deployer._install_requirements() is False
        assert len(calls) == 1, "installing what never arrived"

    def test_pip_is_installed_even_without_gitman(self, tmp_path):
        """The Odoo nightly deb depends on neither pip nor a toolchain, and
        python3-pip only came in with gitman: without this the step failed on
        every project that does not use third-party repos."""
        (tmp_path / REQUIREMENTS_FILE).write_text("authlib\n")
        deployer = self._deployer(tmp_path)
        assert deployer.cfg.get("use_gitman") is None
        sent = []
        deployer._ssh = lambda command, **kw: sent.append(command) or True

        deployer._install_system_deps()

        assert "python3-pip" in sent[0]
        # A dependency with no wheel has to have something to compile with.
        assert "python3-dev" in sent[0] and "build-essential" in sent[0]

    def test_a_project_without_the_file_does_not_pull_the_toolchain(self, tmp_path):
        deployer = self._deployer(tmp_path)
        sent = []
        deployer._ssh = lambda command, **kw: sent.append(command) or True

        deployer._install_system_deps()

        assert "build-essential" not in sent[0]

    def test_a_broken_interpreter_is_not_reported_as_success(self, tmp_path, monkeypatch, capsys):
        """Measured on a real build: a project declaring authlib gets
        cryptography bumped and apt's pyOpenSSL stops working, so Odoo does not
        import at all. Without this check the step reports success, the restart
        takes the running Odoo down with it, and `_verify_odoo_answers` fails
        thirty lines later with no idea why."""
        (tmp_path / REQUIREMENTS_FILE).write_text("authlib\n")
        deployer = self._deployer(tmp_path)
        calls = self._record(deployer, monkeypatch, results=[True, True, False])

        assert deployer._install_requirements() is False

        assert "import odoo.addons.base" in calls[-1][0]
        assert "pyopenssl" in capsys.readouterr().out.lower()

    def test_a_failed_install_does_not_blame_the_interpreter(self, tmp_path, monkeypatch, capsys):
        """A pip that failed is a pip that failed: pointing at pyopenssl there
        sends the user after the wrong thing."""
        (tmp_path / REQUIREMENTS_FILE).write_text("authlib\n")
        deployer = self._deployer(tmp_path)
        calls = self._record(deployer, monkeypatch, results=[True, False])

        assert deployer._install_requirements() is False
        assert len(calls) == 2, "smoke-testing an interpreter pip never finished with"
        assert "pyopenssl" not in capsys.readouterr().out.lower()


class TestTheImportCheck:
    """Both images verify the interpreter still works before calling it a build.

    Measured against real `odoo:18.0`/`19.0`/`20.0` builds: a requirements.txt
    with `authlib` bumps cryptography to 50.0.2, leaves apt's pyOpenSSL
    incompatible and Odoo stops importing entirely. Without the check the build
    is green and the container never starts -- the same silent failure #336 came
    to remove, one step lower.
    """

    CASES = (
        ("instance/Dockerfile.jinja", {**INSTANCE_CTX, "requirements": True}),
        ("Dockerfile.jinja", TestTheScaffoldImage.CTX),
    )

    @staticmethod
    def _flat(rendered: str) -> str:
        """The RUN as docker sees it: line continuations collapsed."""
        return re.sub(r"[ \t]*\\\n\s*", " ", rendered)

    @pytest.mark.parametrize("name,ctx", CASES)
    def test_the_chain_is_pip_then_check_then_hint(self, name, ctx):
        """`pip && { check || hint }`, never `pip || hint`: blaming pyopenssl for
        a dependency that does not exist sends the user after the wrong thing."""
        flat = self._flat(_render(name, **ctx))

        assert re.search(
            r"pip install --ignore-installed -r \S+ && \{ (cd / && )?python3 -c "
            r'"import odoo\.addons\.base" \|\| \{ echo "ERROR: [^"]*pyopenssl[^"]*" >&2; exit 1; \}; \}',
            flat,
        ), flat

    @pytest.mark.parametrize("name,ctx", CASES)
    def test_it_imports_a_module_that_runs_odoo_code(self, name, ctx):
        """`import odoo` would be a no-op where it matters most: from 19.0 the
        `odoo` package has no __init__.py (PEP 420 namespace package, startup
        moved to init.py), so it runs no Odoo code and exits 0 on a broken
        interpreter -- measured on odoo:19.0 and 20.0, which are golden profiles
        and the series the nightly deb installs. `odoo.addons.base` is what
        load_server_wide_modules() imports and exits 1 on all six series."""
        flat = self._flat(_render(name, **ctx))

        assert 'python3 -c "import odoo.addons.base"' in flat
        assert 'python3 -c "import odoo"' not in flat

    def test_the_instance_image_does_not_check_without_the_file(self):
        rendered = _render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=False)

        assert "import odoo" not in rendered

    def test_the_scaffold_check_lives_inside_the_guard(self):
        """Outside the `if`, a project without the file would run it too -- which
        is fine, until it is not: this RUN must stay a no-op without the file."""
        rendered = _render("Dockerfile.jinja", **TestTheScaffoldImage.CTX)

        guarded = rendered.split(f"if [ -f {SCAFFOLD_REQ} ]; then")[1].split("else")[0]

        assert "import odoo.addons.base" in guarded

    def test_the_instance_check_leaves_the_gitman_workdir(self):
        """With use_gitman the WORKDIR is still /opt/odoo/gitman, and `python3 -c`
        prepends the cwd to sys.path: a gitman file with `location: odoo` would
        shadow the real package with an empty directory and the check would pass
        on anything."""
        flat = self._flat(_render("instance/Dockerfile.jinja", **INSTANCE_CTX, requirements=True))

        assert "{ cd / && python3 -c" in flat
