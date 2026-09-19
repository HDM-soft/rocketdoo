"""
RocketDoo CI - regenerate the gitignored files a Docker build needs, and
generate the GitHub Actions workflow that exercises them.
"""

import os
import subprocess
import sys
from pathlib import Path

import click
import yaml
from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from rocketdoo import __version__
from rocketdoo.core.addons_path import CONTAINER_ADDONS_ROOT, discover, ensure_addons_path
from rocketdoo.core.edition_setup import add_enterprise_to_odoo_conf
from rocketdoo.core.gitman_config import update_odoo_conf_with_gitman
from rocketdoo.core.models.profiles import RELEASES
from rocketdoo.core.module_scanner import ModuleScanner
from rocketdoo.init_project import CONFIG_TEMPLATE_DIR, render_template
from rocketdoo.project_info import get_project_info, project_exists

console = Console()

PG_PASS_TEMPLATE = Path(__file__).resolve().parent / "templates" / "odoo_pg_pass"
WORKFLOW_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "ci"
WORKFLOW_RELATIVE_PATH = Path(".github") / "workflows" / "rkd-ci.yml"

INSTALL_TRIGGERS = ("pull_request", "push", "manual", "never")
DEFAULT_INSTALL_TRIGGER = "pull_request"


@click.group(name="ci")
def ci():
    """CI/CD helpers for a generated Rocketdoo project."""


@ci.command()
@click.option(
    "--admin-passwd",
    default=None,
    help="admin_passwd for a newly created config/odoo.conf (default: admin)",
)
def prepare(admin_passwd):
    """Recreate the gitignored files a Docker build needs, without overwriting them."""
    if not project_exists():
        console.print("[red]No Rocketdoo project detected in this directory.[/red]")
        sys.exit(1)

    project_root = Path.cwd()
    created = []
    kept = []

    pg_pass = project_root / "odoo_pg_pass"
    if pg_pass.exists():
        kept.append("odoo_pg_pass")
    else:
        pg_pass.write_text(PG_PASS_TEMPLATE.read_text())
        created.append("odoo_pg_pass")

    odoo_conf = project_root / "config" / "odoo.conf"
    if odoo_conf.exists():
        kept.append("config/odoo.conf")
    else:
        info = get_project_info()
        context = {**info, "admin_passwd": admin_passwd or "admin"}
        render_template(CONFIG_TEMPLATE_DIR, "odoo.conf.jinja", os.path.join("config", "odoo.conf"), **context)
        created.append("config/odoo.conf")

        if info["odoo_edition"] == "Enterprise":
            add_enterprise_to_odoo_conf(odoo_conf)

        if info["third_party_repos"]:
            update_odoo_conf_with_gitman(odoo_conf, info["third_party_repos"])

    action, changes = ensure_addons_path(project_root)

    if created:
        console.print(f"[green]Created:[/green] {', '.join(created)}")
    if "config/odoo.conf" in created and not admin_passwd:
        console.print("[yellow]admin_passwd set to the default; pass --admin-passwd to change it.[/yellow]")
    if kept:
        console.print(f"[dim]Already present, kept as-is:[/dim] {', '.join(kept)}")
    if action == "updated":
        console.print(f"[green]addons_path updated:[/green] {', '.join(changes)}")


@ci.command()
def modules():
    """Print the installable modules Odoo can reach, comma-separated, for `odoo -i`."""
    project_root = Path.cwd()
    addons_dir = project_root / "addons"
    if not addons_dir.is_dir():
        return

    reachable = set(discover(project_root))

    names = set()
    for module in ModuleScanner(addons_dir).get_installable_modules():
        parent = module.relative_path.parent
        container_dir = CONTAINER_ADDONS_ROOT
        if str(parent) != ".":
            container_dir = f"{CONTAINER_ADDONS_ROOT}/{parent.as_posix()}"
        if container_dir in reachable and module.name.isidentifier():
            names.add(module.name)

    if names:
        click.echo(",".join(sorted(names)))


def _default_branch(project_root):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return "main"
    return result.stdout.strip() or "main"


def _rkd_spec(version):
    major, _, rest = version.partition(".")
    minor = rest.split(".")[0]
    if not major.isdigit() or not minor.isdigit():
        return ""
    return f"~={major}.{minor}"


def _ruff_target(odoo_version):
    release = RELEASES.get(odoo_version)
    if release is None:
        return "py310"
    return "py" + release.python_version.replace(".", "")


