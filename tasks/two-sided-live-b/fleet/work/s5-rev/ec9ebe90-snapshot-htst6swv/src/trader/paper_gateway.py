"""PaperGateway: simulated fills and positions for the live paper engine.

Synchronous same-loop contract: no locks; a thread producer must marshal in.
"""

import itertools
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from polymaker.config import Config
from polymaker.domain import Fill, MarketMeta, OpenOrder, OrderState, Quote, Side
from polymaker.execution.gateway import ExecutionGateway
from polymaker.journal import Journal
from polymaker.marketdata.orderbook import BookLevel, OrderBook
from polymaker.marketdata.parse import TradePrint
from polymaker.state.store import StateStore

from shared.utils.log import get_logger
from trader.wallet_store import WalletStateStore

logger = get_logger(__name__)

PAPER_ADDRESS = "0xPAPER"
# ponytail: paper bankroll fixture; promote to a config key if paper start cash becomes real.
PAPER_START_USDC = 1000.0


def _positive_finite(value: float) -> bool:
    """Finite and strictly positive."""
    return math.isfinite(value) and value > 0.0


def _valid_source_price(price: float) -> bool:
    """A usable trigger price: finite token price strictly above zero, at most one."""
    return math.isfinite(price) and 0.0 < price <= 1.0


def _crossing_trigger(bid: BookLevel, ask: BookLevel, side: Side) -> BookLevel:
    """The trigger level for one order side: the ask for BUY, the bid for SELL."""
    if side is Side.BUY:
        return ask
    return bid


def _usable_trigger(trigger: BookLevel | None) -> BookLevel | None:
    """The trigger level when it carries a finite in-range price and a positive size."""
    if trigger is None:
        return None
    if not _valid_source_price(trigger.price) or not _positive_finite(trigger.size):
        return None
    return trigger


def _is_crossed(order: OpenOrder, trigger_price: float) -> bool:
    """Strict crossing: BUY only strictly below its limit, SELL only strictly above."""
    if order.side is Side.BUY:
        return trigger_price < order.price
    return trigger_price > order.price


@dataclass(frozen=True)
class _FillSink:
    """The engine-owned position store plus the fill callback, bound as one unit."""

    state: StateStore
    on_fill: Callable[[Fill], None]


