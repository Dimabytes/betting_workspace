"""Maker match results, checkpoint/resume, and validation artifact writers."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from backtest.context import MarketContext
from backtest.feed_schedules import MatchFeedPlan, SchedulePlan
from backtest.paths import (
    MANIFEST_FILENAME,
    QUOTE_EVENTS_FILENAME,
    RESULTS_FILENAME,
    SUMMARY_FILENAME,
)
from backtest.report_io import load_parquet
from backtest.report_types import ReplayInstrumentResult
from backtest.telemetry import FillRecord, QuoteEvent
from shared.constants.dataset import PREHORN_LEAD_SECONDS
from shared.constants.strategy import MIN_ORDER_SIZE
from shared.utils.json_io import read_json, write_json
from shared.utils.parquet_io import write_parquet


@dataclass(frozen=True)
class GateSeconds:
    """1 Hz no_quote seconds per gate; unknown reasons fold into other."""

    stale_book: int
    pair_tolerance: int
    stale_signal: int
    anchor: int
    fair: int
    min_delta: int
    min_price: int
    max_price: int
    cutoff: int
    nw_velocity: int
    missing_nw: int
    dust: int
    exit_settle: int
    no_cash: int
    position_cap: int
    other: int


_GATE_REASONS = tuple(field.name for field in fields(GateSeconds))


def zero_gate_seconds() -> GateSeconds:
    """Empty per-gate histogram."""
    return GateSeconds(**dict.fromkeys(_GATE_REASONS, 0))


@dataclass(frozen=True)
class SignalProvenance:
    """Where a match's feed events and model decisions came from."""

    signal_mode: str
    feed_source: Literal["grid", "oddin", ""]
    model_name: str


def signal_provenance(plan: MatchFeedPlan) -> SignalProvenance:
    """Result-row feed and model identity for one match."""
    if isinstance(plan, SchedulePlan):
        return SignalProvenance(
            signal_mode="schedule",
            feed_source=plan.binding.feed_source,
            model_name=plan.model_dir.name,
        )
    return SignalProvenance(
        signal_mode="grid_v1",
        feed_source="",
        model_name=plan.model_dir.name,
    )


def signal_provenance_map(
    plans: Mapping[int, MatchFeedPlan],
    match_ids: Sequence[int],
) -> dict[int, SignalProvenance]:
    """Result-row feed/model identity for every replayed match."""
    return {match_id: signal_provenance(plans[match_id]) for match_id in match_ids}


@dataclass(frozen=True)
class MakerMatchResult:
    """One replayed match for one arm: identity, fill counts, terminal inventory, PnL."""

    match_id: int
    condition_id: str
    slug: str
    seconds_delay: int
    placement: str
    fill_model: str
    horn_at: str
    game_ended_at: str
    market_closed_at: str
    buy_fills: int
    sell_fills: int
    buy_quantity: float
    sell_quantity: float
    orders_submitted: int
    orders_accepted: int
    orders_canceled: int
    orders_rejected: int
    incomplete_orders: int
    terminal_token_index: int
    terminal_side: str
    terminal_position: float
    dust_position: bool
    window_seconds: int
    live_order_seconds: int
    gate_seconds: GateSeconds
    cash_flow: float
    engine_pnl: float
    settlement_applied: bool
    terminated_early: bool
    stop_reason: str | None
    signal_mode: str
    feed_source: Literal["grid", "oddin", ""]
    model_name: str
    merge_count: int = 0
    merge_shares: float = 0.0
    merge_usdc: float = 0.0
    leftover_shares_0: float = 0.0
    leftover_shares_1: float = 0.0
    leftover_settlement_usdc: float = 0.0

    @property
    def signal_group(self) -> str:
        """Report label: the bound feed disambiguates schedule rows."""
        if not self.feed_source:
            return self.signal_mode
        return f"{self.signal_mode}:{self.feed_source}"


def sum_gate_seconds(rows: Sequence[MakerMatchResult]) -> GateSeconds:
    """Add per-match gate histograms."""
    totals = dict.fromkeys(_GATE_REASONS, 0)
    for result in rows:
        for name in _GATE_REASONS:
            totals[name] += getattr(result.gate_seconds, name)
    return GateSeconds(**totals)


