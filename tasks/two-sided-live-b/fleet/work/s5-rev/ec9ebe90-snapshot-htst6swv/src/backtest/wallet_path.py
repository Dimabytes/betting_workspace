"""Shared-wallet cash and mark-to-market path for one arm of the maker backtest."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from backtest.context import MarketContext
from backtest.marks import EnrichedFill, MidSeries, settlement_value_for_token, token_mid
from backtest.results import MakerMatchResult
from backtest.telemetry import QuoteEvent
from shared.utils.match_time import NS_PER_SECOND, datetime_to_ns, parse_utc

SPARK_POINTS = 100


@dataclass(frozen=True)
class MidEvent:
    """Paired radiant mid for a match that has fills."""

    ts_ns: int
    match_id: int
    market_p: float


@dataclass(frozen=True)
class SettleEvent:
    """Binary settlement of leftover size at game end."""

    ts_ns: int
    match_id: int


@dataclass(frozen=True)
class WalletPath:
    """Shared-wallet cash requirement, trough, overlap, and downsampled equity."""

    required_cash: float
    span_seconds: float
    maps_at_once: int
    lowest_capital: float
    equity_spark: tuple[float, ...]


@dataclass(frozen=True)
class LotKey:
    """One token leg of one match. A pair is two keys, not one overwritten lot."""

    match_id: int
    token_index: int


@dataclass
class _OpenLot:
    """Open size and last mark for one token leg."""

    qty: float
    mark: float


@dataclass(frozen=True)
class MergeCredit:
    """Paired shares returned as one dollar each. The venue never sees this row."""

    ts_ns: int
    match_id: int
    shares: float


TapeEvent = EnrichedFill | MidEvent | MergeCredit | SettleEvent


@dataclass(frozen=True)
class _WalletSample:
    """Cash and open-lot mark at one tape timestamp."""

    ts_ns: int
    cash: float
    mark: float


@dataclass
class _Tape:
    """Running cash and open inventory as the shared fill/mid/settlement tape replays."""

    contexts: Mapping[int, MarketContext]
    cash: float = 0.0
    open_lots: dict[LotKey, _OpenLot] = field(default_factory=dict[LotKey, _OpenLot])

    @property
    def mark(self) -> float:
        """Mark-to-market of open lots at their last fill or mid price."""
        return sum(lot.qty * lot.mark for lot in self.open_lots.values() if lot.qty > 1e-12)

    @property
    def open_maps(self) -> int:
        """Matches that currently hold inventory."""
        return len({key.match_id for key, lot in self.open_lots.items() if lot.qty > 1e-12})

    def apply(self, event: TapeEvent) -> None:
        """Fold one sorted tape event into cash and open lots."""
        match event:
            case EnrichedFill():
                self._apply_fill(event)
            case MidEvent():
                self._apply_mid(event)
            case MergeCredit():
                self._apply_merge(event)
            case SettleEvent():
                self._apply_settle(event)

    def _apply_fill(self, fill: EnrichedFill) -> None:
        notional = fill.price * fill.quantity
        key = LotKey(fill.match_id, fill.token_index)
        lot = self.open_lots.get(key)
        if fill.side == "BUY":
            self.cash -= notional
            if lot is None:
                self.open_lots[key] = _OpenLot(fill.quantity, fill.price)
                return
            lot.qty += fill.quantity
            lot.mark = fill.price
            return
        self.cash += notional
        if lot is None:
            return
        lot.qty -= fill.quantity
        lot.mark = fill.price
        if lot.qty <= 1e-12:
            del self.open_lots[key]

    def _apply_mid(self, event: MidEvent) -> None:
        context = self.contexts.get(event.match_id)
        if context is None:
            return
        for key, lot in self.open_lots.items():
            if key.match_id != event.match_id or lot.qty <= 1e-12:
                continue
            lot.mark = token_mid(event.market_p, key.token_index, context.radiant_token_index)

    def _apply_merge(self, event: MergeCredit) -> None:
        yes = self.open_lots.get(LotKey(event.match_id, 0))
        no = self.open_lots.get(LotKey(event.match_id, 1))
        available = min(
            event.shares,
            0.0 if yes is None else yes.qty,
            0.0 if no is None else no.qty,
        )
        if available <= 1e-12:
            return
        self.cash += available
        for token_index in (0, 1):
            key = LotKey(event.match_id, token_index)
            lot = self.open_lots[key]
            lot.qty -= available
            if lot.qty <= 1e-12:
                del self.open_lots[key]

    def _apply_settle(self, event: SettleEvent) -> None:
        context = self.contexts.get(event.match_id)
        if context is None:
            return
        keys = [key for key in self.open_lots if key.match_id == event.match_id]
        for key in keys:
            lot = self.open_lots.pop(key)
            self.cash += lot.qty * settlement_value_for_token(
                key.token_index, context.radiant_token_index, context.radiant_win
            )


def _event_sort_key(event: TapeEvent) -> tuple[int, int, int]:
    """Order by time, then fill/mid/settle, then match id."""
    match event:
        case EnrichedFill():
            kind = 0
        case MergeCredit():
            kind = 1
        case MidEvent():
            kind = 2
        case SettleEvent():
            kind = 3
    return (event.ts_ns, kind, event.match_id)


def downsample_time_path(samples: Sequence[tuple[int, float]], count: int) -> list[float]:
    """Keep `count` as-of samples on calendar time; pin start, min, and max.

    Bucket 0 is the start vertex; the remaining `count - 1` buckets are equal
    calendar windows over the tape span.
    """
    if not samples or count <= 0:
        return []
    start_ts, start_val = samples[0]
    if count == 1:
        return [start_val]
    windows = count - 1
    span = samples[-1][0] - start_ts

    def bucket_of(index: int) -> int:
        if index == 0:
            return 0
        if span <= 0:
            return 1
        return min(1 + (samples[index][0] - start_ts) * windows // span, windows)

    last_in_bucket: list[float | None] = [None] * count
    for index in range(1, len(samples)):
        last_in_bucket[bucket_of(index)] = samples[index][1]

    path: list[float] = []
    carried = start_val
    for value in last_in_bucket:
        if value is not None:
            carried = value
        path.append(carried)

    # Pin after the carry-forward so an extreme stays in its own bucket only.
    values = [value for _, value in samples]
    for at in (values.index(max(values)), values.index(min(values))):
        path[bucket_of(at)] = values[at]
    path[0] = start_val
    return path


def _build_tape_events(
    results: Sequence[MakerMatchResult],
    ordered_fills: Sequence[EnrichedFill],
    mids: Mapping[int, MidSeries],
    merges: Sequence[MergeCredit],
) -> list[TapeEvent]:
    """Interleave fills with their matches' mids and end-of-game settlements."""
    filled_ids = {fill.match_id for fill in ordered_fills}
    events: list[TapeEvent] = [*ordered_fills, *merges]
    for match_id in filled_ids:
        series = mids.get(match_id)
        if series is None:
            continue
        for timestamp_ns, market_p in zip(series.timestamps_ns, series.market_ps, strict=True):
            events.append(MidEvent(timestamp_ns, match_id, market_p))
    for result in results:
        if result.terminated_early or result.match_id not in filled_ids:
            continue
        settle_ns = datetime_to_ns(parse_utc(result.game_ended_at))
        events.append(SettleEvent(settle_ns, result.match_id))
    events.sort(key=_event_sort_key)
    return events


