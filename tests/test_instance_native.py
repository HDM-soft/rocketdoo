"""What a native `rkd instance deploy` actually sends to the VPS (#248).

The native path never created a PostgreSQL role: the Debian package creates
one named `odoo`, the wizard's default user is `odoo_{env}`, and nothing in
between created it -- so the deploy reported success and left an Odoo that
could not authenticate. With `list_db = False` there was no database either,
and no database manager to make one from.

These tests capture the commands the deployer would run, which is how that
was found. Most need no VPS: subprocess.run is replaced and the assertion is
about what would have been sent. Two groups do run something, because text
assertions only prove text: the remote script runs under bash against stub
binaries, and the role statement runs against a real PostgreSQL.
"""

import os
import shutil
import stat
import subprocess
import time
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("green_preflight")
import yaml
from click.testing import CliRunner

from rocketdoo.cli import main
from rocketdoo.core.instance import deployer_native, secrets_store, ssh_utils


class _RecordingRun:
    """subprocess.run stand-in that records every call and never connects."""

    def __init__(self, fail_on: str = ""):
        self.calls = []
        self.fail_on = fail_on

    @staticmethod
    def _haystack(args, kwargs) -> str:
        """Argv plus stdin: two steps share the command `bash -s` and are
        told apart only by the script they carry."""
        return " ".join(str(a) for a in args) + "\n" + (kwargs.get("input") or "")

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        failed = self.fail_on and self.fail_on in self._haystack(args, kwargs)
        return subprocess.CompletedProcess(args, returncode=1 if failed else 0, stdout="", stderr="")

    def index_of(self, needle: str) -> int:
        """Position in the full call list, for ordering assertions."""
        for i, (args, kwargs) in enumerate(self.calls):
            if needle in self._haystack(args, kwargs):
                return i
        raise AssertionError(f"no call contained {needle!r}")

    def command(self, needle: str) -> str:
        """The remote command string of the ssh call that carried `needle`.

        Looked up by identity rather than by indexing a filtered list with an
        unfiltered index, which silently returns a neighbouring command once
        a non-ssh call lands before it.
        """
        args, _kwargs = self.calls[self.index_of(needle)]
        assert args[0] == "ssh", f"{needle!r} was not sent over ssh: {args[0]}"
        return args[-1]

    def stdin_of(self, needle: str) -> str:
        return self.calls[self.index_of(needle)][1].get("input") or ""

    def commands_containing(self, needle: str) -> list[str]:
        return [args[-1] for args, _ in self.calls if args[0] == "ssh" and needle in args[-1]]

    def sent_containing(self, needle: str) -> list[str]:
        """Calls carrying `needle` in their command or in their stdin."""
        return [self._haystack(args, kwargs) for args, kwargs in self.calls if needle in self._haystack(args, kwargs)]


def _config(**overrides) -> dict:
    cfg = {
        "type": "native",
        "vps": {
            "host": "vps.example.com",
            "user": "ubuntu",
            "port": 22,
            "auth_method": "ssh_key",
            "ssh_key": "~/.ssh/id_rsa",
        },
        "odoo_version": "18.0",
        "domain": "erp.example.com",
        "db_user": "odoo_prod",
        "remote_path": "/opt/odoo-prod",
    }
    cfg.update(overrides)
    return cfg


def _deploy(tmp_path, monkeypatch, *, fail_on: str = "", **overrides) -> tuple[bool, _RecordingRun]:
    (tmp_path / "addons").mkdir(exist_ok=True)
    monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
    run = _RecordingRun(fail_on=fail_on)
    monkeypatch.setattr(deployer_native.subprocess, "run", run)
    deployer = deployer_native.NativeInstanceDeployer("prod", _config(**overrides), tmp_path)
    return deployer.deploy(), run


def _stored(tmp_path, key: str) -> str:
    return secrets_store.load(tmp_path, "prod")[key]


