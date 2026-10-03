import socket
import sys

import click
from rich import box
from rich.console import Console
from rich.panel import Panel

from rocketdoo import __version__

console = Console()


def _port_unavailable(host: str, port: int) -> bool:
    """Whether binding (host, port) would fail, asked the way uvicorn asks it.

    No SO_REUSEADDR: the probe has to fail where the real bind would fail, and
    setting it would let the probe succeed on a port a listener still holds.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return True
    return False


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

    # Checked before anything is printed. Every run mints a fresh token and
    # the panel below hands it to the user as the way in -- but uvicorn only
    # tries to bind at the very end, so a busy port used to produce a full
    # success panel, a brand new token, and then an error buried underneath.
    # The user copies the newest URL, reaches the server that is actually
    # listening, and gets a 401 for a token it never issued.
    #
    # A bind probe rather than core.port_validation.is_port_in_use(): that one
    # answers "is something reachable on localhost:port", which is a near
    # neighbour of the question but not it. This tries exactly what uvicorn is
    # about to try, on exactly the address it will use.
    if _port_unavailable(host, port):
        console.print()
        console.print(f"[red]✗[/red] Port [bold]{port}[/bold] on {host} is already in use.")
        console.print("[dim]  If another [cyan]rkd gui[/cyan] is running, open the URL that one printed:[/dim]")
        console.print("[dim]  its token is the only one the listening server accepts.[/dim]")
        console.print(f"[dim]  Otherwise pick a free port: [cyan]rkd gui --port {port + 1}[/cyan][/dim]")
        console.print()
        sys.exit(1)

    # Built after the check so the panel never shows a token for a server
    # that will not come up.
    app = create_app(host=host, port=port)
    url = f"http://{host}:{port}/?token={app.state.rkd_token}"
    browser_note = "opening automatically" if auto_open else "not opened (--open to launch one here)"

    console.print()
    console.print(
        Panel(
            f"[bold white]Rocketdoo GUI[/bold white] [dim]v{__version__}[/dim]\n\n"
            f"  [dim]Browser:[/dim]  {browser_note}\n"
            f"  [dim]Stop:[/dim]     [bold]Ctrl+C[/bold] (this terminal stays busy while the server runs)",
            title="[bold blue]RKD GUI[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    # Outside the panel and unwrapped: the tokenised URL is long, and inside a
    # bordered box Rich splits it across lines, which breaks copy-paste — the
    # one thing the user has to do with it.
    console.print("\n  Open this URL (it carries the session token):\n")
    console.print(f"  [bold cyan]{url}[/bold cyan]", soft_wrap=True, highlight=False)
    console.print()

    if auto_open:
        # The tokenised URL becomes an argument of the browser helper process
        # (webbrowser resolves to Popen(["xdg-open", url]) on Linux), so it is
        # briefly visible in `ps aux` to any local user. Same channel #142
        # closed for VPS passwords, here as an accepted trade-off of an opt-in
        # flag: documented in SECURITY.md rather than silently accepted.
        console.print("[dim]  note: --open passes the tokenised URL to the browser helper,[/dim]")
        console.print("[dim]        which makes it briefly visible in the process list.[/dim]")
        console.print()

        import threading
        import time
        import webbrowser

        def _open():
            time.sleep(1.2)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    # log_level="error" also keeps the token out of an access log: the query
    # string would otherwise be recorded on every request.
    uvicorn.run(app, host=host, port=port, log_level="error")
