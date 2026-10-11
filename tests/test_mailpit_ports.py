"""Host-port choice for the Mailpit block (#234).

Only the published (host) side of `"H:1025"` / `"H:8025"` may change: the
container side is what Odoo and the ir.mail_server talk to.
"""

import pytest

import rocketdoo.core.mailpit as mailpit
from rocketdoo.core.mailpit import (
    MailpitError,
    _block_ports,
    _choose_ports,
    _toggle_compose,
    disable,
    enable,
    published_ports,
    status,
)

BLOCK = """  # rkd:mailpit
  #mailpit:
  #  image: axllent/mailpit:latest
  #  container_name: demo-mailpit
  #  restart: unless-stopped
  #  ports:
  #    - "{smtp}:1025"
  #    - "{web}:8025"
  #  environment:
  #    MP_MAX_MESSAGES: 5000
  # /rkd:mailpit
"""

COMPOSE = """services:
  web:
    image: odoo:18.0
    ports:
      - "8069:8069"

""" + BLOCK.format(smtp=1025, web=8025)


def _block(smtp=1025, web=8025):
    return BLOCK.format(smtp=smtp, web=web)


def _project(tmp_path, compose=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "docker-compose.yaml").write_bytes((compose or COMPOSE).encode())
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "odoo.conf").write_text("[options]\n; smtp_server = localhost\n; smtp_port = 25\n")
    return tmp_path


@pytest.fixture
def host(monkeypatch):
    """No busy ports, no siblings, no docker. Tests override `busy` / `declared`."""
    state = {"busy": set(), "declared": {}}
    monkeypatch.setattr(mailpit, "run_compose", lambda *a, **k: 0)
    monkeypatch.setattr(mailpit, "container_running", lambda *a, **k: False)
    monkeypatch.setattr(mailpit, "databases_result", lambda *a, **k: (["dev"], ""))
    monkeypatch.setattr(mailpit, "enable_mailpit_server", lambda *a, **k: "")
    monkeypatch.setattr(mailpit, "disable_mailpit_server", lambda *a, **k: (1, ""))
    monkeypatch.setattr(mailpit, "is_port_in_use", lambda port: port in state["busy"])
    monkeypatch.setattr(mailpit, "collect_declared_ports", lambda **k: dict(state["declared"]))

    def find(start, declared):
        port = start
        while port in declared or port in state["busy"]:
            port += 1
        return port

    monkeypatch.setattr(mailpit, "find_available_port", find)
    return state


class TestReadingTheBlock:
    def test_reads_the_ports_of_a_commented_block(self):
        assert _block_ports(COMPOSE) == {1025: 1025, 8025: 8025}

    def test_reads_the_ports_of_an_enabled_block(self):
        enabled = _toggle_compose(COMPOSE, enable=True)
        assert _block_ports(enabled) == {1025: 1025, 8025: 8025}

    def test_a_block_without_ports_lines_reads_empty(self, tmp_path):
        root = _project(tmp_path, "# rkd:mailpit\n  mailpit:\n    image: x\n# /rkd:mailpit\n")
        assert _block_ports((root / "docker-compose.yaml").read_text()) == {}
        assert published_ports(root) == {"smtp_port": None, "web_port": None}

    def test_published_ports_without_compose(self, tmp_path):
        assert published_ports(tmp_path) == {"smtp_port": None, "web_port": None}

    def test_ports_outside_the_block_are_ignored(self):
        assert 8069 not in _block_ports(COMPOSE)
        assert 8069 not in _block_ports(COMPOSE.replace("8069:8069", "1025:1025"))

    @pytest.mark.parametrize("line", ['  #    - "${MAILPIT_PORT}:1025"', "  #    - 1025", '  #    - "1025"'])
    def test_an_unrecognized_form_is_ignored(self, line):
        content = f"# rkd:mailpit\n{line}\n# /rkd:mailpit\n"
        assert _block_ports(content) == {}
        assert _toggle_compose(content, enable=True, ports={1025: 1125}) == content.replace("#    -", "    -")

    def test_a_hand_edited_port_is_read(self):
        assert _block_ports(COMPOSE.replace('"1025:1025"', '"2025:1025"')) == {1025: 2025, 8025: 8025}

    def test_a_foreign_mapping_inside_the_block_is_not_touched(self):
        content = COMPOSE.replace('"8025:8025"', '"9000:9000"')
        assert _block_ports(content) == {1025: 1025}
        assert '"9000:9000"' in _toggle_compose(content, enable=True, ports={1025: 1125, 8025: 8125})

    def test_a_port_missing_from_the_choice_is_left_as_written(self):
        result = _toggle_compose(COMPOSE, enable=True, ports={1025: 1125})
        assert '"1125:1025"' in result and '"8025:8025"' in result


