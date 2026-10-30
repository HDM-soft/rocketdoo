"""Tests for the `rkd launch` easter egg.

The command is a joke; the terminal handling is not. It writes raw ANSI and
takes the screen over, so what these tests pin down is that it always hands
the terminal back — cursor included, and also when interrupted — and that it
stays out of sight until somebody types its name.
"""

import io
import os
import re
import shutil
import sys

import pytest
from click.testing import CliRunner

from rocketdoo import easter_egg
from rocketdoo.cli import main
from rocketdoo.easter_egg import (
    ALT_OFF,
    ALT_ON,
    CYAN,
    ESC,
    HOME,
    MIN_HEIGHT,
    MIN_WIDTH,
    SHOW,
    ZEN,
    Screen,
    animate,
    launch,
)

COMMAND_ENTRY = re.compile(r"^\s+launch\b", re.MULTILINE)
ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


def rendered_frames(width, height):
    """Every frame the animation draws, stripped of escapes, as plain rows."""
    out = io.StringIO()
    animate(out, width, height, color=False, delay=0)
    return [ANSI.sub("", frame).split("\n") for frame in out.getvalue().split(HOME) if frame]


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal, so the animation path runs."""

    def isatty(self):
        return True


def fake_terminal(monkeypatch, size=(80, 24)):
    """Swap stdout for a terminal-looking stream, from inside the test body.

    Not a fixture: pytest reassigns sys.stdout when it resumes capture for the
    call phase, which silently undoes any patch made during fixture setup.
    """
    out = FakeTTY()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: os.terminal_size(size))
    return out


class TestStaysHidden:
    def test_not_listed_among_the_commands(self):
        result = CliRunner().invoke(main, ["--help"])
        assert not COMMAND_ENTRY.search(result.output)

    def test_help_carries_the_hint(self):
        result = CliRunner().invoke(main, ["--help"])
        assert "Pre-flight" in result.output

    def test_unknown_command_listing_does_not_leak_it(self):
        result = CliRunner().invoke(main, ["help", "no-such-command"])
        assert "Available commands:" in result.output
        assert not COMMAND_ENTRY.search(result.output)

    def test_runs_when_typed(self):
        result = CliRunner().invoke(main, ["launch"])
        assert result.exit_code == 0
        assert "zen de odoo" in result.output


class TestWithoutATerminal:
    def test_emits_no_escape_sequences(self, capsys):
        launch(delay=0)
        assert ESC not in capsys.readouterr().out

    def test_still_prints_the_message(self, capsys):
        launch(delay=0)
        assert "zen de odoo" in capsys.readouterr().out


class TestHandsTheTerminalBack:
    def test_enters_and_leaves_the_alternate_screen(self, monkeypatch):
        out = fake_terminal(monkeypatch)
        launch(delay=0)
        text = out.getvalue()
        assert text.index(ALT_ON) < text.index(ALT_OFF)
        assert text.rindex(SHOW) > text.rindex(HOME)

    def test_restores_everything_when_interrupted(self, monkeypatch):
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt

        out = fake_terminal(monkeypatch)
        monkeypatch.setattr(easter_egg, "animate", interrupt)
        launch(delay=0)
        text = out.getvalue()
        assert ALT_OFF in text
        assert SHOW in text

    def test_restores_everything_on_any_exception(self, monkeypatch):
        def fail(*args, **kwargs):
            raise RuntimeError("frame render failed")

        out = fake_terminal(monkeypatch)
        monkeypatch.setattr(easter_egg, "animate", fail)
        with pytest.raises(RuntimeError):
            launch(delay=0)
        text = out.getvalue()
        assert ALT_OFF in text
        assert SHOW in text

    def test_no_color_is_honoured(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        out = fake_terminal(monkeypatch)
        launch(delay=0)
        assert CYAN not in out.getvalue()

    def test_skips_the_animation_on_a_tiny_terminal(self, monkeypatch):
        out = fake_terminal(monkeypatch, size=(20, 5))
        launch(delay=0)
        assert ALT_ON not in out.getvalue()


class TestScreen:
    def test_render_has_one_row_per_line(self):
        assert Screen(10, 4).render().count("\n") == 3

    def test_writes_outside_the_grid_are_dropped(self):
        screen = Screen(10, 4)
        screen.put(99, 0, "x")
        screen.put(0, 99, "x")
        screen.put(-1, 0, "x")
        assert "x" not in screen.render()

    def test_color_off_emits_no_color(self):
        screen = Screen(10, 2, color=False)
        screen.put(0, 0, "R", CYAN)
        assert CYAN not in screen.render()

    def test_every_frame_fits_the_smallest_allowed_terminal(self):
        frames = rendered_frames(MIN_WIDTH, MIN_HEIGHT)
        assert frames
        for rows in frames:
            assert len(rows) == MIN_HEIGHT
            assert max(len(row) for row in rows) <= MIN_WIDTH

    def test_the_checklist_is_not_clipped(self):
        """Screen.put clips at the edge, so a too-narrow row loses text silently."""
        frames = rendered_frames(MIN_WIDTH, MIN_HEIGHT)
        checklist = next(rows for rows in frames if any("pre-flight" in row for row in rows))
        assert [row for row in checklist if row.rstrip().endswith(" ok")]

    def test_the_countdown_never_covers_the_rocket(self):
        frames = rendered_frames(MIN_WIDTH, MIN_HEIGHT)
        liftoff = next(rows for rows in frames if any("LIFTOFF" in row for row in rows))
        assert any("/\\" in row for row in liftoff)


class TestZen:
    def test_every_line_has_content(self):
        assert ZEN
        assert all(line.strip() for entry in ZEN for line in entry.split("\n"))
