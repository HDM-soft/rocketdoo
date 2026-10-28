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
    _compose_project_name,
    _docker_inventory,
    _find_backup_files,
    _get_db_container_name,
    _load_shared_meta,
    _rename_project_in_compose,
    _resolve_project_name,
    _update_db_host_in_conf,
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

    def test_a_file_the_user_keeps_here_never_wins_the_newest_slot(self, project_dir):
        """The newest is decided by sorting filenames, so a file that is not a
        backup at all can sort above the real one. `pg_restore` would then be
        sent after a database named from its filename, fail, and take the
        filestore with it -- that step is nested under a successful restore.

        pack leaves the user's files under rkd_backups/ alone (#225), which
        makes it this function's job to recognise what pack itself wrote.
        """
        backups = project_dir / "rkd_backups"
        backups.mkdir()
        real = backups / "db_aaa_20260106_120000.dump"
        real.write_bytes(b"x")
        (backups / "db_zzz_notes.dump").write_bytes(b"x")
        (backups / "filestore_zzz_notes.tar.gz").write_bytes(b"x")

        found_dump, found_fs = _find_backup_files(project_dir)

        assert found_dump == real
        assert found_fs is None

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


class TestComposeProjectName:
    def test_reads_the_top_level_name(self, project_dir):
        (project_dir / "docker-compose.yaml").write_text(COMPOSE_TEMPLATE)

        assert _compose_project_name(project_dir) == "demo"

    def test_a_missing_compose_is_none(self, project_dir):
        assert _compose_project_name(project_dir) is None

    def test_a_compose_without_name_is_none(self, project_dir):
        (project_dir / "docker-compose.yaml").write_text("services:\n  web:\n    image: odoo\n")

        assert _compose_project_name(project_dir) is None

    def test_a_nested_name_key_is_not_the_project_name(self, project_dir):
        """Mutation: dropping the line anchor would read `name: web` from
        inside a service and rename the wrong thing.
        """
        (project_dir / "docker-compose.yaml").write_text("services:\n  web:\n    name: web\n")

        assert _compose_project_name(project_dir) is None


class _Run:
    """Stand-in for subprocess.run keyed on the docker subcommand."""

    def __init__(self, compose_ls=None, volume_ls=None):
        self.compose_ls = compose_ls
        self.volume_ls = volume_ls

    def __call__(self, cmd, **kwargs):
        reply = self.volume_ls if "volume" in cmd else self.compose_ls
        if isinstance(reply, Exception):
            raise reply
        return reply


