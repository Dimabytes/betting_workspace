"""Live match tapes: session.jsonl book/model series; game state lives in game_state_replay.

The journal is read tolerantly: blank lines, torn tail lines, kalshi-venue rows,
and mistyped records are skipped, so a mid-write rsync copy still renders.
Resting segments and bid/ask/mid come from core_trace.jsonl with now_ns mapped
to wall seconds-from-horn by the header's opened_wall_s/opened_now_ns pair;
without a trace, journal seconds land on the same axis through the feed
archive's receipt clock. Fill markers keep exchange ts_utc.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from shared.utils.jsonl_io import resolve_jsonl
from shared.utils.match_time import parse_utc
from trader.paths import (
    CORE_TRACE_FILENAME,
    SESSION_JOURNAL_FILENAME,
)
from viewer.archive_read import (
    as_float,
    as_int,
    as_str,
    horn_at_utc,
    iter_json_objects,
    market_block,
    read_match_document,
)
from viewer.game_state_replay import (
    FeedClock,
    load_feed_clock,
)
from viewer.game_state_replay import (
    load_live_game_state as load_live_game_state,
)
from viewer.types import (
    OrderSegment,
    Series,
    TapeMarker,
    TokenTape,
    last_per_second,
    token_predicted_delta,
)

Game = Literal["dota", "lol"]


@dataclass(frozen=True)
class LiveMatch:
    """One live match archive row: table fields plus the loader key."""

    match_id: str
    game: Game
    slug: str
    joined_at_utc: str
    equity: float | None
    net_cash: float | None
    buy_fills: int
    sell_fills: int
    archive_dir: Path


@dataclass
class _SeriesBuilder:
    """Growable (second, value) samples reduced by last_per_second."""

    seconds: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    def add(self, second: float, value: float | None) -> None:
        """Append one sample unless the journal cell is null."""
        if value is not None:
            self.seconds.append(second)
            self.values.append(value)

    def build(self) -> Series:
        """Last sample per truncated game second."""
        return last_per_second(self.seconds, self.values)

    def all_samples(self) -> Series:
        """Every sample in order, so a fill can sit on the bid/ask that produced it."""
        return Series(tuple(self.seconds), tuple(self.values))


@dataclass
class _TokenSignals:
    """One token's five raw sample builders."""

    bid: _SeriesBuilder = field(default_factory=_SeriesBuilder)
    ask: _SeriesBuilder = field(default_factory=_SeriesBuilder)
    mid: _SeriesBuilder = field(default_factory=_SeriesBuilder)
    fair: _SeriesBuilder = field(default_factory=_SeriesBuilder)
    pred: _SeriesBuilder = field(default_factory=_SeriesBuilder)


@dataclass(frozen=True)
class _LiveTokens:
    """Yes/no token ids and which index pays Radiant."""

    yes_token_id: str
    no_token_id: str
    radiant_index: int


@dataclass(frozen=True)
class _TokenEvent:
    """One placed quote or fill reduced to token index and game second."""

    token_index: int
    side: str
    price: float
    size: float
    second: float


@dataclass(frozen=True)
class _JournalFill:
    """One journal fill with the ledger key that pins it to a core order."""

    event: _TokenEvent
    fill_key: str | None
    ts_utc: str | None = None


@dataclass(frozen=True)
class _ReducedJournal:
    """Journal folded into signals, placed quotes, cancel seconds, fills, and late fills."""

    yes: _TokenSignals
    no: _TokenSignals
    placed: tuple[_TokenEvent, ...]
    cancel_seconds: tuple[float, ...]
    fills: tuple[_JournalFill, ...]
    late_records: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class _CorePlace:
    """One accepted core placement in trace order."""

    order_id: str
    token_index: int
    side: str
    price: float
    quantity: float
    level_index: int
    now_ns: int | None = None


@dataclass(frozen=True)
class _CoreTrace:
    """Order lifecycle plus the header's now_ns -> seconds-from-horn offset."""

    places: tuple[_CorePlace, ...]
    ack_order: tuple[str, ...]
    fill_owner: dict[str, str]
    time_offset: float
    cancel_now_ns: dict[str, int] = field(default_factory=dict)
    unsettled_now_ns: dict[str, int] = field(default_factory=dict)


