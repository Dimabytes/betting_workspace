"""Polymarket fee primitives and the 0.01 quote-grid snap."""

import math

from shared.constants.strategy import QUOTE_GRID

FEE_RATE = 0.05
REBATE_RATE = 0.15


def buy_share_quantity(*, base_size_usdc: float, price: float) -> float:
    """Buy size in whole share ticks, never rounded above the dollar budget."""
    return share_floor(base_size_usdc / price)


HALF_SHARE_TICK = 0.005  # half a CLOB share tick: float residue, not a short SELL


def share_floor(size: float) -> float:
    """Floor share size to two decimals without dropping a binary 0.01 remainder."""
    return math.floor(size * 100.0 + 1e-6) / 100.0


def drop_share_residue(size: float) -> float:
    """Zero qty below one CLOB share tick. A remainder of whole ticks stays."""
    if share_floor(size) <= 0.0:
        return 0.0
    return size


def floor_to_grid(price: float) -> float:
    """Floor a price onto the 0.01 quote grid, snapped to two decimals."""
    ticks = math.floor(price / QUOTE_GRID + 1e-12)
    return round(ticks * QUOTE_GRID, 2)


def ceil_to_grid(price: float) -> float:
    """Ceil a price onto the 0.01 quote grid, snapped to two decimals."""
    ticks = math.ceil(price / QUOTE_GRID - 1e-12)
    return round(ticks * QUOTE_GRID, 2)


def spread_ticks(bid: float, ask: float) -> int:
    """Round ask-bid onto the 0.01 grid so 0.60-0.52 counts as 8, not 7."""
    return round((ask - bid) / QUOTE_GRID)


def calculate_taker_fee_per_share(*, price: float, fee_rate: float) -> float:
    """Polymarket fee base per share: rate * p * (1 - p), with p clamped to [0, 1]."""
    clamped = min(max(price, 0.0), 1.0)
    return max(fee_rate, 0.0) * clamped * (1.0 - clamped)


def maker_rebate_usdc(*, price: float, size: float) -> float:
    """Sports maker rebate on this fill: 0.15 * 0.05 * size * p * (1-p). Zero if invalid."""
    if type(price) is bool or type(size) is bool:
        return 0.0
    if not math.isfinite(price) or not math.isfinite(size):
        return 0.0
    if not 0.0 < price < 1.0 or size <= 0.0:
        return 0.0
    fee_base = size * calculate_taker_fee_per_share(price=price, fee_rate=1.0)
    return REBATE_RATE * FEE_RATE * fee_base
