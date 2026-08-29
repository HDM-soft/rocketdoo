"""Shared fixtures for the Rocketdoo test suite."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "rocketdoo"
TEMPLATES = PACKAGE_ROOT / "templates"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=15,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.fixture(scope="session")
def docker_available() -> bool:
    return _docker_available()


@pytest.fixture
def requires_docker(docker_available):
    if not docker_available:
        pytest.skip("Docker daemon not available")


@pytest.fixture
def project_dir(tmp_path, monkeypatch) -> Path:
    """An empty directory that is also the process CWD.

    Most of Rocketdoo reads Path.cwd() directly, so tests that exercise those
    code paths need the working directory moved, not just a path argument.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def addons_tree(tmp_path) -> Path:
    """A small addons/ tree covering the cases the scanner must tell apart."""
    addons = tmp_path / "addons"

    def module(name, *, installable=True, version="16.0.1.0.0", depends=("base",), extra=""):
        mod = addons / name
        mod.mkdir(parents=True)
        (mod / "__init__.py").write_text("")
        manifest = (
            "{\n"
            f"    'name': '{name.replace('_', ' ').title()}',\n"
            f"    'version': '{version}',\n"
            f"    'installable': {installable},\n"
            f"    'depends': {list(depends)!r},\n"
            f"{extra}"
            "}\n"
        )
        (mod / "__manifest__.py").write_text(manifest)
        return mod

    module("sale_extension")
    module("stock_custom", depends=("base", "stock"))
    module("legacy_module", installable=False)

    # Not modules: no manifest at all, and a manifest nested under tests/
    plain = addons / "not_a_module"
    plain.mkdir(parents=True)
    (plain / "__init__.py").write_text("")

    excluded = addons / "sale_extension" / "tests"
    excluded.mkdir(parents=True)
    (excluded / "__manifest__.py").write_text("{'name': 'should be skipped'}\n")

    return addons