@dataclass
class _CoreAccumulator:
    """Growable core lifecycle while folding the trace."""

    places: list[_CorePlace] = field(default_factory=list)
    rejected: set[str] = field(default_factory=set)
    acks: list[str] = field(default_factory=list)
    fill_owner: dict[str, str] = field(default_factory=dict)
    cancel_now_ns: dict[str, int] = field(default_factory=dict)
    unsettled_now_ns: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _AlignedOrder:
    """One journal placed entry pinned to core lifecycle: real level and exact end."""

    event: _TokenEvent
    order_id: str
    level_index: int
    end: float


def _is_polymarket(record: dict[str, object]) -> bool:
    """Missing/null venue is polymarket (schema 1-5); kalshi rows are skipped."""
    venue = record.get("venue")
    return venue is None or venue == "polymarket"


def _read_game(document: dict[str, object]) -> Game | None:
    """dota when `game` is missing (schema 3/4); None when it names no known title."""
    raw_game = document.get("game")
    if raw_game is None or raw_game == "dota":
        return "dota"
    if raw_game == "lol":
        return "lol"
    return None


def _live_tokens(document: dict[str, object]) -> _LiveTokens | None:
    """Token ids and the Radiant index, or None when the market block is unusable."""
    market = market_block(document)
    if market is None:
        return None
    yes_token_id = as_str(market.get("yes_token_id"))
    no_token_id = as_str(market.get("no_token_id"))
    yes_is_radiant = market.get("yes_is_radiant")
    if yes_token_id is None or no_token_id is None or not isinstance(yes_is_radiant, bool):
        return None
    return _LiveTokens(yes_token_id, no_token_id, 0 if yes_is_radiant else 1)


def read_live_match(archive_dir: Path) -> LiveMatch | None:
    """One table row for a live archive dir, or None when it is not a live match."""
    document = read_match_document(archive_dir)
    if document is None:
        return None
    game = _read_game(document)
    market = market_block(document)
    slug = as_str(market.get("market_slug")) if market is not None else None
    match_id = as_str(document.get("match_id"))
    joined = as_str(document.get("joined_at_utc"))
    if game is None or slug is None or match_id is None or joined is None:
        return None
    records = list(iter_json_objects(archive_dir / SESSION_JOURNAL_FILENAME))
    if not records or records[0].get("kind") != "session_start":
        return None
    if records[0].get("execution_mode", "paper") != "live":
        return None
    equity: float | None = None
    net_cash: float | None = None
    buys = 0
    sells = 0
    for record in records:
        kind = record.get("kind")
        if kind == "session_end" and _is_polymarket(record):
            equity = as_float(record.get("equity"))
            net_cash = as_float(record.get("net_cash"))
        elif kind in ("fill", "late_fill") and _is_polymarket(record):
            if record.get("side") == "BUY":
                buys += 1
            elif record.get("side") == "SELL":
                sells += 1
    return LiveMatch(match_id, game, slug, joined, equity, net_cash, buys, sells, archive_dir)


def list_live_matches(root: Path) -> tuple[LiveMatch, ...]:
    """Live match rows under `root`, freshest `joined_at_utc` first."""
    if not root.is_dir():
        return ()
    matches: list[LiveMatch] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name == "wallet":
            continue
        match = read_live_match(child)
        if match is not None:
            matches.append(match)
    matches.sort(key=lambda match: match.joined_at_utc, reverse=True)
    return tuple(matches)


def _token_index(token_id: object, tokens: _LiveTokens) -> int | None:
    """0 for yes, 1 for no, None for anything else."""
    if token_id == tokens.yes_token_id:
        return 0
    if token_id == tokens.no_token_id:
        return 1
    return None


