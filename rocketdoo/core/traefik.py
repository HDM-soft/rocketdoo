"""Traefik reverse proxy: shared service, project override and status.

Shared by `rkd traefik on/off/status` and the GUI endpoints so the two
cannot drift (moved out of `traefik_cli.py` in #143 T9, closing D1-D4 of
`proposal.md`: the GUI used to reimplement the CLI's steps and skip the two
that actually start Traefik and restart the project). Presentation-free per
the core/ contract (RF1.2): callers render their own progress and error
messages from the returned report or from TraefikError.
"""

import subprocess
from pathlib import Path

import yaml

from rocketdoo.core.compose import COMPOSE_NAMES, compose_path, run_compose
from rocketdoo.core.service import ProgressCallback, ServiceError, reporter

NETWORK = "traefik-public"
OVERRIDE_FILE = "docker-compose.override.yml"
CONFIG_FILE = ".rkd/traefik.yaml"
_DEFAULT_TRAEFIK_DIR = "./traefik"


class TraefikError(ServiceError):
    """Traefik cannot be toggled in this project."""


# ─── helpers ─────────────────────────────────────────────────────────────────


def _display(path: Path, root: Path) -> str:
    """Path relative to `root` for a progress message, or absolute if outside it.

    Case limite: `traefik_dir` may point outside the project (RF6 case 4).
    Progress messages must never crash over that; only the final report
    keeps the absolute path so the CLI's summary can do its own fallback.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def project_name(project_root: Path | str | None = None) -> str:
    root = Path(project_root) if project_root else Path.cwd()
    for name in COMPOSE_NAMES:
        p = root / name
        if p.exists():
            try:
                data = yaml.safe_load(p.read_text())
                if data and data.get("name"):
                    return data["name"]
            except Exception:
                pass
    return root.name


def load_config(project_root: Path | str | None = None) -> dict:
    root = Path(project_root) if project_root else Path.cwd()
    p = root / CONFIG_FILE
    if p.exists():
        try:
            return yaml.safe_load(p.read_text()) or {}
        except Exception:
            pass
    return {}


def save_config(project_root: Path | str, config: dict) -> None:
    root = Path(project_root)
    p = root / CONFIG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def network_exists() -> bool:
    r = subprocess.run(["docker", "network", "inspect", NETWORK], capture_output=True)
    return r.returncode == 0


def _create_network() -> None:
    subprocess.run(["docker", "network", "create", NETWORK], capture_output=True)


def override_exists(project_root: Path | str | None = None) -> bool:
    root = Path(project_root) if project_root else Path.cwd()
    return (root / OVERRIDE_FILE).exists()


def traefik_running() -> bool:
    r = subprocess.run(["docker", "ps", "-q", "--filter", "name=traefik"], capture_output=True, text=True)
    return bool(r.stdout.strip())


# ─── content generators ───────────────────────────────────────────────────────


def render_compose(mode: str) -> str:
    https_port = '\n      - "443:443"' if mode == "production" else ""
    acme_vol = "\n      - ./certs/acme.json:/certs/acme.json" if mode == "production" else ""
    return f"""\
name: traefik

services:
  traefik:
    image: traefik:v2.11
    container_name: traefik
    restart: always
    ports:
      - "80:80"{https_port}
    volumes:
      - ./traefik.yml:/etc/traefik/traefik.yml
      - /var/run/docker.sock:/var/run/docker.sock:ro{acme_vol}
    networks:
      - traefik-public

networks:
  traefik-public:
    external: true
"""


def render_traefik_yml(mode: str, email: str = "") -> str:
    if mode == "local":
        return """\
entryPoints:
  web:
    address: ":80"

providers:
  docker:
    exposedByDefault: false
    network: traefik-public

api:
  dashboard: false
"""
    return f"""\
entryPoints:
  web:
    address: ":80"
  websecure:
    address: ":443"

certificatesResolvers:
  letsencrypt:
    acme:
      email: {email}
      storage: /certs/acme.json
      httpChallenge:
        entryPoint: web

providers:
  docker:
    exposedByDefault: false
    network: traefik-public

api:
  dashboard: false
