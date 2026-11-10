"""
RocketDoo - .gitignore management for generated projects.

A Rocketdoo project holds credentials on disk (the SSH build context, the
PostgreSQL secret, admin passwords inside config files). Without a .gitignore
a plain `git add .` commits all of them, so scaffold/init always write one and
`rkd info` warns when an existing project is missing coverage.

The template lives at templates/.gitignore.jinja — named with the .jinja
suffix so git does not apply it to Rocketdoo's own source tree.
"""

from pathlib import Path

_TEMPLATE = Path(__file__).parent.parent / "templates" / ".gitignore.jinja"

# Entries that must be present for credentials to stay out of the repo.
# Kept deliberately short: these are the leak vectors, not the full template.
SENSITIVE_ENTRIES = [
    (".ssh/", "SSH private key copied into the build context"),
    (".rkd/secrets/", "VPS passwords"),
    ("odoo_pg_pass", "PostgreSQL password"),
    ("*.env", "environment variables: API keys and passwords read at runtime"),
    (".rkd/instance.yaml", "deployment config with admin_passwd"),
    (".rkd/deploy.yaml", "deploy targets with VPS host, user and SSH key path"),
    ("config/odoo.conf", "Odoo config with admin_passwd"),
    ("traefik/certs/acme.json", "Let's Encrypt account key and issued certificates"),
]

# Patterns that would also swallow the file documenting them. `.env` holds the
# values; `.env.example` holds the names, and it is what lets whoever receives
# the project write their own. The template already draws that line with
# `!*.env.example`, and everything built on SENSITIVE_ENTRIES draws the same
# one: the block appended below and the ZIP exclusion in core/pack.py (#253).
SENSITIVE_EXCEPTIONS = {"*.env": ("*.env.example",)}


def template_content() -> str:
    """Return the canonical .gitignore shipped with Rocketdoo."""
    return _TEMPLATE.read_text()


def _entries(text: str) -> set[str]:
    """Non-comment, non-empty lines of a .gitignore."""
    return {line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")}


def missing_entries(project_root: Path) -> list[tuple[str, str]]:
    """
    Return the sensitive entries a project's .gitignore does not cover.

    A missing .gitignore returns every entry.
    """
    gitignore = Path(project_root) / ".gitignore"
    if not gitignore.exists():
        return list(SENSITIVE_ENTRIES)

    present = _entries(gitignore.read_text())
    return [(pat, why) for pat, why in SENSITIVE_ENTRIES if pat not in present]


def ensure_gitignore(project_root: Path) -> tuple[str, list[str]]:
    """
    Guarantee the project's .gitignore covers every sensitive entry.

    Returns (action, entries) where action is:
        'created' — no .gitignore existed, the full template was written
        'appended' — an existing file was kept and missing entries added
        'ok'      — already covered, nothing written

    An existing .gitignore is never overwritten: user rules are preserved and
    only the missing sensitive entries are appended.
    """
    project_root = Path(project_root)
    gitignore = project_root / ".gitignore"

    if not gitignore.exists():
        gitignore.write_text(template_content())
        return "created", [pat for pat, _ in SENSITIVE_ENTRIES]

    missing = missing_entries(project_root)
    if not missing:
        return "ok", []

    current = gitignore.read_text()
    block = ["", "# ─── Added by Rocketdoo: secrets that must not be committed ───"]
    for pat, _ in missing:
        block.append(pat)
        block += [f"!{exception}" for exception in SENSITIVE_EXCEPTIONS.get(pat, ())]
    suffix = "\n".join(block) + "\n"
    gitignore.write_text(current if current.endswith("\n") else current + "\n")
    with gitignore.open("a") as fh:
        fh.write(suffix)

    return "appended", [pat for pat, _ in missing]
