from rich.console import Console
from rich.panel import Panel
from pyfiglet import Figlet
from rocketdoo import __version__

console = Console()

def show_welcome():
    # We generated the logo with pyfiglet.
    fig = Figlet(font="slant")
    logo = fig.renderText("RKD as ROCKETDOO")

    subtitle = Figlet(font="big").renderText("by HDMSOFT")

    # We display in console with Rich
    console.print(f"[bold yellow]{logo}[/bold yellow]")
    console.print(f"[bold cyan]{subtitle}[/bold cyan]")

    console.print("=" * 60, style="yellow")
    console.print(
        Panel.fit(
            f"""Welcome to the Odoo Development Framework!

This wizard will guide you through the creation of a dockerized Odoo development environment.
Please answer the following questions to customize your project.
If you don't need to modify the values, just press ENTER.

After finishing the guide, you will be able to run your project
with the command: [bold cyan]rocketdoo up[/bold cyan].

If you have any questions, please check the documentation
or contact the administrator.

Version: [bold green]{__version__}[/bold green]
https://odoo.hdmsoft.com.ar
            """,
            title="[bold yellow]Rocketdoo Init Wizard[/bold yellow]",
            border_style="bright_magenta",
        )
    )

    console.print("\n[bold cyan]Press ENTER to start or CTRL+C to cancel...[/bold cyan]")
    try:
        input()  # Simplemente ignoramos lo que escriba
    except KeyboardInterrupt:
        console.print("\n\n[bold red]Operation cancelled by user.[/bold red]")
        exit(0)