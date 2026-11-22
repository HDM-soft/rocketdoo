"""Pre-flight (#276): what is checked before a deploy touches anything, and a
dry-run that neither claims success nor writes."""

import subprocess

import pytest
from click.testing import CliRunner

from rocketdoo import instance_cli
from rocketdoo.cli_output import print_checks
from rocketdoo.core.instance import deployer_docker, deployer_native, preflight, secrets_store, ssh_utils
from rocketdoo.core.instance.enterprise import REPO
from tests.conftest import GREEN_REMOTE

GIB = 1024 * 1024


def _cfg(**overrides) -> dict:
    cfg = {
        "type": "docker",
        "vps": {"host": "vps.example.com", "user": "ubuntu", "port": 22, "auth_method": "ssh_key", "ssh_key": "~/.ssh/k"},
        "remote_path": "/opt/odoo-prod",
        "pg_profile": "small",
        "db_user": "odoo_prod",
        "db_name": "odoo_prod",
    }
    cfg.update(overrides)
    return cfg


def _local(**overrides) -> dict:
    local = {
        "enterprise_dir": True,
        "agent_has_keys": True,
        "ssh_auth_sock": True,
        "build_needs_ssh": False,
        "key_file": True,
        "invalid_identifiers": [],
    }
    local.update(overrides)
    return local


def _remote(**overrides) -> dict:
    return {**GREEN_REMOTE, **overrides}


def _by_name(checks: list[dict]) -> dict[str, dict]:
    return {c["name"]: c for c in checks}


def _evaluate(cfg=None, local=None, remote=None, env="prod") -> dict[str, dict]:
    return _by_name(preflight.evaluate(cfg or _cfg(), local or _local(), remote or _remote(), env=env))


class TestAGreenVps:
    def test_nothing_blocks_and_nothing_warns(self):
        checks = preflight.evaluate(_cfg(), _local(), _remote(), env="stage")

        assert not preflight.blocking(checks)
        assert all(c["ok"] for c in checks)


class TestBlockers:
    def test_an_unreachable_vps_is_a_blocker_and_the_rest_is_not_asked(self):
        checks = _evaluate(remote={"reachable": "no", "error": "connection refused"})

        assert checks["vps connection"]["ok"] is False
        assert "connection refused" in checks["vps connection"]["detail"]
        assert "docker" not in checks and "memory" not in checks

    def test_invalid_identifiers_block(self):
        checks = _evaluate(local=_local(invalid_identifiers=["bad-name"]))

        assert checks["database identifiers"]["ok"] is False
        assert "bad-name" in checks["database identifiers"]["detail"]

    def test_a_missing_key_file_is_a_warning_not_a_blocker(self):
        checks = preflight.evaluate(_cfg(), _local(key_file=False), _remote(), env="prod")

        assert not preflight.blocking(checks)
        key = _by_name(checks)["ssh key file"]
        assert key["ok"] is False and key["level"] == "warn"

    def test_password_auth_has_no_key_file_check(self):
        assert "ssh key file" not in _evaluate(local=_local(key_file=None))

    @pytest.mark.parametrize("remote", [{"docker_ok": "no"}, {"compose": ""}])
    def test_docker_must_be_usable_with_compose(self, remote):
        assert _evaluate(remote=_remote(**remote))["docker"]["ok"] is False

    def test_native_does_not_ask_for_docker(self):
        assert "docker" not in _evaluate(_cfg(type="native"))

    def test_an_unwritable_path_blocks_only_without_sudo(self):
        assert _evaluate(remote=_remote(path_writable="no", sudo="no"))["remote path"]["ok"] is False
        with_sudo = _evaluate(remote=_remote(path_writable="no", sudo="yes"))["remote path"]
        assert with_sudo["ok"] is True
        assert "needs sudo" in with_sudo["detail"]
        assert _evaluate(remote=_remote(path_writable="yes", sudo="no"))["remote path"]["ok"] is True

    def test_rsync_enterprise_needs_the_local_directory(self):
        cfg = _cfg(use_enterprise=True, enterprise_source="rsync")

        assert _evaluate(cfg, _local(enterprise_dir=False))["enterprise directory"]["ok"] is False
        assert _evaluate(cfg, _local(enterprise_dir=True))["enterprise directory"]["ok"] is True
        assert "enterprise directory" not in _evaluate(_cfg(use_enterprise=False), _local(enterprise_dir=False))

    @pytest.mark.parametrize(
        "remote, ok",
        [
            ({}, True),
            ({"repo_ok": [], "repo_fail": [REPO]}, False),
            ({"git": "no"}, False),
        ],
    )
    def test_a_cloned_enterprise_must_be_readable_from_the_vps(self, remote, ok):
        cfg = _cfg(use_enterprise=True, enterprise_source="clone")

        assert _evaluate(cfg, remote=_remote(**remote))["enterprise repository"]["ok"] is ok

    def test_a_missing_traefik_is_not_a_blocker(self):
        checks = preflight.evaluate(
            _cfg(domain="erp.example.com"), _local(), _remote(traefik_running="no", traefik_network="no"), env="prod"
        )

        assert not preflight.blocking(checks)


