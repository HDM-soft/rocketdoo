"""Tests for pack_environment.py.

`rkd pack` failed on every invocation with UnboundLocalError: `filestore_base`
was only assigned inside one deep branch, but the manifest at the end reads it
unconditionally. Two paths reached that read without passing through the
assignment — `--no-db`, and an instance with no databases yet — which is both
of the ways the command is normally used before there is data to share.

The module had no tests at all; these cover the manifest paths. Its extraction
to core/pack.py is #143.
"""

import json
import zipfile
from types import SimpleNamespace

import pytest

from rocketdoo import pack_environment
from rocketdoo.core.gitignore_manager import SENSITIVE_ENTRIES
from rocketdoo.init_project import init_from_profile
from rocketdoo.scaffold import scaffold_project


@pytest.fixture
def packable_project(project_dir):
    """A generated project, without containers running."""
    scaffold_project()
    init_from_profile("odoo18-ce", project_name="packdemo")
    return project_dir


def _zip_for(project_dir):
    """The ZIP `pack` writes, which lands beside the project, not inside it."""
    candidates = sorted(project_dir.parent.glob("*_rkd_shared_*.zip"))
    assert candidates, "pack produced no ZIP"
    return candidates[-1]


class TestPackWithoutDatabase:
    def test_no_db_completes(self, packable_project):
        """The path that raised UnboundLocalError before #166."""
        from click.testing import CliRunner

        from rocketdoo.cli import main

        result = CliRunner().invoke(main, ["pack", "--no-db"])
        assert result.exit_code == 0, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_no_db_writes_a_zip(self, packable_project):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        CliRunner().invoke(main, ["pack", "--no-db"])
        assert _zip_for(packable_project).exists()

    def test_the_manifest_records_no_backup(self, packable_project):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        CliRunner().invoke(main, ["pack", "--no-db"])
        with zipfile.ZipFile(_zip_for(packable_project)) as zf:
            manifest = json.loads(zf.read("rkd-shared.json"))

        assert manifest["has_db_backup"] is False
        assert manifest["filestore_base"] is None

    def test_the_zip_carries_the_project_files(self, packable_project):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        CliRunner().invoke(main, ["pack", "--no-db"])
        with zipfile.ZipFile(_zip_for(packable_project)) as zf:
            names = zf.namelist()

        assert any(n.endswith("Dockerfile") for n in names)
        assert any(n.endswith("docker-compose.yaml") for n in names)
        assert "rkd-shared.json" in names

    def test_no_ssh_key_is_ever_packed(self, packable_project):
        """The whole point of the sanitise step: keys must not travel."""
        from click.testing import CliRunner

        from rocketdoo.cli import main

        ssh_dir = packable_project / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "id_rsa").write_text("PRIVATE KEY MATERIAL")

        CliRunner().invoke(main, ["pack", "--no-db"])
        with zipfile.ZipFile(_zip_for(packable_project)) as zf:
            names = zf.namelist()
            blob = b"".join(zf.read(n) for n in names if not n.endswith("/"))

        assert not any("/.ssh/" in n or n.startswith(".ssh/") for n in names)
        assert b"PRIVATE KEY MATERIAL" not in blob


class TestPackManifest:
    def test_the_manifest_describes_the_project(self, packable_project):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        CliRunner().invoke(main, ["pack", "--no-db"])
        with zipfile.ZipFile(_zip_for(packable_project)) as zf:
            manifest = json.loads(zf.read("rkd-shared.json"))

        assert manifest["odoo_version"] == "18.0"
        assert "filestore_base" in manifest
        assert "has_db_backup" in manifest

    def test_the_manifest_is_json_serialisable_end_to_end(self, packable_project):
        """`rkd unpack` parses this; a non-serialisable value breaks the receiver."""
        from click.testing import CliRunner

        from rocketdoo.cli import main

        CliRunner().invoke(main, ["pack", "--no-db"])
        with zipfile.ZipFile(_zip_for(packable_project)) as zf:
            json.loads(zf.read("rkd-shared.json"))


