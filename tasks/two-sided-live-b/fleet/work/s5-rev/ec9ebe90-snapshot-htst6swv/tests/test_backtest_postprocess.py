"""Unit tests for maker post-processing: rebate, markout, bootstrap, drawdown, summary."""

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

from backtest.chart import CHART_HEIGHT, render_balance_chart
from backtest.context import MarketContext, calculate_clock_end, calculate_replay_window
from backtest.marks import (
    EnrichedFill,
    MidSeries,
    lookup_reference_mid,
    settlement_value_for_token,
)
from backtest.postprocess import (
    ASSUMPTIONS,
    BOOTSTRAP_SEED,
    FEE_RATE,
    REBATE_RATE,
    bootstrap_markout,
    build_summary_payload,
    calculate_cvar_5,
    calculate_match_drawdown,
    calculate_min_equity,
    enrich_fills,
    load_match_mid_series,
    summarize_arm,
    summarize_holds,
)
from backtest.quote_store import empty_quote_telemetry, quote_telemetry_from_events
from backtest.report import format_terminal_report
from backtest.results import (
    MakerMatchResult,
    count_incomplete_orders,
    zero_gate_seconds,
)
from backtest.selection import ValidationCoverage
from backtest.telemetry import FillRecord, QuoteEvent
from backtest.wallet_path import (
    SPARK_POINTS,
    calculate_reserve_path,
    calculate_wallet_path,
    downsample_time_path,
)
from shared.constants.strategy import BACKTEST_DOTA_MAX_POSITION_LEVELS, BUY_POLICY_VERSION
from shared.utils.match_time import NS_PER_SECOND, datetime_to_ns

HORN = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
GAME_END = HORN + timedelta(seconds=2000)
CLOSED = GAME_END + timedelta(hours=1)


def build_context(
    *, match_id: int = 1, radiant_token_index: int = 0, radiant_win: bool = True
) -> MarketContext:
    """Minimal market context for enrichment and drawdown."""
    replay = calculate_replay_window(horn_at=HORN, game_ended_at=GAME_END)
    return MarketContext(
        match_id=match_id,
        condition_id="0xok",
        event_id="e1",
        market_slug="demo",
        token_ids=("111", "222"),
        radiant_token_index=radiant_token_index,
        radiant_win=radiant_win,
        seconds_delay=1,
        horn_at=HORN,
        game_ended_at=GAME_END,
        market_closed_at=CLOSED,
        replay_start=replay.start,
        replay_end=replay.end,
        clock_end=calculate_clock_end(game_ended_at=GAME_END, market_closed_at=CLOSED),
    )


def build_raw_fill(
    *,
    match_id: int = 1,
    side: str = "BUY",
    price: float = 0.5,
    quantity: float = 5.0,
    submitted_quantity: float | None = None,
    ts_ns: int = 0,
    token_index: int = 0,
    order_id: str = "O-1",
    episode_id: int = 1,
    level_index: int = 0,
    is_maker: bool = True,
) -> FillRecord:
    """Raw fill used as enrich_fills input."""
    return FillRecord(
        match_id=match_id,
        token_index=token_index,
        side=side,
        price=price,
        quantity=quantity,
        submitted_quantity=quantity if submitted_quantity is None else submitted_quantity,
        ts_ns=ts_ns,
        predicted_delta=0.01,
        fair=0.5,
        book_p_radiant=0.5,
        dataset_market_p=0.5,
        spread=0.02,
        placement="join",
        queue_ahead=1.0,
        position_after=5.0,
        order_id=order_id,
        episode_id=episode_id,
        level_index=level_index,
        submit_level_index=level_index,
        level_moves=0,
        fair_at_fill=0.5,
        signal_age_seconds=1.0,
        gate_reason_at_fill="",
        episode_buy_notional=price * quantity,
        episode_sell_proceeds=0.0,
        episode_buy_fill_index=0,
        position_cost_basis=price * quantity,
        reserved_buy_notional=0.0,
        is_maker=is_maker,
    )


def build_enriched(
    *,
    match_id: int = 1,
    side: str = "BUY",
    price: float = 0.5,
    quantity: float = 5.0,
    submitted_quantity: float | None = None,
    markout_30s: float = 0.0,
    markout_300s: float = 0.0,
    order_id: str = "O-1",
    fill_model: str = "queue",
    fee_base: float = 1.25,
    maker_rebate: float = 0.009375,
    ts_ns: int = 0,
    token_index: int = 0,
    episode_id: int = 1,
    level_index: int = 0,
    board_age_seconds: float = float("nan"),
) -> EnrichedFill:
    """Enriched fill with markout columns set for bootstrap/summary tests."""
    return EnrichedFill(
        match_id=match_id,
        token_index=token_index,
        side=side,
        price=price,
        quantity=quantity,
        submitted_quantity=quantity if submitted_quantity is None else submitted_quantity,
        ts_ns=ts_ns,
        predicted_delta=0.01,
        fair=0.5,
        book_p_radiant=0.5,
        dataset_market_p=0.5,
        spread=0.02,
        placement="join",
        queue_ahead=1.0,
        position_after=quantity,
        order_id=order_id,
        episode_id=episode_id,
        level_index=level_index,
        submit_level_index=level_index,
        level_moves=0,
        fair_at_fill=0.5,
        signal_age_seconds=1.0,
        gate_reason_at_fill="",
        episode_buy_notional=price * quantity,
        episode_sell_proceeds=0.0,
        episode_buy_fill_index=0,
        position_cost_basis=price * quantity,
        reserved_buy_notional=0.0,
        is_maker=True,
        fill_model=fill_model,
        fee_base=fee_base,
        maker_rebate=maker_rebate,
        taker_fee=0.0,
        reference_30s=price + markout_30s if side == "BUY" else price - markout_30s,
        reference_300s=price + markout_300s if side == "BUY" else price - markout_300s,
        markout_30s=markout_30s,
        markout_300s=markout_300s,
        reference_source_30s="mid",
        reference_source_300s="mid",
        board_age_seconds=board_age_seconds,
    )