def gate_seconds_json(gates: GateSeconds) -> dict[str, int]:
    """summary.json keys: gate_<reason>_seconds."""
    return {f"gate_{name}_seconds": getattr(gates, name) for name in _GATE_REASONS}


def count_incomplete_orders(fills: Sequence[FillRecord]) -> int:
    """Count orders whose filled qty is short of the live submitted size."""
    filled_qty: dict[str, float] = {}
    submitted_qty: dict[str, float] = {}
    for fill in fills:
        filled_qty[fill.order_id] = filled_qty.get(fill.order_id, 0.0) + fill.quantity
        submitted_qty[fill.order_id] = fill.submitted_quantity
    return sum(1 for order_id, qty in filled_qty.items() if qty + 1e-12 < submitted_qty[order_id])


def _count_gate_seconds(events: Sequence[QuoteEvent]) -> GateSeconds:
    """Count 1 Hz no_quote events per gate; unknown reasons fold into other."""
    counts = dict.fromkeys(_GATE_REASONS, 0)
    for event in events:
        if event.kind != "no_quote":
            continue
        if event.reason in counts:
            counts[event.reason] += 1
        else:
            counts["other"] += 1
    return GateSeconds(**counts)


def build_maker_match_result(
    context: MarketContext,
    results_by_instrument_id: Mapping[str, ReplayInstrumentResult],
    fills: Sequence[FillRecord],
    quote_events: Sequence[QuoteEvent],
    *,
    placement: str,
    fill_model: str,
    uptimes: Mapping[int, int],
    provenance: SignalProvenance,
) -> MakerMatchResult:
    """Fold framework legs and telemetry into one match result row."""
    match_fills = [fill for fill in fills if fill.match_id == context.match_id]
    match_events = [event for event in quote_events if event.match_id == context.match_id]

    buy_fills = [fill for fill in match_fills if fill.side == "BUY"]
    sell_fills = [fill for fill in match_fills if fill.side == "SELL"]
    buy_quantity = sum(fill.quantity for fill in buy_fills)
    sell_quantity = sum(fill.quantity for fill in sell_fills)
    cash_flow = sum(
        (-fill.price * fill.quantity) if fill.side == "BUY" else (fill.price * fill.quantity)
        for fill in match_fills
    )
    incomplete_orders = count_incomplete_orders(match_fills)

    if match_fills:
        last = match_fills[-1]
        terminal_position = last.position_after
        terminal_token_index = last.token_index if terminal_position > 0 else -1
    else:
        terminal_position = 0.0
        terminal_token_index = -1
    dust_position = 0.0 < terminal_position < MIN_ORDER_SIZE
    terminal_side = "" if terminal_token_index < 0 else context.side_of(terminal_token_index)
    gate_seconds = _count_gate_seconds(match_events)
    merges = [event for event in match_events if event.kind == "merge"]
    merge_shares = sum(event.quantity for event in merges)
    bought = [0.0, 0.0]
    sold = [0.0, 0.0]
    for fill in match_fills:
        bucket = bought if fill.side == "BUY" else sold
        bucket[fill.token_index] += fill.quantity
    leftover_shares = [max(0.0, bought[index] - sold[index] - merge_shares) for index in (0, 1)]
    winner = context.radiant_token_index if context.radiant_win else 1 - context.radiant_token_index
    leftover_settlement_usdc = sum(
        shares for index, shares in enumerate(leftover_shares) if index == winner
    )
    window_seconds = (
        int((context.game_ended_at - context.horn_at).total_seconds()) + PREHORN_LEAD_SECONDS
    )

    engine_pnl = 0.0
    terminated_early = False
    stop_reason: str | None = None
    filled_legs: list[ReplayInstrumentResult] = []
    for instrument_id in context.instrument_ids:
        result = results_by_instrument_id[instrument_id]
        engine_pnl += float(result["pnl"])
        if result.get("terminated_early"):
            terminated_early = True
            stop_reason = result.get("stop_reason") or stop_reason
        if result.get("fills", 0):
            filled_legs.append(result)
    settlement_applied = bool(filled_legs) and all(
        bool(result.get("settlement_pnl_applied")) for result in filled_legs
    )
    return MakerMatchResult(
        match_id=context.match_id,
        condition_id=context.condition_id,
        slug=context.market_slug,
        seconds_delay=context.seconds_delay,
        placement=placement,
        fill_model=fill_model,
        horn_at=context.horn_at.isoformat(),
        game_ended_at=context.game_ended_at.isoformat(),
        market_closed_at=context.market_closed_at.isoformat(),
        buy_fills=len(buy_fills),
        sell_fills=len(sell_fills),
        buy_quantity=buy_quantity,
        sell_quantity=sell_quantity,
        orders_submitted=sum(1 for event in match_events if event.kind == "submitted"),
        orders_accepted=sum(1 for event in match_events if event.kind == "accepted"),
        orders_canceled=sum(1 for event in match_events if event.kind == "canceled"),
        orders_rejected=sum(1 for event in match_events if event.kind == "rejected"),
        incomplete_orders=incomplete_orders,
        terminal_token_index=terminal_token_index,
        terminal_side=terminal_side,
        terminal_position=terminal_position,
        dust_position=dust_position,
        window_seconds=window_seconds,
        live_order_seconds=uptimes.get(context.match_id, 0),
        gate_seconds=gate_seconds,
        cash_flow=cash_flow,
        engine_pnl=engine_pnl,
        settlement_applied=settlement_applied,
        terminated_early=terminated_early,
        stop_reason=stop_reason,
        signal_mode=provenance.signal_mode,
        feed_source=provenance.feed_source,
        model_name=provenance.model_name,
        merge_count=len(merges),
        merge_shares=merge_shares,
        merge_usdc=merge_shares,
        leftover_shares_0=leftover_shares[0],
        leftover_shares_1=leftover_shares[1],
        leftover_settlement_usdc=leftover_settlement_usdc,
    )


