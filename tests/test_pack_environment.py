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
import os
import zipfile
from pathlib import Path
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
    anyone noticing. RF8.5 moved it into core/pack.py in T11.

    #245 widened it: it used to know only about SSH key filenames, which is
    why `.rkd/secrets/` travelled inside shared ZIPs while the panel reported
    the archive clean. The key heuristics below are unchanged -- a path list
    cannot catch a private key the user named something else.
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
            # The shapes `_create_zip` actually writes: paths relative to the
            # project root, with no prefix. The four used to carry a
            # `project/` one, which `pack` never emits -- and under which the
            # path list does not match at all, so the `.ssh` entry added by
            # #245 was never exercised and only the filename heuristics were.
            ".ssh/id_rsa",
            "id_ed25519",
            "id_ecdsa",
            ".ssh/deploy_key",
            "addons/mi_modulo/id_rsa_viejo",
        ],
    )
    def test_a_private_key_is_reported(self, tmp_path, name):
        found = core_pack._scan_zip_for_secrets(self._zip_with(tmp_path, name))
        assert found == [name]

    @pytest.mark.parametrize("name", ["project/.ssh/id_rsa.pub", "project/config/odoo.conf"])
    def test_public_keys_and_ordinary_files_are_not_reported(self, tmp_path, name):
        assert core_pack._scan_zip_for_secrets(self._zip_with(tmp_path, name)) == []


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


def _stale_pair(project_dir, db="vieja", stamp="20260101_120000"):
    """A (dump, filestore) pair left by an earlier `rkd pack`, written with
    the exact names pack produces so the real code has to recognise them.
    """
    backup_dir = project_dir / "rkd_backups"
    backup_dir.mkdir(exist_ok=True)
    dump = backup_dir / f"db_{db}_{stamp}.dump"
    tar = backup_dir / f"filestore_{db}_{stamp}.tar.gz"
    dump.write_bytes(b"PGDMP-vieja")
    tar.write_bytes(b"\x1f\x8b-vieja")
    return dump, tar


