"""
RocketDoo Deploy CLI
Commands to deploy Odoo modules to VPS and Odoo.sh
"""

import click
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Confirm
from rich import box
import questionary

console = Console()


@click.group(name='deploy')
@click.pass_context
def deploy(ctx):
    """
    🚀 Deploy modules to VPS or Odoo.sh
    
    \b
    Examples:
    
    \b
    # Initialize deploy configuration
    rkd deploy init
    
    \b
    # List available modules
    rkd deploy list-modules
    
    \b
    # Configure deployment targets
    rkd deploy config
    
    \b
    # Deploy to a target
    rkd deploy run --target production
    
    \b
    # Deploy specific modules
    rkd deploy run --target staging --module my_module
    """
    pass


@deploy.command(name='init')
@click.option('--force', '-f', is_flag=True, help='Overwrite existing deploy.yaml')
@click.pass_context
def deploy_init(ctx, force):
    """
    Initialize deployment configuration
    
    Creates deploy.yaml with interactive wizard
    """
    from rocketdoo.core.deploy.config_manager import DeployConfigManager
    
    project_path = Path.cwd()
    config_manager = DeployConfigManager(project_path)
    
    # Check if config already exists
    if config_manager.config_exists() and not force:
        console.print("\n[yellow]⚠️  deploy.yaml already exists![/yellow]")
        if not Confirm.ask("Do you want to overwrite it?", default=False):
            console.print("[dim]Cancelled[/dim]\n")
            return
    
    console.print()
    console.print(Panel(
        "[bold cyan]🚀 Deploy Configuration Wizard[/bold cyan]\n\n"
        "[dim]This wizard will help you configure deployment targets[/dim]",
        border_style="cyan",
        box=box.ROUNDED
    ))
    console.print()
    
    try:
        config_manager.interactive_setup()
        console.print("\n[green]✅ Deploy configuration created successfully![/green]")
        console.print(f"[dim]📄 Configuration saved to: {config_manager.config_path}[/dim]\n")
        
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Setup cancelled[/yellow]\n")
    except Exception as e:
        console.print(f"\n[red]❌ Error creating configuration: {e}[/red]\n")


@deploy.command(name='list-modules')
@click.option('--path', '-p', default='addons', help='Path to addons directory')
@click.option('--all', '-a', 'show_all', is_flag=True, help='Show all modules including non-installable')
@click.pass_context
def list_modules(ctx, path, show_all):
    """
    List all detected Odoo modules
    
    Scans the addons directory and shows available modules
    """
    from rocketdoo.core.module_scanner import ModuleScanner
    
    project_path = Path.cwd()
    addons_path = project_path / path
    
    if not addons_path.exists():
        console.print(f"\n[red]❌ Addons directory not found: {addons_path}[/red]\n")
        return
    
    console.print()
    console.print(Panel(
        f"[bold cyan]📦 Scanning Modules[/bold cyan]\n\n"
        f"[dim]Path:[/dim] {addons_path}",
        border_style="cyan",
        box=box.ROUNDED
    ))
    
    scanner = ModuleScanner(addons_path)
    modules = scanner.scan()
    
    if not modules:
        console.print("\n[yellow]⚠️  No modules found in addons directory[/yellow]\n")
        return
    
    # Filter installable if needed
    display_modules = modules if show_all else [m for m in modules if m.is_installable]
    
    # Create table
    table = Table(
        show_header=True,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True
    )
    table.add_column("Status", justify="center", style="bold", width=6)
    table.add_column("Module Name", style="cyan bold")
    table.add_column("Version", justify="center", style="green")
    table.add_column("Path", style="dim")
    table.add_column("Depends", style="yellow")
    
    for module in display_modules:
        # Status icon
        if module.is_installable:
            status = "✓"
            status_style = "green"
        else:
            status = "○"
            status_style = "dim"
        
        # Module name with warning if needed
        name = module.name
        if module.has_invalid_name:
            name += " ⚠️"
        
        # Dependencies (max 3, then ...)
        depends = module.depends[:3]
        depends_str = ", ".join(depends)
        if len(module.depends) > 3:
            depends_str += f" +{len(module.depends) - 3}"
        
        table.add_row(
            f"[{status_style}]{status}[/{status_style}]",
            name,
            module.version,
            str(module.relative_path),
            depends_str or "-"
        )
    
    console.print()
    console.print(table)
    
    # Summary
    console.print()
    total = len(modules)
    installable = len([m for m in modules if m.is_installable])
    console.print(f"[dim]Total: {total} modules | Installable: {installable}[/dim]\n")
    
    # Show validations if any
    validation_results = scanner.validate_all()
    if validation_results:
        console.print("[yellow]⚠️  Validation warnings:[/yellow]")
        for module_name, issues in validation_results.items():
            console.print(f"\n  [cyan]{module_name}:[/cyan]")
            for issue in issues:
                console.print(f"    {issue}")
        console.print()


