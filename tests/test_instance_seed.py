"""Seeding wired into the deploy (#301): what the two deployers do with `seed_database`.

The restore and neutralization scripts have their own suite in
`test_db_seed.py`. Here the question is the wiring: that a seeded database is
never handed a generated `admin` password, that a redeploy does not restore
over a live instance, and that nothing is dumped or shipped unless it has to be.
"""

import os
import shutil
import subprocess

import pytest
from click.testing import CliRunner

from rocketdoo.cli import main
from rocketdoo.core.instance import (
    config_manager,
    db_seed,
    deployer_docker,
    deployer_native,
    preflight,
    seeding,
    ssh_utils,
)
from tests.conftest import GREEN_REMOTE

pytestmark = pytest.mark.usefixtures("green_preflight")

VPS = {"host": "vps.example.com", "user": "ubuntu", "port": 22, "auth_method": "ssh_key", "ssh_key": "~/.ssh/id_rsa"}


def _docker_cfg(**overrides) -> dict:
    cfg = {
        "type": "docker",
        "vps": VPS,
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


def _native_cfg(**overrides) -> dict:
    cfg = {
        "type": "native",
        "vps": VPS,
        "odoo_version": "18.0",
        "domain": "erp.example.com",
        "db_user": "odoo_prod",
        "remote_path": "/opt/odoo-prod",
    }
    cfg.update(overrides)
    return cfg


class _Vps:
    """subprocess.run stand-in for the whole deploy: records, never connects."""

    def __init__(self, state="absent", restore_stderr=""):
        self.calls = []
        self.state = state
        self.restore_stderr = restore_stderr

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        script = kwargs.get("input") or ""
        stdout = f"{db_seed.STATE_PREFIX}{self.state}\n" if db_seed.STATE_PREFIX in script else ""
        stderr = self.restore_stderr if "pg_restore" in script else ""
        return subprocess.CompletedProcess(args, returncode=0, stdout=stdout, stderr=stderr)

    def scripts(self, needle: str) -> list[str]:
        return [kw.get("input") or "" for _, kw in self.calls if needle in (kw.get("input") or "")]

    def rsyncs_to(self, needle: str) -> list[list[str]]:
        return [args for args, _ in self.calls if args[0] == "rsync" and needle in args[-1]]

    def ssh_commands(self, needle: str) -> list[str]:
        return [args[-1] for args, _ in self.calls if args[0] == "ssh" and needle in args[-1]]


@pytest.fixture
def dumps(monkeypatch):
    """The local half of the seed: recorded instead of docker-exec'd."""
    taken = []

    def dump_database(container, db, path, report):
        taken.append(("database", container, db))
        path.write_bytes(b"dump")
        return True

    def dump_filestore(container, db, path, report):
        taken.append(("filestore", container, db))
        path.write_bytes(b"tarball")
        return True, "/var/lib/odoo/filestore"

    compose = {"services": {"db": {"container_name": "dev-db"}, "web": {"container_name": "dev-web"}}}
    monkeypatch.setattr(seeding, "dump_database", dump_database)
    monkeypatch.setattr(seeding, "dump_filestore", dump_filestore)
    monkeypatch.setattr(seeding, "read_docker_compose", lambda root: compose)
    monkeypatch.setattr(
        preflight,
        "_seed_facts",
        lambda root, db: {"seed_exists": True, "seed_error": "", "seed_bytes": 0, "local_pg_major": 16},
    )
    monkeypatch.setattr(preflight, "remote_facts", lambda *a, **k: {**GREEN_REMOTE, "pg_major": "16"})
    monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
    return taken


def _deploy(kind, tmp_path, monkeypatch, vps, *, dry_run=False, **overrides):
    (tmp_path / "addons").mkdir(exist_ok=True)
    if kind == "docker":
        monkeypatch.setattr(deployer_docker.subprocess, "run", vps)
        deployer = deployer_docker.DockerInstanceDeployer("prod", _docker_cfg(**overrides), tmp_path)
    else:
        monkeypatch.setattr(deployer_native.subprocess, "run", vps)
        deployer = deployer_native.NativeInstanceDeployer("prod", _native_cfg(**overrides), tmp_path)
    return deployer, deployer.deploy(dry_run=dry_run)


KINDS = pytest.mark.parametrize("kind", ["docker", "native"])


@KINDS
class TestFirstSeed:
    def test_the_dump_is_taken_shipped_restored_and_cleaned_up(self, kind, tmp_path, monkeypatch, dumps):
        vps = _Vps(state="absent")
        _, ok = _deploy(kind, tmp_path, monkeypatch, vps, seed_database="dev_db")

        assert ok
        assert [step for step, _, _ in dumps] == ["database", "filestore"]
        assert dumps[0][1:] == ("dev-db", "dev_db")
        assert vps.rsyncs_to("/var/tmp/rkd-seed-prod/")
        restore = vps.scripts("pg_restore")
        assert len(restore) == 1
        assert "createdb" in restore[0] and "rkd.seeded_from" in restore[0]
        assert vps.ssh_commands("rm -rf /var/tmp/rkd-seed-prod")

    def test_the_restore_replaces_the_creation_of_the_database(self, kind, tmp_path, monkeypatch, dumps):
        vps = _Vps(state="absent")
        _deploy(kind, tmp_path, monkeypatch, vps, seed_database="dev_db")
        restore = vps.scripts("pg_restore")[0]

        assert restore.index("pg_restore") > restore.index("createdb")
        # The step that initialises a database finds it initialised and skips.
        assert not [s for s in vps.scripts("-i base") if "pg_restore" in s]

    def test_a_filestore_that_cannot_be_dumped_stops_the_deploy(self, kind, tmp_path, monkeypatch, dumps):
        monkeypatch.setattr(seeding, "dump_filestore", lambda *a: (False, None))
        vps = _Vps(state="absent")
        _, ok = _deploy(kind, tmp_path, monkeypatch, vps, seed_database="dev_db")

        assert not ok
        assert not vps.scripts("pg_restore")


@KINDS
class TestRedeploy:
    def test_a_seeded_database_is_not_dumped_shipped_or_restored_again(self, kind, tmp_path, monkeypatch, dumps):
        vps = _Vps(state="seeded")
        _, ok = _deploy(kind, tmp_path, monkeypatch, vps, seed_database="dev_db")

        assert ok
        assert dumps == []
        assert not vps.rsyncs_to("rkd-seed")
        assert not vps.scripts("pg_restore")

    def test_a_database_initialised_without_a_seed_is_never_overwritten(self, kind, tmp_path, monkeypatch, dumps):
        vps = _Vps(state="initialised")
        _, ok = _deploy(kind, tmp_path, monkeypatch, vps, seed_database="dev_db")

        assert not ok
        assert dumps == []
        assert not vps.scripts("pg_restore")


@KINDS
class TestWithoutASeed:
    def test_the_deploy_is_the_one_that_existed_before(self, kind, tmp_path, monkeypatch, dumps):
        vps = _Vps()
        _, ok = _deploy(kind, tmp_path, monkeypatch, vps)

        assert ok
        assert dumps == []
        assert not vps.scripts(db_seed.STATE_PREFIX)
        assert not vps.scripts("pg_restore")
        assert not vps.rsyncs_to("rkd-seed")
        creation = vps.scripts("-i base")
        assert len(creation) == 1 and "createdb" in creation[0]


@KINDS
class TestDryRun:
    def test_nothing_is_dumped_or_transferred(self, kind, tmp_path, monkeypatch, dumps):
        vps = _Vps()
        _, ok = _deploy(kind, tmp_path, monkeypatch, vps, dry_run=True, seed_database="dev_db")

        assert ok
        assert dumps == []
        assert not [c for c in vps.calls if c[0][0] == "rsync"]
        assert not vps.scripts("pg_restore")


@KINDS
class TestRestoreWarning:
    def test_pg_restore_exit_one_reaches_the_final_warnings(self, kind, tmp_path, monkeypatch, dumps, capsys):
        vps = _Vps(state="absent", restore_stderr=db_seed.RESTORE_WARNING + "\n")
        deployer, ok = _deploy(kind, tmp_path, monkeypatch, vps, seed_database="dev_db")

        assert ok
        assert [c["name"] for c in deployer.checks if c["name"] == "seed restore"] == ["seed restore"]
        shown = capsys.readouterr().out
        assert "Warnings from the deploy" in shown
        assert shown.rindex("seed restore") > shown.index("Warnings from the deploy")

    def test_a_clean_restore_adds_no_warning(self, kind, tmp_path, monkeypatch, dumps):
        deployer, _ = _deploy(kind, tmp_path, monkeypatch, _Vps(state="absent"), seed_database="dev_db")

        assert not [c for c in deployer.checks if c["name"] == "seed restore"]


_DOCKER_STUB = (
    "#!/bin/sh\n"
    'echo "docker $*" >> "$RKD_LOG"\n'
    'case "$*" in\n'
    '  *"compose up"*) cat > /dev/null 2>/dev/null ;;\n'
    '  *"odoo shell"*) cat >> "$RKD_LOG" ;;\n'
    "  *pg_isready*) ;;\n"
    "  *pg_database*) echo 1 ;;\n"
    "  *ir_module_module*) echo 1 ;;\n"
    "  *ir_config_parameter*)\n"
    '    for mark in $(echo "$RKD_MARKS" | tr , " "); do\n'
    '      case "$*" in *"\'$mark\'"*) echo 1 ;; esac\n'
    "    done ;;\n"
    "esac\n"
    "exit 0\n"
)

_NATIVE_STUB = _DOCKER_STUB.replace('echo "docker $*"', 'echo "sudo $*"').replace("*pg_isready*) ;;\n", "")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs a POSIX shell")
class TestTheAdminPasswordOfASeededDatabase:
    """The guard on the step that replaces the password of `admin`, executed.

    A restored database never carries `rkd.admin_password_set`, so a guard on
    that key alone changes the customer's admin password to a generated one
    on the first deploy of every seeded instance.
    """

    def _script(self, kind, tmp_path, monkeypatch) -> str:
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        sent = []
        if kind == "docker":
            remote = tmp_path / "remote"
            (remote / "prod").mkdir(parents=True)
            deployer = deployer_docker.DockerInstanceDeployer("prod", _docker_cfg(remote_path=str(remote)), tmp_path)
            monkeypatch.setattr(
                deployer, "_ssh", lambda cmd, agent_forward=False, timeout=600, input=None: sent.append(input) or True
            )
        else:
            deployer = deployer_native.NativeInstanceDeployer("prod", _native_cfg(), tmp_path)
            monkeypatch.setattr(
                deployer, "_ssh", lambda cmd, timeout=300, input=None, agent_forward=False: sent.append(input) or True
            )
        deployer._ensure_database()
        return sent[-1]

    def _run(self, kind, tmp_path, script, marks) -> str:
        stubs = tmp_path / "stubs"
        stubs.mkdir(exist_ok=True)
        for name, body in {"sleep": "#!/bin/sh\nexit 0\n", kind.replace("native", "sudo"): _DOCKER_STUB}.items():
            path = stubs / name
            path.write_text(_NATIVE_STUB if name == "sudo" else body)
            path.chmod(0o755)
        log = tmp_path / "commands.log"
        log.write_text("")
        env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}", "RKD_LOG": str(log), "RKD_MARKS": marks}
        result = subprocess.run(["bash", "-s"], input=script, env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        return log.read_text()

    @KINDS
    def test_a_seeded_database_keeps_the_password_of_its_admin(self, kind, tmp_path, monkeypatch):
        script = self._script(kind, tmp_path, monkeypatch)

        log = self._run(kind, tmp_path, script, marks=db_seed.SEEDED_MARK)

        assert "odoo shell" not in log
        assert "user_admin" not in log

    @KINDS
    def test_a_database_rkd_initialised_keeps_its_password_once_set(self, kind, tmp_path, monkeypatch):
        script = self._script(kind, tmp_path, monkeypatch)

        log = self._run(kind, tmp_path, script, marks="rkd.admin_password_set")

        assert "user_admin" not in log

    @KINDS
    def test_a_database_with_neither_mark_does_get_its_password_set(self, kind, tmp_path, monkeypatch):
        script = self._script(kind, tmp_path, monkeypatch)

        log = self._run(kind, tmp_path, script, marks="")

        assert "user_admin" in log


class _FakeConfig:
    def __init__(self, project_path):
        pass

    def exists(self):
        return True

    def get_env(self, env):
        return _docker_cfg(seed_database="dev_db", db_name="prod_db")


class _FakeDeployer:
    instances = []

    def __init__(self, env, cfg, path):
        self.checks = [
            {
                "name": "seed restore",
                "level": "warn",
                "ok": False,
                "detail": "pg_restore ignored some statements",
                "hint": "compare the instance",
            }
        ]
        self.dry_run = None
        _FakeDeployer.instances.append(self)

    def deploy(self, dry_run=False):
        self.dry_run = dry_run
        return True


class TestTheCommand:
    @pytest.fixture(autouse=True)
    def _fakes(self, tmp_path, monkeypatch):
        _FakeDeployer.instances = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(config_manager, "InstanceConfigManager", _FakeConfig)
        monkeypatch.setattr(deployer_docker, "DockerInstanceDeployer", _FakeDeployer)

    def _invoke(self, *args, input=None):
        return CliRunner().invoke(main, ["instance", "deploy", *args], input=input)

    def test_prod_needs_the_word_prod_and_names_both_databases(self):
        result = self._invoke("--env", "prod", input="y\nno\n")

        assert result.exit_code == 1
        assert "dev_db" in result.output and "prod_db" in result.output
        assert "PRODUCTION" in result.output
        assert _FakeDeployer.instances == []

    def test_prod_goes_ahead_when_the_word_is_typed(self):
        result = self._invoke("--env", "prod", input="y\nprod\n")

        assert result.exit_code == 0
        assert len(_FakeDeployer.instances) == 1

    def test_yes_skips_both_questions(self):
        result = self._invoke("--env", "prod", "--yes")

        assert result.exit_code == 0
        assert "Type 'prod'" not in result.output

    def test_the_dry_run_asks_nothing_and_describes_the_seed(self):
        result = self._invoke("--env", "prod", "--dry-run")

        assert result.exit_code == 0
        assert "dev_db" in result.output and "prod_db" in result.output
        assert _FakeDeployer.instances[0].dry_run is True

    def test_the_final_panel_carries_the_restore_warning_and_no_admin_password(self):
        result = self._invoke("--env", "prod", "--yes")

        assert "pg_restore ignored some statements" in result.output
        assert "compare the instance" in result.output
        assert "keep their passwords" in result.output
        assert "instance_prod_db.env" not in result.output