def build_result_row(
    *,
    match_id: int,
    engine_pnl: float = 1.0,
    cash_flow: float = -2.0,
    buy_fills: int = 1,
    sell_fills: int = 0,
    orders_submitted: int = 2,
    incomplete_orders: int = 0,
    terminated_early: bool = False,
    window_seconds: int = 10,
    live_order_seconds: int = 4,
    gate_anchor_seconds: int = 0,
    dust_position: bool = False,
    terminal_side: str = "",
    terminal_position: float = 0.0,
    signal_mode: str = "grid_v1",
    feed_source: Literal["grid", "oddin", ""] = "",
    model_name: str = "test-model",
) -> MakerMatchResult:
    """Maker result row with the US-009 fields summarize_arm reads."""
    return MakerMatchResult(
        match_id=match_id,
        condition_id=f"0x{match_id}",
        slug=f"market-{match_id}",
        seconds_delay=1,
        placement="join",
        fill_model="queue",
        horn_at=HORN.isoformat(),
        game_ended_at=GAME_END.isoformat(),
        market_closed_at=CLOSED.isoformat(),
        buy_fills=buy_fills,
        sell_fills=sell_fills,
        buy_quantity=float(5 * buy_fills),
        sell_quantity=float(5 * sell_fills),
        orders_submitted=orders_submitted,
        orders_accepted=1,
        orders_canceled=0,
        orders_rejected=0,
        incomplete_orders=incomplete_orders,
        terminal_token_index=0 if terminal_side == "radiant" else -1,
        terminal_side=terminal_side,
        terminal_position=terminal_position,
        dust_position=dust_position,
        window_seconds=window_seconds,
        live_order_seconds=live_order_seconds,
        gate_seconds=replace(zero_gate_seconds(), anchor=gate_anchor_seconds),
        cash_flow=cash_flow,
        engine_pnl=engine_pnl,
        settlement_applied=not terminated_early,
        terminated_early=terminated_early,
        stop_reason="account" if terminated_early else None,
        signal_mode=signal_mode,
        feed_source=feed_source,
        model_name=model_name,
    )


def test_rebate_formula_at_half_and_two_tenths() -> None:
    """fee_base = qty * p * (1-p); rebate = 0.15 * 0.05 * fee_base."""
    series = MidSeries(timestamps_ns=(0,), market_ps=(0.5,))
    half = enrich_fills(
        [build_raw_fill(price=0.5, quantity=5.0)],
        {1: series},
        [build_context()],
        fill_model="queue",
        board_ticks={},
    )[0]
    assert half.fee_base == pytest.approx(1.25)
    assert half.maker_rebate == pytest.approx(REBATE_RATE * FEE_RATE * 1.25)
    assert half.maker_rebate == pytest.approx(0.009375)

    two_tenths = enrich_fills(
        [build_raw_fill(price=0.2, quantity=5.0)],
        {1: series},
        [build_context()],
        fill_model="queue",
        board_ticks={},
    )[0]
    assert two_tenths.fee_base == pytest.approx(0.8)
    assert two_tenths.maker_rebate == pytest.approx(0.006)


def test_taker_fill_pays_fee_and_gets_no_rebate() -> None:
    series = MidSeries(timestamps_ns=(0,), market_ps=(0.5,))
    taker = enrich_fills(
        [build_raw_fill(price=0.5, quantity=5.0, is_maker=False)],
        {1: series},
        [build_context()],
        fill_model="queue",
        board_ticks={},
    )[0]
    assert taker.maker_rebate == 0.0
    assert taker.taker_fee == pytest.approx(FEE_RATE * 1.25)


def test_markout_sign_is_reference_minus_price_for_buy() -> None:
    """BUY markout is reference - price; SELL is price - reference."""
    series = MidSeries(timestamps_ns=(0, 30 * NS_PER_SECOND), market_ps=(0.40, 0.50))
    buy = enrich_fills(
        [build_raw_fill(side="BUY", price=0.40, ts_ns=0)],
        {1: series},
        [build_context()],
        fill_model="queue",
        board_ticks={},
    )[0]
    sell = enrich_fills(
        [build_raw_fill(side="SELL", price=0.40, ts_ns=0)],
        {1: series},
        [build_context()],
        fill_model="queue",
        board_ticks={},
    )[0]
    assert buy.reference_30s == pytest.approx(0.50)
    assert buy.markout_30s == pytest.approx(0.10)
    assert sell.reference_30s == pytest.approx(0.50)
    assert sell.markout_30s == pytest.approx(-0.10)


def test_reference_is_mid_at_fill_ts_plus_horizon() -> None:
    """30s and 300s references read the mid at fill_ts + h, not the fill-time mid."""
    series = MidSeries(
        timestamps_ns=(0, 30 * NS_PER_SECOND, 300 * NS_PER_SECOND),
        market_ps=(0.40, 0.45, 0.60),
    )
    fill = enrich_fills(
        [build_raw_fill(side="BUY", price=0.40, ts_ns=0)],
        {1: series},
        [build_context()],
        fill_model="queue",
        board_ticks={},
    )[0]
    assert fill.reference_30s == pytest.approx(0.45)
    assert fill.reference_300s == pytest.approx(0.60)
    assert fill.reference_source_30s == "mid"
    assert fill.reference_source_300s == "mid"


def test_as_of_falls_back_to_last_fresh_mid() -> None:
    """When t+h is past the last ok row, as-of lands on that last mid."""
    series = MidSeries(timestamps_ns=(0, 30 * NS_PER_SECOND), market_ps=(0.40, 0.55))
    assert lookup_reference_mid(series, 300 * NS_PER_SECOND) == pytest.approx(0.55)
    fill = enrich_fills(
        [build_raw_fill(side="BUY", price=0.40, ts_ns=0)],
        {1: series},
        [build_context()],
        fill_model="queue",
        board_ticks={},
    )[0]
    assert fill.reference_300s == pytest.approx(0.55)
    assert fill.reference_source_300s == "mid"


def test_settlement_fallback_for_both_radiant_win_orientations() -> None:
    """Empty mid series uses settlement: 1 if the filled token's side won."""
    empty = MidSeries(timestamps_ns=(), market_ps=())
    radiant_won = enrich_fills(
        [build_raw_fill(token_index=0), build_raw_fill(token_index=1, order_id="O-2")],
        {1: empty},
        [build_context(radiant_win=True)],
        fill_model="queue",
        board_ticks={},
    )
    assert radiant_won[0].reference_300s == pytest.approx(1.0)
    assert radiant_won[1].reference_300s == pytest.approx(0.0)
    assert radiant_won[0].reference_source_300s == "settlement"

    dire_won = enrich_fills(
        [build_raw_fill(token_index=0), build_raw_fill(token_index=1, order_id="O-2")],
        {1: empty},
        [build_context(radiant_win=False)],
        fill_model="queue",
        board_ticks={},
    )
    assert dire_won[0].reference_300s == pytest.approx(0.0)
    assert dire_won[1].reference_300s == pytest.approx(1.0)
    assert settlement_value_for_token(0, 1, True) == pytest.approx(0.0)
    assert settlement_value_for_token(1, 1, True) == pytest.approx(1.0)


