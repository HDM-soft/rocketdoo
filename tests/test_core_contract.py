"""Structural guard for the core/ service contract (RF1).

Same mechanism as tests/test_secrets_hardening.py: parse the module's AST and
flag shapes that violate the contract, rather than trust that reviewers will
always notice a stray `print()` or `rich` import creeping into core/.

Two explicit allowlists, one entry per module, growing as later tasks of
#143 move or create services:

- PURE_CORE (RF1.2 + RF1.3): no click/questionary/rich/typer import, no
  print()/input()/sys.exit()/os._exit() call.
- NO_CWD (RF1.1): in addition, no implicit Path.cwd()/os.getcwd() outside the
  visible `X if project_root else Path.cwd()` default-argument idiom that
  core/compose.py's compose_path() already uses. Only modules created or
  moved by this epic go here: the three seed modules below seed PURE_CORE
  only, since RF1.1 does not ask anything new of code that already predates
  #143.

core/deploy/** and core/instance/** are known to import rich/questionary and
are deliberately left off both lists: that is pre-existing debt out of scope
for #143, not something this guard should flag.
"""

import ast
from pathlib import Path

import pytest

ROCKETDOO_ROOT = Path(__file__).resolve().parent.parent / "rocketdoo"

PURE_CORE = [
    "core/service.py",
    "core/compose.py",
    "core/addons_path.py",
    "core/odoo_db.py",
    "core/project_info.py",
    "core/mailpit.py",
    "core/traefik.py",
    "core/pack.py",
    "core/unpack.py",
]

NO_CWD: list[str] = [
    "core/project_info.py",
    "core/mailpit.py",
    "core/traefik.py",
    "core/pack.py",
    "core/unpack.py",
]

_FORBIDDEN_IMPORTS = {"click", "questionary", "rich", "typer"}
# exit()/quit() are builtins, so a body moved out of the CLI can keep aborting
# the process without the `sys.` prefix that _FORBIDDEN_QUALIFIED_CALLS catches.
_FORBIDDEN_CALLS = {"print", "input", "exit", "quit"}
_FORBIDDEN_QUALIFIED_CALLS = {("sys", "exit"), ("os", "_exit")}
_CWD_CALLS = {("Path", "cwd"), ("os", "getcwd")}


def _module_ast(relative_path: str) -> ast.Module:
    path = ROCKETDOO_ROOT / relative_path
    return ast.parse(path.read_text(), filename=str(path))


def _imported_top_level_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _qualified_call(node: ast.Call) -> tuple[str, str] | None:
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return node.func.value.id, node.func.attr
    return None


def _presentation_violations(tree: ast.Module) -> list[str]:
    violations = [f"imports '{name}'" for name in sorted(_imported_top_level_names(tree) & _FORBIDDEN_IMPORTS)]

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
            violations.append(f"line {node.lineno}: calls '{node.func.id}()'")
            continue
        qualified = _qualified_call(node)
        if qualified in _FORBIDDEN_QUALIFIED_CALLS:
            violations.append(f"line {node.lineno}: calls '{qualified[0]}.{qualified[1]}()'")

    return violations


def _default_fallback_call_ids(tree: ast.Module) -> set[int]:
    """id() of every Path.cwd()/os.getcwd() sitting in an `X if p else ...` idiom."""
    exempt = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.IfExp) and isinstance(node.orelse, ast.Call):
            if _qualified_call(node.orelse) in _CWD_CALLS:
                exempt.add(id(node.orelse))
    return exempt


def _cwd_violations(tree: ast.Module) -> list[str]:
    exempt = _default_fallback_call_ids(tree)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and id(node) not in exempt:
            qualified = _qualified_call(node)
            if qualified in _CWD_CALLS:
                violations.append(
                    f"line {node.lineno}: '{qualified[0]}.{qualified[1]}()' outside the "
                    "visible 'X if project_root else ...' default"
                )
    return violations


# The two helpers above are what the epic leans on from T3 onward. These exercise
# them against synthetic source, so a helper that silently stops detecting
# anything fails here instead of turning every module list green by accident.


@pytest.mark.parametrize(
    "source",
    [
        "def f():\n    print('x')\n",
        "def f():\n    return input('x')\n",
        "import sys\ndef f():\n    sys.exit(1)\n",
        "def f():\n    exit(1)\n",
        "import click\n",
        "from rich.console import Console\n",
        "import questionary\n",
    ],
)
def test_presentation_guard_flags_each_forbidden_shape(source):
    assert _presentation_violations(ast.parse(source))


def test_presentation_guard_accepts_a_clean_module():
    source = "from pathlib import Path\n\n\ndef f(root):\n    return Path(root)\n"
    assert not _presentation_violations(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\ndef f():\n    return Path.cwd()\n",
        "import os\ndef f():\n    return os.getcwd()\n",
        # evaluated once at import time, so it silently freezes the directory
        "from pathlib import Path\ndef f(root=Path.cwd()):\n    return root\n",
    ],
)
def test_cwd_guard_flags_an_implicit_working_directory(source):
    assert _cwd_violations(ast.parse(source))


def test_cwd_guard_allows_the_visible_default_idiom():
    """The shape core/compose.py already uses: explicit, and visible in the signature."""
    source = (
        "from pathlib import Path\n\n\n"
        "def f(project_root=None):\n"
        "    return Path(project_root) if project_root else Path.cwd()\n"
    )
    assert not _cwd_violations(ast.parse(source))


def test_pure_core_modules_have_no_presentation_layer():
    violations = {path: _presentation_violations(_module_ast(path)) for path in PURE_CORE}
    violations = {path: found for path, found in violations.items() if found}
    assert not violations, violations


def test_no_cwd_modules_do_not_default_to_the_process_cwd():
    violations = {path: _cwd_violations(_module_ast(path)) for path in NO_CWD}
    violations = {path: found for path, found in violations.items() if found}
    assert not violations, violations