def _done(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class TestDockerInventory:
    def _patch(self, monkeypatch, run):
        monkeypatch.setattr("rocketdoo.core.compose.subprocess.run", run)
        monkeypatch.setattr(core_unpack.subprocess, "run", run)

    def test_collects_projects_and_volumes(self, monkeypatch):
        run = _Run(
            _done('[{"Name":"demo","Status":"running(1)","ConfigFiles":"/a/demo/docker-compose.yaml"}]'),
            # `{project}_{key}`, which is how Compose actually names a project
            # volume; `anon` with no label is an anonymous one.
            _done("demo_demo-db-data\tdemo\nanon\t\n"),
        )
        self._patch(monkeypatch, run)

        assert _docker_inventory() == {
            "ok": True,
            "projects": {"demo": ["/a/demo/docker-compose.yaml"]},
            "volumes": {"demo_demo-db-data": "demo", "anon": ""},
        }

    def test_a_failing_compose_ls_is_not_ok(self, monkeypatch):
        self._patch(monkeypatch, _Run(_done("", 1, "boom"), _done("v\tp\n")))

        assert _docker_inventory()["ok"] is False

    def test_a_failing_volume_ls_is_not_ok(self, monkeypatch):
        """Mutation: "one query answering is enough" would treat a host whose
        volumes could not be listed as having none, and call a name free.
        """
        self._patch(monkeypatch, _Run(_done("[]"), _done("", 1, "boom")))

        assert _docker_inventory()["ok"] is False

    def test_a_missing_docker_binary_is_not_ok(self, monkeypatch):
        self._patch(monkeypatch, _Run(FileNotFoundError("docker"), FileNotFoundError("docker")))

        assert _docker_inventory()["ok"] is False

    def test_a_failure_carries_no_partial_data(self, monkeypatch):
        """Mutation: keeping the projects already parsed when volume ls fails
        hands the resolver half an inventory that reads as "complete".
        """
        projects = _done('[{"Name":"demo","ConfigFiles":"/a/docker-compose.yaml"}]')
        for volume_ls in (_done("", 1, "boom"), FileNotFoundError("docker")):
            self._patch(monkeypatch, _Run(projects, volume_ls))

            assert _docker_inventory() == {"ok": False, "projects": {}, "volumes": {}}


def _inv(projects=None, volumes=None, ok=True):
    return {"ok": ok, "projects": projects or {}, "volumes": volumes or {}}


class TestResolveProjectName:
    def test_no_name_line_is_not_renamable(self, tmp_path):
        """Mutation: renaming anyway would rewrite container_name alone, the
        one path that destroys the other copy's environment.
        """
        got = _resolve_project_name(tmp_path, None, {}, _inv({"demo": ["/x/docker-compose.yaml"]}))

        assert got["renamable"] is False
        assert got["conflict"] is False
        assert got["name"] is None

    def test_an_already_renamed_copy_is_left_alone(self, tmp_path):
        """Mutation: ignoring the manifest renames on every unpack."""
        got = _resolve_project_name(tmp_path, "demo-2", {"project_name": "demo"}, _inv({"demo-2": ["/x/docker-compose.yaml"]}))

        assert got["conflict"] is False
        assert got["name"] == "demo-2"

    def test_an_unreachable_docker_is_undetermined(self, tmp_path):
        """`is None`, not falsy: "unknown" must not read as "free" nor as "taken"."""
        got = _resolve_project_name(tmp_path, "demo", {}, _inv(ok=False))

        assert got["conflict"] is None
        assert got["name"] == "demo"
        assert "Docker" in got["reason"]

    def test_a_free_name_is_kept(self, tmp_path):
        got = _resolve_project_name(tmp_path, "demo", {}, _inv({"other": ["/x/docker-compose.yaml"]}))

        assert got["conflict"] is False
        assert got["name"] == "demo"

    def test_a_foreign_project_forces_a_suffix_and_is_named(self, tmp_path):
        got = _resolve_project_name(tmp_path, "demo", {}, _inv({"demo": ["/other/demo/docker-compose.yaml"]}))

        assert got["conflict"] is True
        assert got["name"] == "demo-2"
        assert "/other/demo" in got["reason"]

    def test_our_own_project_is_not_a_conflict(self, tmp_path):
        """Mutation: checking the name alone re-renames a copy that is
        already running from this very directory.
        """
        inv = _inv({"demo": [str(tmp_path / "docker-compose.yaml")]})

        got = _resolve_project_name(tmp_path, "demo", {}, inv)

        assert got["conflict"] is False

    def test_a_foreign_volume_alone_is_a_conflict(self, tmp_path):
        """The original shut down with `rkd down` is absent from `compose ls`
        but its volumes remain; restoring over them would overwrite its database.

        The volume names here are the ones Docker actually produces,
        `{project}_{key}` with an underscore. An earlier version of this test
        used `demo-db-data`, a name Compose never writes, and so passed against
        a check that tested `startswith("demo-")` and therefore never fired on
        a real volume: 23 of the shut-down projects on the development machine
        reported no conflict at all. Feed this one real shapes or it pins
        nothing.
        """
        inv = _inv(volumes={"demo_demo-db-data": "demo", "demo_demo-web-data": "demo"})

        got = _resolve_project_name(tmp_path, "demo", {}, inv)

        assert got["conflict"] is True
        assert got["name"] == "demo-2"
        assert "demo_demo-db-data" in got["reason"]

    def test_a_project_whose_name_merely_starts_the_same_is_not_a_conflict(self, tmp_path):
        """`demos-v19` is not `demo`. Matching the volume name by prefix made
        every sibling look like a collision and renamed copies that had none.
        """
        inv = _inv(volumes={"demos-v19_demos-v19-db-data": "demos-v19"})

        assert _resolve_project_name(tmp_path, "demo", {}, inv)["conflict"] is False

    def test_our_own_volume_is_not_a_conflict(self, tmp_path):
        inv = _inv({"demo": [str(tmp_path / "docker-compose.yaml")]}, {"demo_demo-db-data": "demo"})

        assert _resolve_project_name(tmp_path, "demo", {}, inv)["conflict"] is False

    def test_the_suffix_keeps_counting(self, tmp_path):
        """Mutation: a fixed `-2` would collide with the second copy."""
        inv = _inv({"demo": ["/a/docker-compose.yaml"], "demo-2": ["/b/docker-compose.yaml"]})

        assert _resolve_project_name(tmp_path, "demo", {}, inv)["name"] == "demo-3"

    def test_an_exhausted_range_does_not_rename(self, tmp_path):
        projects = {"demo": ["/a/docker-compose.yaml"]}
        projects.update({f"demo-{k}": ["/a/docker-compose.yaml"] for k in range(2, 100)})

        got = _resolve_project_name(tmp_path, "demo", {}, _inv(projects))

        assert got["conflict"] is True
        assert got["name"] == "demo"


class TestRenameProjectInCompose:
    RENAMES = [
        ("name: demo\n", "name: demo-2\n"),
        ("    image: demo\n", "    image: demo-2\n"),
        ("container_name: odoo-demo\n", "container_name: odoo-demo-2\n"),
        ("container_name: db-demo\n", "container_name: db-demo-2\n"),
        ("container_name: demo-mailpit\n", "container_name: demo-2-mailpit\n"),
    ]

    def _write(self, project_dir, text=COMPOSE_TEMPLATE):
        path = project_dir / "docker-compose.yaml"
        path.write_text(text)
        return path

    def test_only_the_identity_lines_change(self, project_dir):
        """Whole-file comparison: a regex that strays past the ten identity
        lines fails here, not in a user's compose.
        """
        path = self._write(project_dir)
        expected = COMPOSE_TEMPLATE
        for before, after in self.RENAMES:
            assert before in expected
            expected = expected.replace(before, after)

        assert _rename_project_in_compose(project_dir, "demo", "demo-2") is True

        assert path.read_text() == expected

    def test_mailpit_markers_survive_and_the_block_stays_disabled(self, project_dir):
        """Mutation: a PyYAML round trip drops the markers and the comments."""
        from rocketdoo.core.mailpit import is_enabled

        path = self._write(project_dir)
        before = is_enabled(project_dir)

        _rename_project_in_compose(project_dir, "demo", "demo-2")

        content = path.read_text()
        assert "  # rkd:mailpit\n" in content
        assert "  # /rkd:mailpit\n" in content
        assert "  #mailpit:\n" in content
        assert is_enabled(project_dir) == before

    def test_a_project_named_postgres_keeps_the_postgres_image(self, project_dir):
        """Mutation: token replacement on `image:` would turn the database
        image into `postgres-2:16`.
        """
        path = self._write(project_dir)

        _rename_project_in_compose(project_dir, "postgres", "postgres-2")

        assert "    image: postgres:16\n" in path.read_text()

    def test_unrelated_lines_are_untouched(self, project_dir):
        """Mutation: a global `text.replace(old, new)` reaches all of these
        when the project is called after a word the file uses elsewhere.
        """
        path = self._write(project_dir)
        for old in ("db", "config", "odoo", "postgres", "web"):
            path.write_text(COMPOSE_TEMPLATE)
            _rename_project_in_compose(project_dir, old, f"{old}-2")
            content = path.read_text()
            for line in (
                "      - ./config:/etc/odoo\n",
                "      - POSTGRES_DB=postgres\n",
                "      - postgresql_password\n",
                "    file: odoo_pg_pass\n",
                "      - ./.vscode:/usr/lib/python3/dist-packages/odoo/.vscode\n",
            ):
                assert line in content, (old, line)

    def test_depends_on_db_survives_a_project_named_db(self, project_dir):
        path = self._write(project_dir)

        _rename_project_in_compose(project_dir, "db", "db-2")

        assert "    depends_on:\n      - db\n" in path.read_text()

    def test_a_similarly_named_token_is_untouched(self, project_dir):
        """Mutation: a bare substring match (no token boundary) renames
        `mydemo` and `demo.demos`, which belong to something else.
        """
        text = COMPOSE_TEMPLATE.replace("    container_name: db-demo\n", "    container_name: db-demo-mydemo_demo.demos\n")
        path = self._write(project_dir, text)

        _rename_project_in_compose(project_dir, "demo", "demo-2")

        assert "    container_name: db-demo-2-mydemo_demo.demos\n" in path.read_text()

    def test_volume_names_are_left_alone(self, project_dir):
        """Compose prefixes a named volume with the project, so `demo` and
        `demo-2` get `demo_demo-db-data` and `demo-2_demo-db-data` from the
        same key (measured). Renaming the key bought nothing and broke the
        shapes that cannot take it (#249)."""
        path = self._write(project_dir)

        _rename_project_in_compose(project_dir, "demo", "demo-2")

        content = path.read_text()
        assert "      - demo-web-data:/var/lib/odoo\n" in content
        assert "  demo-db-data:\n" in content
        assert "demo-2-db-data" not in content

    def test_an_external_volume_still_points_at_what_exists(self, project_dir):
        """Renaming the key of an `external: true` volume made `up` fail with
        "declared as external, but could not be found"."""
        text = COMPOSE_TEMPLATE.replace("  demo-db-data:\n", "  demo-db-data:\n    external: true\n")
        path = self._write(project_dir, text)

        _rename_project_in_compose(project_dir, "demo", "demo-2")

        assert "  demo-db-data:\n    external: true\n" in path.read_text()

    def test_the_long_volume_syntax_stays_consistent(self, project_dir):
        """The key was rewritten and the `source:` beside it was not, which
        left the file referring to a volume it no longer declared."""
        text = COMPOSE_TEMPLATE.replace(
            "      - demo-db-data:/var/lib/postgresql/data/pgdata\n",
            "      - type: volume\n        source: demo-db-data\n        target: /var/lib/postgresql/data/pgdata\n",
        )
        path = self._write(project_dir, text)

        _rename_project_in_compose(project_dir, "demo", "demo-2")

        content = path.read_text()
        assert "        source: demo-db-data\n" in content
        assert "  demo-db-data:\n" in content

    def test_a_public_image_of_the_same_name_is_not_renamed(self, project_dir):
        """A project named like a public image had the database service's
        `image: postgres` rewritten to `postgres-2`, which is a tag that does
        not exist. Two services naming one image is exactly that case, and
        there neither is renamed: the copies share a tag, which costs a
        rebuild and destroys nothing (#249)."""
        text = COMPOSE_TEMPLATE.replace("name: demo\n", "name: postgres\n")
        text = text.replace("    image: demo\n", "    image: postgres\n")
        text = text.replace("    image: postgres:16\n", "    image: postgres\n")
        path = self._write(project_dir, text)

        _rename_project_in_compose(project_dir, "postgres", "postgres-2")

        content = path.read_text()
        # The fixture's containers are named after `demo`, so the only
        # `postgres` tokens left to judge are the two image lines.
        assert "    image: postgres\n" in content
        assert "image: postgres-2" not in content
        assert "name: postgres-2\n" in content

    def test_the_built_image_is_renamed_whatever_the_order_of_the_keys(self, project_dir):
        """Asking the surrounding lines whether a service builds made the
        answer depend on `build:` being written above `image:`, and a compose
        with them the other way round got its containers renamed while its
        image kept the old tag -- a half rename, which is what the all or
        nothing rule exists to prevent (#249)."""
        text = COMPOSE_TEMPLATE.replace(
            "    build: .\n    restart: unless-stopped\n    image: demo\n",
            "    image: demo\n    build: .\n    restart: unless-stopped\n",
        )
        path = self._write(project_dir, text)

        _rename_project_in_compose(project_dir, "demo", "demo-2")

        content = path.read_text()
        assert "    image: demo-2\n" in content
        assert "    container_name: odoo-demo-2\n" in content

    def test_an_image_nobody_builds_is_not_renamed(self, project_dir):
        """A project that pulls its image instead of building it -- the tag
        exists in a registry, and `demo-2` does not: renaming it leaves an
        `up` that cannot find the image (#249)."""
        text = COMPOSE_TEMPLATE.replace("    build: .\n", "")
        path = self._write(project_dir, text)

        _rename_project_in_compose(project_dir, "demo", "demo-2")

        content = path.read_text()
        assert "    image: demo\n" in content
        assert "name: demo-2\n" in content
        assert "    container_name: odoo-demo-2\n" in content

    def test_a_compose_that_cannot_be_parsed_still_renames_the_identity(self, project_dir):
        """The image question is asked of the parsed YAML; when there is no
        parse, the answer is no -- a shared tag costs a rebuild, while the
        identity that decides whose containers these are is still rewritten."""
        text = COMPOSE_TEMPLATE + "\n\t- esto no es YAML valido\n"
        path = self._write(project_dir, text)

        assert _rename_project_in_compose(project_dir, "demo", "demo-2")

        content = path.read_text()
        assert "name: demo-2\n" in content
        assert "    container_name: odoo-demo-2\n" in content

    def test_crlf_line_endings_are_preserved(self, project_dir):
        """Mutation: `splitlines()` without keepends rejoins with \\n."""
        path = project_dir / "docker-compose.yaml"
        path.write_bytes(COMPOSE_TEMPLATE.replace("\n", "\r\n").encode())

        _rename_project_in_compose(project_dir, "demo", "demo-2")

        raw = path.read_bytes()
        assert b"name: demo-2\r\n" in raw
        assert raw.count(b"\n") == raw.count(b"\r\n")

    def test_a_missing_compose_returns_false(self, project_dir):
        assert _rename_project_in_compose(project_dir, "demo", "demo-2") is False


class TestUpdateDbHostInConf:
    CONF = "[options]\n; comment\n#db_host = False\ndb_host = db-demo\ndb_port = 5432\n"

    def _conf(self, project_dir, text):
        (project_dir / "config").mkdir()
        path = project_dir / "config" / "odoo.conf"
        path.write_text(text)
        return path

    def test_rewrites_only_the_matching_host(self, project_dir):
        path = self._conf(project_dir, self.CONF)

        assert _update_db_host_in_conf(project_dir, "db-demo", "db-demo-2") is True

        assert path.read_text() == self.CONF.replace("db_host = db-demo\n", "db_host = db-demo-2\n")

    def test_a_host_that_is_not_the_old_container_is_left_alone(self, project_dir):
        """Mutation: writing the new name unconditionally would clobber `db`
        or an external server the user pointed Odoo at.
        """
        text = self.CONF.replace("db-demo", "db")
        path = self._conf(project_dir, text)

        assert _update_db_host_in_conf(project_dir, "db-demo", "db-demo-2") is False

        assert path.read_text() == text

    def test_a_missing_conf_returns_false(self, project_dir):
        assert _update_db_host_in_conf(project_dir, "db-demo", "db-demo-2") is False


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

    def test_has_meta_is_false_for_a_manifest_missing_the_rkd_shared_flag(self, project_dir, monkeypatch):
        # Without this the assertion below reads the real host: anyone with an
        # Odoo already on 8069 -- the default this project hands out -- sees a
        # red suite for a reason that has nothing to do with the code.
        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: False)
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