def test_quantity_weighted_markout_and_bootstrap_determinism() -> None:
    """Point estimate is qty-weighted; the same seed reproduces the CI."""
    fills = [
        build_enriched(match_id=1, quantity=1.0, markout_300s=0.10, order_id="A"),
        build_enriched(match_id=2, quantity=3.0, markout_300s=0.00, order_id="B"),
    ]
    first = bootstrap_markout(fills, side="BUY", horizon=300, iterations=200, seed=BOOTSTRAP_SEED)
    second = bootstrap_markout(fills, side="BUY", horizon=300, iterations=200, seed=BOOTSTRAP_SEED)
    assert first is not None
    assert second is not None
    assert first.estimate == pytest.approx(0.025)
    assert first == second
    other_seed = bootstrap_markout(fills, side="BUY", horizon=300, iterations=200, seed=1)
    assert other_seed is not None
    assert other_seed.estimate == pytest.approx(0.025)


def test_zero_fill_arm_returns_none_markout() -> None:
    """An arm with no fills of that side reports JSON-null markout."""
    sells = [build_enriched(side="SELL", markout_300s=0.01)]
    assert bootstrap_markout(sells, side="BUY", horizon=300, iterations=10, seed=1) is None
    assert bootstrap_markout([], side="BUY", horizon=300, iterations=10, seed=1) is None


def test_summarize_arm_chain_excludes_terminated_and_reports_stub() -> None:
    """Fill rate, turnover, uptime, rebate, remainder, dust, and stub fields."""
    clean = build_result_row(
        match_id=1,
        buy_fills=2,
        orders_submitted=2,
        incomplete_orders=1,
        engine_pnl=1.0,
        cash_flow=-2.0,
        window_seconds=10,
        live_order_seconds=4,
        gate_anchor_seconds=3,
        dust_position=True,
        terminal_side="radiant",
        terminal_position=3.0,
    )
    terminated = build_result_row(
        match_id=2,
        buy_fills=1,
        orders_submitted=1,
        engine_pnl=99.0,
        cash_flow=-9.0,
        terminated_early=True,
    )
    fills = [
        build_enriched(
            match_id=1,
            quantity=2.0,
            price=0.5,
            fee_base=0.5,
            maker_rebate=0.00375,
            order_id="O-1",
            markout_300s=0.02,
        ),
        build_enriched(
            match_id=1,
            quantity=2.0,
            price=0.5,
            fee_base=0.5,
            maker_rebate=0.00375,
            order_id="O-1",
            markout_300s=0.02,
        ),
    ]
    drawdowns = {
        1: 0.25,
        2: 9.0,
    }
    terminated_events = [
        QuoteEvent(
            match_id=2,
            ts_ns=1,
            kind="no_quote",
            token_index=-1,
            side="",
            price=0.0,
            reason="nw_velocity",
            predicted_delta=0.0,
            fair=0.0,
            book_p_radiant=0.0,
            spread=0.0,
            episode_id=0,
            order_id="",
            quantity=0.0,
            level_index=-1,
            submit_level_index=-1,
            reserved_buy_notional=0.0,
        )
    ]
    summary = summarize_arm(
        [clean, terminated],
        fills,
        drawdowns,
        quote_telemetry_from_events(terminated_events),
        {},
        {},
    )
    assert summary["matches"] == 2
    assert summary["completed"] == 1
    assert summary["terminated"] == 1
    assert summary["fill_rate"] == pytest.approx(0.5)
    assert summary["filled_quantity"] == pytest.approx(4.0)
    assert summary["incomplete_orders"] == 1
    assert summary["incomplete_order_rate"] == pytest.approx(1.0)
    assert summary["buy_turnover"] == pytest.approx(2.0)
    assert summary["median_buy_order_notional"] == pytest.approx(2.0)
    assert summary["sell_turnover"] == pytest.approx(0.0)
    assert summary["window_seconds"] == 10
    assert summary["live_order_seconds"] == 4
    assert summary["gate_anchor_seconds"] == 3
    assert summary["gate_nw_velocity_seconds"] == 0
    assert summary["quote_events"] == {
        "by_kind": {},
        "canceled_reason": {},
    }
    assert summary["terminal_radiant_inventory"] == pytest.approx(3.0)
    assert summary["terminal_dire_inventory"] == pytest.approx(0.0)
    assert summary["pnl_before_rebate"] == pytest.approx(1.0)
    assert summary["maker_rebate"] == pytest.approx(0.0075)
    assert summary["taker_fee"] == pytest.approx(0.0)
    assert summary["net_pnl"] == pytest.approx(1.0075)
    assert summary["settlement_remainder"] == pytest.approx(3.0)
    assert summary["dust_positions"] == 1
    assert summary["max_match_drawdown"] == pytest.approx(0.25)
    assert summary["min_equity"] == pytest.approx(0.0)
    assert summary["median_match_pnl"] == pytest.approx(1.0)
    assert summary["hold"]["closed"] == 0
    assert summary["bought_shares"] == pytest.approx(10.0)
    assert summary["pnl_per_eligible_match"] == pytest.approx(1.0)
    assert summary["pnl_per_bought_share"] == pytest.approx(0.1)
    assert summary["net_pnl_per_match"] == pytest.approx(1.0075)
    assert summary["loss_match_rate"] == pytest.approx(0.0)
    assert summary["cvar_5"] == pytest.approx(1.0)
    assert summary["worst_match"] == pytest.approx(1.0)
    assert summary["terminal_inventory"] == pytest.approx(3.0)
    assert summary["forced_liquidation"] == {
        "taker_fee": None,
        "net_pnl": None,
        "unliquidatable": None,
    }
    assert summary["markout"]["buy_300s"] is not None
    assert summary["markout"]["sell_300s"] is None
    removed = {
        "forced_hold_maps",
        "forced_hold_shares",
        "pnl_at_cutoff",
        "tail_pnl",
        "settled_pnl",
    }
    assert removed.isdisjoint(summary)


def test_drawdown_tracks_adverse_mid_and_settlement_terminal() -> None:
    """Peak-to-trough includes an adverse mid move and the settlement mark."""
    fills = [
        build_enriched(side="BUY", price=0.5, quantity=5.0, ts_ns=NS_PER_SECOND, token_index=0)
    ]
    series = MidSeries(timestamps_ns=(0, 2 * NS_PER_SECOND), market_ps=(0.50, 0.40))
    drawdown = calculate_match_drawdown(fills, series, 0.0, 0)
    assert drawdown == pytest.approx(2.5)
    assert calculate_match_drawdown([], series, 0.0, 0) == 0.0


