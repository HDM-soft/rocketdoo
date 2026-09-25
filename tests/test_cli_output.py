"""Tests for the progress-callback vocabulary and its CLI adapter (RF2)."""

from rich.console import Console

from rocketdoo.cli_output import console_progress
from rocketdoo.core.service import reporter, silent


def test_reporter_of_none_is_silent():
    assert reporter(None) is silent


def test_reporter_of_a_callback_returns_it_unchanged():
    def on_progress(message: str, level: str = "info") -> None:
        pass

    assert reporter(on_progress) is on_progress


def test_console_progress_emits_the_three_marks():
    console = Console(width=200, no_color=True)
    with console.capture() as capture:
        report = console_progress(console)
        report("starting up")
        report("step done", "ok")
        report("step failed, continuing", "warn")

    output = " ".join(capture.get().split())
    assert "starting up" in output
    assert "✓ step done" in output
    assert "⚠ step failed, continuing" in output
