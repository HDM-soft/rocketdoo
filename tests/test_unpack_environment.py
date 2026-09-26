"""Unit tests for core/unpack.py -- extracted from unpack_environment.py in
#143 T13, mirroring core/pack.py's extraction in T11.

The five helpers below (_load_shared_meta, _find_backup_files, _check_ports
in its non-interactive shape, _update_ports_in_compose and
_get_db_container_name) moved verbatim; only the monkeypatch target follows
them to their new home, per the plan's instruction not to leave a second
copy behind in unpack_environment.py just to keep these imports pointed at
the old module. TestCoreUnpackInspect and TestCoreUnpackOrchestration below
add the RF9.1 services (`inspect()`/`unpack()`) themselves, including the
five mutations #143 T13 names explicitly. They all run in milliseconds; the
real round trip against Docker lives in tests/test_e2e_pack_unpack.py.
"""

import json
import subprocess

import pytest

import rocketdoo.core.unpack as core_unpack
from rocketdoo.core.unpack import (
    _check_ports,
    _find_backup_files,
    _get_db_container_name,
    _load_shared_meta,
    _update_ports_in_compose,
)


class TestLoadSharedMeta:
    """A missing or corrupt rkd-shared.json must read as "no metadata", never
    as an empty-but-valid manifest: the caller branches on
    `meta and meta.get("rkd_shared")`, which is already False for `{}`, so
    the assertions below check identity (`is None`) rather than truthiness --
    a mutation that swaps None for {} would slip past a truthiness check.
    """

    def test_a_valid_manifest_is_parsed(self, project_dir):
        payload = {"rkd_shared": True, "project_name": "demo", "odoo_port": 8069}
        (project_dir / "rkd-shared.json").write_text(json.dumps(payload))

        assert _load_shared_meta(project_dir) == payload

    def test_a_missing_file_is_none(self, project_dir):
        assert _load_shared_meta(project_dir) is None

    def test_a_corrupt_file_is_none_not_an_empty_dict(self, project_dir):
        """Mutation (c): returning {} here reads downstream as "valid,
        empty metadata" instead of "no manifest at all".
        """
        (project_dir / "rkd-shared.json").write_text("{not valid json")

        assert _load_shared_meta(project_dir) is None


class TestFindBackupFiles:
    """Picks the most recent dump/tar by filename; each kind is reported
    independently as None rather than the function ever guessing.
    """

    def test_no_backup_directory_returns_no_files(self, project_dir):
        assert _find_backup_files(project_dir) == (None, None)

    def test_an_empty_backup_directory_returns_no_files(self, project_dir):
        (project_dir / "rkd_backups").mkdir()

        assert _find_backup_files(project_dir) == (None, None)

    def test_only_a_dump_is_reported_without_a_filestore(self, project_dir):
        backups = project_dir / "rkd_backups"
        backups.mkdir()
        dump = backups / "db_demo_20240101_010101.dump"
        dump.write_bytes(b"x")

        found_dump, found_fs = _find_backup_files(project_dir)
        assert found_dump == dump
        assert found_fs is None

    def test_only_a_filestore_is_reported_without_a_dump(self, project_dir):
        backups = project_dir / "rkd_backups"
        backups.mkdir()
        fs = backups / "filestore_demo_20240101_010101.tar.gz"
        fs.write_bytes(b"x")

        found_dump, found_fs = _find_backup_files(project_dir)
        assert found_dump is None
        assert found_fs == fs

    def test_the_most_recent_of_several_dumps_is_picked(self, project_dir):
        """Mutation (a): always returning (None, None) still passes any
        assertion that only checks the "no backups" case -- this one
        requires the real, most-recent files to come back.
        """
        backups = project_dir / "rkd_backups"
        backups.mkdir()
        older = backups / "db_demo_20240101_010101.dump"
        newer = backups / "db_demo_20240202_020202.dump"
        older.write_bytes(b"old")
        newer.write_bytes(b"new")

        found_dump, _found_fs = _find_backup_files(project_dir)

        assert found_dump == newer

    def test_both_files_are_found_together(self, project_dir):
        backups = project_dir / "rkd_backups"
        backups.mkdir()
        dump = backups / "db_demo_20240101_010101.dump"
        fs = backups / "filestore_demo_20240101_010101.tar.gz"
        dump.write_bytes(b"x")
        fs.write_bytes(b"x")

        assert _find_backup_files(project_dir) == (dump, fs)


