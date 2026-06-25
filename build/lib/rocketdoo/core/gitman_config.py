import yaml
from pathlib import Path


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
            "scripts": ["sh /usr/lib/python3/dist-packages/odoo/install_dependencies.sh"]
        }
        sources_with_scripts.append(repo_config)
    
    config = {
        "location": "external_addons",
        "sources": sources_with_scripts,
        "default_group": "",
        "groups": []
    }

    yaml_text = yaml.safe_dump(config, sort_keys=False, default_flow_style=False, indent=2)

    # 🔧 Manual fix: ensure correct indentation for `scripts`
    yaml_text = yaml_text.replace(
        "scripts:\n  - ",
        "scripts:\n    - "
    )

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
    
    lines = odoo_conf_path.read_text().splitlines()
    
    # Generate external_addons paths
    new_paths = [
        f"/usr/lib/python3/dist-packages/odoo/external_addons/{source['name']}"
        for source in gitman_sources
    ]
    
    # Find and update the addons_path line
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith("addons_path"):
            # Extract current value
            current_value = line.split("=", 1)[1].strip()
            current_paths = [p.strip() for p in current_value.split(",")]
            
            # Add only paths that don't already exist
            for new_path in new_paths:
                if new_path not in current_paths:
                    current_paths.append(new_path)
            
            # Rebuild the line
            lines[i] = f"addons_path = {','.join(current_paths)}"
            updated = True
            break
    
    # If addons_path didn't exist, add it
    if not updated:
        lines.append(f"addons_path = {','.join(new_paths)}")
    
    # Save the file
    odoo_conf_path.write_text("\n".join(lines) + "\n")


def extract_repo_name_from_url(url):
    """
    Extracts the repository name from a Git URL.
    
    Examples:
        https://github.com/ingadhoc/odoo-argentina.git -> odoo-argentina
        https://gitlab.com/user/my-repo -> my-repo
    """
    # Get the last part of the URL
    url = url.rstrip('/')
    repo_name_with_ext = url.split('/')[-1]
    
    # Remove .git extension if it exists
    if repo_name_with_ext.endswith('.git'):
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
    
    if '.git' in url_lower or 'github.com' in url_lower or 'gitlab.com' in url_lower or 'bitbucket.org' in url_lower:
        return 'git'
    
    # You can add more types if needed (svn, hg, etc.)
    return 'git'