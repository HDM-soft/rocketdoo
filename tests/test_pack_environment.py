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

from rocketdoo.core import pack as core_pack
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

        monkeypatch.setattr("rocketdoo.core.pack.databases_result", _databases_result)
        monkeypatch.setattr("rocketdoo.core.pack.db_container", lambda *a, **k: "db-packdemo")
        # Without this the run stops at the "container is not running" prompt
        # and never reaches the listing branch under test.
        monkeypatch.setattr("rocketdoo.core.pack._is_container_running", lambda *a, **k: True)

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
    anyone noticing. RF8.5 moved it into core/pack.py in T11 -- the one
    authorised change here is following the import to its new home; the
    asserts are untouched.
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
        found = core_pack._verify_no_ssh_in_zip(self._zip_with(tmp_path, name))
        assert found == [name]

    @pytest.mark.parametrize("name", ["project/.ssh/id_rsa.pub", "project/config/odoo.conf"])
    def test_public_keys_and_ordinary_files_are_not_reported(self, tmp_path, name):
        assert core_pack._verify_no_ssh_in_zip(self._zip_with(tmp_path, name)) == []


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

        monkeypatch.setattr(core_pack.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(core_pack, "databases_result", lambda *a, **k: (["packdemo"], ""))

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

        monkeypatch.setattr(core_pack.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(core_pack, "databases_result", lambda *a, **k: (["packdemo"], ""))

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

        monkeypatch.setattr(core_pack.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(core_pack, "databases_result", lambda *a, **k: (["alpha", "beta", "gamma"], ""))
        select = _FakeAsk("alpha")  # the wrong answer: proves it was never asked
        monkeypatch.setattr(pack_environment.questionary, "select", select)

        result = CliRunner().invoke(main, ["pack", "--db-name", "beta"])
        assert result.exit_code == 0, result.output

        assert not select.calls, "must not prompt when --db-name is given"
        pg_dump_calls = [c for c in calls if c[3:4] == ["pg_dump"]]
        assert pg_dump_calls == [["docker", "exec", "db-packdemo", "pg_dump", "-U", "root", "--format=custom", "beta"]]


class TestPackDatabaseSelectionPrompt:
    """RF8.2: with 2+ databases and no --db-name, pack used to ask with
    `questionary.select`. T11 replaces the prompt with a `PackError` that
    lists the databases and hints at --db-name -- `pack()` never asks.

    This is the one test the plan authorises T11 to change the assertions
    of: the old expectation (a prompt call, then a backup of the answer) is
    replaced by "no prompt, no backup, no ZIP, a clear error instead".
    """

    def test_ambiguous_selection_is_an_error_not_a_prompt(self, packable_project, monkeypatch):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        calls = []
        import rocketdoo.pack_environment as pack_environment

        monkeypatch.setattr(core_pack.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(core_pack, "databases_result", lambda *a, **k: (["alpha", "beta"], ""))
        select = _FakeAsk("beta")
        monkeypatch.setattr(pack_environment.questionary, "select", select)

        output = packable_project / "pack-ambiguous.zip"
        result = CliRunner().invoke(main, ["pack", "--output", str(output)])
        assert result.exit_code == 0, result.output

        assert not select.calls, "RF8.2: pack() never asks, so no select prompt is shown"
        flat_output = " ".join(result.output.split())
        assert "2 databases found" in flat_output
        assert "--db-name" in flat_output
        pg_dump_calls = [c for c in calls if c[3:4] == ["pg_dump"]]
        assert not pg_dump_calls
        assert not output.exists()
        assert not (packable_project / "rkd_backups").exists()


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
            core_pack.subprocess,
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

        def _boom(*_args, **_kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(core_pack, "_create_zip", _boom)

        result = CliRunner().invoke(main, ["pack", "--no-db"])
        assert result.exit_code == 0, result.output

        restored = dockerfile_path.read_text()
        assert restored == original
        assert "[RKD-SANITIZED]" not in restored


class TestCorePackDirectly:
    """CA11: `core.pack.pack()` exercised on its own, bypassing the CLI --
    the contract any other caller (the GUI) relies on. `pack_environment.py`
    never lets this specific scenario reach `pack()` (it fails fast itself,
    see TestPackDatabaseSelectionPrompt), so `pack()` has to enforce it too.
    """

    def test_two_databases_and_no_db_name_raises_without_writing_anything(self, packable_project, monkeypatch):
        monkeypatch.setattr(core_pack, "databases_result", lambda *a, **k: (["alpha", "beta"], ""))
        monkeypatch.setattr(core_pack, "_is_container_running", lambda *a, **k: True)
        output = packable_project / "core-pack-ambiguous.zip"

        with pytest.raises(core_pack.PackError):
            core_pack.pack(packable_project, db_name=None, output=output)

        assert not output.exists()
        assert not (packable_project / "rkd_backups").exists()
        assert not (packable_project / "rkd-shared.json").exists()

    def test_dockerfile_is_restored_when_zip_creation_raises(self, packable_project, monkeypatch):
        """Mutation (a): the restore call sitting outside the except branch."""
        from rocketdoo.core.ssh_manager import inject_ssh_into_dockerfile

        dockerfile_path = packable_project / "Dockerfile"
        inject_ssh_into_dockerfile(dockerfile_path, "id_rsa_deploy")
        original = dockerfile_path.read_text()

        def _boom(*_a, **_k):
            raise RuntimeError("disk full")

        monkeypatch.setattr(core_pack, "_create_zip", _boom)

        with pytest.raises(core_pack.PackError):
            core_pack.pack(packable_project, include_db=False, output=packable_project / "core-pack-boom.zip")

        assert dockerfile_path.read_text() == original

    def test_a_given_db_name_is_not_overridden_by_the_first_available_database(self, packable_project, monkeypatch):
        """Mutation (d): picking available_dbs[0] instead of the requested name."""
        calls = []
        monkeypatch.setattr(core_pack.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(core_pack, "databases_result", lambda *a, **k: (["alpha", "beta"], ""))
        output = packable_project / "core-pack-requested.zip"

        report = core_pack.pack(packable_project, db_name="beta", output=output)

        assert report["db_name"] == "beta"
        pg_dump_calls = [c for c in calls if c[3:4] == ["pg_dump"]]
        assert pg_dump_calls == [["docker", "exec", "db-packdemo", "pg_dump", "-U", "root", "--format=custom", "beta"]]


class TestCorePackAllowMissingDb:
    """RF8.3/mutation (c): `allow_missing_db=False` must actually gate the
    raise -- a mutation that forces it True would make the first test below
    silently succeed instead of raising.
    """

    def test_allow_missing_db_false_raises_when_the_container_is_down(self, packable_project, monkeypatch):
        monkeypatch.setattr(core_pack, "_is_container_running", lambda *a, **k: False)
        output = packable_project / "core-pack-down.zip"

        with pytest.raises(core_pack.PackError):
            core_pack.pack(packable_project, allow_missing_db=False, output=output)

        assert not output.exists()

    def test_allow_missing_db_true_continues_without_a_backup(self, packable_project, monkeypatch):
        monkeypatch.setattr(core_pack, "_is_container_running", lambda *a, **k: False)
        output = packable_project / "core-pack-down-allowed.zip"

        report = core_pack.pack(packable_project, allow_missing_db=True, output=output)

        assert report["db_backup"] is False
        assert output.exists()


class TestCorePackProgressCallback:
    """CA3: `on_progress=None` and a callback must produce the same report
    and the same files on disk, exercised across two separate project trees.
    """

    def _make_project(self, tmp_path, monkeypatch, name):
        project_dir = tmp_path / name
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        scaffold_project()
        init_from_profile("odoo18-ce", project_name="packdemo")
        return project_dir

    def test_none_and_a_callback_produce_the_same_result(self, tmp_path, monkeypatch):
        project_a = self._make_project(tmp_path, monkeypatch, "project-a")
        project_b = self._make_project(tmp_path, monkeypatch, "project-b")

        report_silent = core_pack.pack(project_a, include_db=False, output=project_a.parent / "a.zip", on_progress=None)

        events = []
        report_cb = core_pack.pack(
            project_b,
            include_db=False,
            output=project_b.parent / "b.zip",
            on_progress=lambda message, level="info": events.append((message, level)),
        )

        report_silent.pop("zip")
        report_cb.pop("zip")
        assert report_silent == report_cb
        assert events, "the callback must have been invoked"


class TestPackWithoutATerminal:
    """`rkd pack` from a script or a CI job.

    Before #143 the "container not detected" branch never prompted, so a
    scripted pack produced its ZIP with a warning. RF8.3 unified it with the
    "container down" branch, which does prompt -- and `questionary` raises
    EOFError when there is no TTY, turning a working invocation into a
    traceback.
    """

    def test_a_prompt_with_nobody_to_ask_aborts_cleanly(self, packable_project, monkeypatch):
        from click.testing import CliRunner

        from rocketdoo import pack_environment
        from rocketdoo.cli import main

        class _NoTerminal:
            def ask(self):
                raise EOFError

        monkeypatch.setattr(core_pack, "_is_container_running", lambda *a, **k: False)
        monkeypatch.setattr(pack_environment.questionary, "confirm", lambda *a, **k: _NoTerminal())

        result = CliRunner().invoke(main, ["pack", "-o", str(packable_project / "out.zip")])

        assert result.exit_code == 0, result.output
        assert result.exception is None
        assert "--yes" in " ".join(result.output.split())
        assert not (packable_project / "out.zip").exists()


class TestOutputInsideTheProject:
    """`rkd pack -o <path inside the project>` (#223).

    The walk that builds the archive reaches the archive being written, and
    zipfile reads a source file until EOF -- but the end of this one recedes
    as it is read, because every chunk read back is a chunk just appended.
    With compressible text the deflate ratio wins the race and the ZIP merely
    carries a corrupt copy of itself; with data that does not compress,
    nothing ends the loop. Observed on a real project: 392 GB written in 153
    minutes before the process was killed.

    Three of these tests pin the hole from a different direction (absolute
    path, path relative to the cwd, a second name for the same file); the
    last two are the opposite guard -- what must *stay* in the ZIP, so that a
    wider exclusion (every `.zip`, or anything matching the output's
    basename) cannot pass as a fix. Those two stay green when the fix is
    removed, by design.
    """

    def test_the_archive_does_not_contain_itself(self, packable_project):
        output = packable_project / "entorno.zip"

        report = core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "entorno.zip" not in names
        assert report["file_count"] == len(names)

    def test_an_output_relative_to_the_cwd_does_not_contain_itself(self, packable_project):
        """`rkd pack -o entorno.zip` from inside the project: the way the
        command is actually typed, and the spelling that never matches the
        absolute paths the walk produces.
        """
        from click.testing import CliRunner

        from rocketdoo.cli import main

        result = CliRunner().invoke(main, ["pack", "--no-db", "-o", "entorno.zip"])

        assert result.exit_code == 0, result.output
        with zipfile.ZipFile(packable_project / "entorno.zip") as zf:
            assert "entorno.zip" not in zf.namelist()

    def test_a_second_name_for_the_archive_does_not_contain_it_either(self, packable_project):
        """A symlink is another name for the same growing file, so comparing
        path text leaves the hole open where comparing identity closes it.
        A hard link and a case-insensitive filesystem are the same mistake.
        """
        output = packable_project / "entorno.zip"
        (packable_project / "alias.zip").symlink_to(output)

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "entorno.zip" not in names
        assert "alias.zip" not in names

    def test_another_zip_in_the_project_is_still_included(self, packable_project):
        """Only the archive being written is skipped, not every ZIP: one the
        user keeps in the project is project content like any other file.
        """
        (packable_project / "adjunto.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)

        core_pack.pack(packable_project, include_db=False, output=packable_project / "entorno.zip")

        with zipfile.ZipFile(packable_project / "entorno.zip") as zf:
            assert "adjunto.zip" in zf.namelist()

    def test_a_namesake_deeper_in_the_tree_is_still_included(self, packable_project):
        """`addons/entorno.zip` is not the output, it only shares its name.
        Excluding it would drop an addon's attachment from the shared
        environment with nothing in the report to say so.
        """
        (packable_project / "addons" / "entorno.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)

        report = core_pack.pack(packable_project, include_db=False, output=packable_project / "entorno.zip")

        with zipfile.ZipFile(packable_project / "entorno.zip") as zf:
            names = zf.namelist()
        assert "addons/entorno.zip" in names
        assert report["file_count"] == len(names)
