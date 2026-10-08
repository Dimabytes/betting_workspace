"""Outside-engine markout, rebate, drawdown, and arm summary for the maker backtest.

Fee/rebate constants are the sports_fees_v3 fallback (rate 0.05, rebate 0.15).
A two-sided run passes per-market terms from market_terms.json instead.
"""

# pyright: reportMissingTypeStubs=false

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from random import Random
from statistics import median
from typing import cast

import pandas as pd

from backtest.context import MarketContext
from backtest.market_terms import MarketFee
from backtest.marks import (
    EnrichedFill,
    MidSeries,
    ReferenceSource,
    calculate_cvar_5,
    lookup_reference_mid,
    settlement_value_for_token,
    token_mid,
)
from backtest.paths import FILLS_FILENAME
from backtest.quote_store import QuoteTelemetry, sum_quote_event_counts
from backtest.report_io import load_parquet
from backtest.report_types import (
    ArmMarkouts,
    ArmPayload,
    ArmSummary,
    BoardFillSlice,
    ForcedLiquidationStub,
    HoldPayload,
    MarkoutBlock,
    SummaryPayload,
    WalletPayload,
)
from backtest.results import (
    MakerMatchResult,
    gate_seconds_json,
    sum_gate_seconds,
)
from backtest.selection import ValidationCoverage
from backtest.telemetry import FillRecord
from backtest.wallet_path import (
    MergeCredit,
    ReservePath,
    WalletPath,
    calculate_reserve_path,
    calculate_wallet_path,
)
from market_data.build_market_data import market_seconds_cache_path
from shared.constants.strategy import ORDER_CANCEL_LATENCY_MS, ORDER_INSERT_LATENCY_MS
from shared.utils.match_time import NS_PER_SECOND
from shared.utils.parquet_io import write_parquet
from shared.utils.telonex_book import NS_PER_US
from shared.utils.trading import calculate_taker_fee_per_share

FEE_RATE = 0.05
REBATE_RATE = 0.15
MARKOUT_HORIZONS = (30, 300)
BOOTSTRAP_ITERATIONS = 10_000
BOOTSTRAP_SEED = 20260812
# Report slice: fills landing this soon after a board tick; same window the
# kill gate holds the book for.
BOARD_FILL_WINDOW_SECONDS = 10.0
ASSUMPTIONS = (
    "queue: LIMIT fills only after same-price size ahead at accept trades "
    "through (Nautilus queue_position=True; accept is "
    f"{ORDER_INSERT_LATENCY_MS:.0f} ms after submit; cancel release is "
    f"{ORDER_CANCEL_LATENCY_MS:.0f} ms after the request; Gamma secondsDelay "
    "is not applied to post-only orders). "
    "rebate: 0.15 * 0.05 * qty * p * (1-p) is the sports maker rebate on our fills "
    "(15% of the taker fee that fill generated). Other makers in the same market do "
    "not dilute it. Polymarket pays the UTC-day sum to the wallet only if it is >= $1. "
    "Liquidity Rewards are not modeled: at $100 clip size is often >= 50 and sometimes "
    ">= 250 where the program exists; 171/328 validation markets have no program; "
    "skipping is conservative (PnL understated). "
    "markout: paired mid at fill_ts+h from the market-seconds cache (as-of; last fresh "
    "mid before game end when t+h is past the book; settlement when a match has no "
    "usable mid). "
    "drawdown: peak-to-trough of cash_flow + open_qty * token_mid over the mid series, "
    "plus a terminal settlement mark; max_match_drawdown is the worst per-match value. "
    "min_equity is the lowest cumulative engine_pnl vs start-at-zero, matches "
    "ordered by horn (one lump per match). "
    "wallet: required cash is -min(cash) on the shared fill+settlement tape "
    "(parallel maps and losses); lowest capital point is min(MTM equity) after "
    "injecting that deposit; equity_spark is 100 as-of points on a calendar axis "
    "(bucket 0 = deposit, 99 equal windows over the fill-tape span), snapshot only "
    "on cash/MTM change, min/max pinned. "
    "rebate is not cash at the next BUY. "
    "required_cash is an estimate from executed fills only. required_cash_with_reserves "
    "is the deposit the wallet actually needs: max(reserved working BUY notional - "
    "cumulative cash flow), where a canceling BUY keeps its reserve until the venue "
    "acknowledges the cancel and a SELL never frees it. The _at_close variant returns "
    "settlement cash at market_closed_at instead of game_ended_at; that is a delay proxy, "
    "not a measured redeem time. "
    "Backtest hardcodes MIN_ORDER_SIZE = 5; live reads min_order_size from the sidecar "
    "and can change on the fly. On CLOB validation min = 5. "
    "bootstrap: match_id cluster, 10000 iterations, fixed seed, percentile 95% CI. "
    "These matches are the model's validation split, and tree count was chosen by early "
    "stopping on the same split — reported edge is optimistic."
)


