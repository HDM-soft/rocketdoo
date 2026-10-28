"""Unit tests for core/port_validation.py.

Port handling is where `rkd init` decides whether a project can start, so the
parsing of docker-compose port entries and the "is this port really taken?"
distinction are worth pinning down precisely.
"""

import pytest

from rocketdoo.core import port_validation as pv


class TestHostPort:
    """_host_port must recognise every shape docker-compose allows."""

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ("8069:8069", 8069),
            ("8069:8069/tcp", 8069),
            ("127.0.0.1:8069:8069", 8069),
            ("0.0.0.0:5432:5432/tcp", 5432),
            (8069, 8069),
            ({"published": 8069, "target": 8069}, 8069),
            ({"published": "8069", "target": 8069}, 8069),
        ],
    )
    def test_reserves_a_host_port(self, entry, expected):
        assert pv._host_port(entry) == expected

    @pytest.mark.parametrize(
        "entry",
        [
            "8069",  # container-only: Docker picks a random host port
            "${ODOO_PORT}:8069",  # unresolved variable
            "not-a-port",
            {"target": 8069},  # long form without `published`
            {"published": None},
            None,
            True,  # bool is an int subclass: must not read as port 1
            [],
        ],
    )
    def test_does_not_reserve_a_host_port(self, entry):
        assert pv._host_port(entry) is None