class TestTheSshAgentSocket:
    """`docker compose build --ssh default` aborts with no agent socket, even
    when the Dockerfile mounts none. The check is asked exactly when the build
    passes that flag: demanding a socket from a build that dropped it would
    block the very projects the flag was made conditional for.
    """

    def test_a_build_that_forwards_an_agent_is_blocked_without_a_socket(self):
        check = _evaluate(_cfg(), _local(ssh_auth_sock=False, build_needs_ssh=True))["ssh agent socket"]

        assert check["level"] == "block"
        assert check["ok"] is False
        assert "eval $(ssh-agent)" in check["hint"]

    def test_with_a_socket_it_passes_even_if_the_agent_is_empty(self):
        local = _local(ssh_auth_sock=True, agent_has_keys=False, build_needs_ssh=True)

        assert _evaluate(_cfg(), local)["ssh agent socket"]["ok"] is True

    def test_a_build_without_the_flag_is_not_asked(self):
        local = _local(ssh_auth_sock=False, build_needs_ssh=False)

        assert "ssh agent socket" not in _evaluate(_cfg(), local)

    def test_a_native_deploy_is_not_asked(self):
        local = _local(ssh_auth_sock=False, build_needs_ssh=True)

        assert "ssh agent socket" not in _evaluate(_cfg(type="native"), local)

    def test_the_fact_reads_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
        assert preflight.local_facts(tmp_path, _cfg())["ssh_auth_sock"] is True
        monkeypatch.delenv("SSH_AUTH_SOCK")
        assert preflight.local_facts(tmp_path, _cfg())["ssh_auth_sock"] is False


class TestWhetherTheBuildForwardsAnAgent:
    """One definition, read by the pre-flight check and by the build flag."""

    def _needs(self, tmp_path, sources, **over):
        (tmp_path / "gitman.yaml").write_text("sources:\n" + "".join(f"- repo: {s}\n" for s in sources))
        return preflight.build_needs_ssh(tmp_path, _cfg(use_gitman=True, **over))

    def test_https_sources_do_not(self, tmp_path):
        assert self._needs(tmp_path, ["https://github.com/OCA/web.git"]) is False

    def test_an_ssh_source_does(self, tmp_path):
        assert self._needs(tmp_path, ["git@github.com:org/repo.git"]) is True

    def test_a_scp_style_source_without_a_user_does(self, tmp_path):
        """It carries no user and no scheme: matching SSH forms would miss it
        and strip a flag the build needs."""
        assert self._needs(tmp_path, ["github.com:org/repo"]) is True

    def test_without_gitman_it_does_not(self, tmp_path):
        assert preflight.build_needs_ssh(tmp_path, _cfg(use_gitman=False)) is False

    def test_the_deployer_reads_the_same_predicate(self, tmp_path):
        from rocketdoo.core.instance import deployer_docker

        (tmp_path / "gitman.yaml").write_text("sources:\n  - repo: git@github.com:org/repo.git\n")
        cfg = _cfg(use_gitman=True)
        deployer = deployer_docker.DockerInstanceDeployer("prod", cfg, tmp_path)

        assert deployer._needs_ssh() == preflight.build_needs_ssh(tmp_path, cfg)