"""


def render_override(project: str, domain: str, mode: str) -> str:
    slug = project.replace("-", "_").replace(" ", "_")

    if mode == "local":
        labels = (
            f'      - "traefik.enable=true"\n'
            f'      - "traefik.docker.network=traefik-public"\n'
            f'      - "traefik.http.routers.{slug}.rule=Host(`{domain}`)"\n'
            f'      - "traefik.http.routers.{slug}.entrypoints=web"\n'
            f'      - "traefik.http.services.{slug}-svc.loadbalancer.server.port=8069"\n'
            f'      - "traefik.http.routers.{slug}-lp.rule=Host(`{domain}`) && (PathPrefix(`/longpolling`) || PathPrefix(`/websocket`))"\n'
            f'      - "traefik.http.routers.{slug}-lp.entrypoints=web"\n'
            f'      - "traefik.http.services.{slug}-lp-svc.loadbalancer.server.port=8072"'
        )
    else:
        labels = (
            f'      - "traefik.enable=true"\n'
            f'      - "traefik.docker.network=traefik-public"\n'
            f'      - "traefik.http.routers.{slug}-http.rule=Host(`{domain}`)"\n'
            f'      - "traefik.http.routers.{slug}-http.entrypoints=web"\n'
            f'      - "traefik.http.routers.{slug}-http.middlewares=redirect-https"\n'
            f'      - "traefik.http.middlewares.redirect-https.redirectscheme.scheme=https"\n'
            f'      - "traefik.http.routers.{slug}.rule=Host(`{domain}`)"\n'
            f'      - "traefik.http.routers.{slug}.entrypoints=websecure"\n'
            f'      - "traefik.http.routers.{slug}.tls.certresolver=letsencrypt"\n'
            f'      - "traefik.http.routers.{slug}.service={slug}-svc"\n'
            f'      - "traefik.http.services.{slug}-svc.loadbalancer.server.port=8069"\n'
            f'      - "traefik.http.routers.{slug}-lp.rule=Host(`{domain}`) && (PathPrefix(`/longpolling`) || PathPrefix(`/websocket`))"\n'
            f'      - "traefik.http.routers.{slug}-lp.entrypoints=websecure"\n'
            f'      - "traefik.http.routers.{slug}-lp.tls.certresolver=letsencrypt"\n'
            f'      - "traefik.http.routers.{slug}-lp.service={slug}-lp-svc"\n'
            f'      - "traefik.http.services.{slug}-lp-svc.loadbalancer.server.port=8072"'
        )

    return f"""\
# Generated by rkd traefik on — disable with: rkd traefik off
services:
  web:
    networks:
      - traefik-public
      - traefik-internal
    labels:
{labels}

  db:
    networks:
      - traefik-internal

networks:
  traefik-public:
    external: true
  traefik-internal:
    driver: bridge
