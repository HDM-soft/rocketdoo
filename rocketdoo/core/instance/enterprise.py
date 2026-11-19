"""Getting odoo/enterprise onto a VPS.

A deploy key cannot do this: it is granted by whoever administers the
repository, and odoo/enterprise belongs to Odoo, not to the user. The only
thing left would be an account-level key stored on the server with read access
to every repository of that account. Agent forwarding uses the local key that
is already authorised and leaves no credential on the VPS.
"""

import shlex

REPO = "git@github.com:odoo/enterprise.git"


GIT_SSH = "GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new'"


def branch(odoo_version: str) -> str:
    """The odoo/enterprise branch for an Odoo series: `19` and `19.0` both give `19.0`."""
    version = odoo_version.strip()
    if not version:
        raise ValueError("odoo_version is empty in this environment's instance.yaml; it names the Enterprise branch")
    return version if "." in version else f"{version}.0"


def clone_script(dest: str, branch: str) -> str:
    """Clone odoo/enterprise into `dest`, or move an existing checkout to `branch`.

    A destination with content that is not a git checkout (what the rsync
    mode leaves, since it excludes .git) is refused: git cannot clone into it.
    """
    target = shlex.quote(dest)
    ref = shlex.quote(branch)
    return (
        "set -e\n"
        f"if [ -d {target}/.git ]; then\n"
        f"  {GIT_SSH} git -C {target} fetch --depth 1 origin {ref} && git -C {target} reset --hard FETCH_HEAD\n"
        f'elif [ ! -e {target} ] || [ -z "$(ls -A {target})" ]; then\n'
        f"  {GIT_SSH} git clone --depth 1 --branch {ref} {REPO} {target}\n"
        "else\n"
        f"  echo {shlex.quote(dest + ' has content that is not a git checkout (left by the rsync mode?): empty it to clone.')} >&2\n"
        "  exit 1\n"
        "fi\n"
    )