class TestAManifestThatSaysThereIsNoDatabase:
    """The other half of #225, for ZIPs already in circulation.

    `unpack()` decides whether to restore from `db_dump is not None` alone,
    never from the manifest. A ZIP built before the pack-side fix carries
    the dumps of every earlier pack, so one packed with `--no-db` arrives
    with a database anyway -- an older one, from whenever that project was
    last packed with a backup. The recipient gets data the sender never
    meant to send.

    `has_db_backup` is only honoured when the manifest states it. A missing
    key means an older or hand-made manifest that cannot answer the
    question, and there the previous behaviour stands.
    """

    def _project_with_a_stale_pair(self, project_dir, monkeypatch, meta):
        """A (dump, filestore) pair, which is the shape pack always leaves:
        a lone dump would let a guard that only fires without a filestore
        pass while being off in every real ZIP.
        """
        _write_project(project_dir)
        (project_dir / "rkd-shared.json").write_text(json.dumps(meta))
        backups = project_dir / "rkd_backups"
        backups.mkdir(exist_ok=True)
        dump = backups / "db_vieja_20260101_120000.dump"
        tar = backups / "filestore_vieja_20260101_120000.tar.gz"
        dump.write_bytes(b"PGDMP-vieja")
        tar.write_bytes(b"\x1f\x8b-vieja")
        monkeypatch.setattr(core_unpack, "_find_backup_files", lambda root: (dump, tar))
        monkeypatch.setattr(core_unpack, "_get_odoo_container_name", lambda root: "odoo-demo")
        monkeypatch.setattr(core_unpack, "_launch_environment", lambda root, build, report: True)
        monkeypatch.setattr(core_unpack, "_is_container_running", lambda name: True)
        monkeypatch.setattr(core_unpack.time, "sleep", lambda *_a: None)
        started = []
        monkeypatch.setattr(core_unpack, "_launch_db_only", lambda root, report: started.append("db") or True)
        monkeypatch.setattr(core_unpack, "_init_odoo_volume", lambda root, container, report: started.append("volume") or True)
        return started

    def test_an_explicit_false_keeps_the_stale_pair_out_of_the_environment(self, project_dir, monkeypatch):
        started = self._project_with_a_stale_pair(project_dir, monkeypatch, {"rkd_shared": True, "has_db_backup": False})

        report = core_unpack.unpack(project_dir, restore=True)

        assert started == [], "neither the database nor the filestore restore may start"
        assert report["db_restored"] is False
        assert report["filestore_restored"] is False

    def test_the_recipient_is_told_which_file_was_ignored(self, project_dir, monkeypatch):
        """Silently dropping it would leave the recipient with an empty Odoo
        and a dump sitting right there, with nothing connecting the two.
        """
        self._project_with_a_stale_pair(project_dir, monkeypatch, {"rkd_shared": True, "has_db_backup": False})

        report = core_unpack.unpack(project_dir, restore=True)

        assert any("db_vieja_20260101_120000.dump" in w for w in report["warnings"]), report["warnings"]

    def test_a_manifest_without_the_key_still_restores(self, project_dir, monkeypatch):
        """An older manifest cannot answer, so nothing changes for it."""
        started = self._project_with_a_stale_pair(project_dir, monkeypatch, {"rkd_shared": True})

        core_unpack.unpack(project_dir, restore=True)

        assert started[:1] == ["db"]

    def test_a_manifest_rkd_never_wrote_still_restores(self, project_dir, monkeypatch):
        """Without `rkd_shared` the manifest is reset to {} before any of
        this, so its `has_db_backup` must not steer the restore either.
        """
        started = self._project_with_a_stale_pair(project_dir, monkeypatch, {"has_db_backup": False})

        core_unpack.unpack(project_dir, restore=True)

        assert started[:1] == ["db"]