"""


# ─── read functions ──────────────────────────────────────────────────────────


def status(project_root: Path | str | None = None) -> dict:
    """What both `rkd traefik status` and `GET /api/traefik/status` need.

    `enabled` is derived from `override_exists()`, not from a config key: the
    CLI never wrote one (D4 of proposal.md), so the GUI used to report a
    project the CLI had just enabled as disabled.
    """
    root = Path(project_root) if project_root else Path.cwd()
    config = load_config(root)
    enabled = override_exists(root)
    # Order matters: it is the exact sequence `rkd traefik status` issues today.
    running = traefik_running()
    network = network_exists()
    return {
        "enabled": enabled,
        "mode": config.get("mode"),
        "domain": config.get("domain"),
        "override_exists": enabled,
        "network_exists": network,
        "traefik_running": running,
        "traefik_dir": config.get("traefik_dir"),
        "configured": bool(config),
    }


# ─── action functions ────────────────────────────────────────────────────────


def enable(
    project_root: Path | str,
    *,
    mode: str,
    domain: str,
    email: str = "",
    traefik_dir: Path | str | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Generate the Traefik service, wire the project to it, and start both.

    Raises TraefikError when the project has no compose file at all or is
    already connected to Traefik (an override already exists): both make the
    operation impossible, not just partially failed (RF1.4). A failure to
    actually start Traefik or restart the project is reported through
    `on_progress` and the `traefik_started`/`project_restarted` fields
    instead, since the files are already in place either way.
    """
    root = Path(project_root)
    report = reporter(on_progress)

    if not compose_path(root):
        raise TraefikError("No docker-compose.yaml found.", "Run rkd init first.")

    if override_exists(root):
        raise TraefikError(
            "Traefik is already enabled for this project.",
            "Run rkd traefik off first to reconfigure.",
        )

    project = project_name(root)
    # .resolve() alone would anchor a relative path to the process cwd, which
    # is the very assumption this module takes project_root to replace (RF1.1).
    traefik_path = (root / (traefik_dir or _DEFAULT_TRAEFIK_DIR)).resolve()
    traefik_path.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    skipped: list[str] = []

    compose_file = traefik_path / "docker-compose.yml"
    if compose_file.exists():
        skipped.append(str(compose_file))
        report(f"{_display(compose_file, root)} already exists, skipping", "info")
    else:
        compose_file.write_text(render_compose(mode))
        files.append(str(compose_file))
        report(f"{_display(compose_file, root)} generated", "ok")

    traefik_yml = traefik_path / "traefik.yml"
    if traefik_yml.exists():
        skipped.append(str(traefik_yml))
        report(f"{_display(traefik_yml, root)} already exists, skipping", "info")
    else:
        traefik_yml.write_text(render_traefik_yml(mode, email))
        files.append(str(traefik_yml))
        report(f"{_display(traefik_yml, root)} generated", "ok")

    if mode == "production":
        certs_dir = traefik_path / "certs"
        certs_dir.mkdir(exist_ok=True)
        acme = certs_dir / "acme.json"
        if not acme.exists():
            acme.touch()
            acme.chmod(0o600)
            files.append(str(acme))
            report(f"{_display(acme, root)} created (chmod 600)", "ok")

    network_created = False
    if not network_exists():
        report(f'Creating docker network "{NETWORK}"...', "info")
        _create_network()
        network_created = True
        report(f'Network "{NETWORK}" created', "ok")
    else:
        report(f'Network "{NETWORK}" already exists', "info")

    override_file = root / OVERRIDE_FILE
    override_file.write_text(render_override(project, domain, mode))
    files.append(str(override_file))
    report(f"{OVERRIDE_FILE} generated", "ok")

    save_config(
        root,
        {
            "mode": mode,
            "domain": domain,
            "email": email,
            "traefik_dir": str(traefik_path),
        },
    )
    files.append(str(root / CONFIG_FILE))
    report(f"Config saved to {CONFIG_FILE}", "ok")

    report("Starting Traefik...", "info")
    traefik_started = run_compose("up", "-d", cwd=traefik_path) == 0
    report(
        "Traefik started" if traefik_started else "Could not start Traefik (is Docker running?)",
        "ok" if traefik_started else "warn",
    )

    report("Restarting Odoo with Traefik integration...", "info")
    project_restarted = run_compose("up", "-d", cwd=root) == 0
    report(
        "Project restarted" if project_restarted else "Could not restart the project (is Docker running?)",
        "ok" if project_restarted else "warn",
    )

    return {
        "mode": mode,
        "domain": domain,
        "email": email,
        "traefik_dir": str(traefik_path),
        "project": project,
        "files": files,
        "skipped": skipped,
        "network_created": network_created,
        "traefik_started": traefik_started,
        "project_restarted": project_restarted,
    }


def disable(project_root: Path | str, *, on_progress: ProgressCallback | None = None) -> bool:
    """Remove the Traefik override and bring the project back on direct ports.

    Returns False when the project was not connected to Traefik in the first
    place, so the caller can tell "nothing to do" from "done".
    """
    root = Path(project_root)
    report = reporter(on_progress)

    override = root / OVERRIDE_FILE
    if not override.exists():
        return False

    report("Restarting project without Traefik...", "info")
    override.unlink()
    report(f"Removed {OVERRIDE_FILE}", "ok")

    restarted = run_compose("up", "-d", cwd=root) == 0
    report(
        "Project restarted" if restarted else "Could not restart the project (is Docker running?)",
        "ok" if restarted else "warn",
    )

    return True
