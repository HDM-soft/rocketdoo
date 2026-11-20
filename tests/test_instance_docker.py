"""What a dockerized `rkd instance deploy` actually sends to the VPS (#250).

The compose creates the PostgreSQL *role* by construction, but two things
were missing. The rendered odoo.conf said `db_password = False`, and the
image's entrypoint only forwards `--db_password` when the conf does not
define it -- so the secret the compose carefully mounts never reached Odoo
and the connection was refused. And nothing ever created the database, with
`list_db = False` leaving no database manager to create one by hand.

Like the native suite, most of these capture the commands that would be sent
and need no VPS. The ones that run do so because two bugs in this code were
only visible when the script was executed: `docker compose` drains the stdin
it inherits, which is the script itself, and `cmd && break` ends a `set -e`
script on the round where the container is not ready yet.
"""

import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.usefixtures("green_preflight")

from rocketdoo.core.instance import config_manager, deployer_docker, secrets_store, ssh_utils
from tests.conftest import GREEN_REMOTE


class _RecordingRun:
    """subprocess.run stand-in that records every call and never connects."""

    def __init__(self, fail_on: str = ""):
        self.calls = []
        self.fail_on = fail_on

    @staticmethod
    def _haystack(args, kwargs) -> str:
        """Argv plus stdin: three steps share the command `bash -s`, and are
        told apart only by the script they carry."""
        return " ".join(str(a) for a in args) + "\n" + (kwargs.get("input") or "")

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        failed = self.fail_on and self.fail_on in self._haystack(args, kwargs)
        return subprocess.CompletedProcess(args, returncode=1 if failed else 0, stdout="", stderr="")

    def index_of(self, needle: str) -> int:
        for i, (args, kwargs) in enumerate(self.calls):
            if needle in self._haystack(args, kwargs):
                return i
        raise AssertionError(f"no call contained {needle!r}")

    def sent_containing(self, needle: str) -> list[str]:
        return [self._haystack(args, kwargs) for args, kwargs in self.calls if needle in self._haystack(args, kwargs)]

    def script_with(self, needle: str) -> str:
        """The stdin of the call that carried `needle`."""
        return self.calls[self.index_of(needle)][1].get("input") or ""


def _config(**overrides) -> dict:
    cfg = {
        "type": "docker",
        "vps": {
            "host": "vps.example.com",
            "user": "ubuntu",
            "port": 22,
            "auth_method": "ssh_key",
            "ssh_key": "~/.ssh/id_rsa",
        },
        "odoo_version": "18.0",
        "odoo_tag": "18.0",
        "domain": "erp.example.com",
        "traefik_email": "ops@example.com",
        "db_user": "odoo_prod",
        "db_version": "16",
        "pg_profile": "small",
        "remote_path": "/opt/odoo-prod",
    }
    cfg.update(overrides)
    return cfg


def _deploy(tmp_path, monkeypatch, *, fail_on: str = "", **overrides) -> tuple[bool, _RecordingRun]:
    monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
    run = _RecordingRun(fail_on=fail_on)
    monkeypatch.setattr(deployer_docker.subprocess, "run", run)
    deployer = deployer_docker.DockerInstanceDeployer("prod", _config(**overrides), tmp_path)
    return deployer.deploy(), run


def _stored(tmp_path, key: str) -> str:
    return secrets_store.load(tmp_path, "prod")[key]