class TestTheRewritersLeaveLineEndingsAlone:
    """Both in-place rewriters read and write bytes.

    `Path.read_text()` applies universal newlines, so it hands back a string
    with every CRLF already collapsed to LF. Remapping a single port in a
    compose saved with CRLF -- a Windows editor, or git's own core.autocrlf,
    on a tool whose users are largely on WSL -- rewrote every line in the
    file. Nothing asked for that, and a diff of the whole file is what the
    receiving developer sees.

    These two tests pin the read side, which is the half that misbehaves on
    Linux. The matching `write_bytes()` only pays off where os.linesep is
    CRLF, so reverting it alone survives every mutation run from here -- it
    stays for the platform this suite cannot reach, not for decoration.

    `_rename_project_in_compose` was written with bytes from the start; this
    pins both, so the pair cannot drift apart again.
    """

    CRLF = (
        b"name: demo\r\n"
        b"services:\r\n"
        b"  web:\r\n"
        b"    image: demo\r\n"
        b"    container_name: odoo-demo\r\n"
        b"    ports:\r\n"
        b'      - "8069:8069"\r\n'
        b'      - "8888:8888"\r\n'
    )

    def test_remapping_a_port_keeps_crlf(self, project_dir):
        compose = project_dir / "docker-compose.yaml"
        compose.write_bytes(self.CRLF)

        core_unpack._update_ports_in_compose(project_dir, 9069, 9888)

        after = compose.read_bytes()
        assert after.count(b"\r\n") == self.CRLF.count(b"\r\n")
        assert b'"9069:8069"' in after

    def test_renaming_the_project_keeps_crlf(self, project_dir):
        compose = project_dir / "docker-compose.yaml"
        compose.write_bytes(self.CRLF)

        core_unpack._rename_project_in_compose(project_dir, "demo", "demo-2")

        after = compose.read_bytes()
        assert after.count(b"\r\n") == self.CRLF.count(b"\r\n")
        assert b"name: demo-2\r\n" in after


