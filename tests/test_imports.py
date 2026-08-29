"""Import-level smoke tests.

Four GUI endpoints shipped broken for months because they import their
implementation lazily, inside the request handler, and swallow the resulting
ImportError into an HTTP 200 ``{"ok": false}`` body. Nothing failed loudly, so
nothing was noticed.

These tests walk the package the way an installer would and resolve every
intra-package import — including the deferred ones — so that a missing module
or a renamed function breaks CI instead of a user's browser.

Every module in the package is expected to import cleanly: the v1/v2 orphans
that could not (config.py blocked on stdin at import time, main.py imported a
module that no longer existed) were removed in #137.
"""

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

from .conftest import PACKAGE_ROOT


def _package_modules() -> list[str]:
    import rocketdoo

    return sorted(m.name for m in pkgutil.walk_packages(rocketdoo.__path__, "rocketdoo."))


def _source_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


@pytest.mark.parametrize("module_name", _package_modules())
def test_module_imports(module_name):
    """Every shipped module imports cleanly."""
    importlib.import_module(module_name)


def test_console_entrypoint_is_callable():
    """The `rkd` / `rocketdoo` entry point resolves and is a Click group."""
    from rocketdoo.cli import main

    assert callable(main)


def _intra_package_imports():
    """(file, lineno, module, symbol) for every rocketdoo.* import in the tree.

    Covers deferred imports inside functions, which is where the broken GUI
    endpoints live and which a plain `import rocketdoo.x` smoke test misses.
    """
    found = []
    for py in _source_files():
        rel = py.relative_to(PACKAGE_ROOT.parent)
        module_path = str(rel.with_suffix("")).replace("/", ".")
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:  # relative import -> absolutise
                base = module_path.rsplit(".", node.level)[0]
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            if not target.startswith("rocketdoo"):
                continue
            for alias in node.names:
                if alias.name != "*":
                    found.append((rel.as_posix(), node.lineno, target, alias.name))
    return found


_IMPORTS = _intra_package_imports()


def test_intra_package_imports_were_discovered():
    """Guard against the AST walk silently finding nothing."""
    assert len(_IMPORTS) > 50


@pytest.mark.parametrize(
    ("where", "lineno", "module", "symbol"),
    _IMPORTS,
    ids=[f"{w}:{ln}:{m.split('.')[-1]}.{s}" for w, ln, m, s in _IMPORTS],
)
def test_imported_symbol_exists(where, lineno, module, symbol):
    """Every `from rocketdoo.x import y` resolves to a real `y`.

    Deferred imports are never executed at import time, so this is the only
    check that catches a helper that was renamed or never written.
    """
    mod = importlib.import_module(module)
    if hasattr(mod, symbol):
        return
    try:  # `from package import submodule` is also legal
        importlib.import_module(f"{module}.{symbol}")
    except ImportError:
        pytest.fail(f"{where}:{lineno} imports {symbol!r} from {module}, which does not define it")