class TestStaleBackupsDoNotTravel:
    """`rkd_backups/` accumulates a (dump, filestore) pair per pack and
    nothing ever removes them (#225). `core/unpack.py::_find_backup_files`
    restores the newest pair and ignores the rest, so every older pair is
    weight the recipient downloads and never reads -- gigabytes of it on a
    real database.

    The `--no-db` case is worse than weight. `unpack()` branches on
    `db_dump is not None`, never on the manifest, so a pack that carried no
    database on purpose still shipped the previous pack's dump and the
    recipient restored it: stale data the sender never meant to send.
    """

    def test_a_pack_without_a_database_does_not_ship_an_older_dump(self, packable_project):
        dump, tar = _stale_pair(packable_project)
        output = packable_project / "sin-base.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert f"rkd_backups/{dump.name}" not in names
        assert f"rkd_backups/{tar.name}" not in names

    def test_only_this_packs_own_backups_travel(self, packable_project, monkeypatch):
        calls = []
        monkeypatch.setattr(core_pack.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(core_pack, "databases_result", lambda *a, **k: (["qa"], ""))
        stale_dump, stale_tar = _stale_pair(packable_project)
        output = packable_project / "con-base.zip"

        report = core_pack.pack(packable_project, db_name="qa", output=output)

        with zipfile.ZipFile(output) as zf:
            shipped = [n for n in zf.namelist() if n.startswith("rkd_backups/")]
        assert f"rkd_backups/{stale_dump.name}" not in shipped
        assert f"rkd_backups/{stale_tar.name}" not in shipped
        assert [n for n in shipped if n.startswith("rkd_backups/db_qa_")], shipped
        assert report["stale_backups_skipped"] == 2

    def test_the_stale_backups_stay_on_the_senders_disk(self, packable_project):
        """Left out of the ZIP, not deleted: they are the sender's own
        backups of their own project.
        """
        dump, tar = _stale_pair(packable_project)

        core_pack.pack(packable_project, include_db=False, output=packable_project / "sin-base.zip")

        assert dump.exists()
        assert tar.exists()

    def test_anything_else_under_rkd_backups_still_travels(self, packable_project):
        """Only the files pack itself writes are recognised as stale. A
        directory rkd created is not a directory rkd owns: dropping a file
        the user put there would be silent data loss in the shared copy.
        """
        _stale_pair(packable_project)
        (packable_project / "rkd_backups" / "notas.txt").write_text("por que guarde esto\n")
        (packable_project / "rkd_backups" / "db_a_mano.dump").write_bytes(b"PGDMP-mano")
        output = packable_project / "sin-base.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "rkd_backups/notas.txt" in names
        assert "rkd_backups/db_a_mano.dump" in names


class TestWhatCountsAsOneOfPacksOwnBackups:
    """The pattern that decides whether a file under `rkd_backups/` is rkd's
    to leave behind or the user's to ship. Both halves matter: miss one of
    pack's own names and the ZIP keeps growing; match one of the user's and
    their file silently vanishes from the shared copy.

    `core/unpack.py` reads back the same pattern, so a name pack writes and
    unpack does not recognise would be restored as nothing at all.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "db_qa_20261006_120000.dump",
            "db_my_db_2_20261006_120000.dump",
            "db_v16.0_20261006_120000.dump",
            "db_20260101_20261006_120000.dump",
            "filestore_qa_20261006_120000.tar.gz",
            "filestore_my_db_2_20261006_120000.tar.gz",
        ],
    )
    def test_names_pack_writes_are_recognised(self, name):
        assert core_pack.BACKUP_FILENAME.match(name), name

    @pytest.mark.parametrize(
        "name",
        [
            "notas.txt",
            "db_a_mano.dump",
            "db_qa.dump",
            "filestore_qa.tar.gz",
            "db_qa_20261006.dump",
            "db_qa_20261006_120000.sql",
            "_20261006_120000.dump",
        ],
    )
    def test_anything_else_is_the_users(self, name):
        assert not core_pack.BACKUP_FILENAME.match(name), name

    def test_a_backup_in_a_subdirectory_is_the_users_too(self, packable_project):
        """pack writes flat into `rkd_backups/`. A subdirectory is someone's
        own filing, so it travels whatever the files inside are called.
        """
        archive = packable_project / "rkd_backups" / "2025" / "db_vieja_20250101_120000.dump"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"PGDMP-2025")
        output = packable_project / "sin-base.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert "rkd_backups/2025/db_vieja_20250101_120000.dump" in zf.namelist()


class TestAFilestoreWithNoDumpBesideIt:
    """`unpack()` nests the whole restore -- filestore included -- under the
    dump existing, so a filestore that arrives alone can never be read.

    That is exactly the shape a failed `pg_dump` leaves behind: `pack()` sets
    `db_backup_path = None` and carries on to back the filestore up anyway.
    Shipping it is guaranteed dead weight, and on a real project it is the
    larger of the two files.
    """

    def test_a_failed_dump_does_not_ship_this_runs_filestore(self, packable_project, monkeypatch):
        calls = []
        monkeypatch.setattr(core_pack.subprocess, "run", _fake_docker_run(calls))
        monkeypatch.setattr(core_pack, "databases_result", lambda *a, **k: (["qa"], ""))
        monkeypatch.setattr(core_pack, "_backup_database", lambda *a, **k: False)
        output = packable_project / "sin-dump.zip"

        report = core_pack.pack(packable_project, db_name="qa", output=output)

        assert report["db_backup"] is False
        with zipfile.ZipFile(output) as zf:
            shipped = [n for n in zf.namelist() if n.startswith("rkd_backups/")]
        assert shipped == [], shipped


class TestSecretsDoNotTravelInTheZip:
    """rkd declares five things sensitive in the .gitignore it generates, and
    four of them used to travel inside the ZIP meant to be shared (#245).

    Two of those four have to: docker-compose mounts `odoo_pg_pass` as a
    Docker secret and the Dockerfile COPYs `config/odoo.conf` at build time,
    so without them the received environment neither starts nor builds. They
    are the local credentials of the environment being handed over.

    The other two are credentials for the *sender's own servers* -- VPS host,
    user and password, the production PostgreSQL password, the master
    password -- and have no function in the recipient's environment.
    """

    def _with_deployment_secrets(self, project_dir):
        secrets = project_dir / ".rkd" / "secrets"
        secrets.mkdir(parents=True, exist_ok=True)
        (secrets / "instance_prod.env").write_text("INSTANCE_PROD_PASSWORD=elPasswordDelVps\n")
        (project_dir / ".rkd" / "instance.yaml").write_text("prod:\n  vps:\n    host: 10.0.0.1\n    user: root\n")

    def test_the_senders_server_credentials_stay_out(self, packable_project):
        self._with_deployment_secrets(packable_project)
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert not [n for n in names if n.startswith(".rkd/secrets")], names
        assert ".rkd/instance.yaml" not in names

    def test_what_the_environment_needs_still_travels(self, packable_project):
        """Excluding every sensitive file would ship a ZIP that does not run."""
        self._with_deployment_secrets(packable_project)
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "odoo_pg_pass" in names
        assert "config/odoo.conf" in names

    def test_a_directory_of_the_user_named_secrets_still_travels(self, packable_project):
        """Excluded by relative path, not by component name: dropping every
        directory called `secrets` would quietly delete an addon's own.
        """
        addon_secrets = packable_project / "addons" / "mi_modulo" / "secrets"
        addon_secrets.mkdir(parents=True)
        (addon_secrets / "datos.txt").write_text("contenido del addon\n")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert "addons/mi_modulo/secrets/datos.txt" in zf.namelist()

    def test_the_scanner_reports_a_secret_that_slipped_through(self, tmp_path):
        """The exclusion and the scanner are two separate guards: if the first
        one regresses, the second has to say so instead of reporting clean.
        """
        path = tmp_path / "probe.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(".rkd/secrets/instance_prod.env", "INSTANCE_PROD_PASSWORD=x")
            zf.writestr(".rkd/instance.yaml", "prod: {}")
            zf.writestr("odoo_pg_pass", "local")

        found = core_pack._scan_zip_for_secrets(path)

        assert sorted(found) == [".rkd/instance.yaml", ".rkd/secrets/instance_prod.env"]

    def test_the_exclusion_list_comes_from_the_sensitive_entries(self):
        """One list, not two. A new entry in SENSITIVE_ENTRIES stays out of
        the ZIP by default; letting it travel takes an explicit decision.
        """
        from rocketdoo.core.gitignore_manager import SENSITIVE_ENTRIES

        declared = {entry.rstrip("/") for entry, _reason in SENSITIVE_ENTRIES}
        excluded = set(core_pack._secret_paths())

        assert excluded == declared - core_pack._SHAREABLE_SECRETS

    def test_a_name_that_merely_starts_like_a_secret_still_travels(self, packable_project):
        """`.rkd/secrets` excludes that path and what is under it, not every
        name beginning with those letters. A loose prefix would silently drop
        the user's own `.rkd/secretsnotas.txt` from the shared copy.
        """
        rkd = packable_project / ".rkd"
        rkd.mkdir(exist_ok=True)
        (rkd / "secretsnotas.txt").write_text("notas mias\n")
        (rkd / "instance.yaml.ejemplo").write_text("plantilla\n")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert ".rkd/secretsnotas.txt" in names
        assert ".rkd/instance.yaml.ejemplo" in names

    def test_a_flagged_secret_reaches_the_report_and_the_cli(self, packable_project, monkeypatch):
        """The scanner wired to what the user actually sees.

        It was covered as a function and nowhere else, so replacing its call
        in `pack()` with `[]` left the whole suite green -- the exact shape of
        #245, where the archive carried a secret and the panel said it was
        clean. The exclusion and the scanner are two guards; this pins the
        second one all the way to the warning.
        """
        from click.testing import CliRunner

        from rocketdoo.cli import main

        monkeypatch.setattr(core_pack, "_scan_zip_for_secrets", lambda _zip: [".rkd/secrets/instance_prod.env"])

        result = CliRunner().invoke(main, ["pack", "--no-db", "-o", str(packable_project / "x.zip")])

        assert result.exit_code == 0, result.output
        output = " ".join(result.output.split())
        assert "SECURITY WARNING" in output
        assert ".rkd/secrets/instance_prod.env" in output

    def test_a_pattern_entry_is_excluded_too(self, packable_project, monkeypatch):
        """SENSITIVE_ENTRIES holds gitignore patterns, so the next one added
        may be `*.pem` rather than a literal path. Matching only literal
        prefixes would let it travel while the comment in pack.py promises it
        will not -- a false promise is worse than none.
        """
        monkeypatch.setattr(core_pack, "SENSITIVE_ENTRIES", [*core_pack.SENSITIVE_ENTRIES, ("*.pem", "clave TLS")])
        (packable_project / "certs").mkdir()
        (packable_project / "certs" / "server.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert "certs/server.pem" not in zf.namelist()

    def test_a_deploy_target_file_stays_out(self, packable_project):
        """`.rkd/deploy.yaml` carries the VPS host, user and the path to the
        SSH key -- the same class of data as instance.yaml. It was in neither
        list, so it travelled in the ZIP *and* was not gitignored.
        """
        rkd = packable_project / ".rkd"
        rkd.mkdir(exist_ok=True)
        (rkd / "deploy.yaml").write_text("targets:\n  prod:\n    connection:\n      host: vps.cliente.com\n")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert ".rkd/deploy.yaml" not in zf.namelist()

    def test_the_match_ignores_case_because_mnt_c_does(self, packable_project):
        """On `/mnt/c` -- where this project is normally developed -- `.rkd/Secrets`
        and `.rkd/secrets` are the same directory, and rkd's own
        `mkdir(exist_ok=True)` writes into whichever spelling is already there.
        A case-sensitive comparison would ship the password from one of them.

        Same reasoning as `is_the_archive()`, which compares by file identity
        for this exact filesystem.
        """
        odd = packable_project / ".rkd" / "Secrets"
        odd.mkdir(parents=True, exist_ok=True)
        (odd / "instance_prod.env").write_text("INSTANCE_PROD_PASSWORD=elPasswordDelVps\n")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert not [n for n in names if n.lower().startswith(".rkd/secrets")], names


class TestSymbolicLinksDoNotCarrySecretsOut:
    """`zf.write()` follows a link and stores the target's bytes under the
    link's name, so the exclusion -- which sees the link's own path -- and the
    scanner -- which only sees names inside the archive -- both miss it (#246).

    The target does not even have to be inside the project.
    """

    def test_a_link_to_a_secret_does_not_travel(self, packable_project):
        secrets = packable_project / ".rkd" / "secrets"
        secrets.mkdir(parents=True, exist_ok=True)
        (secrets / "instance_prod.env").write_text("INSTANCE_PROD_PASSWORD=elPasswordDelVps\n")
        (packable_project / "notas.txt").symlink_to(secrets / "instance_prod.env")
        output = packable_project / "compartido.zip"

        report = core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert "notas.txt" not in zf.namelist()
            assert not any("elPasswordDelVps" in zf.read(n).decode("utf-8", "replace") for n in zf.namelist())
        assert "notas.txt" in report["symlinks_skipped"]

    def test_a_link_pointing_outside_the_project_does_not_travel(self, packable_project, tmp_path):
        outside = tmp_path / "fuera"
        outside.mkdir()
        (outside / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
        (packable_project / "addons" / "clave_build").symlink_to(outside / "id_rsa")
        output = packable_project / "compartido.zip"

        report = core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert "addons/clave_build" not in zf.namelist()
        assert "addons/clave_build" in report["symlinks_skipped"]

    def test_a_linked_directory_is_named_instead_of_vanishing(self, packable_project, tmp_path):
        """The walk never descended into one, so it was already left out --
        silently, which is its own problem for whoever unpacks it."""
        elsewhere = tmp_path / "otro_repo" / "mi_addon"
        elsewhere.mkdir(parents=True)
        (elsewhere / "__manifest__.py").write_text("{'name': 'mi_addon'}\n")
        (packable_project / "addons" / "mi_addon").symlink_to(elsewhere, target_is_directory=True)
        output = packable_project / "compartido.zip"

        report = core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert not [n for n in zf.namelist() if n.startswith("addons/mi_addon")]
        assert "addons/mi_addon" in report["symlinks_skipped"]

    def test_the_files_beside_a_link_still_travel(self, packable_project):
        (packable_project / "addons" / "real.txt").write_text("contenido\n")
        (packable_project / "addons" / "enlace.txt").symlink_to(packable_project / "addons" / "real.txt")
        output = packable_project / "compartido.zip"

        report = core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "addons/real.txt" in names
        assert "addons/enlace.txt" not in names
        assert report["symlinks_skipped"] == ["addons/enlace.txt"]

    def test_the_panel_names_what_was_left_out(self, packable_project):
        """A ZIP quietly missing a file is the complaint behind #245 read
        from the other side: the receiver finds out when it does not run."""
        from click.testing import CliRunner

        from rocketdoo.cli import main

        (packable_project / "addons" / "real.txt").write_text("contenido\n")
        (packable_project / "addons" / "enlace.txt").symlink_to(packable_project / "addons" / "real.txt")

        result = CliRunner().invoke(main, ["pack", "--no-db"])

        assert "addons/enlace.txt" in result.output.replace("\n", "")

    def test_a_project_without_links_reports_none(self, packable_project):
        output = packable_project / "compartido.zip"

        report = core_pack.pack(packable_project, include_db=False, output=output)

        assert report["symlinks_skipped"] == []


class TestANestedRkdIsStillRkds:
    """The exclusion is anchored at the project root so an addon's own
    `secrets/` keeps travelling. But `.rkd` is a name rkd reserves, and
    `rkd instance init` run one directory too deep writes its credentials
    there -- where the generated .gitignore does not cover them either (#246).
    """

    def _nested_secret(self, project_dir):
        nested = project_dir / "addons" / "mi_modulo" / ".rkd" / "secrets"
        nested.mkdir(parents=True)
        (nested / "instance_prod.env").write_text("INSTANCE_PROD_PASSWORD=elPasswordDelVps\n")
        return nested

    def test_a_nested_secret_does_not_travel(self, packable_project):
        self._nested_secret(packable_project)
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert not [n for n in names if ".rkd/secrets" in n], names

    def test_a_nested_instance_yaml_does_not_travel(self, packable_project):
        nested = packable_project / "addons" / "mi_modulo" / ".rkd"
        nested.mkdir(parents=True)
        (nested / "instance.yaml").write_text("prod:\n  vps:\n    host: 10.0.0.1\n")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert "addons/mi_modulo/.rkd/instance.yaml" not in zf.namelist()

    def test_an_addons_own_secrets_directory_still_travels(self, packable_project):
        """The anchoring exists for this: `secrets` is a plausible name for a
        directory of an addon, `.rkd` is not."""
        own = packable_project / "addons" / "mi_modulo" / "secrets"
        own.mkdir(parents=True)
        (own / "config.xml").write_text("<odoo/>\n")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert "addons/mi_modulo/secrets/config.xml" in zf.namelist()

    def test_the_scanner_flags_a_nested_secret_that_got_in(self, tmp_path):
        """The exclusion and the scanner answer the same question twice, on
        purpose: an archive built elsewhere still gets looked at."""
        zip_path = tmp_path / "ajeno.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("addons/mi_modulo/.rkd/secrets/instance_prod.env", "PASSWORD=x\n")

        assert core_pack._scan_zip_for_secrets(zip_path) == ["addons/mi_modulo/.rkd/secrets/instance_prod.env"]


class TestTheWizardsDoNotScatterCredentials:
    """Where the nested `.rkd/` of the class above comes from: both wizards
    wrote to `Path.cwd()` without checking it was the project root, so a `cd`
    too far left credentials in a directory the generated .gitignore does not
    cover and `rkd pack` shipped (#246).
    """

    @pytest.mark.parametrize("command", [["instance", "init"], ["deploy", "init"]])
    def test_a_wizard_refuses_outside_a_project(self, tmp_path, monkeypatch, command):
        from click.testing import CliRunner

        from rocketdoo.cli import main

        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, command)

        assert "No Rocketdoo project" in result.output
        assert not (tmp_path / ".rkd").exists(), "the wizard wrote credentials outside a project"
        # Non-zero, or `rkd deploy init && rkd deploy run -t prod` carries on
        # as though a configuration had been written.
        assert result.exit_code != 0

    @pytest.mark.parametrize("command", [["instance", "init"], ["deploy", "init"]])
    def test_a_wizard_runs_in_the_project_root(self, packable_project, command):
        """The guard must not stand in the way of the normal case."""
        from click.testing import CliRunner

        from rocketdoo.cli import main

        result = CliRunner().invoke(main, command)

        assert "No Rocketdoo project" not in result.output


class TestHardLinksAreNotADoorEither:
    """A hard link is not a link as far as the walk is concerned: its own
    name, no `is_symlink()`, the same bytes. Excluding by path alone let it
    through the same way the symbolic version did (#246).
    """

    def test_a_hard_link_to_a_secret_does_not_travel(self, packable_project):
        secrets = packable_project / ".rkd" / "secrets"
        secrets.mkdir(parents=True, exist_ok=True)
        secret = secrets / "instance_prod.env"
        secret.write_text("INSTANCE_PROD_PASSWORD=elPasswordDelVps\n")
        os.link(secret, packable_project / "notas.txt")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
            assert "notas.txt" not in names
            assert not any("elPasswordDelVps" in zf.read(n).decode("utf-8", "replace") for n in names)

    def test_two_names_for_the_same_ordinary_file_both_travel(self, packable_project):
        """Only the secrets are identified by inode; a project that hard-links
        its own files must not lose them."""
        first = packable_project / "addons" / "real.txt"
        first.write_text("contenido\n")
        os.link(first, packable_project / "addons" / "copia.txt")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "addons/real.txt" in names
        assert "addons/copia.txt" in names

    def test_a_filesystem_that_reports_no_inode_does_not_empty_the_zip(self, packable_project, monkeypatch):
        """Identifying secrets by inode cuts both ways: where the project's
        files report 0, a set built without that guard matches every one of
        them and the ZIP silently loses the project.

        The archive keeps its real inode here on purpose: `is_the_archive()`
        compares by identity too (#223), so a filesystem that reports 0 for
        absolutely everything defeats that check as well. That is older than
        this change and not what this test is about.
        """
        secrets = packable_project / ".rkd" / "secrets"
        secrets.mkdir(parents=True, exist_ok=True)
        (secrets / "instance_prod.env").write_text("INSTANCE_PROD_PASSWORD=x\n")
        real_stat = Path.stat
        output = packable_project / "compartido.zip"

        def without_inode(self, *args, **kwargs):
            st = real_stat(self, *args, **kwargs)
            if self == output:
                return st
            return os.stat_result((st.st_mode, 0, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, st.st_size, 0, 0, 0))

        monkeypatch.setattr(Path, "stat", without_inode)

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "docker-compose.yaml" in names
        assert not [n for n in names if n.startswith(".rkd/secrets")]


class TestTheLetsEncryptKeyStaysHome:
    """`rkd traefik on --mode production` creates traefik/certs/acme.json with
    chmod 600 and Traefik writes the ACME account key and the issued
    certificates into it. It was in neither list, so `git add .` committed it
    and `rkd pack` shipped it with the archive reported clean (#246).
    """

    def test_it_does_not_travel(self, packable_project):
        certs = packable_project / "traefik" / "certs"
        certs.mkdir(parents=True)
        (certs / "acme.json").write_text('{"letsencrypt": {"Account": {"PrivateKey": "laClavePrivada"}}}')
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
            assert "traefik/certs/acme.json" not in names
            assert not any("laClavePrivada" in zf.read(n).decode("utf-8", "replace") for n in names)

    def test_the_rest_of_the_traefik_config_still_travels(self, packable_project):
        """Only the key is a secret: the compose and the static config are
        what the receiver edits for their own domain."""
        traefik = packable_project / "traefik"
        traefik.mkdir()
        (traefik / "traefik.yml").write_text("entryPoints:\n  web:\n    address: ':80'\n")
        output = packable_project / "compartido.zip"

        core_pack.pack(packable_project, include_db=False, output=output)

        with zipfile.ZipFile(output) as zf:
            assert "traefik/traefik.yml" in zf.namelist()

    def test_the_scanner_flags_one_that_got_in(self, tmp_path):
        zip_path = tmp_path / "ajeno.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("traefik/certs/acme.json", "{}")

        assert core_pack._scan_zip_for_secrets(zip_path) == ["traefik/certs/acme.json"]