def engine_fault_match_result(
    context: MarketContext,
    *,
    placement: str,
    fill_model: str,
    stop_reason: str,
    provenance: SignalProvenance,
) -> MakerMatchResult:
    """Result row for a map we refused to send into the engine."""
    window_seconds = (
        int((context.game_ended_at - context.horn_at).total_seconds()) + PREHORN_LEAD_SECONDS
    )
    return MakerMatchResult(
        match_id=context.match_id,
        condition_id=context.condition_id,
        slug=context.market_slug,
        seconds_delay=context.seconds_delay,
        placement=placement,
        fill_model=fill_model,
        horn_at=context.horn_at.isoformat(),
        game_ended_at=context.game_ended_at.isoformat(),
        market_closed_at=context.market_closed_at.isoformat(),
        buy_fills=0,
        sell_fills=0,
        buy_quantity=0.0,
        sell_quantity=0.0,
        orders_submitted=0,
        orders_accepted=0,
        orders_canceled=0,
        orders_rejected=0,
        incomplete_orders=0,
        terminal_token_index=-1,
        terminal_side="",
        terminal_position=0.0,
        dust_position=False,
        window_seconds=window_seconds,
        live_order_seconds=0,
        gate_seconds=zero_gate_seconds(),
        cash_flow=0.0,
        engine_pnl=0.0,
        settlement_applied=False,
        terminated_early=True,
        stop_reason=stop_reason,
        signal_mode=provenance.signal_mode,
        feed_source=provenance.feed_source,
        model_name=provenance.model_name,
    )


def build_maker_match_results(
    contexts: Sequence[MarketContext],
    results: Sequence[ReplayInstrumentResult],
    fills: Sequence[FillRecord],
    quote_events: Sequence[QuoteEvent],
    *,
    placement: str,
    fill_model: str,
    uptimes: Mapping[int, int],
    provenance: Mapping[int, SignalProvenance],
) -> list[MakerMatchResult]:
    """One maker result row per market context."""
    results_by_instrument_id = {result["instrument_id"]: result for result in results}
    missing = [
        context.match_id
        for context in contexts
        if not all(
            instrument_id in results_by_instrument_id for instrument_id in context.instrument_ids
        )
    ]
    if missing:
        raise ValueError(f"framework returned no results for matches {missing}")
    return [
        build_maker_match_result(
            context,
            results_by_instrument_id,
            fills,
            quote_events,
            placement=placement,
            fill_model=fill_model,
            uptimes=uptimes,
            provenance=provenance[context.match_id],
        )
        for context in contexts
    ]