class TestCheckPortsAutoAccept:
    """Only the non-interactive path (auto_accept=True). The interactive
    fork exists precisely so a human answers a `questionary.confirm`, which
    is what auto_accept lets a caller skip -- exercising it here would mean
    faking a TTY prompt for no gain.
    """

    def test_both_ports_free_are_kept_unchanged(self, monkeypatch):
        import rocketdoo.core.unpack as core_unpack

        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: False)

        def _must_not_run(*_a, **_k):
            raise AssertionError("find_available_port must not run when nothing is busy")

        monkeypatch.setattr(core_unpack, "find_available_port", _must_not_run)

        result = _check_ports({"odoo_port": 8069, "vsc_port": 8888}, auto_accept=True)

        assert result == (8069, 8888, False)

    def test_a_busy_odoo_port_is_replaced_by_the_suggestion(self, monkeypatch):
        import rocketdoo.core.unpack as core_unpack

        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: port == 8069)
        monkeypatch.setattr(core_unpack, "find_available_port", lambda start: 19070)

        result = _check_ports({"odoo_port": 8069, "vsc_port": 8888}, auto_accept=True)

        assert result == (19070, 8888, True)

    def test_both_ports_busy_are_both_replaced(self, monkeypatch):
        """Mutation (d): auto_accept=True still returning the requested,
        occupied ports instead of the suggested free ones.
        """
        import rocketdoo.core.unpack as core_unpack

        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: True)
        monkeypatch.setattr(core_unpack, "find_available_port", lambda start: start + 1000)

        odoo_port, vsc_port, changed = _check_ports({"odoo_port": 8069, "vsc_port": 8888}, auto_accept=True)

        assert odoo_port == 9070
        assert vsc_port == 9889
        assert changed is True

    def test_auto_accept_never_prompts(self, monkeypatch):
        import questionary

        import rocketdoo.core.unpack as core_unpack

        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: True)
        monkeypatch.setattr(core_unpack, "find_available_port", lambda start: start + 1)

        def _must_not_prompt(*_a, **_k):
            raise AssertionError("auto_accept=True must never prompt")

        monkeypatch.setattr(questionary, "confirm", _must_not_prompt)

        _check_ports({"odoo_port": 8069, "vsc_port": 8888}, auto_accept=True)

    def test_missing_keys_fall_back_to_the_rocketdoo_defaults(self, monkeypatch):
        import rocketdoo.core.unpack as core_unpack

        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: False)

        result = _check_ports({}, auto_accept=True)

        assert result == (8069, 8888, False)


COMPOSE_TEMPLATE = """name: demo
services:
  web:
    build: .
    restart: unless-stopped
    image: demo
    container_name: odoo-demo
    depends_on:
      - db
    ports:
      - "8069:8069"
      - "8888:8888"
    volumes:
      - demo-web-data:/var/lib/odoo
      - ./config:/etc/odoo
      - ./addons:/usr/lib/python3/dist-packages/odoo/extra-addons
      #- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise
      - ./.vscode:/usr/lib/python3/dist-packages/odoo/.vscode
  db:
    restart: unless-stopped
    image: postgres:16
    container_name: db-demo
    environment:
      - POSTGRES_DB=postgres
      - POSTGRES_PASSWORD_FILE=/run/secrets/postgresql_password
      - POSTGRES_USER=root
      - PGDATA=/var/lib/postgresql/data/pgdata
    volumes:
      - demo-db-data:/var/lib/postgresql/data/pgdata
    secrets:
      - postgresql_password

  # rkd:mailpit
  #mailpit:
  #  image: axllent/mailpit:latest
  #  container_name: demo-mailpit
  #  restart: unless-stopped
  #  ports:
  #    - "1025:1025"
  #    - "8025:8025"
  #  environment:
  #    MP_MAX_MESSAGES: 5000
  # /rkd:mailpit

volumes:
  demo-web-data:
  demo-db-data:
  # rkd:mailpit
  #demo-mailpit-data:
  # /rkd:mailpit

secrets:
  postgresql_password:
    file: odoo_pg_pass
"""


