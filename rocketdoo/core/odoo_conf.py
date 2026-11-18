"""Read and write the addons_path option of a project's config/odoo.conf.

Odoo parses odoo.conf with configparser, where one option's value may carry on
over indented lines. Rewriting only the line that holds the delimiter leaves
those continuations orphaned: they stay part of the value, the entry they are
glued to ends up with a newline inside it, and Odoo drops that path without a
word. Every caller that edits addons_path goes through here so that the whole
option is read and the whole option is replaced.

configparser is the reference for where the option starts and ends, because it
is what Odoo itself runs (tools/config.py builds a RawConfigParser with no
arguments). That means both '=' and ':' open an option, and a whole-line
comment does not end a value.
"""

import re
from pathlib import Path

OPTION = "addons_path"

_DELIMITERS = "=:"
_CONTINUATION = re.compile(r"^[ \t]")
_COMMENT_PREFIXES = (";", "#")
# A key never holds a '/', which is what tells an option someone mis-indented
# from a path that happens to contain a delimiter, such as /opt/x=y/addons.
_MIS_INDENTED_OPTION = re.compile(r"^[ \t]*[^\s=:/]+[ \t]*[=:]")


def _split(line: str) -> tuple[str, str] | None:
    """(key, value) if the line opens an option, else None.

    configparser takes whichever delimiter comes first, so this does too.
    """
    found = [index for index in (line.find(delimiter) for delimiter in _DELIMITERS) if index >= 0]
    if not found:
        return None

    at = min(found)
    return line[:at], line[at + 1 :]


def _carried(line: str) -> bool:
    """A line inside the value that holds no path and must survive a rewrite.

    Two kinds. A whole-line comment, which configparser drops wherever it sits,
    so it is no path and no path is made of it. And an option someone
    mis-indented: configparser reads that one as part of this value -- Odoo has
    already lost it -- but it is the user's line, so it is neither turned into a
    path nor deleted from the file.
    """
    return line.lstrip().startswith(_COMMENT_PREFIXES) or bool(_MIS_INDENTED_OPTION.match(line))


def _read(conf_path: Path) -> str:
    # newline="" keeps \r\n intact: rewriting one option must not convert the
    # line endings of a config written on Windows.
    with Path(conf_path).open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _block(lines: list[str]) -> tuple[int, int] | None:
    """Half-open line range holding the whole addons_path option, or None."""
    for index, line in enumerate(lines):
        if _CONTINUATION.match(line):
            # Indented, so this opens nothing: it is some other option's value
            # carrying on, and rewriting it would edit that option instead.
            continue
        split = _split(line)
        if split and split[0].strip() == OPTION:
            end = index + 1
            while end < len(lines) and (
                not lines[end].strip() or lines[end].lstrip().startswith(_COMMENT_PREFIXES) or _CONTINUATION.match(lines[end])
            ):
                end += 1
            while end > index + 1 and not lines[end - 1].strip():
                end -= 1
            return index, end

    return None


def read_addons_path(conf_path: Path) -> list[str]:
    """Every path the addons_path option lists, continuation lines included.

    Entries are split on newlines as well as commas. A newline is never a
    legitimate separator -- Odoo rejects a path that contains one -- so the
    only way to meet one is a value a line-only rewrite has already broken, and
    splitting it back apart is what lets the next write repair the file.
    """
    lines = _read(conf_path).splitlines()
    span = _block(lines)
    if span is None:
        return []

    start, end = span
    body = [line for line in lines[start + 1 : end] if not _carried(line)]
    value = "\n".join([_split(lines[start])[1], *body])
    return [path.strip() for path in re.split(r"[,\n]", value) if path.strip()]


def write_addons_path(conf_path: Path, entries: list[str]) -> bool:
    """Replace the addons_path option with one line listing entries.

    Returns True when the file was rewritten, False when it already said
    exactly this. Nothing outside the option is touched, so a multi-line value
    collapses to a single line the one time it is rewritten.
    """
    conf_path = Path(conf_path)
    text = _read(conf_path)
    newline = "\r\n" if "\r\n" in text else "\n"
    ends_with_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()

    # Odoo gains nothing from reading the same directory twice, and repairing a
    # value a line-only rewrite broke is exactly how an entry ends up twice.
    new_line = f"{OPTION} = {','.join(dict.fromkeys(entries))}"
    span = _block(lines)
    if span is None:
        lines.append(new_line)
    else:
        start, end = span
        new_block = [new_line, *(line for line in lines[start + 1 : end] if _carried(line))]
        if lines[start:end] == new_block:
            return False
        lines[start:end] = new_block

    new_text = newline.join(lines)
    if ends_with_newline or span is None:
        new_text += newline

    with conf_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)

    return True
