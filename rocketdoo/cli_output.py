"""CLI presentation adapter for core/ progress callbacks.

Maps the three levels a core/ service reports (RF2.2) to the styles and
marks `rkd` already prints today (`dim` for info, green check for ok, yellow
warning for warn), so moving a command's body into core/ does not change
what the terminal shows. Lives here, not in core/, because it imports rich.
"""

from rich.console import Console
from rich.markup import escape

from rocketdoo.core.service import ProgressCallback

_STYLES = {
    "info": "[dim]{message}[/dim]",
    "ok": "[green]✓[/green] {message}",
    "warn": "[yellow]⚠ {message}[/yellow]",
}


def console_progress(console: Console) -> ProgressCallback:
    """Build a ProgressCallback that prints each message through `console`."""

    def report(message: str, level: str = "info") -> None:
        # The message is plain text (RF2.3) and routinely carries the stderr of
        # pg_restore or psql, which brackets its own tags: `pg_restore:
        # [archiver (db)] ...`. Interpolated raw, rich eats the tag silently,
        # and an unbalanced one raises MarkupError from inside the callback --
        # which the services deliberately do not guard (spec, edge case 1), so
        # it would abort a restore half way through.
        console.print(_STYLES.get(level, _STYLES["info"]).format(message=escape(message)))

    return report
