"""Pure Follow300 signal helpers. Copied from backtest; no pandas or files."""

from shared.constants.strategy import QUOTE_GRID
from shared.utils.trading import ceil_to_grid, floor_to_grid, spread_ticks
from strategy.types import BlockReason, BookPair, MarketLimits

ANCHOR_TOLERANCE = 0.01


def clip_fair(value: float) -> float:
    return min(1.0, max(0.0, value))


def fair_radiant(*, book_p_radiant: float, predicted_delta: float) -> float:
    return clip_fair(book_p_radiant + predicted_delta)


def token_fair(*, fair_radiant: float, token_index: int, radiant_token_index: int) -> float:
    if token_index == radiant_token_index:
        return fair_radiant
    return 1.0 - fair_radiant


def normalize_pair_mids(*, radiant_mid: float, dire_mid: float, tolerance: float) -> float | None:
    pair_sum = radiant_mid + dire_mid
    if abs(pair_sum - 1.0) > tolerance or pair_sum <= 0.0:
        return None
    return radiant_mid / pair_sum


def book_p_radiant(*, books: BookPair, radiant_token_index: int, tolerance: float) -> float | None:
    radiant = books.tokens[radiant_token_index]
    dire = books.tokens[1 - radiant_token_index]
    radiant_mid = (radiant.bid + radiant.ask) / 2.0
    dire_mid = (dire.bid + dire.ask) / 2.0
    return normalize_pair_mids(radiant_mid=radiant_mid, dire_mid=dire_mid, tolerance=tolerance)


def core_book_p(*, books: BookPair, limits: MarketLimits) -> float | None:
    """Radiant mid from the limits the kernel stores on its state."""
    return book_p_radiant(
        books=books,
        radiant_token_index=limits.radiant_token_index,
        tolerance=limits.pair_sum_tolerance,
    )


def is_fresh(*, now_ns: int, ts_ns: int, max_age_s: float) -> bool:
    if ts_ns > now_ns:
        return False
    age_seconds = (now_ns - ts_ns) / 1_000_000_000
    return age_seconds <= max_age_s


def next_delta_gate_open(
    *, was_open: bool, abs_delta: float, entry: float, exit_threshold: float
) -> bool:
    if was_open:
        return abs_delta >= exit_threshold
    return abs_delta >= entry


def entry_delta_blocked(
    *, predicted_delta: float, min_abs_delta: float, exit_abs_delta: float, gate_open: bool
) -> bool:
    floor = exit_abs_delta if gate_open else min_abs_delta
    return abs(predicted_delta) < floor


def passes_anchor(*, book_p: float, anchor_p: float) -> bool:
    return abs(book_p - anchor_p) <= ANCHOR_TOLERANCE + 1e-12


def buy_reject_reason(
    *,
    price: float,
    bid: float,
    ask: float,
    fair_token: float,
    min_entry_price: float,
    max_entry_price: float,
    max_entry_spread_ticks: int,
) -> BlockReason | None:
    if price <= 0 or price > fair_token + 1e-12:
        return "fair"
    if price < min_entry_price - 1e-12:
        return "min_price"
    if price >= max_entry_price - 1e-12:
        return "max_price"
    if spread_ticks(bid, ask) >= max_entry_spread_ticks:
        return "wide_spread"
    if not 0.0 < price < 1.0:
        return "fair"
    return None


def buy_price_legal(
    *,
    price: float,
    bid: float,
    ask: float,
    fair_token: float,
    min_entry_price: float,
    max_entry_price: float,
    max_entry_spread_ticks: int,
) -> bool:
    return (
        buy_reject_reason(
            price=price,
            bid=bid,
            ask=ask,
            fair_token=fair_token,
            min_entry_price=min_entry_price,
            max_entry_price=max_entry_price,
            max_entry_spread_ticks=max_entry_spread_ticks,
        )
        is None
    )


def ladder_price(
    *, join_price: float, level_index: int, step_ticks: int, tick_size: float
) -> float:
    return round(join_price - level_index * step_ticks * tick_size, 2)


def price_cents(price: float) -> int:
    """Quote-grid key for the live book strip."""
    return round(price / QUOTE_GRID)


def prices_match(*, left: float, right: float, tick_size: float) -> bool:
    return abs(left - right) < tick_size / 2


def join_buy_price(bid: float) -> float:
    return floor_to_grid(bid)


def join_sell_price(ask: float) -> float:
    return ceil_to_grid(ask)
