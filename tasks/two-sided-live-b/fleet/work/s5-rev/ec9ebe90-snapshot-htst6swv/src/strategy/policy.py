"""Quote policies. Adapters pass cadence; quote knobs are filled here."""

from dataclasses import dataclass

from shared.constants.strategy import (
    BUY_CUTOFF_SECOND,
    BUY_LEVEL_COUNT,
    BUY_LEVEL_STEP_TICKS,
    BUY_POLICY_VERSION,
    EXIT_ABS_DELTA,
    EXIT_SETTLE_SECONDS,
    LATCH_REANCHOR_OFF_S,
    LATCH_REANCHOR_SECONDS,
    MAX_ENTRY_PRICE,
    MAX_ENTRY_SPREAD_TICKS,
    MID_SPIKE_COOLOFF_S,
    MID_SPIKE_LOOKBACK_S,
    MID_SPIKE_THRESHOLD,
    MIN_ABS_DELTA,
    MIN_ENTRY_PRICE,
    QUOTE_GRID,
    SELL_MIN_LIFE_SECONDS,
)
from strategy.two_sided import (
    BAND_HI,
    HALF_SPREAD_TICKS,
    MAX_BID_SUM_TICKS,
    NET_MAX_SHARES,
    ORDER_SHARES,
    QUOTE_FROM_SECOND,
    REPRICE_HOLD_NS,
    REPRICE_NOW_TICKS,
    SKEW_PER_SHARE,
    TICK,
)


@dataclass(frozen=True)
class Follow300Policy:
    version: str
    level_count: int
    step_ticks: int
    tick_size: float
    level_usdc: float
    min_abs_delta: float
    exit_abs_delta: float
    min_entry_price: float
    max_entry_price: float
    max_entry_spread_ticks: int
    buy_cutoff_second: int
    exit_settle_s: float
    debounce_ms: int
    fallback_timer_s: float
    sell_min_life_s: float
    hold_unconfirmed_sell: bool
    mid_spike_lookback_s: float
    mid_spike_threshold: float
    mid_spike_cooloff_s: float
    latch_reanchor_s: float


def follow300_policy(
    *, level_usdc: float, debounce_ms: int, fallback_timer_s: float
) -> Follow300Policy:
    """Accepted Follow300: 1s SELL min-life, MATCHED hold, adapter cadence."""
    return Follow300Policy(
        version=BUY_POLICY_VERSION,
        level_count=BUY_LEVEL_COUNT,
        step_ticks=BUY_LEVEL_STEP_TICKS,
        tick_size=QUOTE_GRID,
        level_usdc=level_usdc,
        min_abs_delta=MIN_ABS_DELTA,
        exit_abs_delta=EXIT_ABS_DELTA,
        min_entry_price=MIN_ENTRY_PRICE,
        max_entry_price=MAX_ENTRY_PRICE,
        max_entry_spread_ticks=MAX_ENTRY_SPREAD_TICKS,
        buy_cutoff_second=BUY_CUTOFF_SECOND,
        exit_settle_s=EXIT_SETTLE_SECONDS,
        debounce_ms=debounce_ms,
        fallback_timer_s=fallback_timer_s,
        sell_min_life_s=SELL_MIN_LIFE_SECONDS,
        hold_unconfirmed_sell=True,
        mid_spike_lookback_s=MID_SPIKE_LOOKBACK_S,
        mid_spike_threshold=MID_SPIKE_THRESHOLD,
        mid_spike_cooloff_s=MID_SPIKE_COOLOFF_S,
        latch_reanchor_s=LATCH_REANCHOR_SECONDS,
    )


def extraction_policy(*, level_usdc: float) -> Follow300Policy:
    """Historical extraction Follow300. Production adapters do not use this."""
    return Follow300Policy(
        version="follow300-v1",
        level_count=BUY_LEVEL_COUNT,
        step_ticks=BUY_LEVEL_STEP_TICKS,
        tick_size=QUOTE_GRID,
        level_usdc=level_usdc,
        min_abs_delta=MIN_ABS_DELTA,
        exit_abs_delta=MIN_ABS_DELTA,
        min_entry_price=MIN_ENTRY_PRICE,
        max_entry_price=MAX_ENTRY_PRICE,
        max_entry_spread_ticks=MAX_ENTRY_SPREAD_TICKS,
        buy_cutoff_second=BUY_CUTOFF_SECOND,
        exit_settle_s=EXIT_SETTLE_SECONDS,
        debounce_ms=0,
        fallback_timer_s=1.0,
        sell_min_life_s=0.0,
        hold_unconfirmed_sell=False,
        mid_spike_lookback_s=MID_SPIKE_LOOKBACK_S,
        mid_spike_threshold=MID_SPIKE_THRESHOLD,
        mid_spike_cooloff_s=MID_SPIKE_COOLOFF_S,
        latch_reanchor_s=LATCH_REANCHOR_OFF_S,
    )


@dataclass(frozen=True)
class TwoSidedPolicy:
    order_shares: int
    net_max_shares: int
    half_spread_ticks: int
    skew_per_share: float
    band_hi: float
    quote_from_second: int
    tick: float
    max_bid_sum_ticks: int
    reprice_hold_ns: int
    reprice_now_ticks: int
    debounce_ms: int
    fallback_timer_s: float


Policy = Follow300Policy | TwoSidedPolicy


def two_sided_policy(*, debounce_ms: int, fallback_timer_s: float) -> TwoSidedPolicy:
    """Two-sided maker. Adapters pass cadence; the quote knobs are the module constants."""
    return TwoSidedPolicy(
        order_shares=ORDER_SHARES,
        net_max_shares=NET_MAX_SHARES,
        half_spread_ticks=HALF_SPREAD_TICKS,
        skew_per_share=SKEW_PER_SHARE,
        band_hi=BAND_HI,
        quote_from_second=QUOTE_FROM_SECOND,
        tick=TICK,
        max_bid_sum_ticks=MAX_BID_SUM_TICKS,
        reprice_hold_ns=REPRICE_HOLD_NS,
        reprice_now_ticks=REPRICE_NOW_TICKS,
        debounce_ms=debounce_ms,
        fallback_timer_s=fallback_timer_s,
    )