def _append_signal(
    yes: _TokenSignals,
    no: _TokenSignals,
    record: dict[str, object],
    radiant_index: int,
    clock: FeedClock,
) -> None:
    """Append one polymarket signal's book, fair, and pred samples, skipping nulls."""
    second = as_int(record.get("second"))
    if second is None:
        return
    at = clock.wall_at(second)
    yes.bid.add(at, as_float(record.get("yes_best_bid")))
    yes.ask.add(at, as_float(record.get("yes_best_ask")))
    yes.mid.add(at, as_float(record.get("yes_mid")))
    no.bid.add(at, as_float(record.get("no_best_bid")))
    no.ask.add(at, as_float(record.get("no_best_ask")))
    no.mid.add(at, as_float(record.get("no_mid")))
    market_p = as_float(record.get("market_p_radiant"))
    radiant_fair = as_float(record.get("radiant_fair"))
    if radiant_fair is None:
        fair_yes: float | None = None
        fair_no: float | None = None
    elif radiant_index == 0:
        fair_yes = radiant_fair
        fair_no = 1.0 - radiant_fair
    else:
        fair_yes = 1.0 - radiant_fair
        fair_no = radiant_fair
    yes.fair.add(at, fair_yes)
    no.fair.add(at, fair_no)
    if radiant_fair is None or market_p is None:
        return
    delta = radiant_fair - market_p
    yes.pred.add(at, token_predicted_delta(delta, 0, radiant_index) * 100.0)
    no.pred.add(at, token_predicted_delta(delta, 1, radiant_index) * 100.0)


def _placed_quotes(
    record: dict[str, object], tokens: _LiveTokens, clock: FeedClock
) -> list[_TokenEvent]:
    """Placed lines of one quote record with known tokens and numeric fields."""
    second = as_int(record.get("second"))
    placed = record.get("placed")
    if second is None or not isinstance(placed, list):
        return []
    events: list[_TokenEvent] = []
    for entry in cast(list[object], placed):
        if not isinstance(entry, dict):
            continue
        typed_entry = cast(dict[str, object], entry)
        index = _token_index(typed_entry.get("token_id"), tokens)
        side = as_str(typed_entry.get("side"))
        price = as_float(typed_entry.get("price"))
        size = as_float(typed_entry.get("size"))
        if index is None or side not in ("BUY", "SELL") or price is None or size is None:
            continue
        events.append(_TokenEvent(index, side, price, size, clock.wall_at(second)))
    return events


def _journal_fill(
    record: dict[str, object], tokens: _LiveTokens, clock: FeedClock
) -> _JournalFill | None:
    """One fill/late_fill with its ledger key, or None when unusable."""
    index = _token_index(record.get("token_id"), tokens)
    side = as_str(record.get("side"))
    price = as_float(record.get("price"))
    size = as_float(record.get("size"))
    second = as_int(record.get("second"))
    if (
        index is None
        or side not in ("BUY", "SELL")
        or price is None
        or size is None
        or second is None
    ):
        return None
    event = _TokenEvent(index, side, price, size, clock.wall_at(second))
    return _JournalFill(event, as_str(record.get("fill_key")), as_str(record.get("ts_utc")))


def _fill_plot_second(fill: _JournalFill, horn_at: datetime | None) -> float:
    """Exchange ts_utc as seconds from horn when both exist; else the mapped second."""
    if fill.ts_utc is not None and horn_at is not None:
        try:
            return (parse_utc(fill.ts_utc) - horn_at).total_seconds()
        except ValueError:
            pass
    return fill.event.second


def _canceled_seconds(record: dict[str, object], clock: FeedClock) -> list[float]:
    """One quote-record second per canceled id, in batch order."""
    second = as_int(record.get("second"))
    canceled = record.get("canceled")
    if second is None or not isinstance(canceled, list):
        return []
    return [clock.wall_at(second)] * len(cast(list[object], canceled))


def _tape_token_indexes(
    fills: Sequence[_TokenEvent], placed: Sequence[_TokenEvent], radiant_index: int
) -> tuple[int, ...]:
    """Taped tokens: BUY fills plus quoted tokens; Radiant when the journal never quoted."""
    indexes = sorted(
        {fill.token_index for fill in fills if fill.side == "BUY"}
        | {event.token_index for event in placed}
    )
    return tuple(indexes) if indexes else (radiant_index,)


def _fill_markers(
    fills: Sequence[_JournalFill], token_index: int, horn_at: datetime | None = None
) -> tuple[TapeMarker, ...]:
    """Fill markers for one token; X uses ts_utc from horn when available."""
    return tuple(
        TapeMarker(
            _fill_plot_second(fill, horn_at),
            fill.event.price,
            fill.event.side,
            "fill",
            -1,
            fill.event.size,
        )
        for fill in fills
        if fill.event.token_index == token_index
    )