class PaperGateway(ExecutionGateway):
    """Offline execution: local paper orders with simulated full-size fills."""

    def __init__(self, cfg: Config, journal: Journal | None = None, *, paper: bool = False) -> None:
        if not paper:
            raise ValueError("PaperGateway requires paper=True")
        super().__init__(cfg, journal, paper=True)
        self._orders: dict[str, OpenOrder] = {}
        self._sink: _FillSink | None = None
        self._paper_ns = uuid.uuid4().hex
        self._paper_counter = itertools.count(1)

    def bind_fill_sink(self, state: StateStore, on_fill: Callable[[Fill], None]) -> None:
        """Bind the engine-owned position store and fill callback exactly once."""
        if self._sink is not None:
            raise RuntimeError("fill sink already bound")
        self._sink = _FillSink(state=state, on_fill=on_fill)

    def _bound_sink(self) -> _FillSink:
        """The bound store/callback pair; fail fast before any market data."""
        if self._sink is None:
            raise RuntimeError("bind_fill_sink() must run before processing market data")
        return self._sink

    async def connect(self) -> None:
        """Local paper connect: set the paper address/funder, never call the CLOB."""
        address = self._cfg.secrets.browser_address or PAPER_ADDRESS
        self._address = address
        self._funder = address
        logger.info("paper gateway connected, address=%s", address[:10])

    async def positions(self) -> dict[str, tuple[float, float]]:
        """No network: the engine StateStore owns paper positions."""
        return {}

    async def collateral_balance(self) -> float:
        """Paper wallet: start cash plus ledger running net."""
        if self._sink is None:
            return PAPER_START_USDC
        state = self._sink.state
        if not isinstance(state, WalletStateStore):
            return PAPER_START_USDC
        return PAPER_START_USDC + state.running_net_cash

    async def token_balance(self, token_id: str) -> float:
        """No network: paper balances come from the engine StateStore."""
        return 0.0

    async def token_balances(self, token_ids: list[str]) -> dict[str, float]:
        """No network: an empty map keeps the divergence check a no-op."""
        return {}

    async def get_book(self, token_id: str) -> dict[str, float]:
        """No network: REST book reads are disabled in paper mode."""
        return {}

    async def get_full_book(
        self, token_id: str
    ) -> tuple[list[tuple[float, float]], list[tuple[float, float]], str | None] | None:
        """No network: the maintenance REST refresh is a no-op in paper mode."""
        return None

    async def place(self, quotes: list[Quote], meta: MarketMeta) -> list[OpenOrder]:
        """Validate the whole batch fail-closed, then fabricate paper LIVE orders."""
        if not quotes:
            return []
        if not self._quotes_valid(quotes, meta):
            return []
        placed = await super().place(quotes, meta)
        for order in placed:
            self._orders[order.order_id] = order
        return placed

    def _quotes_valid(self, quotes: list[Quote], meta: MarketMeta) -> bool:
        """Fail-closed batch validation: one bad quote rejects the whole batch."""
        tokens = {meta.yes.token_id, meta.no.token_id}
        if not _positive_finite(meta.tick_size):
            return False
        tick = Decimal(str(meta.tick_size))
        for quote in quotes:
            if not quote.token_id or quote.token_id not in tokens:
                return False
            if quote.side is not Side.BUY and quote.side is not Side.SELL:
                return False
            if not (math.isfinite(quote.price) and 0.0 < quote.price < 1.0):
                return False
            if not _positive_finite(quote.size):
                return False
            if Decimal(str(quote.price)) % tick != 0:
                return False
        return True

    def _paper_order(self, q: Quote) -> OpenOrder:
        """One session-namespaced LIVE order with the exact quote price and size."""
        order_id = f"paper-{self._paper_ns}-{next(self._paper_counter)}"
        return OpenOrder(order_id, q.token_id, q.side, q.price, q.size, OrderState.LIVE)

    async def cancel(self, order_ids: list[str]) -> bool:
        """Local idempotent cancel by id; the engine drops state orders itself."""
        for order_id in order_ids:
            self._orders.pop(order_id, None)
        return True

    async def cancel_asset(self, asset_id: str) -> bool:
        """Local idempotent cancel of every paper order on one token."""
        for order_id in [o for o in self._orders if self._orders[o].token_id == asset_id]:
            del self._orders[order_id]
        return True

    async def cancel_all(self) -> None:
        """Local cancel of every paper order."""
        self._orders.clear()

    async def open_orders(self) -> list[OpenOrder]:
        """Defensive snapshot of all live paper orders (empty only after fill/cancel)."""
        return [replace(o, state=OrderState.LIVE) for o in self._orders.values()]

    def process_book_update(self, token_id: str, book: OrderBook) -> list[Fill]:
        """Simulate fills from the applied book; both sides must be valid or nothing fills."""
        sink = self._bound_sink()
        ts = book.last_update_ts
        if not _positive_finite(ts):
            return []
        bid = _usable_trigger(book.best_bid())
        ask = _usable_trigger(book.best_ask())
        if bid is None or ask is None:
            return []
        return self._fill_crossed_orders(
            token_id, ts, lambda side: _crossing_trigger(bid, ask, side).price, sink
        )

    def process_trade_print(self, trade: TradePrint) -> list[Fill]:
        """Simulate fills from one inbound trade print; a print is never cached."""
        sink = self._bound_sink()
        if not _positive_finite(trade.ts):
            return []
        if not _valid_source_price(trade.price) or not _positive_finite(trade.size):
            return []
        return self._fill_crossed_orders(trade.asset_id, trade.ts, lambda _side: trade.price, sink)

    def _fill_crossed_orders(
        self,
        token_id: str,
        ts: float,
        trigger_price: Callable[[Side], float],
        sink: _FillSink,
    ) -> list[Fill]:
        """Fill every order on one token whose limit the validated trigger crosses."""
        fills: list[Fill] = []
        for order in [o for o in self._orders.values() if o.token_id == token_id]:
            if not _is_crossed(order, trigger_price(order.side)):
                continue
            fill = self._apply_fill(order, ts, sink)
            if fill is not None:
                fills.append(fill)
        return fills

    def _apply_fill(self, order: OpenOrder, ts: float, sink: _FillSink) -> Fill | None:
        """Apply one fill: StateStore first, then drop the order, then the callback."""
        if order.side is Side.SELL and sink.state.position(order.token_id).size < order.size:
            return None  # full-size fill only; leave the order to reconcile/requote
        fill = self._build_fill(order, ts)
        applied = sink.state.apply_fill(fill)
        self._orders.pop(order.order_id, None)
        sink.state.remove_order(order.order_id)
        if not applied:
            return None  # SQLite already knows this trade id; local order cleaned up
        sink.on_fill(fill)
        return fill

    def _build_fill(self, order: OpenOrder, ts: float) -> Fill:
        """One full-size maker fill at the order's own limit price."""
        # ponytail: филл на полный размер, без позиции в очереди. Потолок — оптимизм
        # на размере свипа. Если он будет мешать сравнению с бэктестом, брать позицию  # noqa: RUF003
        # в очереди из архива книги коллектора.
        return Fill(
            token_id=order.token_id,
            side=order.side,
            price=order.price,
            size=order.size,
            trade_id=f"{order.order_id}:fill",
            ts=ts,
            is_maker=True,
        )
