"""Shape of one instrument result returned by prediction-market-backtesting.

Only the fields this repo reads are typed. Required keys are always present on
a live framework result; optional ones are set by `build_backtest_run_state`
but may be absent from unit-test fixtures.

These are static types only: `cast()` at the read boundary, no runtime validation.
"""

from typing import NotRequired, TypedDict


class ReplayFillEvent(TypedDict):
    """One fill event serialized by the framework for an instrument."""

    price: float
    quantity: float
    commission: float
    timestamp: str


class ReplayInstrumentResult(TypedDict):
    """Per-instrument backtest result after `_finalize_replay_results`."""

    instrument_id: str
    fills: int
    pnl: float
    fill_events: list[ReplayFillEvent]
    settlement_pnl_applied: NotRequired[bool]
    terminated_early: NotRequired[bool]
    stop_reason: NotRequired[str | None]
    warnings: NotRequired[list[str]]


class MarkoutBlock(TypedDict):
    """Quantity-weighted markout point estimate and percentile CI."""

    estimate: float
    ci_low: float
    ci_high: float
    fills: int


class ArmMarkouts(TypedDict):
    """30s/300s markout for BUY and SELL; null when that side has no fills."""

    buy_30s: MarkoutBlock | None
    buy_300s: MarkoutBlock | None
    sell_30s: MarkoutBlock | None
    sell_300s: MarkoutBlock | None


class ForcedLiquidationStub(TypedDict):
    """V1 placeholder; full VWAP liquidation is a Non-Goal."""

    taker_fee: None
    net_pnl: None
    unliquidatable: None


class WalletPayload(TypedDict):
    """Shared-wallet capital path written into summary.json."""

    required_cash: float
    required_cash_with_reserves: float
    required_cash_with_reserves_at_close: float
    peak_reserved: float
    final_balance_with_rebate: float
    span_seconds: float
    maps_at_once: int
    lowest_capital: float
    roi_before_rebate: float | None
    roi_with_rebate: float | None
    equity_spark: list[float]


class HoldPayload(TypedDict):
    """Closed round-trip hold times written into summary.json."""

    closed: int
    min_seconds: float
    p50_seconds: float
    p90_seconds: float


class BoardFillSlice(TypedDict):
    """Fills inside the post-board-tick window: count, share, 300s markouts."""

    fills: int
    share: float
    buy_300s: MarkoutBlock | None
    sell_300s: MarkoutBlock | None


class QuoteEventCounts(TypedDict):
    """kind totals plus canceled-reason histogram for summary.json."""

    by_kind: dict[str, int]
    canceled_reason: dict[str, int]


class ArmSummary(TypedDict):
    """Per-arm counters and markouts written into summary.json."""

    matches: int
    completed: int
    terminated: int
    traded: int
    no_trades: int
    buy_fills: int
    sell_fills: int
    orders_submitted: int
    orders_accepted: int
    orders_canceled: int
    orders_rejected: int
    incomplete_orders: int
    dust_positions: int
    fill_rate: float
    filled_quantity: float
    incomplete_order_rate: float
    buy_turnover: float
    sell_turnover: float
    median_buy_order_notional: float | None
    window_seconds: int
    live_order_seconds: int
    gate_stale_book_seconds: int
    gate_pair_tolerance_seconds: int
    gate_stale_signal_seconds: int
    gate_anchor_seconds: int
    gate_fair_seconds: int
    gate_min_delta_seconds: int
    gate_min_price_seconds: int
    gate_max_price_seconds: int
    gate_cutoff_seconds: int
    gate_nw_velocity_seconds: int
    gate_missing_nw_seconds: int
    gate_dust_seconds: int
    gate_exit_settle_seconds: int
    gate_no_cash_seconds: int
    gate_position_cap_seconds: int
    gate_other_seconds: int
    quote_events: QuoteEventCounts
    terminal_radiant_inventory: float
    terminal_dire_inventory: float
    terminal_inventory: float
    total_engine_pnl: float
    cash_flow: float
    settlement_remainder: float
    pnl_before_rebate: float
    pnl_per_eligible_match: float
    pnl_per_bought_share: float
    pnl_per_bought_share_with_rebate: float
    bought_shares: float
    maker_rebate: float
    taker_fee: float
    merge_count: NotRequired[int]
    merge_shares: NotRequired[float]
    merge_usdc: NotRequired[float]
    leftover_shares_0: NotRequired[float]
    leftover_shares_1: NotRequired[float]
    leftover_settlement_usdc: NotRequired[float]
    net_pnl: float
    net_pnl_per_match: float
    median_match_pnl: float
    max_match_drawdown: float
    min_equity: float
    loss_match_rate: float
    cvar_5: float
    worst_match: float
    signal_groups: dict[str, int]
    model_groups: dict[str, int]
    hold: HoldPayload
    wallet: WalletPayload
    markout: ArmMarkouts
    board: BoardFillSlice
    forced_liquidation: ForcedLiquidationStub


class ArmPayload(ArmSummary):
    """One summary.json arm object: identity plus ArmSummary counters."""

    placement: str
    fill_model: str


class SummaryPayload(TypedDict):
    """summary.json body."""

    selected: int
    wall_seconds: float
    arms: list[ArmPayload]
    assumptions: tuple[str, ...]
    manifest: dict[str, object]
    coverage: NotRequired[dict[str, object]]