def _submit_markers(
    placed: Sequence[_TokenEvent], levels: Sequence[int], token_index: int
) -> tuple[TapeMarker, ...]:
    """Journal submits with core levels where aligned, else the gray -1 rung."""
    markers: list[TapeMarker] = []
    for position, event in enumerate(placed):
        if event.token_index != token_index:
            continue
        level = levels[position] if position < len(levels) else -1
        markers.append(
            TapeMarker(event.second, event.price, event.side, "submit", level, event.size)
        )
    return tuple(markers)


def _core_place(entry: object, now_ns: int | None) -> _CorePlace | None:
    """One plan placement, or None when fields are missing or mistyped."""
    if not isinstance(entry, dict):
        return None
    fields = cast(dict[str, object], entry)
    order_id = as_str(fields.get("order_id"))
    token_index = as_int(fields.get("token_index"))
    side = as_str(fields.get("side"))
    price = as_float(fields.get("price"))
    quantity = as_float(fields.get("quantity"))
    if (
        order_id is None
        or token_index is None
        or side not in ("BUY", "SELL")
        or price is None
        or quantity is None
    ):
        return None
    level = as_int(fields.get("level_index"))
    return _CorePlace(
        order_id,
        token_index,
        side,
        price,
        quantity,
        level if level is not None else -1,
        now_ns,
    )


def _fold_core_event(acc: _CoreAccumulator, event: dict[str, object], now_ns: int | None) -> None:
    """Fold one trace event's reject, cancel ack, or fill into the accumulator."""
    event_type = event.get("type")
    if event_type == "OrderRejected":
        order_id = as_str(event.get("order_id"))
        if order_id is not None:
            acc.rejected.add(order_id)
    elif event_type == "CancelAck":
        order_id = as_str(event.get("order_id"))
        if order_id is not None:
            acc.acks.append(order_id)
            if now_ns is not None and order_id not in acc.cancel_now_ns:
                acc.cancel_now_ns[order_id] = now_ns
    elif event_type == "CancelUnsettled":
        # BUY cancels ack here, not in acks: journal cancel_seconds maps
        # positionally on SELL CancelAck only. First ack is the off-book moment;
        # the venue can repeat it, even after BuySettled.
        order_id = as_str(event.get("order_id"))
        if order_id is not None and now_ns is not None and order_id not in acc.unsettled_now_ns:
            acc.unsettled_now_ns[order_id] = now_ns
    elif event_type == "Fill":
        fill_id = as_str(event.get("fill_id"))
        order_id = as_str(event.get("order_id"))
        if fill_id is not None and order_id is not None and fill_id not in acc.fill_owner:
            acc.fill_owner[fill_id] = order_id


def _header_time_offset(record: dict[str, object], horn_at: datetime) -> float | None:
    """now_ns -> seconds-from-horn offset from a trace header row, else None."""
    if record.get("kind") != "header":
        return None
    opened_wall_s = as_float(record.get("opened_wall_s"))
    opened_now_ns = as_float(record.get("opened_now_ns"))
    if opened_wall_s is None or opened_now_ns is None:
        return None
    return opened_wall_s - horn_at.timestamp() - opened_now_ns / 1_000_000_000.0


def _fold_trace_row(acc: _CoreAccumulator, record: dict[str, object]) -> None:
    """Fold one event row's lifecycle event and plan places into the accumulator."""
    now_ns = as_int(record.get("now_ns"))
    event = record.get("event")
    if isinstance(event, dict):
        _fold_core_event(acc, cast(dict[str, object], event), now_ns)
    plan = record.get("plan")
    if not isinstance(plan, dict):
        return
    places = cast(dict[str, object], plan).get("places")
    if not isinstance(places, list):
        return
    for entry in cast(list[object], places):
        place = _core_place(entry, now_ns)
        if place is not None:
            acc.places.append(place)


def _parse_core_trace(path: Path, horn_at: datetime) -> _CoreTrace | None:
    """Order lifecycle from core_trace.jsonl, or None when missing or unanchored."""
    path = resolve_jsonl(path)
    if not path.is_file():
        return None
    acc = _CoreAccumulator()
    time_offset: float | None = None
    for record in iter_json_objects(path):
        if time_offset is None:
            time_offset = _header_time_offset(record, horn_at)
        if record.get("kind") == "event":
            _fold_trace_row(acc, record)
    if time_offset is None:
        return None
    accepted = tuple(place for place in acc.places if place.order_id not in acc.rejected)
    return _CoreTrace(
        accepted,
        tuple(acc.acks),
        acc.fill_owner,
        time_offset,
        dict(acc.cancel_now_ns),
        dict(acc.unsettled_now_ns),
    )


