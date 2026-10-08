"""Dropped SELL quotes alert once after 30 seconds, and a placed SELL clears the run."""

from trader.sell_drop_watch import SellDropWatch


def _note(watch: SellDropWatch, sent_at: float, *, reason: str | None) -> None:
    watch.note(
        match_id="8959",
        token_id="token-123456789",
        quote_size=754.06,
        held=0.0,
        reason=reason,
        now=sent_at,
    )


def test_sell_drop_watch_alerts_once_at_thirty_seconds_and_resets() -> None:
    sent: list[str] = []
    watch = SellDropWatch(sent.append)
    _note(watch, 0.0, reason="size")
    _note(watch, 29.0, reason="size")
    assert sent == []
    _note(watch, 30.0, reason="size")
    assert sent == ["SELL blocked match=8959 token=token-123456 want=754.06 sqlite=0.00"]
    _note(watch, 40.0, reason="size")
    assert len(sent) == 1
    _note(watch, 41.0, reason=None)
    _note(watch, 41.0, reason="size")
    _note(watch, 70.0, reason="size")
    assert len(sent) == 1
    _note(watch, 71.0, reason="size")
    assert len(sent) == 2


def test_frozen_sell_does_not_claim_a_size_gap() -> None:
    sent: list[str] = []
    watch = SellDropWatch(sent.append)
    _note(watch, 0.0, reason="frozen")
    _note(watch, 30.0, reason="frozen")
    assert sent == ["SELL blocked match=8959 token=token-123456 reason=frozen"]