class TestUpdatePortsInCompose:
    """The whole point is the part it does NOT touch: docker-compose.yaml is
    user-edited, and a rewrite that reaches beyond the two port mappings is
    the single most expensive bug this module could ship. Every assertion
    here compares the full file, not just the lines that were supposed to
    change.
    """

    def test_only_the_two_port_mappings_change(self, project_dir):
        compose_path = project_dir / "docker-compose.yaml"
        compose_path.write_text(COMPOSE_TEMPLATE)

        _update_ports_in_compose(project_dir, 9001, 9002)

        expected = COMPOSE_TEMPLATE.replace('"8069:8069"', '"9001:8069"').replace('"8888:8888"', '"9002:8888"')
        assert compose_path.read_text() == expected

    def test_the_mailpit_ports_are_never_touched(self, project_dir):
        """A regex loose enough to also rewrite the debug mapping could
        just as easily reach for unrelated quoted ports like Mailpit's --
        the fixture carries them specifically to catch that.
        """
        compose_path = project_dir / "docker-compose.yaml"
        compose_path.write_text(COMPOSE_TEMPLATE)

        _update_ports_in_compose(project_dir, 9001, 9002)

        content = compose_path.read_text()
        assert '"1025:1025"' in content
        assert '"8025:8025"' in content

    def test_the_debug_port_does_not_receive_the_odoo_port(self, project_dir):
        """Mutation (b), directly: writing new_odoo_port into the 8888
        mapping too would leave '"9001:8888"' instead of '"9002:8888"'.
        """
        compose_path = project_dir / "docker-compose.yaml"
        compose_path.write_text(COMPOSE_TEMPLATE)

        _update_ports_in_compose(project_dir, 9001, 9002)

        content = compose_path.read_text()
        assert '"9002:8888"' in content
        assert '"9001:8888"' not in content

    def test_yml_extension_is_also_supported(self, project_dir):
        compose_path = project_dir / "docker-compose.yml"
        compose_path.write_text(COMPOSE_TEMPLATE)

        _update_ports_in_compose(project_dir, 9001, 9002)

        assert '"9001:8069"' in compose_path.read_text()

    def test_a_missing_compose_file_is_a_silent_no_op(self, project_dir):
        _update_ports_in_compose(project_dir, 9001, 9002)

        assert not (project_dir / "docker-compose.yaml").exists()
        assert not (project_dir / "docker-compose.yml").exists()


class TestGetDbContainerName:
    def test_reads_the_container_name_from_compose(self, project_dir):
        (project_dir / "docker-compose.yaml").write_text(
            "services:\n  db:\n    container_name: db-demo\n  web:\n    container_name: odoo-demo\n"
        )

        assert _get_db_container_name(project_dir) == "db-demo"

    def test_a_missing_compose_file_returns_none(self, project_dir):
        assert _get_db_container_name(project_dir) is None

    def test_a_compose_without_a_db_service_returns_none(self, project_dir):
        (project_dir / "docker-compose.yaml").write_text("services:\n  web:\n    container_name: odoo-demo\n")

        assert _get_db_container_name(project_dir) is None


def _write_project(project_dir):
    """A minimal project inspect()/unpack() will recognise: project_exists()
    only checks for a Dockerfile plus a compose file, not their contents.
    """
    (project_dir / "docker-compose.yaml").write_text(
        "services:\n"
        "  web:\n"
        "    container_name: odoo-demo\n"
        "    ports:\n"
        '      - "8069:8069"\n'
        '      - "8888:8888"\n'
        "  db:\n"
        "    container_name: db-demo\n"
    )
    (project_dir / "Dockerfile").write_text("FROM odoo:18.0\n")
    return project_dir


