"""Render tests for the Jinja templates.

Every generated project starts as one of these renders, so a template that
stops producing valid YAML — or that quietly drops a port or an addons path —
breaks `rkd init` for everyone. Rendering happens with StrictUndefined so a
variable the caller forgot to pass fails here instead of leaving a blank in a
user's docker-compose.yaml.

Snapshots live in tests/snapshots/ and are refreshed with:

    UPDATE_SNAPSHOTS=1 pytest tests/test_templates.py
"""

import os
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from rocketdoo.core.instance.config_manager import PG_PROFILES

from .conftest import TEMPLATES

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# Mirrors the context init_project.py builds (see init_project.py:273).
PROJECT_CONTEXT = {
    "project_name": "demo",
    "odoo_version": "17.0",
    "odoo_edition": "Community",
    "db_version": "15",
    "odoo_port": 8069,
    "vsc_port": 8888,
    "restart": "unless-stopped",
    "odoo_image": "odoo:17.0",
    "odoo_container": "odoo-demo",
    "db_container": "db-demo",
    "admin_passwd": "s3cr3t-admin",
    "use_private_repos": False,
    "ssh_key_name": "id_ed25519",
    "use_third_party_repos": False,
}

INSTANCE_CONTEXT = {
    "project_name": "demo",
    "slug": "demo-stage",
    "environment": "stage",
    "domain": "stage.example.com",
    "traefik_email": "ops@example.com",
    "odoo_tag": "17.0",
    "db_user": "odoo",
    "db_version": "15",
    "admin_passwd": "s3cr3t-admin",
    "workers": 2,
    "log_level": "info",
    "limit_memory_hard": 1610612736,
    "limit_memory_soft": 1073741824,
    "use_enterprise": False,
    "use_gitman": False,
    "gitman_config_file": "gitman.stage.yml",
    "pg": PG_PROFILES["small"],
}

# Mirrors the context rkd ci init derives from get_project_info() (spec RF5).
CI_CONTEXT = {
    "project_name": "demo",
    "odoo_port": 8069,
    "default_branch": "main",
    "rkd_spec": "~=3.5",
    "ruff_target": "py312",
    "install_supported": True,
    "install_trigger": "pull_request",
    "unsupported_reasons": [],
}

CI_INSTALL_TRIGGERS = ("pull_request", "push", "manual", "never")

ALL_TEMPLATES = sorted(p.relative_to(TEMPLATES).as_posix() for p in TEMPLATES.rglob("*.jinja"))


def _env(subdir: Path) -> Environment:
    return Environment(loader=FileSystemLoader(str(subdir)), undefined=StrictUndefined)


def render(rel_path: str, **context) -> str:
    path = TEMPLATES / rel_path
    return _env(path.parent).get_template(path.name).render(**context)


def _context_for(rel_path: str) -> dict:
    if rel_path.startswith("instance/"):
        return INSTANCE_CONTEXT
    if rel_path.startswith("ci/"):
        return CI_CONTEXT
    return PROJECT_CONTEXT


def assert_snapshot(name: str, content: str):
    """Compare against the stored render, or record it the first time."""
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    snapshot = SNAPSHOT_DIR / name
    if os.environ.get("UPDATE_SNAPSHOTS") or not snapshot.exists():
        snapshot.write_text(content)
        if not os.environ.get("UPDATE_SNAPSHOTS"):
            pytest.skip(f"recorded new snapshot {name}")
        return
    assert content == snapshot.read_text(), f"{name} changed. If intended: UPDATE_SNAPSHOTS=1 pytest tests/test_templates.py"


