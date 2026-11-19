import posixpath
import re
from pathlib import Path

import yaml

from rocketdoo.core.odoo_conf import read_addons_path, write_addons_path


def generate_gitman_yaml(sources=None, output_path=None):
    """
    Generates a gitman.yaml file with correct structure and indentation.
    Each repository has its own dependency installation script.
    """
    if output_path is None:
        output_path = Path.cwd() / "gitman.yaml"

    if sources is None:
        sources = []

    # Add the script to each individual repository
    sources_with_scripts = []
    for source in sources:
        repo_config = {
            "repo": source["repo"],
            "name": source["name"],
            "rev": source["rev"],
            "type": source["type"],
            "scripts": ["sh /usr/lib/python3/dist-packages/odoo/install_dependencies.sh"],
        }
        sources_with_scripts.append(repo_config)

    config = {"location": "external_addons", "sources": sources_with_scripts, "default_group": "", "groups": []}

    yaml_text = yaml.safe_dump(config, sort_keys=False, default_flow_style=False, indent=2)

    # 🔧 Manual fix: ensure correct indentation for `scripts`
    yaml_text = yaml_text.replace("scripts:\n  - ", "scripts:\n    - ")

    with open(output_path, "w") as f:
        f.write(yaml_text)

    return output_path


def update_odoo_conf_with_gitman(odoo_conf_path, gitman_sources):
    """
    Updates the addons_path in odoo.conf with the paths from Gitman repos.

    Args:
        odoo_conf_path: Path to the odoo.conf file
        gitman_sources: List of dictionaries with repo configuration
    """
    if not odoo_conf_path.exists():
        raise FileNotFoundError(f"File not found: {odoo_conf_path}")

    # Generate external_addons paths
    new_paths = [f"/usr/lib/python3/dist-packages/odoo/external_addons/{source['name']}" for source in gitman_sources]

    current_paths = read_addons_path(odoo_conf_path)
    write_addons_path(odoo_conf_path, current_paths + [path for path in new_paths if path not in current_paths])


GITMAN_DEFAULT_LOCATION = "gitman_sources"


def _read_gitman(config_path):
    try:
        data = yaml.safe_load(Path(config_path).read_text())
    except (OSError, ValueError, yaml.YAMLError):
        return GITMAN_DEFAULT_LOCATION, []
    if not isinstance(data, dict):
        return GITMAN_DEFAULT_LOCATION, []
    sources = data.get("sources")
    sources = [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else []
    return data.get("location") or GITMAN_DEFAULT_LOCATION, sources


def gitman_repos(config_path):
    """Repository URLs declared by a gitman file; [] if it is unreadable."""
    return [s["repo"] for s in _read_gitman(config_path)[1] if isinstance(s.get("repo"), str) and s["repo"]]


def gitman_has_scripts(config_path):
    """True when any source declares `scripts:`."""
    return any(s.get("scripts") for s in _read_gitman(config_path)[1])


_SSH_HOST = re.compile(r"^(?:ssh://(?:[^@/]+@)?|[^@/:]+@)([A-Za-z0-9.-]+)[:/]")


def gitman_ssh_hosts(config_path):
    """Hosts reached over SSH, whose keys the build must know in advance."""
    hosts = (_SSH_HOST.match(url) for url in gitman_repos(config_path))
    return list(dict.fromkeys(m.group(1) for m in hosts if m))


def _gitman_name(repo):
    return repo.split("/")[-1].split(".")[0]


def gitman_module_paths(config_path, root):
    """
    Addons paths that `gitman install` leaves under `root`, one per source.

    `location` falls back to "gitman_sources", the default of gitman 1.4.0
    (the version the instance Dockerfile pins; Config.__init__), and not to
    the "external_addons" that rkd writes: the file belongs to the user. A
    source without `name` is named as gitman does (Source._infer_name: what
    precedes the first dot of the last path segment), which is not what
    extract_repo_name_from_url returns for `addons.v2.git`. A name with a
    comma or a line break is dropped: addons_path is one line of
    comma-separated entries. A missing, unreadable or source-less file gives [].
    """
    location, sources = _read_gitman(config_path)
    paths = []
    for source in sources:
        repo = source.get("repo")
        name = source.get("name") or (_gitman_name(repo) if isinstance(repo, str) else "")
        if not isinstance(name, str) or not name or any(c in name for c in ",\r\n"):
            continue
        paths.append(posixpath.normpath(posixpath.join(root, str(location), name)))
    return paths


def extract_repo_name_from_url(url):
    """
    Extracts the repository name from a Git URL.

    Examples:
        https://github.com/ingadhoc/odoo-argentina.git -> odoo-argentina
        https://gitlab.com/user/my-repo -> my-repo
    """
    # Get the last part of the URL
    url = url.rstrip("/")
    repo_name_with_ext = url.split("/")[-1]

    # Remove .git extension if it exists
    if repo_name_with_ext.endswith(".git"):
        repo_name = repo_name_with_ext[:-4]
    else:
        repo_name = repo_name_with_ext

    # Return the name as-is (keep hyphens)
    return repo_name


def detect_repo_type(url):
    """
    Detects the repository type based on the URL.
    Returns 'git' by default.
    """
    url_lower = url.lower()

    if ".git" in url_lower or "github.com" in url_lower or "gitlab.com" in url_lower or "bitbucket.org" in url_lower:
        return "git"

    # You can add more types if needed (svn, hg, etc.)
    return "git"
