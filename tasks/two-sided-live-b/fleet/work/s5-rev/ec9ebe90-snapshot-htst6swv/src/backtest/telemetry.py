"""Batch-scoped maker telemetry: fills and quote decisions harvested after backtest.run().

Nautilus builds strategies from a STRATEGY_PATH string, so the runner cannot inject
a collector. Module-level lists are the channel: clear before backtest.run(),
take immediately after.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FillRecord:
    """One maker fill with the signal/book context frozen at submit time."""

    match_id: int
    token_index: int
    side: str
    price: float
    quantity: float
    submitted_quantity: float
    ts_ns: int
    predicted_delta: float
    fair: float
    book_p_radiant: float
    dataset_market_p: float
    spread: float
    placement: str
    queue_ahead: float
    position_after: float
    order_id: str
    episode_id: int
    level_index: int
    submit_level_index: int
    level_moves: int
    fair_at_fill: float
    signal_age_seconds: float
    gate_reason_at_fill: str
    episode_buy_notional: float
    episode_sell_proceeds: float
    episode_buy_fill_index: int
    position_cost_basis: float
    reserved_buy_notional: float
    is_maker: bool


@dataclass(frozen=True)
class QuoteEvent:
    """One quote decision or order-lifecycle event for a match.

    kinds: submitted, accepted, cancel_request, canceled (cancel sent to the venue),
    cancel_ack (venue confirmed and the reserve is free), rejected, denied, expired,
    no_quote, merge (paired shares credited as cash; the venue does not see it).
    """

    match_id: int
    ts_ns: int
    kind: str
    token_index: int
    side: str
    price: float
    reason: str
    predicted_delta: float
    fair: float
    book_p_radiant: float
    spread: float
    episode_id: int
    order_id: str
    quantity: float
    level_index: int
    submit_level_index: int
    reserved_buy_notional: float


@dataclass(frozen=True)
class UptimeRecord:
    """Seconds a live order was posted during one match, counted on the 1 Hz timer."""

    match_id: int
    live_order_seconds: int


@dataclass(frozen=True)
class MakerRecords:
    """Fills, quote events, and uptime harvested from one batch."""

    fills: tuple[FillRecord, ...]
    quote_events: tuple[QuoteEvent, ...]
    uptimes: tuple[UptimeRecord, ...]


_FILLS: list[FillRecord] = []
_QUOTE_EVENTS: list[QuoteEvent] = []
_UPTIMES: list[UptimeRecord] = []


def clear_records() -> None:
    """Drop any fills, quote events, and uptimes left from a previous batch or arm."""
    _FILLS.clear()
    _QUOTE_EVENTS.clear()
    _UPTIMES.clear()


def record_fill(fill: FillRecord) -> None:
    """Append one fill to the current batch recorder."""
    _FILLS.append(fill)


def record_quote_event(event: QuoteEvent) -> None:
    """Append one quote/order lifecycle event to the current batch recorder."""
    _QUOTE_EVENTS.append(event)


def record_uptime(uptime: UptimeRecord) -> None:
    """Append one match uptime record to the current batch recorder."""
    _UPTIMES.append(uptime)


def take_records() -> MakerRecords:
    """Return and clear the current batch's fills, quote events, and uptimes."""
    records = MakerRecords(
        fills=tuple(_FILLS),
        quote_events=tuple(_QUOTE_EVENTS),
        uptimes=tuple(_UPTIMES),
    )
    clear_records()
    return records
