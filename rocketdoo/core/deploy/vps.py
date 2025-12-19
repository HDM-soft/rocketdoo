"""
RocketDoo Deploy - VPS Deployer
Deploys Odoo modules to VPS servers (Docker or Native)
"""

import os
import subprocess
import shutil
from getpass import getpass
from pathlib import Path
import stat
from typing import List, Dict, Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .base import BaseDeployer, DeploymentResult
from .module_packager import ModulePackager

console = Console()


class VPSDeployer(BaseDeployer):
    """
    Deployer for VPS servers
    
    Supports both Docker and Native Odoo installations
    Uses SSH/SCP for file transfer and remote command execution
    Supports both SSH key and password authentication
    """
    
    def _check_sshpass(self) -> bool:
        """
        Check if sshpass is available in system
        """
        return shutil.which("sshpass") is not None
    
    
    def __init__(self, target_name: str, config: Dict, project_path: Path):
        """
        Initialize VPS deployer
        
        Args:
            target_name: Name of the target
            config: VPS configuration from deploy.yaml
            project_path: Root path of the project
        """
        super().__init__(target_name, config, project_path)
        
        # Extract connection config
        self.connection = config.get('connection', {})
        self.host = self.connection.get('host')
        self.port = self.connection.get('port', 22)
        self.user = self.connection.get('user')
        
        # Authentication credentials
        self.ssh_key = self.connection.get('ssh_key')
        self.password = self.connection.get('password')

        # Determine authentication method by presence
        if self.ssh_key and self.password:
            raise ValueError("Invalid configuration: both ssh_key and password defined")

        if self.ssh_key:
            self.auth_method = 'ssh_key'
        elif self.password:
            self.auth_method = 'password'
        else:
            self.auth_method = None

        
        # Resolve environment variables in password
        if self.password and self.password.startswith('${') and self.password.endswith('}'):
            env_var = self.password[2:-1]
            self.password = os.environ.get(env_var)
            if not self.password:
                console.print(f"[yellow]Warning: Environment variable {env_var} not set[/yellow]")
        
        # Deployment type (docker or native)
        self.deployment_type = config.get('deployment_type', 'docker')
        
        # Type-specific config
        if self.deployment_type == 'docker':
            self.docker_config = config.get('docker', {})
            self.container_name = self.docker_config.get('container_name', 'odoo')
            self.compose_path = self.docker_config.get('compose_path', '/opt/odoo')
            self.addons_mount = self.docker_config.get('addons_mount', '/mnt/extra-addons')
        else:
            self.native_config = config.get('native', {})
            self.odoo_path = self.native_config.get('odoo_path', '/opt/odoo')
            self.remote_addons_path = self.native_config.get('addons_path', '/opt/odoo/custom_addons')
            self.service_name = self.native_config.get('service_name', 'odoo')
        
        # Post-deploy config
        self.post_deploy_config = config.get('post_deploy', {})
        
        # Module packager
        exclude_patterns = config.get('modules', {}).get('exclude_patterns', [])
        self.packager = ModulePackager(project_path, exclude_patterns)
        
        # Ask for password interactively if required and not set
        if self.auth_method == 'password' and not self.password:
            console.print("[yellow]🔐 VPS password not found. Please enter it below.[/yellow]")
            self.password = getpass("VPS Password: ")

            if not self.password:
                raise ValueError("Password authentication selected but no password provided")

            # Persist password locally (secure storage)
            secrets_dir = Path(".rkd/secrets")
            secrets_dir.mkdir(parents=True, exist_ok=True)

            secret_file = secrets_dir / f"vps_{self.target_name}.env"

            secret_file.write_text(f"VPS_PASSWORD={self.password}\n")
            secret_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600

            console.print(
                f"[green]✔ Password stored securely at {secret_file}[/green]"
            )

    
    def validate_config(self) -> List[str]:
        """
        Validate VPS configuration
        
        Returns:
            List of configuration errors
        """
        errors = []
        
        # Validate connection
        if not self.host:
            errors.append("Missing 'connection.host'")
        if not self.user:
            errors.append("Missing 'connection.user'")
        
        # Validate authentication method
        if not self.auth_method:
            errors.append("No authentication method defined (ssh_key or password required)")

        
        # Validate authentication credentials
        if self.auth_method == 'ssh_key':
            if not self.ssh_key:
                errors.append("SSH key authentication selected but 'ssh_key' not configured")
            elif not Path(os.path.expanduser(self.ssh_key)).exists():
                errors.append(f"SSH key not found: {self.ssh_key}")
        elif self.auth_method == 'password':
            if not self.password:
                errors.append(
                    "Password authentication selected but 'password' not configured or environment variable not set"
                )
            elif not self._check_sshpass():
                errors.append(
                    "Password authentication requires 'sshpass'. "
                    "Install it with: sudo apt install sshpass"
                )

        
        # Validate type-specific config
        if self.deployment_type == 'docker':
            if not self.container_name:
                errors.append("Missing 'docker.container_name'")
            if not self.addons_mount:
                errors.append("Missing 'docker.addons_mount'")
        elif self.deployment_type == 'native':
            if not self.remote_addons_path:
                errors.append("Missing 'native.addons_path'")
        else:
            errors.append(f"Invalid deployment_type: {self.deployment_type}")
        
        return errors
    
    def pre_deploy_check(self) -> bool:
        """
        Pre-deployment checks
        
        Verifies:
        - SSH connectivity
        - Remote paths exist
        - Required commands available
        
        Returns:
            True if all checks pass
        """
        try:
            # Test SSH connection
            self.log("Testing SSH connection...", "info")
            result = self._run_ssh_command("echo 'Connection successful'")
            
            if result.returncode != 0:
                self.log(f"SSH connection failed: {result.stderr}", "error")
                return False
            
            self.log("✓ SSH connection established", "success")
            
            # Check if remote path exists
            if self.deployment_type == 'docker':
                remote_path = f"{self.compose_path}"
            else:
                remote_path = self.remote_addons_path
            
            result = self._run_ssh_command(f"test -d {remote_path} && echo 'exists'")
            
            if "exists" not in result.stdout:
                self.log(f"Warning: Remote path {remote_path} does not exist", "warning")
                # Try to create it
                self.log(f"Attempting to create {remote_path}...", "info")
                create_result = self._run_ssh_command(f"sudo mkdir -p {remote_path}")
                if create_result.returncode != 0:
                    self.log(f"Failed to create remote path: {create_result.stderr}", "error")
                    return False
            
            # Check Docker if needed
            if self.deployment_type == 'docker':
                result = self._run_ssh_command("docker --version")
                if result.returncode != 0:
                    self.log("Docker not found on remote server", "error")
                    return False
                self.log("✓ Docker available", "success")
                
                # Check if container exists
                result = self._run_ssh_command(f"docker ps -a --filter name={self.container_name} --format '{{{{.Names}}}}'")
                if self.container_name not in result.stdout:
                    self.log(f"Warning: Container '{self.container_name}' not found", "warning")
            
            return True
            
        except Exception as e:
            self.log(f"Pre-deploy check failed: {e}", "error")
            return False
    
    def deploy_modules(self, modules: List[Dict]) -> DeploymentResult:
        """
        Deploy modules to VPS
        
        Args:
            modules: List of modules to deploy
            
        Returns:
            DeploymentResult
        """
        try:
            # Prepare modules
            self.log(f"Preparing {len(modules)} modules...", "info")
            temp_dir = self.packager.prepare_modules(modules)
            
            # Determine target path on remote server
            if self.deployment_type == 'docker':
                # For Docker, we need to copy to the host path that's mounted
                target_path = f"{self.compose_path}/addons"
            else:
                target_path = self.remote_addons_path
            
            # Transfer modules
            self.log(f"Transferring modules to {self.host}:{target_path}...", "info")
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                task = progress.add_task("[cyan]Uploading modules...", total=len(modules))
                
                for module in modules:
                    module_name = module['name']
                    local_path = temp_dir / module_name
                    
                    # Upload module
                    success = self._upload_directory(
                        local_path,
                        f"{target_path}/{module_name}"
                    )
                    
                    if not success:
                        return DeploymentResult(
                            success=False,
                            message=f"Failed to upload module: {module_name}"
                        )
                    
                    progress.update(task, advance=1)
            
            # Clean up temp directory
            import shutil
            shutil.rmtree(temp_dir)
            
            self.log(f"✓ All modules transferred successfully", "success")
            
            return DeploymentResult(
                success=True,
                message="Modules deployed successfully",
                details={'modules': [m['name'] for m in modules]}
            )
            
        except Exception as e:
            self.log(f"Deployment failed: {e}", "error")
            return DeploymentResult(
                success=False,
                message=f"Deployment error: {e}"
            )
    
    def post_deploy_actions(self) -> DeploymentResult:
        """
        Execute post-deployment actions
        
        Actions:
        - Restart Odoo service/container
        - Update modules
        - Run custom commands
        
        Returns:
            DeploymentResult
        """
        try:
            # Restart service
            if self.post_deploy_config.get('restart_service', False):
                self.log("Restarting Odoo service...", "info")
                
                if self.deployment_type == 'docker':
                    # Restart Docker container
                    result = self._run_ssh_command(
                        f"cd {self.compose_path} && docker-compose restart {self.container_name}"
                    )
                    
                    if result.returncode != 0:
                        self.log(f"Failed to restart container: {result.stderr}", "error")
                        return DeploymentResult(
                            success=False,
                            message="Failed to restart Docker container"
                        )
                else:
                    # Restart systemd service
                    result = self._run_ssh_command(
                        f"sudo systemctl restart {self.service_name}"
                    )
                    
                    if result.returncode != 0:
                        self.log(f"Failed to restart service: {result.stderr}", "error")
                        return DeploymentResult(
                            success=False,
                            message="Failed to restart Odoo service"
                        )
                
                self.log("✓ Service restarted", "success")
            
            # Update modules
            if self.post_deploy_config.get('update_modules', False):
                self.log("Updating modules in Odoo...", "info")
                
                if self.deployment_type == 'docker':
                    # Update via Docker exec
                    update_cmd = (
                        f"docker exec {self.container_name} "
                        f"odoo-bin -c /etc/odoo/odoo.conf -u all --stop-after-init"
                    )
                else:
                    # Update via direct command
                    python_env = self.native_config.get('python_env', 'python3')
                    update_cmd = (
                        f"{python_env} {self.odoo_path}/odoo-bin "
                        f"-c {self.odoo_path}/odoo.conf -u all --stop-after-init"
                    )
                
                result = self._run_ssh_command(update_cmd)
                
                if result.returncode != 0:
                    self.log(f"Warning: Module update had issues: {result.stderr}", "warning")
                else:
                    self.log("✓ Modules updated", "success")
            
            # Run custom commands
            custom_commands = self.post_deploy_config.get('custom_commands', [])
            if custom_commands:
                self.log(f"Running {len(custom_commands)} custom commands...", "info")
                
                for cmd in custom_commands:
                    self.log(f"Executing: {cmd}", "info")
                    result = self._run_ssh_command(cmd)
                    
                    if result.returncode != 0:
                        self.log(f"Command failed: {result.stderr}", "warning")
                    else:
                        self.log(f"✓ Command completed", "success")
            
            return DeploymentResult(
                success=True,
                message="Post-deploy actions completed"
            )
            
        except Exception as e:
            self.log(f"Post-deploy actions failed: {e}", "error")
            return DeploymentResult(
                success=False,
                message=f"Post-deploy error: {e}"
            )
    
    def _run_ssh_command(self, command: str, timeout: int = 300) -> subprocess.CompletedProcess:
        """
        Execute command on remote server via SSH
        
        Args:
            command: Command to execute
            timeout: Command timeout in seconds
            
        Returns:
            CompletedProcess with result
        """
        ssh_cmd = ['ssh']
        
        # Add SSH options
        ssh_cmd.extend([
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-p', str(self.port)
        ])
        
        # Add authentication based on method
        if self.auth_method == 'ssh_key' and self.ssh_key:
            key_path = os.path.expanduser(self.ssh_key)
            ssh_cmd.extend(['-i', key_path])
        elif self.auth_method == 'password' and self.password:
            # Use sshpass for password authentication
            ssh_cmd = ['sshpass', '-p', self.password] + ssh_cmd
        
        # Add user@host
        ssh_cmd.append(f"{self.user}@{self.host}")
        
        # Add command
        ssh_cmd.append(command)
        
        # Execute
        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result
        except FileNotFoundError as e:
            if 'sshpass' in str(e):
                self.log("Error: 'sshpass' not found. Install it with: sudo apt install sshpass", "error")
                raise
            raise
    
    def _upload_directory(self, local_path: Path, remote_path: str) -> bool:
        """
        Upload directory to remote server using rsync
        
        Args:
            local_path: Local directory to upload
            remote_path: Remote destination path
            
        Returns:
            True if successful
        """
        try:
            # Build rsync command
            rsync_cmd = ['rsync', '-avz', '--delete']
            
            # Add SSH options based on auth method
            ssh_opts = f"-p {self.port} -o StrictHostKeyChecking=no"
            
            if self.auth_method == 'ssh_key' and self.ssh_key:
                key_path = os.path.expanduser(self.ssh_key)
                ssh_opts += f" -i {key_path}"
            elif self.auth_method == 'password' and self.password:
                # Use sshpass for rsync with password
                rsync_cmd = ['sshpass', '-p', self.password] + rsync_cmd
            
            rsync_cmd.extend(['-e', f'ssh {ssh_opts}'])
            
            # Add source and destination
            rsync_cmd.append(f"{local_path}/")
            rsync_cmd.append(f"{self.user}@{self.host}:{remote_path}/")
            
            # Execute rsync
            result = subprocess.run(
                rsync_cmd,
                capture_output=True,
                text=True,
                timeout=600
            )
            
            if result.returncode != 0:
                self.log(f"rsync failed: {result.stderr}", "error")
                return False
            
            return True
            
        except subprocess.TimeoutExpired:
            self.log("Upload timeout exceeded", "error")
            return False
        except FileNotFoundError as e:
            if 'sshpass' in str(e):
                self.log("Error: 'sshpass' not found. Install it with: sudo apt install sshpass", "error")
            else:
                self.log(f"Upload error: {e}", "error")
            return False
        except Exception as e:
            self.log(f"Upload error: {e}", "error")
            return False
    
    def _upload_file_scp(self, local_path: Path, remote_path: str) -> bool:
        """
        Upload single file using SCP (fallback if rsync not available)
        
        Args:
            local_path: Local file to upload
            remote_path: Remote destination path
            
        Returns:
            True if successful
        """
        try:
            scp_cmd = ['scp']
            
            # Add SSH options
            scp_cmd.extend([
                '-o', 'StrictHostKeyChecking=no',
                '-P', str(self.port)
            ])
            
            # Add authentication based on method
            if self.auth_method == 'ssh_key' and self.ssh_key:
                key_path = os.path.expanduser(self.ssh_key)
                scp_cmd.extend(['-i', key_path])
            elif self.auth_method == 'password' and self.password:
                # Use sshpass for password authentication
                scp_cmd = ['sshpass', '-p', self.password] + scp_cmd
            
            # Add source and destination
            scp_cmd.append(str(local_path))
            scp_cmd.append(f"{self.user}@{self.host}:{remote_path}")
            
            # Execute
            result = subprocess.run(
                scp_cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            return result.returncode == 0
            
        except Exception as e:
            self.log(f"SCP upload error: {e}", "error")
            return False
    
    def rollback(self) -> DeploymentResult:
        """
        Rollback deployment by restoring from backup
        
        Returns:
            DeploymentResult
        """
        try:
            self.log("Initiating rollback...", "warning")
            
            # Find latest backup
            backup_config = self.config.get('backup', {})
            backup_path = self.project_path / backup_config.get('path', '.rkd/deploy_backups')
            
            if not backup_path.exists():
                return DeploymentResult(
                    success=False,
                    message="No backups found for rollback"
                )
            
            # Get latest backup for this target
            backups = sorted(
                [d for d in backup_path.iterdir() if d.is_dir() and d.name.startswith(self.target_name)],
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            
            if not backups:
                return DeploymentResult(
                    success=False,
                    message="No backups found for this target"
                )
            
            latest_backup = backups[0]
            self.log(f"Restoring from backup: {latest_backup.name}", "info")
            
            # Upload backup to server
            if self.deployment_type == 'docker':
                target_path = f"{self.compose_path}/addons"
            else:
                target_path = self.remote_addons_path
            
            # Upload each module from backup
            for module_dir in latest_backup.iterdir():
                if module_dir.is_dir():
                    success = self._upload_directory(
                        module_dir,
                        f"{target_path}/{module_dir.name}"
                    )
                    if not success:
                        self.log(f"Failed to restore module: {module_dir.name}", "error")
            
            # Restart service
            if self.deployment_type == 'docker':
                self._run_ssh_command(f"cd {self.compose_path} && docker-compose restart {self.container_name}")
            else:
                self._run_ssh_command(f"sudo systemctl restart {self.service_name}")
            
            self.log("✓ Rollback completed", "success")
            
            return DeploymentResult(
                success=True,
                message="Rollback completed successfully"
            )
            
        except Exception as e:
            self.log(f"Rollback failed: {e}", "error")
            return DeploymentResult(
                success=False,
                message=f"Rollback error: {e}"
            )