class TestWarnings:
    def test_traefik_warns_only_with_a_domain_on_docker(self):
        remote = _remote(traefik_running="no")

        assert _evaluate(_cfg(domain="erp.example.com"), remote=remote)["traefik"]["ok"] is False
        assert _evaluate(_cfg(domain="erp.example.com"), remote=remote)["traefik"]["level"] == "warn"
        assert "traefik" not in _evaluate(_cfg(), remote=remote)
        assert "traefik" not in _evaluate(_cfg(type="native", domain="erp.example.com"), remote=remote)

    def test_low_disk_warns(self):
        assert _evaluate(remote=_remote(free_kb=str(4 * GIB)))["disk space"]["ok"] is False
        assert _evaluate(remote=_remote(free_kb=str(6 * GIB)))["disk space"]["ok"] is True

    def test_an_empty_agent_warns_when_the_vps_clones(self):
        cfg = _cfg(use_enterprise=True, enterprise_source="clone")

        assert _evaluate(cfg, _local(agent_has_keys=False))["ssh agent"]["ok"] is False
        assert _evaluate(cfg, _local(agent_has_keys=True))["ssh agent"]["ok"] is True
        assert "ssh agent" not in _evaluate(_cfg(), _local(agent_has_keys=False))

    def test_memory_counts_master_workers_cron_and_gevent(self):
        """prod on Docker: (2 workers + 1 cron + 2) x 1024 MiB + 256 MiB of
        shared_buffers = 5376 MiB. The VPS of the issue (3.8 GB, 3624 MiB
        of MemTotal) is below it; counting only the workers never warned."""
        memory = _evaluate(remote=_remote(mem_kb=str(3624 * 1024)))["memory"]

        assert memory["ok"] is False
        assert "5376 MiB" in memory["detail"]
        assert _evaluate(remote=_remote(mem_kb=str(5376 * 1024)))["memory"]["ok"] is True

    def test_more_cron_threads_raise_the_threshold(self, monkeypatch):
        remote = _remote(mem_kb=str(5376 * 1024))
        monkeypatch.setattr(preflight, "CRON_THREADS", 2)

        assert _evaluate(remote=remote)["memory"]["ok"] is False

    def test_native_counts_the_distro_postgresql_not_the_profile(self):
        """Native does not apply pg_profile: Debian's cluster keeps 128 MiB."""
        detail = _evaluate(_cfg(type="native"), remote=_remote(mem_kb="1"))["memory"]["detail"]

        assert "5248 MiB" in detail

    def test_the_hint_names_levers_that_exist(self):
        hint = _evaluate(remote=_remote(mem_kb="1"))["memory"]["hint"]

        assert "stage" in hint and "more RAM" in hint
        assert "limit_memory_soft" not in hint and "workers," not in hint.split("stage")[0]
        assert "Add swap" not in hint

    def test_a_shared_buffers_without_a_unit_does_not_raise(self):
        assert preflight._mib("256") == 0


class TestTheNativeSudo:
    NATIVE = _cfg(type="native")

    def test_sudo_blocks_even_when_the_path_is_writable(self):
        remote = _remote(path_writable="yes", sudo="no", uid="1000")

        checks = _evaluate(self.NATIVE, remote=remote)

        assert checks["sudo"]["ok"] is False
        assert preflight.blocking(preflight.evaluate(self.NATIVE, _local(), remote, env="prod"))

    def test_root_needs_no_sudo(self):
        assert _evaluate(self.NATIVE, remote=_remote(sudo="no", uid="0"))["sudo"]["ok"] is True

    def test_available_sudo_passes(self):
        assert _evaluate(self.NATIVE, remote=_remote(sudo="yes", uid="1000"))["sudo"]["ok"] is True

    def test_docker_still_accepts_a_writable_path_without_sudo(self):
        assert _evaluate(remote=_remote(path_writable="yes", sudo="no", uid="1000"))["remote path"]["ok"] is True
        assert "sudo" not in _evaluate(remote=_remote(sudo="no"))


class TestLocalChecksComeFirst:
    def test_a_known_failure_does_not_open_the_ssh_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preflight, "_agent_has_keys", lambda: True)
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/rkd-test-agent.sock")
        monkeypatch.setattr(preflight, "remote_facts", lambda *a, **k: pytest.fail("the session was opened"))

        checks = preflight.run(tmp_path, {}, _cfg(use_enterprise=True, enterprise_source="rsync"), env="prod")

        assert preflight.blocking(checks)

    def test_without_local_blockers_the_vps_is_asked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preflight, "_agent_has_keys", lambda: True)
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/rkd-test-agent.sock")
        monkeypatch.setattr(preflight, "remote_facts", lambda *a, **k: _remote())

        checks = preflight.run(tmp_path, {}, _cfg(), env="prod")

        assert "vps connection" in _by_name(checks)


class TestLocalFacts:
    def test_what_the_project_holds(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preflight, "_agent_has_keys", lambda: False)

        facts = preflight.local_facts(tmp_path, _cfg(db_user="bad-name"))

        assert facts["enterprise_dir"] is False
        assert facts["agent_has_keys"] is False
        assert facts["invalid_identifiers"] == ["bad-name"]
        assert facts["key_file"] is False


