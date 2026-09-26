"""Tests for the progress-callback vocabulary and its CLI adapter (RF2)."""

import io

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


def test_a_message_with_brackets_survives_intact():
    """Progress messages are plain text (RF2.3) and routinely carry the
    stderr of pg_restore or psql, which brackets its own tags. Interpolated
    into rich markup, `[archiver (db)]` disappears from the very diagnostic
    the user needs to understand a failed restore.
    """
    console = Console(file=io.StringIO(), width=200)
    console_progress(console)("pg_restore: [archiver (db)] Error while PROCESSING TOC:", "warn")

    assert "[archiver (db)]" in console.file.getvalue()


def test_an_unbalanced_tag_does_not_raise():
    """A MarkupError here would propagate out of the callback and abort the
    operation it was only meant to narrate: the services deliberately do not
    guard their report() calls (spec, edge case 1), on the promise that the
    adapters are infallible.
    """
    console = Console(file=io.StringIO(), width=200)

    console_progress(console)("ERROR:  relation [/red] does not exist", "warn")

    assert "does not exist" in console.file.getvalue()