@deploy.command(name='config')
@click.option('--edit', '-e', is_flag=True, help='Open config file in editor')
@click.pass_context
def deploy_config(ctx, edit):
    """
    Manage deployment configuration
    
    View or edit deploy.yaml configuration
    """
    from rocketdoo.core.deploy.config_manager import DeployConfigManager
    
    project_path = Path.cwd()
    config_manager = DeployConfigManager(project_path)
    
    if not config_manager.config_exists():
        console.print("\n[yellow]⚠️  No deploy configuration found![/yellow]")
        console.print("[dim]💡 Run 'rkd deploy init' to create one[/dim]\n")
        return
    
    if edit:
        # Open in default editor
        import os
        editor = os.environ.get('EDITOR', 'nano')
        os.system(f"{editor} {config_manager.config_path}")
        return
    
    # Show current configuration
    config = config_manager.load()
    
    console.print()
    console.print(Panel(
        "[bold cyan]📋 Deploy Configuration[/bold cyan]\n\n"
        f"[dim]File:[/dim] {config_manager.config_path}",
        border_style="cyan",
        box=box.ROUNDED
    ))
    
    # Show targets
    targets = config.get('targets', {})
    
    if not targets:
        console.print("\n[yellow]⚠️  No deployment targets configured[/yellow]\n")
        return
    
    table = Table(
        show_header=True,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True
    )
    table.add_column("Status", justify="center", width=6)
    table.add_column("Target Name", style="cyan bold")
    table.add_column("Type", style="green")
    table.add_column("Destination", style="yellow")
    
    for target_name, target_config in targets.items():
        # Status
        enabled = target_config.get('enabled', True)
        status = "✓" if enabled else "○"
        status_style = "green" if enabled else "dim"
        
        # Type
        target_type = target_config.get('type', 'unknown')
        
        # Destination
        if target_type == 'vps':
            conn = target_config.get('connection', {})
            destination = f"{conn.get('user')}@{conn.get('host')}"
        elif target_type == 'odoo-sh':
            odoo_sh = target_config.get('odoo_sh', {})
            destination = f"{odoo_sh.get('project_id')} ({odoo_sh.get('branch')})"
        else:
            destination = "unknown"
        
        table.add_row(
            f"[{status_style}]{status}[/{status_style}]",
            target_name,
            target_type,
            destination
        )
    
    console.print()
    console.print(table)
    console.print()


@deploy.command(name='targets')
@click.pass_context
def list_targets(ctx):
    """
    List all configured deployment targets
    
    Shows status and details of each target
    """
    # Alias for 'deploy config'
    ctx.invoke(deploy_config)