class TestPackedProjectKeepsSecretsOut:
    def test_the_gitignore_travels(self, packable_project):
        """The receiver must not commit the secrets either."""
        from click.testing import CliRunner

        from rocketdoo.cli import main

        CliRunner().invoke(main, ["pack", "--no-db"])
        with zipfile.ZipFile(_zip_for(packable_project)) as zf:
            names = [n for n in zf.namelist() if n.endswith(".gitignore")]
            assert names
            content = zf.read(names[0]).decode()

        entries = {ln.strip() for ln in content.splitlines() if ln.strip() and not ln.startswith("#")}
        assert {pat for pat, _ in SENSITIVE_ENTRIES} <= entries


class TestPackDatabaseListing:
    """Covers the branch T1 refactored, which `--no-db` never reaches.

    The existing tests all pass `--no-db`, which returns before the database
    listing runs, so the code moved into `core/odoo_db.py` had no coverage
    from this side.
    """

    def _run_pack(self, monkeypatch, databases, error=""):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        calls = []

        def _databases_result(*a, **kw):
            calls.append(True)
            return databases, error

        monkeypatch.setattr("rocketdoo.pack_environment.databases_result", _databases_result)
        monkeypatch.setattr("rocketdoo.pack_environment.db_container", lambda *a, **k: "db-packdemo")
        # Without this the run stops at the "container is not running" prompt
        # and never reaches the listing branch under test.
        monkeypatch.setattr("rocketdoo.pack_environment._is_container_running", lambda *a, **k: True)

        result = CliRunner().invoke(main, ["pack"])
        return result, calls

    def test_no_databases_says_so(self, packable_project, monkeypatch):
        result, _ = self._run_pack(monkeypatch, [])
        assert result.exit_code == 0, result.output
        assert "No Odoo databases found" in result.output

    def test_an_unreachable_container_is_not_reported_as_empty(self, packable_project, monkeypatch):
        """A timeout must not read as "this project has no databases".

        Otherwise pack ships a ZIP without the dump the user expected.
        """
        result, _ = self._run_pack(monkeypatch, [], error="command timed out")
        assert result.exit_code == 0, result.output
        assert "Could not list databases" in result.output
        assert "timed out" in result.output

    def test_the_listing_is_read_through_core_odoo_db(self, packable_project, monkeypatch):
        """Guards the T1 refactor: pack must keep using the shared helper."""
        _, calls = self._run_pack(monkeypatch, [])
        assert calls, "pack did not go through core.odoo_db.databases_result"