class TestTheDatabase:
    def test_the_database_is_created_and_base_installed(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.script_with("pg_database")

        assert '-O "odoo_prod" "odoo_prod"' in script
        assert "-i base" in script
        assert "--stop-after-init" in script

    def test_the_install_does_not_read_the_scripts_stream(self, tmp_path, monkeypatch):
        """The braces protect it today, which is an accident of where the
        line sits inside the guard."""
        _, run = _deploy(tmp_path, monkeypatch)
        install = next(line for line in run.script_with("pg_database").splitlines() if "-i base" in line)

        assert install.endswith("< /dev/null")

    def test_the_one_off_containers_are_not_traefik_backends(self, tmp_path, monkeypatch):
        """`compose run` inherits the service's labels: while `-i base` runs,
        Traefik would balance the live domain onto a container that serves
        nothing, and a dropped SSH session leaves it there."""
        _, run = _deploy(tmp_path, monkeypatch)

        for line in run.script_with("pg_database").splitlines():
            if "docker compose run" in line:
                assert "-l traefik.enable=false" in line

    def test_the_database_is_not_born_with_the_clusters_encoding(self, tmp_path, monkeypatch):
        """The postgres image initialises its cluster from the host locale,
        and template1 is mutable; Odoo's own CREATE DATABASE pins both."""
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.script_with("pg_database")

        assert "-E UTF8" in script
        assert "-T template0" in script
        assert "--lc-collate=C" in script

    def test_neither_half_runs_again_on_a_redeploy(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.script_with("pg_database")

        assert "FROM pg_database WHERE datname = 'odoo_prod'" in script
        assert "FROM ir_module_module WHERE name = 'base' AND state = 'installed'" in script
        assert f"WHERE key IN ('{deployer_docker._ADMIN_PASSWORD_MARK}', '{deployer_docker.SEEDED_MARK}')" in script

    def test_the_existence_check_connects_to_a_database_that_exists(self, tmp_path, monkeypatch):
        """psql defaults to a database named after the role, which is the one
        being looked for: without -d the check errors instead of answering."""
        _, run = _deploy(tmp_path, monkeypatch)
        check = next(line for line in run.script_with("pg_database").splitlines() if "pg_database" in line)

        assert "-d postgres" in check

    def test_docker_is_told_not_to_allocate_a_tty(self, tmp_path, monkeypatch):
        """A TTY wraps psql's output in carriage returns, and the emptiness
        check below would then never see an empty answer."""
        script = _deploy(tmp_path, monkeypatch)[1].script_with("pg_database")

        for line in script.splitlines():
            if "docker compose exec" in line:
                assert "exec -T" in line
            if "docker compose run" in line:
                assert "run --rm -T" in line

    def test_the_database_name_defaults_to_the_role_name(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)

        assert "datname = 'odoo_prod'" in run.script_with("pg_database")

    def test_a_configured_database_name_is_the_one_created(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch, db_name="erp")

        assert "datname = 'erp'" in run.script_with("pg_database")


class TestTheAdminUser:
    """Installing `base` leaves `admin` with the password `admin`, and here
    the instance is published on a public domain behind Traefik."""

    def test_the_admin_password_is_replaced_when_the_database_is_created(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.script_with("pg_database")
        password = _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD)

        assert "odoo shell" in script
        assert f"env.ref('base.user_admin').write({{'password': '{password}'}})" in script

    def test_it_is_set_behind_its_own_guard(self, tmp_path, monkeypatch):
        """Hanging it off the install's guard would skip it for good on a
        deploy where the install succeeded and this step did not."""
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.script_with("pg_database")

        assert script.index(deployer_docker._ADMIN_PASSWORD_MARK) < script.index("odoo shell")
        assert f"set_param('{deployer_docker._ADMIN_PASSWORD_MARK}', '1')" in script

    def test_it_is_not_the_master_password(self, tmp_path, monkeypatch):
        _deploy(tmp_path, monkeypatch)

        assert _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD) != _stored(tmp_path, secrets_store.ADMIN_PASSWD)

    def test_no_password_becomes_a_command_argument(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        secrets = [
            _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD),
            _stored(tmp_path, secrets_store.PG_PASS),
            _stored(tmp_path, secrets_store.ADMIN_PASSWD),
        ]

        for args, _kwargs in run.calls:
            for secret in secrets:
                assert all(secret not in str(item) for item in args)

    def test_a_second_deploy_keeps_the_one_already_handed_out(self, tmp_path, monkeypatch):
        _deploy(tmp_path, monkeypatch)
        first = _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD)

        _deploy(tmp_path, monkeypatch)

        assert _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD) == first