class TestRemoteFacts:
    AUTH = {"method": "ssh_key", "key_path": "/k", "password": None}

    def _run(self, monkeypatch, stdout="", returncode=0, stderr="", raises=None):
        seen = {}

        def run(cmd, **kwargs):
            seen["cmd"], seen["kwargs"] = cmd, kwargs
            if raises:
                raise raises
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

        monkeypatch.setattr(preflight.subprocess, "run", run)
        return seen

    def test_one_read_only_session_with_the_agent_forwarded(self, monkeypatch):
        seen = self._run(monkeypatch, "done=yes\n")

        preflight.remote_facts(self.AUTH, _cfg())

        assert seen["cmd"][-1] == "bash -s"
        assert "-A" in seen["cmd"]
        assert seen["kwargs"]["timeout"] == 90

    def test_the_script_has_no_go_templates_and_braces_its_docker_calls(self, monkeypatch):
        seen = self._run(monkeypatch, "done=yes\n")

        preflight.remote_facts(self.AUTH, _cfg())
        script = seen["kwargs"]["input"]

        assert "{{" not in script
        assert script.startswith("{\n") and script.endswith("}\n")
        assert "docker compose version --short" in script

    def test_it_never_writes_on_the_vps(self, monkeypatch):
        seen = self._run(monkeypatch, "done=yes\n")

        preflight.remote_facts(self.AUTH, _cfg(use_enterprise=True, enterprise_source="clone"))
        script = seen["kwargs"]["input"]

        for writer in ("mkdir", "rm ", "chmod", "chown", "apt", "network create", " up ", "git clone"):
            assert writer not in script

    def test_each_repository_is_probed_without_ever_prompting(self, monkeypatch):
        seen = self._run(monkeypatch, "done=yes\n")

        preflight.remote_facts(
            self.AUTH, _cfg(use_enterprise=True, enterprise_source="clone"), extra_repos=["https://x/y.git"]
        )
        script = seen["kwargs"]["input"]

        assert script.count("git ls-remote --exit-code") == 2
        assert "GIT_TERMINAL_PROMPT=0" in script
        assert "BatchMode=yes" in script

    def test_the_output_is_parsed_into_facts_and_repositories(self, monkeypatch):
        self._run(
            monkeypatch,
            f"mem_kb=1000\ncompose=2.29.0\nrepo_ok={REPO}\nrepo_fail=https://x/y.git\nnoise\ndone=yes\n",
        )

        facts = preflight.remote_facts(self.AUTH, _cfg())

        assert facts["reachable"] == "yes"
        assert facts["mem_kb"] == "1000"
        assert facts["compose"] == "2.29.0"
        assert facts["repo_ok"] == [REPO]
        assert facts["repo_fail"] == ["https://x/y.git"]

    @pytest.mark.parametrize(
        "kwargs, kind",
        [
            ({"returncode": 255, "stderr": "Connection refused"}, "transport"),
            ({"raises": subprocess.TimeoutExpired("ssh", 90)}, "timeout"),
            ({"raises": FileNotFoundError("ssh")}, "transport"),
            ({"stdout": "whoami=deploy\nuid=1000\n"}, "truncated"),
            ({"stdout": ""}, "truncated"),
        ],
    )
    def test_an_unreachable_or_cut_probe_is_a_fact_not_an_exception(self, monkeypatch, kwargs, kind):
        self._run(monkeypatch, **kwargs)

        facts = preflight.remote_facts(self.AUTH, _cfg())

        assert facts["reachable"] == "no"
        assert facts["kind"] == kind
        assert facts["error"]
        assert preflight.blocking(preflight.evaluate(_cfg(), _local(), facts))

    def test_a_cut_probe_is_not_read_as_a_vps_missing_everything(self, monkeypatch):
        self._run(monkeypatch, "whoami=deploy\nuid=1000\n")

        checks = _by_name(preflight.evaluate(_cfg(), _local(), preflight.remote_facts(self.AUTH, _cfg()), env="prod"))

        assert "docker" not in checks and "remote path" not in checks and "memory" not in checks

    def test_each_failure_gets_its_own_hint(self):
        def hint(**remote):
            return _evaluate(remote={"reachable": "no", "error": "x", **remote})["vps connection"]["hint"]

        assert "network problem" in hint(kind="timeout")
        assert "dropped" in hint(kind="truncated")
        assert "instance.yaml" in hint(kind="transport")


class TestPrintChecks:
    def _print(self, checks) -> str:
        from rich.console import Console

        console = Console(record=True, width=200)
        print_checks(console, checks)
        return console.export_text()

    def test_the_hint_appears_only_under_the_failures(self):
        out = self._print(
            [
                {"name": "a", "level": "block", "ok": True, "detail": "fine", "hint": ""},
                {"name": "b", "level": "block", "ok": False, "detail": "broken", "hint": "do this"},
                {"name": "c", "level": "warn", "ok": False, "detail": "meh", "hint": "maybe that"},
            ]
        )

        assert "✓ a: fine" in out
        assert "✗ b: broken" in out and "do this" in out
        assert "⚠ c: meh" in out and "maybe that" in out


@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
    calls = []

    def run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(deployer_docker.subprocess, "run", run)
    return calls