def _ns_to_plot_second(now_ns: int, offset: float) -> float:
    """Map core monotonic now_ns onto seconds-from-horn via the header offset."""
    return now_ns / 1_000_000_000.0 + offset


def _places_match_prefix(core_places: Sequence[_CorePlace], placed: Sequence[_TokenEvent]) -> bool:
    """True when core places prefix-match journal entries on (token, side, price, size)."""
    if len(core_places) > len(placed):
        return False
    for place, event in zip(core_places, placed, strict=False):
        if (
            place.token_index != event.token_index
            or place.side != event.side
            or place.price != event.price
            or place.quantity != event.size
        ):
            return False
    return True


def _order_ends(
    core: _CoreTrace,
    cancel_seconds: Sequence[float],
    fill_seconds: dict[str, float],
    window_end: float,
) -> dict[str, float]:
    """Cancel/unsettled second, else last fill second, else window end, per core order."""
    ack_second: dict[str, float] = {}
    for position, order_id in enumerate(core.ack_order):
        if order_id not in ack_second:
            cancel_ns = core.cancel_now_ns.get(order_id)
            if cancel_ns is not None:
                ack_second[order_id] = _ns_to_plot_second(cancel_ns, core.time_offset)
            else:
                ack_second[order_id] = cancel_seconds[position]
    unsettled_second = {
        order_id: _ns_to_plot_second(now_ns, core.time_offset)
        for order_id, now_ns in core.unsettled_now_ns.items()
    }
    last_fill: dict[str, float] = {}
    for fill_id, order_id in core.fill_owner.items():
        second = fill_seconds.get(fill_id)
        if second is not None and second > last_fill.get(order_id, -1.0):
            last_fill[order_id] = second
    ends: dict[str, float] = {}
    for place in core.places:
        if place.order_id in ack_second:
            ends[place.order_id] = ack_second[place.order_id]
        elif place.order_id in unsettled_second:
            ends[place.order_id] = unsettled_second[place.order_id]
        elif place.order_id in last_fill:
            ends[place.order_id] = last_fill[place.order_id]
        else:
            ends[place.order_id] = window_end
    return ends


def _align_core_orders(
    core: _CoreTrace,
    placed: Sequence[_TokenEvent],
    cancel_seconds: Sequence[float],
    fill_seconds: dict[str, float],
    window_end: float,
) -> tuple[_AlignedOrder, ...] | None:
    """Pin each core place to the journal entry at the same position, or None on any drift.

    Core acks map positionally onto journal canceled seconds (venue roundtrips
    are awaited sequentially, so batches never interleave); fills map by ledger
    key. Place starts and cancel ends sit on the header-anchored wall axis so
    submit/fill order matches exchange time. An end before its start is clamped:
    feed seconds can wobble backward by a second between batches.
    """
    if len(core.ack_order) > len(cancel_seconds):
        return None
    if not _places_match_prefix(core.places, placed):
        return None
    ends = _order_ends(core, cancel_seconds, fill_seconds, window_end)
    aligned: list[_AlignedOrder] = []
    for place, event in zip(core.places, placed, strict=False):
        start = event.second
        if place.now_ns is not None:
            start = _ns_to_plot_second(place.now_ns, core.time_offset)
            event = _TokenEvent(event.token_index, event.side, event.price, event.size, start)
        end = ends[place.order_id]
        if end < event.second:
            end = event.second
        aligned.append(_AlignedOrder(event, place.order_id, place.level_index, end))
    return tuple(aligned)


def _core_segments(
    aligned: tuple[_AlignedOrder, ...] | None, token_index: int
) -> tuple[OrderSegment, ...]:
    """Exact rests for one token; empty when the trace is missing or drifted."""
    if aligned is None:
        return ()
    return tuple(
        OrderSegment(
            order_id=order.order_id,
            side=order.event.side,
            token_index=token_index,
            level_index=order.level_index,
            price=order.event.price,
            start_s=order.event.second,
            end_s=order.end,
        )
        for order in aligned
        if order.event.token_index == token_index
    )


