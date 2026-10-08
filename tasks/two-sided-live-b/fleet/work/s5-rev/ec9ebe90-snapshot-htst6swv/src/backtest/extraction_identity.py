"""Follow300 extraction-identity projection: fills, place/cancel, episode/level, reserves."""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from backtest.postprocess import read_fills_checkpoint
from backtest.results import (
    MakerMatchResult,
    read_quote_events_checkpoint,
    read_results_checkpoint,
)
from backtest.telemetry import FillRecord, QuoteEvent
from shared.utils.json_io import read_json, write_json


def sha256_text(text: str) -> str:
    """Hex sha256 of one UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


PLACE_CANCEL_KINDS = frozenset(
    {
        "submitted",
        "accepted",
        "cancel_request",
        "canceled",
        "cancel_ack",
        "rejected",
        "denied",
        "expired",
    }
)
MONEY_DECIMALS = 8


@dataclass(frozen=True)
class FillIdentity:
    """One fill's extraction fields."""

    match_id: int
    ts_ns: int
    order_id: str
    side: str
    token_index: int
    price: float
    quantity: float
    submitted_quantity: float
    episode_id: int
    level_index: int
    submit_level_index: int
    reserved_buy_notional: float


@dataclass(frozen=True)
class PlaceCancelIdentity:
    """One place or cancel lifecycle event's extraction fields."""

    match_id: int
    ts_ns: int
    kind: str
    order_id: str
    side: str
    token_index: int
    price: float
    quantity: float
    episode_id: int
    level_index: int
    submit_level_index: int
    reserved_buy_notional: float
    reason: str


@dataclass(frozen=True)
class ReplayChecks:
    """PnL and counts reported next to identity; not the identity gate."""

    engine_pnl: float
    cash_flow: float
    buy_fills: int
    sell_fills: int
    orders_submitted: int
    orders_canceled: int


@dataclass(frozen=True)
class ReplayIdentity:
    """Pinned extraction tape for one seed-0 map or one compact harness scenario."""

    match_id: int
    game: str
    seed: int
    scenario: str
    checks: ReplayChecks
    fills: tuple[FillIdentity, ...]
    place_cancel: tuple[PlaceCancelIdentity, ...]


def round_money(value: float) -> float:
    """Canonicalize a USDC/price/qty float so JSON goldens round-trip."""
    return round(float(value), MONEY_DECIMALS)


def project_fill(fill: FillRecord) -> FillIdentity:
    """Keep the identity columns of one fill."""
    return FillIdentity(
        match_id=int(fill.match_id),
        ts_ns=int(fill.ts_ns),
        order_id=str(fill.order_id),
        side=str(fill.side),
        token_index=int(fill.token_index),
        price=round_money(fill.price),
        quantity=round_money(fill.quantity),
        submitted_quantity=round_money(fill.submitted_quantity),
        episode_id=int(fill.episode_id),
        level_index=int(fill.level_index),
        submit_level_index=int(fill.submit_level_index),
        reserved_buy_notional=round_money(fill.reserved_buy_notional),
    )


def project_place_cancel(event: QuoteEvent) -> PlaceCancelIdentity | None:
    """Keep a place/cancel event; drop no_quote and other non-identity kinds."""
    if event.kind not in PLACE_CANCEL_KINDS:
        return None
    return PlaceCancelIdentity(
        match_id=int(event.match_id),
        ts_ns=int(event.ts_ns),
        kind=str(event.kind),
        order_id=str(event.order_id),
        side=str(event.side),
        token_index=int(event.token_index),
        price=round_money(event.price),
        quantity=round_money(event.quantity),
        episode_id=int(event.episode_id),
        level_index=int(event.level_index),
        submit_level_index=int(event.submit_level_index),
        reserved_buy_notional=round_money(event.reserved_buy_notional),
        reason=str(event.reason),
    )


def identity_from_records(
    *,
    match_id: int,
    game: str,
    seed: int,
    scenario: str,
    checks: ReplayChecks,
    fills: Sequence[FillRecord],
    quote_events: Sequence[QuoteEvent],
) -> ReplayIdentity:
    """Project a harvested tape onto the extraction-identity schema."""
    projected_fills = tuple(project_fill(fill) for fill in fills)
    place_cancel = tuple(
        event for event in (project_place_cancel(row) for row in quote_events) if event is not None
    )
    return ReplayIdentity(
        match_id=match_id,
        game=game,
        seed=seed,
        scenario=scenario,
        checks=checks,
        fills=projected_fills,
        place_cancel=place_cancel,
    )


def checks_from_result(result: MakerMatchResult) -> ReplayChecks:
    """Copy the PnL check fields from one match result row."""
    return ReplayChecks(
        engine_pnl=round_money(result.engine_pnl),
        cash_flow=round_money(result.cash_flow),
        buy_fills=int(result.buy_fills),
        sell_fills=int(result.sell_fills),
        orders_submitted=int(result.orders_submitted),
        orders_canceled=int(result.orders_canceled),
    )


def identity_from_run_dir(
    *, report_dir: Path, game: str, seed: int, scenario: str
) -> ReplayIdentity:
    """Build identity from a single-map backtest directory."""
    results = read_results_checkpoint(report_dir)
    if len(results) != 1:
        raise ValueError(f"{report_dir} must contain exactly one match result, got {len(results)}")
    result = results[0]
    fills = read_fills_checkpoint(report_dir)
    events = read_quote_events_checkpoint(report_dir)
    return identity_from_records(
        match_id=result.match_id,
        game=game,
        seed=seed,
        scenario=scenario,
        checks=checks_from_result(result),
        fills=fills,
        quote_events=events,
    )


