import click
from rich import box
from rich.console import Console
from rich.panel import Panel

from rocketdoo import __version__

console = Console()


@click.command("gui")
@click.option("--port", default=8070, show_default=True, help="Port to run the GUI server on")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host address to bind")
# Not opened by default: under WSL2 the handler is usually a Linux browser
# rendered through WSLg, not the user's Windows browser, so opening one is
# more surprising than helpful. The URL is printed to copy instead.
@click.option("--open/--no-open", "auto_open", default=False, show_default=True, help="Open the browser automatically")
@click.option("--cwd", default=None, type=click.Path(exists=True), help="Project directory (default: current dir)")
def gui_command(port, host, auto_open, cwd):
    """Launch the Rocketdoo web GUI.

    \b
    Examples:
      rkd gui                        # Start on default port 8070
      rkd gui --open                 # Also open a browser on this machine
      rkd gui --port 9090            # Custom port
      rkd gui --cwd /path/to/project # Specify project directory
    """
    import os

    if cwd:
        os.chdir(cwd)

    import uvicorn

    from rocketdoo.gui.server import create_app

    url = f"http://{host}:{port}"
    browser_note = "opening automatically" if auto_open else "copy the URL above (--open to launch one here)"

    console.print()
    console.print(
        Panel(
            f"[bold white]Rocketdoo GUI[/bold white] [dim]v{__version__}[/dim]\n\n"
            f"  [dim]URL:[/dim]      [bold cyan]{url}[/bold cyan]\n"
            f"  [dim]Browser:[/dim]  {browser_note}\n"
            f"  [dim]Stop:[/dim]     [bold]Ctrl+C[/bold] (this terminal stays busy while the server runs)",
            title="[bold blue]RKD GUI[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()

    if auto_open:
        import threading
        import time
        import webbrowser

        def _open():
            time.sleep(1.2)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    # host/port reach the app so CORS can allow exactly the origin the user
    # will open, and nothing else.
    app = create_app(host=host, port=port)
    uvicorn.run(app, host=host, port=port, log_level="error")
