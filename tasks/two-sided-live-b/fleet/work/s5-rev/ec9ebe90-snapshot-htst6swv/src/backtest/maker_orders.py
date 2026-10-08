# Nautilus 1.226 ships these Cython APIs without static declarations.
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
# pyright: reportAttributeAccessIssue=false
# pyright: reportGeneralTypeIssues=false
# pyright: reportArgumentType=false

from dataclasses import dataclass
from decimal import Decimal
from math import ceil, floor

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.orders import LimitOrder

from shared.constants.strategy import MAX_ENTRY_PRICE, MAX_ENTRY_SPREAD_TICKS, MIN_ENTRY_PRICE
from shared.utils.trading import spread_ticks

TICK_SIZE = 0.01
CANCEL_ALERT_PREFIX = "CANCEL:"
CANCEL_SEND_PREFIX = "CANCEL_SEND:"
UNAVAILABLE_DEPTH = float("nan")


@dataclass(frozen=True)
class QuoteContext:
    """Signal/book snapshot frozen at submit; fills and cancels read this, not the live tick."""

    predicted_delta: float
    fair: float
    book_p_radiant: float
    dataset_market_p: float
    spread: float
    queue_ahead: float


EMPTY_QUOTE_CONTEXT = QuoteContext(
    predicted_delta=0.0,
    fair=0.0,
    book_p_radiant=0.0,
    dataset_market_p=0.0,
    spread=0.0,
    queue_ahead=0.0,
)


@dataclass(frozen=True)
class QuoteTarget:
    token_index: int
    side: OrderSide
    price: float
    context: QuoteContext
    level_index: int | None = None


@dataclass(frozen=True)
class LatchedDecision:
    """Fair and BUY targets frozen on a fresh model tick until the next one."""

    fair_radiant: float
    predicted_delta: float
    dataset_market_p: float
    book_p_radiant: float
    buy_targets: tuple[QuoteTarget, ...]
    no_buy_reason: str
    fair_valid: bool
    fair_ts_ns: int


@dataclass(frozen=True)
class LatchQuote:
    predicted_delta: float
    dataset_market_p: float
    fair_radiant: float
    allow_buy: bool
    buy_block_reason: str


@dataclass
class LiveOrder:
    """Mutable per-order state while the venue still has (or is inserting) the order.

    `level_index` is the rung this order holds *now*: a reprice can move a resting
    order to another rung, and a fill must complete the rung it holds at fill time.
    """

    order: LimitOrder
    filled_qty: Decimal = Decimal("0")
    awaiting_cancel: bool = False
    cancel_released: bool = False
    cancel_reason: str = ""
    accepted: bool = False
    partially_filled: bool = False
    level_index: int | None = None
    level_moves: int = 0


@dataclass(frozen=True)
class SubmittedOrder:
    """Submit snapshot; survives live-order deletion so a racing fill still has context."""

    quantity: float
    context: QuoteContext
    token_index: int
    side: OrderSide
    price: float
    level_index: int | None
    episode_id: int


@dataclass(frozen=True)
class DetachedOrder:
    """Last rung assignment of an order, saved before its live state is dropped.

    A fill can still land after cancel/reject removed the live order. It belongs to the
    rung and episode the order held at that moment, not to whatever runs now.
    """

    level_index: int | None
    level_moves: int
    episode_id: int


@dataclass
class BuyLevel:
    """One logical BUY rung for the current episode. Separate from the live order on it."""

    index: int
    price: float
    filled_qty: Decimal = Decimal("0")
    live_id: str | None = None
    done: bool = False


def level_column(level_index: int | None) -> int:
    """Rung number for telemetry columns; -1 means the order holds no rung."""
    return -1 if level_index is None else level_index


def prices_match(left: float, right: float) -> bool:
    return abs(left - right) < TICK_SIZE / 2


def ladder_price(join_price: float, level_index: int, layer_step_ticks: int) -> float:
    """BUY price of logical level `level_index` (0 = top) below `join_price`."""
    return round(join_price - level_index * layer_step_ticks * TICK_SIZE, 2)


def remaining_qty(live: LiveOrder) -> Decimal:
    leftover = Decimal(str(live.order.quantity)) - live.filled_qty
    if leftover < 0:
        return Decimal("0")
    return leftover


def remaining_notional(live: LiveOrder) -> Decimal:
    return remaining_qty(live) * Decimal(str(float(live.order.price)))


def remaining_buy_notional(lives: dict[str, LiveOrder]) -> Decimal:
    """Sum of remaining BUY notionals; a canceling order still reserves its leftover."""
    total = Decimal("0")
    for live in lives.values():
        if live.order.side != OrderSide.BUY:
            continue
        total += remaining_notional(live)
    return total


def live_matches_target(
    *,
    live_token: int,
    live_side: OrderSide,
    live_price: float,
    target: QuoteTarget,
) -> bool:
    return (
        live_token == target.token_index
        and live_side == target.side
        and prices_match(live_price, target.price)
    )


def client_id_from_cancel_alert(name: str) -> str:
    return name[len(CANCEL_ALERT_PREFIX) :]


def client_id_from_cancel_send(name: str) -> str:
    return name[len(CANCEL_SEND_PREFIX) :]


def volume_at_price(book: object, price: float, *, is_buy: bool) -> float:
    """Size at `price` on the join side. nan when that level's depth is unknown, 0 when empty."""
    levels_fn = getattr(book, "bids" if is_buy else "asks", None)
    if callable(levels_fn):
        for level in levels_fn():
            level_price = float(level.price)
            if prices_match(level_price, price):
                size = level.size
                return float(size() if callable(size) else size)
        return 0.0
    tob_price = book.best_bid_price() if is_buy else book.best_ask_price()
    tob_size = book.best_bid_size() if is_buy else book.best_ask_size()
    if tob_price is None:
        return UNAVAILABLE_DEPTH
    if prices_match(float(tob_price), price):
        return float(tob_size or 0.0)
    return UNAVAILABLE_DEPTH


def buy_price_legal(*, price: float, bid: float, ask: float, fair_token: float) -> bool:
    if price <= 0 or price > fair_token + 1e-12:
        return False
    if price < MIN_ENTRY_PRICE - 1e-12:
        return False
    if price >= MAX_ENTRY_PRICE - 1e-12:
        return False
    if spread_ticks(bid, ask) >= MAX_ENTRY_SPREAD_TICKS:
        return False
    return 0.0 < price < 1.0


def gate_quote_context(
    *,
    predicted_delta: float,
    fair: float,
    book_p_radiant: float,
    dataset_market_p: float,
) -> QuoteContext:
    return QuoteContext(
        predicted_delta=predicted_delta,
        fair=fair,
        book_p_radiant=book_p_radiant,
        dataset_market_p=dataset_market_p,
        spread=0.0,
        queue_ahead=0.0,
    )


def calculate_mid_price(book: OrderBook) -> float | None:
    best_bid = book.best_bid_price()
    best_ask = book.best_ask_price()
    if best_bid is None or best_ask is None:
        return None
    return (float(best_bid) + float(best_ask)) / 2.0


def is_on_tick_grid(price: float) -> bool:
    scaled = price / TICK_SIZE
    return abs(scaled - round(scaled)) <= 1e-9


def round_buy_price(price: float) -> float:
    """Round a BUY join price down onto the 0.01 grid so it never crosses."""
    return floor(price / TICK_SIZE + 1e-12) * TICK_SIZE


def round_sell_price(price: float) -> float:
    """Round a SELL join price up onto the 0.01 grid so it never crosses."""
    return ceil(price / TICK_SIZE - 1e-12) * TICK_SIZE