def _fake_docker_run(calls, running_containers=None):
    """A `subprocess.run` stand-in covering every docker call `pack` issues:
    the `docker inspect` liveness check, the filestore `test -d` probe, and
    the `pg_dump`/`tar` backups. Appends each argv, in the order pack issues
    it, to `calls` — the list a test compares against the expected sequence.

    `running_containers` overrides the liveness check per container name;
    a container missing from the mapping reports as running, since most of
    the paths under test need that to reach the code they exercise.
    """
    running_containers = running_containers or {}

    def fake_run(cmd, **_kwargs):
        cmd = list(cmd)
        calls.append(cmd)
        if cmd[:2] == ["docker", "inspect"]:
            is_running = running_containers.get(cmd[-1], True)
            return SimpleNamespace(returncode=0, stdout="true" if is_running else "false", stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    return fake_run


class _FakeAsk:
    """A `questionary.select`/`.confirm` stand-in: records the call and
    answers with a fixed value from `.ask()`.

    `questionary.select(...)` returns a prompt object; `.ask()` is a second,
    separate call. Patching only the outer name with a bare `Mock` leaves
    `.ask()` returning a `Mock`, which is truthy — a test built that way
    "passes" without ever exercising the branch it claims to cover. This
    fakes both calls explicitly and keeps every invocation for inspection.
    """

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self

    def ask(self):
        return self.answer


class TestSshSafetyNet:
    """The last line of defence before a ZIP is shared: `pack` excludes the
    SSH build context, and then re-reads the finished ZIP to warn if a key
    slipped through anyway.

    The existing tests prove the exclusion works, which is what makes this
    scanner dead quiet in practice -- and therefore easy to neuter without
    anyone noticing. RF8.5 moves it into the service in T11, so it needs a
    test that fails when it stops finding what it is looking for.
    """

    def _zip_with(self, tmp_path, *names):
        path = tmp_path / "probe.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for name in names:
                zf.writestr(name, "x")
        return path

    @pytest.mark.parametrize(
        "name",
        [
            "project/.ssh/id_rsa",
            "project/id_ed25519",
            "project/id_ecdsa",
            "project/.ssh/deploy_key",
        ],
    )
    def test_a_private_key_is_reported(self, tmp_path, name):
        found = pack_environment._verify_no_ssh_in_zip(self._zip_with(tmp_path, name))
        assert found == [name]

    @pytest.mark.parametrize("name", ["project/.ssh/id_rsa.pub", "project/config/odoo.conf"])
    def test_public_keys_and_ordinary_files_are_not_reported(self, tmp_path, name):
        assert pack_environment._verify_no_ssh_in_zip(self._zip_with(tmp_path, name)) == []


class TestPackWithDatabaseBackup:
    """RF8.1/RF8.4: with exactly one database, pack backs it up without
    prompting, running pg_dump and tar with a specific argv.

    core/pack.py (T11) has to reproduce this exact command for both tools;
    this is the regression net that catches a refactor that changes either
    one, even if the ZIP it produces still "looks" fine.
    """

    def test_pg_dump_and_tar_run_with_the_expected_argv(self, packable_project, monkeypatch):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        calls = []
        import rocketdoo.pack_environment as pack_environment

        monkeypatch.setattr(pack_environment.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(pack_environment, "databases_result", lambda *a, **k: (["packdemo"], ""))

        result = CliRunner().invoke(main, ["pack"])
        assert result.exit_code == 0, result.output

        assert calls == [
            ["docker", "inspect", "--format", "{{.State.Running}}", "db-packdemo"],
            ["docker", "exec", "db-packdemo", "pg_dump", "-U", "root", "--format=custom", "packdemo"],
            ["docker", "inspect", "--format", "{{.State.Running}}", "odoo-packdemo"],
            [
                "docker",
                "exec",
                "odoo-packdemo",
                "test",
                "-d",
                "/var/lib/odoo/.local/share/Odoo/filestore/packdemo",
            ],
            [
                "docker",
                "exec",
                "odoo-packdemo",
                "tar",
                "-czf",
                "-",
                "-C",
                "/var/lib/odoo/.local/share/Odoo/filestore",
                "packdemo",
            ],
        ]

    def test_both_backup_files_land_inside_the_zip(self, packable_project, monkeypatch):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        calls = []
        import rocketdoo.pack_environment as pack_environment

        monkeypatch.setattr(pack_environment.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(pack_environment, "databases_result", lambda *a, **k: (["packdemo"], ""))

        result = CliRunner().invoke(main, ["pack"])
        assert result.exit_code == 0, result.output

        with zipfile.ZipFile(_zip_for(packable_project)) as zf:
            names = zf.namelist()

        assert any(n.startswith("rkd_backups/db_packdemo_") and n.endswith(".dump") for n in names)
        assert any(n.startswith("rkd_backups/filestore_packdemo_") and n.endswith(".tar.gz") for n in names)


class TestPackWithDbNameOption:
    """RF8.2: `--db-name` picks the requested database without asking,
    even with several available — that is the whole point of the option.
    """

    def test_db_name_selects_the_requested_database_without_prompting(self, packable_project, monkeypatch):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        calls = []
        import rocketdoo.pack_environment as pack_environment

        monkeypatch.setattr(pack_environment.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(pack_environment, "databases_result", lambda *a, **k: (["alpha", "beta", "gamma"], ""))
        select = _FakeAsk("alpha")  # the wrong answer: proves it was never asked
        monkeypatch.setattr(pack_environment.questionary, "select", select)

        result = CliRunner().invoke(main, ["pack", "--db-name", "beta"])
        assert result.exit_code == 0, result.output

        assert not select.calls, "must not prompt when --db-name is given"
        pg_dump_calls = [c for c in calls if c[3:4] == ["pg_dump"]]
        assert pg_dump_calls == [["docker", "exec", "db-packdemo", "pg_dump", "-U", "root", "--format=custom", "beta"]]


class TestPackDatabaseSelectionPrompt:
    """Fixes today's behaviour: with 2+ databases and no --db-name, pack
    asks with `questionary.select`.

    T11 replaces this with a `PackError` per RF8.2 (`pack()` never asks) —
    this test's assertion of a prompt call is the one adjustment T11 is
    allowed to make to an existing test, per the plan.
    """

    def test_prompts_with_the_available_databases_and_backs_up_the_choice(self, packable_project, monkeypatch):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        calls = []
        import rocketdoo.pack_environment as pack_environment

        monkeypatch.setattr(pack_environment.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(pack_environment, "databases_result", lambda *a, **k: (["alpha", "beta"], ""))
        select = _FakeAsk("beta")
        monkeypatch.setattr(pack_environment.questionary, "select", select)

        result = CliRunner().invoke(main, ["pack"])
        assert result.exit_code == 0, result.output

        assert select.calls == [(("Select the database to back up:",), {"choices": ["alpha", "beta"]})]
        pg_dump_calls = [c for c in calls if c[3:4] == ["pg_dump"]]
        assert pg_dump_calls == [["docker", "exec", "db-packdemo", "pg_dump", "-U", "root", "--format=custom", "beta"]]


class TestPackWithDbContainerDown:
    """RF8.3: with the database container not running, pack asks whether
    to continue without a backup via `questionary.confirm` — never a
    silent default in either direction.
    """

    def _run(self, packable_project, monkeypatch, *, answer):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        calls = []
        import rocketdoo.pack_environment as pack_environment

        monkeypatch.setattr(
            pack_environment.subprocess,
            "run",
            _fake_docker_run(calls, running_containers={"db-packdemo": False}),
        )
        confirm = _FakeAsk(answer)
        monkeypatch.setattr(pack_environment.questionary, "confirm", confirm)
        select = _FakeAsk("should never be reached")
        monkeypatch.setattr(pack_environment.questionary, "select", select)

        # A fixed --output, instead of the default clock-based name beside
        # the project: several tests share the same pytest tmp root as their
        # ZIP's parent directory, and the default name only has second
        # resolution, so two ZIPs written within the same second collide on
        # the exact same path. Pinning --output here makes "was a ZIP
        # written" a direct existence check instead of a directory glob.
        output = packable_project / "pack-under-test.zip"
        result = CliRunner().invoke(main, ["pack", "--output", str(output)])
        return result, calls, confirm, select, output

    def test_continuing_skips_the_backup_but_still_builds_the_zip(self, packable_project, monkeypatch):
        result, calls, confirm, select, output = self._run(packable_project, monkeypatch, answer=True)
        assert result.exit_code == 0, result.output

        assert confirm.calls == [(("Continue anyway without DB backup?",), {"default": False})]
        assert not select.calls, "no database is ever listed once the container is down"
        assert not any(c[3:4] == ["pg_dump"] for c in calls)
        assert output.exists()

    def test_declining_aborts_without_writing_a_zip(self, packable_project, monkeypatch):
        result, calls, confirm, select, output = self._run(packable_project, monkeypatch, answer=False)
        assert result.exit_code == 0, result.output

        assert confirm.calls
        assert "Operation cancelled" in " ".join(result.output.split())
        assert not any(c[3:4] == ["pg_dump"] for c in calls)
        assert not output.exists(), "declining must not produce a ZIP"


class TestPackRestoresTheDockerfileWhenZipFails:
    """RF8.5's central guarantee: `_sanitize_dockerfile` comments out the SSH
    lines before packing, and `_restore_dockerfile` must bring them back even
    when `_create_zip` blows up in between — otherwise a failed `rkd pack`
    leaves the user's project with a mutilated Dockerfile.
    """

    def test_dockerfile_is_restored_when_the_zip_step_raises(self, packable_project, monkeypatch):
        from click.testing import CliRunner

        from rocketdoo.cli import main
        from rocketdoo.core.ssh_manager import inject_ssh_into_dockerfile

        dockerfile_path = packable_project / "Dockerfile"
        inject_ssh_into_dockerfile(dockerfile_path, "id_rsa_deploy")
        original = dockerfile_path.read_text()

        import rocketdoo.pack_environment as pack_environment

        def _boom(*_args, **_kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(pack_environment, "_create_zip", _boom)

        result = CliRunner().invoke(main, ["pack", "--no-db"])
        assert result.exit_code == 0, result.output

        restored = dockerfile_path.read_text()
        assert restored == original
        assert "[RKD-SANITIZED]" not in restored