def _write_named_project(project_dir):
    """The full compose template plus the odoo.conf rkd generates."""
    (project_dir / "docker-compose.yaml").write_text(COMPOSE_TEMPLATE)
    (project_dir / "Dockerfile").write_text("FROM odoo:18.0\n")
    (project_dir / "config").mkdir()
    (project_dir / "config" / "odoo.conf").write_text("[options]\ndb_host = db-demo\ndb_port = 5432\n")
    return project_dir


def _stub_launch(monkeypatch):
    monkeypatch.setattr(core_unpack, "_find_backup_files", lambda root: (None, None))
    monkeypatch.setattr(core_unpack, "_launch_environment", lambda root, build, report: True)
    monkeypatch.setattr(core_unpack, "_is_container_running", lambda name: True)
    monkeypatch.setattr(core_unpack.time, "sleep", lambda *_a: None)


class TestInspectProject:
    def _inventory(self, monkeypatch, inventory):
        monkeypatch.setattr(core_unpack, "_docker_inventory", lambda: inventory)

    def test_conflict_is_reported_with_the_five_keys(self, project_dir, monkeypatch):
        _write_named_project(project_dir)
        self._inventory(monkeypatch, _inv(projects={"demo": ["/elsewhere/docker-compose.yaml"]}))

        project = core_unpack.inspect(project_dir)["project"]

        assert project == {
            "name": "demo",
            "suggested_name": "demo-2",
            "conflict": True,
            "reason": project["reason"],
            "renamable": True,
        }
        assert "/elsewhere" in project["reason"]

    def test_docker_down_means_unknown_and_the_name_is_kept(self, project_dir, monkeypatch):
        """Mutation: reading an unreachable Docker as 'free' (False) or as a conflict."""
        _write_named_project(project_dir)
        self._inventory(monkeypatch, _inv(ok=False))

        project = core_unpack.inspect(project_dir)["project"]

        assert project["conflict"] is None
        assert project["suggested_name"] == project["name"] == "demo"

    def test_inspect_still_never_writes_with_a_conflict(self, project_dir, monkeypatch):
        _write_named_project(project_dir)
        self._inventory(monkeypatch, _inv(projects={"demo": ["/elsewhere/docker-compose.yaml"]}))
        before = {p: p.read_bytes() for p in project_dir.rglob("*") if p.is_file()}

        core_unpack.inspect(project_dir)

        assert {p: p.read_bytes() for p in project_dir.rglob("*") if p.is_file()} == before

    def test_the_old_keys_keep_their_meaning(self, project_dir, monkeypatch):
        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: False)
        _write_named_project(project_dir)
        self._inventory(monkeypatch, _inv())

        info = core_unpack.inspect(project_dir)

        for key in ("meta", "has_meta", "port_conflicts", "suggested_ports", "backups", "uses_private_repos", "ssh_keys"):
            assert key in info

    def test_mailpit_port_conflict_reads_the_real_ports(self, project_dir, monkeypatch):
        """Mutation: a fixed (1025, 8025) pair instead of the published ports."""
        _write_named_project(project_dir)
        compose = project_dir / "docker-compose.yaml"
        text = (
            compose.read_text()
            .replace("#    - ", "    - ")
            .replace("1025:1025", "1125:1025")
            .replace("8025:8025", "8125:8025")
        )
        compose.write_text(text.replace("  #mailpit:", "  mailpit:"))
        self._inventory(monkeypatch, _inv())

        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: port == 1025)
        assert core_unpack.inspect(project_dir)["mailpit_port_conflict"] is False

        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: port == 1125)
        info = core_unpack.inspect(project_dir)
        assert info["mailpit_port_conflict"] is True
        assert info["mailpit_ports"] == [1125, 8125]

    def test_mailpit_port_conflict_needs_the_block_enabled(self, project_dir, monkeypatch):
        """Mutation: warning whenever 1025/8025 is busy, enabled or not."""
        _write_named_project(project_dir)
        self._inventory(monkeypatch, _inv())
        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: port in (1025, 8025))

        assert core_unpack.inspect(project_dir)["mailpit_port_conflict"] is False

        monkeypatch.setattr(core_unpack, "mailpit_is_enabled", lambda root: True)
        assert core_unpack.inspect(project_dir)["mailpit_port_conflict"] is True

        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: False)
        assert core_unpack.inspect(project_dir)["mailpit_port_conflict"] is False


