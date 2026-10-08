"""Two-sided quote math shared by the backtest and the live core."""

from dataclasses import dataclass

TICK = 0.01
MAX_BID_SUM_TICKS = 99
ORDER_SHARES = 20
NET_MAX_SHARES = 50
HALF_SPREAD_TICKS = 3
SKEW_PER_SHARE = 2e-4
BAND_HI = 0.90
QUOTE_FROM_SECOND = -60
REPRICE_HOLD_NS = 300_000_000
REPRICE_NOW_TICKS = 2


@dataclass(frozen=True)
class BidTicks:
    yes_ticks: int
    no_ticks: int


def _round_ticks(price: float) -> int:
    """Nearest tick, half up. Matches the grok-sim snap used for control B."""
    return int(price / TICK + 0.5)


def price_bids(*, fair: float, half_spread_ticks: int, skew: float) -> BidTicks:
    """Round each bid to the nearest tick, then walk the larger one down until the sum leaves one tick."""
    half = half_spread_ticks * TICK
    yes_ticks = max(0, _round_ticks(fair - half - skew))
    no_ticks = max(0, _round_ticks((1.0 - fair) - half + skew))
    while yes_ticks + no_ticks > MAX_BID_SUM_TICKS and (yes_ticks > 0 or no_ticks > 0):
        if yes_ticks >= no_ticks and yes_ticks > 0:
            yes_ticks -= 1
        else:
            no_ticks -= 1
    return BidTicks(yes_ticks=yes_ticks, no_ticks=no_ticks)


def inventory_skew(*, fair: float, net_shares: float, skew_per_share: float) -> float:
    """g(p) * n, with g(p) = g0 * 4 * fair * (1 - fair)."""
    variance_scale = 4.0 * fair * (1.0 - fair)
    return skew_per_share * variance_scale * net_shares


def scale_order_size(
    *,
    size_shares: float,
    net_shares: float,
    net_max_shares: float,
    token_index: int,
) -> float:
    """Shrink the side that increases |net|. The other side stays at size_shares."""
    adding_yes = token_index == 0 and net_shares > 0.0
    adding_no = token_index == 1 and net_shares < 0.0
    if not adding_yes and not adding_no:
        return size_shares
    room = 1.0 - abs(net_shares) / net_max_shares
    if room <= 0.0:
        return 0.0
    return size_shares * min(room, 1.0)


def tick_gap(*, live_price: float, want_price: float) -> int:
    return abs(round(live_price / TICK) - round(want_price / TICK))