class TestAllTemplates:
    def test_templates_were_discovered(self):
        assert len(ALL_TEMPLATES) >= 9

    @pytest.mark.parametrize("rel_path", ALL_TEMPLATES)
    def test_renders_without_undefined_variables(self, rel_path):
        """StrictUndefined: every variable the template uses must be supplied."""
        assert render(rel_path, **_context_for(rel_path)).strip()

    @pytest.mark.parametrize("rel_path", ALL_TEMPLATES)
    def test_render_matches_snapshot(self, rel_path):
        content = render(rel_path, **_context_for(rel_path))
        assert_snapshot(rel_path.replace("/", "__") + ".txt", content)

    @pytest.mark.parametrize("rel_path", [p for p in ALL_TEMPLATES if "compose" in p or p.endswith(".yaml.jinja")])
    def test_yaml_templates_render_to_valid_yaml(self, rel_path):
        parsed = yaml.safe_load(render(rel_path, **_context_for(rel_path)))
        assert isinstance(parsed, dict)


class TestProjectCompose:
    @pytest.fixture
    def compose(self):
        return yaml.safe_load(render("docker-compose.yaml.jinja", **PROJECT_CONTEXT))

    def test_declares_web_and_db(self, compose):
        assert {"web", "db"} <= set(compose["services"])

    def test_publishes_the_configured_ports(self, compose):
        from rocketdoo.core.port_validation import _host_port

        published = {_host_port(p) for p in compose["services"]["web"]["ports"]}
        assert {8069, 8888} <= published

    def test_container_names_follow_the_project(self, compose):
        names = {s.get("container_name") for s in compose["services"].values()}
        assert {"odoo-demo", "db-demo"} <= names

    def test_restart_policy_is_applied(self, compose):
        assert compose["services"]["web"]["restart"] == "unless-stopped"

    def test_db_uses_the_requested_postgres_version(self, compose):
        assert "15" in compose["services"]["db"]["image"]

    def test_admin_password_is_not_embedded_in_compose(self, compose):
        """admin_passwd belongs in odoo.conf, never in the compose file."""
        assert "s3cr3t-admin" not in yaml.dump(compose)

    def test_mailpit_block_ships_commented_out(self):
        """`rkd mail on` toggles this block; it must start disabled."""
        raw = render("docker-compose.yaml.jinja", **PROJECT_CONTEXT)
        assert "rkd:mailpit" in raw
        assert "mailpit" not in yaml.safe_load(raw)["services"]