@dataclass(frozen=True)
class MarkoutEstimate:
    """Quantity-weighted markout with a match-cluster percentile CI."""

    estimate: float
    ci_low: float
    ci_high: float
    fills: int


def load_match_mid_series(match_ids: Sequence[int]) -> dict[int, MidSeries]:
    """Load as-of mid series from the market-seconds cache for each match."""
    series_by_match: dict[int, MidSeries] = {}
    for match_id in match_ids:
        path = market_seconds_cache_path(match_id)
        frame = pd.read_parquet(path, columns=["state_ts_us", "market_p_radiant", "market_status"])
        ok = frame[frame["market_status"] == "ok"]
        timestamps_ns: list[int] = []
        market_ps: list[float] = []
        for raw_ts, raw_p in zip(
            ok["state_ts_us"].tolist(), ok["market_p_radiant"].tolist(), strict=True
        ):
            timestamps_ns.append(int(raw_ts) * NS_PER_US)
            market_ps.append(float(raw_p))
        series_by_match[match_id] = MidSeries(
            timestamps_ns=tuple(timestamps_ns), market_ps=tuple(market_ps)
        )
    return series_by_match


def _reference_at_horizon(
    fill: FillRecord,
    series: MidSeries,
    radiant_token_index: int,
    radiant_win: bool,
    horizon_seconds: int,
) -> tuple[float, ReferenceSource]:
    """Paired token mid at fill_ts+h, else settlement when no usable mid exists."""
    target_ns = fill.ts_ns + horizon_seconds * NS_PER_SECOND
    market_p = lookup_reference_mid(series, target_ns)
    if market_p is None:
        value = settlement_value_for_token(fill.token_index, radiant_token_index, radiant_win)
        return value, "settlement"
    return token_mid(market_p, fill.token_index, radiant_token_index), "mid"


def _signed_markout(side: str, price: float, reference: float) -> float:
    """BUY: reference - price; SELL: price - reference."""
    if side == "BUY":
        return reference - price
    return price - reference


def _board_age_seconds(ticks_ns: Sequence[int], ts_ns: int) -> float:
    """Seconds since the most recent board tick, NaN when none arrived yet."""
    index = bisect_right(ticks_ns, ts_ns) - 1
    if index < 0:
        return float("nan")
    return (ts_ns - ticks_ns[index]) / NS_PER_SECOND