class TestChoosingThePorts:
    def test_free_ports_are_kept(self, host, tmp_path):
        assert _choose_ports({1025: 1025, 8025: 8025}, tmp_path) == ({1025: 1025, 8025: 8025}, False)

    def test_a_busy_port_moves(self, host, tmp_path):
        host["busy"] = {1025, 8025}
        chosen, moved = _choose_ports({1025: 1025, 8025: 8025}, tmp_path)
        assert moved is True
        assert chosen == {1025: 1026, 8025: 8026}

    def test_only_the_busy_one_moves(self, host, tmp_path):
        host["busy"] = {8025}
        chosen, moved = _choose_ports({1025: 1025, 8025: 8025}, tmp_path)
        assert chosen == {1025: 1025, 8025: 8026}
        assert moved is True

    def test_a_port_declared_by_a_stopped_sibling_is_avoided(self, host, tmp_path):
        host["declared"] = {1025: "other"}
        chosen, moved = _choose_ports({1025: 1025, 8025: 8025}, tmp_path)
        assert chosen[1025] != 1025
        assert chosen[8025] == 8025
        assert moved is True

    def test_the_two_mappings_never_get_the_same_port(self, host, tmp_path):
        host["busy"] = {1025}
        chosen, _ = _choose_ports({1025: 1025, 8025: 1026}, tmp_path)
        assert chosen[1025] != chosen[8025]

    def test_an_exhausted_range_raises_mailpit_error(self, host, monkeypatch, tmp_path):
        host["busy"] = {1025}

        def exhausted(*a, **k):
            raise RuntimeError("No available ports found")

        monkeypatch.setattr(mailpit, "find_available_port", exhausted)
        with pytest.raises(MailpitError) as info:
            _choose_ports({1025: 1025}, tmp_path)
        assert info.value.hint

    def test_the_scan_is_anchored_to_the_project_root(self, host, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(mailpit, "collect_declared_ports", lambda **k: seen.update(k) or {})
        _choose_ports({1025: 1025}, tmp_path)
        assert seen == {"exclude_dir": tmp_path, "base": tmp_path}

    def test_the_rewrite_keeps_the_container_side_and_the_markers(self):
        result = _toggle_compose(COMPOSE, enable=True, ports={1025: 1125, 8025: 8125})
        assert '    - "1125:1025"' in result
        assert '    - "8125:8025"' in result
        assert "  # rkd:mailpit\n" in result
        assert "  # /rkd:mailpit\n" in result
        assert mailpit._is_enabled(result)
        assert '- "8069:8069"' in result


class TestEnableAndDisable:
    def test_enable_keeps_the_defaults_on_a_free_host(self, host, tmp_path):
        root = _project(tmp_path)
        report = enable(root)
        content = (root / "docker-compose.yaml").read_text()
        assert '"1025:1025"' in content and '"8025:8025"' in content
        assert report["ports_moved"] is False
        assert (report["smtp_port"], report["web_port"]) == (1025, 8025)

    def test_two_projects_get_different_ports(self, host, tmp_path):
        first = enable(_project(tmp_path / "a"))
        host["busy"] = {first["smtp_port"], first["web_port"]}
        second = enable(_project(tmp_path / "b"))
        assert second["smtp_port"] != first["smtp_port"]
        assert second["web_port"] != first["web_port"]
        assert second["ports_moved"] is True
        assert f'"{second["web_port"]}:8025"' in (tmp_path / "b" / "docker-compose.yaml").read_text()

    def test_crlf_survives_enable_and_disable(self, host, tmp_path):
        root = _project(tmp_path, COMPOSE.replace("\n", "\r\n"))
        compose = root / "docker-compose.yaml"
        crlf_count = compose.read_bytes().count(b"\r\n")
        for action in (enable, disable):
            action(root)
            data = compose.read_bytes()
            assert b"\n" not in data.replace(b"\r\n", b"")
            assert data.count(b"\r\n") == crlf_count

    def test_crlf_with_a_moved_port(self, host, tmp_path):
        host["busy"] = {1025}
        root = _project(tmp_path, COMPOSE.replace("\n", "\r\n"))
        enable(root)
        data = (root / "docker-compose.yaml").read_bytes()
        assert b'    - "1026:1025"\r\n' in data

    def test_odoo_conf_keeps_the_internal_port(self, host, tmp_path):
        host["busy"] = {1025, 8025}
        root = _project(tmp_path)
        enable(root)
        conf = (root / "config" / "odoo.conf").read_text()
        assert "smtp_port = 1025" in conf
        assert "1026" not in conf

    def test_enable_on_an_already_enabled_block_does_not_touch_the_file_and_reports_the_live_ports(self, host, tmp_path):
        root = _project(
            tmp_path,
            _toggle_compose(COMPOSE.replace('"1025:1025"', '"1125:1025"'), True).replace('"8025:8025"', '"8125:8025"'),
        )
        host["busy"] = {1125, 8125}
        before = (root / "docker-compose.yaml").read_bytes()
        report = enable(root)
        assert report["changed"] is False
        assert (report["smtp_port"], report["web_port"]) == (1125, 8125)
        assert report["ports_moved"] is False
        assert (root / "docker-compose.yaml").read_bytes() == before

    def test_disable_keeps_the_chosen_port(self, host, tmp_path):
        host["busy"] = {1025}
        root = _project(tmp_path)
        assert enable(root)["smtp_port"] == 1026
        disable(root)
        assert '      #- "1026:1025"' in (root / "docker-compose.yaml").read_text()
        assert published_ports(root)["smtp_port"] == 1026

    def test_a_block_without_ports_lines_enables_and_disables_cleanly(self, host, tmp_path):
        root = _project(tmp_path, "# rkd:mailpit\n  mailpit:\n    image: x\n# /rkd:mailpit\n")
        report = enable(root)
        assert (report["smtp_port"], report["web_port"], report["ports_moved"]) == (None, None, False)
        disable(root)
        assert not mailpit._is_enabled((root / "docker-compose.yaml").read_text())

    def test_an_exhausted_range_leaves_the_compose_untouched(self, host, monkeypatch, tmp_path):
        host["busy"] = {1025}

        def exhausted(*a, **k):
            raise RuntimeError("No available ports found")

        monkeypatch.setattr(mailpit, "find_available_port", exhausted)
        root = _project(tmp_path)
        before = (root / "docker-compose.yaml").read_bytes()
        with pytest.raises(MailpitError):
            enable(root)
        assert (root / "docker-compose.yaml").read_bytes() == before

    def test_status_carries_the_published_ports(self, host, tmp_path):
        root = _project(tmp_path)
        assert status(root) == {"enabled": False, "running": False, "smtp_port": 1025, "web_port": 8025}


class TestPortsTheUserEditedByHand:
    """The compose is a file people edit, so what comes out of it is input.

    `- "70000:1025"` reached `is_port_in_use()`, whose `connect_ex` raises
    `OverflowError` outside its try, and `rkd mail on` died with a bare
    traceback -- on a compose that worked before this feature existed. From
    the GUI it surfaced as `{"ok": false, "error": "connect_ex(): ..."}`.
    """

    @pytest.mark.parametrize("host", [70000, 99999, 0])
    def test_a_host_side_outside_the_range_reads_as_absent(self, host):
        assert _block_ports(_block(smtp=host)) == {8025: 8025}

    def test_choosing_falls_back_to_the_default_instead_of_raising(self, tmp_path, monkeypatch):
        (tmp_path / "docker-compose.yaml").write_text(_block(smtp=70000))
        monkeypatch.setattr(mailpit, "is_port_in_use", lambda port: False)
        monkeypatch.setattr(mailpit, "collect_declared_ports", lambda **_k: {})

        chosen, _moved = _choose_ports(_block_ports(_block(smtp=70000)), tmp_path)

        assert chosen == {8025: 8025}


class TestMailpitNeverTakesThisProjectsOwnPort:
    """`exclude_dir` prunes this project from the sibling scan, so the ports it
    already publishes were invisible to the search.

    With Odoo on 8026 and 8025 busy, Mailpit picked 8026 -- the project's own
    web port -- reported success, and the next `rkd up` failed with
    `port is already allocated`. One busy port is the whole precondition.
    """

    def test_the_web_port_of_this_project_is_not_offered_to_mailpit(self, tmp_path, monkeypatch):
        (tmp_path / "docker-compose.yaml").write_text(
            'services:\n  web:\n    image: odoo:18.0\n    ports:\n      - "8026:8069"\n      - "8888:8888"\n\n' + _block()
        )
        monkeypatch.setattr(mailpit, "is_port_in_use", lambda port: port == 8025)
        monkeypatch.setattr(mailpit, "collect_declared_ports", lambda **_k: {})

        chosen, moved = _choose_ports(_block_ports(_block()), tmp_path)

        assert moved is True
        assert chosen[8025] not in (8026, 8888), "Mailpit took a port this project already publishes"
