"""
RocketDoo Deploy - Configuration Manager
Manages deployment configuration (deploy.yaml)
"""

import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from rich.console import Console
from rich.prompt import Prompt, Confirm
import questionary
from questionary import Style

console = Console()

# Custom style for questionary
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


class DeployConfigManager:
    """Manages deployment configuration"""
    
    DEFAULT_CONFIG_NAME = "deploy.yaml"
    CONFIG_DIR = ".rkd"
    
    def __init__(self, project_path: Path):
        """
        Initialize configuration manager
        
        Args:
            project_path: Root path of the project
        """
        self.project_path = Path(project_path)
        self.config_dir = self.project_path / self.CONFIG_DIR
        self.config_path = self.config_dir / self.DEFAULT_CONFIG_NAME
        
    def config_exists(self) -> bool:
        """Checks if configuration file exists"""
        return self.config_path.exists()
    
    def load(self) -> Dict:
        """
        Loads configuration from deploy.yaml
        
        Returns:
            Dictionary with configuration
        """
        if not self.config_exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            return config or {}
        except Exception as e:
            raise ValueError(f"Error loading configuration: {e}")
    
    def save(self, config: Dict):
        """
        Saves configuration to deploy.yaml
        
        Args:
            config: Dictionary with configuration
        """
        # Create directory if it doesn't exist
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except Exception as e:
            raise ValueError(f"Error saving configuration: {e}")
    
    def validate(self, config: Dict) -> List[str]:
        """
        Validates configuration
        
        Args:
            config: Configuration to validate
            
        Returns:
            List of errors (empty if OK)
        """
        errors = []
        
        # Validate basic structure
        if 'modules' not in config:
            errors.append("Missing 'modules' section")
        
        if 'targets' not in config:
            errors.append("Missing 'targets' section")
        elif not config['targets']:
            errors.append("No deployment targets configured")
        
        # Validate each target
        for target_name, target_config in config.get('targets', {}).items():
            if 'type' not in target_config:
                errors.append(f"Target '{target_name}': missing 'type' field")
            
            target_type = target_config.get('type')
            
            # Validate VPS
            if target_type == 'vps':
                if 'connection' not in target_config:
                    errors.append(f"Target '{target_name}': missing 'connection' section")
                else:
                    conn = target_config['connection']
                    required = ['host', 'user']
                    for field in required:
                        if field not in conn:
                            errors.append(f"Target '{target_name}': missing connection.{field}")
                
                if 'deployment_type' not in target_config:
                    errors.append(f"Target '{target_name}': missing 'deployment_type'")
            
            # Validate Odoo.sh
            elif target_type == 'odoo-sh':
                if 'odoo_sh' not in target_config:
                    errors.append(f"Target '{target_name}': missing 'odoo_sh' section")
                else:
                    odoo_sh = target_config['odoo_sh']
                    required = ['project_id', 'branch']
                    for field in required:
                        if field not in odoo_sh:
                            errors.append(f"Target '{target_name}': missing odoo_sh.{field}")
        
        return errors
    
    def get_default_config(self) -> Dict:
        """
        Returns default configuration
        
        Returns:
            Dictionary with base configuration
        """
        return {
            'modules': {
                'auto_detect': True,
                'base_path': 'addons',
                'exclude_patterns': [
                    '*/tests/*',
                    '*/__pycache__/*',
                    '*/.git/*',
                    '*/node_modules/*',
                    '*.pyc',
                    '*.pyo'
                ]
            },
            'targets': {},
            'backup': {
                'enabled': True,
                'keep_last': 3,
                'path': '.rkd/deploy_backups'
            },
            'validations': {
                'check_manifest': True,
                'check_python_syntax': True,
                'check_xml_syntax': True,
                'check_dependencies': True,
                'run_tests': False
            },
            'logging': {
                'level': 'INFO',
                'file': '.rkd/deploy.log',
                'console': True
            }
        }
    
    def interactive_setup(self):
        """Interactive wizard to configure deploy.yaml"""
        console.print("[bold cyan]🚀 Deploy Configuration Wizard[/bold cyan]\n")
        
        # Load existing config or create new
        if self.config_exists():
            config = self.load()
            console.print("[dim]Loading existing configuration...[/dim]\n")
        else:
            config = self.get_default_config()
        
        # Module configuration
        console.print("[bold]📦 Module Configuration[/bold]")
        
        auto_detect = Confirm.ask(
            "Auto-detect all modules in addons directory?",
            default=config['modules'].get('auto_detect', True)
        )
        config['modules']['auto_detect'] = auto_detect
        
        addons_path = Prompt.ask(
            "Addons directory path",
            default=config['modules'].get('base_path', 'addons')
        )
        config['modules']['base_path'] = addons_path
        
        console.print()
        
        # Configure targets
        console.print("[bold]🎯 Deployment Targets[/bold]")
        console.print("[dim]Configure servers where you'll deploy your modules[/dim]\n")
        
        if 'targets' not in config:
            config['targets'] = {}
        
        while True:
            add_target = Confirm.ask("Add a deployment target?", default=True)
            if not add_target:
                break
            
            target_config = self._configure_target()
            if target_config:
                target_name, target_data = target_config
                config['targets'][target_name] = target_data
                console.print(f"[green]✓[/green] Target '{target_name}' added\n")
            
            if not Confirm.ask("Add another target?", default=False):
                break
        
        # Backup configuration
        console.print()
        console.print("[bold]💾 Backup Configuration[/bold]")
        
        backup_enabled = Confirm.ask(
            "Enable automatic backup before deploy?",
            default=config.get('backup', {}).get('enabled', True)
        )
        
        if backup_enabled:
            keep_last = Prompt.ask(
                "How many backups to keep?",
                default=str(config.get('backup', {}).get('keep_last', 3))
            )
            config['backup'] = {
                'enabled': True,
                'keep_last': int(keep_last),
                'path': config.get('backup', {}).get('path', '.rkd/deploy_backups')
            }
        else:
            config['backup'] = {'enabled': False}
        
        # Validations
        console.print()
        console.print("[bold]🔍 Validation Configuration[/bold]")
        
        validations = {}
        validations['check_manifest'] = Confirm.ask(
            "Validate __manifest__.py files?",
            default=True
        )
        validations['check_python_syntax'] = Confirm.ask(
            "Check Python syntax?",
            default=True
        )
        validations['check_xml_syntax'] = Confirm.ask(
            "Check XML syntax?",
            default=True
        )
        
        config['validations'] = validations
        
        # Save configuration
        console.print()
        self.save(config)
        
        console.print(f"[green]✓[/green] Configuration saved to: [cyan]{self.config_path}[/cyan]")
    
    def _configure_target(self) -> Optional[tuple]:
        """
        Configures a deployment target interactively
        
        Returns:
            Tuple (target_name, configuration) or None if cancelled
        """
        console.print()
        
        # Target name
        target_name = Prompt.ask(
            "[bold]Target name[/bold]",
            default="production"
        )
        
        # Target type
        target_type = questionary.select(
            "Deployment type:",
            choices=[
                questionary.Choice("VPS (Docker)", value="vps_docker"),
                questionary.Choice("VPS (Native)", value="vps_native"),
                questionary.Choice("Odoo.sh", value="odoo-sh")
            ],
            style=custom_style
        ).ask()
        
        if not target_type:
            return None
        
        if target_type in ['vps_docker', 'vps_native']:
            return self._configure_vps_target(target_name, target_type)
        elif target_type == 'odoo-sh':
            return self._configure_odoo_sh_target(target_name)
        
        return None
    
    def _configure_vps_target(self, target_name: str, target_type: str) -> tuple:
        """
        Configures a VPS target
        
        Args:
            target_name: Name of the target
            target_type: vps_docker or vps_native
            
        Returns:
            Tuple (name, configuration)
        """
        console.print("\n[bold cyan]🖥️  VPS Configuration[/bold cyan]")
        
        # SSH connection
        host = Prompt.ask("Server hostname or IP")
        port = Prompt.ask("SSH port", default="22")
        user = Prompt.ask("SSH username", default="odoo")
        
        use_key = Confirm.ask("Use SSH key authentication?", default=True)
        
        if use_key:
            ssh_key = Prompt.ask(
                "SSH key path",
                default="~/.ssh/id_rsa"
            )
            connection = {
                'host': host,
                'port': int(port),
                'user': user,
                'ssh_key': ssh_key
            }
        else:
            console.print("[yellow]⚠️  Password will be requested at deploy time[/yellow]")
            connection = {
                'host': host,
                'port': int(port),
                'user': user,
                'password': '${VPS_PASSWORD}'  # Environment variable
            }
        
        # Type-specific configuration
        deployment_type = 'docker' if target_type == 'vps_docker' else 'native'
        
        config = {
            'type': 'vps',
            'enabled': True,
            'connection': connection,
            'deployment_type': deployment_type
        }
        
        if deployment_type == 'docker':
            console.print("\n[bold]Docker Configuration[/bold]")
            container_name = Prompt.ask("Odoo container name", default="odoo")
            compose_path = Prompt.ask("Docker compose path", default="/opt/odoo")
            addons_mount = Prompt.ask("Addons mount path", default="/mnt/extra-addons")
            
            config['docker'] = {
                'container_name': container_name,
                'compose_path': compose_path,
                'addons_mount': addons_mount
            }
        else:
            console.print("\n[bold]Native Installation Configuration[/bold]")
            odoo_path = Prompt.ask("Odoo installation path", default="/opt/odoo")
            addons_path = Prompt.ask("Custom addons path", default="/opt/odoo/custom_addons")
            service_name = Prompt.ask("System service name", default="odoo")
            
            config['native'] = {
                'odoo_path': odoo_path,
                'addons_path': addons_path,
                'service_name': service_name
            }
        
        # Post-deploy actions
        console.print("\n[bold]Post-Deploy Actions[/bold]")
        restart = Confirm.ask("Restart Odoo after deploy?", default=True)
        update = Confirm.ask("Update modules automatically?", default=True)
        
        config['post_deploy'] = {
            'restart_service': restart,
            'update_modules': update,
            'run_tests': False,
            'custom_commands': []
        }
        
        return (target_name, config)
    
    def _configure_odoo_sh_target(self, target_name: str) -> tuple:
        """
        Configures an Odoo.sh target
        
        Args:
            target_name: Name of the target
            
        Returns:
            Tuple (name, configuration)
        """
        console.print("\n[bold cyan]☁️  Odoo.sh Configuration[/bold cyan]")
        
        project_id = Prompt.ask("Odoo.sh project ID")
        branch = Prompt.ask("Git branch", default="development or staging")
        
        use_api = Confirm.ask("Use Odoo.sh API?", default=False)
        
        config = {
            'type': 'odoo-sh',
            'enabled': True,
            'odoo_sh': {
                'project_id': project_id,
                'branch': branch
            },
            'structure': {
                'custom_addons_path': '..'
            },
            'post_deploy': {
                'wait_for_build': True,
                'open_browser': False
            }
        }
        
        if use_api:
            console.print("[yellow]⚠️  API token should be set as environment variable: ODOO_SH_TOKEN[/yellow]")
            config['odoo_sh']['api_token'] = '${ODOO_SH_TOKEN}'
        else:
            git_remote = Prompt.ask("Git remote name", default="origin")
            git_url = Prompt.ask("Git repository URL")
            
            config['odoo_sh']['git_remote'] = git_remote
            config['odoo_sh']['git_url'] = git_url
        
        # Protection for production
        if 'production' in target_name.lower():
            console.print("\n[yellow]⚠️  Production environment detected[/yellow]")
            require_confirm = Confirm.ask("Require explicit confirmation for this target?", default=True)
            require_tag = Confirm.ask("Only allow tagged releases?", default=True)
            
            config['require_confirmation'] = require_confirm
            config['require_git_tag'] = require_tag
        
        return (target_name, config)
    
    def add_target(self, target_name: str, target_config: Dict):
        """
        Adds a new target to configuration
        
        Args:
            target_name: Name of the target
            target_config: Target configuration
        """
        config = self.load()
        config['targets'][target_name] = target_config
        self.save(config)
    
    def remove_target(self, target_name: str):
        """
        Removes a target from configuration
        
        Args:
            target_name: Name of the target to remove
        """
        config = self.load()
        if target_name in config.get('targets', {}):
            del config['targets'][target_name]
            self.save(config)
        else:
            raise ValueError(f"Target '{target_name}' not found")
    
    def get_target(self, target_name: str) -> Optional[Dict]:
        """
        Gets configuration of a specific target
        
        Args:
            target_name: Name of the target
            
        Returns:
            Target configuration or None if not found
        """
        config = self.load()
        return config.get('targets', {}).get(target_name)
    
    def list_targets(self) -> List[str]:
        """
        Lists all configured targets
        
        Returns:
            List of target names
        """
        config = self.load()
        return list(config.get('targets', {}).keys())
    
    def create_from_template(self, template_name: str = 'basic'):
        """
        Creates configuration from predefined template
        
        Args:
            template_name: Template name (basic, advanced)
        """
        templates = {
            'basic': self._get_basic_template(),
            'advanced': self._get_advanced_template()
        }
        
        config = templates.get(template_name, templates['basic'])
        self.save(config)
    
    def _get_basic_template(self) -> Dict:
        """Basic template with one VPS Docker"""
        config = self.get_default_config()
        config['targets'] = {
            'production': {
                'type': 'vps',
                'enabled': True,
                'connection': {
                    'host': 'your-server.com',
                    'port': 22,
                    'user': 'odoo',
                    'ssh_key': '~/.ssh/id_rsa'
                },
                'deployment_type': 'docker',
                'docker': {
                    'container_name': 'odoo',
                    'compose_path': '/opt/odoo',
                    'addons_mount': '/mnt/extra-addons'
                },
                'post_deploy': {
                    'restart_service': True,
                    'update_modules': True,
                    'run_tests': False,
                    'custom_commands': []
                }
            }
        }
        return config
    
    def _get_advanced_template(self) -> Dict:
        """Advanced template with multiple targets"""
        config = self.get_default_config()
        config['targets'] = {
            'staging': {
                'type': 'vps',
                'enabled': True,
                'connection': {
                    'host': 'staging.example.com',
                    'port': 22,
                    'user': 'odoo',
                    'ssh_key': '~/.ssh/id_rsa'
                },
                'deployment_type': 'docker',
                'docker': {
                    'container_name': 'odoo',
                    'compose_path': '/opt/odoo',
                    'addons_mount': '/mnt/extra-addons'
                },
                'post_deploy': {
                    'restart_service': True,
                    'update_modules': True,
                    'run_tests': False
                }
            },
            'production': {
                'type': 'vps',
                'enabled': True,
                'connection': {
                    'host': 'production.example.com',
                    'port': 22,
                    'user': 'odoo',
                    'ssh_key': '~/.ssh/id_rsa'
                },
                'deployment_type': 'docker',
                'docker': {
                    'container_name': 'odoo',
                    'compose_path': '/opt/odoo',
                    'addons_mount': '/mnt/extra-addons'
                },
                'require_confirmation': True,
                'post_deploy': {
                    'restart_service': True,
                    'update_modules': True,
                    'run_tests': False
                }
            },
            'odoo_sh_test': {
                'type': 'odoo-sh',
                'enabled': True,
                'odoo_sh': {
                    'project_id': 'my-project',
                    'branch': 'test',
                    'git_remote': 'odoo-sh',
                    'git_url': 'git@github.com:username/odoo-sh-project.git'
                },
                'structure': {
                    'custom_addons_path': 'custom_addons'
                },
                'post_deploy': {
                    'wait_for_build': True,
                    'open_browser': False
                }
            }
        }
        return config