def calculate_wallet_path(
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    mids: Mapping[int, MidSeries],
    contexts: Mapping[int, MarketContext],
    merges: Sequence[MergeCredit] = (),
) -> WalletPath:
    """Fill-interleaved cash and MTM equity for one arm on one shared wallet."""
    ordered_fills = sorted(fills, key=lambda fill: (fill.ts_ns, fill.match_id))
    if not ordered_fills:
        return WalletPath(0.0, 0.0, 0, 0.0, ())

    events = _build_tape_events(results, ordered_fills, mids, merges)
    tape = _Tape(contexts=contexts)
    min_cash = 0.0
    maps_at_once = 0
    first_ts = ordered_fills[0].ts_ns
    # The deposit vertex precedes every event, so a fill at first_ts cannot overwrite it.
    samples = [_WalletSample(first_ts, 0.0, 0.0)]
    index = 0
    while index < len(events):
        ts_ns = events[index].ts_ns
        while index < len(events) and events[index].ts_ns == ts_ns:
            tape.apply(events[index])
            index += 1
        mark = tape.mark
        if tape.cash == samples[-1].cash and mark == samples[-1].mark:
            continue
        min_cash = min(min_cash, tape.cash)
        maps_at_once = max(maps_at_once, tape.open_maps)
        samples.append(_WalletSample(ts_ns, tape.cash, mark))

    required = -min_cash if min_cash < 0 else 0.0
    path = [(sample.ts_ns, required + sample.cash + sample.mark) for sample in samples]
    return WalletPath(
        required_cash=required,
        span_seconds=(samples[-1].ts_ns - first_ts) / NS_PER_SECOND,
        maps_at_once=maps_at_once,
        lowest_capital=min(equity for _, equity in path),
        equity_spark=tuple(downsample_time_path(path, SPARK_POINTS)),
    )


SettlementTiming = Literal["game_end", "market_close"]


@dataclass(frozen=True)
class ReservePath:
    """Deposit a shared wallet needs when a working BUY reserves its own notional.

    `required_cash` is the peak of `reserved_buy_notional - cumulative_cash_flow`.
    A canceling BUY keeps its reserve until the venue acknowledges the cancel, because
    until then it can still fill. A SELL never frees a canceling BUY's reserve.
    """

    required_cash: float
    peak_reserved: float
    settlement_at: SettlementTiming


@dataclass(frozen=True)
class ReserveOpen:
    """A BUY order started working and locked its full remaining notional."""

    ts_ns: int
    match_id: int
    order_id: str
    notional: float


@dataclass(frozen=True)
class ReserveClose:
    """The venue confirmed a terminal state, so whatever is left of the reserve is free."""

    ts_ns: int
    match_id: int
    order_id: str


ReserveEvent = EnrichedFill | ReserveOpen | ReserveClose | MergeCredit | SettleEvent

