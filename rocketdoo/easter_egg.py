"""`rkd launch`: a hidden command, hinted at in the epilog of `rkd --help`.

The animation draws onto the terminal's alternate screen buffer, the same one
`vim` and `less` use, so the scrollback is left untouched and the terminal is
restored exactly as it was found. The closing message is printed afterwards,
on the normal screen, so it survives for the reader once the rocket is gone.

Frames are plain ANSI rather than `rich`: a cell-addressed grid needs control
over every character position, which markup does not give. The message that
follows is ordinary output and goes through `rich`, like the rest of the CLI.
"""

import os
import random
import shutil
import sys
import time

import click
from rich.console import Console

ZEN = (
    "Studio no genera código.\nGenera arqueología.",
    "Git no ve lo que Studio escribe.\nEl upgrade sí.",
    "Lo que Studio resuelve en cinco minutos,\nla migración lo cobra en cinco horas.",
    "Todo campo x_studio_ es una decisión\nque alguien tomó un martes y nadie anotó.",
    "Un campo sin módulo es un campo sin dueño.",
    '"Lo hice con Studio" es la versión presente\nde "no sé por qué se rompió".',
)

ROCKET = ("    /\\", "   |  |", "   |RK|", "   |D |", "  /|  |\\", " /_|__|_\\")
FLAMES = ((r"  \||/", r"   \/"), (r"   ||", r"   ''"), (r"  \||/", r"   ||"))
CHECKS = ("pre-flight", "ignition", "tower clear")
STARS = ".'`*"

ESC = "\033"
ALT_ON, ALT_OFF = f"{ESC}[?1049h", f"{ESC}[?1049l"
HIDE, SHOW = f"{ESC}[?25l", f"{ESC}[?25h"
HOME, RESET = f"{ESC}[H", f"{ESC}[0m"
CYAN, YELLOW, DIM, GREEN = f"{ESC}[96m", f"{ESC}[93m", f"{ESC}[90m", f"{ESC}[92m"

MIN_WIDTH, MIN_HEIGHT, MAX_HEIGHT = 25, 13, 40
DUMB_TERMS = ("dumb", "unknown")


class Screen:
    """A character grid with a parallel colour grid, rendered as one frame.

    Colour is kept beside the characters instead of inside them so that
    padding and overwriting stay plain string work: an escape sequence in the
    grid would break every width calculation that follows it.
    """

    def __init__(self, width: int, height: int, color: bool = True):
        self.width = width
        self.height = height
        self.color = color
        self.clear()

    def clear(self) -> None:
        self.chars = [[" "] * self.width for _ in range(self.height)]
        self.colors = [[None] * self.width for _ in range(self.height)]

    def put(self, y: int, x: int, text: str, color: str | None = None) -> None:
        if not 0 <= y < self.height:
            return
        for offset, char in enumerate(text):
            if 0 <= x + offset < self.width:
                self.chars[y][x + offset] = char
                self.colors[y][x + offset] = color

    def block(self, y: int, x: int, lines, color: str | None = None) -> None:
        for offset, line in enumerate(lines):
            self.put(y + offset, x, line, color)

    def render(self) -> str:
        rows = []
        for y in range(self.height):
            row, current = [], None
            for x in range(self.width):
                color = self.colors[y][x] if self.color else None
                if color != current:
                    row.append(RESET if color is None else color)
                    current = color
                row.append(self.chars[y][x])
            rows.append("".join(row) + RESET)
        return HOME + "\n".join(rows)


def animate(out, width: int, height: int, color: bool = True, delay: float = 1.0) -> None:
    """Draw the launch onto `out`, which the caller has put in alternate screen."""
    screen = Screen(width, height, color)
    left = width // 2 - 4
    stars = [
        [random.randrange(height), random.randrange(width), random.choice(STARS)] for _ in range(max(12, width * height // 70))
    ]

    def draw_stars() -> None:
        for y, x, char in stars:
            screen.put(y, x, char, DIM)

    def flush() -> None:
        out.write(screen.render())
        out.flush()

    for done in range(len(CHECKS)):
        screen.clear()
        draw_stars()
        for row, check in enumerate(CHECKS[: done + 1]):
            screen.put(height // 2 - 1 + row, max(0, left - 9), f"{check:.<22} ok", GREEN if row == done else DIM)
        flush()
        time.sleep(0.24 * delay)

    for label in ("3", "2", "1", "LIFTOFF"):
        screen.clear()
        draw_stars()
        screen.block(height - 8, left, ROCKET, CYAN)
        screen.put(height // 2 - 2, max(0, (width - len(label)) // 2), label, GREEN if label == "LIFTOFF" else YELLOW)
        flush()
        time.sleep(0.32 * delay)

    y, frame = height - 8, 0
    while y > -len(ROCKET) - 2:
        screen.clear()
        for star in stars:
            star[0] = (star[0] + 1) % height
        draw_stars()
        screen.block(y, left, ROCKET, CYAN)
        screen.block(y + len(ROCKET), left, FLAMES[frame % len(FLAMES)], YELLOW)
        flush()
        time.sleep(0.05 * delay)
        y -= 1 if frame < 4 else (2 if frame < 8 else 3)
        frame += 1

    screen.clear()
    draw_stars()
    flush()
    time.sleep(0.3 * delay)


def launch(delay: float = 1.0) -> None:
    """Animate when the terminal allows it, then print the message either way.

    Without a terminal (a pipe, CI), one too small to hold the frame, or one
    that cannot interpret escapes, the animation is skipped but the message is
    not: `rkd launch | cat` still reads, it just does not move.
    """
    out = sys.stdout
    width, height = shutil.get_terminal_size((80, 24))
    if out.isatty() and width >= MIN_WIDTH and height >= MIN_HEIGHT and os.environ.get("TERM") not in DUMB_TERMS:
        try:
            out.write(ALT_ON + HIDE)
            animate(out, width, min(height, MAX_HEIGHT), not os.environ.get("NO_COLOR"), delay)
        except KeyboardInterrupt:
            pass
        finally:
            out.write(SHOW + ALT_OFF)
            out.flush()

    console = Console()
    console.print()
    console.print("  [dim]-- zen de odoo --[/dim]\n")
    for line in random.choice(ZEN).split("\n"):
        console.print(f"  {line}", style="italic bright_white")
    console.print()


@click.command(name="launch", hidden=True)
def launch_command():
    """Ignition sequence."""
    launch()