def _parse_gate_seconds(raw: object) -> GateSeconds:
    """Rebuild GateSeconds from a parquet nested mapping; extra archived keys are ignored."""
    if isinstance(raw, GateSeconds):
        return raw
    if isinstance(raw, Mapping):
        counts = {name: int(raw.get(name, 0)) for name in _GATE_REASONS}  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        return GateSeconds(**counts)
    raise TypeError(f"gate_seconds checkpoint value is {type(raw)}")


def read_results_checkpoint(report_dir: Path) -> list[MakerMatchResult]:
    """Results an earlier run already checkpointed here; empty when there is no parquet."""
    frame = load_parquet(report_dir / RESULTS_FILENAME)
    if frame.empty:
        return []
    spec = fields(MakerMatchResult)
    field_names = [field.name for field in spec]
    for field in spec:
        if field.name in frame.columns:
            continue
        default = field.default
        if not isinstance(default, (int, float)):
            raise ValueError(f"results.parquet is missing column {field.name}")
        frame[field.name] = default
    gate_index = field_names.index("gate_seconds")
    loaded: list[MakerMatchResult] = []
    for row in frame[field_names].itertuples(index=False, name=None):
        values = list(row)
        values[gate_index] = _parse_gate_seconds(values[gate_index])
        loaded.append(MakerMatchResult(*values))
    return loaded


def write_results_checkpoint(*, report_dir: Path, results: Sequence[MakerMatchResult]) -> None:
    """Overwrite results.parquet with every (match, arm) row collected so far."""
    frame = pd.DataFrame([asdict(result) for result in results])
    write_parquet(frame, report_dir / RESULTS_FILENAME)


def write_quote_events_parquet(*, report_dir: Path, events: Sequence[QuoteEvent]) -> None:
    """Write quote_events.parquet for the run."""
    if not events:
        return
    frame = pd.DataFrame([asdict(event) for event in events])
    write_parquet(frame, report_dir / QUOTE_EVENTS_FILENAME)


def read_quote_events_checkpoint(report_dir: Path) -> list[QuoteEvent]:
    """Quote events an earlier run already wrote; empty when there is no parquet."""
    frame = load_parquet(report_dir / QUOTE_EVENTS_FILENAME)
    if frame.empty:
        return []
    field_names = [field.name for field in fields(QuoteEvent)]
    missing = [name for name in field_names if name not in frame.columns]
    if missing:
        raise ValueError(
            f"quote_events.parquet is missing columns {missing}; "
            "refuse resume of an incompatible checkpoint"
        )
    ordered = frame[field_names].itertuples(index=False, name=None)
    return [QuoteEvent(*row) for row in ordered]


def write_validation_outputs(*, report_dir: Path, summary: Mapping[str, Any]) -> None:
    """Write summary.json; results/fills were already checkpointed per batch."""
    write_json(report_dir / SUMMARY_FILENAME, dict(summary))


def read_manifest(report_dir: Path) -> dict[str, Any]:
    """Load manifest.json from a previous run, or empty when absent."""
    path = report_dir / MANIFEST_FILENAME
    if not path.exists():
        return {}
    return read_json(path)


def write_manifest(report_dir: Path, manifest: Mapping[str, Any]) -> None:
    """Write the run fingerprint next to the result artifacts."""
    write_json(report_dir / MANIFEST_FILENAME, dict(manifest))


def assert_manifest_matches(existing: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    """Refuse --resume when the checkpoint fingerprint differs from this run."""
    if not existing:
        raise ValueError("resume requested but manifest.json is missing")
    filter_keys = {
        "league_whitelist_path",
        "league_whitelist_sha256",
        "selected_matches",
        "selected_matches_sha256",
    }
    compared_keys = set(expected) | (set(existing) & filter_keys)
    mismatches = sorted(key for key in compared_keys if existing.get(key) != expected.get(key))
    if mismatches:
        raise ValueError(
            f"resume/shard fingerprint mismatch for keys {mismatches}; "
            "use a new run directory and shards from the same selection and whitelist"
        )


def concat_results_without_overlap(
    groups: Sequence[Sequence[MakerMatchResult]],
) -> list[MakerMatchResult]:
    """Flatten checkpoint groups; raise if the same match_id appears twice."""
    seen: set[int] = set()
    merged: list[MakerMatchResult] = []
    for group in groups:
        for result in group:
            if result.match_id in seen:
                raise ValueError(f"duplicate match_id {result.match_id} across shard checkpoints")
            seen.add(result.match_id)
            merged.append(result)
    return merged