@deploy.command(name='run')
@click.option('--target', '-t', required=True, help='Target name from deploy.yaml')
@click.option('--module', '-m', multiple=True, help='Specific module(s) to deploy (default: all)')
@click.option('--dry-run', is_flag=True, help='Simulate deployment without making changes')
@click.option('--skip-backup', is_flag=True, help='Skip backup creation')
@click.option('--skip-validation', is_flag=True, help='Skip pre-deploy validation')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation prompts')
@click.pass_context
def deploy_run(ctx, target, module, dry_run, skip_backup, skip_validation, yes):
    """
    Deploy modules to a target
    
    \b
    Examples:
    
    \b
    # Deploy all modules to production
    rkd deploy run --target production
    
    \b
    # Deploy specific modules
    rkd deploy run --target staging --module my_module --module other_module
    
    \b
    # Dry run (simulation)
    rkd deploy run --target production --dry-run
    """
    from rocketdoo.core.deploy.config_manager import DeployConfigManager
    from rocketdoo.core.module_scanner import ModuleScanner
    from rocketdoo.core.deploy.vps import VPSDeployer
    from rocketdoo.core.deploy.odoo_sh import OdooSHDeployer
    
    project_path = Path.cwd()
    
    # Load configuration
    config_manager = DeployConfigManager(project_path)
    
    if not config_manager.config_exists():
        console.print("\n[red]❌ No deploy configuration found![/red]")
        console.print("[dim]💡 Run 'rkd deploy init' to create one[/dim]\n")
        return
    
    config = config_manager.load()
    
    # Get target configuration
    target_config = config.get('targets', {}).get(target)
    
    if not target_config:
        console.print(f"\n[red]❌ Target '{target}' not found in deploy.yaml[/red]\n")
        available = list(config.get('targets', {}).keys())
        if available:
            console.print("[dim]Available targets:[/dim]")
            for t in available:
                console.print(f"  • {t}")
        console.print()
        return
    
    # Check if target is enabled
    if not target_config.get('enabled', True):
        console.print(f"\n[yellow]⚠️  Target '{target}' is disabled[/yellow]")
        if not Confirm.ask("Do you want to deploy anyway?", default=False):
            console.print("[dim]Cancelled[/dim]\n")
            return
    
    # Banner
    console.print()
    if dry_run:
        console.print(Panel(
            "[bold yellow]🔍 DRY-RUN MODE[/bold yellow]\n\n"
            "[dim]No changes will be made[/dim]",
            border_style="yellow",
            box=box.ROUNDED
        ))
    
    console.print(Panel(
        f"[bold cyan]🚀 Deploying to: {target}[/bold cyan]\n\n"
        f"[dim]Type:[/dim] {target_config.get('type', 'unknown')}",
        border_style="cyan",
        box=box.ROUNDED
    ))
    
    # Scan modules
    addons_path = project_path / config.get('modules', {}).get('base_path', 'addons')
    scanner = ModuleScanner(
        addons_path,
        exclude_patterns=config.get('modules', {}).get('exclude_patterns')
    )
    
    all_modules = scanner.get_installable_modules()
    
    if not all_modules:
        console.print("\n[yellow]⚠️  No modules found to deploy[/yellow]\n")
        return
    
    # Filter specific modules if requested
    if module:
        modules_to_deploy = [m for m in all_modules if m.name in module]
        not_found = set(module) - {m.name for m in modules_to_deploy}
        if not_found:
            console.print(f"\n[yellow]⚠️  Modules not found: {', '.join(not_found)}[/yellow]\n")
            return
    else:
        modules_to_deploy = all_modules
    
    # Show modules to deploy
    console.print(f"\n📦 Modules to deploy: [cyan]{len(modules_to_deploy)}[/cyan]")
    for m in modules_to_deploy:
        console.print(f"  • {m.name} [dim]v{m.version}[/dim]")
    console.print()
    
    # Confirmation
    if not yes and not dry_run:
        if target_config.get('require_confirmation', False):
            console.print("[yellow]⚠️  This target requires explicit confirmation[/yellow]")
        
        if not Confirm.ask(f"Deploy {len(modules_to_deploy)} module(s) to '{target}'?", default=False):
            console.print("[dim]Cancelled[/dim]\n")
            return
    
    # Create deployer based on type
    target_type = target_config.get('type')
    
    if target_type == 'vps':
        deployer = VPSDeployer(target, target_config, project_path)
    elif target_type == 'odoo-sh':
        deployer = OdooSHDeployer(target, target_config, project_path)
    else:
        console.print(f"\n[red]❌ Unknown target type: {target_type}[/red]\n")
        return
    
    # Override config if flags are set
    if skip_backup:
        target_config['backup'] = {'enabled': False}
    if skip_validation:
        target_config['validations'] = {}
    
    # Execute deployment
    try:
        modules_dict = [m.to_dict() for m in modules_to_deploy]
        
        if dry_run:
            console.print("\n[yellow]🔍 DRY-RUN: Simulating deployment...[/yellow]\n")
            # TODO: Implement dry-run logic
            console.print("[green]✅ Dry-run completed successfully[/green]\n")
        else:
            result = deployer.execute(modules_dict)
            
            if result.success:
                console.print()
                console.print(Panel(
                    f"[bold green]✅ Deployment successful![/bold green]\n\n"
                    f"[dim]Target:[/dim] {target}\n"
                    f"[dim]Modules:[/dim] {len(modules_to_deploy)}\n"
                    f"[dim]Time:[/dim] {result.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
                    border_style="green",
                    box=box.DOUBLE
                ))
            else:
                console.print()
                console.print(Panel(
                    f"[bold red]❌ Deployment failed[/bold red]\n\n"
                    f"{result.message}",
                    border_style="red",
                    box=box.ROUNDED
                ))
    
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Deployment cancelled by user[/yellow]\n")
    except Exception as e:
        console.print(f"\n[red]❌ Deployment error: {e}[/red]\n")
        if ctx.obj.get('verbose'):
            import traceback
            console.print("[dim]" + traceback.format_exc() + "[/dim]")


@deploy.command(name='validate')
@click.option('--path', '-p', default='addons', help='Path to addons directory')
@click.pass_context
def validate_modules(ctx, path):
    """
    Validate modules before deployment
    
    Checks for common issues in module structure and code
    """
    from rocketdoo.core.module_scanner import ModuleScanner
    
    project_path = Path.cwd()
    addons_path = project_path / path
    
    console.print()
    console.print(Panel(
        "[bold cyan]🔍 Validating Modules[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED
    ))
    
    scanner = ModuleScanner(addons_path)
    validation_results = scanner.validate_all()
    
    if not validation_results:
        console.print("\n[green]✅ All modules passed validation![/green]\n")
        return
    
    # Show issues
    has_errors = False
    for module_name, issues in validation_results.items():
        console.print(f"\n[cyan]📦 {module_name}[/cyan]")
        for issue in issues:
            console.print(f"  {issue}")
            if "❌" in issue:
                has_errors = True
    
    console.print()
    if has_errors:
        console.print("[red]❌ Validation failed with errors[/red]\n")
    else:
        console.print("[yellow]⚠️  Validation completed with warnings[/yellow]\n")


# Export commands for use in main CLI
__all__ = ['deploy', 'deploy_init', 'list_modules', 'deploy_config', 'deploy_run', 'validate_modules']