import itertools
import time
from collections.abc import Callable, Iterable, Reversible
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, cast

from polymaker.marketdata.orderbook import BookLevel
from polymaker.marketdata.parse import parse_book
from polymaker.marketdata.service import MarketDataService

BOOK_TOP_LEVELS = 20
DEFAULT_TICK_SIZE = 0.001
DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass(frozen=True)
class BookSnapshot:
    token_id: str
    condition_id: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    tick_size: float
    book_hash: str | None
    exchange_ts: float | None
    local_ts: float | None
    generation: int
    initialized: bool
    ready: bool
    connected: bool
    disconnected_since: float | None
    truncated: bool


@dataclass(frozen=True)
class DesiredMarket:
    condition_id: str
    token_ids: tuple[str, ...]


ScheduleFn = Callable[[Callable[[], None]], None]
BooksCallback = Callable[[tuple[BookSnapshot, ...]], None]


class HubBooks(MarketDataService):
    def __init__(
        self,
        url: str = DEFAULT_WS_URL,
        *,
        schedule: ScheduleFn,
        on_books: BooksCallback,
        proxy: str | None = None,
        top_levels: int = BOOK_TOP_LEVELS,
        wall: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(url, proxy=proxy)
        self._schedule_cb = schedule
        self._on_books_cb = on_books
        self._top_levels = top_levels
        self._wall = wall
        self._generation = 0
        self._ready: set[str] = set()
        self._initialized: set[str] = set()
        self._desired: dict[str, str] = {}
        self._published: dict[str, BookSnapshot] = {}
        self._applied_at: float | None = None
        self._pending_publish = False

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def applied_at(self) -> float | None:
        return self._applied_at

    def feed_frame(self, raw: str | bytes) -> None:
        self._handle(raw)

    def _wake(self, token_id: str) -> None:
        self._schedule_publish()

    def _schedule_publish(self) -> None:
        if self._pending_publish:
            return
        self._pending_publish = True
        self._schedule_cb(self._publish_now)

    def _publish_now(self) -> None:
        self._pending_publish = False
        self._on_books_cb(self._snapshots())

    def _snapshots(self) -> tuple[BookSnapshot, ...]:
        snaps = tuple(self._capture(token, cid) for token, cid in sorted(self._desired.items()))
        for snap in snaps:
            self._published[snap.token_id] = snap
        return snaps

    def _capture(self, token: str, condition_id: str) -> BookSnapshot:
        book = self.books.get(token)
        ready = token in self._ready
        prev = self._published.get(token)
        if ready and book is not None:
            bid_items = cast(Reversible[tuple[float, float]], book.bids.items())
            ask_items = cast(Iterable[tuple[float, float]], book.asks.items())
            bids = tuple(
                BookLevel(price, size)
                for price, size in itertools.islice(reversed(bid_items), self._top_levels)
            )
            asks = tuple(
                BookLevel(price, size)
                for price, size in itertools.islice(ask_items, self._top_levels)
            )
            truncated = len(book.bids) > self._top_levels or len(book.asks) > self._top_levels
            book_hash = book.book_hash
            exchange_ts = book.last_update_ts or None
            local_ts = book.local_ts or None
        elif prev is not None:
            bids, asks, truncated = prev.bids, prev.asks, prev.truncated
            book_hash = prev.book_hash
            exchange_ts = prev.exchange_ts
            local_ts = prev.local_ts
        else:
            bids, asks = (), ()
            truncated = False
            book_hash = None
            exchange_ts = None
            local_ts = None
        if book is not None:
            tick = book.tick_size
        elif prev is not None:
            tick = prev.tick_size
        else:
            tick = DEFAULT_TICK_SIZE
        disconnected = self.disconnected_since if self.disconnected_since else None
        return BookSnapshot(
            token_id=token,
            condition_id=condition_id,
            bids=bids,
            asks=asks,
            tick_size=tick,
            book_hash=book_hash,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            generation=self._generation,
            initialized=token in self._initialized,
            ready=ready,
            connected=self.connected,
            disconnected_since=disconnected,
            truncated=truncated,
        )

    def _on_book(self, msg: dict[str, Any]) -> None:
        upd = parse_book(msg)
        if upd is None or upd.asset_id not in self._desired:
            return
        super()._on_book(msg)
        if upd.asset_id in self.books:
            self._ready.add(upd.asset_id)
            self._initialized.add(upd.asset_id)
            self._schedule_publish()

    async def _connect_and_listen(self) -> None:
        if self._subs:
            self._generation += 1
            self._ready.clear()
            for token in self._subs:
                book = self.books.get(token)
                if book is None:
                    continue
                book.bids.clear()
                book.asks.clear()
                book.book_hash = None
                book.last_update_ts = 0.0
                book.local_ts = 0.0
            self._schedule_publish()
        try:
            await super()._connect_and_listen()
        finally:
            self._schedule_publish()

    async def _close_socket(self) -> None:
        ws = self._ws
        if ws is None:
            return
        with suppress(Exception):
            await ws.close()

    async def apply_subscriptions(self, markets: tuple[DesiredMarket, ...]) -> bool:
        desired: dict[str, str] = {}
        for market in markets:
            for token in market.token_ids:
                desired[token] = market.condition_id
        if desired == self._desired:
            return False
        for token in self._desired:
            if token not in desired:
                self.books.pop(token, None)
                self._token_condition.pop(token, None)
                self._ready.discard(token)
                self._initialized.discard(token)
                self._published.pop(token, None)
        self._desired = desired
        self.set_markets([(m.condition_id, list(m.token_ids)) for m in markets])
        self._applied_at = self._wall()
        await self._close_socket()
        self._schedule_publish()
        return True

    async def shutdown(self) -> None:
        self.stop()
        await self._close_socket()