class TestUnpackProjectName:
    def test_without_a_name_the_compose_is_byte_for_byte_the_same(self, project_dir, monkeypatch):
        """CA2: the clean-machine case must not change."""
        _write_named_project(project_dir)
        _stub_launch(monkeypatch)

        report = core_unpack.unpack(project_dir, restore=False)

        assert (project_dir / "docker-compose.yaml").read_text() == COMPOSE_TEMPLATE
        assert "db_host = db-demo\n" in (project_dir / "config" / "odoo.conf").read_text()
        assert report["project_renamed"] is False
        assert report["renamed_from"] is None
        assert report["project_name"] == "demo"
        assert report["odoo_container"] == "odoo-demo"

    def test_a_name_renames_compose_and_conf_before_anything_starts(self, project_dir, monkeypatch):
        _write_named_project(project_dir)
        _stub_launch(monkeypatch)
        seen = {}

        def launch(root, build, report):
            seen["compose"] = (root / "docker-compose.yaml").read_text()
            seen["conf"] = (root / "config" / "odoo.conf").read_text()
            return True

        monkeypatch.setattr(core_unpack, "_launch_environment", launch)

        report = core_unpack.unpack(project_dir, project_name="demo-2", restore=False)

        expected = (project_dir / "docker-compose.yaml").read_text()
        assert seen["compose"] == expected
        assert "name: demo-2\n" in expected
        assert "container_name: db-demo-2\n" in expected
        assert "db_host = db-demo-2\n" in seen["conf"]
        assert report["project_name"] == "demo-2"
        assert report["project_renamed"] is True
        assert report["renamed_from"] == "demo"
        assert report["odoo_container"] == "odoo-demo-2"

    def test_restore_reads_the_renamed_db_container(self, project_dir, monkeypatch):
        """Mutation: renaming after the restore leaves it aimed at the other copy's database."""
        _write_named_project(project_dir)
        _stub_launch(monkeypatch)
        dump = project_dir / "db_demo_20240101_010101.dump"
        dump.write_bytes(b"x")
        monkeypatch.setattr(core_unpack, "_find_backup_files", lambda root: (dump, None))
        monkeypatch.setattr(core_unpack, "_launch_db_only", lambda root, report: True)
        monkeypatch.setattr(core_unpack, "_wait_for_postgres", lambda c, r: True)
        monkeypatch.setattr(core_unpack, "_clear_generated_assets", lambda *a: None)
        restored_into = []
        monkeypatch.setattr(core_unpack, "_restore_database", lambda c, d, r: restored_into.append(c) or "demo")

        core_unpack.unpack(project_dir, project_name="demo-2")

        assert restored_into == ["db-demo-2"]

    def test_the_same_name_rewrites_nothing(self, project_dir, monkeypatch):
        _write_named_project(project_dir)
        _stub_launch(monkeypatch)

        report = core_unpack.unpack(project_dir, project_name="demo", restore=False)

        assert (project_dir / "docker-compose.yaml").read_text() == COMPOSE_TEMPLATE
        assert report["project_renamed"] is False

    @pytest.mark.parametrize("bad", ["Demo 2", "-demo", "demo/2", ""])
    def test_an_invalid_name_raises_before_writing(self, project_dir, monkeypatch, bad):
        """Mutation: validating after the ports/compose were already rewritten."""
        _write_named_project(project_dir)
        _stub_launch(monkeypatch)

        with pytest.raises(core_unpack.UnpackError) as exc:
            core_unpack.unpack(project_dir, project_name=bad, ports={"odoo_port": 9999, "vsc_port": 9998}, restore=False)

        assert exc.value.hint
        assert (project_dir / "docker-compose.yaml").read_text() == COMPOSE_TEMPLATE

    def test_a_compose_without_name_is_not_touched(self, project_dir, monkeypatch):
        """Mutation: renaming container_name alone is the destructive path."""
        _write_project(project_dir)
        before = (project_dir / "docker-compose.yaml").read_text()
        _stub_launch(monkeypatch)

        report = core_unpack.unpack(project_dir, project_name="demo-2", restore=False)

        assert (project_dir / "docker-compose.yaml").read_text() == before
        assert report["project_renamed"] is False
        assert any("name:" in w for w in report["warnings"])

    def test_an_unreadable_db_container_warns_about_db_host(self, project_dir, monkeypatch):
        _write_named_project(project_dir)
        _stub_launch(monkeypatch)
        monkeypatch.setattr(core_unpack, "_get_db_container_name", lambda root: None)

        report = core_unpack.unpack(project_dir, project_name="demo-2", restore=False)

        assert report["project_renamed"] is True
        assert "db_host = db-demo\n" in (project_dir / "config" / "odoo.conf").read_text()
        assert any("db_host" in w for w in report["warnings"])

    def test_override_and_mailpit_leftovers_are_warned(self, project_dir, monkeypatch):
        _write_named_project(project_dir)
        (project_dir / "docker-compose.override.yml").write_text("services: {}\n")
        monkeypatch.setattr(core_unpack, "mailpit_is_enabled", lambda root: True)
        _stub_launch(monkeypatch)

        report = core_unpack.unpack(project_dir, project_name="demo-2", restore=False)

        assert any("override" in w for w in report["warnings"])
        assert any("Mailpit" in w for w in report["warnings"])

    def test_no_leftover_warnings_when_there_are_no_leftovers(self, project_dir, monkeypatch):
        """Mutation: warning about an override that is not there."""
        _write_named_project(project_dir)
        _stub_launch(monkeypatch)

        report = core_unpack.unpack(project_dir, project_name="demo-2", restore=False)

        assert report["warnings"] == []

    def test_no_warnings_without_a_rename(self, project_dir, monkeypatch):
        _write_named_project(project_dir)
        (project_dir / "docker-compose.override.yml").write_text("services: {}\n")
        _stub_launch(monkeypatch)

        assert core_unpack.unpack(project_dir, restore=False)["warnings"] == []


