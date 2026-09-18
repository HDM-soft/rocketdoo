"""Keep a project's addons_path in sync with what is under addons/.

Odoo's addons_path is a static, comma-separated list of directories that each
hold modules directly; a nested addons/oca/mod is invisible to Odoo unless
.../extra-addons/oca is listed too. discover() finds those directories and
ensure_addons_path() merges them into an existing config/odoo.conf without
touching anything else in the file.
"""

from pathlib import Path

from rocketdoo.core.module_scanner import ModuleScanner

CONTAINER_ADDONS_ROOT = "/usr/lib/python3/dist-packages/odoo/extra-addons"

_EXCLUDE_PATTERNS = [
    "*/tests/*",
    "*/__pycache__/*",
    "*/.git/*",
    "*/node_modules/*",
    "*/setup/*",
]


def discover(project_root: Path | str) -> list[str]:
    """Container-side addons_path entries covering every module under addons/.

    Always includes CONTAINER_ADDONS_ROOT, even when addons/ is empty or
    missing, so this never degrades the current single-entry behavior.
    """
    scanner = ModuleScanner(Path(project_root) / "addons", exclude_patterns=_EXCLUDE_PATTERNS)

    entries = {CONTAINER_ADDONS_ROOT}
    for module in scanner.scan():
        parent = module.relative_path.parent
        if str(parent) != ".":
            entries.add(f"{CONTAINER_ADDONS_ROOT}/{parent.as_posix()}")

    return sorted(entries)


def _is_managed(path: str) -> bool:
    """True for CONTAINER_ADDONS_ROOT itself or a directory inside it.

    A plain startswith() would also claim sibling directories that merely
    share the prefix, such as .../extra-addons-private, and prune them out
    of the user's config.
    """
    return path == CONTAINER_ADDONS_ROOT or path.startswith(f"{CONTAINER_ADDONS_ROOT}/")


def _parse_addons_path(lines: list[str]) -> tuple[int | None, list[str]]:
    for index, line in enumerate(lines):
        if line.strip().startswith("addons_path"):
            _, _, value = line.partition("=")
            return index, [path.strip() for path in value.split(",") if path.strip()]
    return None, []


def ensure_addons_path(project_root: Path | str) -> tuple[str, list[str]]:
    """Merge discover() into config/odoo.conf's addons_path line.

    Returns (action, changes):
        "missing" - config/odoo.conf does not exist, nothing written.
        "ok"      - addons_path already covers discover() exactly, nothing written.
        "updated" - the addons_path line was rewritten; changes lists the
                    entries added ("+path") and removed ("-path").

    Entries outside CONTAINER_ADDONS_ROOT (Odoo's default, enterprise,
    Gitman's external_addons, user paths) are kept as-is, in their original
    order and position. Only the managed block is replaced,
    and only the addons_path line is touched.
    """
    project_root = Path(project_root)
    odoo_conf = project_root / "config" / "odoo.conf"
    if not odoo_conf.exists():
        return "missing", []

    discovered = discover(project_root)
    text = odoo_conf.read_text()
    ends_with_newline = text.endswith("\n")
    lines = text.splitlines()

    line_index, current_paths = _parse_addons_path(lines)
    managed = [path for path in current_paths if _is_managed(path)]
    kept = [path for path in current_paths if not _is_managed(path)]

    if managed == discovered:
        return "ok", []

    insert_at = 0
    for path in current_paths:
        if _is_managed(path):
            break
        insert_at += 1
    new_paths = kept[:insert_at] + discovered + kept[insert_at:]

    changes = [f"+{path}" for path in discovered if path not in managed]
    changes += [f"-{path}" for path in managed if path not in discovered]

    new_line = f"addons_path = {','.join(new_paths)}"
    if line_index is None:
        lines.append(new_line)
    else:
        lines[line_index] = new_line

    new_text = "\n".join(lines)
    if ends_with_newline or line_index is None:
        new_text += "\n"
    odoo_conf.write_text(new_text)

    return "updated", changes
