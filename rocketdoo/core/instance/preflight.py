"""What a deploy needs from this machine and from the VPS, asked before it starts.

`local_facts()` and `remote_facts()` only gather; `evaluate()` turns the facts
into checks. A check at level "block" means the deploy cannot work; one at
"warn" means it can, with a caveat the operator should hear.
"""

import os
import re
import shlex
import subprocess
from pathlib import Path

from rocketdoo.core.gitman_config import gitman_has_scripts, gitman_repos

from .config_manager import (
    CRON_THREADS,
    DEBIAN_PG_SHARED_BUFFERS,
    DEFAULT_GITMAN_FILE,
    MEMORY_BY_ENV,
    PG_PROFILES,
    WORKERS_BY_ENV,
    invalid_identifiers,
)
from .enterprise import REPO as ENTERPRISE_REPO
from .ssh_utils import build_ssh_cmd

Check = dict

_REMOTE_TIMEOUT = 90
_MIN_FREE_KB = 5 * 1024 * 1024
_MIB = 1024 * 1024
_GIT_PROBE_SECONDS = 30
INSTALL_SCRIPT = "install_dependencies.sh"
_TRAEFIK_NETWORK = "traefik-public"
_SENTINEL = "done"


def _agent_has_keys() -> bool:
    try:
        return subprocess.run(["ssh-add", "-l"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _remote_path(cfg: dict) -> str:
    return cfg.get("remote_path", "/")


def gitman_file(cfg: dict) -> str:
    return cfg.get("gitman_config") or DEFAULT_GITMAN_FILE


def local_facts(project_root: Path, cfg: dict) -> dict:
    root = Path(project_root)
    vps = cfg.get("vps", {})
    docker_gitman = bool(cfg.get("use_gitman")) and cfg.get("type", "docker") == "docker"
    key = vps.get("ssh_key", "~/.ssh/id_rsa") if vps.get("auth_method", "ssh_key") == "ssh_key" else None
    return {
        "enterprise_dir": (root / "enterprise").is_dir(),
        "gitman_file": (root / gitman_file(cfg)).is_file(),
        "gitman_urls": list(dict.fromkeys(gitman_repos(root / gitman_file(cfg)))) if docker_gitman else [],
        "gitman_scripts": docker_gitman and gitman_has_scripts(root / gitman_file(cfg)),
        "install_script": (root / INSTALL_SCRIPT).is_file(),
        "agent_has_keys": _agent_has_keys(),
        "ssh_auth_sock": bool(os.environ.get("SSH_AUTH_SOCK")),
        "build_needs_ssh": build_needs_ssh(root, cfg),
        "key_file": None if key is None else os.path.exists(os.path.expanduser(key)),
        "invalid_identifiers": invalid_identifiers(cfg.get("db_user", ""), cfg.get("db_name", "")),
    }


def build_needs_ssh(project_root: Path, cfg: dict) -> bool:
    """Whether the Docker build has to forward an agent.

    Derived by exclusion: a scp-style `github.com:org/repo` carries no user and
    no scheme, so matching the SSH forms would miss it and strip a flag the
    build needs. A false positive only keeps today's behaviour.
    """
    if not cfg.get("use_gitman"):
        return False
    repos = gitman_repos(Path(project_root) / gitman_file(cfg))
    return any(not url.startswith(("http://", "https://")) for url in repos)


def _enterprise_urls(cfg: dict) -> list[str]:
    if cfg.get("use_enterprise") and cfg.get("enterprise_source", "rsync") == "clone":
        return [ENTERPRISE_REPO]
    return []


def _remote_script(cfg: dict, repos: list[str]) -> str:
    lines = [
        "{",
        "echo whoami=$(whoami)",
        "echo uid=$(id -u)",
        "echo mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)",
        f"p={shlex.quote(_remote_path(cfg))}",
        'while [ ! -e "$p" ] && [ "$p" != / ]; do p=$(dirname "$p"); done',
        'echo path_writable=$([ -w "$p" ] && echo yes || echo no)',
        "echo free_kb=$(df -Pk \"$p\" | awk 'NR==2 {print $4}')",
        "echo sudo=$(sudo -n true >/dev/null 2>&1 && echo yes || echo no)",
        "echo git=$(command -v git >/dev/null 2>&1 && echo yes || echo no)",
    ]
    if cfg.get("type", "docker") == "docker":
        lines += [
            "echo docker_ok=$(docker info >/dev/null 2>&1 && echo yes || echo no)",
            "echo compose=$(docker compose version --short 2>/dev/null </dev/null)",
            "echo traefik_running=$(docker ps 2>/dev/null | grep -qi traefik && echo yes || echo no)",
            f"echo traefik_attached=$(docker ps --filter network={_TRAEFIK_NETWORK} 2>/dev/null"
            " | grep -qi traefik && echo yes || echo no)",
        ]
    for url in repos:
        quoted = shlex.quote(url)
        lines.append(
            "(GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes -o StrictHostKeyChecking=no'"
            f" timeout {_GIT_PROBE_SECONDS} git ls-remote --exit-code {quoted} HEAD >/dev/null 2>&1"
            f" && echo repo_ok={quoted} || echo repo_fail={quoted}) &"
        )
    if repos:
        lines.append("wait")
    lines += [f"echo {_SENTINEL}=yes", "exit 0", "}"]
    return "\n".join(lines) + "\n"


def _parse(output: str) -> dict:
    facts: dict = {"repo_ok": [], "repo_fail": []}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key in ("repo_ok", "repo_fail"):
            facts[key].append(value.strip())
        else:
            facts[key] = value.strip()
    return facts


def _unreachable(kind: str, error: str) -> dict:
    return {"reachable": "no", "kind": kind, "error": error}


def remote_facts(auth: dict, cfg: dict, *, extra_repos: list[str] = ()) -> dict:
    vps = cfg["vps"]
    repos = [*_enterprise_urls(cfg), *extra_repos]
    cmd, env = build_ssh_cmd(auth, vps.get("port", 22), vps["user"], vps["host"], "bash -s", agent_forward=True)
    timeout = _REMOTE_TIMEOUT + (_GIT_PROBE_SECONDS if repos else 0)
    try:
        result = subprocess.run(
            cmd, input=_remote_script(cfg, repos), capture_output=True, text=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired:
        return _unreachable("timeout", f"no answer after {timeout}s")
    except (subprocess.SubprocessError, OSError) as error:
        return _unreachable("transport", str(error))
    if result.returncode != 0:
        return _unreachable("transport", (result.stderr or "").strip() or f"ssh exited {result.returncode}")
    facts = _parse(result.stdout)
    if facts.get(_SENTINEL) != "yes":
        return _unreachable("truncated", "the VPS answered but the probe ended before it finished")
    return {**facts, "reachable": "yes"}


def _check(name: str, level: str, ok: bool, detail: str, hint: str = "") -> Check:
    return {"name": name, "level": level, "ok": ok, "detail": detail, "hint": "" if ok else hint}


def _mib(size: str) -> float:
    match = re.fullmatch(r"(\d+)\s*([KMG])B", size.upper())
    if not match:
        return 0
    return int(match.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024}[match.group(2)]


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _memory_check(cfg: dict, remote: dict, env: str) -> Check:
    """Warn when the soft limits of every Odoo process plus PostgreSQL's buffers exceed physical RAM.

    The sum is a conservative threshold, not a ceiling: Odoo recycles a
    worker past its soft limit and only kills it at the hard one.
    """
    workers = WORKERS_BY_ENV.get(env, WORKERS_BY_ENV["stage"])
    soft = MEMORY_BY_ENV.get(env, MEMORY_BY_ENV["stage"])["soft"] // _MIB
    if cfg.get("type", "docker") == "docker":
        buffers = _mib(PG_PROFILES.get(cfg.get("pg_profile"), PG_PROFILES["small"])["shared_buffers"])
    else:
        buffers = _mib(DEBIAN_PG_SHARED_BUFFERS)
    processes = workers + CRON_THREADS + 2
    needed = processes * soft + buffers
    have = _int(remote.get("mem_kb")) // 1024
    return _check(
        "memory",
        "warn",
        have >= needed,
        f"the VPS has {have} MiB of RAM; {processes} Odoo processes (master, {workers} workers, "
        f"{CRON_THREADS} cron, gevent) at {soft} MiB soft limit plus {buffers:.0f} MiB of PostgreSQL "
        f"shared_buffers come to {needed:.0f} MiB",
        "Workers and memory limits follow the environment name: stage runs fewer workers with lower limits, "
        "so deploy this one as stage, or use a VPS with more RAM. Swap does not count: this reads physical RAM.",
    )


def _remote_checks(cfg: dict, remote: dict, env: str, local: dict) -> list[Check]:
    docker = cfg.get("type", "docker") == "docker"
    path = _remote_path(cfg)
    user = remote.get("whoami")
    sudo = remote.get("sudo") == "yes"
    checks = []
    if docker:
        ok = remote.get("docker_ok") == "yes" and bool(remote.get("compose"))
        checks.append(
            _check(
                "docker",
                "block",
                ok,
                f"Docker and Compose {remote.get('compose')} usable by {user}"
                if ok
                else f"Docker or Compose not usable by {user}",
                "Install Docker with the compose plugin and add the SSH user to the docker group "
                "(sudo usermod -aG docker $USER), then log in again.",
            )
        )
        writable = remote.get("path_writable") == "yes"
        checks.append(
            _check(
                "remote path",
                "block",
                writable or sudo,
                f"{path} is writable by {user}"
                if writable
                else f"{path} needs sudo, and sudo -n is {'available' if sudo else 'not available'}",
                "Give the SSH user passwordless sudo, or set a remote_path it owns (for example /home/<user>/odoo).",
            )
        )
    else:
        root = remote.get("uid") == "0"
        checks.append(
            _check(
                "sudo",
                "block",
                root or sudo,
                f"{user} is root" if root else f"sudo -n {'is' if sudo else 'is not'} available to {user}",
                "A native deploy runs apt and systemctl through sudo with no terminal: "
                "give the SSH user passwordless sudo (NOPASSWD) or connect as root.",
            )
        )
    if _enterprise_urls(cfg):
        git = remote.get("git") == "yes"
        cloned = ENTERPRISE_REPO in remote.get("repo_ok", [])
        checks.append(
            _check(
                "enterprise repository",
                "block",
                git and cloned,
                "odoo/enterprise is readable from the VPS"
                if git and cloned
                else ("git is not installed on the VPS" if not git else "git ls-remote odoo/enterprise failed"),
                "Install git on the VPS and load a key with access to odoo/enterprise in the local SSH agent "
                "(ssh-add -l), or use enterprise_source: rsync.",
            )
        )
    gitman_urls = local.get("gitman_urls", [])
    if gitman_urls:
        failed = [url for url in gitman_urls if url not in remote.get("repo_ok", [])]
        if remote.get("git") != "yes":
            checks.append(
                _check(
                    "gitman repositories",
                    "warn",
                    False,
                    "git is not installed on the VPS host, so the repositories were not probed",
                    "The build clones them inside the image with its own git, so this does not stop the deploy; "
                    "install git on the host to have them checked first.",
                )
            )
        else:
            checks.append(
                _check(
                    "gitman repositories",
                    "block",
                    not failed,
                    "every gitman repository is readable from the VPS"
                    if not failed
                    else f"git ls-remote failed: {', '.join(failed)}",
                    "Load a key with access to those repositories in the local SSH agent (ssh-add -l): "
                    "the build clones them with your forwarded key.",
                )
            )
    checks.append(_memory_check(cfg, remote, env))
    free = _int(remote.get("free_kb"))
    checks.append(
        _check(
            "disk space",
            "warn",
            free >= _MIN_FREE_KB,
            f"{free // (1024 * 1024)} GiB free on the VPS (5 GiB recommended)",
            "An Odoo image, its database and Enterprise take several GiB: free some space on the VPS.",
        )
    )
    if docker and cfg.get("domain"):
        running = remote.get("traefik_running") == "yes"
        attached = remote.get("traefik_attached") == "yes"
        if not running:
            detail = "no Traefik container found on the VPS"
        elif not attached:
            detail = f"a Traefik runs on the VPS but is not attached to the {_TRAEFIK_NETWORK} network"
        else:
            detail = "Traefik is running on the VPS"
        checks.append(
            _check(
                "traefik",
                "warn",
                running and attached,
                detail,
                f"Without Traefik on {_TRAEFIK_NETWORK}, {cfg['domain']} will not answer. "
                f"Run: rkd instance traefik --env {env}",
            )
        )
    return checks


_PROXY_ERROR_CODES = {"404", "502", "503", "504"}


def public_url_check(domain: str, output: str, *, env: str) -> Check:
    """
    Classify what the VPS saw when it requested the public URL of the deploy.

    `output` holds `http=<code>:<curl exit>` and `https=<code>:<curl exit>`.
    Two requests because one cannot tell apart "nothing listens" from
    "Traefik routes but the certificate is not there yet": a 3xx on plain
    HTTP proves the router matched before Let's Encrypt has issued anything,
    while curl exits 60/35 on https for the same reason.
    """
    seen = {}
    for line in output.splitlines():
        scheme, _, value = line.partition("=")
        code, _, rc = value.partition(":")
        if scheme in ("http", "https"):
            seen[scheme] = (code.strip(), rc.strip())
    http_code, _ = seen.get("http", ("000", ""))
    https_code, https_rc = seen.get("https", ("000", ""))
    fix = f"Run: rkd instance traefik --env {env}"

    if https_rc == "0" and https_code not in _PROXY_ERROR_CODES and https_code != "000":
        return _check("public url", "warn", True, f"https://{domain} answers ({https_code})")
    if https_code in _PROXY_ERROR_CODES or http_code in _PROXY_ERROR_CODES:
        return _check(
            "public url",
            "warn",
            False,
            f"Traefik answers {https_code if https_code in _PROXY_ERROR_CODES else http_code} but does not reach Odoo",
            f"The labels or the {_TRAEFIK_NETWORK} network do not match this Traefik: check its entrypoints "
            f"and certificate resolver names, or {fix.lower()}",
        )
    if http_code.startswith("3"):
        return _check(
            "public url",
            "warn",
            False,
            f"Traefik routes {domain} but the certificate is not available yet",
            "Let's Encrypt can take a few minutes, and needs DNS pointing at this VPS and ports 80 and 443 open.",
        )
    return _check(
        "public url",
        "warn",
        False,
        f"nothing answers for {domain} on the VPS",
        f"No proxy is routing this domain. {fix}",
    )


_CONNECTION_HINTS = {
    "timeout": "The VPS did not answer in time: check that it is up and that its SSH port is open "
    "(firewall, security group). This is a network problem, not necessarily your configuration.",
    "truncated": "The connection dropped while the VPS was answering: run the command again.",
}
_CONNECTION_DEFAULT_HINT = "ssh failed: check host, port, user and key (or password) in .rkd/instance.yaml."


def _gitman_checks(cfg: dict, local: dict) -> list[Check]:
    if cfg.get("type", "docker") != "docker":
        return [
            _check(
                "gitman",
                "warn",
                False,
                "use_gitman is set on a native deploy: the repositories in the gitman file are not installed",
                "The native deploy has no build step to run gitman in. Put those addons under addons/, "
                "or deploy with type: docker.",
            )
        ]
    name = gitman_file(cfg)
    found = bool(local.get("gitman_file"))
    checks = [
        _check(
            "gitman file",
            "block",
            found,
            f"{name} found in the project" if found else f"{name} is missing from the project",
            "The Dockerfile copies it into the image: create it (rkd init writes gitman.yaml) "
            "or fix gitman_config in .rkd/instance.yaml.",
        )
    ]
    if found:
        script = bool(local.get("install_script"))
        needed = bool(local.get("gitman_scripts"))
        checks.append(
            _check(
                "gitman dependency script",
                "block" if needed else "warn",
                script,
                f"{INSTALL_SCRIPT} found in the project"
                if script
                else (
                    f"{INSTALL_SCRIPT} is missing and the gitman file declares scripts that run it"
                    if needed
                    else f"{INSTALL_SCRIPT} is missing from the project"
                ),
                "A failed script aborts gitman install (the build passes --force only for stage), and the "
                "Python dependencies of the cloned repositories are not installed either way. "
                f"Restore {INSTALL_SCRIPT} in the project root.",
            )
        )
    return checks


def _local_checks(cfg: dict, local: dict) -> list[Check]:
    invalid = local.get("invalid_identifiers", [])
    checks = [
        _check(
            "database identifiers",
            "block",
            not invalid,
            "db_user and db_name are valid" if not invalid else f"not a valid PostgreSQL identifier: {', '.join(invalid)}",
            "Up to 63 letters, digits and underscores, not starting with a digit.",
        )
    ]
    if local.get("key_file") is not None:
        checks.append(
            _check(
                "ssh key file",
                "warn",
                bool(local["key_file"]),
                "the SSH key file exists" if local["key_file"] else "the SSH key file does not exist",
                "ssh will fall back to the agent and the default keys. Check ssh_key in .rkd/instance.yaml.",
            )
        )
    if cfg.get("use_enterprise") and cfg.get("enterprise_source", "rsync") == "rsync":
        found = bool(local.get("enterprise_dir"))
        checks.append(
            _check(
                "enterprise directory",
                "block",
                found,
                "enterprise/ found in the project" if found else "enterprise/ is missing from the project",
                "Copy odoo/enterprise into enterprise/ or set enterprise_source: clone.",
            )
        )
    if cfg.get("use_gitman"):
        checks += _gitman_checks(cfg, local)
    # Only when the build will actually pass `--ssh default`: that flag is what
    # aborts without a socket, and it is now conditional on the same predicate.
    if cfg.get("type", "docker") == "docker" and local.get("build_needs_ssh"):
        sock = bool(local.get("ssh_auth_sock"))
        checks.append(
            _check(
                "ssh agent socket",
                "block",
                sock,
                "SSH_AUTH_SOCK is set" if sock else "SSH_AUTH_SOCK is not set",
                "docker compose build --ssh needs an agent socket. An agent with no keys loaded is enough: "
                "run eval $(ssh-agent) in this shell.",
            )
        )
    if _enterprise_urls(cfg):
        agent = bool(local.get("agent_has_keys"))
        checks.append(
            _check(
                "ssh agent",
                "warn",
                agent,
                "the SSH agent holds keys" if agent else "the SSH agent holds no keys",
                "The VPS clones odoo/enterprise with your forwarded key: run ssh-add and check ssh-add -l.",
            )
        )
    return checks


def evaluate(cfg: dict, local: dict, remote: dict | None = None, *, env: str = "stage") -> list[Check]:
    """Every check; with no `remote`, only the ones that need no connection."""
    checks = _local_checks(cfg, local)
    if remote is None:
        return checks
    reachable = remote.get("reachable") == "yes"
    checks.append(
        _check(
            "vps connection",
            "block",
            reachable,
            "the VPS answers" if reachable else f"{remote.get('error', 'no details')}",
            _CONNECTION_HINTS.get(remote.get("kind"), _CONNECTION_DEFAULT_HINT),
        )
    )
    if reachable:
        checks += _remote_checks(cfg, remote, env, local)
    return checks


def blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c["level"] == "block" and not c["ok"]]


def run(project_root: Path, auth: dict, cfg: dict, *, env: str) -> list[Check]:
    """Local checks first: a failure already known does not wait for a 90 s SSH session."""
    local = local_facts(project_root, cfg)
    checks = evaluate(cfg, local, env=env)
    if blocking(checks):
        return checks
    return evaluate(cfg, local, remote_facts(auth, cfg, extra_repos=local["gitman_urls"]), env=env)
