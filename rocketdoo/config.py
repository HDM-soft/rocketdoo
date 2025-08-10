import socket
import sys
import os
import subprocess
import re
import yaml
import shutil
import argparse

def is_port_in_use(port):
    """Check if a TCP port is currently in use on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def is_port_used_by_rocketdoo(port):
    """Check if the port is used by any running Docker container related to Rocketdoo."""
    try:
        output = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}} {{.Ports}}']).decode()
        for line in output.strip().splitlines():
            if 'rocketdoo' in line and str(port) in line:
                return True
    except Exception as e:
        print(f"[ERROR] Could not inspect Docker containers: {e}")
    return False

def validate_port_from_int(port, label):
    print(f"[DEBUG] Validating {label} port: {port}")
    
    if not isinstance(port, int):
        try:
            port = int(str(port).strip())
        except ValueError:
            print(f"[ERROR] Invalid {label} port. Please ensure it is a valid number.")
            sys.exit(1)
    if is_port_in_use(port):
        if is_port_used_by_rocketdoo(port):
            print(f"[ERROR] Port {port} is already used by another Rocketdoo project ({label}).")
        else:
            print(f"[ERROR] Port {port} is already in use by another application ({label}).")
        sys.exit(1)
    print(f"[INFO] {label} port {port} is available.")
    
# Validate ports for Odoo, VSCode, and Rocketdoo
odoo_port = os.getenv("COPIER_odoo_port", 8069)
vsc_port = os.getenv("COPIER_vsc_port", 8888)
validate_port_from_int(odoo_port, "Odoo")
validate_port_from_int(vsc_port, "VSCode")

# Extract Odoo version correctly from multiple sources
print(f"[DEBUG] All environment variables starting with COPIER_:")
for key, value in os.environ.items():
    if key.startswith("COPIER_"):
        print(f"  {key} = {value}")

print(f"[DEBUG] Current working directory: {os.getcwd()}")
print(f"[DEBUG] Command line arguments: {sys.argv}")

# First try to get from command line arguments
parser = argparse.ArgumentParser()
parser.add_argument('--odoo-version', help='Odoo version to use')
args, unknown = parser.parse_known_args()

odoo_version_raw = None

# Priority 1: command line arguments
if args.odoo_version:
    odoo_version_raw = args.odoo_version
    print(f"[DEBUG] Found odoo version in command line args: {odoo_version_raw}")

# Priority 2: environment variables
if odoo_version_raw is None:
    possible_env_vars = [
        "COPIER_odoo_version",
        "odoo_version", 
        "ODOO_VERSION"
    ]
    
    for env_var in possible_env_vars:
        odoo_version_raw = os.getenv(env_var)
        if odoo_version_raw:
            print(f"[DEBUG] Found odoo version in {env_var}: {odoo_version_raw}")
            break

# Priority 3: copier configuration files
if odoo_version_raw is None:
    print("[DEBUG] Version not found in environment variables, checking for copier answers file...")
    
    possible_files = [
        ".copier-answers.yml",
        ".copier-answers.yaml", 
        "copier-answers.yml",
        "copier-answers.yaml"
    ]
    
    for answers_file in possible_files:
        if os.path.exists(answers_file):
            try:
                print(f"[DEBUG] Reading {answers_file}...")
                with open(answers_file, 'r') as f:
                    answers_content = yaml.safe_load(f)
                    print(f"[DEBUG] Content of {answers_file}: {answers_content}")
                    if answers_content and 'odoo_version' in answers_content:
                        odoo_version_raw = answers_content['odoo_version']
                        print(f"[DEBUG] Found odoo version in {answers_file}: {odoo_version_raw}")
                        break
            except Exception as e:
                print(f"[DEBUG] Error reading {answers_file}: {e}")

# Last option: ask user directly
if odoo_version_raw is None:
    print("[WARNING] Could not automatically detect Odoo version!")
    odoo_version_raw = input("Please enter the Odoo version you selected (e.g., 15.0, 16.0, 17.0, 18.0): ").strip()

if not odoo_version_raw:
    print("[WARNING] No version provided. Using default 18.0")
    odoo_version = "18.0"
else:
    # Extract only numeric part (e.g., ":18.0" -> "18.0")
    odoo_version = str(odoo_version_raw).lstrip(":")
    
print(f"[DEBUG] Final Odoo version to use: {odoo_version}")

docker_compose_path = "docker-compose.yml"
dockerfile_path = "Dockerfile"

def modify_docker_compose(edition):
    if edition.lower() == 'ee':
        try:
            with open(docker_compose_path, 'r') as file:
                content = file.readlines()
            modified_content = []
            for line in content:
                if line.lstrip().startswith('#- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise'):
                    indentation = len(line) - len(line.lstrip())
                    line = ' ' * indentation + line.lstrip().lstrip('#').lstrip()
                    modified_content.append(line)
                else:
                    modified_content.append(line)
            with open(docker_compose_path, 'w') as file:
                file.writelines(modified_content)
            print("Environment prepared for the Enterprise edition. Be sure to upload your Enterprise folder to the root of the project.")
        except FileNotFoundError:
            print(f"The file {docker_compose_path} was not found.")
        except Exception as e:
            print(f"Error modifying {docker_compose_path}: {e}")

def modify_odoo_conf(edition):
    odoo_conf_path = os.path.join("config", "odoo.conf")
    
    if edition.lower() == 'ee':
        if not os.path.exists(odoo_conf_path):
            print(f"The file {odoo_conf_path} does not exist.")
            return
        with open(odoo_conf_path, 'r') as file:
            content = file.read()
        addons_path = "/usr/lib/python3/dist-packages/odoo/enterprise"
        modified_content = re.sub(r'(addons_path\s*=\s*)(.*)', r'\1\2,{}'.format(addons_path), content)
        with open(odoo_conf_path, 'w') as file:
            file.write(modified_content)
        print("Modified odoo.conf file to include the Enterprise path.")

edition = input("In which Odoo edition will you develop? Community or Enterprise (ce/ee): ").strip().lower()
if edition == 'ee':
    modify_docker_compose(edition)
    modify_odoo_conf(edition)
else:
    print("Community Edition Selected")
    
ssh_lines = [
    "#RUN mkdir -p /root/.ssh\n",
    "#COPY ./.ssh/rsa /root/.ssh/id_rsa\n",
    "#RUN chmod 700 /root/.ssh/id_rsa\n",
    '#RUN echo "StrictHostKeyChecking no" >> /root/.ssh/config\n',
]

def get_input(prompt, required=True):
    """Get user input with validation."""
    value = input(prompt)
    while required and not value:
        value = input("This field cannot be empty " + prompt)
    return value

def manage_ssh(private_repos, dockerfile_path):
    if not private_repos:
        print("No private repositories will be used. SSH keys will not be modified.")
        return
    ssh_folder = os.path.expanduser("~/.ssh")
    build_context_ssh_folder = os.path.join(os.path.dirname(dockerfile_path), ".ssh")
    try:
        if not os.path.exists(ssh_folder):
            print(f"The {ssh_folder} folder was not found. Make sure you have SSH keys configured.")
            return
        ssh_keys = [
            f for f in os.listdir(ssh_folder)
            if os.path.isfile(os.path.join(ssh_folder, f)) and not f.endswith('.pub')
        ]
        if not ssh_keys:
            print(f"No private keys were found in {ssh_folder}.")
            return
        print("The following private keys were found in ~/.ssh:")
        for i, key in enumerate(ssh_keys):
            print(f"{i + 1}. {key}")
        key_index = int(get_input("Select the number of the key you wish to use: ")) - 1
        selected_key = ssh_keys[key_index]
        print(f"You have selected the key: {selected_key}")
        if not os.path.exists(build_context_ssh_folder):
            os.makedirs(build_context_ssh_folder)
        source_key_path = os.path.join(ssh_folder, selected_key)
        dest_key_path = os.path.join(build_context_ssh_folder, selected_key)
        shutil.copy(source_key_path, dest_key_path)
        print(f"Key {selected_key} copied to the construction context: {dest_key_path}")
        with open(dockerfile_path, "r") as file:
            lines = file.readlines()
        with open(dockerfile_path, "w") as file:
            for line in lines:
                if line.startswith("#RUN mkdir -p /root/.ssh"):
                    file.write("RUN mkdir -p /root/.ssh\n")
                elif "#COPY ./.ssh/rsa" in line:
                    file.write(
                        f"COPY ./.ssh/{selected_key} /root/.ssh/{selected_key}\n"
                    )
                elif "#RUN chmod 700 /root/.ssh/id_rsa" in line:
                    file.write(
                        f"RUN chmod 700 /root/.ssh/{selected_key}\n"
                    )
                elif '#RUN echo "StrictHostKeyChecking no"' in line:
                    file.write('RUN echo "StrictHostKeyChecking no" >> /root/.ssh/config\n')
                else:
                    file.write(line)
        print(f"Dockerfile has been updated to use the key {selected_key}.")
    except IndexError:
        print("Invalid selection. Please try again.")
    except Exception as e:
        print(f"Error handling SSH keys: {e}")

def comment_lines():
    """Comment out gitman-related lines in Dockerfile."""
    copy_line = "COPY ./gitman.yml /usr/lib/python3/dist-packages/odoo/\n"
    gitman_line = "RUN gitman install -r /usr/lib/python3/dist-packages/odoo/\n"
    try:
        with open(dockerfile_path, "r") as file:
            lines = file.readlines()
        with open(dockerfile_path, "w") as file:
            for line in lines:
                if line == copy_line or line == gitman_line:
                    file.write(f"# {line}")
                else:
                    file.write(line)
        print("Lines commented correctly in the Dockerfile.")
    except FileNotFoundError:
        print(f"The file {dockerfile_path} was not found.")
    except Exception as e:
        print(f"Unexpected error: {e}")

def get_answer_yes_no(message):
    """Get yes/no answer from user."""
    while True:
        answer = input(message).strip().lower()
        if answer in ["y", "n"]:
            return answer
        else:
            print("Please enter 'y' for yes or 'n' for no.")
    
use_private_repos = input("Do you want to use private repositories (y/n): ").strip().lower()
manage_ssh(use_private_repos == "y", dockerfile_path)

use_gitman = input("Do you want to use third-party repositories (y/n): ").strip().lower()
if use_gitman != "y":
    if os.path.exists("gitman.yml"):
        os.remove("gitman.yml")
    print("Not using third-party repositories. Commenting lines in the Dockerfile...")
    comment_lines()
    sys.exit(0)

config = {
    "location": "external_addons",
    "sources": [],
    "default_group": "",
    "groups": [],
}

def get_input(message):
    """Get user input and return it cleaned."""
    return input(message).strip()

def validate_git_url(url):
    """Validate that the URL is a valid Git repository."""
    if not url:
        return False, "URL cannot be empty"
    
    # Common Git URL patterns
    git_patterns = [
        r'^https://github\.com/.+/.+\.git$',
        r'^https://github\.com/.+/.+$',
        r'^git@github\.com:.+/.+\.git$',
        r'^https://gitlab\.com/.+/.+\.git$',
        r'^https://gitlab\.com/.+/.+$',
        r'^git@gitlab\.com:.+/.+\.git$',
        r'^https://bitbucket\.org/.+/.+\.git$',
        r'^https://bitbucket\.org/.+/.+$',
        r'^git@bitbucket\.org:.+/.+\.git$',
        r'^https://.+/.+/.+\.git$',  # Other Git services
        r'^git@.+:.+/.+\.git$'       # Generic SSH
    ]
    
    for pattern in git_patterns:
        if re.match(pattern, url):
            return True, "Valid Git URL"
    
    return False, "Invalid Git repository URL format"

def extract_repo_name(url):
    """Extract repository name from URL before .git."""
    if not url:
        return ""
    return url.rstrip("/").split("/")[-1].replace(".git", "")

def show_repository_help():
    """Show help about how to find repository URLs."""
    print("\n" + "="*60)
    print("📚 HELP: How to find repository URLs")
    print("="*60)
    print("1. Go to the repository page on GitHub/GitLab/Bitbucket")
    print("2. Click the 'Code' or 'Clone' button")
    print("3. Copy the HTTPS or SSH URL")
    print("\nValid URL formats:")
    print("  ✅ https://github.com/username/repository.git")
    print("  ✅ https://github.com/username/repository")
    print("  ✅ git@github.com:username/repository.git")
    print("  ✅ https://gitlab.com/username/repository.git")
    print("  ✅ https://bitbucket.org/username/repository.git")
    print("\nInvalid examples:")
    print("  ❌ github.com/username/repository (missing https://)")
    print("  ❌ https://github.com/username (missing repository name)")
    print("  ❌ https://github.com (missing username and repository)")
    print("="*60)

def add_repository():
    """Add a repository with improved validation."""
    max_attempts = 3
    attempts = 0
    
    while attempts < max_attempts:
        print(f"\n--- Adding Repository {len(config['sources']) + 1} ---")
        print("💡 Tip: Type 'help' for assistance, or 'cancel' to skip")
        repo_url = get_input("Write or paste the repository URL: ")
        
        # Show help
        if repo_url.lower() in ['help', 'h', '?']:
            show_repository_help()
            continue
            
        # Allow canceling
        if repo_url.lower() in ['cancel', 'c', 'exit', 'quit']:
            print("Repository addition cancelled.")
            return False
            
        # Check if empty
        if not repo_url:
            attempts += 1
            print(f"❌ Error: Repository URL cannot be empty!")
            print(f"💡 Tip: Type 'help' for URL format examples")
            print(f"Attempts remaining: {max_attempts - attempts}")
            if attempts >= max_attempts:
                print("❌ Maximum attempts reached. Skipping repository addition.")
                return False
            continue
        
        # Validate URL format
        is_valid, message = validate_git_url(repo_url)
        if not is_valid:
            attempts += 1
            print(f"❌ Error: {message}")
            print("Examples of valid URLs:")
            print("  - https://github.com/user/repo.git")
            print("  - https://github.com/user/repo")
            print("  - git@github.com:user/repo.git")
            print("💡 Tip: Type 'help' for more information")
            print(f"Attempts remaining: {max_attempts - attempts}")
            if attempts >= max_attempts:
                print("❌ Maximum attempts reached. Skipping repository addition.")
                return False
            continue
        
        # Check for duplicates
        repo_name = extract_repo_name(repo_url)
        existing_names = [source["name"] for source in config["sources"]]
        if repo_name in existing_names:
            print(f"❌ Warning: Repository '{repo_name}' already exists!")
            overwrite = input("Do you want to add it anyway? (y/n): ").strip().lower()
            if overwrite not in ['y', 'yes']:
                print("Repository addition cancelled.")
                return False
        
        # Create repository information
        repo_info = {
            "repo": repo_url,
            "name": repo_name,
            "rev": odoo_version,
            "type": "git",
            "scripts": [
                "sh /usr/lib/python3/dist-packages/odoo/install_dependencies.sh"
            ],
        }
        
        # Confirm before adding
        print(f"\n✅ Repository information:")
        print(f"   URL: {repo_url}")
        print(f"   Name: {repo_name}")
        print(f"   Version: {odoo_version}")
        
        confirm = input("Add this repository? (y/n): ").strip().lower()
        if confirm in ['y', 'yes']:
            config["sources"].append(repo_info)
            print(f"✅ Repository '{repo_name}' added successfully!")
            print(f"[DEBUG] Added repository with rev: {odoo_version}")
            return True
        else:
            print("Repository addition cancelled.")
            return False
    
    return False

def confirm_third_party_usage():
    """Confirm if user really wants to use third-party repositories."""
    while True:
        if not config["sources"]:  # If no repositories have been added
            print("\n❌ No repositories have been added yet.")
            choice = input("Do you want to:\n  1. Add repositories now\n  2. Continue without third-party repositories\n  3. Cancel setup\nChoice (1/2/3): ").strip()
            
            if choice == "1":
                return True  # Continue adding repos
            elif choice == "2":
                return False  # Don't use third-party repos
            elif choice == "3":
                print("Setup cancelled.")
                sys.exit(0)
            else:
                print("Invalid choice. Please enter 1, 2, or 3.")
        else:
            return True  # Already have repos, continue

def generate_gitman_yml_manually(config, filename="gitman.yml"):
    """
    Generate gitman.yml file with specific format to ensure 
    correct indentation in scripts section.
    """
    with open(filename, "w") as file:
        # Write location
        file.write(f"location: {config['location']}\n")
        
        # Write sources
        file.write("sources:\n")
        for source in config['sources']:
            file.write(f"- repo: {source['repo']}\n")
            file.write(f"  name: {source['name']}\n")
            file.write(f"  rev: '{source['rev']}'\n")  # Quotes to ensure string format
            file.write(f"  type: {source['type']}\n")
            file.write("  scripts:\n")
            for script in source['scripts']:
                file.write(f"    - {script}\n")  # Correct indentation: 4 spaces
        
        # Write default_group
        file.write(f"default_group: '{config['default_group']}'\n")
        
        # Write groups
        file.write("groups: ")
        if config['groups']:
            file.write("\n")
            for group in config['groups']:
                file.write(f"- {group}\n")
        else:
            file.write("[]\n")

def modify_odoo_conf():
    """Modify odoo.conf to include third-party repository paths."""
    try:
        odoo_conf_path = os.path.join("config", "odoo.conf")
        if not os.path.exists(odoo_conf_path):
            raise FileNotFoundError(f"The file {odoo_conf_path} does not exist.")
        if not os.access(odoo_conf_path, os.R_OK):
            raise PermissionError(
                f"Cannot read the {odoo_conf_path} file. Check the permissions."
            )
        if not os.access(odoo_conf_path, os.W_OK):
            raise PermissionError(
                f"Cannot write to the {odoo_conf_path} file. Check the permissions."
            )
        print(f"Modifying the {odoo_conf_path} file...")
        with open("gitman.yml", "r") as file:
            gitman_data = yaml.safe_load(file)
        repositories_name = [
            repo["name"] for repo in gitman_data["sources"] if repo["name"]
        ]
        print(f"Extracted repositories: {repositories_name}")
        if not repositories_name:
            print("No repositories were found to add.")
            return
        new_routes = ",".join(
            [
                f"/usr/lib/python3/dist-packages/odoo/external_addons/{name}"
                for name in repositories_name
            ]
        )
        with open(odoo_conf_path, "r") as file:
            lines = file.readlines()
        addons_path_found = False
        for i, line in enumerate(lines):
            if line.startswith("addons_path ="):
                current_line = line.strip().split(" = ")[1]
                existing_routes_lines = current_line.split(",")
                update_routes = existing_routes_lines + [
                    route
                    for route in new_routes.split(",")
                    if route not in existing_routes_lines
                ]
                lines[i] = f"addons_path = {','.join(update_routes)}\n"
                addons_path_found = True
                print(f"addons_path line modified: {lines[i]}")
                break
        if not addons_path_found:
            lines.append(f"addons_path = {new_routes}\n")
            print("A new addons_path line was added.")
        with open(odoo_conf_path, "w") as file:
            file.writelines(lines)
        print("odoo.conf file successfully updated.")
    except FileNotFoundError as fnf_error:
        print(fnf_error)
    except PermissionError as perm_error:
        print(perm_error)
    except Exception as e:
        print(f"Unexpected error when modifying odoo.conf: {e}")

print("\n🔧 Third-party Repository Configuration")
print("=" * 50)

while True:
    if not confirm_third_party_usage():
        # User decided not to use third-party repositories or cancel
        if not config["sources"]:
            print("\n✅ No third-party repositories will be used.")
            if os.path.exists("gitman.yml"):
                os.remove("gitman.yml")
            print("🔧 Commenting gitman lines in Dockerfile...")
            comment_lines()
            sys.exit(0)
        break
    
    # Try to add a repository
    if add_repository():
        # Repository added successfully, ask if they want to add more
        while True:
            add_more = input("\nDo you want to add another repository? (y/n): ").strip().lower()
            if add_more in ['y', 'yes', 'n', 'no']:
                break
            print("Please enter 'y' for yes or 'n' for no.")
        
        if add_more in ['n', 'no']:
            print("\n✅ Finished configuring third-party repositories.")
            break
    else:
        # Could not add repository, check if there are repos or exit
        if not config["sources"]:
            print("\n⚠️  No repositories were added.")
            continue
        else:
            # Already have some repos, ask if continue
            continue_anyway = input("Continue with existing repositories? (y/n): ").strip().lower()
            if continue_anyway in ['y', 'yes']:
                break
            else:
                continue

# Show final summary
if config["sources"]:
    print(f"\n📋 Summary: {len(config['sources'])} repositories configured:")
    for i, source in enumerate(config["sources"], 1):
        print(f"   {i}. {source['name']} (v{source['rev']})")
else:
    print("\n✅ No third-party repositories configured.")
    if os.path.exists("gitman.yml"):
        os.remove("gitman.yml")
    comment_lines()
    sys.exit(0)

# Generate file with correct format
generate_gitman_yml_manually(config)
print("File gitman.yml generated successfully with correct indentation.")

# Debug: Verify generated content
print("\n[DEBUG] Generated gitman.yml content:")
try:
    with open("gitman.yml", "r") as file:
        content = file.read()
        print(content)
        
    # Verify indentation is correct
    if "  scripts:" in content and "    - sh" in content:
        print("✅ [INFO] YAML indentation is correct!")
    else:
        print("❌ [WARNING] YAML indentation might be incorrect!")
except Exception as e:
    print(f"[ERROR] Could not read generated file: {e}")

modify_odoo_conf()