class TestTheRenderedConf:
    def _conf(self, tmp_path, monkeypatch, **overrides) -> str:
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_docker.DockerInstanceDeployer("prod", _config(**overrides), tmp_path)
        rendered = tmp_path / "rendered"
        rendered.mkdir()
        monkeypatch.setattr(deployer, "_read_remote_pg_pass", lambda: None)
        deployer._render_templates(rendered)
        return (rendered / "prod" / "config" / "odoo.conf").read_text()

    def test_prod_defaults_fit_a_small_vps(self, tmp_path, monkeypatch):
        conf = self._conf(tmp_path, monkeypatch)

        assert "workers = 2\n" in conf
        assert "limit_memory_hard = 1610612736\n" in conf
        assert "db_maxconn = 11\n" in conf
        assert "max_cron_threads = 1\n" in conf

    def test_an_unlisted_environment_falls_back_to_the_stage_limits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_docker.DockerInstanceDeployer("qa", _config(), tmp_path)
        rendered = tmp_path / "rendered"
        rendered.mkdir()
        monkeypatch.setattr(deployer, "_read_remote_pg_pass", lambda: None)
        deployer._render_templates(rendered)

        conf = (rendered / "qa" / "config" / "odoo.conf").read_text()
        assert "workers = 1\n" in conf
        assert "limit_memory_hard = 1073741824\n" in conf
        assert "db_maxconn = 15\n" in conf

    def test_the_conf_declares_the_password_the_compose_mounts(self, tmp_path, monkeypatch):
        conf = self._conf(tmp_path, monkeypatch)

        assert f"db_password = {_stored(tmp_path, secrets_store.PG_PASS)}\n" in conf
        assert "db_password = False" not in conf

    def test_the_conf_names_the_database_odoo_must_serve(self, tmp_path, monkeypatch):
        conf = self._conf(tmp_path, monkeypatch, db_name="erp")

        assert "db_name = erp\n" in conf

    def test_the_secret_file_is_not_left_readable_by_the_whole_vps(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)

        assert run.sent_containing("chmod 600 /opt/odoo-prod/prod/odoo_pg_pass")


class TestTheSequence:
    def test_each_step_runs_after_the_one_it_depends_on(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)

        order = [
            run.index_of("docker compose build"),
            run.index_of("pg_database"),
            run.index_of("docker compose up -d\n"),
            run.index_of("web/login"),
        ]
        assert order == sorted(order)

    def test_the_external_network_the_compose_needs_is_created(self, tmp_path, monkeypatch):
        """The compose declares traefik-public as external, so Compose will
        not create it: on a VPS that never ran Traefik, every step from the
        build onwards dies with "network traefik-public declared as
        external, but could not be found"."""
        _, run = _deploy(tmp_path, monkeypatch)

        assert run.sent_containing("docker network create traefik-public")
        assert run.index_of("network create traefik-public") < run.index_of("docker compose build")

    def test_a_failed_build_stops_before_the_database(self, tmp_path, monkeypatch):
        ok, run = _deploy(tmp_path, monkeypatch, fail_on="docker compose build")

        assert not ok
        assert not run.sent_containing("pg_database")

    def test_the_deploy_is_not_called_a_success_until_odoo_answers(self, tmp_path, monkeypatch):
        """`docker compose up -d` returns once the containers exist, which it
        does just as happily for an Odoo that exits a second later."""
        ok, run = _deploy(tmp_path, monkeypatch, fail_on="web/login")

        assert not ok
        assert run.index_of("web/login") > run.index_of("docker compose up -d\n")

    def test_the_check_runs_on_a_redeploy_too(self, tmp_path, monkeypatch):
        _deploy(tmp_path, monkeypatch)
        _, run = _deploy(tmp_path, monkeypatch)

        assert run.sent_containing("web/login")

    @pytest.mark.parametrize("key", ["db_user", "db_name"])
    @pytest.mark.parametrize("value", ['odoo"; DROP DATABASE postgres; --', "odoo prod", "9lives", "odoo_prod\n"])
    def test_a_name_that_is_not_an_identifier_stops_the_deploy(self, tmp_path, monkeypatch, key, value):
        ok, run = _deploy(tmp_path, monkeypatch, **{key: value})

        assert not ok
        assert run.calls == []


