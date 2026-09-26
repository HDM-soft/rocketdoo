"""End-to-end: a real `rkd pack` -> `rkd unpack` round trip against Docker.

#143 T12: rocketdoo/unpack_environment.py has 749 lines and no tests at all.
Before T13 moves its logic into core/unpack.py, this is the one test that
proves the whole chain still works against a real Odoo database -- not
mocked subprocess calls: a database is initialised for real, packed into a
ZIP, unpacked into a second directory, and its rows are counted afterwards.
CA13.

Marked docker + slow: it builds the Odoo image, runs `-i base`, and restores
a pg_dump, easily several minutes end to end.
"""

import shutil
import socket
import subprocess
import time
import zipfile

import pytest
import yaml
from click.testing import CliRunner

from rocketdoo.cli import main
from rocketdoo.init_project import init_from_profile
from rocketdoo.scaffold import scaffold_project

PROJECT_NAME = "rkdroundtrip"


def _compose(root, *args, timeout, check=True):
    result = subprocess.run(["docker", "compose", *args], cwd=root, capture_output=True, text=True, timeout=timeout)
    if check:
        assert result.returncode == 0, f"docker compose {' '.join(args)}\n{result.stderr[-2000:]}"
    return result


def _wait_for_postgres(root, max_wait=60):
    """`docker compose up -d` returns as soon as the container starts, not
    once Postgres accepts connections -- `odoo -i base` right after it is a
    flaky race, not a real assertion about the restore chain.
    """
    for _ in range(max_wait):
        result = _compose(root, "exec", "-T", "db", "pg_isready", "-U", "root", timeout=15, check=False)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise AssertionError(f"PostgreSQL not ready in {root} after {max_wait}s")


def _installed_module_count(root, database):
    """A real row count in ir_module_module, not just a 0 exit code."""
    result = _compose(
        root,
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "root",
        "-d",
        database,
        "-tAc",
        "SELECT count(*) FROM ir_module_module WHERE state = 'installed';",
        timeout=120,
    )
    return int(result.stdout.strip())


def _bind_port(port):
    """Occupies a host port with a plain listening socket.

    This is the trigger `_check_ports` reacts to. A second live copy of the
    same rkd project would occupy the port too, but it would also collide on
    `container_name` (fixed per project_name, see the report) before the
    port ever came into play -- a plain socket isolates the port-conflict
    path from that unrelated, pre-existing limitation.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


@pytest.mark.docker
@pytest.mark.slow
def test_pack_then_unpack_restores_the_database(tmp_path, docker_available, monkeypatch):
    if not docker_available:
        pytest.skip("Docker daemon not available")

    original = tmp_path / "original"
    original.mkdir()
    restore = tmp_path / "restore"

    monkeypatch.chdir(original)
    scaffold_project()
    init_from_profile("odoo18-ce", project_name=PROJECT_NAME)

    runner = CliRunner()
    zip_path = tmp_path / "roundtrip.zip"
    blockers = []

    try:
        _compose(original, "up", "-d", timeout=1800)
        _wait_for_postgres(original)
        _compose(
            original,
            "exec",
            "-T",
            "web",
            "odoo",
            "-d",
            PROJECT_NAME,
            "-i",
            "base",
            "--stop-after-init",
            "--without-demo=all",
            timeout=1800,
        )

        installed_before = _installed_module_count(original, PROJECT_NAME)
        assert installed_before > 0, "base did not install -- nothing here would prove a restore"

        monkeypatch.chdir(original)
        result = runner.invoke(main, ["pack", "--db-name", PROJECT_NAME, "--output", str(zip_path), "--yes"])
        assert result.exit_code == 0, result.output
        assert zip_path.exists()

        # Stopped and removed *before* the restore starts: `container_name`
        # is derived from project_name alone (init_project.py), identical in
        # every copy of a shared project, so two live copies of this exact
        # project collide on the container name regardless of ports -- a
        # limitation this test works around, not fixes (see the report).
        _compose(original, "down", "-v", timeout=300, check=False)

        restore.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(restore)

        # Squat the ports the restored copy would otherwise reuse, so `--yes`
        # actually has a conflict on its hands to resolve.
        blockers.append(_bind_port(8069))
        blockers.append(_bind_port(8888))

        monkeypatch.chdir(restore)
        result = runner.invoke(main, ["unpack", "--yes", "--no-ssh"])
        assert result.exit_code == 0, result.output

        compose_data = yaml.safe_load((restore / "docker-compose.yaml").read_text())
        web_ports = compose_data["services"]["web"]["ports"]
        assert not any(str(p).startswith("8069:") for p in web_ports), "the occupied port must have been remapped"

        # Read through the service name, not a hardcoded host port: the
        # restored copy landed on whatever port _check_ports picked.
        installed_after = _installed_module_count(restore, PROJECT_NAME)
        assert installed_after == installed_before
    finally:
        for sock in blockers:
            sock.close()
        if original.exists():
            _compose(original, "down", "-v", timeout=300, check=False)
        if restore.exists():
            _compose(restore, "down", "-v", timeout=300, check=False)
        subprocess.run(["docker", "rmi", "-f", PROJECT_NAME], capture_output=True)
        shutil.rmtree(original, ignore_errors=True)
        shutil.rmtree(restore, ignore_errors=True)