def test_min_equity_is_lowest_running_pnl_vs_zero() -> None:
    """Sorts by horn, ignores terminated, keeps the most negative cumulative PnL."""
    first = replace(
        build_result_row(match_id=2, engine_pnl=1.0),
        horn_at=HORN.isoformat(),
    )
    second = replace(
        build_result_row(match_id=1, engine_pnl=-2.0),
        horn_at=(HORN + timedelta(hours=1)).isoformat(),
    )
    dead = replace(
        build_result_row(match_id=3, engine_pnl=-100.0, terminated_early=True),
        horn_at=(HORN + timedelta(hours=2)).isoformat(),
    )
    assert calculate_min_equity([second, first, dead]) == pytest.approx(-1.0)
    assert calculate_min_equity([]) == 0.0


def test_load_match_mid_series_projects_ok_rows_and_names_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache read keeps only ok mids; a missing file names the match."""
    path = tmp_path / "match.parquet"
    pd.DataFrame(
        {
            "state_ts_us": [1_000_000, 2_000_000, 3_000_000],
            "market_p_radiant": [0.4, 0.5, 0.6],
            "market_status": ["ok", "stale_quote", "ok"],
        }
    ).to_parquet(path, index=False)

    def present_cache_path(_match_id: int) -> Path:
        """Return the fixture parquet for any match id."""
        return path

    monkeypatch.setattr("backtest.postprocess.market_seconds_cache_path", present_cache_path)
    series = load_match_mid_series([7])[7]
    assert series.timestamps_ns == (1_000_000_000, 3_000_000_000)
    assert series.market_ps == (0.4, 0.6)

    missing = tmp_path / "absent.parquet"

    def missing_cache_path(_match_id: int) -> Path:
        """Return a path that does not exist."""
        return missing

    monkeypatch.setattr("backtest.postprocess.market_seconds_cache_path", missing_cache_path)
    with pytest.raises(FileNotFoundError):
        load_match_mid_series([9])


def test_cvar_5_is_mean_of_worst_five_percent() -> None:
    """Empty is 0; 20 matches take 1 worst, 40 matches average the 2 worst."""
    assert calculate_cvar_5([]) == 0.0
    assert calculate_cvar_5([1.0]) == pytest.approx(1.0)
    assert calculate_cvar_5([float(value) for value in range(20)]) == pytest.approx(0.0)
    assert calculate_cvar_5([float(value) for value in range(40)]) == pytest.approx(0.5)


def test_summarize_arm_loss_rate_and_cvar_use_completed_pnls() -> None:
    """Terminated rows are dropped; CVaR and loss-rate use completed match PnLs."""
    rows = [
        build_result_row(match_id=1, engine_pnl=-10.0, buy_fills=1),
        build_result_row(match_id=2, engine_pnl=-2.0, buy_fills=1),
        build_result_row(match_id=3, engine_pnl=1.0, buy_fills=1),
        build_result_row(match_id=4, engine_pnl=3.0, buy_fills=0, sell_fills=0),
        build_result_row(match_id=5, engine_pnl=99.0, terminated_early=True),
    ]
    summary = summarize_arm(rows, [], {}, empty_quote_telemetry(), {}, {})
    assert summary["completed"] == 4
    assert summary["loss_match_rate"] == pytest.approx(0.5)
    assert summary["worst_match"] == pytest.approx(-10.0)
    assert summary["cvar_5"] == pytest.approx(-10.0)
    assert summary["min_equity"] == pytest.approx(-12.0)
    assert summary["bought_shares"] == pytest.approx(15.0)
    assert summary["terminal_inventory"] == pytest.approx(0.0)
    assert summary["median_match_pnl"] == pytest.approx(-0.5)


def test_summarize_holds_closed_round_trips() -> None:
    """Hold is flatten minus open; closed trips keep min/p50/p90 only."""
    fills = [
        build_enriched(match_id=1, side="BUY", ts_ns=0, quantity=5.0, order_id="B1"),
        build_enriched(
            match_id=1,
            side="SELL",
            ts_ns=10 * NS_PER_SECOND,
            quantity=5.0,
            order_id="S1",
        ),
        build_enriched(match_id=2, side="BUY", ts_ns=0, quantity=5.0, order_id="B2"),
        build_enriched(
            match_id=2,
            side="SELL",
            ts_ns=300 * NS_PER_SECOND,
            quantity=5.0,
            order_id="S2",
        ),
    ]
    holds = summarize_holds(fills)
    assert holds["closed"] == 2
    assert holds["min_seconds"] == pytest.approx(10.0)
    assert "unwind_rate" not in holds


def test_format_terminal_report_prints_compact_sections() -> None:
    """Two-column report names eligible vs completed and wallet capital, not dust."""
    rows = [
        build_result_row(match_id=1, engine_pnl=-2.0),
        build_result_row(match_id=2, engine_pnl=1.0, buy_fills=0, sell_fills=0),
        build_result_row(match_id=3, engine_pnl=99.0, terminated_early=True),
    ]
    payload = build_summary_payload(
        results=rows,
        fills=[],
        drawdowns={},
        coverage=ValidationCoverage(
            validation_matches=20,
            without_map_market=0,
            without_signal_rows=0,
            without_local_telonex=0,
            archive_excluded=0,
            eligible=10,
        ),
        selected=2,
        wall_seconds=1.0,
        manifest={
            "model_name": "20260821T000000Z",
            "exit_settle_seconds": 10.0,
            "layer_usdc": 100.0,
            "signal_cadence_seed": 2,
            "max_signal_age_seconds": 16.0,
            "sell_full_age_seconds": 10.0,
            "sell_ask_age_seconds": 25.0,
            "archive_exclusions": {},
        },
        telemetry=empty_quote_telemetry(),
        mids={},
        contexts={},
    )
    text = format_terminal_report(payload)
    assert "join/queue" in text
    assert "20260821T000000Z" in text
    assert "size" in text
    assert "$100" in text
    assert "unwind" not in text
    assert "  seed                      2" in text
    assert "  sigage                    16s" in text
    assert "  sellfull                  10s" in text
    assert "  sellask                   25s" in text
    assert "RUN" in text
    assert "PNL" in text
    assert "FILLS" in text
    assert "HOLD" in text
    assert "RISK" in text
    assert "traded matches" in text
    assert "traded + no-trade" in text
    assert "median clip" in text
    assert "median match pnl" in text
    assert "-$0.500" in text
    assert "cash est (fills only)" in text
    assert "deposit w/ reserves" in text
    assert "peak reserved" in text
    assert "net / deposit w/ reserves" in text
    assert "lowest capital point" in text
    assert "below zero" not in text
    assert "max match drawdown" not in text
    assert "dust" not in text
    assert "BALANCE" not in text
    assert "hold" in text
    assert "WARNING: terminated > 0; do not trust total PnL" in text
    assert "incomplete_order" not in text
    assert "terminal_inventory" not in text
    assert "1. Correctness" not in text
    assert "{" not in text
    assert "**" not in text
    assert "|" not in text


def test_three_fill_pieces_summing_to_submitted_are_complete() -> None:
    """Three pieces that add to submitted qty are not a partial order."""
    fills = [
        build_raw_fill(order_id="O-1", quantity=1.0, submitted_quantity=5.0),
        build_raw_fill(order_id="O-1", quantity=2.0, submitted_quantity=5.0),
        build_raw_fill(order_id="O-1", quantity=2.0, submitted_quantity=5.0),
    ]
    assert count_incomplete_orders(fills) == 0


def test_partial_fill_then_cancel_counts_one_incomplete_order() -> None:
    """A leftover after cancel is one incomplete order, not one incomplete fill."""
    fills = [build_raw_fill(order_id="O-1", quantity=2.0, submitted_quantity=5.0)]
    assert count_incomplete_orders(fills) == 1


def test_incomplete_order_rate_is_incomplete_over_filled_orders() -> None:
    """Rate is incomplete orders / orders with at least one fill."""
    row = build_result_row(match_id=1, buy_fills=2, orders_submitted=2, incomplete_orders=1)
    fills = [
        build_enriched(order_id="O-1", quantity=5.0),
        build_enriched(order_id="O-2", quantity=2.0),
    ]
    summary = summarize_arm([row], fills, {}, empty_quote_telemetry(), {}, {})
    assert summary["incomplete_orders"] == 1
    assert summary["incomplete_order_rate"] == pytest.approx(0.5)


def test_median_buy_order_notional_is_median_of_per_order_buy_clips() -> None:
    """BUY notionals are summed per order_id; SELL is ignored."""
    row = build_result_row(match_id=1, buy_fills=2, sell_fills=1)
    fills = [
        build_enriched(order_id="A", price=0.5, quantity=5.0),
        build_enriched(order_id="B", price=0.4, quantity=10.0),
        build_enriched(order_id="C", side="SELL", price=0.9, quantity=100.0),
    ]
    summary = summarize_arm([row], fills, {}, empty_quote_telemetry(), {}, {})
    assert summary["median_buy_order_notional"] == pytest.approx(3.25)


def test_median_buy_order_notional_is_none_without_buys() -> None:
    """No BUY fill means JSON null and the terminal prints n/a."""
    row = build_result_row(match_id=1, buy_fills=0, sell_fills=1)
    fills = [build_enriched(side="SELL")]
    summary = summarize_arm([row], fills, {}, empty_quote_telemetry(), {}, {})
    assert summary["median_buy_order_notional"] is None
    payload = build_summary_payload(
        results=[row],
        fills=fills,
        drawdowns={},
        coverage=None,
        selected=1,
        wall_seconds=1.0,
        manifest={"archive_exclusions": {}},
        telemetry=empty_quote_telemetry(),
        mids={},
        contexts={},
    )
    text = format_terminal_report(payload)
    assert "median clip" in text
    assert "n/a" in text


def test_summary_assumptions_describe_the_fixed_follow300_ladder() -> None:
    """assumptions[0] is the shared block; the ladder lines describe latch-then-follow."""
    payload = build_summary_payload(
        results=[],
        fills=[],
        drawdowns={},
        coverage=None,
        selected=0,
        wall_seconds=0.0,
        manifest={
            "buy_ladder_policy": BUY_POLICY_VERSION,
            "layers": 3,
            "layer_usdc": 100.0,
            "max_position_levels": BACKTEST_DOTA_MAX_POSITION_LEVELS,
        },
        telemetry=empty_quote_telemetry(),
        mids={},
        contexts={},
    )
    assert payload["assumptions"][0] == ASSUMPTIONS
    text = " ".join(payload["assumptions"][1:])
    assert BUY_POLICY_VERSION in text
    assert "3 BUY rungs of $100.0" in text
    assert "one tick apart" in text
    assert "latch-then-follow" in text
    assert "fresh $100.0 BUY" in text
    assert "shared position cap" in text
    assert f"{BACKTEST_DOTA_MAX_POSITION_LEVELS} rungs" in text


def test_format_terminal_report_prints_median_clip_money() -> None:
    """RUN prints median BUY notional next to turnover."""
    row = build_result_row(match_id=1)
    fills = [build_enriched(price=0.5, quantity=5.0, order_id="A")]
    payload = build_summary_payload(
        results=[row],
        fills=fills,
        drawdowns={},
        coverage=None,
        selected=1,
        wall_seconds=1.0,
        manifest={"archive_exclusions": {}},
        telemetry=empty_quote_telemetry(),
        mids={},
        contexts={},
    )
    text = format_terminal_report(payload)
    assert "median clip" in text
    assert "$2.50" in text


def test_wallet_overlapping_buys_sum_required_cash() -> None:
    """Two open clips at once: required cash is the sum of notionals."""
    fills = [
        build_enriched(
            match_id=1, side="BUY", price=0.5, quantity=5.0, ts_ns=NS_PER_SECOND, order_id="B1"
        ),
        build_enriched(
            match_id=2, side="BUY", price=0.4, quantity=5.0, ts_ns=NS_PER_SECOND, order_id="B2"
        ),
    ]
    path = calculate_wallet_path(
        [build_result_row(match_id=1), build_result_row(match_id=2)],
        fills,
        {},
        {},
    )
    assert path.required_cash == pytest.approx(4.5)
    assert path.maps_at_once == 2


def test_wallet_same_timestamp_fills_on_one_match() -> None:
    """Two fills on one match at the same ts sort without comparing fill objects."""
    fills = [
        build_enriched(
            match_id=1, side="BUY", price=0.5, quantity=100.0, ts_ns=NS_PER_SECOND, order_id="B1a"
        ),
        build_enriched(
            match_id=1, side="BUY", price=0.5, quantity=108.33, ts_ns=NS_PER_SECOND, order_id="B1b"
        ),
    ]
    path = calculate_wallet_path([build_result_row(match_id=1)], fills, {}, {})
    assert path.required_cash == pytest.approx(104.165)


def test_wallet_loss_then_buy_includes_the_hole() -> None:
    """A later clip needs the leftover loss plus the new notional, not the clip alone."""
    fills = [
        build_enriched(
            match_id=1, side="BUY", price=0.5, quantity=5.0, ts_ns=NS_PER_SECOND, order_id="B1"
        ),
        build_enriched(
            match_id=1,
            side="SELL",
            price=0.3,
            quantity=5.0,
            ts_ns=2 * NS_PER_SECOND,
            order_id="S1",
        ),
        build_enriched(
            match_id=2, side="BUY", price=0.8, quantity=5.0, ts_ns=3 * NS_PER_SECOND, order_id="B2"
        ),
    ]
    path = calculate_wallet_path(
        [build_result_row(match_id=1), build_result_row(match_id=2)],
        fills,
        {},
        {},
    )
    assert path.required_cash == pytest.approx(5.0)


def test_wallet_adverse_mid_drops_lowest_capital_not_required() -> None:
    """MTM dip after a BUY lowers the trough; cash required stays the buy notional."""
    fills = [
        build_enriched(
            match_id=1, side="BUY", price=0.5, quantity=5.0, ts_ns=NS_PER_SECOND, token_index=0
        )
    ]
    series = MidSeries(timestamps_ns=(0, 2 * NS_PER_SECOND), market_ps=(0.50, 0.30))
    context = build_context(match_id=1, radiant_token_index=0, radiant_win=True)
    path = calculate_wallet_path(
        [build_result_row(match_id=1, terminal_side="radiant", terminal_position=5.0)],
        fills,
        {1: series},
        {1: context},
    )
    assert path.required_cash == pytest.approx(2.5)
    assert path.lowest_capital == pytest.approx(1.5)


def test_wallet_roi_on_closed_round() -> None:
    """PnL $2 on required $4 is 50% ROI and $6 final balance when rebate is zero."""
    fills = [
        build_enriched(
            match_id=1,
            side="BUY",
            price=0.5,
            quantity=8.0,
            ts_ns=0,
            maker_rebate=0.0,
            order_id="B1",
        ),
        build_enriched(
            match_id=1,
            side="SELL",
            price=0.75,
            quantity=8.0,
            ts_ns=NS_PER_SECOND,
            maker_rebate=0.0,
            order_id="S1",
        ),
    ]
    row = build_result_row(
        match_id=1,
        engine_pnl=2.0,
        cash_flow=2.0,
        buy_fills=1,
        sell_fills=1,
    )
    row = replace(row, buy_quantity=8.0, sell_quantity=8.0)
    summary = summarize_arm([row], fills, {}, empty_quote_telemetry(), {}, {})
    assert summary["wallet"]["required_cash"] == pytest.approx(4.0)
    assert summary["wallet"]["roi_before_rebate"] == pytest.approx(0.5)
    assert summary["wallet"]["final_balance_with_rebate"] == pytest.approx(6.0)


def test_downsample_and_chart_keep_start_line() -> None:
    """200 time samples collapse to 100; a flat path still draws the starting-capital row."""
    sampled = downsample_time_path([(i, float(i)) for i in range(200)], SPARK_POINTS)
    assert len(sampled) == SPARK_POINTS
    flat = render_balance_chart([5.0] * SPARK_POINTS, 5.0)
    assert "─" in flat
    assert "┼" in flat
    assert "5.00" in flat
    wiggly = render_balance_chart(sampled, 50.0)
    assert "┤" in wiggly
    assert "─" in wiggly
    assert "50.00" in wiggly


def test_downsample_keeps_first_value() -> None:
    """Bucket 0 stays the start vertex even when later t0 samples drop."""
    values = [100.0 - float(i) for i in range(200)]
    sampled = downsample_time_path([(i, v) for i, v in enumerate(values)], SPARK_POINTS)
    assert sampled[0] == values[0]


def test_chart_start_row_prints_starting_capital() -> None:
    """Y labels count from starting_capital, not from whichever row drew more dashes."""
    path = [6.10] + [40.0] * (SPARK_POINTS - 1)
    chart = render_balance_chart(path, 6.10)
    start_line = next(line for line in chart.splitlines() if line.lstrip().startswith("6.10"))
    dashiest = max(chart.splitlines(), key=lambda line: line.count("─"))
    if dashiest != start_line:
        assert not dashiest.lstrip().startswith("6.10")


def test_downsample_pins_path_min() -> None:
    """A one-sample hole is kept even when it is not last in its window."""
    values = [10.0] * 100
    values[40] = 1.0
    sampled = downsample_time_path([(i, v) for i, v in enumerate(values)], 10)
    assert len(sampled) == 10
    assert 1.0 in sampled


def test_wallet_buy_sell_same_ns_required_cash_ignores_match_id_order() -> None:
    """Simultaneous BUY+SELL net cash does not depend on which match id sorts first."""
    t0 = NS_PER_SECOND
    t1 = 2 * NS_PER_SECOND

    def path_for(buy_id: int, sell_id: int) -> float:
        fills = [
            build_enriched(
                match_id=sell_id, side="BUY", price=0.5, quantity=5.0, ts_ns=t0, order_id="open"
            ),
            build_enriched(
                match_id=buy_id, side="BUY", price=0.8, quantity=5.0, ts_ns=t1, order_id="B"
            ),
            build_enriched(
                match_id=sell_id, side="SELL", price=0.5, quantity=5.0, ts_ns=t1, order_id="S"
            ),
        ]
        path = calculate_wallet_path(
            [build_result_row(match_id=buy_id), build_result_row(match_id=sell_id)],
            fills,
            {},
            {},
        )
        return path.required_cash

    assert path_for(1, 2) == pytest.approx(path_for(2, 1))


def test_downsample_min_right_after_start_skips_bucket_zero() -> None:
    """A trough at t0 after the deposit vertex is in the spark and not in bucket 0."""
    samples = [(0, 100.0), (0, 10.0), (100, 80.0)]
    sampled = downsample_time_path(samples, SPARK_POINTS)
    assert sampled[0] == 100.0
    assert 10.0 in sampled
    assert sampled[0] != 10.0


def test_downsample_zero_span_pins_min_off_start() -> None:
    """Zero-width tape keeps bucket 0 as start and pins the later min into buckets > 0."""
    sampled = downsample_time_path([(5, 8.0), (5, 3.0)], 4)
    assert sampled[0] == 8.0
    assert 3.0 in sampled[1:]
    assert len(sampled) == 4


def test_chart_has_configured_height() -> None:
    """Custom renderer occupies CHART_HEIGHT rows, not the old 16-row library plot."""
    chart = render_balance_chart([5.0] * SPARK_POINTS, 5.0)
    assert len(chart.splitlines()) == CHART_HEIGHT


def test_chart_step_corners_face_each_other() -> None:
    """A one-row rise puts ╭ above ╯ in the same column so the stroke meets."""
    path = [float(i) for i in range(CHART_HEIGHT)]
    lines = render_balance_chart(path, 0.0).splitlines()
    grid = [line[12:] for line in lines]
    assert grid[CHART_HEIGHT - 2][0] == "╭"
    assert grid[CHART_HEIGHT - 1][0] == "╯"


def test_wallet_start_snapshot_before_same_ts_fill() -> None:
    """Deposit vertex is recorded before a fill and adverse mid at the first timestamp."""
    t0 = NS_PER_SECOND
    fills = [
        build_enriched(match_id=1, side="BUY", price=0.5, quantity=5.0, ts_ns=t0, token_index=0)
    ]
    series = MidSeries(timestamps_ns=(t0,), market_ps=(0.30,))
    context = build_context(match_id=1, radiant_token_index=0, radiant_win=True)
    path = calculate_wallet_path(
        [build_result_row(match_id=1, terminal_side="radiant", terminal_position=5.0)],
        fills,
        {1: series},
        {1: context},
    )
    assert path.equity_spark[0] == pytest.approx(path.required_cash)
    assert path.lowest_capital == pytest.approx(path.required_cash - 1.0)
    assert path.lowest_capital in path.equity_spark
    assert path.equity_spark[0] != pytest.approx(path.lowest_capital)


def build_reserve_event(
    *,
    kind: str,
    order_id: str,
    ts_ns: int,
    price: float,
    quantity: float,
    match_id: int = 1,
    side: str = "BUY",
) -> QuoteEvent:
    """One BUY lifecycle event as the reserve ledger reads it."""
    return QuoteEvent(
        match_id=match_id,
        ts_ns=ts_ns,
        kind=kind,
        token_index=0,
        side=side,
        price=price,
        reason="",
        predicted_delta=0.0,
        fair=0.0,
        book_p_radiant=0.0,
        spread=0.0,
        episode_id=1,
        order_id=order_id,
        quantity=quantity,
        level_index=0,
        submit_level_index=0,
        reserved_buy_notional=0.0,
    )


def test_reserve_path_holds_a_canceling_buy_until_the_venue_acks() -> None:
    """$33 held plus two $33 BUYs needs $99; the ack of one releases it back to $66."""
    fills = [
        build_enriched(
            match_id=1, side="BUY", price=0.33, quantity=100.0, ts_ns=NS_PER_SECOND, order_id="B0"
        )
    ]
    quote_events = [
        build_reserve_event(kind="submitted", order_id="B0", ts_ns=0, price=0.33, quantity=100.0),
        build_reserve_event(kind="submitted", order_id="B1", ts_ns=0, price=0.33, quantity=100.0),
        build_reserve_event(kind="submitted", order_id="B2", ts_ns=0, price=0.33, quantity=100.0),
        build_reserve_event(
            kind="cancel_ack",
            order_id="B2",
            ts_ns=2 * NS_PER_SECOND,
            price=0.33,
            quantity=100.0,
        ),
    ]
    results = [build_result_row(match_id=1)]
    before_ack = calculate_reserve_path(
        results,
        fills,
        [event for event in quote_events if event.kind != "cancel_ack"],
        {},
        settlement_at="game_end",
    )
    assert before_ack.required_cash == pytest.approx(99.0)
    after_ack = calculate_reserve_path(results, fills, quote_events, {}, settlement_at="game_end")
    assert after_ack.required_cash == pytest.approx(99.0)
    assert after_ack.peak_reserved == pytest.approx(99.0)


def test_reserve_path_sell_does_not_free_a_canceling_buy() -> None:
    """Selling the position back does not release a BUY that can still fill."""
    fills = [
        build_enriched(
            match_id=1, side="BUY", price=0.33, quantity=100.0, ts_ns=NS_PER_SECOND, order_id="B0"
        ),
        build_enriched(
            match_id=1,
            side="SELL",
            price=0.33,
            quantity=100.0,
            ts_ns=2 * NS_PER_SECOND,
            order_id="S0",
        ),
    ]
    quote_events = [
        build_reserve_event(kind="submitted", order_id="B0", ts_ns=0, price=0.33, quantity=100.0),
        build_reserve_event(kind="submitted", order_id="B1", ts_ns=0, price=0.33, quantity=100.0),
    ]
    path = calculate_reserve_path(
        [build_result_row(match_id=1)], fills, quote_events, {}, settlement_at="game_end"
    )
    assert path.required_cash == pytest.approx(66.0)


def test_reserve_path_settlement_timing_shifts_the_peak() -> None:
    """Waiting for market close instead of game end keeps a later BUY unfunded for longer."""
    late_buy_ns = datetime_to_ns(GAME_END + timedelta(minutes=10))
    fills = [
        build_enriched(
            match_id=1, side="BUY", price=0.5, quantity=100.0, ts_ns=NS_PER_SECOND, order_id="B0"
        ),
        build_enriched(
            match_id=2, side="BUY", price=0.5, quantity=100.0, ts_ns=late_buy_ns, order_id="B1"
        ),
    ]
    quote_events = [
        build_reserve_event(kind="submitted", order_id="B0", ts_ns=0, price=0.5, quantity=100.0),
        build_reserve_event(
            kind="submitted", order_id="B1", ts_ns=late_buy_ns, price=0.5, quantity=100.0
        ),
    ]
    results = [build_result_row(match_id=1), build_result_row(match_id=2)]
    contexts = {
        1: build_context(match_id=1, radiant_token_index=0, radiant_win=True),
        2: build_context(match_id=2, radiant_token_index=0, radiant_win=True),
    }
    at_game_end = calculate_reserve_path(
        results, fills, quote_events, contexts, settlement_at="game_end"
    )
    at_close = calculate_reserve_path(
        results, fills, quote_events, contexts, settlement_at="market_close"
    )
    assert at_game_end.required_cash < at_close.required_cash


def test_terminated_map_contributes_no_pnl_and_no_reserve() -> None:
    """A failed map's outstanding BUY must not inflate the reserve-inclusive deposit."""
    clean = build_result_row(match_id=1, buy_fills=1, orders_submitted=1, engine_pnl=1.0)
    terminated = build_result_row(
        match_id=2, buy_fills=1, orders_submitted=1, engine_pnl=99.0, terminated_early=True
    )
    fills = [
        build_enriched(
            match_id=1, side="BUY", price=0.50, quantity=100.0, ts_ns=NS_PER_SECOND, order_id="B0"
        ),
        build_enriched(
            match_id=2, side="BUY", price=0.50, quantity=100.0, ts_ns=NS_PER_SECOND, order_id="C0"
        ),
    ]
    quote_events = [
        build_reserve_event(
            kind="submitted", order_id="B0", ts_ns=0, price=0.50, quantity=100.0, match_id=1
        ),
        build_reserve_event(
            kind="submitted", order_id="C0", ts_ns=0, price=0.50, quantity=100.0, match_id=2
        ),
        build_reserve_event(
            kind="submitted", order_id="C1", ts_ns=0, price=0.50, quantity=400.0, match_id=2
        ),
    ]
    summary = summarize_arm(
        [clean, terminated], fills, {}, quote_telemetry_from_events(quote_events), {}, {}
    )
    assert summary["pnl_before_rebate"] == pytest.approx(1.0)
    assert summary["buy_turnover"] == pytest.approx(50.0)
    assert summary["wallet"]["required_cash_with_reserves"] == pytest.approx(50.0)
    assert summary["wallet"]["peak_reserved"] == pytest.approx(50.0)