class TestProjectDockerfile:
    def test_uses_the_selected_odoo_version(self):
        raw = render("Dockerfile.jinja", **PROJECT_CONTEXT)
        assert "FROM odoo:17.0" in raw

    def test_exposes_the_debug_port(self):
        assert "8888" in render("Dockerfile.jinja", **PROJECT_CONTEXT)

    def test_gitman_is_skipped_without_third_party_repos(self):
        raw = render("Dockerfile.jinja", **{**PROJECT_CONTEXT, "use_third_party_repos": False})
        active = [ln for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        assert not any("gitman install" in ln for ln in active)

    def test_gitman_is_wired_in_with_third_party_repos(self):
        raw = render("Dockerfile.jinja", **{**PROJECT_CONTEXT, "use_third_party_repos": True})
        assert "gitman" in raw


class TestOdooConf:
    @pytest.fixture
    def conf(self):
        return render("config/odoo.conf.jinja", **PROJECT_CONTEXT)

    def test_carries_the_admin_password(self, conf):
        assert "s3cr3t-admin" in conf

    def test_points_at_the_db_container(self, conf):
        assert "db-demo" in conf

    def test_declares_an_addons_path(self, conf):
        assert "addons_path" in conf


class TestInstanceTemplates:
    @pytest.fixture
    def compose(self):
        return yaml.safe_load(render("instance/docker-compose.jinja", **INSTANCE_CONTEXT))

    def test_is_valid_yaml_with_services(self, compose):
        assert compose["services"]

    def test_applies_the_postgres_tuning_profile(self, compose):
        rendered = yaml.dump(compose)
        assert str(PG_PROFILES["small"]["shared_buffers"]) in rendered

    @pytest.mark.parametrize("profile", sorted(PG_PROFILES))
    def test_every_pg_profile_renders(self, profile):
        raw = render("instance/docker-compose.jinja", **{**INSTANCE_CONTEXT, "pg": PG_PROFILES[profile]})
        assert yaml.safe_load(raw)["services"]

    def test_traefik_routes_to_the_domain(self, compose):
        assert "stage.example.com" in yaml.dump(compose)

    def test_odoo_conf_runs_behind_a_proxy(self):
        conf = render("instance/odoo.conf.jinja", **INSTANCE_CONTEXT)
        assert "proxy_mode = True" in conf

    def test_odoo_conf_hides_the_database_list(self):
        """list_db must stay off on a public instance."""
        conf = render("instance/odoo.conf.jinja", **INSTANCE_CONTEXT)
        assert "list_db = False" in conf

    @pytest.mark.parametrize(("env", "workers"), [("stage", 2), ("prod", 4)])
    def test_worker_count_follows_the_environment(self, env, workers):
        conf = render("instance/odoo.conf.jinja", **{**INSTANCE_CONTEXT, "environment": env, "workers": workers})
        assert f"workers = {workers}" in conf

    def test_dockerfile_uses_the_requested_tag(self):
        assert "17.0" in render("instance/Dockerfile.jinja", **INSTANCE_CONTEXT)

    def test_dockerfile_enables_buildkit_ssh_syntax(self):
        """The `--mount=type=ssh` clone needs the syntax directive on line 1."""
        raw = render("instance/Dockerfile.jinja", **INSTANCE_CONTEXT)
        assert raw.lstrip().startswith("# syntax=docker/dockerfile:1")


class TestGitignoreTemplate:
    def test_covers_every_sensitive_entry(self):
        from rocketdoo.core.gitignore_manager import SENSITIVE_ENTRIES, template_content

        rendered = template_content()
        entries = {ln.strip() for ln in rendered.splitlines() if ln.strip() and not ln.startswith("#")}
        assert {pat for pat, _ in SENSITIVE_ENTRIES} <= entries


class TestDockerfilePipInstall:
    """The PEP 668 marker handling, which broke the build twice.

    #152: `--break-system-packages` was used, which pip 20/22 (odoo:15-17) does
    not accept. #163: the marker was removed in its own layer, before the apt
    install that reinstates it on odoo:18/19 — so the removal had no effect
    where it was actually needed.
    """

    @pytest.fixture
    def dockerfile(self):
        return render("Dockerfile.jinja", **PROJECT_CONTEXT)

    @pytest.fixture
    def instructions(self, dockerfile):
        """The Dockerfile without comments — what actually runs.

        The comments deliberately mention --break-system-packages to explain
        why it is not used, so a plain substring check would fail on them.
        """
        return "\n".join(line for line in dockerfile.splitlines() if not line.lstrip().startswith("#"))

    def test_does_not_use_break_system_packages(self, instructions):
        """pip 20.3 (bullseye) and 22.0 (jammy) reject the flag outright."""
        assert "--break-system-packages" not in instructions

    def test_removes_the_externally_managed_marker(self, instructions):
        assert "rm -f /usr/lib/python*/EXTERNALLY-MANAGED" in instructions

    def test_the_marker_is_removed_after_apt_and_before_pip(self, instructions):
        """Order is the whole bug: python3-dev reinstates the marker.

        A removal in an earlier layer is undone by the apt install, so pip then
        fails with externally-managed-environment on Ubuntu noble.
        """
        removal = instructions.index("rm -f /usr/lib/python*/EXTERNALLY-MANAGED")
        apt_install = instructions.rindex("apt install -y python3-m2crypto")
        pip_install = instructions.index("python3 -m pip install")

        assert apt_install < removal < pip_install, (
            "EXTERNALLY-MANAGED must be removed after the apt installs (python3-dev puts it back) and before pip runs"
        )

    def test_the_removal_shares_a_layer_with_pip(self, instructions):
        """Same RUN: a separate layer would be undone by any later apt."""
        run_blocks = instructions.split("\nRUN ")
        pip_block = next(b for b in run_blocks if "python3 -m pip install" in b)
        assert "EXTERNALLY-MANAGED" in pip_block

    def test_pipx_failure_does_not_abort_the_build(self, instructions):
        """pipx is not packaged for bullseye (odoo:15.0/16.0)."""
        assert "|| echo" in instructions


class TestOdooConfKeys:
    """The generated odoo.conf must use keys Odoo actually reads.

    `givent_port` shipped for a while: Odoo ignores an unknown key and falls
    back to the default, so nothing broke visibly, but editing that line to
    change the port had no effect.
    """

    @pytest.fixture
    def conf(self):
        return render("config/odoo.conf.jinja", **PROJECT_CONTEXT)

    def test_gevent_port_is_spelled_correctly(self, conf):
        assert "gevent_port" in conf
        assert "givent_port" not in conf

    @pytest.mark.parametrize("rel_path", ["config/odoo.conf.jinja", "instance/odoo.conf.jinja"])
    def test_no_template_carries_the_typo(self, rel_path):
        assert "givent_port" not in (TEMPLATES / rel_path).read_text()

    def test_active_keys_have_no_obvious_typos(self, conf):
        """Guard the uncommented keys against the same class of mistake.

        Odoo swallows unknown options silently, so a misspelling here is
        invisible until someone wonders why a setting does nothing.
        """
        known = {
            "addons_path",
            "admin_passwd",
            "data_dir",
            "db_host",
            "db_maxconn",
            "db_name",
            "db_password",
            "db_port",
            "db_sslmode",
            "db_template",
            "db_user",
            "dbfilter",
            "demo",
            "email_from",
            "gevent_port",
            "geoip_database",
            "http_enable",
            "http_interface",
            "http_port",
            "import_partial",
            "limit_memory_hard",
            "limit_memory_soft",
            "limit_request",
            "limit_time_cpu",
            "limit_time_real",
            "limit_time_real_cron",
            "list_db",
            "log_db",
            "log_db_level",
            "log_handler",
            "log_level",
            "logfile",
            "logrotate",
            "longpolling_port",
            "max_cron_threads",
            "osv_memory_age_limit",
            "osv_memory_count_limit",
            "pg_path",
            "pidfile",
            "proxy_mode",
            "reportgz",
            "screencasts",
            "screenshots",
            "server_wide_modules",
            "smtp_password",
            "smtp_port",
            "smtp_server",
            "smtp_ssl",
            "smtp_user",
            "syslog",
            "test_enable",
            "test_file",
            "test_tags",
            "transient_age_limit",
            "translate_modules",
            "unaccent",
            "upgrade_path",
            "without_demo",
            "workers",
        }
        active = {
            line.split("=")[0].strip()
            for line in conf.splitlines()
            if "=" in line and not line.strip().startswith((";", "#", "["))
        }
        assert active <= known, f"unknown odoo.conf keys: {sorted(active - known)}"


class TestDockerfileBullseyeRepos:
    """Debian 11 left debian-security while its index kept advertising versions.

    bullseye became oldoldstable and its security binaries went away from every
    mirror, while the index still lists them, so apt resolves against packages
    whose .deb returns 404 — including git, which the build needs. The Dockerfile
    drops that repo on bullseye only; elsewhere the security updates are real and
    must stay.
    """

    @pytest.fixture
    def instructions(self):
        rendered = render("Dockerfile.jinja", **PROJECT_CONTEXT)
        return "\n".join(line for line in rendered.splitlines() if not line.lstrip().startswith("#"))

    def test_the_security_repo_is_dropped_only_on_bullseye(self, instructions):
        assert "VERSION_CODENAME" in instructions
        assert "bullseye" in instructions
        assert "sed -i '/security/d' /etc/apt/sources.list" in instructions

    def test_the_removal_is_guarded_by_a_conditional(self, instructions):
        """Unconditional removal would strip real security updates on noble."""
        sed_at = instructions.index("sed -i '/security/d'")
        guard_at = instructions.index("VERSION_CODENAME")
        assert guard_at < sed_at

    def test_it_runs_before_apt_update(self, instructions):
        """apt update after the edit, or the stale index is used anyway."""
        assert instructions.index("sed -i '/security/d'") < instructions.index("apt update")

    def test_build_only_deps_tolerate_failure(self, instructions):
        """libssl-dev and friends compile M2Crypto; python3-m2crypto ships it built."""
        assert "libssl-dev" in instructions
        build_deps_line = next(ln for ln in instructions.splitlines() if "libssl-dev" in ln)
        assert "||" in build_deps_line

    def test_git_is_still_required(self, instructions):
        """gitman cannot clone without it: its install must NOT be tolerated."""
        git_line = next(ln for ln in instructions.splitlines() if "apt install -y git" in ln)
        assert "||" not in git_line


class TestCiWorkflowTemplate:
    """`rkd ci init` renders this to `.github/workflows/rkd-ci.yml` (spec RF6/RF8).

    install_trigger governs whether the (expensive) install job runs on every
    push, only on pull requests against the default branch, only manually, or
    never at all — see spec RF5.b. install_supported=False (Enterprise or
    private-repo projects) drops the install job regardless of the trigger.
    """

    @pytest.mark.parametrize("trigger", CI_INSTALL_TRIGGERS)
    def test_renders_valid_yaml_for_every_install_trigger(self, trigger):
        content = render("ci/workflow.yaml.jinja", **{**CI_CONTEXT, "install_trigger": trigger})
        parsed = yaml.safe_load(content)

        assert "lint" in parsed["jobs"]
        if trigger == "never":
            assert "install" not in parsed["jobs"]
        else:
            assert "install" in parsed["jobs"]

        assert_snapshot(f"ci__workflow_install_trigger_{trigger}.yaml.jinja.txt", content)

    def test_pull_request_trigger_only_targets_the_default_branch(self):
        content = render("ci/workflow.yaml.jinja", **{**CI_CONTEXT, "install_trigger": "pull_request"})
        condition = yaml.safe_load(content)["jobs"]["install"]["if"]
        assert "pull_request" in condition
        assert "main" in condition

    @pytest.mark.parametrize("trigger", CI_INSTALL_TRIGGERS)
    def test_the_rendered_workflow_has_no_actions_expression(self, trigger):
        """Spec RF6: no ${{ }} at all, so every decision stays in the rkd commands.

        It is also Jinja's own delimiter, so anything left would need escaping
        and would drift the moment the template is edited.
        """
        content = render("ci/workflow.yaml.jinja", **{**CI_CONTEXT, "install_trigger": trigger})
        assert "${{" not in content

    def test_the_built_image_is_the_one_compose_runs(self):
        """The build tags the image compose declares, or compose builds again.

        docker-compose.yaml.jinja gives web both `build: .` and
        `image: {{project_name}}`; the workflow relies on that second key to
        reuse the cached build instead of paying for it twice.
        """
        compose = yaml.safe_load(render("docker-compose.yaml.jinja", **PROJECT_CONTEXT))
        content = render("ci/workflow.yaml.jinja", **CI_CONTEXT)
        build_step = next(
            step
            for step in yaml.safe_load(content)["jobs"]["install"]["steps"]
            if str(step.get("uses", "")).startswith("docker/build-push-action")
        )
        assert build_step["with"]["tags"] == compose["services"]["web"]["image"]

    def test_install_job_is_absent_when_not_supported(self):
        context = {
            **CI_CONTEXT,
            "install_supported": False,
            "unsupported_reasons": ["Enterprise addons are not public."],
        }
        content = render("ci/workflow.yaml.jinja", **context)
        parsed = yaml.safe_load(content)

        assert set(parsed["jobs"]) == {"lint"}
        assert "Enterprise addons are not public." in content

        assert_snapshot("ci__workflow_install_unsupported.yaml.jinja.txt", content)

    def test_lint_job_always_runs_ruff_and_deploy_validate(self):
        content = render("ci/workflow.yaml.jinja", **{**CI_CONTEXT, "install_supported": False})
        parsed = yaml.safe_load(content)
        run_steps = " ".join(step.get("run", "") for step in parsed["jobs"]["lint"]["steps"])
        assert "ruff check" in run_steps
        assert "rkd deploy validate" in run_steps