def _unsupported_reasons(info):
    reasons = []
    if info["odoo_edition"] == "Enterprise":
        reasons.append(
            "Enterprise addons are not public; the install job would need your "
            "subscription credentials as a repository secret."
        )
    if info["use_private_repos"]:
        reasons.append("The Dockerfile copies a private SSH key into the build context (.ssh/), which CI runners do not have.")
    return reasons


def _private_gitman_sources(project_root):
    """Gitman sources the runner cannot clone: SSH URLs need a key it lacks.

    Only the obvious spellings are matched; a private HTTPS repo is
    indistinguishable from a public one without asking the remote.
    """
    gitman = project_root / "gitman.yaml"
    if not gitman.exists():
        return []

    try:
        config = yaml.safe_load(gitman.read_text()) or {}
    except yaml.YAMLError:
        return []

    return [
        source.get("repo", "")
        for source in config.get("sources") or []
        if isinstance(source, dict) and source.get("repo", "").startswith(("git@", "ssh://"))
    ]


def _ci_context(project_root, install_trigger):
    info = get_project_info()
    reasons = _unsupported_reasons(info)
    return {
        "project_name": info["project_name"],
        "odoo_version": info["odoo_version"],
        "odoo_port": info["odoo_port"],
        "default_branch": _default_branch(project_root),
        "rkd_spec": _rkd_spec(__version__),
        "ruff_target": _ruff_target(info["odoo_version"]),
        "install_supported": not reasons,
        "install_trigger": install_trigger,
        "unsupported_reasons": reasons,
    }


def _render_workflow(context):
    env = Environment(loader=FileSystemLoader(WORKFLOW_TEMPLATE_DIR))
    return env.get_template("workflow.yaml.jinja").render(**context)


def _stdin_is_interactive():
    return sys.stdin.isatty()


@ci.command()
@click.option(
    "--install-trigger",
    "install_trigger",
    type=click.Choice(INSTALL_TRIGGERS),
    default=None,
    help="When the install job runs (default: asked interactively, 'pull_request' without a terminal).",
)
@click.option("--force", is_flag=True, help="Overwrite an existing workflow that differs from the current render.")
@click.option("-y", "--yes", "yes", is_flag=True, help="Skip the interactive install-trigger prompt.")
def init(install_trigger, force, yes):
    """Generate .github/workflows/rkd-ci.yml for this project."""
    if not project_exists():
        console.print("[red]No Rocketdoo project detected in this directory.[/red]")
        sys.exit(1)

    if install_trigger is None:
        if not yes and _stdin_is_interactive():
            install_trigger = click.prompt(
                "When should the install job run against a real Odoo?",
                type=click.Choice(INSTALL_TRIGGERS),
                default=DEFAULT_INSTALL_TRIGGER,
            )
        else:
            install_trigger = DEFAULT_INSTALL_TRIGGER

    project_root = Path.cwd()
    context = _ci_context(project_root, install_trigger)
    content = _render_workflow(context)
    output_path = project_root / WORKFLOW_RELATIVE_PATH

    if output_path.exists():
        if output_path.read_text() == content:
            console.print(f"[dim]{WORKFLOW_RELATIVE_PATH} is already up to date.[/dim]")
            return
        if not force:
            console.print(
                f"[yellow]{WORKFLOW_RELATIVE_PATH} already exists and differs from what `rkd ci init` "
                "would generate now (either you edited it by hand, or your project config or the "
                "Rocketdoo version changed since it was generated). Not overwriting it; run "
                "`rkd ci init --force` to regenerate it.[/yellow]"
            )
            return
        output_path.write_text(content)
        console.print(f"[green]Overwritten:[/green] {WORKFLOW_RELATIVE_PATH}")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        console.print(f"[green]Created:[/green] {WORKFLOW_RELATIVE_PATH}")

    if context["unsupported_reasons"]:
        console.print("[yellow]The install job was not generated:[/yellow]")
        for reason in context["unsupported_reasons"]:
            console.print(f"  - {reason}")
    private_sources = _private_gitman_sources(project_root)
    if private_sources:
        console.print("[yellow]gitman.yaml clones over SSH; the runner has no key for:[/yellow]")
        for repo in private_sources:
            console.print(f"  [yellow]- {repo}[/yellow]")
        console.print("[yellow]The install job will fail there unless you supply a deploy key.[/yellow]")
    console.print("[dim]The lint job runs ruff with its default rules over addons/.[/dim]")