def test_summarize_arm_groups_signals_and_models() -> None:
    """Arm summary counts every feed-source/model combination across all results."""
    rows = [
        build_result_row(match_id=1),
        build_result_row(
            match_id=2,
            signal_mode="schedule",
            feed_source="grid",
            model_name="research",
        ),
        build_result_row(
            match_id=3,
            signal_mode="schedule",
            feed_source="oddin",
            model_name="noxp",
        ),
        build_result_row(
            match_id=4,
            signal_mode="schedule",
            feed_source="grid",
            model_name="research",
        ),
    ]
    summary = summarize_arm(rows, [], {}, empty_quote_telemetry(), {}, {})
    assert summary["signal_groups"] == {
        "grid_v1": 1,
        "schedule:grid": 2,
        "schedule:oddin": 1,
    }
    assert summary["model_groups"] == {"test-model": 1, "research": 2, "noxp": 1}


def test_format_terminal_report_prints_signals_section() -> None:
    """SIGNALS lists feed/model groups and the manifest's exclusion reasons."""
    rows = [
        build_result_row(match_id=1),
        build_result_row(
            match_id=2,
            signal_mode="schedule",
            feed_source="grid",
            model_name="research",
        ),
    ]
    payload = build_summary_payload(
        results=rows,
        fills=[],
        drawdowns={},
        coverage=None,
        selected=2,
        wall_seconds=1.0,
        manifest={
            "model_name": "mixed",
            "exit_settle_seconds": 10.0,
            "layer_usdc": 100.0,
            "signal_cadence_seed": 0,
            "max_signal_age_seconds": 16.0,
            "archive_exclusions": {
                "7": "archive_unlinked",
                "8": "archive_unlinked",
                "9": "schedule_stale",
            },
        },
        telemetry=empty_quote_telemetry(),
        mids={},
        contexts={},
    )
    text = format_terminal_report(payload)
    assert "SIGNALS" in text
    assert "  grid_v1                   1" in text
    assert "  schedule:grid             1" in text
    assert "  excluded                  3   (archive_unlinked 2, schedule_stale 1)" in text
    assert "  model research            1" in text
    assert "  model test-model          1" in text


