"""Installing the gitman repositories of a native deploy on the VPS.

gitman runs on the VPS and not on the developer's machine because the scripts
rkd writes into each source (`sh .../install_dependencies.sh`) do a
`pip install` in the interpreter that runs Odoo, which here is the system
Python of the VPS.
"""

import shlex

GITMAN_DIR = "/opt/odoo/gitman"
GITMAN_VENV = "/opt/odoo/gitman-venv"
GITMAN_FILE = f"{GITMAN_DIR}/gitman.yml"
DEPENDENCIES_DIR = "/usr/lib/python3/dist-packages/odoo"
DEPENDENCIES_SCRIPT = f"{DEPENDENCIES_DIR}/install_dependencies.sh"

_HEREDOC = "RKD_GITMAN_EOF"

# The pins of templates/instance/Dockerfile.jinja.
_VENV_PIP = (
    f"{GITMAN_VENV}/bin/pip install --upgrade pip setuptools wheel",
    f'{GITMAN_VENV}/bin/pip install "Cython<3"',
    f'{GITMAN_VENV}/bin/pip install "PyYAML==5.4.1" --no-build-isolation',
    f"{GITMAN_VENV}/bin/pip install simplejson parse requests schema colorama YORM==1.6.2 gitman==1.4.0 --no-deps",
)


def _heredoc(command: str, body: str) -> str:
    if _HEREDOC in body:
        raise ValueError(f"the content to send contains the heredoc delimiter {_HEREDOC}")
    return f"{command} <<'{_HEREDOC}'\n{body.rstrip(chr(10))}\n{_HEREDOC}\n"


def install_script(environment: str, gitman_config: str, ssh_hosts: list[str], dependencies_script: str = "") -> str:
    """The `bash -s` script that leaves the gitman sources under GITMAN_DIR.

    Takes the content of the user's gitman file and of install_dependencies.sh
    (empty when the project has none) instead of reading them: it is pure. The
    tree is handed to the SSH user for the clone, which is what the forwarded
    agent authenticates, and to `odoo` at the end.
    """
    hosts = " ".join(shlex.quote(host) for host in dict.fromkeys(["github.com", *ssh_hosts]))
    force = " --force" if environment == "stage" else ""
    venv = "\n".join(f"  sudo {line}" for line in _VENV_PIP)
    deps = (
        _heredoc(f"sudo tee {shlex.quote(DEPENDENCIES_SCRIPT)} >/dev/null", dependencies_script)
        + f"sudo chmod 755 {shlex.quote(DEPENDENCIES_SCRIPT)}\n"
        if dependencies_script
        else ""
    )
    # gitman is called by absolute path and its venv is never activated. The
    # `scripts:` of each source run `pip install` and must reach the system
    # pip, because that is where the packaged Odoo looks for its
    # dependencies: with the venv active (or its bin on PATH) they land inside
    # it, Odoo never sees them, and the deploy still reports success.
    return (
        "{\n"
        "set -e\n"
        "export GITMAN_CACHE=0\n"
        f"sudo mkdir -p {GITMAN_DIR} {DEPENDENCIES_DIR}\n"
        f'sudo chown -R "$(id -un)": {GITMAN_DIR}\n'
        f"if [ ! -x {GITMAN_VENV}/bin/gitman ]; then\n"
        f"  sudo python3 -m venv {GITMAN_VENV}\n"
        f"{venv}\n"
        "fi\n"
        "mkdir -p ~/.ssh\n"
        f"ssh-keyscan {hosts} >> ~/.ssh/known_hosts 2>/dev/null\n"
        + _heredoc(f"cat > {GITMAN_FILE}", gitman_config)
        + deps
        + f"cd {GITMAN_DIR}\n"
        f"{GITMAN_VENV}/bin/gitman install{force} </dev/null\n"
        f"sudo chown -R odoo: {GITMAN_DIR}\n"
        "}\n"
    )
