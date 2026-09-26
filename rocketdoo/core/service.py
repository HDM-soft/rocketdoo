"""Shared vocabulary for services in core/: errors and progress reporting.

Every action extracted out of the CLI into core/ speaks this vocabulary, so
the CLI, the GUI and tests can each plug in their own presentation without
the service knowing which one is listening.
"""

from typing import Callable

ProgressCallback = Callable[[str, str], None]
"""`(message, level) -> None`, with `level` in `"info" | "ok" | "warn"`.

A failure that aborts the operation is raised as a ServiceError, never
reported through this callback. The callback itself must be infallible: a
service does not wrap calls to it in a try/except, so an exception raised by
the callback propagates and aborts the operation it was reporting on.
"""


class ServiceError(RuntimeError):
    """The operation cannot proceed at all.

    Carries an optional `hint` describing how to fix the situation, so a
    caller can show it without inspecting the exception further.
    """

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def silent(message: str, level: str = "info") -> None:
    """A ProgressCallback that discards every message."""


def reporter(on_progress: ProgressCallback | None) -> ProgressCallback:
    """Normalize `on_progress` once, at the top of a service call.

    `reporter(None)` returns `silent`, so the rest of the function calls
    `report(...)` unconditionally instead of guarding every call site with
    `if on_progress:`.
    """
    return on_progress if on_progress is not None else silent