def test_board_age_is_seconds_since_the_latest_board_tick() -> None:
    """Fills stamp their age behind the most recent board tick, NaN before it."""
    series = MidSeries(timestamps_ns=(0,), market_ps=(0.5,))
    fills = enrich_fills(
        [
            build_raw_fill(ts_ns=95 * NS_PER_SECOND, order_id="early"),
            build_raw_fill(ts_ns=105 * NS_PER_SECOND, order_id="inside"),
            build_raw_fill(ts_ns=150 * NS_PER_SECOND, order_id="late"),
        ],
        {1: series},
        [build_context()],
        fill_model="queue",
        board_ticks={1: (100 * NS_PER_SECOND, 140 * NS_PER_SECOND)},
    )
    assert math.isnan(fills[0].board_age_seconds)
    assert fills[1].board_age_seconds == pytest.approx(5.0)
    assert fills[2].board_age_seconds == pytest.approx(10.0)


def test_summarize_arm_board_slice_counts_fills_inside_the_window() -> None:
    """The board slice reports fills <=10s after a tick, share, and 300s markouts."""
    row = build_result_row(match_id=1)
    fills = [
        build_enriched(match_id=1, markout_300s=0.10, board_age_seconds=4.0, order_id="A"),
        build_enriched(match_id=1, markout_300s=-0.20, board_age_seconds=30.0, order_id="B"),
        build_enriched(match_id=1, markout_300s=0.50, order_id="C"),
    ]
    summary = summarize_arm([row], fills, {}, empty_quote_telemetry(), {}, {})
    board = summary["board"]
    assert board["fills"] == 1
    assert board["share"] == pytest.approx(1 / 3)
    assert board["buy_300s"] is not None
    assert board["buy_300s"]["estimate"] == pytest.approx(0.10)
    assert board["buy_300s"]["fills"] == 1
    assert board["sell_300s"] is None


def test_format_terminal_report_prints_board_slice_by_default() -> None:
    """The canonical report always includes the scoreboard slice."""
    rows = [build_result_row(match_id=1)]
    fills = [
        build_enriched(match_id=1, markout_300s=0.10, board_age_seconds=4.0, order_id="A"),
        build_enriched(match_id=1, markout_300s=0.50, order_id="B"),
    ]
    manifest: dict[str, object] = {
        "model_name": "test-model",
        "exit_settle_seconds": 10.0,
        "layer_usdc": 100.0,
        "signal_cadence_seed": 0,
        "max_signal_age_seconds": 16.0,
        "archive_exclusions": {},
        "signal_contract": "received-table-board-v1",
    }
    payload = build_summary_payload(
        results=rows,
        fills=fills,
        drawdowns={},
        coverage=None,
        selected=1,
        wall_seconds=1.0,
        manifest=manifest,
        telemetry=empty_quote_telemetry(),
        mids={},
        contexts={},
    )
    text = format_terminal_report(payload)
    assert "BOARD FILLS <=10s" in text
    assert "1  (50.0% of fills)" in text
