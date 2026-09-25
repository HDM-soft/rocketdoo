"""CLI presentation adapter for core/ progress callbacks.

Maps the three levels a core/ service reports (RF2.2) to the styles and
marks `rkd` already prints today (`dim` for info, green check for ok, yellow
warning for warn), so moving a command's body into core/ does not change
what the terminal shows. Lives here, not in core/, because it imports rich.
"""

from rich.console import Console

from rocketdoo.core.service import ProgressCallback

_STYLES = {
    "info": "[dim]{message}[/dim]",
    "ok": "[green]✓[/green] {message}",
    "warn": "[yellow]⚠ {message}[/yellow]",
}


def console_progress(console: Console) -> ProgressCallback:
    """Build a ProgressCallback that prints each message through `console`."""

    def report(message: str, level: str = "info") -> None:
        console.print(_STYLES.get(level, _STYLES["info"]).format(message=message))

    return report
