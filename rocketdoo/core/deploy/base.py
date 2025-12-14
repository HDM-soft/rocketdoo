
"""
RocketDoo Deploy - Base Deployer
Abstract base class for all deployers
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


class DeploymentResult:
    """Result of a deployment operation"""
    
    def __init__(self, success: bool, message: str, details: Optional[Dict] = None):
        self.success = success
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now()
    
    def __bool__(self):
        return self.success
    
    def __str__(self):
        status = "✅ SUCCESS" if self.success else "❌ FAILED"
        return f"{status}: {self.message}"


class BaseDeployer(ABC):
    """
    Abstract base class for deployers
    
    Each deployment type (VPS, Odoo.sh, etc) inherits from this class
    and implements the abstract methods.
    """
    
    def __init__(self, target_name: str, config: Dict, project_path: Path):
        """
        Initialize the deployer
        
        Args:
            target_name: Name of the target (e.g., 'vps_production')
            config: Target configuration from deploy.yaml
            project_path: Root path of the project
        """
        self.target_name = target_name
        self.config = config
        self.project_path = Path(project_path)
        self.addons_path = self.project_path / config.get('modules', {}).get('base_path', 'addons')
        self.console = console
        
        # Deployment logs
        self.logs = []
    
    def log(self, message: str, level: str = "info"):
        """Log a message"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append({
            'timestamp': timestamp,
            'level': level,
            'message': message
        })
        
        # Also print to console
        styles = {
            'info': 'cyan',
            'success': 'green',
            'warning': 'yellow',
            'error': 'red'
        }
        self.console.print(f"[{timestamp}] {message}", style=styles.get(level, 'white'))
    
    @abstractmethod
    def validate_config(self) -> List[str]:
        """
        Validate deployer configuration
        
        Returns:
            List of configuration errors (empty if OK)
        """
        pass
    
    @abstractmethod
    def pre_deploy_check(self) -> bool:
        """
        Pre-deployment checks (connectivity, permissions, etc)
        
        Returns:
            True if all checks pass
        """
        pass
    
    @abstractmethod
    def deploy_modules(self, modules: List[Dict]) -> DeploymentResult:
        """
        Deploy the modules
        
        Args:
            modules: List of modules to deploy (from ModuleScanner)
            
        Returns:
            DeploymentResult with the operation result
        """
        pass
    
    @abstractmethod
    def post_deploy_actions(self) -> DeploymentResult:
        """
        Post-deployment actions (restart, update, etc)
        
        Returns:
            DeploymentResult with the operation result
        """
        pass
    
    def rollback(self) -> DeploymentResult:
        """
        Rollback in case of error
        Default does nothing, subclasses can implement
        
        Returns:
            DeploymentResult with rollback result
        """
        self.log("Rollback not implemented for this deployer", "warning")
        return DeploymentResult(
            success=True,
            message="No rollback needed"
        )
    
    def create_backup(self, modules: List[Dict]) -> bool:
        """
        Create backup of modules before deployment
        
        Args:
            modules: List of modules to backup
            
        Returns:
            True if backup was successful
        """
        backup_config = self.config.get('backup', {})
        if not backup_config.get('enabled', True):
            return True
        
        try:
            backup_path = self.project_path / backup_config.get('path', '.rkd/deploy_backups')
            backup_path.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = backup_path / f"{self.target_name}_{timestamp}"
            backup_dir.mkdir()
            
            # Copy modules to backup
            import shutil
            for module in modules:
                src = Path(module['full_path'])
                dst = backup_dir / module['name']
                shutil.copytree(src, dst)
            
            self.log(f"Backup created at: {backup_dir}", "success")
            
            # Clean up old backups
            self._cleanup_old_backups(backup_path, backup_config.get('keep_last', 3))
            
            return True
            
        except Exception as e:
            self.log(f"Error creating backup: {e}", "error")
            return False
    
    def _cleanup_old_backups(self, backup_path: Path, keep_last: int):
        """Delete old backups keeping only the last N"""
        try:
            backups = sorted(
                [d for d in backup_path.iterdir() if d.is_dir() and d.name.startswith(self.target_name)],
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            
            # Delete old backups
            for backup in backups[keep_last:]:
                import shutil
                shutil.rmtree(backup)
                self.log(f"Old backup deleted: {backup.name}", "info")
                
        except Exception as e:
            self.log(f"Error cleaning up backups: {e}", "warning")
    
    def validate_modules(self, modules: List[Dict]) -> List[str]:
        """
        Validate modules before deployment
        
        Args:
            modules: List of modules to validate
            
        Returns:
            List of errors found
        """
        errors = []
        validations = self.config.get('validations', {})
        
        if not validations:
            return errors
        
        for module in modules:
            module_name = module['name']
            module_path = Path(module['full_path'])
            
            # Validate manifest
            if validations.get('check_manifest', True):
                manifest_path = module_path / "__manifest__.py"
                if not manifest_path.exists():
                    errors.append(f"{module_name}: __manifest__.py not found")
            
            # Validate Python syntax
            if validations.get('check_python_syntax', True):
                py_files = list(module_path.rglob("*.py"))
                for py_file in py_files:
                    try:
                        with open(py_file, 'r', encoding='utf-8') as f:
                            compile(f.read(), str(py_file), 'exec')
                    except SyntaxError as e:
                        errors.append(f"{module_name}: Syntax error in {py_file.name}: {e}")
            
            # Validate XML
            if validations.get('check_xml_syntax', True):
                xml_files = list(module_path.rglob("*.xml"))
                for xml_file in xml_files:
                    try:
                        import xml.etree.ElementTree as ET
                        ET.parse(xml_file)
                    except ET.ParseError as e:
                        errors.append(f"{module_name}: Invalid XML in {xml_file.name}: {e}")
        
        return errors
    
    def execute(self, modules: List[Dict]) -> DeploymentResult:
        """
        Execute the complete deployment
        
        This is the main method that orchestrates the entire process
        
        Args:
            modules: List of modules to deploy
            
        Returns:
            DeploymentResult with final result
        """
        self.console.print(f"\n🚀 Starting deployment to: {self.target_name}", style="bold blue")
        self.console.print(f"📦 Modules to deploy: {len(modules)}\n")
        
        # 1. Validate configuration
        self.log("Validating configuration...", "info")
        config_errors = self.validate_config()
        if config_errors:
            for error in config_errors:
                self.log(f"  ❌ {error}", "error")
            return DeploymentResult(
                success=False,
                message="Invalid configuration",
                details={'errors': config_errors}
            )
        
        # 2. Validate modules
        self.log("Validating modules...", "info")
        validation_errors = self.validate_modules(modules)
        if validation_errors:
            for error in validation_errors:
                self.log(f"  ❌ {error}", "error")
            return DeploymentResult(
                success=False,
                message="Module validation failed",
                details={'errors': validation_errors}
            )
        
        # 3. Create backup
        self.log("Creating backup...", "info")
        if not self.create_backup(modules):
            return DeploymentResult(
                success=False,
                message="Backup failed"
            )
        
        # 4. Pre-deploy check
        self.log("Checking connectivity...", "info")
        if not self.pre_deploy_check():
            return DeploymentResult(
                success=False,
                message="Pre-deploy check failed"
            )
        
        # 5. Deploy
        self.log("Deploying modules...", "info")
        deploy_result = self.deploy_modules(modules)
        if not deploy_result.success:
            self.log("Deployment failed, initiating rollback...", "error")
            self.rollback()
            return deploy_result
        
        # 6. Post-deploy
        self.log("Executing post-deploy actions...", "info")
        post_result = self.post_deploy_actions()
        if not post_result.success:
            self.log("Post-deploy failed", "warning")
            # Don't rollback here, modules are already deployed
        
        self.console.print(f"\n✅ Deployment completed successfully!", style="bold green")
        return DeploymentResult(
            success=True,
            message=f"Deployment to {self.target_name} completed",
            details={
                'modules_deployed': len(modules),
                'logs': self.logs
            }
        )