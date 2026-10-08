"""Live book/window helpers. Follow300 quoting lives in src/strategy/."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from polymaker.marketdata.orderbook import OrderBook

from shared.constants.dataset import MODEL_START_SECOND
from shared.constants.strategy import QUOTE_GRID
from trader.live_feed import GameSnapshot, MatchPhase
from trader.session_types import RawBookPair, SignalReason


def pin_place_tick(observed: float, current_place: float) -> float:
    """Keep the CLOB place tick when sidecar advertises a finer grid than QUOTE_GRID."""
    if observed < QUOTE_GRID:
        return current_place
    return observed


@dataclass(frozen=True)
class _RawTokenLevels:
    """Raw best bid/ask/mid of one token book plus the first level fault."""

    best_bid: float | None
    best_ask: float | None
    mid: float | None
    reason: SignalReason | None


@dataclass(frozen=True)
class RawPairRead:
    """Raw two-token levels plus the first pair fault, if any."""

    raw: RawBookPair
    reason: SignalReason | None


def _raw_token_levels(book: OrderBook | None) -> _RawTokenLevels:
    """Read raw best bid/ask of one book; classify the failure when unusable."""
    if book is None:
        return _RawTokenLevels(None, None, None, SignalReason.MISSING_BOOK)
    if not book.bids and not book.asks:
        return _RawTokenLevels(None, None, None, SignalReason.MISSING_BOOK)
    if not book.bids or not book.asks:
        return _RawTokenLevels(None, None, None, SignalReason.ONE_SIDED)
    bid = book.best_bid()
    ask = book.best_ask()
    assert bid is not None and ask is not None
    bid_price = bid.price
    ask_price = ask.price
    finite_bid = math.isfinite(bid_price)
    finite_ask = math.isfinite(ask_price)
    if not finite_bid or not finite_ask:
        return _RawTokenLevels(
            bid_price if finite_bid else None,
            ask_price if finite_ask else None,
            None,
            SignalReason.NONFINITE,
        )
    if not (bid_price > 0.0 and ask_price > 0.0 and bid_price < 1.0 and ask_price < 1.0):
        return _RawTokenLevels(bid_price, ask_price, None, SignalReason.OUT_OF_RANGE)
    if bid_price >= ask_price:
        return _RawTokenLevels(bid_price, ask_price, None, SignalReason.CROSSED)
    return _RawTokenLevels(bid_price, ask_price, (bid_price + ask_price) / 2.0, None)


def read_raw_pair(yes_book: OrderBook | None, no_book: OrderBook | None) -> RawPairRead:
    """Read both token books raw; the first fault reason wins."""
    yes_read = _raw_token_levels(yes_book)
    no_read = _raw_token_levels(no_book)
    raw = RawBookPair(
        yes_best_bid=yes_read.best_bid,
        yes_best_ask=yes_read.best_ask,
        yes_mid=yes_read.mid,
        no_best_bid=no_read.best_bid,
        no_best_ask=no_read.best_ask,
        no_mid=no_read.mid,
    )
    return RawPairRead(raw, yes_read.reason or no_read.reason)


def fill_applied_at_utc() -> str:
    """Receipt UTC stamp for a fill that carries no exchange trade time."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def fill_ts_utc(ts: float) -> str:
    """UTC stamp of the Polymarket trade time on Fill.ts, validated by the wallet store."""
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def in_model_window(snapshot: GameSnapshot) -> bool:
    """True before the horn from model start, or during a nonnegative live game."""
    if snapshot.phase is MatchPhase.PRE_HORN:
        return MODEL_START_SECOND <= snapshot.second < 0
    if snapshot.phase is MatchPhase.IN_PROGRESS:
        return snapshot.second >= 0
    return False


def window_reason(snapshot: GameSnapshot, fresh: bool = True) -> SignalReason | None:
    """Return the first reason a live snapshot cannot feed a model decision."""
    if snapshot.finished:
        return SignalReason.FINISHED
    if not in_model_window(snapshot):
        return SignalReason.PRE_HORN
    if snapshot.paused:
        return SignalReason.PAUSED
    if not fresh:
        return SignalReason.STALE
    return None
