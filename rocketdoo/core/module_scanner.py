"""
RocketDoo Module Scanner
Recursively detects Odoo modules in the addons directory
"""

import ast
from pathlib import Path
from typing import List, Dict, Optional
from rich.console import Console

console = Console()


class OdooModule:
    """Represents a detected Odoo module"""
    
    def __init__(self, path: Path, base_path: Path):
        self.path = path
        self.base_path = base_path
        self.name = path.name
        self.manifest_path = path / "__manifest__.py"
        self._manifest = None
        
    @property
    def relative_path(self) -> Path:
        """Relative path from base_path"""
        return self.path.relative_to(self.base_path)
    
    @property
    def manifest(self) -> Dict:
        """Loads and caches the manifest content"""
        if self._manifest is None:
            self._manifest = self._load_manifest()
        return self._manifest
    
    def _load_manifest(self) -> Dict:
        """Safely loads __manifest__.py"""
        try:
            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Parse content as AST
            tree = ast.parse(content)
            
            # Find the main dictionary assignment
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    return ast.literal_eval(node)
            
            return {}
            
        except Exception as e:
            console.print(f"⚠️  Error loading manifest for {self.name}: {e}", style="yellow")
            return {}
    
    @property
    def is_installable(self) -> bool:
        """Checks if the module is installable"""
        return self.manifest.get('installable', True)
    
    @property
    def version(self) -> str:
        """Module version"""
        return self.manifest.get('version', '1.0')
    
    @property
    def depends(self) -> List[str]:
        """List of dependencies"""
        return self.manifest.get('depends', [])
    
    @property
    def has_invalid_name(self) -> bool:
        """Checks if name contains hyphens (not recommended)"""
        return '-' in self.name
    
    def validate(self) -> List[str]:
        """
        Validates the module and returns list of warnings/errors
        
        Returns:
            List of validation messages
        """
        issues = []
        
        # Validate name
        if self.has_invalid_name:
            issues.append(f"⚠️  Name '{self.name}' contains hyphens. Odoo recommends underscores (_)")
        
        # Validate manifest
        if not self.manifest:
            issues.append(f"❌ Could not load __manifest__.py")
            return issues
        
        # Validate required fields
        required_fields = ['name', 'version', 'depends', 'data']
        for field in required_fields:
            if field not in self.manifest:
                issues.append(f"⚠️  Field '{field}' missing in __manifest__.py")
        
        # Validate installable
        if not self.is_installable:
            issues.append(f"ℹ️  Module marked as non-installable")
        
        return issues
    
    def to_dict(self) -> Dict:
        """Converts the module to dictionary for serialization"""
        return {
            'name': self.name,
            'path': str(self.relative_path),
            'full_path': str(self.path),
            'installable': self.is_installable,
            'version': self.version,
            'depends': self.depends,
            'manifest': self.manifest
        }


class ModuleScanner:
    """Scans and manages Odoo modules in a project"""
    
    def __init__(self, addons_path: Path, exclude_patterns: Optional[List[str]] = None):
        self.addons_path = Path(addons_path)
        self.exclude_patterns = exclude_patterns or [
            "*/tests/*",
            "*/__pycache__/*",
            "*/.git/*",
            "*/node_modules/*"
        ]
        self._modules = None
    
    def should_exclude(self, path: Path) -> bool:
        """Checks if a path should be excluded"""
        from fnmatch import fnmatch
        
        path_str = str(path)
        for pattern in self.exclude_patterns:
            if fnmatch(path_str, pattern):
                return True
        return False
    
    def scan(self, force_rescan: bool = False) -> List[OdooModule]:
        """
        Scans the addons directory for modules
        
        Args:
            force_rescan: Force a new scan even if cache exists
            
        Returns:
            List of found modules
        """
        if self._modules is not None and not force_rescan:
            return self._modules
        
        if not self.addons_path.exists():
            console.print(f"❌ Addons directory not found: {self.addons_path}", style="red")
            return []
        
        modules = []
        
        # Recursively search for all __manifest__.py files
        for manifest_path in self.addons_path.rglob("__manifest__.py"):
            module_path = manifest_path.parent
            
            # Exclude patterns
            if self.should_exclude(module_path):
                continue
            
            module = OdooModule(module_path, self.addons_path)
            modules.append(module)
        
        # Sort by name
        modules.sort(key=lambda m: m.name)
        
        self._modules = modules
        return modules
    
    def get_module_by_name(self, name: str) -> Optional[OdooModule]:
        """Finds a module by name"""
        modules = self.scan()
        for module in modules:
            if module.name == name:
                return module
        return None
    
    def get_installable_modules(self) -> List[OdooModule]:
        """Returns only installable modules"""
        return [m for m in self.scan() if m.is_installable]
    
    def validate_all(self) -> Dict[str, List[str]]:
        """
        Validates all modules
        
        Returns:
            Dict with module name and list of issues
        """
        results = {}
        for module in self.scan():
            issues = module.validate()
            if issues:
                results[module.name] = issues
        return results
    
    def print_summary(self):
        """Prints a summary of found modules"""
        modules = self.scan()
        
        if not modules:
            console.print("❌ No Odoo modules found in addons/", style="red")
            return
        
        console.print(f"\n📦 Modules found: {len(modules)}", style="bold green")
        console.print("─" * 60)
        
        for module in modules:
            # Icon based on status
            if module.is_installable:
                icon = "✓"
                style = "green"
            else:
                icon = "○"
                style = "dim"
            
            # Show name with warning if it has hyphens
            name_display = module.name
            if module.has_invalid_name:
                name_display += " ⚠️"
            
            console.print(
                f"  {icon} {name_display:<30} v{module.version:<10} {module.relative_path}",
                style=style
            )
        
        # Show validations if there are issues
        validation_results = self.validate_all()
        if validation_results:
            console.print("\n⚠️  Validations:", style="yellow bold")
            for module_name, issues in validation_results.items():
                console.print(f"\n  {module_name}:", style="yellow")
                for issue in issues:
                    console.print(f"    {issue}")


def find_odoo_modules(addons_path: Path, exclude_patterns: Optional[List[str]] = None) -> List[Dict]:
    """
    Helper function for compatibility with existing code
    
    Args:
        addons_path: Path to the addons directory
        exclude_patterns: Patterns to exclude
        
    Returns:
        List of dictionaries with module information
    """
    scanner = ModuleScanner(addons_path, exclude_patterns)
    modules = scanner.scan()
    return [m.to_dict() for m in modules]


# Example usage
if __name__ == "__main__":
    import sys
    
    addons_path = Path(sys.argv[1] if len(sys.argv) > 1 else "addons")
    
    scanner = ModuleScanner(addons_path)
    scanner.print_summary()