def write_identity_json(*, path: Path, identity: ReplayIdentity) -> None:
    """Write identity JSON. Capture scripts use write_identity_golden."""
    write_json(path, asdict(identity))


def write_identity_golden(*, path: Path, identity: ReplayIdentity) -> None:
    write_identity_json(path=path, identity=identity)


def _require_int(raw: Mapping[str, object], key: str) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be int, got {type(value)}")
    return value


def _require_str(raw: Mapping[str, object], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be str, got {type(value)}")
    return value


def _require_float(raw: Mapping[str, object], key: str) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be float, got {type(value)}")
    return round_money(float(value))


def _require_mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"{label} must be an object")
    return cast(Mapping[str, object], raw)


def _load_fill(raw: object) -> FillIdentity:
    row = _require_mapping(raw, "fill")
    return FillIdentity(
        match_id=_require_int(row, "match_id"),
        ts_ns=_require_int(row, "ts_ns"),
        order_id=_require_str(row, "order_id"),
        side=_require_str(row, "side"),
        token_index=_require_int(row, "token_index"),
        price=_require_float(row, "price"),
        quantity=_require_float(row, "quantity"),
        submitted_quantity=_require_float(row, "submitted_quantity"),
        episode_id=_require_int(row, "episode_id"),
        level_index=_require_int(row, "level_index"),
        submit_level_index=_require_int(row, "submit_level_index"),
        reserved_buy_notional=_require_float(row, "reserved_buy_notional"),
    )


def _load_place_cancel(raw: object) -> PlaceCancelIdentity:
    row = _require_mapping(raw, "place_cancel")
    return PlaceCancelIdentity(
        match_id=_require_int(row, "match_id"),
        ts_ns=_require_int(row, "ts_ns"),
        kind=_require_str(row, "kind"),
        order_id=_require_str(row, "order_id"),
        side=_require_str(row, "side"),
        token_index=_require_int(row, "token_index"),
        price=_require_float(row, "price"),
        quantity=_require_float(row, "quantity"),
        episode_id=_require_int(row, "episode_id"),
        level_index=_require_int(row, "level_index"),
        submit_level_index=_require_int(row, "submit_level_index"),
        reserved_buy_notional=_require_float(row, "reserved_buy_notional"),
        reason=_require_str(row, "reason"),
    )


def _load_checks(raw: object) -> ReplayChecks:
    row = _require_mapping(raw, "checks")
    return ReplayChecks(
        engine_pnl=_require_float(row, "engine_pnl"),
        cash_flow=_require_float(row, "cash_flow"),
        buy_fills=_require_int(row, "buy_fills"),
        sell_fills=_require_int(row, "sell_fills"),
        orders_submitted=_require_int(row, "orders_submitted"),
        orders_canceled=_require_int(row, "orders_canceled"),
    )


def load_identity_golden(path: Path) -> ReplayIdentity:
    """Read a golden written by write_identity_golden."""
    payload = _require_mapping(read_json(path), str(path))
    fills_raw = payload["fills"]
    events_raw = payload["place_cancel"]
    if not isinstance(fills_raw, list) or not isinstance(events_raw, list):
        raise TypeError(f"{path} fills and place_cancel must be arrays")
    fill_rows = cast(list[object], fills_raw)
    event_rows = cast(list[object], events_raw)
    return ReplayIdentity(
        match_id=_require_int(payload, "match_id"),
        game=_require_str(payload, "game"),
        seed=_require_int(payload, "seed"),
        scenario=_require_str(payload, "scenario"),
        checks=_load_checks(payload["checks"]),
        fills=tuple(_load_fill(row) for row in fill_rows),
        place_cancel=tuple(_load_place_cancel(row) for row in event_rows),
    )


def _seq_mismatch(label: str, actual: Sequence[object], expected: Sequence[object]) -> str:
    """Empty when sequences match; otherwise length or first index path."""
    if len(actual) != len(expected):
        return f"{label} length {len(actual)} != {len(expected)}"
    for index, (got, want) in enumerate(zip(actual, expected, strict=True)):
        if got != want:
            return f"{label}[{index}] {got} != {want}"
    return ""


def first_identity_mismatch(actual: ReplayIdentity, expected: ReplayIdentity) -> str:
    """Empty string when tapes match; otherwise the first differing path."""
    if actual == expected:
        return ""
    for name in ("match_id", "game", "seed", "scenario", "checks"):
        got = getattr(actual, name)
        want = getattr(expected, name)
        if got != want:
            return f"{name} {got} != {want}"
    fills_mismatch = _seq_mismatch("fills", actual.fills, expected.fills)
    if fills_mismatch:
        return fills_mismatch
    events_mismatch = _seq_mismatch("place_cancel", actual.place_cancel, expected.place_cancel)
    if events_mismatch:
        return events_mismatch
    return "identity differs"


def first_tape_mismatch(actual: ReplayIdentity, expected: ReplayIdentity) -> str:
    """Identity gate without `checks` (PnL is reported, not the sole bar)."""
    for name in ("match_id", "game", "seed", "scenario"):
        got = getattr(actual, name)
        want = getattr(expected, name)
        if got != want:
            return f"{name} {got} != {want}"
    fills_mismatch = _seq_mismatch("fills", actual.fills, expected.fills)
    if fills_mismatch:
        return fills_mismatch
    events_mismatch = _seq_mismatch("place_cancel", actual.place_cancel, expected.place_cancel)
    if events_mismatch:
        return events_mismatch
    return ""