def _blocked(monkeypatch):
    monkeypatch.setattr(preflight, "remote_facts", lambda *a, **k: {"reachable": "no", "error": "connection refused"})


class TestADeployThatIsBlocked:
    def _docker(self, tmp_path, **overrides):
        cfg = {**_cfg(**overrides), "odoo_version": "18.0"}
        return deployer_docker.DockerInstanceDeployer("prod", cfg, tmp_path)

    def test_docker_stops_before_rsync_or_build(self, tmp_path, monkeypatch, traced, green_preflight):
        _blocked(monkeypatch)

        assert self._docker(tmp_path).deploy() is False
        sent = " ".join(" ".join(map(str, cmd)) + (kw.get("input") or "") for cmd, kw in traced)
        assert "rsync" not in sent and "docker compose build" not in sent
        assert not (tmp_path / ".rkd").exists()

    def test_docker_stops_on_a_missing_local_enterprise_before_any_transfer(
        self, tmp_path, monkeypatch, traced, green_preflight
    ):
        deployer = self._docker(tmp_path, use_enterprise=True, enterprise_source="rsync")

        assert deployer.deploy() is False
        assert not any(cmd[0] == "rsync" for cmd, _ in traced)

    def test_native_stops_before_touching_the_vps(self, tmp_path, monkeypatch, traced, green_preflight):
        _blocked(monkeypatch)
        monkeypatch.setattr(deployer_native.subprocess, "run", deployer_docker.subprocess.run)
        deployer = deployer_native.NativeInstanceDeployer("prod", _cfg(type="native"), tmp_path)

        assert deployer.deploy() is False
        assert traced == []


class TestADryRun:
    def _deployer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        cfg = {**_cfg(), "odoo_version": "18.0"}
        return deployer_docker.DockerInstanceDeployer("prod", cfg, tmp_path)

    def test_writes_nothing_under_the_project(self, tmp_path, monkeypatch, traced, green_preflight):
        assert self._deployer(tmp_path, monkeypatch).deploy(dry_run=True) is True

        assert not (tmp_path / ".rkd").exists()
        assert list(tmp_path.iterdir()) == []

    def test_renders_the_placeholder_where_no_secret_is_stored(self, tmp_path, monkeypatch, traced, green_preflight):
        deployer = self._deployer(tmp_path, monkeypatch)
        rendered = tmp_path.parent / f"{tmp_path.name}-rendered"
        rendered.mkdir()

        deployer._render_templates(rendered, dry_run=True)

        assert "db_password = <generated at deploy time>" in (rendered / "prod" / "config" / "odoo.conf").read_text()
        assert not (tmp_path / ".rkd").exists()

    def test_uses_the_stored_secrets_when_there_are_some(self, tmp_path, monkeypatch, traced, green_preflight):
        secrets_store.save(tmp_path, "prod", {secrets_store.PG_PASS: "storedpass", secrets_store.ADMIN_PASSWD: "master"})
        deployer = self._deployer(tmp_path, monkeypatch)

        creds = deployer._resolve_secrets(dry_run=True)

        assert creds == {"pg_pass": "storedpass", "admin_passwd": "master"}

    def test_does_not_read_the_remote_password_file(self, tmp_path, monkeypatch, traced, green_preflight):
        self._deployer(tmp_path, monkeypatch).deploy(dry_run=True)

        assert not any("odoo_pg_pass" in " ".join(map(str, cmd)) for cmd, _ in traced)

    def test_native_dry_run_writes_nothing_either(self, tmp_path, monkeypatch, traced, green_preflight):
        monkeypatch.setattr(deployer_native.subprocess, "run", deployer_docker.subprocess.run)
        deployer = deployer_native.NativeInstanceDeployer("prod", _cfg(type="native"), tmp_path)

        assert deployer.deploy(dry_run=True) is True
        assert not (tmp_path / ".rkd").exists()


class TestTheConfiguredKey:
    """resolve_auth() had the same blind spot as the module deployer (#314):
    a bare name in instance.yaml resolved relative to the current directory."""

    def test_bare_name_resolves_under_ssh_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        vps = {"host": "h", "user": "u", "auth_method": "ssh_key", "ssh_key": "hdmsoft"}

        auth = ssh_utils.resolve_auth(vps, "prod", tmp_path)

        assert auth["key_path"] == str(tmp_path / ".ssh" / "hdmsoft")

    def test_preflight_agrees_with_the_deployer(self, tmp_path, monkeypatch):
        # A warn that contradicts what the deploy then does is worse than no warn.
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".ssh").mkdir()
        (tmp_path / ".ssh" / "hdmsoft").write_text("private")

        vps = {"host": "h", "user": "u", "port": 22, "auth_method": "ssh_key", "ssh_key": "hdmsoft"}

        facts = preflight.local_facts(tmp_path, _cfg(vps=vps))

        assert facts["key_file"] is True