class TestCoreUnpackInspect:
    """RF9.1/RF9.2: inspect() is read-only and never prompts -- it only
    hands the CLI enough to build its own questions.
    """

    def test_inspect_never_writes_anything(self, project_dir):
        """Mutation (c): inspect() must not touch the filesystem at all."""
        _write_project(project_dir)
        compose_before = (project_dir / "docker-compose.yaml").read_text()
        entries_before = sorted(p.name for p in project_dir.iterdir())

        core_unpack.inspect(project_dir)

        assert sorted(p.name for p in project_dir.iterdir()) == entries_before
        assert (project_dir / "docker-compose.yaml").read_text() == compose_before

    def test_has_meta_is_false_without_rkd_shared_json(self, project_dir):
        """Case 7 of spec.md: no manifest reads as has_meta: False, not as
        an error and not as a silent default.
        """
        _write_project(project_dir)

        info = core_unpack.inspect(project_dir)

        assert info["has_meta"] is False

    def test_has_meta_is_false_for_a_manifest_missing_the_rkd_shared_flag(self, project_dir):
        _write_project(project_dir)
        (project_dir / "rkd-shared.json").write_text(json.dumps({"odoo_port": 9999}))

        info = core_unpack.inspect(project_dir)

        assert info["has_meta"] is False
        assert info["suggested_ports"]["odoo_port"] == 8069, "a manifest without rkd_shared must not leak its ports"

    def test_has_meta_is_true_with_a_valid_manifest(self, project_dir):
        _write_project(project_dir)
        (project_dir / "rkd-shared.json").write_text(json.dumps({"rkd_shared": True, "odoo_port": 8069}))

        info = core_unpack.inspect(project_dir)

        assert info["has_meta"] is True
        assert info["meta"]["odoo_port"] == 8069

    def test_reports_port_conflicts_and_suggestions(self, project_dir, monkeypatch):
        _write_project(project_dir)
        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: port == 8069)
        monkeypatch.setattr(core_unpack, "find_available_port", lambda start: 19070)

        info = core_unpack.inspect(project_dir)

        assert info["port_conflicts"] == {"odoo_port": True, "vsc_port": False}
        assert info["suggested_ports"] == {"odoo_port": 19070, "vsc_port": 8888}


class TestRestoreStepsAreNotCapped:
    """The machine receiving a shared environment is the one that has to pull
    PostgreSQL and build the project image, and `subprocess.run(timeout=...)`
    does not just report a timeout -- it kills the child. A cap on these two
    turns a first unpack into a silent partial restore: no database, or no
    filestore, under a panel that says the environment is ready.

    The pre-#143 code ran both uncapped; the round trip cannot catch a
    regression here because it runs with the images already cached.
    """

    def _record(self, monkeypatch):
        seen = []

        def _fake(*args, **kwargs):
            seen.append({"args": args, "timeout": kwargs.get("timeout", "ABSENT")})
            return {"ok": True, "stdout": "", "stderr": ""}

        monkeypatch.setattr(core_unpack, "run_compose_result", _fake)
        return seen

    def test_launching_the_database_has_no_timeout(self, project_dir, monkeypatch):
        seen = self._record(monkeypatch)

        core_unpack._launch_db_only(project_dir, lambda *_a: None)

        assert seen[0]["args"] == ("up", "-d", "db")
        assert seen[0]["timeout"] is None

    def test_initialising_the_odoo_volume_has_no_timeout(self, project_dir, monkeypatch):
        seen = self._record(monkeypatch)
        monkeypatch.setattr(core_unpack.time, "sleep", lambda *_a: None)
        monkeypatch.setattr(core_unpack.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))

        core_unpack._init_odoo_volume(project_dir, "odoo-demo", lambda *_a: None)

        assert seen[0]["args"] == ("up", "-d", "web")
        assert seen[0]["timeout"] is None


