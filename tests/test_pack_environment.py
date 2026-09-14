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

import pytest

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
