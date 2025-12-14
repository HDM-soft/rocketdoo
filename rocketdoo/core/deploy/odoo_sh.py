"""
RocketDoo Deploy - Odoo.sh Deployer
Deploys Odoo modules to Odoo.sh platform via Git
"""

import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .base import BaseDeployer, DeploymentResult
from .module_packager import ModulePackager

console = Console()


class OdooSHDeployer(BaseDeployer):
    """
    Deployer for Odoo.sh platform
    
    Odoo.sh expects modules in the root of the repository:
    repo/
    ├── module1/
    │   └── __manifest__.py
    ├── module2/
    │   └── __manifest__.py
    └── odoo/              # Submodule: Odoo core (optional)
    
    This deployer:
    1. Prepares modules in the root structure
    2. Commits and pushes to Odoo.sh Git repository
    3. Optionally waits for build completion
    """
    
    def __init__(self, target_name: str, config: Dict, project_path: Path):
        """
        Initialize Odoo.sh deployer
        
        Args:
            target_name: Name of the target
            config: Odoo.sh configuration from deploy.yaml
            project_path: Root path of the project
        """
        super().__init__(target_name, config, project_path)
        
        # Extract Odoo.sh config
        self.odoo_sh_config = config.get('odoo_sh', {})
        self.project_id = self.odoo_sh_config.get('project_id')
        self.branch = self.odoo_sh_config.get('branch', 'main')
        self.api_token = self.odoo_sh_config.get('api_token')
        self.git_remote = self.odoo_sh_config.get('git_remote', 'origin')
        self.git_url = self.odoo_sh_config.get('git_url')
        
        # Post-deploy config
        self.post_deploy_config = config.get('post_deploy', {})
        
        # Module packager
        exclude_patterns = config.get('modules', {}).get('exclude_patterns', [])
        self.packager = ModulePackager(project_path, exclude_patterns)
        
        # Temporary repository path
        self.temp_repo = None
    
    def validate_config(self) -> List[str]:
        """
        Validate Odoo.sh configuration
        
        Returns:
            List of configuration errors
        """
        errors = []
        
        # Validate required fields
        if not self.project_id:
            errors.append("Missing 'odoo_sh.project_id'")
        
        if not self.branch:
            errors.append("Missing 'odoo_sh.branch'")
        
        # Validate Git configuration
        if not self.git_url and not self.api_token:
            errors.append("Either 'git_url' or 'api_token' must be configured")
        
        # Check if git is available
        try:
            result = subprocess.run(
                ['git', '--version'],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                errors.append("Git is not installed or not available in PATH")
        except FileNotFoundError:
            errors.append("Git is not installed")
        
        return errors
    
    def pre_deploy_check(self) -> bool:
        """
        Pre-deployment checks for Odoo.sh
        
        Verifies:
        - Git is configured
        - Remote repository is accessible
        - Branch exists
        
        Returns:
            True if all checks pass
        """
        try:
            # Check Git configuration
            self.log("Checking Git configuration...", "info")
            
            result = subprocess.run(
                ['git', 'config', 'user.name'],
                capture_output=True,
                text=True
            )
            
            if not result.stdout.strip():
                self.log("Git user.name not configured", "error")
                return False
            
            result = subprocess.run(
                ['git', 'config', 'user.email'],
                capture_output=True,
                text=True
            )
            
            if not result.stdout.strip():
                self.log("Git user.email not configured", "error")
                return False
            
            self.log("✓ Git configured", "success")
            
            # Check if we can access the remote
            if self.git_url:
                self.log(f"Testing access to {self.git_url}...", "info")
                
                # Clone to temp directory to test access
                temp_test = Path(tempfile.mkdtemp(prefix='rkd_test_'))
                try:
                    result = subprocess.run(
                        ['git', 'clone', '--depth', '1', '--branch', self.branch, self.git_url, str(temp_test)],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    
                    if result.returncode != 0:
                        # Branch might not exist yet, that's OK
                        if "not found" in result.stderr.lower():
                            self.log(f"Branch '{self.branch}' doesn't exist yet (will be created)", "warning")
                        else:
                            self.log(f"Failed to access repository: {result.stderr}", "error")
                            return False
                    else:
                        self.log("✓ Repository accessible", "success")
                
                finally:
                    # Clean up test clone
                    if temp_test.exists():
                        shutil.rmtree(temp_test)
            
            return True
            
        except subprocess.TimeoutExpired:
            self.log("Repository access timeout", "error")
            return False
        except Exception as e:
            self.log(f"Pre-deploy check failed: {e}", "error")
            return False
    
    def deploy_modules(self, modules: List[Dict]) -> DeploymentResult:
        """
        Deploy modules to Odoo.sh
        
        Process:
        1. Clone/prepare Odoo.sh repository
        2. Copy modules to repository root
        3. Commit and push changes
        
        Args:
            modules: List of modules to deploy
            
        Returns:
            DeploymentResult
        """

        try:
            # Create temporary directory for repository
            self.temp_repo = Path(tempfile.mkdtemp(prefix='rkd_odoo_sh_'))
            self.log(f"Working in: {self.temp_repo}", "info")
            
            # Clone or initialize repository
            self.log("Preparing repository...", "info")
            if not self._prepare_repository():
                return DeploymentResult(
                    success=False,
                    message="Failed to prepare repository"
                )
            
            # Copy modules to repository root
            self.log(f"Copying {len(modules)} modules to repository...", "info")
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task("[cyan]Copying modules...", total=len(modules))
                
                for module in modules:
                    module_name = module['name']
                    source_path = Path(module['full_path'])
                    # Modules go to repository root
                    dest_path = self.temp_repo / module_name
                    
                    self.log(f"Copying module: {module_name}", "info")
                    self.log(f"  From: {source_path}", "debug")
                    self.log(f"  To: {dest_path}", "debug")
                    
                    if not source_path.exists():
                        self.log(f"  ERROR: Source path does not exist!", "error")
                        continue
                    
                    # Remove existing module if present and managed by us
                    if dest_path.exists():
                        if (dest_path / '.rkd_managed').exists():
                            self.log(f"  Removing existing managed module", "debug")
                            shutil.rmtree(dest_path)
                        else:
                            self.log(f"  WARNING: Module exists but not managed by RocketDoo, skipping", "warning")
                            progress.update(task, advance=1)
                            continue
                    
                    # Copy module
                    self._copy_module_filtered(source_path, dest_path)
                    
                    # Mark as managed by RocketDoo
                    (dest_path / '.rkd_managed').touch()
                    
                    # Verify copy
                    if dest_path.exists():
                        copied_files = list(dest_path.rglob('*'))
                        self.log(f"  ✓ Copied successfully ({len(copied_files)} files)", "success")
                    else:
                        self.log(f"  ✗ Copy failed!", "error")
                    
                    progress.update(task, advance=1)
            
            self.log("✓ Modules copied", "success")
            
            # Stage changes - Add all module directories
            self.log("Staging changes...", "info")
            module_names = [m['name'] for m in modules]
            
            # Add each module directory
            for module_name in module_names:
                result = self._run_git_command(['add', '-A', module_name])
                if result.returncode != 0:
                    self.log(f"Warning: Failed to stage {module_name}: {result.stderr}", "warning")
            
            # Check if there are changes to commit
            result = self._run_git_command(['diff', '--cached', '--name-only'])
            staged_files = result.stdout.strip()
            
            if not staged_files:
                self.log("No changes to deploy", "warning")
                return DeploymentResult(
                    success=True,
                    message="No changes detected, nothing to deploy"
                )
            
            self.log(f"Staged files:\n{staged_files}", "debug")
            
            # Commit changes with detailed message
            from datetime import datetime
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_message = f"[RocketDoo] Deploy modules: {', '.join(module_names)}\n\nDeployed at: {timestamp}\nModules: {len(module_names)}\nFiles changed: {len(staged_files.splitlines())}"
            
            self.log("Creating commit...", "info")
            result = self._run_git_command(['commit', '-m', commit_message])
            if result.returncode != 0:
                return DeploymentResult(
                    success=False,
                    message=f"Failed to commit: {result.stderr}"
                )
            
            self.log("✓ Changes committed", "success")
            
            # Push to Odoo.sh
            self.log(f"Pushing to Odoo.sh (branch: {self.branch})...", "info")
            result = self._run_git_command(['push', self.git_remote, self.branch])
            
            if result.returncode != 0:
                return DeploymentResult(
                    success=False,
                    message=f"Failed to push: {result.stderr}"
                )
            
            self.log("✓ Pushed to Odoo.sh", "success")
            
            return DeploymentResult(
                success=True,
                message="Modules deployed to Odoo.sh",
                details={
                    'modules': module_names,
                    'branch': self.branch,
                    'commit': commit_message
                }
            )
            
        except Exception as e:
            import traceback
            self.log(f"Deployment failed: {e}", "error")
            self.log(f"Traceback: {traceback.format_exc()}", "debug")
            return DeploymentResult(
                success=False,
                message=f"Deployment error: {e}"
            )
        finally:
            # Clean up temporary repository
            if self.temp_repo and self.temp_repo.exists():
                shutil.rmtree(self.temp_repo)
    
    def post_deploy_actions(self) -> DeploymentResult:
        """
        Post-deployment actions for Odoo.sh
        
        Actions:
        - Wait for build (optional)
        - Open browser to instance (optional)
        
        Returns:
            DeploymentResult
        """
        try:
            # Wait for build
            if self.post_deploy_config.get('wait_for_build', False):
                self.log("Waiting for Odoo.sh build...", "info")
                self.log("Note: Build monitoring not yet implemented", "warning")
                self.log("Check build status at: https://www.odoo.sh/project/{}/builds", "info")
            
            # Open browser
            if self.post_deploy_config.get('open_browser', False):
                instance_url = f"https://{self.project_id}-{self.branch}.odoo.com"
                self.log(f"Opening browser: {instance_url}", "info")
                
                import webbrowser
                webbrowser.open(instance_url)
            
            # Show useful information
            console.print()
            console.print("📋 Deployment Information:", style="bold cyan")
            console.print(f"  Project: {self.project_id}")
            console.print(f"  Branch: {self.branch}")
            console.print(f"  URL: https://{self.project_id}-{self.branch}.odoo.com")
            console.print()
            
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
    
    def _prepare_repository(self) -> bool:
        """
        Clone or initialize the Odoo.sh repository
        
        Returns:
            True if successful
        """
        try:
            if self.git_url:
                # Try to clone existing repository
                self.log(f"Cloning repository from {self.git_url}...", "info")
                
                # Clone with branch specification
                result = subprocess.run(
                    ['git', 'clone', '--branch', self.branch, self.git_url, str(self.temp_repo)],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                # Change to repo directory BEFORE checking result
                if self.temp_repo.exists():
                    os.chdir(self.temp_repo)
                
                if result.returncode != 0:
                    # Branch might not exist, clone default and create branch
                    self.log(f"Branch '{self.branch}' not found, creating new branch...", "warning")
                    
                    # Clone without branch specification
                    result = subprocess.run(
                        ['git', 'clone', self.git_url, str(self.temp_repo)],
                        capture_output=True,
                        text=True,
                        timeout=300
                    )
                    
                    if result.returncode != 0:
                        self.log(f"Failed to clone repository: {result.stderr}", "error")
                        return False
                    
                    # Create and checkout new branch
                    os.chdir(self.temp_repo)
                    result = self._run_git_command(['checkout', '-b', self.branch])
                    if result.returncode != 0:
                        self.log(f"Failed to create branch: {result.stderr}", "error")
                        return False
                else:
                    os.chdir(self.temp_repo)
                
                self.log("✓ Repository ready", "success")
                return True
            else:
                # Initialize new repository (API mode)
                self.log("Initializing new repository...", "info")
                os.chdir(self.temp_repo)
                
                # Init repo
                result = self._run_git_command(['init'])
                if result.returncode != 0:
                    return False
                
                # Create branch
                result = self._run_git_command(['checkout', '-b', self.branch])
                if result.returncode != 0:
                    return False
                
                # Add remote
                if self.git_url:
                    result = self._run_git_command(['remote', 'add', self.git_remote, self.git_url])
                    if result.returncode != 0:
                        return False
                
                return True
                
        except subprocess.TimeoutExpired:
            self.log("Repository clone timeout", "error")
            return False
        except Exception as e:
            self.log(f"Failed to prepare repository: {e}", "error")
            return False
    
    def _copy_module_filtered(self, source: Path, dest: Path):
        """
        Copy module files excluding patterns
        
        Args:
            source: Source module directory
            dest: Destination directory
        """
        if not source.exists():
            self.log(f"Source does not exist: {source}", "error")
            return
        
        if not source.is_dir():
            self.log(f"Source is not a directory: {source}", "error")
            return
        
        # Create destination directory
        dest.mkdir(parents=True, exist_ok=True)
        
        # Copy all files recursively
        for item in source.iterdir():
            # Skip excluded items
            if self.packager.should_exclude(item):
                continue
            
            dest_item = dest / item.name
            
            if item.is_dir():
                self._copy_module_filtered(item, dest_item)
            else:
                shutil.copy2(item, dest_item)
    
    def _run_git_command(self, args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
        """
        Execute git command in repository
        
        Args:
            args: Git command arguments
            timeout: Command timeout in seconds
            
        Returns:
            CompletedProcess with result
        """
        cmd = ['git'] + args
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=self.temp_repo
        )
        
        return result
    
    def rollback(self) -> DeploymentResult:
        """
        Rollback by reverting the last commit and pushing
        
        Returns:
            DeploymentResult
        """
        try:
            self.log("Initiating rollback on Odoo.sh...", "warning")
            
            # Clone repository
            temp_repo = Path(tempfile.mkdtemp(prefix='rkd_rollback_'))
            
            try:
                # Clone
                result = subprocess.run(
                    ['git', 'clone', '--branch', self.branch, self.git_url, str(temp_repo)],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                if result.returncode != 0:
                    return DeploymentResult(
                        success=False,
                        message=f"Failed to clone for rollback: {result.stderr}"
                    )
                
                os.chdir(temp_repo)
                
                # Revert last commit
                self.log("Reverting last commit...", "info")
                result = subprocess.run(
                    ['git', 'revert', '--no-edit', 'HEAD'],
                    capture_output=True,
                    text=True,
                    cwd=temp_repo
                )
                
                if result.returncode != 0:
                    return DeploymentResult(
                        success=False,
                        message=f"Failed to revert: {result.stderr}"
                    )
                
                # Push
                self.log("Pushing rollback...", "info")
                result = subprocess.run(
                    ['git', 'push', self.git_remote, self.branch],
                    capture_output=True,
                    text=True,
                    cwd=temp_repo
                )
                
                if result.returncode != 0:
                    return DeploymentResult(
                        success=False,
                        message=f"Failed to push rollback: {result.stderr}"
                    )
                
                self.log("✓ Rollback completed", "success")
                
                return DeploymentResult(
                    success=True,
                    message="Rollback completed successfully"
                )
                
            finally:
                # Clean up
                if temp_repo.exists():
                    shutil.rmtree(temp_repo)
                
        except Exception as e:
            self.log(f"Rollback failed: {e}", "error")
            return DeploymentResult(
                success=False,
                message=f"Rollback error: {e}"
            )