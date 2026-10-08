import logging


def print_count(name: str, count: int) -> None:
    print(f"{name}: {count}", flush=True)


_FORMAT = "[%(asctime)s] %(message)s"
REPEAT_INTERVAL_S = 30.0


def setup_logging() -> None:
    """Configure root logging for script stdout (no-op if already configured)."""
    logging.basicConfig(level=logging.INFO, format=_FORMAT)
    suppress_http_url_logging()
    logging.getLogger("engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)


def suppress_http_url_logging() -> None:
    """Silence httpx INFO logs, which print request URLs that carry API keys."""
    logging.getLogger("httpx").setLevel(logging.WARNING)


class RepeatingWarning:
    """Log a warning on the first hit and again every `interval_s` with a suppress count."""

    def __init__(self, interval_s: float = REPEAT_INTERVAL_S) -> None:
        """Start with no last message; `interval_s` is wall/monotonic seconds between repeats."""
        self._interval_s = interval_s
        self._last_message: str | None = None
        self._last_log_at: float = 0.0
        self._suppressed = 0

    def emit(self, logger: logging.Logger, message: str, now: float) -> None:
        """Warn `message` now, or count it until `interval_s` has passed.

        A new message flushes the previous suppress count before the new warning.
        """
        last = self._last_message
        if message != last:
            if self._suppressed > 0:
                logger.warning("%s suppressed=%d", last, self._suppressed)
            logger.warning("%s", message)
            self._last_message = message
            self._last_log_at = now
            self._suppressed = 0
            return
        if now - self._last_log_at >= self._interval_s:
            logger.warning("%s suppressed=%d", message, self._suppressed)
            self._last_log_at = now
            self._suppressed = 0
            return
        self._suppressed += 1