class TestThePasswordPrompt:
    def test_it_no_longer_writes_the_password_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "getpass", lambda *_: "s3cret")
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        vps = {"host": "h", "user": "u", "auth_method": "password", "password": ""}

        auth = ssh_utils.resolve_auth(vps, "prod", tmp_path)

        assert auth["password"] == "s3cret"
        assert not (tmp_path / ".rkd").exists()


class _FakeDeployer:
    result = True

    def __init__(self, *args):
        pass

    def deploy(self, dry_run=False):
        return self.result


class TestTheDeployCommand:
    def _invoke(self, project_dir, monkeypatch, result, *args):
        from rocketdoo.core.instance.config_manager import InstanceConfigManager

        InstanceConfigManager(project_dir).save(
            {"environments": {"prod": {"type": "docker", "vps": {"host": "h", "user": "u"}, "domain": "d.example.com"}}}
        )
        _FakeDeployer.result = result
        monkeypatch.setattr(deployer_docker, "DockerInstanceDeployer", _FakeDeployer)
        from rich.console import Console

        console = Console(record=True, width=120)
        monkeypatch.setattr(instance_cli, "console", console)
        CliRunner().invoke(instance_cli.instance_deploy, ["--env", "prod", *args])
        return console.export_text()

    def test_a_dry_run_never_says_deployment_successful(self, project_dir, monkeypatch):
        out = self._invoke(project_dir, monkeypatch, True, "--dry-run")

        assert "Dry-run complete" in out and "nothing was deployed" in out
        assert "Deployment successful" not in out

    def test_a_blocked_dry_run_says_so(self, project_dir, monkeypatch):
        out = self._invoke(project_dir, monkeypatch, False, "--dry-run")

        assert "Dry-run found blocking problems" in out
        assert "Deployment successful" not in out and "Deployment failed" not in out

    def test_a_real_deploy_keeps_its_panel(self, project_dir, monkeypatch):
        out = self._invoke(project_dir, monkeypatch, True, "--yes")

        assert "Deployment successful" in out