class TestTheDatabaseRole:
    def test_the_role_is_created_before_the_conf_that_authenticates_with_it(self, tmp_path, monkeypatch):
        ok, run = _deploy(tmp_path, monkeypatch)

        assert ok
        assert run.index_of("psql") < run.index_of("tee /etc/odoo/odoo.conf")

    def test_an_existing_role_is_realigned_instead_of_failing(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        sql = run.stdin_of("psql")

        # A bare CREATE ROLE errors out on the second deploy, and on the role
        # the Debian package already created when db_user is left as `odoo`.
        assert "FROM pg_roles WHERE rolname = 'odoo_prod'" in sql
        assert 'ALTER ROLE "odoo_prod"' in sql
        assert 'CREATE ROLE "odoo_prod"' in sql

    def test_the_operator_is_told_which_of_the_two_happened(self, tmp_path, monkeypatch):
        """Realigning touches a role that may predate this project."""
        _, run = _deploy(tmp_path, monkeypatch)
        sql = run.stdin_of("psql")

        assert sql.count("RAISE NOTICE") == 2

    def test_the_role_may_log_in_and_create_databases(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        sql = run.stdin_of("psql")

        assert "LOGIN CREATEDB" in sql
        assert "SUPERUSER" not in sql

    def test_the_password_never_becomes_a_command_argument(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        password = _stored(tmp_path, secrets_store.PG_PASS)

        for args, _kwargs in run.calls:
            assert all(password not in str(item) for item in args)
        assert password in run.stdin_of("psql")

    def test_the_conf_declares_the_password_the_role_was_given(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        password = _stored(tmp_path, secrets_store.PG_PASS)

        assert f"db_password = {password}\n" in run.stdin_of("tee /etc/odoo/odoo.conf")
        assert f"'{password}'" in run.stdin_of("psql")

    def test_a_second_deploy_keeps_the_password_the_role_already_has(self, tmp_path, monkeypatch):
        _deploy(tmp_path, monkeypatch)
        first = _stored(tmp_path, secrets_store.PG_PASS)

        _, run = _deploy(tmp_path, monkeypatch)

        assert _stored(tmp_path, secrets_store.PG_PASS) == first
        assert f"'{first}'" in run.stdin_of("psql")

    def test_a_quote_in_a_stored_password_is_escaped_for_sql(self, tmp_path, monkeypatch):
        """A hand-edited secret file is the only way to get one, and an
        unescaped quote would end the literal and run the rest as SQL."""
        secrets_store.save(tmp_path, "prod", {secrets_store.PG_PASS: "p4ss'word"})

        _, run = _deploy(tmp_path, monkeypatch)

        assert "PASSWORD 'p4ss''word'" in run.stdin_of("psql")

    def test_the_stored_password_is_readable_only_by_its_owner(self, tmp_path, monkeypatch):
        _deploy(tmp_path, monkeypatch)
        mode = secrets_store.secrets_file(tmp_path, "prod").stat().st_mode

        assert not mode & (stat.S_IRGRP | stat.S_IROTH)


class TestTheAdminUser:
    """Installing `base` leaves `admin` with the password `admin`, and
    `list_db = False` disables the only code that would replace it."""

    def test_the_admin_password_is_replaced_when_the_database_is_created(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.stdin_of("bash -s")
        password = _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD)

        assert "odoo shell" in script
        assert f"env.ref('base.user_admin').write({{'password': '{password}'}})" in script
        assert "env.cr.commit()" in script

    def test_it_is_set_behind_its_own_guard(self, tmp_path, monkeypatch):
        """Resetting it on every deploy would undo the operator's own change,
        and hanging it off the install's guard would skip it for good when
        the install succeeded and this step did not."""
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.stdin_of("bash -s")

        assert script.index(deployer_native._ADMIN_PASSWORD_MARK) < script.index("odoo shell")
        assert f"set_param('{deployer_native._ADMIN_PASSWORD_MARK}', '1')" in script
        assert script.rstrip().endswith("fi")

    def test_it_is_not_the_master_password(self, tmp_path, monkeypatch):
        """With list_db = False the master password guards nothing, so
        handing it over as the login would be worse than useless."""
        _, run = _deploy(tmp_path, monkeypatch)

        login = _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD)
        master = _stored(tmp_path, secrets_store.ADMIN_PASSWD)
        assert login != master
        assert f"admin_passwd = {master}\n" in run.stdin_of("tee /etc/odoo/odoo.conf")

    def test_it_never_becomes_a_command_argument(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        password = _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD)

        for args, _kwargs in run.calls:
            assert all(password not in str(item) for item in args)

    def test_a_second_deploy_keeps_the_one_already_handed_out(self, tmp_path, monkeypatch):
        _deploy(tmp_path, monkeypatch)
        first = _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD)

        _deploy(tmp_path, monkeypatch)

        assert _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD) == first


class TestTheDatabase:
    def test_the_database_is_created_and_base_installed(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.stdin_of("bash -s")

        assert 'createdb -E UTF8 -T template0 --lc-collate=C -O "odoo_prod" "odoo_prod"' in script
        assert "-i base" in script
        assert "--stop-after-init" in script

    def test_the_database_is_not_born_with_the_clusters_encoding(self, tmp_path, monkeypatch):
        """Odoo's own CREATE DATABASE uses UTF8 from template0 on purpose: a
        cluster that came up SQL_ASCII would corrupt every translation."""
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.stdin_of("bash -s")

        assert "-E UTF8" in script
        assert "-T template0" in script
        assert "--lc-collate=C" in script

    def test_the_install_cannot_race_the_service_the_package_started(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.stdin_of("bash -s")

        assert "systemctl stop odoo" in script
        assert script.index("systemctl stop odoo") < script.index("-i base")
        # Without --workers=0 the prefork server binds 8069 before loading,
        # and collides with the service it just stopped once it comes back.
        assert "--workers=0" in script
        assert "--max-cron-threads=0" in script

    def test_a_live_instance_is_not_stopped_by_a_redeploy(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.stdin_of("bash -s")

        # The stop lives inside the install's guard, not at the top of the
        # script: a redeploy that has nothing to install must not take a live
        # instance down before the restart.
        assert script.index("state = 'installed'") < script.index("systemctl stop odoo")

    def test_the_install_reports_where_it_failed(self, tmp_path, monkeypatch):
        """Its output would otherwise go to the server logfile the conf sets,
        leaving a half-hour step that fails with one line of console."""
        _, run = _deploy(tmp_path, monkeypatch)

        # The line itself, not the script: the `odoo shell` line carries the
        # same flag, so looking anywhere in the script proves nothing. The
        # value is empty, not /dev/stdout: that one makes Odoo print "ERROR:
        # couldn't create the logfile directory" before logging to the same
        # place anyway.
        install = next(line for line in run.stdin_of("bash -s").splitlines() if "-i base" in line)
        assert " --logfile= " in f"{install} "
        assert "/dev/stdout" not in install

    def test_neither_half_runs_again_on_a_redeploy(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        script = run.stdin_of("bash -s")

        # Guarded separately on purpose: a database created by an attempt that
        # died during the install must still get initialised next time.
        assert "FROM pg_database WHERE datname = 'odoo_prod'" in script
        assert "FROM ir_module_module WHERE name = 'base' AND state = 'installed'" in script
        assert f"WHERE key IN ('{deployer_native._ADMIN_PASSWORD_MARK}', '{deployer_native.SEEDED_MARK}')" in script

    def test_the_database_name_defaults_to_the_role_name(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch, db_user="odoo_prod")

        assert "db_name = odoo_prod\n" in run.stdin_of("tee /etc/odoo/odoo.conf")

    def test_a_configured_database_name_is_the_one_served_and_created(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch, db_name="erp")

        assert "db_name = erp\n" in run.stdin_of("tee /etc/odoo/odoo.conf")
        assert '-O "odoo_prod" "erp"' in run.stdin_of("bash -s")


class TestTheConf:
    def test_the_conf_is_not_left_world_readable(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        command = run.command("tee /etc/odoo/odoo.conf")

        # It carries the master password and now the database one too.
        assert "chmod 640 /etc/odoo/odoo.conf" in command
        assert "chown odoo: /etc/odoo/odoo.conf" in command

    def test_proxy_mode_is_off_unless_a_proxy_is_configured(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)

        assert "proxy_mode = False\n" in run.stdin_of("tee /etc/odoo/odoo.conf")

    def test_proxy_mode_can_be_turned_on_for_a_deploy_that_has_one(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch, proxy_mode=True)

        assert "proxy_mode = True\n" in run.stdin_of("tee /etc/odoo/odoo.conf")


class TestTheConnectionBudget:
    def test_the_pool_fits_a_stock_debian_cluster(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)
        conf = run.stdin_of("tee /etc/odoo/odoo.conf")

        assert "workers = 2\n" in conf
        assert "db_maxconn = 23\n" in conf
        assert "max_cron_threads = 1\n" in conf


class TestTheSequence:
    def test_each_step_runs_after_the_one_it_depends_on(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)

        order = [
            run.index_of("apt-get install -y odoo"),
            run.index_of("psql"),
            run.index_of("tee /etc/odoo/odoo.conf"),
            run.index_of("rsync"),
            run.index_of("bash -s"),
            run.index_of("systemctl enable"),
            run.index_of("web/login"),
        ]
        assert order == sorted(order)

    def test_odoo_is_installed_once(self, tmp_path, monkeypatch):
        _, run = _deploy(tmp_path, monkeypatch)

        assert len(run.commands_containing("apt-get install -y odoo")) == 1

    def test_the_deploy_is_not_called_a_success_until_odoo_answers(self, tmp_path, monkeypatch):
        """`systemctl restart` returns as soon as the process forks, so it
        succeeds for an Odoo that dies on its database a second later."""
        ok, run = _deploy(tmp_path, monkeypatch, fail_on="web/login")

        assert not ok
        assert run.index_of("web/login") > run.index_of("systemctl enable")

    def test_the_check_runs_on_a_redeploy_too(self, tmp_path, monkeypatch):
        """It is the only end-to-end proof left once the database exists."""
        _deploy(tmp_path, monkeypatch)
        _, run = _deploy(tmp_path, monkeypatch)

        assert run.sent_containing("web/login")

    def test_a_failed_step_stops_the_deploy_before_the_rest(self, tmp_path, monkeypatch):
        ok, run = _deploy(tmp_path, monkeypatch, fail_on="psql")

        assert not ok
        assert not run.commands_containing("tee /etc/odoo/odoo.conf")

    def test_a_timeout_fails_the_deploy_instead_of_raising(self, tmp_path, monkeypatch):
        def timing_out(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 0))

        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        monkeypatch.setattr(deployer_native.subprocess, "run", timing_out)
        deployer = deployer_native.NativeInstanceDeployer("prod", _config(), tmp_path)

        assert deployer.deploy() is False

    def test_the_dry_run_lists_the_steps_the_deploy_runs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_native.subprocess, "run", run)
        deployer = deployer_native.NativeInstanceDeployer("prod", _config(), tmp_path)

        with deployer_native.console.capture() as planned:
            assert deployer.deploy(dry_run=True)
        assert not run.calls

        (tmp_path / "addons").mkdir(exist_ok=True)
        with deployer_native.console.capture() as executed:
            deployer.deploy()

        # Compared against what the real run announces, not against the list
        # the plan was printed from, which cannot disagree with itself.
        for label, _fn in deployer._steps():
            head = label.split("→")[0].strip()[:40]
            assert head in planned.get(), f"the dry run never mentions {head!r}"
            assert head in executed.get()


class TestIdentifierValidation:
    """db_user and db_name reach an SQL statement and a remote shell."""

    @pytest.mark.parametrize("key", ["db_user", "db_name"])
    @pytest.mark.parametrize(
        "value",
        [
            'odoo"; DROP DATABASE postgres; --',
            "odoo prod",
            "odoo'; --",
            "$(touch /tmp/pwn)",
            "9lives",
            # `$` matches before a trailing newline, so this one splits the
            # remote script into two commands, the first of them an Odoo
            # server without --stop-after-init.
            "odoo_prod\n",
        ],
    )
    def test_a_name_that_is_not_an_identifier_stops_the_deploy(self, tmp_path, monkeypatch, key, value):
        ok, run = _deploy(tmp_path, monkeypatch, **{key: value})

        assert not ok
        assert run.calls == []

    def test_an_underscored_name_is_accepted(self, tmp_path, monkeypatch):
        ok, _run = _deploy(tmp_path, monkeypatch, db_user="_odoo_19_prod")

        assert ok

    @pytest.mark.parametrize("key", ["db_user", "db_name"])
    def test_an_empty_name_falls_back_to_the_default_rather_than_failing(self, tmp_path, monkeypatch, key):
        """An environment written before these keys existed has neither."""
        ok, run = _deploy(tmp_path, monkeypatch, **{key: ""})

        assert ok
        assert "db_user = odoo_prod\n" in run.stdin_of("tee /etc/odoo/odoo.conf")
        assert "db_name = odoo_prod\n" in run.stdin_of("tee /etc/odoo/odoo.conf")


_STUBS = {
    "sudo": '#!/bin/sh\nif [ "$1" = "-u" ]; then shift 2; fi\nexec "$@"\n',
    "systemctl": '#!/bin/sh\necho "systemctl $*" >> "$RKD_LOG"\n',
    "createdb": ('#!/bin/sh\necho "createdb $*" >> "$RKD_LOG"\n[ -z "$RKD_CREATEDB_FAILS" ]\n'),
    # Fails for the first $RKD_CURL_FAILURES calls, so the retry loop is
    # exercised instead of asserted.
    "curl": (
        "#!/bin/sh\n"
        'echo "curl $*" >> "$RKD_LOG"\n'
        'attempts=$(grep -c "^curl " "$RKD_LOG")\n'
        '[ "$attempts" -gt "${RKD_CURL_FAILURES:-0}" ]\n'
    ),
    "sleep": "#!/bin/sh\nexit 0\n",
    "tail": '#!/bin/sh\necho "tail $*" >> "$RKD_LOG"\n',
    # Reads whatever reaches its stdin, so a missing redirection shows up as
    # the rest of the script being eaten instead of executed.
    "odoo": (
        '#!/bin/sh\necho "odoo $*" >> "$RKD_LOG"\nwhile IFS= read -r line; do echo "odoo-stdin: $line" >> "$RKD_LOG"; done\n'
    ),
    "psql": (
        "#!/bin/sh\n"
        'echo "psql argc=$#" >> "$RKD_LOG"\n'
        'case "$*" in\n'
        '  *pg_database*) [ -n "$RKD_DB_EXISTS" ] && echo 1 ;;\n'
        '  *ir_module_module*) [ -n "$RKD_BASE_INSTALLED" ] && echo 1 ;;\n'
        '  *ir_config_parameter*) [ -n "$RKD_ADMIN_MARK" ] && echo 1 ;;\n'
        "esac\n"
        "exit 0\n"
    ),
}


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs a POSIX shell")
class TestTheRemoteScriptRuns:
    """The database step, executed for real against stub binaries.

    Its SQL sits inside a shell test inside an SSH command string; asserting
    on the text only proves the text. Running it proves psql receives one
    argument, that each half is skipped when its work is already done, and
    that no command swallows the script it is being read from.
    """

    def _run_script(self, tmp_path, monkeypatch, **state) -> tuple[int, list[str]]:
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_native.NativeInstanceDeployer("prod", _config(), tmp_path)
        sent = {}
        monkeypatch.setattr(deployer, "_ssh", lambda cmd, timeout=300, input=None: sent.update(script=input) or True)
        deployer._ensure_database()

        stubs = tmp_path / "stubs"
        stubs.mkdir()
        for name, body in _STUBS.items():
            path = stubs / name
            path.write_text(body)
            path.chmod(0o755)

        log = tmp_path / "commands.log"
        env = {
            **os.environ,
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "RKD_LOG": str(log),
            "RKD_DB_EXISTS": "1" if state.get("db_exists") else "",
            "RKD_BASE_INSTALLED": "1" if state.get("base_installed") else "",
            "RKD_ADMIN_MARK": "1" if state.get("admin_mark") else "",
            "RKD_CREATEDB_FAILS": "1" if state.get("createdb_fails") else "",
        }
        result = subprocess.run(["bash", "-s"], input=sent["script"], env=env, text=True, capture_output=True)
        return result.returncode, log.read_text().splitlines() if log.exists() else []

    def test_a_fresh_server_gets_the_database_created_and_initialised(self, tmp_path, monkeypatch):
        code, log = self._run_script(tmp_path, monkeypatch)

        assert code == 0
        assert any(line.startswith("createdb") for line in log)
        assert any("-i base" in line for line in log)

    def test_an_initialised_database_is_left_alone(self, tmp_path, monkeypatch):
        code, log = self._run_script(tmp_path, monkeypatch, db_exists=True, base_installed=True, admin_mark=True)

        assert code == 0
        assert not any(line.startswith(("createdb", "odoo", "systemctl")) for line in log)

    def test_a_database_left_empty_by_an_interrupted_deploy_is_initialised(self, tmp_path, monkeypatch):
        code, log = self._run_script(tmp_path, monkeypatch, db_exists=True)

        assert code == 0
        assert not any(line.startswith("createdb") for line in log)
        assert any("-i base" in line for line in log)

    def test_an_install_that_died_halfway_is_finished_next_time(self, tmp_path, monkeypatch):
        """Odoo commits ir_module_module seconds into the install and
        finishes minutes later. Measured: at that point `base` is still `to
        install`, so a guard on the table alone calls a half-built database
        done -- and leaves `admin` with the password `base` ships with."""
        code, log = self._run_script(tmp_path, monkeypatch, db_exists=True, base_installed=False)

        assert code == 0
        assert any("-i base" in line for line in log)

    def test_an_admin_password_that_failed_to_apply_is_retried(self, tmp_path, monkeypatch):
        """The install can succeed and this step fail; without its own mark
        the next run would skip it for good."""
        code, log = self._run_script(tmp_path, monkeypatch, db_exists=True, base_installed=True)

        assert code == 0
        assert not any("-i base" in line for line in log)
        assert any("odoo shell" in line for line in log)

    def test_a_failed_createdb_stops_before_installing_into_nothing(self, tmp_path, monkeypatch):
        code, log = self._run_script(tmp_path, monkeypatch, createdb_fails=True)

        assert code != 0
        assert not any("-i base" in line for line in log)

    def test_every_step_after_the_install_still_runs(self, tmp_path, monkeypatch):
        """bash feeds the script through the same stdin the commands inherit,
        and the `odoo` stub reads stdin on purpose: a command that swallows
        the script leaves the steps after it unexecuted.

        What this cannot show is the `< /dev/null` on the install line, which
        is why it is asserted as text below: bash reads the whole `if` block
        before running any of it, so today the guard, not the redirection, is
        what keeps the stream intact.
        """
        code, log = self._run_script(tmp_path, monkeypatch)

        assert code == 0
        assert any("-i base" in line for line in log)
        assert any("odoo shell" in line for line in log), "the step after the install never ran"

    def test_the_install_does_not_read_the_scripts_stream(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_native.NativeInstanceDeployer("prod", _config(), tmp_path)
        sent = {}
        monkeypatch.setattr(deployer, "_ssh", lambda cmd, timeout=300, input=None: sent.update(script=input) or True)
        deployer._ensure_database()

        install = next(line for line in sent["script"].splitlines() if "-i base" in line)
        assert install.endswith("< /dev/null")

    def test_the_admin_password_reaches_odoo_shell_by_stdin(self, tmp_path, monkeypatch):
        _code, log = self._run_script(tmp_path, monkeypatch)
        password = _stored(tmp_path, secrets_store.ADMIN_LOGIN_PASSWD)

        shell = next(line for line in log if "odoo shell" in line)
        assert password not in shell
        assert any(f"odoo-stdin: env.ref('base.user_admin').write({{'password': '{password}'}})" == line for line in log)

    def _run_check(self, tmp_path, monkeypatch, *, failures: int) -> tuple[int, list[str]]:
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_native.NativeInstanceDeployer("prod", _config(), tmp_path)
        sent = {}
        monkeypatch.setattr(deployer, "_ssh", lambda cmd, timeout=300, input=None: sent.update(script=input) or True)
        deployer._verify_odoo_answers()

        stubs = tmp_path / "stubs"
        stubs.mkdir()
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
            "RKD_CURL_FAILURES": str(failures),
        }
        result = subprocess.run(["bash", "-s"], input=sent["script"], env=env, text=True, capture_output=True)
        return result.returncode, log.read_text().splitlines()

    def test_the_check_waits_for_odoo_instead_of_asking_once(self, tmp_path, monkeypatch):
        """Odoo takes seconds to answer after a restart; a single attempt
        would fail every deploy."""
        code, log = self._run_check(tmp_path, monkeypatch, failures=2)

        assert code == 0
        assert len([line for line in log if line.startswith("curl")]) == 3

    def test_an_odoo_that_never_answers_fails_the_deploy_with_its_log(self, tmp_path, monkeypatch):
        code, log = self._run_check(tmp_path, monkeypatch, failures=99)

        assert code == 1
        assert any("odoo-server.log" in line for line in log), "the operator is left without the reason"

    def test_psql_receives_the_statement_as_a_single_argument(self, tmp_path, monkeypatch):
        _code, log = self._run_script(tmp_path, monkeypatch)

        # Counted, not echoed: `echo "$*"` renders one argument and nine the
        # same way, so it would pass either way.
        assert log[0] == "psql argc=2"


@pytest.mark.docker
class TestTheRoleStatementAgainstPostgres:
    """The DO block, run twice against a real server.

    Three `assert ... in sql` would pass just as happily for SQL that
    PostgreSQL rejects, and the statement's whole job is to be idempotent.
    """

    @pytest.fixture
    def postgres(self, requires_docker):
        name = f"rkd-test-pg-{uuid.uuid4().hex[:8]}"
        subprocess.run(
            ["docker", "run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD=x", "postgres:16"],
            check=True,
            capture_output=True,
        )
        try:
            # Over TCP, and not through the unix socket: the entrypoint runs
            # a temporary server during initdb with `listen_addresses=''`,
            # which answers on the socket and then goes away. A probe on the
            # socket therefore succeeds against a server about to vanish, and
            # the test's next statement gets "the database system is shutting
            # down". Only the real server ever listens on TCP.
            for _ in range(60):
                ready = subprocess.run(
                    ["docker", "exec", name, "psql", "-U", "postgres", "-h", "127.0.0.1", "-tAc", "SELECT 1"],
                    capture_output=True,
                )
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                logs = subprocess.run(["docker", "logs", "--tail", "30", name], capture_output=True, text=True)
                pytest.fail(f"the PostgreSQL container never became ready:\n{logs.stdout}\n{logs.stderr}")
            yield name
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    def _sql(self, tmp_path, monkeypatch) -> str:
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        deployer = deployer_native.NativeInstanceDeployer("prod", _config(), tmp_path)
        sent = {}
        monkeypatch.setattr(deployer, "_ssh", lambda cmd, timeout=300, input=None: sent.update(sql=input) or True)
        deployer._ensure_db_role()
        return sent["sql"]

    def test_the_statement_runs_twice_and_leaves_a_usable_role(self, tmp_path, monkeypatch, postgres):
        sql = self._sql(tmp_path, monkeypatch)
        psql = ["docker", "exec", "-i", postgres, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]

        for attempt in ("create", "realign"):
            result = subprocess.run(psql, input=sql, text=True, capture_output=True)
            assert result.returncode == 0, f"{attempt} failed: {result.stderr}"

        attributes = subprocess.run(
            [
                "docker",
                "exec",
                postgres,
                "psql",
                "-U",
                "postgres",
                "-tAc",
                "SELECT rolcanlogin, rolcreatedb, rolsuper FROM pg_roles WHERE rolname = 'odoo_prod'",
            ],
            text=True,
            capture_output=True,
        )
        assert attributes.stdout.strip() == "t|t|f"

    def test_the_role_authenticates_over_tcp_with_the_password_it_was_given(self, tmp_path, monkeypatch, postgres):
        """The conf declares this same password with db_host = localhost, so
        a role created without one would fail at the first connection."""
        secrets_store.save(tmp_path, "prod", {secrets_store.PG_PASS: "p4ss'word"})
        sql = self._sql(tmp_path, monkeypatch)
        subprocess.run(
            ["docker", "exec", "-i", postgres, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"],
            input=sql,
            text=True,
            capture_output=True,
            check=True,
        )

        login = subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                "PGPASSWORD=p4ss'word",
                postgres,
                "psql",
                "-h",
                "127.0.0.1",
                "-U",
                "odoo_prod",
                "-d",
                "postgres",
                "-tAc",
                "SELECT 1",
            ],
            text=True,
            capture_output=True,
        )
        assert login.returncode == 0, login.stderr
        assert login.stdout.strip() == "1"


class TestTheSuccessPanel:
    """What the operator is told once the deploy returns.

    The panel used to promise `https://{domain}` and a Let's Encrypt
    certificate for every deployment type. A native deploy puts no proxy in
    front of Odoo (#211), so that URL answers nothing -- and now that the
    deploy leaves a working instance, the wrong address is the only thing
    between the operator and it.
    """

    def _run(self, tmp_path, monkeypatch, *, dep_type, success=True):
        config = {
            "environments": {
                "prod": {
                    "type": dep_type,
                    "vps": {"host": "vps.example.com", "user": "ubuntu", "auth_method": "ssh_key", "ssh_key": "~/.ssh/id_rsa"},
                    "domain": "erp.example.com",
                    "remote_path": "/opt/odoo-prod",
                }
            }
        }
        (tmp_path / ".rkd").mkdir(exist_ok=True)
        (tmp_path / ".rkd" / "instance.yaml").write_text(yaml.safe_dump(config))
        monkeypatch.chdir(tmp_path)

        class _Deployer:
            def __init__(self, *args, **kwargs):
                pass

            def deploy(self, dry_run=False):
                return success

        monkeypatch.setattr(deployer_native, "NativeInstanceDeployer", _Deployer)
        monkeypatch.setattr("rocketdoo.core.instance.deployer_docker.DockerInstanceDeployer", _Deployer, raising=False)
        return CliRunner().invoke(main, ["instance", "deploy", "--env", "prod", "--yes"])

    def test_a_native_deploy_is_announced_at_the_address_that_answers(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, dep_type="native")

        assert result.exit_code == 0
        output = result.output.replace("\n", "")
        assert "http://vps.example.com:8069" in output
        assert "https://erp.example.com" not in output
        assert "Let's Encrypt" not in output

    def test_a_native_deploy_says_where_the_admin_password_is(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, dep_type="native")

        assert "instance_prod_db.env" in result.output.replace("\n", "")

    def test_a_docker_deploy_still_gets_its_domain(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, dep_type="docker")

        assert "https://erp.example.com" in result.output.replace("\n", "")

    def test_both_deployment_types_say_where_the_admin_password_is(self, tmp_path, monkeypatch):
        """It is generated, not chosen, so the panel is where the operator
        finds out it exists at all."""
        for dep_type in ("native", "docker"):
            result = self._run(tmp_path, monkeypatch, dep_type=dep_type)

            assert "instance_prod_db.env" in result.output.replace("\n", ""), dep_type

    def test_a_failed_deploy_promises_nothing(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, dep_type="native", success=False)

        output = result.output.replace("\n", "")
        assert "Deployment failed" in output
        assert "8069" not in output


class TestEnterprise:
    def _local(self, tmp_path):
        (tmp_path / "enterprise").mkdir()

    def test_rsync_sends_the_local_checkout_without_its_git_history(self, tmp_path, monkeypatch):
        self._local(tmp_path)
        ok, run = _deploy(tmp_path, monkeypatch, use_enterprise=True)
        assert ok
        calls = [a for a, _ in run.calls if a[0] == "rsync" and a[-1].endswith("/opt/odoo/enterprise/")]
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
        assert "--branch 19.0 " in run.stdin_of("git clone")

    def test_rsync_without_a_local_checkout_stops_before_touching_the_vps(self, tmp_path, monkeypatch):
        ok, run = _deploy(tmp_path, monkeypatch, use_enterprise=True)
        assert not ok
        assert not run.calls
        assert not [a for a, _ in run.calls if a[0] == "rsync" and a[-1].endswith("/opt/odoo/enterprise/")]

    def test_nothing_is_synced_without_use_enterprise(self, tmp_path, monkeypatch):
        self._local(tmp_path)
        ok, run = _deploy(tmp_path, monkeypatch, enterprise_source="clone")
        assert ok
        assert not run.sent_containing("git clone")
        assert not run.sent_containing("enterprise/")

    def test_the_destination_is_created_with_the_helper_that_survives_a_root_parent(self, tmp_path, monkeypatch):
        self._local(tmp_path)
        _, run = _deploy(tmp_path, monkeypatch, use_enterprise=True)
        command = run.command("mkdir -p /opt/odoo/enterprise")
        assert "sudo -n mkdir -p /opt/odoo/enterprise" in command

    def test_the_conf_points_at_the_directory_that_was_filled(self, tmp_path, monkeypatch):
        self._local(tmp_path)
        _, run = _deploy(tmp_path, monkeypatch, use_enterprise=True)
        assert "addons_path = /opt/odoo/enterprise,/opt/odoo-prod" in run.stdin_of("addons_path")

    def test_the_plan_does_not_mention_enterprise_when_it_is_off(self, tmp_path):
        deployer = deployer_native.NativeInstanceDeployer("prod", _config(), tmp_path)
        assert not [label for label, _ in deployer._steps() if "Enterprise" in label]

    def test_an_empty_odoo_version_fails_the_step_instead_of_guessing_a_branch(self, tmp_path, monkeypatch):
        ok, run = _deploy(tmp_path, monkeypatch, use_enterprise=True, enterprise_source="clone", odoo_version="")
        assert not ok
        assert not run.sent_containing("git clone")


class TestTheDeployExitCode:
    """A deploy that failed has to be distinguishable by a script (#292).

    `rkd instance deploy --dry-run && rkd instance deploy` used to run the
    second half even when the dry-run had found blocking problems: every path
    of the command returned, and a bare `return` exits 0.
    """

    def _invoke(self, tmp_path, monkeypatch, *, success, dry_run=False, env="prod", config=True):
        if config:
            data = {
                "environments": {
                    "prod": {
                        "type": "native",
                        "vps": {
                            "host": "vps.example.com",
                            "user": "ubuntu",
                            "auth_method": "ssh_key",
                            "ssh_key": "~/.ssh/id_rsa",
                        },
                        "remote_path": "/opt/odoo-prod",
                    }
                }
            }
            (tmp_path / ".rkd").mkdir(exist_ok=True)
            (tmp_path / ".rkd" / "instance.yaml").write_text(yaml.safe_dump(data))
        monkeypatch.chdir(tmp_path)

        class _Deployer:
            checks: list = []

            def __init__(self, *args, **kwargs):
                pass

            def deploy(self, dry_run=False):
                return success

        monkeypatch.setattr(deployer_native, "NativeInstanceDeployer", _Deployer)
        monkeypatch.setattr("rocketdoo.core.instance.deployer_docker.DockerInstanceDeployer", _Deployer, raising=False)
        args = ["instance", "deploy", "--env", env, "--yes"]
        if dry_run:
            args.append("--dry-run")
        return CliRunner().invoke(main, args)

    def test_a_failed_deploy_exits_non_zero(self, tmp_path, monkeypatch):
        assert self._invoke(tmp_path, monkeypatch, success=False).exit_code == 1

    def test_a_blocked_dry_run_exits_non_zero(self, tmp_path, monkeypatch):
        assert self._invoke(tmp_path, monkeypatch, success=False, dry_run=True).exit_code == 1

    def test_a_successful_deploy_still_exits_zero(self, tmp_path, monkeypatch):
        assert self._invoke(tmp_path, monkeypatch, success=True).exit_code == 0

    def test_a_successful_dry_run_still_exits_zero(self, tmp_path, monkeypatch):
        assert self._invoke(tmp_path, monkeypatch, success=True, dry_run=True).exit_code == 0

    def test_a_missing_environment_exits_non_zero(self, tmp_path, monkeypatch):
        assert self._invoke(tmp_path, monkeypatch, success=True, env="stage").exit_code == 1

    def test_without_a_config_it_exits_non_zero(self, tmp_path, monkeypatch):
        assert self._invoke(tmp_path, monkeypatch, success=True, config=False).exit_code == 1