class TestUnpackCliProjectName:
    @pytest.fixture
    def cli(self, project_dir, monkeypatch):
        from click.testing import CliRunner

        from rocketdoo import unpack_environment
        from rocketdoo.cli import main

        _write_named_project(project_dir)
        calls = {}

        def fake_unpack(root, **kwargs):
            calls.update(kwargs)
            return {
                "ports": {"odoo_port": 8069, "vsc_port": 8888},
                "started": True,
                "launched": True,
                "odoo_container": "odoo-demo",
                "db_backup_present": False,
                "db_restored": False,
                "logs_tail": [],
                "warnings": [],
                "project_name": kwargs["project_name"] or "demo",
                "project_renamed": bool(kwargs["project_name"]),
                "renamed_from": "demo" if kwargs["project_name"] else None,
            }

        def set_info(project, mailpit=False):
            info = {
                "meta": {"rkd_shared": True},
                "has_meta": True,
                "port_conflicts": {"odoo_port": False, "vsc_port": False},
                "suggested_ports": {"odoo_port": 8069, "vsc_port": 8888},
                "uses_private_repos": False,
                "ssh_keys": [],
                "project": project,
                "mailpit_ports": [1125, 8125],
                "mailpit_port_conflict": mailpit,
            }
            monkeypatch.setattr(unpack_environment, "inspect", lambda root: info)

        monkeypatch.setattr(unpack_environment, "unpack", fake_unpack)
        monkeypatch.setattr(unpack_environment.questionary, "confirm", lambda *a, **k: _Answer(True))

        def run(*args):
            return CliRunner().invoke(main, ["unpack", "--no-ssh", *args])

        return run, set_info, calls, unpack_environment

    CONFLICT = {"name": "demo", "suggested_name": "demo-2", "conflict": True, "reason": "taken", "renamable": True}
    FREE = {"name": "demo", "suggested_name": "demo", "conflict": False, "reason": "", "renamable": True}
    UNKNOWN = {"name": "demo", "suggested_name": "demo", "conflict": None, "reason": "no docker", "renamable": True}

    def test_yes_passes_the_suggestion_and_says_so(self, cli):
        run, set_info, calls, _ = cli
        set_info(self.CONFLICT)

        result = run("--yes")

        assert calls["project_name"] == "demo-2"
        assert "demo-2" in result.output
        assert "renamed" in result.output

    def test_no_conflict_passes_none(self, cli):
        run, set_info, calls, _ = cli
        set_info(self.FREE)

        run("--yes")

        assert calls["project_name"] is None

    def test_unknown_passes_none_and_warns(self, cli):
        run, set_info, calls, _ = cli
        set_info(self.UNKNOWN)

        result = run("--yes")

        assert calls["project_name"] is None
        assert "not verified" in result.output

    def test_declining_the_rename_passes_none_and_says_what_that_costs(self, cli, monkeypatch):
        """The warning has to name the real consequence.

        Sharing `name:` is not a conflict for Compose, it is adoption: `up`
        takes the other copy's containers as this project's, recreates them and
        mounts its volumes. An earlier wording said `up` "will probably fail",
        which reads as a harmless retry and is the opposite of what happens.
        """
        run, set_info, calls, unpack_environment = cli
        set_info(self.CONFLICT)
        monkeypatch.setattr(unpack_environment.questionary, "confirm", lambda *a, **k: _Answer(False))

        result = run()

        assert calls["project_name"] is None
        output = " ".join(result.output.split())
        assert "recreate the other copy's containers" in output
        assert "overwrite its database" in output

    def test_yes_does_not_ask(self, cli, monkeypatch):
        """Mutation: dropping the `auto_accept` branch left `--yes` asking.

        The other tests patch `questionary.confirm` to answer yes, so they
        cannot tell "accepted because of --yes" from "asked and was told yes".
        Here the prompt itself is the failure: without a terminal questionary
        raises, and `rkd unpack --yes` runs in scripts.
        """
        run, set_info, calls, unpack_environment = cli
        set_info(self.CONFLICT)

        def must_not_ask(*_a, **_k):
            raise AssertionError("--yes must not reach questionary")

        monkeypatch.setattr(unpack_environment.questionary, "confirm", must_not_ask)

        result = run("--yes")

        assert result.exit_code == 0, result.output
        assert calls["project_name"] == "demo-2"

    def test_accepting_the_rename_passes_the_suggestion(self, cli):
        run, set_info, calls, _ = cli
        set_info(self.CONFLICT)

        run()

        assert calls["project_name"] == "demo-2"

    def test_without_a_terminal_it_declines_cleanly(self, cli, monkeypatch):
        """EOFError from questionary must not become a traceback in a script."""
        run, set_info, calls, unpack_environment = cli
        set_info(self.CONFLICT)

        class _NoTerminal:
            def ask(self):
                raise EOFError

        monkeypatch.setattr(unpack_environment.questionary, "confirm", lambda *a, **k: _NoTerminal())

        result = run()

        assert result.exception is None, result.output
        assert calls["project_name"] is None
        assert "--yes" in result.output

    def test_mailpit_port_conflict_suggests_mail_off(self, cli):
        run, set_info, _, _ = cli
        set_info(self.FREE, mailpit=True)

        output = " ".join(run("--yes").output.split())
        assert "rkd mail off" in output
        assert "(1125/8125)" in output
        assert "1025/8025" not in output

        set_info(self.FREE, mailpit=False)
        assert "rkd mail off" not in run("--yes").output


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


class TestTheRenameIsAllOrNothing:
    """`_rename_project_in_compose` refuses a compose it cannot rename whole.

    The guard used to live only in `unpack()`. Reaching the helper directly
    with a compose whose `name:` says something else -- which a quoted
    `name: "demo"` used to produce from inside this very module -- rewrote the
    `container_name` lines and left `name:` alone: the exact shape that makes
    Compose adopt and recreate the other copy's containers.
    """

    COMPOSE = "name: otro\nservices:\n  db:\n    container_name: db-demo\n"

    def test_a_compose_naming_another_project_is_not_touched(self, project_dir):
        compose = project_dir / "docker-compose.yaml"
        compose.write_text(self.COMPOSE)

        assert core_unpack._rename_project_in_compose(project_dir, "demo", "demo-2") is False
        assert compose.read_text() == self.COMPOSE

    def test_a_quoted_name_is_read_without_its_quotes(self, project_dir):
        """`name: "demo"` is the project `demo`, not `"demo"`."""
        compose = project_dir / "docker-compose.yaml"
        compose.write_text('name: "demo"\nservices:\n  db:\n    container_name: db-demo\n')

        assert core_unpack._compose_project_name(project_dir) == "demo"

    def test_a_crlf_compose_still_reports_its_project_name(self, project_dir):
        """`[ \\t]*$` does not match the `\\r` CRLF leaves before the newline.

        The name came back as None, so a compose saved on Windows looked like
        one with no `name:` at all and the conflict check quietly skipped it --
        on a tool whose users are largely on WSL.
        """
        compose = project_dir / "docker-compose.yaml"
        compose.write_bytes(b"name: demo\r\nservices:\r\n  db:\r\n    container_name: db-demo\r\n")

        assert core_unpack._compose_project_name(project_dir) == "demo"