class TestWarningsSurviveAFailure:
    def test_they_are_repeated_when_the_deploy_fails_midway(self, tmp_path, monkeypatch, capsys, green_preflight):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        monkeypatch.setattr(preflight, "remote_facts", lambda *a, **k: _remote(mem_kb="1"))
        monkeypatch.setattr(deployer_docker.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", ""))
        deployer = deployer_docker.DockerInstanceDeployer("prod", {**_cfg(), "odoo_version": "18.0"}, tmp_path)

        assert deployer.deploy() is False
        assert capsys.readouterr().out.count("memory:") == 2


class TestGitman:
    CFG = {"use_gitman": True, "gitman_config": "gitman.yaml"}

    def test_a_declared_file_that_is_absent_blocks(self):
        checks = _evaluate(_cfg(**self.CFG), _local(gitman_file=False))
        assert checks["gitman file"]["ok"] is False
        assert checks["gitman file"]["level"] == "block"

    def test_a_missing_script_that_the_gitman_file_runs_blocks(self):
        local = _local(gitman_file=True, install_script=False, gitman_scripts=True)
        checks = preflight.evaluate(_cfg(**self.CFG), local)
        assert [c["name"] for c in preflight.blocking(checks)] == ["gitman dependency script"]

    def test_a_missing_script_that_nothing_runs_only_warns(self):
        local = _local(gitman_file=True, install_script=False, gitman_scripts=False)
        checks = preflight.evaluate(_cfg(**self.CFG), local)
        assert not preflight.blocking(checks)
        assert _by_name(checks)["gitman dependency script"]["ok"] is False

    def test_no_git_on_the_host_warns_for_docker_because_the_build_has_its_own(self):
        url = "https://h/a.git"
        local = _local(gitman_file=True, install_script=True, gitman_urls=[url])
        checks = preflight.evaluate(_cfg(**self.CFG), local, _remote(git="no", repo_ok=[]), env="prod")
        assert _by_name(checks)["gitman repositories"]["level"] == "warn"
        assert not preflight.blocking(checks)

    def test_a_repository_the_vps_cannot_read_blocks(self):
        url = "https://h/a.git"
        local = _local(gitman_file=True, install_script=True, gitman_urls=[url])
        checks = _evaluate(_cfg(**self.CFG), local, _remote(repo_ok=[], repo_fail=[url]))
        assert checks["gitman repositories"]["level"] == "block"
        assert url in checks["gitman repositories"]["detail"]
        assert _evaluate(_cfg(**self.CFG), local, _remote(repo_ok=[url]))["gitman repositories"]["ok"] is True

    def test_a_native_deploy_is_checked_like_docker_and_no_longer_warns_it_is_ignored(self):
        cfg = _cfg(type="native", sudo_user="x", **self.CFG)
        checks = preflight.evaluate(cfg, _local(gitman_file=False))
        names = _by_name(checks)
        assert "gitman" not in names
        assert names["gitman file"]["level"] == "block"
        assert [c["name"] for c in preflight.blocking(checks)] == ["gitman file"]

    def test_a_native_deploy_probes_the_repositories(self):
        url = "https://h/a.git"
        cfg = _cfg(type="native", sudo_user="x", **self.CFG)
        local = _local(gitman_file=True, install_script=True, gitman_urls=[url])
        checks = _evaluate(cfg, local, _remote(repo_ok=[], repo_fail=[url]))
        assert checks["gitman repositories"]["level"] == "block"

    def test_local_facts_read_the_gitman_file_for_both_types(self, tmp_path):
        (tmp_path / "gitman.yaml").write_text("sources:\n  - repo: https://h/a.git\n    scripts: [x]\n")
        for kind in ("docker", "native"):
            facts = preflight.local_facts(tmp_path, _cfg(type=kind, **self.CFG))
            assert facts["gitman_urls"] == ["https://h/a.git"] and facts["gitman_scripts"] is True
        off = preflight.local_facts(tmp_path, _cfg(type="native", use_gitman=False))
        assert off["gitman_urls"] == [] and off["gitman_scripts"] is False

    def test_the_urls_come_from_the_file_and_are_probed(self, tmp_path, monkeypatch):
        (tmp_path / "gitman.yaml").write_text("sources:\n  - repo: https://h/a.git\n  - repo: https://h/a.git\n")
        monkeypatch.setattr(preflight, "_agent_has_keys", lambda: True)
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/rkd-test-agent.sock")
        seen = {}
        monkeypatch.setattr(preflight, "remote_facts", lambda *a, **k: seen.update(k) or _remote())
        preflight.run(tmp_path, {}, _cfg(**self.CFG), env="prod")
        assert seen["extra_repos"] == ["https://h/a.git"]

    def test_the_probes_run_in_parallel_and_are_waited_for(self, monkeypatch):
        script = preflight._remote_script(_cfg(), ["a", "b"])
        assert script.count(") &\n") == 2
        assert script.index("wait") < script.index("done=yes")

    def test_the_session_timeout_allows_one_probe_not_one_per_repository(self, monkeypatch):
        seen = {}

        def run(cmd, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, stdout="done=yes\n", stderr="")

        monkeypatch.setattr(preflight.subprocess, "run", run)
        auth = {"method": "ssh_key", "key_path": "/k", "password": None}
        preflight.remote_facts(auth, _cfg(), extra_repos=["a", "b", "c"])
        assert seen["timeout"] == preflight._REMOTE_TIMEOUT + preflight._GIT_PROBE_SECONDS


class TestTheTraefikCheck:
    def test_a_traefik_the_container_cannot_reach_is_told_apart_from_no_traefik(self):
        absent = _evaluate(_cfg(domain="erp.example.com"), remote=_remote(traefik_running="no", traefik_attached="no"))
        stranger = _evaluate(_cfg(domain="erp.example.com"), remote=_remote(traefik_attached="no"))

        assert "no Traefik container" in absent["traefik"]["detail"]
        assert "not attached" in stranger["traefik"]["detail"]
        assert stranger["traefik"]["ok"] is False

    def test_the_hint_names_the_command(self):
        checks = _evaluate(_cfg(domain="erp.example.com"), remote=_remote(traefik_running="no"))

        assert "rkd instance traefik --env prod" in checks["traefik"]["hint"]

    def test_the_remote_script_asks_whether_traefik_is_on_the_network(self):
        assert "traefik_attached=" in preflight._remote_script(_cfg(domain="erp.example.com"), [])

    def test_the_attachment_probe_recognises_a_proxy_the_same_way_as_the_running_one(self):
        """By image and case-insensitively, not by container name: docker's
        `name` filter is case-sensitive, so a foreign proxy called `edge` read
        as "not attached" while it was sitting on the network."""
        script = preflight._remote_script(_cfg(domain="erp.example.com"), [])
        probe = next(line for line in script.splitlines() if "traefik_attached=" in line)

        assert "--filter name=" not in probe
        assert f"--filter network={preflight._TRAEFIK_NETWORK}" in probe


class TestThePublicUrlProbe:
    @pytest.mark.parametrize(
        "output, ok, fragment",
        [
            ("http=301:0\nhttps=200:0\n", True, "answers"),
            ("http=301:0\nhttps=000:60\n", False, "certificate"),
            ("http=301:0\nhttps=000:35\n", False, "certificate"),
            ("http=404:0\nhttps=404:0\n", False, "does not reach Odoo"),
            ("http=301:0\nhttps=502:0\n", False, "does not reach Odoo"),
            ("http=000:7\nhttps=000:7\n", False, "nothing answers"),
            ("", False, "nothing answers"),
        ],
    )
    def test_the_four_states_are_told_apart(self, output, ok, fragment):
        check = preflight.public_url_check("erp.example.com", output, env="prod")

        assert check["ok"] is ok
        assert fragment in check["detail"]
        assert check["level"] == "warn"

    def test_a_failure_points_at_the_command(self):
        check = preflight.public_url_check("erp.example.com", "http=000:7\nhttps=000:7\n", env="stage")

        assert "rkd instance traefik --env stage" in check["hint"]


class TestSeeding:
    SEEDED = {"seed_database": "dev_db", "db_version": "16"}

    def _local_seed(self, **overrides):
        return _local(seed_exists=True, seed_error="", seed_bytes=0, local_pg_major=16, **overrides)

    def test_neither_check_appears_without_a_seed_database(self):
        checks = _evaluate()

        assert "seed database" not in checks and "postgresql version" not in checks

    def test_both_appear_with_one_and_pass(self):
        checks = _evaluate(_cfg(**self.SEEDED), self._local_seed())

        assert checks["seed database"]["ok"] and checks["postgresql version"]["ok"]
        assert checks["seed database"]["level"] == checks["postgresql version"]["level"] == "block"

    def test_a_database_that_vanished_blocks_with_the_reason(self):
        local = self._local_seed()
        local.update(seed_exists=False, seed_error="no database container configured")
        checks = preflight.evaluate(_cfg(**self.SEEDED), local)

        blocked = _by_name(preflight.blocking(checks))
        assert "no database container configured" in blocked["seed database"]["detail"]
        assert "postgresql version" not in _by_name(checks)

    def test_a_newer_dump_into_an_older_docker_server_blocks(self):
        checks = _evaluate(_cfg(seed_database="dev_db", db_version="15"), self._local_seed())

        assert not checks["postgresql version"]["ok"]
        assert "db_version" in checks["postgresql version"]["hint"]

    @pytest.mark.parametrize("target", ["16", "17"])
    def test_an_equal_or_newer_server_does_not_block(self, target):
        checks = _evaluate(_cfg(seed_database="dev_db", db_version=target), self._local_seed())

        assert checks["postgresql version"]["ok"]

    def test_native_compares_against_the_cluster_the_vps_reports(self):
        cfg = _cfg(type="native", seed_database="dev_db")
        old = _evaluate(cfg, self._local_seed(), _remote(pg_major="14"))
        new = _evaluate(cfg, self._local_seed(), _remote(pg_major="17"))

        assert not old["postgresql version"]["ok"]
        assert new["postgresql version"]["ok"]

    def test_native_without_a_cluster_yet_is_not_compared(self):
        checks = _evaluate(_cfg(type="native", seed_database="dev_db"), self._local_seed(), _remote(pg_major=""))

        assert checks["postgresql version"]["ok"] and "not compared" in checks["postgresql version"]["detail"]

    def test_the_remote_script_asks_for_the_cluster_only_when_native_and_seeding(self):
        assert "pg_major=" in preflight._remote_script(_cfg(type="native", seed_database="x"), [])
        assert "pg_major=" not in preflight._remote_script(_cfg(type="native"), [])
        assert "pg_major=" not in preflight._remote_script(_cfg(seed_database="x"), [])

    def test_disk_space_counts_twice_what_is_seeded(self):
        free = _remote(free_kb=str(8 * GIB))
        plain = _evaluate(_cfg(), _local(), free)["disk space"]
        seeded = _evaluate(_cfg(**self.SEEDED), self._local_seed() | {"seed_bytes": 2 * 1024**3}, free)["disk space"]

        assert plain["ok"] and not seeded["ok"]
        assert "9 GiB recommended" in seeded["detail"]

    def test_local_facts_measure_the_seed_only_when_asked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preflight, "databases_result", lambda compose: (["dev_db"], ""))
        monkeypatch.setattr(preflight, "database_bytes", lambda db, compose: 100)
        monkeypatch.setattr(preflight, "filestore_bytes", lambda container, db: 50)
        monkeypatch.setattr(preflight, "pg_major_version", lambda compose: 16)

        facts = preflight.local_facts(tmp_path, _cfg(**self.SEEDED))

        assert facts["seed_exists"] and facts["seed_bytes"] == 150 and facts["local_pg_major"] == 16
        assert "seed_exists" not in preflight.local_facts(tmp_path, _cfg())