def _reduce_journal(
    records: Iterable[dict[str, object]], tokens: _LiveTokens, clock: FeedClock
) -> _ReducedJournal:
    """Fold journal records into signals, placed quotes, cancel seconds, and fills."""
    yes = _TokenSignals()
    no = _TokenSignals()
    placed: list[_TokenEvent] = []
    cancel_seconds: list[float] = []
    fills: list[_JournalFill] = []
    late_records: list[dict[str, object]] = []
    for record in records:
        if not _is_polymarket(record):
            continue
        kind = record.get("kind")
        if kind == "signal":
            _append_signal(yes, no, record, tokens.radiant_index, clock)
        elif kind == "quote":
            placed.extend(_placed_quotes(record, tokens, clock))
            cancel_seconds.extend(_canceled_seconds(record, clock))
        elif kind == "fill":
            fill = _journal_fill(record, tokens, clock)
            if fill is not None:
                fills.append(fill)
        elif kind == "late_fill":
            late_records.append(record)
    return _ReducedJournal(
        yes, no, tuple(placed), tuple(cancel_seconds), tuple(fills), tuple(late_records)
    )


def _window_end(reduced: _ReducedJournal) -> float:
    """Last plot second across signals, placed quotes, and fills."""
    samples = [event.second for event in reduced.placed]
    samples.extend(fill.event.second for fill in reduced.fills)
    for signals in (reduced.yes, reduced.no):
        for builder in (signals.bid, signals.ask, signals.mid, signals.fair, signals.pred):
            samples.extend(builder.seconds)
    return max(samples, default=0.0)


def _with_late_fills(
    reduced: _ReducedJournal, tokens: _LiveTokens, window_end: float, clock: FeedClock
) -> list[_JournalFill]:
    """Fills plus late fills pinned at the window end (their journal second is a 0 sentinel)."""
    fills = list(reduced.fills)
    for record in reduced.late_records:
        fill = _journal_fill(record, tokens, clock)
        if fill is not None:
            event = fill.event
            pinned = _TokenEvent(event.token_index, event.side, event.price, event.size, window_end)
            fills.append(_JournalFill(pinned, fill.fill_key, fill.ts_utc))
    return fills


def _signal_plot_seconds(path: Path, time_offset: float) -> list[float]:
    """Plot seconds for each non-null SignalUpdate, in core_trace order."""
    seconds: list[float] = []
    for record in iter_json_objects(path):
        if record.get("kind") != "event":
            continue
        event = record.get("event")
        if not isinstance(event, dict):
            continue
        event_obj = cast(dict[str, object], event)
        if event_obj.get("type") != "SignalUpdate":
            continue
        signal = event_obj.get("signal")
        if not isinstance(signal, dict):
            continue
        signal_obj = cast(dict[str, object], signal)
        now_ns = as_int(record.get("now_ns"))
        if now_ns is None:
            now_ns = as_int(event_obj.get("now_ns"))
        if now_ns is None:
            now_ns = as_int(signal_obj.get("received_ns"))
        if now_ns is None:
            continue
        seconds.append(_ns_to_plot_second(now_ns, time_offset))
    return seconds


def _series_on_signal_times(builder: _SeriesBuilder, plot_seconds: list[float]) -> Series:
    """Retimed fair/pred: journal values, SignalUpdate X. Prefix-zip if counts drift."""
    count = min(len(builder.values), len(plot_seconds))
    if count == 0:
        return builder.build()
    return last_per_second(list(plot_seconds[:count]), list(builder.values[:count]))


def _add_book_token_quotes(
    by_token: dict[int, _TokenSignals], entry: object, plot_second: float
) -> None:
    """Fold one BookUpdate token row into bid/ask/mid builders."""
    if not isinstance(entry, dict):
        return
    fields = cast(dict[str, object], entry)
    token_index = as_int(fields.get("token_index"))
    if token_index is None:
        return
    bid = as_float(fields.get("bid"))
    ask = as_float(fields.get("ask"))
    signals = by_token.setdefault(token_index, _TokenSignals())
    signals.bid.add(plot_second, bid)
    signals.ask.add(plot_second, ask)
    if bid is not None and ask is not None:
        signals.mid.add(plot_second, (bid + ask) / 2.0)


