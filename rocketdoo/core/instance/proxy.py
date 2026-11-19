"""Install the reverse proxy a Docker instance deploy assumes is already there.

The deployed compose only `expose`s Odoo and labels it for Traefik, so without
a Traefik on the `traefik-public` network the domain never answers. The files
the VPS gets are the ones `core/traefik.py` renders for local production mode,
embedded verbatim in the script, so local and remote cannot drift apart.
"""

import shlex
import subprocess

from rocketdoo.core.service import ProgressCallback, ServiceError, reporter
from rocketdoo.core.traefik import NETWORK, render_compose, render_traefik_yml

from .ssh_utils import build_ssh_cmd

TRAEFIK_DIR = "/opt/traefik"

_TIMEOUT = 300
_YML_EOF = "RKD_TRAEFIK_YML"
_COMPOSE_EOF = "RKD_TRAEFIK_COMPOSE"


class ProxyError(ServiceError):
    pass


def _write_unless_present(name: str, delimiter: str, content: str) -> str:
    return (
        f'if [ -e "$d/{name}" ]; then kept="$kept {name}"; else\n'
        f"cat > \"$d/{name}\" <<'{delimiter}'\n"
        f"{content}{delimiter}\n"
        f'wrote="$wrote {name}"; fi'
    )


def install_script(traefik_dir: str, email: str) -> str:
    if not email or "\n" in email or "\r" in email or _YML_EOF in email or _COMPOSE_EOF in email:
        raise ValueError("The Let's Encrypt email must be a single line of text")
    lines = [
        "{",
        f"d={shlex.quote(traefik_dir)}",
        'wrote=""; kept=""; ports_checked=skipped; taken=""',
        "own=$(docker ps -a --filter 'name=^traefik$' --format '{{.Names}}' </dev/null | grep -x traefik)",
        # An existing install of ours owns 80 and 443: probing the ports before
        # asking whether the container is ours aborts every run after the first.
        'if [ -z "$own" ]; then',
        "foreign=$(docker ps --format '{{.Names}} {{.Image}}' </dev/null"
        " | awk '$2 ~ /traefik/ && $1 != \"traefik\" {print $1; exit}')",
        'if [ -n "$foreign" ]; then',
        'echo "traefik_foreign=$foreign"',
        f"if docker network inspect {NETWORK} --format '{{{{range .Containers}}}}{{{{.Name}}}} {{{{end}}}}'"
        ' 2>/dev/null </dev/null | tr " " "\\n" | grep -qx "$foreign"; then echo foreign_attached=yes;'
        " else echo foreign_attached=no; fi",
        "echo started=no; echo done=yes; exit 0",
        "fi",
        'probe=""',
        "if command -v ss >/dev/null 2>&1; then probe='ss -ltn'",
        "elif command -v netstat >/dev/null 2>&1; then probe='netstat -ltn'; fi",
        'if [ -n "$probe" ]; then',
        "ports_checked=yes",
        "taken=$($probe 2>/dev/null | awk '{print $4}' | sed 's/.*[:.]//' | grep -xE '80|443' | sort -u | tr '\\n' ' ')",
        "else ports_checked=no; fi",
        'if [ -n "$taken" ]; then echo "ports_taken=$taken"; echo ports_checked=yes; echo started=no;'
        " echo done=yes; exit 0; fi",
        "fi",
        'mkdir -p "$d/certs" 2>/dev/null && [ -w "$d" ] ||'
        ' { sudo -n mkdir -p "$d/certs" && sudo -n chown -R "$(id -u):$(id -g)" "$d"; } ||'
        ' { echo "Cannot create $d: the SSH user needs passwordless sudo or a directory it owns" >&2; exit 1; }',
        _write_unless_present("traefik.yml", _YML_EOF, render_traefik_yml("production", email)),
        _write_unless_present("docker-compose.yml", _COMPOSE_EOF, render_compose("production")),
        # Traefik refuses to start with an acme.json readable by others, and a
        # truncated one means certificates reissued against Let's Encrypt's rate limit.
        '[ -e "$d/certs/acme.json" ] || touch "$d/certs/acme.json"',
        'chmod 600 "$d/certs/acme.json"',
        f"docker network inspect {NETWORK} >/dev/null 2>&1 </dev/null"
        f" || docker network create {NETWORK} >/dev/null </dev/null",
        'if (cd "$d" && docker compose up -d </dev/null) >&2; then started=yes; else started=no; fi',
        'echo "wrote=$wrote"; echo "kept=$kept"; echo "ports_checked=$ports_checked"; echo "started=$started"',
        "echo done=yes",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _parse(output: str) -> dict:
    facts = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            facts[key] = value.strip()
    return facts


def install(auth: dict, vps: dict, *, traefik_dir: str, email: str, on_progress: ProgressCallback | None = None) -> dict:
    progress = reporter(on_progress)
    script = install_script(traefik_dir, email)
    cmd, env = build_ssh_cmd(auth, vps.get("port", 22), vps["user"], vps["host"], "bash -s")
    progress(f"Installing Traefik in {traefik_dir} on {vps['host']}", "info")
    try:
        result = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=_TIMEOUT, env=env)
    except (subprocess.SubprocessError, OSError) as error:
        raise ProxyError(f"Could not reach {vps['host']}: {error}") from error
    if result.returncode != 0:
        reason = (result.stderr or "").strip() or f"ssh exited {result.returncode}"
        raise ProxyError(f"The install failed on {vps['host']}: {reason}")
    facts = _parse(result.stdout)
    if facts.get("done") != "yes":
        raise ProxyError("The VPS answered but the install ended before it finished")
    return {
        "wrote": facts.get("wrote", "").split(),
        "kept": facts.get("kept", "").split(),
        "ports_checked": facts.get("ports_checked", "no"),
        "ports_taken": facts.get("ports_taken", "").split(),
        "traefik_foreign": facts.get("traefik_foreign", ""),
        "foreign_attached": facts.get("foreign_attached", ""),
        "started": facts.get("started") == "yes",
    }