class TestCoreUnpackOrchestration:
    """Exercises unpack()'s own decisions -- which fields to trust, whether
    to call the restore chain at all -- with every low-level Docker step
    replaced by a fake. The docker argv itself is already characterised by
    the leaves above (TestUpdatePortsInCompose, TestGetDbContainerName) and
    by the real round trip in tests/test_e2e_pack_unpack.py.

    Covers the five mutations #143 T13 names for core/unpack.py:
    (a) ports received vs. the meta's own ports, (b) a skipped or failed
    restore still reported as done, (c) inspect() writing something (above),
    (d) logs_tail empty regardless of outcome, (e) --no-restore ignored.
    """

    def _stub_clean_launch(self, monkeypatch, *, running=True, backups=(None, None)):
        monkeypatch.setattr(core_unpack, "_find_backup_files", lambda root: backups)
        monkeypatch.setattr(core_unpack, "_get_odoo_container_name", lambda root: "odoo-demo")
        monkeypatch.setattr(core_unpack, "_launch_environment", lambda root, build, report: True)
        monkeypatch.setattr(core_unpack, "_is_container_running", lambda name: running)
        monkeypatch.setattr(core_unpack.time, "sleep", lambda *_a: None)

    def test_unpack_uses_the_ports_it_receives_not_the_meta_ports(self, project_dir, monkeypatch):
        """Mutation (a)."""
        _write_project(project_dir)
        (project_dir / "rkd-shared.json").write_text(json.dumps({"rkd_shared": True, "odoo_port": 8069, "vsc_port": 8888}))
        self._stub_clean_launch(monkeypatch)

        report = core_unpack.unpack(project_dir, ports={"odoo_port": 19070, "vsc_port": 19071}, restore=False)

        assert report["ports"] == {"odoo_port": 19070, "vsc_port": 19071}
        assert report["ports_changed"] is True
        content = (project_dir / "docker-compose.yaml").read_text()
        assert '"19070:8069"' in content
        assert '"19071:8888"' in content

    def test_ports_left_alone_when_no_conflict_is_reported(self, project_dir, monkeypatch):
        _write_project(project_dir)
        self._stub_clean_launch(monkeypatch)

        report = core_unpack.unpack(project_dir, ports={"odoo_port": 8069, "vsc_port": 8888}, restore=False)

        assert report["ports_changed"] is False
        assert '"8069:8069"' in (project_dir / "docker-compose.yaml").read_text()

    def test_no_restore_flag_skips_the_restore_even_with_a_backup_present(self, project_dir, monkeypatch):
        """Mutation (e): --no-restore (restore=False) must not reach the
        database at all, backup or no backup.
        """
        _write_project(project_dir)
        dump_dir = project_dir / "rkd_backups"
        dump_dir.mkdir()
        dump_path = dump_dir / "db_demo_20240101_010101.dump"
        dump_path.write_bytes(b"x")

        def _must_not_run(*_a, **_k):
            raise AssertionError("restore=False must never start the db-only step")

        monkeypatch.setattr(core_unpack, "_launch_db_only", _must_not_run)
        self._stub_clean_launch(monkeypatch, backups=(dump_path, None))

        report = core_unpack.unpack(project_dir, restore=False)

        assert report["db_restored"] is False

    def test_a_failed_restore_is_reported_as_not_restored(self, project_dir, monkeypatch):
        """Mutation (b): _restore_database returning None (pg_restore
        failed outright) must not be read downstream as a success.
        """
        _write_project(project_dir)
        dump_dir = project_dir / "rkd_backups"
        dump_dir.mkdir()
        dump_path = dump_dir / "db_demo_20240101_010101.dump"
        dump_path.write_bytes(b"x")

        monkeypatch.setattr(core_unpack, "_get_db_container_name", lambda root: "db-demo")
        monkeypatch.setattr(core_unpack, "_launch_db_only", lambda root, report: True)
        monkeypatch.setattr(core_unpack, "_wait_for_postgres", lambda container, report, max_wait=60: True)
        monkeypatch.setattr(core_unpack, "_restore_database", lambda container, dump, report: None)
        self._stub_clean_launch(monkeypatch, backups=(dump_path, None))

        report = core_unpack.unpack(project_dir, restore=True)

        assert report["db_restored"] is False

    def test_a_successful_restore_is_reported_as_restored(self, project_dir, monkeypatch):
        """The positive case for mutation (b): a real restored_db must still
        be reflected as True, so a mutation that hardcodes False is caught too.
        """
        _write_project(project_dir)
        dump_dir = project_dir / "rkd_backups"
        dump_dir.mkdir()
        dump_path = dump_dir / "db_demo_20240101_010101.dump"
        dump_path.write_bytes(b"x")

        monkeypatch.setattr(core_unpack, "_get_db_container_name", lambda root: "db-demo")
        monkeypatch.setattr(core_unpack, "_launch_db_only", lambda root, report: True)
        monkeypatch.setattr(core_unpack, "_wait_for_postgres", lambda container, report, max_wait=60: True)
        monkeypatch.setattr(core_unpack, "_restore_database", lambda container, dump, report: "demo")
        monkeypatch.setattr(core_unpack, "_clear_generated_assets", lambda *a, **k: None)
        self._stub_clean_launch(monkeypatch, backups=(dump_path, None))

        report = core_unpack.unpack(project_dir, restore=True)

        assert report["db_restored"] is True

    def test_a_database_that_will_not_start_warns_and_carries_on(self, project_dir, monkeypatch):
        """T13 changed this on purpose and it had no test.

        The command used to `return` here, aborting the whole unpack without
        a word: no restore, no environment, no panel, exit 0. A service must
        not abort in silence, so the failure now travels in `warnings` and
        the environment still comes up for the user to fix by hand. The
        restore must NOT be attempted against a database that never started.
        """
        _write_project(project_dir)
        dump_dir = project_dir / "rkd_backups"
        dump_dir.mkdir()
        dump_path = dump_dir / "db_demo_20240101_010101.dump"
        dump_path.write_bytes(b"x")

        def _must_not_run(*_a, **_k):
            raise AssertionError("no restore may be attempted when the database never started")

        monkeypatch.setattr(core_unpack, "_get_db_container_name", lambda root: "db-demo")
        monkeypatch.setattr(core_unpack, "_launch_db_only", lambda root, report: False)
        monkeypatch.setattr(core_unpack, "_wait_for_postgres", _must_not_run)
        monkeypatch.setattr(core_unpack, "_restore_database", _must_not_run)
        self._stub_clean_launch(monkeypatch, backups=(dump_path, None))

        report = core_unpack.unpack(project_dir, restore=True)

        assert report["db_restored"] is False
        assert any("database service" in w for w in report["warnings"])
        assert report["started"] is True

    def test_logs_tail_is_populated_when_the_environment_fails_to_start(self, project_dir, monkeypatch):
        """Mutation (d), the failure side: a real failure must produce
        real log lines, not an empty list.
        """
        _write_project(project_dir)
        self._stub_clean_launch(monkeypatch, running=False)
        monkeypatch.setattr(core_unpack, "_capture_logs_tail", lambda name, lines=30: ["boom: exit 1"])

        report = core_unpack.unpack(project_dir, restore=False)

        assert report["started"] is False
        assert report["logs_tail"] == ["boom: exit 1"]

    def test_logs_tail_stays_empty_on_a_clean_start(self, project_dir, monkeypatch):
        """Mutation (d), the success side: logs must not be captured (and
        therefore cannot be non-empty) when nothing failed.
        """
        _write_project(project_dir)
        self._stub_clean_launch(monkeypatch, running=True)

        def _must_not_run(*_a, **_k):
            raise AssertionError("logs must not be captured on a clean start")

        monkeypatch.setattr(core_unpack, "_capture_logs_tail", _must_not_run)

        report = core_unpack.unpack(project_dir, restore=False)

        assert report["started"] is True
        assert report["logs_tail"] == []

    def test_missing_project_raises_instead_of_silently_doing_nothing(self, project_dir):
        with pytest.raises(core_unpack.UnpackError):
            core_unpack.unpack(project_dir)

    def test_ssh_key_none_never_touches_the_dockerfile(self, project_dir, monkeypatch):
        _write_project(project_dir)
        self._stub_clean_launch(monkeypatch)
        original = (project_dir / "Dockerfile").read_text()

        report = core_unpack.unpack(project_dir, restore=False, ssh_key=None)

        assert report["ssh_configured"] is False
        assert (project_dir / "Dockerfile").read_text() == original

    def test_none_and_a_callback_produce_the_same_report(self, project_dir, monkeypatch):
        """CA3-equivalent for unpack(): on_progress=None must not change
        what gets decided, only whether anything is reported along the way.
        A port change forces at least one report() call inside unpack()
        itself, not just inside the stubbed-out leaves.
        """
        _write_project(project_dir)
        self._stub_clean_launch(monkeypatch)
        ports = {"odoo_port": 19070, "vsc_port": 19071}

        report_silent = core_unpack.unpack(project_dir, ports=ports, restore=False, on_progress=None)

        events = []
        report_cb = core_unpack.unpack(
            project_dir,
            ports=ports,
            restore=False,
            on_progress=lambda message, level="info": events.append((message, level)),
        )

        assert report_silent == report_cb
        assert events, "the callback must have been invoked"


class TestUnpackRefusesANonProjectBeforeAsking:
    """`unpack()` refuses a directory that is not a Rocketdoo project, and the
    CLI has to say so before it starts asking.

    Walking the user through the metadata, the port review and the SSH key
    selection only to refuse at the end is a worse way to deliver the same
    answer -- and the pre-#143 command checked this first.
    """

    def test_it_refuses_without_prompting(self, project_dir, monkeypatch):
        from click.testing import CliRunner

        from rocketdoo import unpack_environment
        from rocketdoo.cli import main

        def _must_not_ask(*_a, **_k):
            raise AssertionError("nothing may be asked before the project is validated")

        monkeypatch.setattr(unpack_environment.questionary, "confirm", _must_not_ask)
        monkeypatch.setattr(unpack_environment.questionary, "select", _must_not_ask)

        result = CliRunner().invoke(main, ["unpack"])

        assert result.exit_code == 0, result.output
        assert "No Rocketdoo project" in " ".join(result.output.split())