class TestCollectDeclaredPorts:
    """Ports declared by *other* projects, read from sibling compose files."""

    @staticmethod
    def _project(root, name, ports, filename="docker-compose.yaml"):
        proj = root / name
        proj.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f'      - "{p}"' for p in ports)
        (proj / filename).write_text("services:\n  web:\n    image: odoo:16.0\n    ports:\n" + body + "\n")
        return proj

    @pytest.fixture
    def workspace(self, tmp_path, monkeypatch):
        """A current project with two siblings, with CWD moved into it."""
        root = tmp_path / "workspace"
        current = self._project(root, "current", ["8069:8069"])
        self._project(root, "other-a", ["8070:8069", "5433:5432"])
        self._project(root, "other-b", ["8071:8069"], filename="compose.yml")
        monkeypatch.chdir(current)
        # Keep the scan off the real ~/rocketdoo directory
        monkeypatch.setattr(pv.Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
        return current

    def test_finds_ports_of_sibling_projects(self, workspace):
        declared = pv.collect_declared_ports()
        assert declared[8070] == "other-a"
        assert declared[5433] == "other-a"
        assert declared[8071] == "other-b"

    def test_excludes_the_current_project(self, workspace):
        """Re-running `rkd init` must not flag the project's own ports."""
        assert 8069 not in pv.collect_declared_ports()

    def test_invalid_yaml_is_skipped_not_fatal(self, workspace, tmp_path):
        broken = tmp_path / "workspace" / "broken"
        broken.mkdir()
        (broken / "docker-compose.yaml").write_text("services: [unclosed\n")
        assert pv.collect_declared_ports()[8070] == "other-a"

    def test_base_anchors_the_scan_to_the_project_not_the_cwd(self, workspace, tmp_path, monkeypatch):
        elsewhere = tmp_path / "far" / "a" / "b" / "c" / "d" / "e"
        elsewhere.mkdir(parents=True)
        monkeypatch.chdir(elsewhere)
        assert 8070 not in pv.collect_declared_ports(exclude_dir=workspace)
        declared = pv.collect_declared_ports(exclude_dir=workspace, base=workspace)
        assert declared[8070] == "other-a"
        assert 8069 not in declared

    def test_reservation_message_names_the_other_project(self, workspace):
        msg = pv.get_port_reservation(8070)
        assert msg and "other-a" in msg and "8070" in msg

    def test_no_reservation_for_a_free_port(self, workspace):
        assert pv.get_port_reservation(9999) is None


class TestGetPortPublisher:
    """Which running container publishes a port, parsed from `docker ps`."""

    @staticmethod
    def _fake_ps(output):
        def _run(cmd, **kwargs):
            return output

        return _run

    def test_matches_the_publishing_container(self, monkeypatch):
        monkeypatch.setattr(
            pv.subprocess,
            "check_output",
            self._fake_ps("odoo-web\t0.0.0.0:8069->8069/tcp\ndb\t5432/tcp\n"),
        )
        assert pv.get_port_publisher(8069) == "odoo-web"

    def test_does_not_match_a_longer_port_number(self, monkeypatch):
        """8069 must not match 18069 — the digit guard in the regex."""
        monkeypatch.setattr(
            pv.subprocess,
            "check_output",
            self._fake_ps("other\t0.0.0.0:18069->8069/tcp\n"),
        )
        assert pv.get_port_publisher(8069) is None

    def test_returns_none_without_docker(self, monkeypatch):
        def _boom(*a, **kw):
            raise FileNotFoundError("docker")

        monkeypatch.setattr(pv.subprocess, "check_output", _boom)
        assert pv.get_port_publisher(8069) is None
        assert pv.is_port_used_by_rocketdoo(8069) is False


class TestFindAvailablePort:
    def test_skips_ports_declared_by_other_projects(self, monkeypatch):
        monkeypatch.setattr(pv, "is_port_in_use", lambda p: False)
        assert pv.find_available_port(8069, 8100, declared={8069: "a", 8070: "b"}) == 8071

    def test_skips_ports_actually_in_use(self, monkeypatch):
        monkeypatch.setattr(pv, "is_port_in_use", lambda p: p in (8069, 8070))
        assert pv.find_available_port(8069, 8100, declared={}) == 8071

    def test_never_returns_a_privileged_port(self, monkeypatch):
        monkeypatch.setattr(pv, "is_port_in_use", lambda p: False)
        assert pv.find_available_port(80, 2000, declared={}) >= 1024

    def test_raises_when_the_range_is_exhausted(self, monkeypatch):
        monkeypatch.setattr(pv, "is_port_in_use", lambda p: True)
        with pytest.raises(RuntimeError, match="No available ports"):
            pv.find_available_port(8069, 8072, declared={})


def _boom(*_args, **_kwargs):
    raise OSError("netstat no esta")


class TestThePortCheckStaysSilent:
    """`core/mailpit.py` reaches this function, and a module of core/ printing
    is what the contract of #143 forbids. The guard is per module, so a
    `print()` one call away from a pure module passed it (#249).
    """

    def test_a_failing_os_check_prints_nothing(self, monkeypatch, capsys):
        monkeypatch.setattr(pv, "_check_port_unix", _boom)
        monkeypatch.setattr(pv, "_check_port_windows", _boom)

        # A port nothing listens on, so the socket probe passes it through to
        # the OS check that is about to fail.
        assert pv.is_port_in_use(59117) is True

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestTheProbeNeverWaitsForever:
    """A connect() to a filtered port is not refused, it is dropped, and the
    socket then waits out the system timeout -- 145 seconds measured on this
    machine, with the whole unpack suite behind it. A firewall that DROPs is
    the normal setup on a VPS, so without a timeout `rkd init` hangs for
    minutes without saying why (#249).
    """

    class _Socket:
        """Stands in for a socket whose connect never completes."""

        def __init__(self, recorder, result=111):
            self.recorder = recorder
            self.result = result

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def settimeout(self, value):
            self.recorder.append(value)

        def connect_ex(self, _address):
            if not self.recorder:
                raise AssertionError("connect_ex was called before any timeout was set")
            return self.result

    def test_a_dropped_connection_still_asks_the_operating_system(self, monkeypatch):
        """A filtered port answers the probe with EAGAIN, not with a refusal,
        and that is not an answer about the port: the OS check decides."""
        recorded = []
        consulted = []
        monkeypatch.setattr(pv.socket, "socket", lambda *_a, **_k: self._Socket(recorded, result=11))
        monkeypatch.setattr(pv, "_check_port_unix", lambda port: consulted.append(port) or True)

        assert pv.is_port_in_use(8069) is True
        assert consulted == [8069]

    def test_the_probe_sets_a_timeout_before_connecting(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(pv.socket, "socket", lambda *_a, **_k: self._Socket(recorded))
        monkeypatch.setattr(pv, "_check_port_unix", lambda _port: False)
        monkeypatch.setattr(pv, "_check_port_windows", lambda _port: False)

        assert pv.is_port_in_use(8069) is False

        assert recorded, "no timeout was set"
        assert 0 < recorded[0] <= 1, f"a {recorded[0]}s wait is not a probe"