def enrich_fills(
    fills: Sequence[FillRecord],
    mids: Mapping[int, MidSeries],
    contexts: Sequence[MarketContext],
    *,
    fill_model: str,
    board_ticks: Mapping[int, Sequence[int]],
    fees: Mapping[int, MarketFee] | None = None,
) -> list[EnrichedFill]:
    """Stamp fee/rebate, markout, and board-tick age onto each raw fill."""
    context_by_match = {context.match_id: context for context in contexts}
    enriched: list[EnrichedFill] = []
    for fill in fills:
        context = context_by_match[fill.match_id]
        series = mids[fill.match_id]
        terms = None if fees is None else fees.get(fill.match_id)
        fee_rate = FEE_RATE if terms is None else terms.fee_rate
        rebate_rate = REBATE_RATE if terms is None else terms.rebate_rate
        fee_base = fill.quantity * calculate_taker_fee_per_share(price=fill.price, fee_rate=1.0)
        if fill.is_maker:
            maker_rebate = rebate_rate * fee_rate * fee_base
            taker_fee = 0.0
        else:
            maker_rebate = 0.0
            taker_fee = fee_rate * fee_base
        reference_30s, source_30s = _reference_at_horizon(
            fill, series, context.radiant_token_index, context.radiant_win, 30
        )
        reference_300s, source_300s = _reference_at_horizon(
            fill, series, context.radiant_token_index, context.radiant_win, 300
        )
        enriched.append(
            EnrichedFill(
                **asdict(fill),
                fill_model=fill_model,
                fee_base=fee_base,
                maker_rebate=maker_rebate,
                taker_fee=taker_fee,
                reference_30s=reference_30s,
                reference_300s=reference_300s,
                markout_30s=_signed_markout(fill.side, fill.price, reference_30s),
                markout_300s=_signed_markout(fill.side, fill.price, reference_300s),
                reference_source_30s=source_30s,
                reference_source_300s=source_300s,
                board_age_seconds=_board_age_seconds(
                    board_ticks.get(fill.match_id, ()), fill.ts_ns
                ),
            )
        )
    return enriched


@dataclass
class DrawdownState:
    """Cash + inventory mark used to walk peak-to-trough equity."""

    cash: float = 0.0
    qty: float = 0.0
    token_index: int = -1
    peak: float = 0.0
    max_drawdown: float = 0.0

    def apply_fill(self, fill: EnrichedFill) -> None:
        notional = fill.price * fill.quantity
        if fill.side == "BUY":
            self.cash -= notional
            self.qty += fill.quantity
            self.token_index = fill.token_index
            return
        self.cash += notional
        self.qty -= fill.quantity
        if self.qty <= 0:
            self.qty = 0.0
            self.token_index = -1

    def mark(self, token_price: float) -> None:
        equity = self.cash + self.qty * token_price
        if equity > self.peak:
            self.peak = equity
        drop = self.peak - equity
        if drop > self.max_drawdown:
            self.max_drawdown = drop


def calculate_match_drawdown(
    fills: Sequence[EnrichedFill],
    series: MidSeries,
    settlement: float,
    radiant_token_index: int,
) -> float:
    """Peak-to-trough drop of cash + MTM over the mid series, then a settlement mark."""
    if not fills:
        return 0.0
    ordered = sorted(fills, key=lambda fill: fill.ts_ns)
    state = DrawdownState()
    fill_index = 0
    for timestamp_ns, market_p in zip(series.timestamps_ns, series.market_ps, strict=True):
        while fill_index < len(ordered) and ordered[fill_index].ts_ns <= timestamp_ns:
            state.apply_fill(ordered[fill_index])
            fill_index += 1
        if state.qty > 0:
            state.mark(token_mid(market_p, state.token_index, radiant_token_index))
        else:
            state.mark(0.0)

    while fill_index < len(ordered):
        state.apply_fill(ordered[fill_index])
        fill_index += 1
    state.mark(settlement if state.qty > 0 else 0.0)
    return state.max_drawdown


def calculate_min_equity(results: Sequence[MakerMatchResult]) -> float:
    """Lowest cumulative engine PnL vs start-at-zero, matches in horn order."""
    ordered = sorted(
        (result for result in results if not result.terminated_early),
        key=lambda result: (result.horn_at, result.match_id),
    )
    equity = 0.0
    min_equity = 0.0
    for result in ordered:
        equity += result.engine_pnl
        if equity < min_equity:
            min_equity = equity
    return min_equity