_STUBS = {
    "sleep": "#!/bin/sh\nexit 0\n",
    "docker": (
        "#!/bin/sh\n"
        'echo "docker $*" >> "$RKD_LOG"\n'
        'case "$*" in\n'
        # The real `compose up` and `compose logs` drain the stdin they
        # inherit, which is the script bash is reading.
        '  *"compose up"*|*"compose logs"*) cat > /dev/null 2>/dev/null ;;\n'
        '  *"odoo shell"*) cat >> "$RKD_LOG" ;;\n'
        "  *pg_isready*)\n"
        '    [ "$(grep -c pg_isready "$RKD_LOG")" -gt "${RKD_PG_FAILURES:-0}" ] || exit 1 ;;\n'
        '  *pg_database*) [ -n "$RKD_DB_EXISTS" ] && echo 1 ;;\n'
        '  *ir_module_module*) [ -n "$RKD_BASE_INSTALLED" ] && echo 1 ;;\n'
        '  *ir_config_parameter*) [ -n "$RKD_ADMIN_MARK" ] && echo 1 ;;\n'
        '  *createdb*) [ -z "$RKD_CREATEDB_FAILS" ] || exit 1 ;;\n'
        "  *curl*)\n"
        '    [ "$(grep -c curl "$RKD_LOG")" -gt "${RKD_CURL_FAILURES:-0}" ] || exit 1 ;;\n'
        "esac\n"
        "exit 0\n"
    ),
}


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs a POSIX shell")
class TestTheScriptsRun:
    """Both remote scripts, executed against stub binaries.

    Two bugs here were invisible to any assertion on the text: the script
    ending after `docker compose up -d db` because compose drank the rest of
    it, and the whole script ending on the first `pg_isready` of a container
    that was still starting.
    """

    def _scripts(self, tmp_path, monkeypatch) -> dict[str, str]:
        # Only the instance's own _ssh is replaced here, never
        # `deployer_docker.subprocess.run`: that attribute belongs to the
        # global subprocess module, and patching it would leave this harness
        # unable to run bash -- it would be handed the stub's answer instead.
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        # The scripts start by cd'ing into the remote directory, and `set -e`
        # ends them there if it does not exist.
        remote = tmp_path / "remote"
        (remote / "prod").mkdir(parents=True)
        deployer = deployer_docker.DockerInstanceDeployer("prod", _config(remote_path=str(remote)), tmp_path)
        sent = {}
        monkeypatch.setattr(
            deployer,
            "_ssh",
            lambda cmd, agent_forward=False, timeout=600, input=None: sent.update({len(sent): input}) or True,
        )
        deployer._ensure_database()
        deployer._verify_odoo_answers()
        return {"database": sent[0], "verify": sent[1]}

    def _run(self, tmp_path, script: str, **env_overrides) -> tuple[int, list[str]]:  # noqa: D401
        stubs = tmp_path / "stubs"
        stubs.mkdir(exist_ok=True)
        for name, body in _STUBS.items():
            path = stubs / name
            path.write_text(body)
            path.chmod(0o755)

        log = tmp_path / "commands.log"
        log.write_text("")
        env = {
            **os.environ,
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "RKD_LOG": str(log),
            **env_overrides,
        }
        result = subprocess.run(["bash", "-s"], input=script, env=env, text=True, capture_output=True)
        return result.returncode, log.read_text().splitlines()

    def test_the_whole_script_runs_even_though_compose_drinks_stdin(self, tmp_path, monkeypatch):
        code, log = self._run(tmp_path, self._scripts(tmp_path, monkeypatch)["database"])

        assert code == 0
        assert any("-i base" in line for line in log), "the script ended at `compose up`"
        assert any("odoo shell" in line for line in log)

    def test_a_container_that_is_not_ready_yet_is_waited_for(self, tmp_path, monkeypatch):
        """PostgreSQL is not accepting connections the instant its container
        starts, and the step right after needs it."""
        code, log = self._run(tmp_path, self._scripts(tmp_path, monkeypatch)["database"], RKD_PG_FAILURES="2")

        assert code == 0
        assert len([line for line in log if "pg_isready" in line]) == 3
        assert any("-i base" in line for line in log)

    def test_an_initialised_database_is_left_alone(self, tmp_path, monkeypatch):
        code, log = self._run(
            tmp_path,
            self._scripts(tmp_path, monkeypatch)["database"],
            RKD_DB_EXISTS="1",
            RKD_BASE_INSTALLED="1",
            RKD_ADMIN_MARK="1",
        )

        assert code == 0
        assert not any("createdb" in line or "-i base" in line or "odoo shell" in line for line in log)

    def test_a_database_left_empty_by_an_interrupted_deploy_is_initialised(self, tmp_path, monkeypatch):
        code, log = self._run(tmp_path, self._scripts(tmp_path, monkeypatch)["database"], RKD_DB_EXISTS="1")

        assert code == 0
        assert not any("createdb" in line for line in log)
        assert any("-i base" in line for line in log)

    def test_an_install_that_died_halfway_is_finished_next_time(self, tmp_path, monkeypatch):
        """Measured: Odoo commits ir_module_module about nine seconds into
        the install, with `base` still `to install` for minutes afterwards.
        A guard on the table alone calls that database done."""
        code, log = self._run(tmp_path, self._scripts(tmp_path, monkeypatch)["database"], RKD_DB_EXISTS="1")

        assert code == 0
        assert any("-i base" in line for line in log)

    def test_an_admin_password_that_failed_to_apply_is_retried(self, tmp_path, monkeypatch):
        code, log = self._run(
            tmp_path,
            self._scripts(tmp_path, monkeypatch)["database"],
            RKD_DB_EXISTS="1",
            RKD_BASE_INSTALLED="1",
        )

        assert code == 0
        assert not any("-i base" in line for line in log)
        assert any("odoo shell" in line for line in log)

    def test_a_failed_createdb_stops_before_installing_into_nothing(self, tmp_path, monkeypatch):
        code, log = self._run(tmp_path, self._scripts(tmp_path, monkeypatch)["database"], RKD_CREATEDB_FAILS="1")

        assert code != 0
        assert not any("-i base" in line for line in log)

    def test_the_admin_password_reaches_odoo_shell_by_stdin(self, tmp_path, monkeypatch):
        scripts = self._scripts(tmp_path, monkeypatch)
        _code, log = self._run(tmp_path, scripts["database"])
        password = _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD)

        invocation = next(line for line in log if "odoo shell" in line)
        assert password not in invocation
        assert any(f"'password': '{password}'" in line for line in log)

    def test_the_verification_passes_once_odoo_answers(self, tmp_path, monkeypatch):
        code, log = self._run(tmp_path, self._scripts(tmp_path, monkeypatch)["verify"], RKD_CURL_FAILURES="2")

        assert code == 0
        assert len([line for line in log if "curl" in line]) == 3

    def test_an_odoo_that_never_answers_fails_the_deploy_with_its_log(self, tmp_path, monkeypatch):
        """The `exit 1` sits after `docker compose logs`, which also drinks
        stdin: eaten, the step would report success."""
        code, log = self._run(tmp_path, self._scripts(tmp_path, monkeypatch)["verify"], RKD_CURL_FAILURES="999")

        assert code == 1
        assert any("compose logs" in line for line in log), "the operator is left without the reason"