class TestPortsOfAStoppedOriginal:
    """A project name that is taken takes its ports with it (#238).

    `is_port_in_use()` only sees live listeners. An original stopped with
    `rkd down` (no -v) leaves 8069 free, so the copy used to keep it -- and
    when the original came back its web container could not start:
    `Bind for 0.0.0.0:8069 failed: port is already allocated`, left in
    `Created` while its db came up fine.

    It is the same blind spot #205 closed for the Compose identity, where the
    answer was to read the volume labels, which survive a `down`. Here what
    survives is the project name, so a name already taken moves the ports
    whether or not anything is listening.
    """

    def test_a_taken_name_moves_the_ports_with_nothing_listening(self, monkeypatch):
        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: False)
        monkeypatch.setattr(core_unpack, "find_available_port", lambda start: start)

        odoo_port, vsc_port, changed = _check_ports({"odoo_port": 8069, "vsc_port": 8888}, name_taken=True)

        assert (odoo_port, vsc_port) == (8070, 8889)
        assert changed is True

    def test_a_free_name_leaves_free_ports_alone(self, monkeypatch):
        """Today's behaviour, unchanged: no conflict, no remap."""
        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: False)

        assert _check_ports({"odoo_port": 8069, "vsc_port": 8888}, name_taken=False) == (8069, 8888, False)

    def test_inspect_does_not_move_the_ports_when_docker_cannot_answer(self, project_dir, monkeypatch):
        """`conflict is None` means unknown, and unknown is not a conflict.

        Mutation: reading it as truthy would remap the ports of every unpack
        run on a host without a reachable Docker.
        """
        _write_named_project(project_dir)
        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: False)
        monkeypatch.setattr(core_unpack, "_docker_inventory", lambda: {"ok": False, "projects": {}, "volumes": {}})

        info = core_unpack.inspect(project_dir)

        assert info["project"]["conflict"] is None
        assert info["ports_reserved"] is False
        assert info["port_conflicts"] == {"odoo_port": False, "vsc_port": False}

    def test_inspect_reports_why_the_ports_moved(self, project_dir, monkeypatch):
        """`ports_reserved` is what lets the CLI stop calling a port that
        anyone can see is free "already in use".
        """
        _write_named_project(project_dir)
        monkeypatch.setattr(core_unpack, "is_port_in_use", lambda port: False)
        monkeypatch.setattr(core_unpack, "find_available_port", lambda start: start)
        monkeypatch.setattr(
            core_unpack,
            "_docker_inventory",
            lambda: {"ok": True, "projects": {}, "volumes": {"demo_demo-db-data": "demo"}},
        )

        info = core_unpack.inspect(project_dir)

        assert info["project"]["conflict"] is True
        assert info["ports_reserved"] is True
        assert info["suggested_ports"] == {"odoo_port": 8070, "vsc_port": 8889}


class TestTheFinalPanelDoesNotClaimARestoreThatDidNotHappen:
    """`started` only says the containers came up.

    A dump that never made it in is the user's whole reason for unpacking,
    and the green "Environment is ready" panel used to appear anyway, with
    the warning already scrolled past above it. Same shape as #245 and as
    the Traefik case of #143: a panel affirming a success that did not
    happen.

    The distinction needs `db_backup_present`: without it there is no way to
    tell "this ZIP carried no database" from "the restore did not run".
    """

    def _run(self, project_dir, monkeypatch, **report_overrides):
        from click.testing import CliRunner

        from rocketdoo import unpack_environment
        from rocketdoo.cli import main

        _write_named_project(project_dir)
        report = {
            "ports": {"odoo_port": 8069, "vsc_port": 8888},
            "ports_changed": False,
            "project_name": "demo",
            "project_renamed": False,
            "renamed_from": None,
            "ssh_configured": False,
            "db_backup_present": True,
            "db_restored": True,
            "filestore_restored": True,
            "started": True,
            "launched": True,
            "odoo_container": "odoo-demo",
            "warnings": [],
            "logs_tail": [],
        }
        report.update(report_overrides)
        monkeypatch.setattr(unpack_environment, "unpack", lambda root, **kwargs: report)
        monkeypatch.setattr(
            unpack_environment,
            "inspect",
            lambda root: {
                "meta": {"rkd_shared": True},
                "has_meta": True,
                "port_conflicts": {"odoo_port": False, "vsc_port": False},
                "suggested_ports": {"odoo_port": 8069, "vsc_port": 8888},
                "ports_reserved": False,
                "uses_private_repos": False,
                "ssh_keys": [],
                "project": {"name": "demo", "suggested_name": "demo", "conflict": False, "reason": "", "renamable": True},
                "mailpit_port_conflict": False,
            },
        )
        return CliRunner().invoke(main, ["unpack", "--yes", "--no-ssh"])

    def test_a_dump_that_did_not_restore_is_called_out(self, project_dir, monkeypatch):
        result = self._run(project_dir, monkeypatch, db_restored=False)

        output = " ".join(result.output.split())
        assert "database was not restored" in output
        assert "rkd unpack --yes" in output, "the warning has to say how to retry"

    def test_a_zip_without_a_database_says_nothing_about_restoring(self, project_dir, monkeypatch):
        """`--no-db` packs are normal; they must not raise a false alarm."""
        result = self._run(project_dir, monkeypatch, db_backup_present=False, db_restored=False)

        assert "database was not restored" not in " ".join(result.output.split())

    def test_a_successful_restore_keeps_the_green_panel_alone(self, project_dir, monkeypatch):
        result = self._run(project_dir, monkeypatch)

        output = " ".join(result.output.split())
        assert "Environment is ready" in output
        assert "database was not restored" not in output