def calculate_drawdowns(
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    mids: Mapping[int, MidSeries],
    contexts: Mapping[int, MarketContext],
) -> dict[int, float]:
    """Per-match drawdown keyed by match_id."""
    fills_by_match: dict[int, list[EnrichedFill]] = {}
    for fill in fills:
        fills_by_match.setdefault(fill.match_id, []).append(fill)

    drawdowns: dict[int, float] = {}
    for result in results:
        context = contexts[result.match_id]
        match_fills = fills_by_match.get(result.match_id, [])
        if result.terminal_token_index < 0:
            settlement = 0.0
        else:
            settlement = settlement_value_for_token(
                result.terminal_token_index,
                context.radiant_token_index,
                context.radiant_win,
            )
        drawdowns[result.match_id] = calculate_match_drawdown(
            match_fills, mids[result.match_id], settlement, context.radiant_token_index
        )
    return drawdowns


def _markout_value(fill: EnrichedFill, horizon: int) -> float:
    """Select the 30s or 300s markout column."""
    if horizon == 30:
        return fill.markout_30s
    return fill.markout_300s


def _weighted_markout(fills: Sequence[EnrichedFill], horizon: int) -> float | None:
    """Quantity-weighted mean markout, or None when there is no quantity."""
    total_qty = sum(fill.quantity for fill in fills)
    if total_qty == 0:
        return None
    weighted = sum(_markout_value(fill, horizon) * fill.quantity for fill in fills)
    return weighted / total_qty


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile; q is in [0, 1]."""
    last = len(sorted_values) - 1
    position = q * last
    low = int(position)
    high = min(low + 1, last)
    weight = position - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def _estimate_from_samples(
    estimate: float, samples: Sequence[float], fills: int
) -> MarkoutEstimate:
    """Build a 95% percentile CI from bootstrap samples around a point estimate."""
    ordered = sorted(samples)
    return MarkoutEstimate(
        estimate=estimate,
        ci_low=_percentile(ordered, 0.025),
        ci_high=_percentile(ordered, 0.975),
        fills=fills,
    )


def _fills_by_match(fills: Sequence[EnrichedFill]) -> dict[int, list[EnrichedFill]]:
    """Group fills by match_id for cluster resampling."""
    by_match: dict[int, list[EnrichedFill]] = {}
    for fill in fills:
        by_match.setdefault(fill.match_id, []).append(fill)
    return by_match


def _cluster_picks(cluster_ids: Sequence[int], iterations: int, seed: int) -> list[list[int]]:
    """With-replacement draws of match ids, one draw of len(clusters) per iteration."""
    rng = Random(seed)
    n_clusters = len(cluster_ids)
    return [rng.choices(cluster_ids, k=n_clusters) for _ in range(iterations)]


def bootstrap_markout(
    fills: Sequence[EnrichedFill],
    *,
    side: str,
    horizon: int,
    iterations: int,
    seed: int,
) -> MarkoutEstimate | None:
    """Match-cluster bootstrap of quantity-weighted markout; None when the side has no fills."""
    selected = [fill for fill in fills if fill.side == side]
    if not selected:
        return None
    by_match = _fills_by_match(selected)
    cluster_ids = sorted(by_match)
    estimate = _weighted_markout(selected, horizon)
    if estimate is None:
        return None
    samples: list[float] = []
    for picks in _cluster_picks(cluster_ids, iterations, seed):
        resampled = [fill for match_id in picks for fill in by_match[match_id]]
        sample_mean = _weighted_markout(resampled, horizon)
        if sample_mean is None:
            continue
        samples.append(sample_mean)
    if not samples:
        return None
    return _estimate_from_samples(estimate, samples, len(selected))


def _markout_block(estimate: MarkoutEstimate | None) -> MarkoutBlock | None:
    """JSON object for one side x horizon, or null when that bucket is empty."""
    if estimate is None:
        return None
    return {
        "estimate": estimate.estimate,
        "ci_low": estimate.ci_low,
        "ci_high": estimate.ci_high,
        "fills": estimate.fills,
    }


def _board_fill_slice(fills: Sequence[EnrichedFill]) -> BoardFillSlice:
    """Fills inside the post-board-tick window: count, share, 300s markouts."""
    windowed = [fill for fill in fills if fill.board_age_seconds <= BOARD_FILL_WINDOW_SECONDS]
    return {
        "fills": len(windowed),
        "share": len(windowed) / len(fills) if fills else 0.0,
        "buy_300s": _markout_block(
            bootstrap_markout(
                windowed,
                side="BUY",
                horizon=300,
                iterations=BOOTSTRAP_ITERATIONS,
                seed=BOOTSTRAP_SEED,
            )
        ),
        "sell_300s": _markout_block(
            bootstrap_markout(
                windowed,
                side="SELL",
                horizon=300,
                iterations=BOOTSTRAP_ITERATIONS,
                seed=BOOTSTRAP_SEED,
            )
        ),
    }


def _arm_markouts(fills: Sequence[EnrichedFill]) -> ArmMarkouts:
    """Bootstrap all four side x horizon markout blocks for one arm."""
    return {
        "buy_30s": _markout_block(
            bootstrap_markout(
                fills,
                side="BUY",
                horizon=30,
                iterations=BOOTSTRAP_ITERATIONS,
                seed=BOOTSTRAP_SEED,
            )
        ),
        "buy_300s": _markout_block(
            bootstrap_markout(
                fills,
                side="BUY",
                horizon=300,
                iterations=BOOTSTRAP_ITERATIONS,
                seed=BOOTSTRAP_SEED,
            )
        ),
        "sell_30s": _markout_block(
            bootstrap_markout(
                fills,
                side="SELL",
                horizon=30,
                iterations=BOOTSTRAP_ITERATIONS,
                seed=BOOTSTRAP_SEED,
            )
        ),
        "sell_300s": _markout_block(
            bootstrap_markout(
                fills,
                side="SELL",
                horizon=300,
                iterations=BOOTSTRAP_ITERATIONS,
                seed=BOOTSTRAP_SEED,
            )
        ),
    }


def _clean_fills_for_results(
    results: Sequence[MakerMatchResult], fills: Sequence[EnrichedFill]
) -> list[EnrichedFill]:
    """Keep fills whose match result row is not terminated_early."""
    clean_ids = {result.match_id for result in results if not result.terminated_early}
    return [fill for fill in fills if fill.match_id in clean_ids]


def _closed_hold_seconds(fills: Sequence[EnrichedFill]) -> list[float]:
    """Seconds from position open to flatten, one value per closed round trip."""
    by_match: dict[int, list[EnrichedFill]] = {}
    for fill in fills:
        by_match.setdefault(fill.match_id, []).append(fill)
    holds: list[float] = []
    for match_fills in by_match.values():
        ordered = sorted(match_fills, key=lambda fill: fill.ts_ns)
        qty = 0.0
        opened_ns: int | None = None
        for fill in ordered:
            if fill.side == "BUY":
                if qty <= 1e-12:
                    opened_ns = fill.ts_ns
                qty += fill.quantity
                continue
            qty -= fill.quantity
            if qty > 1e-12:
                continue
            qty = 0.0
            if opened_ns is not None:
                holds.append((fill.ts_ns - opened_ns) / NS_PER_SECOND)
            opened_ns = None
    return holds


def summarize_holds(fills: Sequence[EnrichedFill]) -> HoldPayload:
    """min/p50/p90 hold of closed round trips."""
    holds = _closed_hold_seconds(fills)
    if not holds:
        return {
            "closed": 0,
            "min_seconds": 0.0,
            "p50_seconds": 0.0,
            "p90_seconds": 0.0,
        }
    ordered = sorted(holds)
    return {
        "closed": len(holds),
        "min_seconds": ordered[0],
        "p50_seconds": _percentile(ordered, 0.50),
        "p90_seconds": _percentile(ordered, 0.90),
    }


def _wallet_payload(
    wallet: WalletPath,
    reserves: ReservePath,
    reserves_at_close: ReservePath,
    *,
    engine_pnl: float,
    net_pnl: float,
) -> WalletPayload:
    """Wallet path plus the ROIs it implies; ROI is None when no capital was needed."""
    required = wallet.required_cash
    return {
        "required_cash": required,
        "required_cash_with_reserves": reserves.required_cash,
        "required_cash_with_reserves_at_close": reserves_at_close.required_cash,
        "peak_reserved": reserves.peak_reserved,
        "final_balance_with_rebate": required + net_pnl,
        "span_seconds": wallet.span_seconds,
        "maps_at_once": wallet.maps_at_once,
        "lowest_capital": wallet.lowest_capital,
        "roi_before_rebate": engine_pnl / required if required > 0 else None,
        "roi_with_rebate": net_pnl / required if required > 0 else None,
        "equity_spark": list(wallet.equity_spark),
    }


def summarize_arm(
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    drawdowns: Mapping[int, float],
    telemetry: QuoteTelemetry,
    mids: Mapping[int, MidSeries],
    contexts: Mapping[int, MarketContext],
) -> ArmSummary:
    """Per-arm counters, markouts, rebate, quote-event histograms, and stub liquidation."""
    clean = [result for result in results if not result.terminated_early]
    terminated = [result for result in results if result.terminated_early]
    fills = _clean_fills_for_results(results, fills)
    clean_ids = frozenset(result.match_id for result in clean)
    reserve_events = [event for event in telemetry.reserve_events if event.match_id in clean_ids]
    merge_events = [event for event in telemetry.merge_events if event.match_id in clean_ids]
    merges = [
        MergeCredit(ts_ns=event.ts_ns, match_id=event.match_id, shares=event.quantity)
        for event in merge_events
    ]
    traded = [result for result in clean if result.buy_fills + result.sell_fills > 0]
    buy_fills = [fill for fill in fills if fill.side == "BUY"]
    sell_fills = [fill for fill in fills if fill.side == "SELL"]
    orders_submitted = sum(result.orders_submitted for result in clean)
    filled_order_ids = {fill.order_id for fill in fills}
    incomplete_orders = sum(result.incomplete_orders for result in clean)
    buy_notionals: dict[str, float] = {}
    for fill in buy_fills:
        buy_notionals[fill.order_id] = (
            buy_notionals.get(fill.order_id, 0.0) + fill.price * fill.quantity
        )
    total_engine_pnl = sum(result.engine_pnl for result in clean)
    cash_flow = sum(result.cash_flow for result in clean)
    maker_rebate = sum(fill.maker_rebate for fill in fills)
    taker_fee = sum(fill.taker_fee for fill in fills)
    net_pnl = total_engine_pnl + maker_rebate - taker_fee
    completed = len(clean)
    max_match_drawdown = max((drawdowns.get(result.match_id, 0.0) for result in clean), default=0.0)
    min_equity = calculate_min_equity(clean)
    n_filled_orders = len(filled_order_ids)
    fill_rate = n_filled_orders / orders_submitted if orders_submitted else 0.0
    incomplete_order_rate = incomplete_orders / n_filled_orders if n_filled_orders else 0.0
    median_clip = median(buy_notionals.values()) if buy_notionals else None
    match_pnls = [result.engine_pnl for result in clean]
    bought_shares = sum(result.buy_quantity for result in clean)
    pnl_per_eligible_match = total_engine_pnl / completed if completed else 0.0
    pnl_per_bought_share = total_engine_pnl / bought_shares if bought_shares else 0.0
    net_pnl_per_match = net_pnl / completed if completed else 0.0
    loss_match_rate = sum(1 for pnl in match_pnls if pnl < 0) / completed if completed else 0.0
    worst_match = min(match_pnls) if match_pnls else 0.0
    median_match_pnl = median(match_pnls) if match_pnls else 0.0
    pnl_per_bought_share_with_rebate = net_pnl / bought_shares if bought_shares else 0.0
    wallet = _wallet_payload(
        calculate_wallet_path(clean, fills, mids, contexts, merges),
        calculate_reserve_path(
            clean, fills, [*reserve_events, *merge_events], contexts, settlement_at="game_end"
        ),
        calculate_reserve_path(
            clean,
            fills,
            [*reserve_events, *merge_events],
            contexts,
            settlement_at="market_close",
        ),
        engine_pnl=total_engine_pnl,
        net_pnl=net_pnl,
    )
    terminal_radiant_inventory = sum(
        result.terminal_position for result in clean if result.terminal_side == "radiant"
    )
    terminal_dire_inventory = sum(
        result.terminal_position for result in clean if result.terminal_side == "dire"
    )
    quote_event_counts = sum_quote_event_counts(telemetry.kind_counts, clean_ids)
    signal_groups: dict[str, int] = {}
    model_groups: dict[str, int] = {}
    for result in results:
        signal_groups[result.signal_group] = signal_groups.get(result.signal_group, 0) + 1
        model_groups[result.model_name] = model_groups.get(result.model_name, 0) + 1
    forced_liquidation: ForcedLiquidationStub = {
        "taker_fee": None,
        "net_pnl": None,
        "unliquidatable": None,
    }
    return cast(
        ArmSummary,
        {
            "matches": len(results),
            "completed": completed,
            "terminated": len(terminated),
            "traded": len(traded),
            "no_trades": completed - len(traded),
            "buy_fills": sum(result.buy_fills for result in clean),
            "sell_fills": sum(result.sell_fills for result in clean),
            "orders_submitted": orders_submitted,
            "orders_accepted": sum(result.orders_accepted for result in clean),
            "orders_canceled": sum(result.orders_canceled for result in clean),
            "orders_rejected": sum(result.orders_rejected for result in clean),
            "incomplete_orders": incomplete_orders,
            "dust_positions": sum(1 for result in clean if result.dust_position),
            "fill_rate": fill_rate,
            "filled_quantity": sum(fill.quantity for fill in fills),
            "incomplete_order_rate": incomplete_order_rate,
            "buy_turnover": sum(fill.price * fill.quantity for fill in buy_fills),
            "sell_turnover": sum(fill.price * fill.quantity for fill in sell_fills),
            "median_buy_order_notional": median_clip,
            "window_seconds": sum(result.window_seconds for result in clean),
            "live_order_seconds": sum(result.live_order_seconds for result in clean),
            **gate_seconds_json(sum_gate_seconds(clean)),
            "quote_events": quote_event_counts,
            "terminal_radiant_inventory": terminal_radiant_inventory,
            "terminal_dire_inventory": terminal_dire_inventory,
            "terminal_inventory": terminal_radiant_inventory + terminal_dire_inventory,
            "total_engine_pnl": total_engine_pnl,
            "cash_flow": cash_flow,
            "settlement_remainder": total_engine_pnl - cash_flow,
            "pnl_before_rebate": total_engine_pnl,
            "pnl_per_eligible_match": pnl_per_eligible_match,
            "pnl_per_bought_share": pnl_per_bought_share,
            "pnl_per_bought_share_with_rebate": pnl_per_bought_share_with_rebate,
            "bought_shares": bought_shares,
            "maker_rebate": maker_rebate,
            "taker_fee": taker_fee,
            "merge_count": sum(result.merge_count for result in clean),
            "merge_shares": sum(result.merge_shares for result in clean),
            "merge_usdc": sum(result.merge_usdc for result in clean),
            "leftover_shares_0": sum(result.leftover_shares_0 for result in clean),
            "leftover_shares_1": sum(result.leftover_shares_1 for result in clean),
            "leftover_settlement_usdc": sum(result.leftover_settlement_usdc for result in clean),
            "net_pnl": net_pnl,
            "net_pnl_per_match": net_pnl_per_match,
            "median_match_pnl": median_match_pnl,
            "max_match_drawdown": max_match_drawdown,
            "min_equity": min_equity,
            "loss_match_rate": loss_match_rate,
            "cvar_5": calculate_cvar_5(match_pnls),
            "worst_match": worst_match,
            "signal_groups": signal_groups,
            "model_groups": model_groups,
            "hold": summarize_holds(fills),
            "wallet": wallet,
            "markout": _arm_markouts(fills),
            "board": _board_fill_slice(fills),
            "forced_liquidation": forced_liquidation,
        },
    )


def buy_ladder_assumption_lines(manifest: Mapping[str, object]) -> tuple[str, ...]:
    """Fixed Follow300 BUY-ladder notes, tagged with the policy the manifest names."""
    policy = manifest.get("buy_ladder_policy")
    layers = manifest.get("layers")
    rung_usdc = manifest.get("layer_usdc")
    max_levels = manifest.get("max_position_levels")
    lines = [
        f"buy-ladder: {policy}. {layers} BUY rungs of ${rung_usdc} on the episode token, "
        "one tick apart, starting at the best bid.",
        "latch-then-follow: rung prices stay latched on the last anchor — a model tick "
        "or the 0.25 s re-anchor between ticks — until the episode's first BUY "
        "execution; after it, all rungs follow the book, filled ones included.",
        f"A filled rung quotes a fresh ${rung_usdc} BUY again as soon as the entry gate "
        "passes; the only buy-side limit is the shared position cap: held cost plus "
        f"standing BUY remainder stay under {max_levels} "
        f"rungs (${rung_usdc} each) across every card. Quantities round down; a new "
        "order below the market minimum is skipped.",
    ]
    return tuple(lines)


def build_summary_payload(
    *,
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    drawdowns: Mapping[int, float],
    coverage: ValidationCoverage | None,
    selected: int,
    wall_seconds: float,
    manifest: Mapping[str, object],
    telemetry: QuoteTelemetry,
    mids: Mapping[int, MidSeries],
    contexts: Mapping[int, MarketContext],
) -> SummaryPayload:
    """summary.json body: coverage, one-arm metrics, assumptions, manifest."""
    summary = summarize_arm(results, fills, drawdowns, telemetry, mids, contexts)
    placement = results[0].placement if results else "join"
    fill_model = results[0].fill_model if results else "queue"
    arms: list[ArmPayload] = [
        cast(ArmPayload, {"placement": placement, "fill_model": fill_model, **summary})
    ]
    payload: SummaryPayload = {
        "selected": selected,
        "wall_seconds": wall_seconds,
        "arms": arms,
        "assumptions": (ASSUMPTIONS, *buy_ladder_assumption_lines(manifest)),
        "manifest": dict(manifest),
    }
    if coverage is not None:
        payload["coverage"] = asdict(coverage)
    return payload


def write_fills_parquet(*, report_dir: Path, fills: Sequence[EnrichedFill]) -> None:
    """Overwrite fills.parquet with every enriched fill collected so far across arms."""
    frame = pd.DataFrame([asdict(fill) for fill in fills])
    write_parquet(frame, report_dir / FILLS_FILENAME)


def read_fills_checkpoint(report_dir: Path) -> list[EnrichedFill]:
    """Enriched fills an earlier run already wrote; empty when there is no parquet."""
    frame = load_parquet(report_dir / FILLS_FILENAME)
    if frame.empty:
        return []
    field_names = [field.name for field in fields(EnrichedFill)]
    if "board_age_seconds" not in frame.columns:
        frame = frame.assign(board_age_seconds=float("nan"))
    missing = [name for name in field_names if name not in frame.columns]
    if missing:
        raise ValueError(
            f"fills.parquet is missing columns {missing}; refuse resume of an incompatible checkpoint"
        )
    ordered = frame[field_names].itertuples(index=False, name=None)
    return [EnrichedFill(*row) for row in ordered]
