"""Unit tests for unpack_environment.py -- the safety net before its
extraction to core/unpack.py in #143 T13.

rocketdoo/unpack_environment.py has 749 lines and, until now, zero tests.
These pin down the five helpers `core/unpack.py` will inherit verbatim:
_load_shared_meta, _find_backup_files, _check_ports (only its non-interactive
path -- the interactive one exists precisely so a human is asked instead),
_update_ports_in_compose, and _get_db_container_name. They run in
milliseconds; the real round trip against Docker lives in
tests/test_e2e_pack_unpack.py.
"""

import json

from rocketdoo.unpack_environment import (
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
        import rocketdoo.unpack_environment as unpack_environment

        monkeypatch.setattr(unpack_environment, "is_port_in_use", lambda port: False)

        def _must_not_run(*_a, **_k):
            raise AssertionError("find_available_port must not run when nothing is busy")

        monkeypatch.setattr(unpack_environment, "find_available_port", _must_not_run)

        result = _check_ports({"odoo_port": 8069, "vsc_port": 8888}, auto_accept=True)

        assert result == (8069, 8888, False)

    def test_a_busy_odoo_port_is_replaced_by_the_suggestion(self, monkeypatch):
        import rocketdoo.unpack_environment as unpack_environment

        monkeypatch.setattr(unpack_environment, "is_port_in_use", lambda port: port == 8069)
        monkeypatch.setattr(unpack_environment, "find_available_port", lambda start: 19070)

        result = _check_ports({"odoo_port": 8069, "vsc_port": 8888}, auto_accept=True)

        assert result == (19070, 8888, True)

    def test_both_ports_busy_are_both_replaced(self, monkeypatch):
        """Mutation (d): auto_accept=True still returning the requested,
        occupied ports instead of the suggested free ones.
        """
        import rocketdoo.unpack_environment as unpack_environment

        monkeypatch.setattr(unpack_environment, "is_port_in_use", lambda port: True)
        monkeypatch.setattr(unpack_environment, "find_available_port", lambda start: start + 1000)

        odoo_port, vsc_port, changed = _check_ports({"odoo_port": 8069, "vsc_port": 8888}, auto_accept=True)

        assert odoo_port == 9070
        assert vsc_port == 9889
        assert changed is True

    def test_auto_accept_never_prompts(self, monkeypatch):
        import questionary

        import rocketdoo.unpack_environment as unpack_environment

        monkeypatch.setattr(unpack_environment, "is_port_in_use", lambda port: True)
        monkeypatch.setattr(unpack_environment, "find_available_port", lambda start: start + 1)

        def _must_not_prompt(*_a, **_k):
            raise AssertionError("auto_accept=True must never prompt")

        monkeypatch.setattr(questionary, "confirm", _must_not_prompt)

        _check_ports({"odoo_port": 8069, "vsc_port": 8888}, auto_accept=True)

    def test_missing_keys_fall_back_to_the_rocketdoo_defaults(self, monkeypatch):
        import rocketdoo.unpack_environment as unpack_environment

        monkeypatch.setattr(unpack_environment, "is_port_in_use", lambda port: False)

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
