"""One Telegram when a token's SELL quotes stay droppable for 30 seconds."""

from collections.abc import Callable

from trader.notify import notify_in_background

SELL_DROP_ALERT_S = 30.0


class SellDropWatch:
    """Alert once per uninterrupted run of dropped SELLs. A placed SELL resets it."""

    def __init__(self, notify: Callable[[str], None] = notify_in_background) -> None:
        self._notify = notify
        self._since: dict[str, float] = {}
        self._alerted: set[str] = set()

    def note(
        self,
        *,
        match_id: str,
        token_id: str,
        quote_size: float,
        held: float,
        reason: str | None,
        now: float,
    ) -> None:
        """Count a dropped SELL, or clear the run when this SELL is allowed out."""
        if reason is None:
            self._since.pop(token_id, None)
            self._alerted.discard(token_id)
            return
        started = self._since.get(token_id)
        if started is None:
            self._since[token_id] = now
            return
        if token_id in self._alerted or now - started < SELL_DROP_ALERT_S:
            return
        self._alerted.add(token_id)
        if reason == "frozen":
            self._notify(f"SELL blocked match={match_id} token={token_id[:12]} reason=frozen")
            return
        self._notify(
            f"SELL blocked match={match_id} token={token_id[:12]}"
            f" want={quote_size:.2f} sqlite={held:.2f}"
        )
