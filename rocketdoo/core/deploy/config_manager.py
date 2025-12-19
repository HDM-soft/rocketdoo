"""
RocketDoo Deploy - Configuration Manager
Manages deployment configuration (deploy.yaml)
"""

import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.panel import Panel
import questionary
from questionary import Style

console = Console()

# ---------------------------------------------------------
# Questionary Style
# ---------------------------------------------------------
custom_style = Style([
    ('qmark', 'fg:#673ab7 bold'),
    ('question', 'bold'),
    ('answer', 'fg:#2196f3 bold'),
    ('pointer', 'fg:#673ab7 bold'),
    ('highlighted', 'fg:#673ab7 bold'),
    ('selected', 'fg:#4caf50'),
    ('separator', 'fg:#cc5454'),
    ('instruction', ''),
    ('text', ''),
])


# =========================================================
# SSH KEY SELECTOR
# =========================================================
class SSHKeySelector:
    """Helper class for SSH key selection"""

    @staticmethod
    def list_ssh_keys(ssh_dir: Path = None) -> List[Dict[str, str]]:
        if ssh_dir is None:
            ssh_dir = Path.home() / '.ssh'

        if not ssh_dir.exists():
            return []

        keys = []

        for file in ssh_dir.iterdir():
            if file.is_file() and not file.name.endswith('.pub'):
                try:
                    with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                        first_line = f.readline().strip()
                        if 'PRIVATE KEY' not in first_line:
                            continue

                    stat = file.stat()
                    pub_file = ssh_dir / f"{file.name}.pub"

                    key_type = 'Ed25519' if 'OPENSSH' in first_line else 'Unknown'
                    permissions = oct(stat.st_mode)[-3:]
                    is_secure = permissions in ('600', '400')

                    comment = ""
                    if pub_file.exists():
                        try:
                            parts = pub_file.read_text().split()
                            if len(parts) >= 3:
                                comment = parts[2]
                        except Exception:
                            pass

                    keys.append({
                        'name': file.name,
                        'path': str(file),
                        'type': key_type,
                        'has_pub': pub_file.exists(),
                        'comment': comment,
                        'permissions': permissions,
                        'is_secure': is_secure
                    })
                except Exception:
                    continue

        return sorted(keys, key=lambda k: k['name'])

    @staticmethod
    def display_keys_table(keys: List[Dict[str, str]]) -> None:
        if not keys:
            console.print(Panel(
                "[yellow]No SSH keys found in ~/.ssh[/yellow]\n\n"
                "Generate one using:\n"
                "[cyan]ssh-keygen -t ed25519 -C \"your@email.com\"[/cyan]",
                title="SSH Keys",
                border_style="yellow"
            ))
            return

        table = Table(title="Available SSH Keys", header_style="bold cyan")
        table.add_column("#", justify="right")
        table.add_column("Name")
        table.add_column("Type")
        table.add_column("Public")
        table.add_column("Secure")
        table.add_column("Comment")

        for idx, key in enumerate(keys, 1):
            table.add_row(
                str(idx),
                key['name'],
                key['type'],
                "✓" if key['has_pub'] else "✗",
                "✓" if key['is_secure'] else "⚠",
                key['comment'] or "-"
            )

        console.print(table)

    @staticmethod
    def prompt_for_key_selection(keys: List[Dict[str, str]]) -> Optional[str]:
        SSHKeySelector.display_keys_table(keys)

        choice = Prompt.ask("Select SSH key number or 'm' for manual path")

        if choice.lower() == 'm':
            return Prompt.ask("SSH key path", default="~/.ssh/id_ed25519")

        try:
            index = int(choice) - 1
            return keys[index]['path']
        except Exception:
            console.print("[red]Invalid selection[/red]")
            return None


# =========================================================
# DEPLOY CONFIG MANAGER
# =========================================================
class DeployConfigManager:
    DEFAULT_CONFIG_NAME = "deploy.yaml"
    CONFIG_DIR = ".rkd"

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path)
        self.config_dir = self.project_path / self.CONFIG_DIR
        self.config_path = self.config_dir / self.DEFAULT_CONFIG_NAME

    # -----------------------------------------------------
    # Base Operations
    # -----------------------------------------------------
    def config_exists(self) -> bool:
        return self.config_path.exists()

    def load(self) -> Dict:
        if not self.config_exists():
            raise FileNotFoundError(self.config_path)
        return yaml.safe_load(self.config_path.read_text()) or {}

    def save(self, config: Dict):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            yaml.dump(config, sort_keys=False, allow_unicode=True)
        )
        
    def interactive_setup(self):
        """
        Entry point for deploy init wizard
        """
        console.print(
            Panel(
                "🚀 Deploy Configuration Wizard\n\n"
                "This wizard will help you configure deployment targets",
                border_style="cyan"
            )
        )

        config = self.get_default_config()

        self.save(config)

        console.print(
            f"[green]✓[/green] Base configuration created at [cyan]{self.config_path}[/cyan]"
        )


    # -----------------------------------------------------
    # Defaults
    # -----------------------------------------------------
    def get_default_config(self) -> Dict:
        return {
            'modules': {
                'auto_detect': True,
                'base_path': 'addons'
            },
            'targets': {},
            'backup': {
                'enabled': True,
                'keep_last': 3
            },
            'validations': {
                'check_manifest': True,
                'check_python_syntax': True,
                'check_xml_syntax': True
            }
        }

    # -----------------------------------------------------
    # Templates
    # -----------------------------------------------------
    def _get_basic_template(self) -> Dict:
        config = self.get_default_config()
        config['targets'] = {
            'production': {
                'type': 'vps',
                'enabled': True,
                'deployment_type': 'docker',
                'connection': {
                    'host': 'server.example.com',
                    'port': 22,
                    'user': 'odoo',
                    'auth_method': 'ssh_key',
                    'ssh_key': '~/.ssh/id_ed25519'
                },
                'docker': {
                    'container_name': 'odoo',
                    'compose_path': '/opt/odoo',
                    'addons_mount': '/mnt/extra-addons'
                }
            }
        }
        return config

    def _get_advanced_template(self) -> Dict:
        config = self.get_default_config()
        config['targets'] = {
            'staging': {
                'type': 'vps',
                'enabled': True,
                'deployment_type': 'docker',
                'connection': {
                    'host': 'staging.example.com',
                    'port': 22,
                    'user': 'odoo',
                    'auth_method': 'ssh_key',
                    'ssh_key': '~/.ssh/id_ed25519'
                },
                'docker': {
                    'container_name': 'odoo-staging',
                    'compose_path': '/opt/odoo-staging',
                    'addons_mount': '/mnt/extra-addons'
                }
            },
            'production': {
                'type': 'odoo-sh',
                'enabled': True,
                'odoo_sh': {
                    'project_id': '12345',
                    'branch': 'production'
                },
                'require_confirmation': True,
                'require_git_tag': True
            }
        }
        return config
