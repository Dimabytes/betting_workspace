"""Tests for the shared logging helpers."""

from typing import Any, cast

from shared.utils.log import RepeatingWarning


def test_repeating_warning_logs_first_then_suppressed_count() -> None:
    """The first hit logs immediately; later hits in the window become suppressed=N."""
    messages: list[str] = []

    class _Logger:
        def warning(self, fmt: str, *args: object) -> None:
            messages.append(fmt % args)

    repeater = RepeatingWarning(interval_s=30.0)
    logger = _Logger()
    repeater.emit(cast(Any, logger), "steam http 400 server=x", 1.0)
    repeater.emit(cast(Any, logger), "steam http 400 server=x", 10.0)
    repeater.emit(cast(Any, logger), "steam http 400 server=x", 20.0)
    repeater.emit(cast(Any, logger), "steam http 400 server=x", 31.0)
    assert messages == ["steam http 400 server=x", "steam http 400 server=x suppressed=2"]
    repeater.emit(cast(Any, logger), "steam http 400 server=x", 32.0)
    repeater.emit(cast(Any, logger), "steam http 500 server=x", 33.0)
    assert messages == [
        "steam http 400 server=x",
        "steam http 400 server=x suppressed=2",
        "steam http 400 server=x suppressed=1",
        "steam http 500 server=x",
    ]
