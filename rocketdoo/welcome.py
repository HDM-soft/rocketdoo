# rocketdoo/cli/welcome.py
from rich.console import Console
from rich.panel import Panel
from pyfiglet import Figlet

console = Console()

def show_welcome():
    # Generamos el logo con pyfiglet
    fig = Figlet(font="slant")
    logo = fig.renderText("ROCKETDOO")

    subtitle = Figlet(font="digital").renderText("by HDMSOFT")

    # Mostramos en consola con Rich
    console.print(f"[bold yellow]{logo}[/bold yellow]")
    console.print(f"[bold cyan]{subtitle}[/bold cyan]")

    console.print("=" * 60, style="yellow")
    console.print(
        Panel.fit(
            """Welcome to the Odoo Development environment generator!

This wizard will guide you through the creation of a dockerized Odoo development environment.
Please answer the following questions to customize your project.
If you don't need to modify the values, just press ENTER.

After finishing the guide, you will be able to run your project
with the command: [bold cyan]docker compose up[/bold cyan].

If you have any questions, please check the documentation
or contact the administrator.

Version: [bold green]2.0.0[/bold green]
https://odoo.hdmsoft.com.ar
            """,
            title="[bold yellow]Rocketdoo Init Wizard[/bold yellow]",
            border_style="bright_magenta",
        )
    )

    console.print("\n[bold cyan]Press ENTER to start ...[/bold cyan]")
    input()
