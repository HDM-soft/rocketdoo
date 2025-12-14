
"""
RocketDoo Deploy - Module Packager
Prepares and packages modules for deployment
"""

import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import List, Dict, Optional
from fnmatch import fnmatch
from rich.console import Console

console = Console()


class ModulePackager:
    """Packages Odoo modules for deployment"""
    
    def __init__(self, project_path: Path, exclude_patterns: Optional[List[str]] = None):
        """
        Initialize module packager
        
        Args:
            project_path: Root path of the project
            exclude_patterns: File patterns to exclude from packaging
        """
        self.project_path = Path(project_path)
        self.exclude_patterns = exclude_patterns or self._get_default_excludes()
        
    def _get_default_excludes(self) -> List[str]:
        """Returns default file patterns to exclude"""
        return [
            '*.pyc',
            '*.pyo',
            '**/__pycache__/**',
            '*.swp',
            '*.swo',
            '*~',
            '.git',
            '.gitignore',
            '.DS_Store',
            'Thumbs.db',
            '*.log',
            '*.tmp',
            'tests',
            'test_*.py',
            '*_test.py',
            '.vscode',
            '.idea',
            'node_modules',
            '.env',
            '*.local'
        ]
    
    def should_exclude(self, path: Path) -> bool:
        """
        Checks if a file or directory should be excluded
        
        Args:
            path: Path to check
            
        Returns:
            True if should be excluded
        """
        
        
        # NEVER exclude core Odoo files
        if path.name in ('__manifest__.py', '__init__.py'):
            return False
        
        name = path.name
            
        for pattern in self.exclude_patterns:
            # Check full path
            if pattern.endswith('/'):
                if path.is_dir() and name == pattern.rstrip('/'):
                    return True
                continue

            # Filename match
            if fnmatch(name, pattern):
                return True

        return False
    
    def prepare_module(self, module: Dict, target_dir: Path) -> Path:
        """
        Prepares a single module for deployment
        
        Args:
            module: Module dictionary from ModuleScanner
            target_dir: Directory where to prepare the module
            
        Returns:
            Path to prepared module directory
        """
        source_path = Path(module['full_path'])
        module_name = module['name']
        dest_path = target_dir / module_name
        
        # Create destination directory
        dest_path.mkdir(parents=True, exist_ok=True)
        
        # Copy module files excluding patterns
        self._copy_module_files(source_path, dest_path)
        
        return dest_path
    
    def _copy_module_files(self, source: Path, dest: Path):
        """
        Recursively copies module files excluding patterns
        
        Args:
            source: Source directory
            dest: Destination directory
        """
        for item in source.iterdir():
            # Skip excluded items
            if self.should_exclude(item):
                continue
            
            dest_item = dest / item.name
            
            if item.is_dir():
                dest_item.mkdir(exist_ok=True)
                self._copy_module_files(item, dest_item)
            else:
                shutil.copy2(item, dest_item)
    
    def prepare_modules(self, modules: List[Dict]) -> Path:
        """
        Prepares multiple modules for deployment
        
        Args:
            modules: List of module dictionaries
            
        Returns:
            Path to temporary directory with prepared modules
        """
        # Create temporary directory
        temp_dir = Path(tempfile.mkdtemp(prefix='rkd_deploy_'))
        
        console.print(f"📦 Preparing {len(modules)} modules for deployment...")
        
        for module in modules:
            try:
                self.prepare_module(module, temp_dir)
                console.print(f"  ✓ {module['name']}", style="green")
            except Exception as e:
                console.print(f"  ✗ {module['name']}: {e}", style="red")
                raise
        
        return temp_dir
    
    def create_archive(self, modules: List[Dict], output_path: Optional[Path] = None) -> Path:
        """
        Creates a tar.gz archive with modules
        
        Args:
            modules: List of modules to package
            output_path: Optional custom output path for archive
            
        Returns:
            Path to created archive
        """
        # Prepare modules in temp directory
        temp_dir = self.prepare_modules(modules)
        
        # Determine output path
        if output_path is None:
            timestamp = Path(tempfile.mktemp(suffix='.tar.gz', prefix='rkd_modules_'))
            output_path = timestamp
        
        output_path = Path(output_path)
        
        # Create archive
        console.print(f"\n📦 Creating archive: {output_path.name}")
        
        with tarfile.open(output_path, 'w:gz') as tar:
            for module in modules:
                module_dir = temp_dir / module['name']
                if module_dir.exists():
                    tar.add(module_dir, arcname=module['name'])
                    console.print(f"  ✓ Added {module['name']}", style="green")
        
        # Clean up temp directory
        shutil.rmtree(temp_dir)
        
        # Get archive size
        size_mb = output_path.stat().st_size / (1024 * 1024)
        console.print(f"\n✅ Archive created: {size_mb:.2f} MB", style="bold green")
        
        return output_path
    
    def extract_archive(self, archive_path: Path, target_dir: Path):
        """
        Extracts a module archive
        
        Args:
            archive_path: Path to archive file
            target_dir: Directory where to extract
        """
        console.print(f"📦 Extracting archive to: {target_dir}")
        
        target_dir.mkdir(parents=True, exist_ok=True)
        
        with tarfile.open(archive_path, 'r:gz') as tar:
            tar.extractall(target_dir)
        
        console.print("✅ Archive extracted successfully", style="green")
    
    def get_module_size(self, module: Dict) -> int:
        """
        Calculates total size of a module in bytes
        
        Args:
            module: Module dictionary
            
        Returns:
            Size in bytes
        """
        module_path = Path(module['full_path'])
        total_size = 0
        
        for item in module_path.rglob('*'):
            if item.is_file() and not self.should_exclude(item):
                total_size += item.stat().st_size
        
        return total_size
    
    def get_modules_size(self, modules: List[Dict]) -> Dict[str, int]:
        """
        Calculates size for each module
        
        Args:
            modules: List of modules
            
        Returns:
            Dictionary with module names and sizes
        """
        sizes = {}
        for module in modules:
            sizes[module['name']] = self.get_module_size(module)
        return sizes
    
    def validate_module_structure(self, module: Dict) -> List[str]:
        """
        Validates module structure before packaging
        
        Args:
            module: Module dictionary
            
        Returns:
            List of validation errors (empty if OK)
        """
        errors = []
        module_path = Path(module['full_path'])
        
        # Check __manifest__.py exists
        manifest_path = module_path / '__manifest__.py'
        if not manifest_path.exists():
            errors.append(f"Missing __manifest__.py")
        
        # Check __init__.py exists
        init_path = module_path / '__init__.py'
        if not init_path.exists():
            errors.append(f"Missing __init__.py")
        
        # Check for common required directories
        common_dirs = ['models', 'views', 'security']
        has_content = False
        
        for dir_name in common_dirs:
            dir_path = module_path / dir_name
            if dir_path.exists() and dir_path.is_dir():
                has_content = True
                break
        
        if not has_content:
            # Check for any .py files besides __init__.py and __manifest__.py
            py_files = [f for f in module_path.glob('*.py') 
                       if f.name not in ['__init__.py', '__manifest__.py']]
            if not py_files:
                errors.append(f"Module appears to be empty (no models/views/security)")
        
        return errors
    
    def validate_modules(self, modules: List[Dict]) -> Dict[str, List[str]]:
        """
        Validates multiple modules
        
        Args:
            modules: List of modules to validate
            
        Returns:
            Dictionary with module names and their validation errors
        """
        results = {}
        
        for module in modules:
            errors = self.validate_module_structure(module)
            if errors:
                results[module['name']] = errors
        
        return results
    
    def create_deployment_manifest(self, modules: List[Dict], output_path: Path):
        """
        Creates a deployment manifest file with module information
        
        Args:
            modules: List of modules
            output_path: Path where to save manifest
        """
        import json
        from datetime import datetime
        
        manifest = {
            'deployment_date': datetime.now().isoformat(),
            'total_modules': len(modules),
            'modules': []
        }
        
        for module in modules:
            manifest['modules'].append({
                'name': module['name'],
                'version': module.get('version', '1.0'),
                'path': module.get('path', ''),
                'size_bytes': self.get_module_size(module),
                'depends': module.get('depends', [])
            })
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)
        
        console.print(f"📄 Deployment manifest created: {output_path}", style="cyan")


# Example usage
if __name__ == "__main__":
    from rocketdoo.core.module_scanner import ModuleScanner
    
    # Scan modules
    scanner = ModuleScanner(Path('addons'))
    modules = [m.to_dict() for m in scanner.get_installable_modules()]
    
    # Package modules
    packager = ModulePackager(Path('.'))
    archive_path = packager.create_archive(modules)
    
    print(f"Archive created: {archive_path}")