def _book_update_tokens(event: dict[str, object]) -> list[object] | None:
    """Token rows from a BookUpdate event, or None when missing."""
    books = event.get("books")
    if not isinstance(books, dict):
        return None
    tokens = cast(dict[str, object], books).get("tokens")
    if not isinstance(tokens, list):
        return None
    return cast(list[object], tokens)


def _book_signals_from_core(path: Path, time_offset: float) -> dict[int, _TokenSignals]:
    """Bid/ask/mid from BookUpdate rows, X on the same monotonic→horn axis as fills."""
    by_token: dict[int, _TokenSignals] = {}
    for record in iter_json_objects(path):
        if record.get("kind") != "event":
            continue
        event = record.get("event")
        if not isinstance(event, dict):
            continue
        event_obj = cast(dict[str, object], event)
        if event_obj.get("type") != "BookUpdate":
            continue
        now_ns = as_int(record.get("now_ns"))
        if now_ns is None:
            now_ns = as_int(event_obj.get("now_ns"))
        if now_ns is None:
            continue
        tokens = _book_update_tokens(event_obj)
        if tokens is None:
            continue
        plot_second = _ns_to_plot_second(now_ns, time_offset)
        for entry in tokens:
            _add_book_token_quotes(by_token, entry, plot_second)
    return by_token


def load_live_tapes(archive_dir: Path) -> tuple[TokenTape, ...]:
    """One tape per bought token from journal rows, with core-trace rests when aligned."""
    document = read_match_document(archive_dir)
    if document is None:
        return ()
    tokens = _live_tokens(document)
    if tokens is None:
        return ()
    horn_at = horn_at_utc(document)
    clock = load_feed_clock(archive_dir)
    reduced = _reduce_journal(
        iter_json_objects(archive_dir / SESSION_JOURNAL_FILENAME), tokens, clock
    )
    window_end = _window_end(reduced)
    fills = _with_late_fills(reduced, tokens, window_end, clock)
    fill_events = [fill.event for fill in fills]
    # Segment ends use exchange ts_utc when present, else the feed-mapped journal second.
    fill_seconds = {
        fill.fill_key: _fill_plot_second(fill, horn_at)
        for fill in fills
        if fill.fill_key is not None
    }
    core_path = resolve_jsonl(archive_dir / CORE_TRACE_FILENAME)
    core = _parse_core_trace(core_path, horn_at) if horn_at is not None else None
    aligned = (
        _align_core_orders(
            core,
            reduced.placed,
            reduced.cancel_seconds,
            fill_seconds,
            window_end,
        )
        if core is not None
        else None
    )
    levels = [order.level_index for order in aligned] if aligned is not None else []
    tapes: list[TokenTape] = []
    core_books = _book_signals_from_core(core_path, core.time_offset) if core is not None else {}
    signal_times = _signal_plot_seconds(core_path, core.time_offset) if core is not None else None
    for token_index in _tape_token_indexes(fill_events, reduced.placed, tokens.radiant_index):
        signals = reduced.yes if token_index == 0 else reduced.no
        side_name = "radiant" if token_index == tokens.radiant_index else "dire"
        book = core_books.get(token_index)
        if book is not None and book.bid.seconds:
            bid = book.bid.all_samples()
            ask = book.ask.all_samples()
            mid = book.mid.all_samples()
        else:
            bid = signals.bid.build()
            ask = signals.ask.build()
            mid = signals.mid.build()
        if signal_times is not None:
            fair = _series_on_signal_times(signals.fair, signal_times)
            pred = _series_on_signal_times(signals.pred, signal_times)
        else:
            fair = signals.fair.build()
            pred = signals.pred.build()
        if aligned is not None:
            submits = tuple(
                TapeMarker(
                    order.event.second,
                    order.event.price,
                    order.event.side,
                    "submit",
                    order.level_index,
                    order.event.size,
                )
                for order in aligned
                if order.event.token_index == token_index
            )
        else:
            submits = _submit_markers(reduced.placed, levels, token_index)
        tapes.append(
            TokenTape(
                token_index=token_index,
                side_name=side_name,
                mid=mid,
                bid=bid,
                ask=ask,
                fair=fair,
                pred_cents=pred,
                segments=_core_segments(aligned, token_index),
                submits=submits,
                fills=_fill_markers(fills, token_index, horn_at),
            )
        )
    return tuple(tapes)