_RESERVE_OPEN_KINDS = frozenset({"submitted"})
_RESERVE_CLOSE_KINDS = frozenset({"cancel_ack", "rejected", "denied", "expired"})


def _reserve_sort_key(event: ReserveEvent) -> tuple[int, int, int]:
    """Release, then open, then fill, then settle.

    The strategy acks a cancel, drops the order, re-evaluates, and only then submits a
    replacement, all inside one visited timestamp. Ordering a same-ns release first
    reproduces that instead of charging the wallet for both rungs at once.
    """
    match event:
        case ReserveClose():
            kind = 0
        case ReserveOpen():
            kind = 1
        case EnrichedFill():
            kind = 2
        case MergeCredit():
            kind = 3
        case SettleEvent():
            kind = 4
    return (event.ts_ns, kind, event.match_id)


def build_reserve_events(
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    quote_events: Sequence[QuoteEvent],
    *,
    settlement_at: SettlementTiming,
) -> list[ReserveEvent]:
    """Interleave BUY reserve open/close events with fills and end-of-match settlement."""
    events: list[ReserveEvent] = list(fills)
    for event in quote_events:
        if event.kind == "merge":
            events.append(MergeCredit(event.ts_ns, event.match_id, event.quantity))
            continue
        if event.side != "BUY" or not event.order_id:
            continue
        if event.kind in _RESERVE_OPEN_KINDS:
            events.append(
                ReserveOpen(
                    ts_ns=event.ts_ns,
                    match_id=event.match_id,
                    order_id=event.order_id,
                    notional=event.quantity * event.price,
                )
            )
        elif event.kind in _RESERVE_CLOSE_KINDS:
            events.append(
                ReserveClose(ts_ns=event.ts_ns, match_id=event.match_id, order_id=event.order_id)
            )
    filled_ids = {fill.match_id for fill in fills}
    for result in results:
        if result.terminated_early or result.match_id not in filled_ids:
            continue
        settle_at = result.game_ended_at if settlement_at == "game_end" else result.market_closed_at
        events.append(SettleEvent(datetime_to_ns(parse_utc(settle_at)), result.match_id))
    events.sort(key=_reserve_sort_key)
    return events


def _apply_reserve_fill(
    fill: EnrichedFill,
    *,
    reserved: dict[str, float],
    open_qty: dict[LotKey, _OpenLot],
) -> float:
    notional = fill.price * fill.quantity
    key = LotKey(fill.match_id, fill.token_index)
    lot = open_qty.get(key)
    if fill.side != "BUY":
        if lot is not None:
            lot.qty -= fill.quantity
            if lot.qty <= 1e-12:
                del open_qty[key]
        return notional
    left = reserved.get(fill.order_id)
    if left is not None:
        reserved[fill.order_id] = max(0.0, left - notional)
    if lot is None:
        open_qty[key] = _OpenLot(fill.quantity, fill.price)
    else:
        lot.qty += fill.quantity
    return -notional


def _apply_reserve_merge(event: MergeCredit, open_qty: dict[LotKey, _OpenLot]) -> float:
    """Credit min(recorded pairs, shares actually held on both legs)."""
    yes = open_qty.get(LotKey(event.match_id, 0))
    no = open_qty.get(LotKey(event.match_id, 1))
    available = min(
        event.shares,
        0.0 if yes is None else yes.qty,
        0.0 if no is None else no.qty,
    )
    if available <= 1e-12:
        return 0.0
    for token_index in (0, 1):
        key = LotKey(event.match_id, token_index)
        lot = open_qty[key]
        lot.qty -= available
        if lot.qty <= 1e-12:
            del open_qty[key]
    return available


def calculate_reserve_path(
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    quote_events: Sequence[QuoteEvent],
    contexts: Mapping[int, MarketContext],
    *,
    settlement_at: SettlementTiming,
) -> ReservePath:
    """Peak deposit for one arm on one shared wallet, counting working BUY reserves."""
    events = build_reserve_events(results, fills, quote_events, settlement_at=settlement_at)
    reserved: dict[str, float] = {}
    open_qty: dict[LotKey, _OpenLot] = {}
    cash = 0.0
    required = 0.0
    peak_reserved = 0.0
    for event in events:
        match event:
            case ReserveOpen():
                reserved[event.order_id] = event.notional
            case ReserveClose():
                reserved.pop(event.order_id, None)
            case EnrichedFill():
                cash += _apply_reserve_fill(event, reserved=reserved, open_qty=open_qty)
            case MergeCredit():
                cash += _apply_reserve_merge(event, open_qty)
            case SettleEvent():
                context = contexts.get(event.match_id)
                keys = [key for key in open_qty if key.match_id == event.match_id]
                for key in keys:
                    lot = open_qty.pop(key)
                    if context is not None:
                        cash += lot.qty * settlement_value_for_token(
                            key.token_index, context.radiant_token_index, context.radiant_win
                        )
        locked = sum(reserved.values())
        peak_reserved = max(peak_reserved, locked)
        required = max(required, locked - cash)
    return ReservePath(
        required_cash=required, peak_reserved=peak_reserved, settlement_at=settlement_at
    )
