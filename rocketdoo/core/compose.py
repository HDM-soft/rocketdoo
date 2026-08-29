"""Shared `docker compose` helpers.

Locating the compose file, invoking compose and reading `compose ps` were
reimplemented in each command module and again in three GUI endpoints, each
with slightly different file-name lists and error handling. They live here so
`rkd mail`, `rkd traefik` and the GUI agree on what "this project" means.
"""

import json
import subprocess
from pathlib import Path

# Ordered by precedence, matching what Docker Compose itself looks for.
COMPOSE_NAMES = (
    "docker-compose.yaml",
    "docker-compose.yml",
    "compose.yaml",
    "compose.yml",
)


def compose_path(base: Path | None = None) -> Path | None:
    """Return the project's compose file, or None if there is none."""
    root = Path(base) if base else Path.cwd()
    for name in COMPOSE_NAMES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def run_compose(*args: str, cwd: Path | None = None) -> int:
    """Run `docker compose <args>` and return its exit code."""
    result = subprocess.run(["docker", "compose", *args], cwd=str(cwd) if cwd else Path.cwd())
    return result.returncode


def compose_ps(*args: str, cwd: Path | None = None, timeout: int = 15) -> list[dict]:
    """Return `docker compose ps --format json` as a list of dicts.

    Any failure — no daemon, no compose file, unparseable output — yields an
    empty list, since most callers treat "cannot tell" the same as "nothing
    running". Use compose_ps_result() when the reason matters.
    """
    return compose_ps_result(*args, cwd=cwd, timeout=timeout)[0]


def compose_ps_result(*args: str, cwd: Path | None = None, timeout: int = 15) -> tuple[list[dict], str]:
    """`compose_ps` plus the reason it came back empty, for callers that surface it.

    Compose has emitted this output as newline-delimited objects and, in some
    versions, as a single JSON array; both are accepted.
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError:
        return [], "docker not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return [], str(exc)

    if result.returncode != 0:
        return [], result.stderr.strip()

    output = result.stdout.strip()
    if not output:
        return [], ""

    if output.startswith("["):
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return [], "unparseable compose output"
        return [item for item in parsed if isinstance(item, dict)], ""

    containers = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            containers.append(item)
    return containers, ""


def is_running(container: dict) -> bool:
    """Whether a `compose ps` entry describes a running container.

    `State` is preferred but Compose sometimes reports it empty and puts the
    information in `Status` ("Up 3 minutes"), so an empty State falls through
    rather than being taken at face value.
    """
    state = str(container.get("State") or container.get("Status") or "").lower()
    return "running" in state or "up" in state


def container_running(service: str, cwd: Path | None = None) -> bool:
    """Whether a compose service currently has a running container."""
    return any(is_running(c) for c in compose_ps(service, cwd=cwd))
