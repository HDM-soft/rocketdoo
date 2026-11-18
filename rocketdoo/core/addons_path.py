"""Keep a project's addons_path in sync with what is under addons/.

Odoo's addons_path is a static, comma-separated list of directories that each
hold modules directly; a nested addons/oca/mod is invisible to Odoo unless
.../extra-addons/oca is listed too. discover() finds those directories and
ensure_addons_path() merges them into an existing config/odoo.conf without
touching any other option in the file.
"""

from pathlib import Path

from rocketdoo.core.module_scanner import ModuleScanner
from rocketdoo.core.odoo_conf import read_addons_path, write_addons_path

CONTAINER_ADDONS_ROOT = "/usr/lib/python3/dist-packages/odoo/extra-addons"

COLLAPSED = "collapsed to a single line"

_EXCLUDE_PATTERNS = [
    "*/tests/*",
    "*/__pycache__/*",
    "*/.git/*",
    "*/node_modules/*",
    "*/setup/*",
]


def discover(project_root: Path | str, container_root: str = CONTAINER_ADDONS_ROOT) -> list[str]:
    """Container-side addons_path entries covering every module under addons/.

    Always includes `container_root`, even when addons/ is empty or
    missing, so this never degrades the current single-entry behavior.
    """
    addons_dir = Path(project_root) / "addons"
    if not addons_dir.is_dir():
        return [container_root]

    scanner = ModuleScanner(addons_dir, exclude_patterns=_EXCLUDE_PATTERNS)

    entries = {container_root}
    for module in scanner.scan():
        parent = module.relative_path.parent
        if str(parent) == ".":
            continue
        # addons_path is one comma-separated line: a directory whose name holds
        # a comma or a newline would split into bogus entries, and re-split on
        # every run, growing the line without ever converging.
        if any(char in parent.as_posix() for char in ",\n\r"):
            continue
        entries.add(f"{container_root}/{parent.as_posix()}")

    return sorted(entries)


def _is_managed(path: str) -> bool:
    """True for CONTAINER_ADDONS_ROOT itself or a directory inside it.

    A plain startswith() would also claim sibling directories that merely
    share the prefix, such as .../extra-addons-private, and prune them out
    of the user's config.
    """
    return path == CONTAINER_ADDONS_ROOT or path.startswith(f"{CONTAINER_ADDONS_ROOT}/")


def missing_entries(project_root: Path | str) -> list[str]:
    """Discovered directories the project's addons_path does not list yet.

    Read-only counterpart of ensure_addons_path(), for callers that report
    without writing, mirroring gitignore_manager.missing_entries().
    """
    odoo_conf = Path(project_root) / "config" / "odoo.conf"
    if not odoo_conf.exists():
        return []

    current_paths = read_addons_path(odoo_conf)
    return [path for path in discover(project_root) if path not in current_paths]


def ensure_addons_path(project_root: Path | str) -> tuple[str, list[str]]:
    """Merge discover() into config/odoo.conf's addons_path option.

    Returns (action, changes):
        "missing" - config/odoo.conf does not exist, nothing written.
        "ok"      - addons_path already said exactly this, nothing written.
        "updated" - the addons_path option was rewritten; changes lists the
                    entries added ("+path") and removed ("-path"), or
                    COLLAPSED when only the option's layout changed.
        "failed"  - the file could not be written; changes holds the reason.

    Entries outside CONTAINER_ADDONS_ROOT (Odoo's default, enterprise,
    Gitman's external_addons, user paths) are kept as-is, in their original
    order and position. Only the managed block is replaced, and only the
    addons_path option is touched -- a value spread over several lines is
    rewritten as one, which is also what repairs a value an older rkd broke.
    """
    project_root = Path(project_root)
    odoo_conf = project_root / "config" / "odoo.conf"
    if not odoo_conf.exists():
        return "missing", []

    discovered = discover(project_root)
    current_paths = read_addons_path(odoo_conf)
    managed = [path for path in current_paths if _is_managed(path)]

    # A managed entry that is still discovered stays exactly where it is, so a
    # foreign path listed between two of ours keeps its place in the order Odoo
    # resolves modules by. What is new goes behind the last managed entry, or
    # at the end when the option lists none of ours yet.
    new_paths: list[str] = []
    pending = list(discovered)
    insert_at = None
    for path in current_paths:
        if not _is_managed(path):
            new_paths.append(path)
            continue
        if insert_at is None:
            insert_at = len(new_paths)
        if path in pending:
            new_paths.append(path)
            pending.remove(path)
            insert_at = len(new_paths)
    if insert_at is None:
        insert_at = len(new_paths)
    new_paths[insert_at:insert_at] = pending

    changes = [f"+{path}" for path in discovered if path not in managed]
    changes += [f"-{path}" for path in managed if path not in discovered]

    try:
        written = write_addons_path(odoo_conf, new_paths)
    except OSError as error:
        # Called from `rkd up` and from the GUI: a config we cannot write is
        # a reason to warn, never a reason to leave the project down.
        return "failed", [str(error)]

    if not written:
        return "ok", []

    return "updated", changes or [COLLAPSED]