class TestTheConnectionBudget:
    @pytest.mark.parametrize(
        ("max_connections", "workers", "expected"),
        [(50, 1, 15), (50, 2, 11), (200, 1, 65), (200, 2, 48), (100, 2, 23), (10, 4, 8)],
    )
    def test_each_process_gets_an_equal_share_with_a_floor(self, max_connections, workers, expected):
        assert config_manager.db_maxconn(max_connections, workers) == expected


class TestEnterprise:
    def _local(self, tmp_path):
        (tmp_path / "enterprise").mkdir()

    def test_rsync_sends_the_local_checkout_without_its_git_history(self, tmp_path, monkeypatch):
        self._local(tmp_path)
        ok, run = _deploy(tmp_path, monkeypatch, use_enterprise=True)
        assert ok
        calls = [a for a, _ in run.calls if a[0] == "rsync" and a[-1].endswith("/opt/odoo-prod/enterprise/")]
        assert calls
        assert "--exclude" in calls[0] and ".git/" in calls[0]
        assert calls[0][-2] == f"{tmp_path}/enterprise/"

    def test_clone_forwards_the_agent_and_pins_the_series(self, tmp_path, monkeypatch):
        ok, run = _deploy(tmp_path, monkeypatch, use_enterprise=True, enterprise_source="clone")
        assert ok
        args, kwargs = run.calls[run.index_of("git clone")]
        assert "-A" in args
        assert "git clone --depth 1 --branch 18.0 git@github.com:odoo/enterprise.git" in kwargs["input"]

    def test_a_series_written_without_the_minor_is_still_a_branch(self, tmp_path, monkeypatch):
        ok, run = _deploy(tmp_path, monkeypatch, use_enterprise=True, enterprise_source="clone", odoo_version="19")
        assert ok
        assert "--branch 19.0 " in run.script_with("git clone")

    def test_rsync_without_a_local_checkout_stops_before_touching_the_vps(self, tmp_path, monkeypatch):
        ok, run = _deploy(tmp_path, monkeypatch, use_enterprise=True)
        assert not ok
        assert not run.calls
        assert not [a for a, _ in run.calls if a[0] == "rsync" and a[-1].endswith("/opt/odoo-prod/enterprise/")]

    def test_nothing_is_synced_without_use_enterprise(self, tmp_path, monkeypatch):
        self._local(tmp_path)
        ok, run = _deploy(tmp_path, monkeypatch, enterprise_source="clone")
        assert ok
        assert not run.sent_containing("git clone")
        assert not run.sent_containing("enterprise/")

    def test_the_destination_is_created_before_anything_is_written_to_it(self, tmp_path, monkeypatch):
        self._local(tmp_path)
        _, run = _deploy(tmp_path, monkeypatch, use_enterprise=True)
        mkdir = run.index_of("/opt/odoo-prod/enterprise")
        assert mkdir < run.index_of("rsync")

    def test_the_build_context_excludes_what_is_mounted_at_runtime(self, tmp_path, monkeypatch):
        self._local(tmp_path)
        seen = {}
        recording = _RecordingRun()

        def run(args, **kwargs):
            if args[0] == "rsync" and args[-1].endswith("/.dockerignore"):
                seen["content"] = open(args[-2]).read()
            return recording(args, **kwargs)

        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        monkeypatch.setattr(deployer_docker.subprocess, "run", run)
        deployer = deployer_docker.DockerInstanceDeployer("prod", _config(use_enterprise=True), tmp_path)
        assert deployer.deploy()
        ignored = seen["content"].split()
        assert "enterprise/" in ignored
        assert "extra-addons/" in ignored
        assert "stage/" in ignored and "prod/" in ignored
        dockerfile = (deployer_docker._TEMPLATES_DIR / "Dockerfile.jinja").read_text()
        assert "COPY extra-addons" not in dockerfile


class TestGitman:
    GITMAN = (
        "location: external_addons\nsources:\n"
        "  - repo: https://github.com/ingadhoc/odoo-argentina.git\n    name: l10n_ar\n"
        "  - repo: https://github.com/OCA/web.git\n"
    )

    def _project(self, tmp_path, *, script=True):
        (tmp_path / "gitman.yml").write_text(self.GITMAN)
        if script:
            (tmp_path / "install_dependencies.sh").write_text("#!/bin/sh\n")

    def _render(self, tmp_path, tmp):
        deployer = deployer_docker.DockerInstanceDeployer(
            "prod", _config(use_gitman=True, gitman_config="gitman.yml"), tmp_path
        )
        deployer._render_templates(tmp)
        return (
            (tmp / "Dockerfile.prod").read_text(),
            (tmp / "prod" / "config" / "odoo.conf").read_text(),
        )

    def test_every_source_reaches_the_addons_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        self._project(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        _, conf = self._render(tmp_path, out)
        assert (
            "addons_path = /mnt/extra-addons,/opt/odoo/gitman/external_addons/l10n_ar,/opt/odoo/gitman/external_addons/web\n"
        ) in conf

    def test_the_dependency_script_is_copied_when_it_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        self._project(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        dockerfile, _ = self._render(tmp_path, out)
        assert "COPY install_dependencies.sh /usr/lib/python3/dist-packages/odoo/" in dockerfile
        assert (out / "install_dependencies.sh").exists()
        assert "/opt/odoo/gitman /etc/odoo" in dockerfile
        assert "COPY extra-addons" not in dockerfile

    def test_without_the_script_nothing_is_copied(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        self._project(tmp_path, script=False)
        out = tmp_path / "out"
        out.mkdir()
        dockerfile, _ = self._render(tmp_path, out)
        assert "install_dependencies" not in dockerfile
        assert not (out / "install_dependencies.sh").exists()

    def test_the_script_travels_with_the_gitman_file(self, tmp_path, monkeypatch):
        self._project(tmp_path)
        repos = ["https://github.com/ingadhoc/odoo-argentina.git", "https://github.com/OCA/web.git"]
        monkeypatch.setattr(deployer_docker.preflight, "remote_facts", lambda *a, **k: {**GREEN_REMOTE, "repo_ok": repos})
        _, run = _deploy(tmp_path, monkeypatch, use_gitman=True, gitman_config="gitman.yml")
        assert run.sent_containing("/opt/odoo-prod/install_dependencies.sh")
        assert run.sent_containing("/opt/odoo-prod/gitman.yml")

    def test_enterprise_leads_and_nested_addons_are_listed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        self._project(tmp_path)
        nested = tmp_path / "addons" / "oca" / "mod"
        nested.mkdir(parents=True)
        (nested / "__manifest__.py").write_text("{}")
        out = tmp_path / "out"
        out.mkdir()
        deployer = deployer_docker.DockerInstanceDeployer(
            "prod", _config(use_gitman=True, gitman_config="gitman.yml", use_enterprise=True), tmp_path
        )
        deployer._render_templates(out)
        conf = (out / "prod" / "config" / "odoo.conf").read_text()
        assert (
            "addons_path = /mnt/enterprise,/mnt/extra-addons,/mnt/extra-addons/oca,"
            "/opt/odoo/gitman/external_addons/l10n_ar,/opt/odoo/gitman/external_addons/web\n"
        ) in conf

    def test_the_host_keys_of_every_ssh_source_are_scanned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        (tmp_path / "gitman.yml").write_text("sources:\n  - repo: git@gitlab.com:g/a.git\n    name: a\n")
        out = tmp_path / "out"
        out.mkdir()
        dockerfile, _ = self._render(tmp_path, out)
        assert "ssh-keyscan github.com gitlab.com >>" in dockerfile


class TestTheBuildSshFlag:
    @staticmethod
    def _build_call(run):
        return run.sent_containing("docker compose build")[0]

    def _built(self, tmp_path, monkeypatch, gitman: str | None):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_docker.subprocess, "run", run)
        if gitman is not None:
            (tmp_path / "gitman.yml").write_text(gitman)
        deployer = deployer_docker.DockerInstanceDeployer(
            "prod", _config(use_gitman=gitman is not None, gitman_config="gitman.yml"), tmp_path
        )
        deployer._build()
        return self._build_call(run)

    def test_without_gitman_there_is_no_ssh_flag(self, tmp_path, monkeypatch):
        assert "--ssh" not in self._built(tmp_path, monkeypatch, None)

    def test_https_sources_need_no_ssh_flag(self, tmp_path, monkeypatch):
        gitman = "sources:\n  - repo: https://github.com/OCA/web.git\n"
        assert "--ssh" not in self._built(tmp_path, monkeypatch, gitman)

    def test_an_ssh_source_keeps_the_flag(self, tmp_path, monkeypatch):
        gitman = "sources:\n  - repo: git@github.com:org/repo.git\n"
        assert "--ssh default" in self._built(tmp_path, monkeypatch, gitman)

    def test_a_scp_style_source_without_a_user_needs_ssh(self, tmp_path):
        (tmp_path / "gitman.yml").write_text("sources:\n  - repo: github.com:org/repo\n")
        deployer = deployer_docker.DockerInstanceDeployer(
            "prod", _config(use_gitman=True, gitman_config="gitman.yml"), tmp_path
        )

        assert deployer._needs_ssh() is True

    def test_the_mount_follows_the_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        for repo, mounted in (("https://github.com/OCA/web.git", False), ("git@github.com:o/r.git", True)):
            project = tmp_path / ("ssh" if mounted else "https")
            out = project / "out"
            out.mkdir(parents=True)
            (project / "gitman.yml").write_text(f"sources:\n  - repo: {repo}\n")
            deployer = deployer_docker.DockerInstanceDeployer(
                "prod", _config(use_gitman=True, gitman_config="gitman.yml"), project
            )
            deployer._render_templates(out)

            assert ("--mount=type=ssh" in (out / "Dockerfile.prod").read_text()) is mounted


class TestThePublicUrlProbe:
    def test_it_runs_from_the_vps_after_the_services_start_with_both_schemes(self, tmp_path, monkeypatch):
        ok, run = _deploy(tmp_path, monkeypatch)

        script = run.script_with("--max-time 10")
        assert ok
        assert "http https" in script
        assert "erp.example.com" in script
        assert run.index_of("--max-time 10") > run.index_of("web/login")

    def test_it_is_braced_like_every_remote_script(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)

        script = run.script_with("--max-time 10")
        assert script.startswith("{\n") and script.endswith("}\n")

    def test_no_domain_means_no_probe(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch, domain="")

        assert not run.sent_containing("--max-time 10")

    def test_a_silent_url_never_fails_the_deploy_but_is_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        monkeypatch.setattr(deployer_docker.subprocess, "run", _RecordingRun())
        deployer = deployer_docker.DockerInstanceDeployer("prod", _config(), tmp_path)

        assert deployer.deploy() is True
        probe = [c for c in deployer.checks if c["name"] == "public url"]
        assert probe and probe[0]["ok"] is False
