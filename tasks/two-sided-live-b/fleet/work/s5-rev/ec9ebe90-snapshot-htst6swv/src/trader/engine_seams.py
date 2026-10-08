"""Live Engine patches: strict REST, shutdown latch, day kill, attach/detach."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false, reportMissingTypeStubs=false
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import asyncio
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

import polymaker.engine as polymaker_engine
import polymaker.userstream.client as userstream_client
import websockets
from polymaker.catalog.store import CatalogStore
from polymaker.config import StrategyProfile
from polymaker.domain import Fill, MarketMeta, OpenOrder, OrderState, Quote, Side
from polymaker.engine import Engine
from polymaker.execution.gateway import ExecutionGateway
from polymaker.journal import Journal
from polymaker.risk.manager import RiskDecision, RiskManager
from polymaker.state.tracker import UserEventProcessor
from polymaker.strategy.regime import RegimeMachine
from polymaker.userstream.client import UserStream
from py_clob_client_v2.clob_types import TradeParams

from shared.utils.log import get_logger
from trader.chain_balances import ChainSnapshot, snapshot_covers
from trader.fill_parsing import normalize_maker_trades
from trader.notify import notify_in_background
from trader.place_rejects import clob_place_items, freeze_sell_rejects, unplaced_quotes_explained
from trader.session_core import CollateralCache, LiveCore, core_now_ns
from trader.session_engine import GatedRegimeMachine, StrategyCell
from trader.trade_backfill import backfill_trades
from trader.wallet_store import (
    BUSY_TIMEOUT_MS,
    HALF_SHARE_TICK,
    WalletFillProcessor,
    WalletStateStore,
    rest_size_down_skip_reason,
    rest_size_within_tolerance,
    share_qty_matches,
)

# On-chain above sqlite is normally REST lag, so one round is ignored. This many
# consecutive divergence rounds with no inflight, no settle, and no live SELL is
# inventory sqlite forgot: block new clips on that token. The divergence check
# runs every fourth reconcile round, so 3 rounds is about 4 minutes.
ONCHAIN_EXCESS_BLOCK_ROUNDS = 3

# The CLOB rejects the first beats of a new session with `Invalid Heartbeat ID`.
# Heartbeat failures do not arm the dead-man halt until the chain works once, or
# until this long after boot. Observed chain start is about 10s.
HEARTBEAT_BOOT_GRACE_SECONDS = 60.0


logger = get_logger(__name__)


def _ignore_order_terminal(msg: object) -> None:
    """Default user-WS order hook. The host replaces it after Engine.start."""
    del msg


DRAIN_TIMEOUT_S = 5.0
LOOKBACK_S = 3600
_SKIP_ORDER_ERROR: ContextVar[bool] = ContextVar("live_paper_skip_order_error", default=False)
_PLACE_CID: ContextVar[str | None] = ContextVar("live_paper_place_cid", default=None)
_ORDER_ERROR_ATTEMPT_FLOOR = 20
IDLE_QUOTER_SLEEP_S = 3600.0
_market_ws_close_tasks: list[asyncio.Task[Any]] = []
_T = TypeVar("_T")


class RestUnproven(Exception):
    """A live REST read failed. Callers must not treat this as an empty snapshot."""


class GatewayClosed(Exception):
    """Place was refused because teardown already closed the gateway."""


@dataclass(frozen=True)
class EngineClassRestore:
    """Fork class objects to put back after the wallet process exits."""

    state_store: type[object]
    catalog_store: type[object]
    processor: type[object]
    user_stream: type[object]
    normalize_trade: object
    journal: type[object]


_JOURNAL_KINDS = frozenset({"orders_out", "user_order", "user_trade"})


class SlimJournal(Journal):
    """Engine journal that keeps our private order/fill tape and drops public books."""

    def write(self, kind: str, payload: object, ts: float) -> None:
        """Write one kept kind; drop public book and last-trade events."""
        if kind not in _JOURNAL_KINDS:
            return
        super().write(kind, payload, ts)


class WalletCatalogStore(CatalogStore):
    """CatalogStore with a long busy_timeout so a fill commit does not lock it out."""

    def __init__(self, db_path: str | Path = "state.db") -> None:
        super().__init__(db_path)
        self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")


class WalletUserStream(UserStream):
    """UserStream that keeps the live socket so attach can force a reconnect."""

    def __init__(
        self,
        creds: object,
        our_address: str,
        processor: UserEventProcessor,
        *,
        other_token: Callable[[str], str | None],
        condition_of_token: Callable[[str], str | None],
        url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user",
        journal: Journal | None = None,
        proxy: str | None = None,
        on_reconnect: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            creds,
            our_address,
            processor,
            other_token=other_token,
            condition_of_token=condition_of_token,
            url=url,
            journal=journal,
            proxy=proxy,
            on_reconnect=on_reconnect,
        )
        self._active_ws: object | None = None
        self._close_task: asyncio.Task[Any] | None = None
        self.on_order_terminal: Callable[[object], None] = _ignore_order_terminal

    def _on_order(self, msg: dict[str, Any]) -> None:
        """Journal and track the order, then hand the same raw object to the host."""
        super()._on_order(msg)
        self.on_order_terminal(msg)

    async def _connect_and_listen(self) -> None:
        """Subscribe, remember the socket, and force REST reconcile on reconnect."""
        sub = {
            "type": "user",
            "auth": {
                "apiKey": self._creds.api_key,
                "secret": self._creds.api_secret,
                "passphrase": self._creds.api_passphrase,
            },
            "markets": self._markets,
        }
        if self._proxy:
            ws_cm = websockets.connect(
                self._url,
                ping_interval=5,
                ping_timeout=10,
                open_timeout=10,
                proxy=self._proxy,
            )
        else:
            ws_cm = websockets.connect(self._url, ping_interval=5, ping_timeout=10, open_timeout=10)
        async with ws_cm as ws:
            self._active_ws = ws
            await ws.send(json.dumps(sub))
            self.connected = True
            is_reconnect = self._ever_connected
            self._ever_connected = True
            if is_reconnect:
                self._on_reconnect()
            try:
                async for raw in ws:
                    self._handle(raw)
            finally:
                self._active_ws = None
                self.connected = False
                self.disconnected_since = time.time()

    def close_socket(self) -> None:
        """Close the live user socket so the run loop reconnects with new markets."""
        task = close_live_socket(self._active_ws)
        if task is not None:
            self._close_task = task


def close_live_socket(ws: object | None) -> asyncio.Task[Any] | None:
    """Close a websocket handle. Coroutine close is scheduled on the running loop."""
    if ws is None:
        return None
    close = getattr(ws, "close", None)
    if close is None:
        return None
    result = close()
    if asyncio.iscoroutine(result):
        return asyncio.create_task(result)
    return None


class BookReadiness:
    """Per-token attach generation. Ready when a book snapshot arrives for that generation."""

    def __init__(self) -> None:
        self._generation: dict[str, int] = {}
        self._seen: dict[str, int] = {}
        self._attached_at: dict[str, float] = {}

    def attach_token(self, token_id: str) -> int:
        """Bump the generation so a stale book from before attach cannot count as ready."""
        gen = self._generation.get(token_id, 0) + 1
        self._generation[token_id] = gen
        self._seen.pop(token_id, None)
        self._attached_at[token_id] = time.time()
        return gen

    def note_snapshot(self, token_id: str) -> None:
        """Record that this token's current generation has a book snapshot."""
        gen = self._generation.get(token_id)
        if gen is None:
            return
        self._seen[token_id] = gen

    def ready(self, token_id: str, last_update_ts: float) -> bool:
        """True when a snapshot arrived for this attach, or last_update_ts is after attach."""
        gen = self._generation.get(token_id)
        if gen is None:
            return False
        if self._seen.get(token_id) == gen:
            return True
        if last_update_ts <= 0:
            return False
        attached_at = self._attached_at.get(token_id)
        if attached_at is None:
            return False
        return last_update_ts > attached_at


class ShutdownLatch:
    """Rejects new places after close and counts in-flight place/cancel calls."""

    def __init__(self) -> None:
        self.closed = False
        self.in_flight = 0
        self._pending: set[asyncio.Task[Any]] = set()

    def wrap_gateway(self, gateway: ExecutionGateway) -> None:
        """Wrap place, cancel, and market_order so teardown drains in-flight I/O."""
        original_place = gateway.place
        original_cancel = gateway.cancel
        latch = self

        async def place(quotes: list[Quote], meta: MarketMeta) -> list[OpenOrder]:
            if latch.closed:
                return _refuse_closed_place(quotes)

            async def call() -> list[OpenOrder]:
                if latch.closed:
                    return _refuse_closed_place(quotes)
                return await original_place(quotes, meta)

            return await latch._await_in_flight(call())

        async def cancel(order_ids: list[str]) -> bool:
            return await latch._await_in_flight(original_cancel(order_ids))

        gateway.place = place
        gateway.cancel = cancel
        _wrap_market_order(latch, gateway)
        cancel_asset = getattr(gateway, "cancel_asset", None)
        if callable(cancel_asset):
            bound_cancel_asset = cast(Callable[[str], Awaitable[bool]], cancel_asset)

            async def wrapped_cancel_asset(asset_id: str) -> bool:
                return await latch._await_in_flight(bound_cancel_asset(asset_id))

            gateway.cancel_asset = wrapped_cancel_asset
        cancel_all = getattr(gateway, "cancel_all", None)
        if callable(cancel_all):
            bound_cancel_all = cast(Callable[[], Awaitable[None]], cancel_all)

            async def wrapped_cancel_all() -> None:
                await latch._await_in_flight(bound_cancel_all())

            gateway.cancel_all = wrapped_cancel_all

    async def _await_in_flight(self, awaitable: Awaitable[_T]) -> _T:
        """Count one call until the inner await finishes, including after task cancel."""
        self.in_flight += 1
        task = asyncio.ensure_future(awaitable)
        self._pending.add(task)
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self.in_flight -= 1
                self._pending.discard(task)
            else:
                releaser = asyncio.create_task(self._release_when_done(task))
                self._pending.add(releaser)
                releaser.add_done_callback(self._pending.discard)

    async def _release_when_done(self, task: asyncio.Task[Any]) -> None:
        """Decrement in_flight after a shielded inner call finishes."""
        try:
            await task
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            self.in_flight -= 1
            self._pending.discard(task)

    def close(self) -> None:
        """Stop accepting new places. In-flight calls still finish."""
        self.closed = True

    async def drain(self, timeout_s: float) -> bool:
        """Wait until in-flight place/cancel calls finish. False on timeout."""
        deadline = time.monotonic() + timeout_s
        while self.in_flight > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return self.in_flight == 0


def _refuse_closed_place(quotes: list[Quote]) -> list[OpenOrder]:
    """Empty quotes stay empty; a non-empty list must not look like a partial batch."""
    if quotes:
        raise GatewayClosed("gateway closed")
    return []


def _wrap_market_order(latch: ShutdownLatch, gateway: ExecutionGateway) -> None:
    """Count dust FAK calls in-flight and refuse them after close."""
    original = gateway.market_order

    async def market_order(
        token_id: str,
        side: Side,
        amount: float,
        meta: MarketMeta,
        *,
        fak: bool = True,
    ) -> dict[str, Any]:
        if latch.closed:
            return {"status": "failed", "error": "closed"}
        return await latch._await_in_flight(original(token_id, side, amount, meta, fak=fak))

    gateway.market_order = market_order


def patch_engine_classes() -> EngineClassRestore:
    """Point Engine construction at the wallet store, processor, and user stream."""
    restore = EngineClassRestore(
        state_store=polymaker_engine.StateStore,
        catalog_store=polymaker_engine.CatalogStore,
        processor=polymaker_engine.UserEventProcessor,
        user_stream=polymaker_engine.UserStream,
        normalize_trade=userstream_client.normalize_trade,
        journal=polymaker_engine.Journal,
    )
    polymaker_engine.StateStore = WalletStateStore
    polymaker_engine.CatalogStore = WalletCatalogStore
    polymaker_engine.UserEventProcessor = WalletFillProcessor
    polymaker_engine.UserStream = WalletUserStream
    userstream_client.normalize_trade = normalize_maker_trades
    polymaker_engine.Journal = SlimJournal
    return restore


def restore_engine_classes(restore: EngineClassRestore) -> None:
    """Put the fork Engine class objects back."""
    polymaker_engine.StateStore = restore.state_store
    polymaker_engine.CatalogStore = restore.catalog_store
    polymaker_engine.UserEventProcessor = restore.processor
    polymaker_engine.UserStream = restore.user_stream
    userstream_client.normalize_trade = restore.normalize_trade
    polymaker_engine.Journal = restore.journal


def install_strict_rest(engine: Engine) -> None:
    """Make live REST reads conservative and every cancel path require proof."""
    if engine.paper:
        return
    gateway = engine.gateway
    original_cancel_asset = gateway.cancel_asset

    async def cancel(order_ids: list[str]) -> bool:
        return await _cancel_with_proof(gateway, order_ids)

    async def cancel_asset(asset_id: str) -> bool:
        await original_cancel_asset(asset_id)
        return await _prove_asset_orders_canceled(engine, asset_id)

    async def open_orders() -> list[OpenOrder]:
        return await _open_orders_with_proof(engine)

    gateway.cancel = cancel
    gateway.cancel_asset = cancel_asset
    gateway.open_orders = open_orders


def install_collateral_snapshot(engine: Engine, cache: CollateralCache) -> None:
    """Refresh the wallet USDC cache after each REST positions read."""
    gateway = engine.gateway
    original_positions = gateway.positions

    async def positions() -> dict[str, tuple[float, float]]:
        snapshot, balance = await asyncio.gather(original_positions(), gateway.collateral_balance())
        cache.value = balance
        if cache.value <= 0.0:
            logger.info("trader collateral cache empty: BUY blocked until first REST read")
        return snapshot

    gateway.positions = positions


def install_rest_snapshot_stamps(engine: Engine) -> None:
    """Stamp when each REST positions request left, so a write-down can date its snapshot.

    The reply time is later than the snapshot the exchange took, and a fill
    between the two is not in that reply. Dating a write-down by the send time
    keeps the watermark conservative: it never discards a fill the snapshot missed.
    """
    gateway = engine.gateway
    store = cast(WalletStateStore, engine.state)
    original_positions = gateway.positions

    async def positions() -> dict[str, tuple[float, float]]:
        store.note_rest_positions_sent(time.time())
        return await original_positions()

    gateway.positions = positions


async def _pull_missed_fills(engine: Engine, store: WalletStateStore) -> None:
    """Book CLOB trades from the last LOOKBACK_S that the maker-only WS parse dropped."""
    gateway = engine.gateway
    client = gateway._client
    if client is None:
        return

    def fetch() -> list[object]:
        params = TradeParams(
            maker_address=gateway.funder,
            after=int(time.time() - LOOKBACK_S),
        )
        return list(client.get_trades(params))

    trades = await gateway._io(fetch)
    result = backfill_trades(store, trades, gateway.funder, engine._other_token)
    if result.applied + result.confirmed == 0:
        return
    for key in result.keys:
        logger.warning("trade_backfill fill_key=%s", key)
    notify_in_background(
        "trader rest backfill: "
        f"{result.applied} new, {result.confirmed} confirmed, cash {result.cash:.2f}"
    )


def install_rest_fill_recovery(
    engine: Engine,
    store: WalletStateStore,
    drain_outbox: Callable[[], None],
    reconcile_unsettled_buys: Callable[[], Awaitable[None]],
) -> None:
    """Wrap the REST positions read: backfill, sweep unsettled BUYs, then journal.

    Both halves depend on where this seam sits. Install it after
    `install_rest_snapshot_stamps` so the backfill runs before that seam moves the
    write-down watermark, which would otherwise mark a recovered fill superseded.
    Draining runs once the snapshot returns, so the journal sees what this pass booked.
    A failed trade read does not skip the other recovery or the positions snapshot.
    """
    gateway = engine.gateway
    original_positions = gateway.positions

    async def positions() -> dict[str, tuple[float, float]]:
        if not engine.paper:
            try:
                await _pull_missed_fills(engine, store)
            except Exception:
                logger.exception("trade_backfill failed")
            try:
                await reconcile_unsettled_buys()
            except Exception:
                logger.exception("unsettled buy reconcile failed")
        snapshot = await original_positions()
        drain_outbox()
        return snapshot

    gateway.positions = positions


def install_heartbeat_boot_grace(engine: Engine) -> None:
    """Hold the dead-man halt until the heartbeat chain works once, or the grace ends.

    The CLOB rejects the first beats of a new session with `Invalid Heartbeat ID`,
    so every boot logged three `heartbeat_failed` lines and one critical
    `heartbeat_down_halting` before the chain started. A boot has no live orders
    for the exchange to auto-cancel, so those failures protect nothing. After the
    first success, or after HEARTBEAT_BOOT_GRACE_SECONDS, failures arm the halt
    again.
    """
    if engine.paper:
        return
    gateway = engine.gateway
    original_heartbeat = gateway.heartbeat
    grace_ends = time.monotonic() + HEARTBEAT_BOOT_GRACE_SECONDS
    chain_started = False

    async def heartbeat() -> bool:
        nonlocal chain_started
        beat_ok = await original_heartbeat()
        if beat_ok:
            chain_started = True
            return True
        if chain_started or time.monotonic() >= grace_ends:
            return False
        logger.info("trader heartbeat chain not started yet; dead-man halt held")
        gateway._hb_failures = 0
        return True

    gateway.heartbeat = heartbeat


async def _cancel_with_proof(gateway: ExecutionGateway, order_ids: list[str]) -> bool:
    """True only when every id is in canceled or a documented already-gone reason."""
    if not order_ids:
        return True
    if gateway._paper or gateway._client is None:
        logger.warning("cancel_unproven n=%s reason=no_client", len(order_ids))
        return False
    bucket = getattr(gateway, "_cancel_bucket", None)
    if bucket is not None:
        await bucket.acquire(1)

    def _cancel() -> object:
        return gateway._client.cancel_orders(order_ids)

    try:
        resp = await gateway._io(_cancel)
    except Exception as exc:
        logger.warning("cancel_unproven n=%s err=%s", len(order_ids), exc)
        return False
    terminal = _terminal_cancel_ids(resp)
    if terminal is None or not set(order_ids) <= terminal:
        logger.warning("cancel_unproven n=%s", len(order_ids))
        return False
    return True


async def _open_orders_with_proof(engine: Engine) -> list[OpenOrder]:
    """Return REST orders plus every unproven local order without mutating state."""
    gateway = engine.gateway
    if gateway._paper or gateway._client is None:
        raise RestUnproven("open_orders unproven")

    def _get() -> object:
        return gateway._client.get_open_orders()

    try:
        raw = await gateway._io(_get)
    except Exception as exc:
        raise RestUnproven("open_orders unproven") from exc
    rest_orders = _parse_open_orders_payload(raw)
    if rest_orders is None:
        raise RestUnproven("open_orders unproven")
    by_id = {order.order_id: order for order in rest_orders}
    for order in engine.state.orders.values():
        by_id.setdefault(order.order_id, order)
    return list(by_id.values())


async def _prove_asset_orders_canceled(engine: Engine, asset_id: str) -> bool:
    """Cancel and remove only orders for one asset after terminal per-id proof."""
    try:
        live = await engine.gateway.open_orders()
    except RestUnproven:
        return False
    order_ids = list(dict.fromkeys(order.order_id for order in live if order.token_id == asset_id))
    if not order_ids:
        return True
    if not await engine.gateway.cancel(order_ids):
        return False
    for order_id in order_ids:
        engine.state.remove_order(order_id)
    cid = engine._token_cid.get(asset_id)
    if cid:
        engine._wake_cid(cid)
    return True


def _collect_terminal_cancel_ids(
    canceled: list[object], not_canceled: dict[object, object]
) -> set[str]:
    """Ids in canceled[] plus documented already-gone not_canceled reasons."""
    terminal: set[str] = set()
    for item in canceled:
        if item:
            terminal.add(str(item))
    for order_id, reason in not_canceled.items():
        if order_id and _cancel_reason_is_terminal(reason):
            terminal.add(str(order_id))
    return terminal


def _terminal_cancel_ids(resp: object) -> set[str] | None:
    """Proven-terminal order ids from a cancel_orders body. None if unreadable."""
    if type(resp) is not dict:
        return None
    has_canceled = "canceled" in resp
    not_canceled_raw = resp.get("not_canceled", resp.get("notCancelled"))
    has_not_canceled = "not_canceled" in resp or "notCancelled" in resp
    if not has_canceled and not has_not_canceled:
        return None
    canceled = resp.get("canceled", [])
    if canceled is None:
        canceled = []
    if type(canceled) is not list:
        return None
    not_canceled = not_canceled_raw
    if not_canceled is None:
        not_canceled = {}
    if type(not_canceled) is not dict:
        return None
    return _collect_terminal_cancel_ids(
        cast(list[object], canceled),
        cast(dict[object, object], not_canceled),
    )


def _cancel_reason_is_terminal(reason: object) -> bool:
    """True for the documented not-found / already-canceled cancel reasons."""
    text = str(reason).lower()
    return (
        "not found" in text
        or "already canceled" in text
        or "already cancelled" in text
        or "can't be found" in text
        or "cannot be found" in text
    )


def _parse_open_orders_payload(raw: object) -> list[OpenOrder] | None:
    """Parse a CLOB open-orders body. None means malformed, not empty."""
    if type(raw) is list:
        rows = raw
    elif type(raw) is dict:
        if "data" in raw:
            rows = raw["data"]
        elif "orders" in raw:
            rows = raw["orders"]
        else:
            rows = []
    else:
        return None
    if type(rows) is not list:
        return None
    parsed: list[OpenOrder] = []
    for row in rows:
        order = _parse_open_order(row)
        if order is not None:
            parsed.append(order)
    return parsed


def _parse_open_order(row: object) -> OpenOrder | None:
    if type(row) is not dict:
        return None
    try:
        side = Side(str(row["side"]).upper())
        remaining = float(row.get("original_size", row.get("size", 0))) - float(
            row.get("size_matched", 0)
        )
        order_id = _first_str(row, "id", "orderID", "order_id")
        token_id = str(row["asset_id"])
        price = float(row["price"])
    except (KeyError, TypeError, ValueError):
        return None
    if not order_id:
        return None
    return OpenOrder(order_id, token_id, side, price, remaining, OrderState.LIVE)


def _first_str(fields: object, *keys: str) -> str:
    if type(fields) is not dict:
        return ""
    for key in keys:
        value = fields.get(key)
        if value:
            return str(value)
    return ""


def wrap_alert_transitions(engine: Engine) -> None:
    """Forward alerter.alert only when `(key, message)` changes; log when a risk_halt key clears."""
    alerter = engine.alerter
    original_alert = alerter.alert
    original_evaluate = engine.risk.evaluate
    last: dict[str, str] = {}

    def alert(key: str, message: str, *, critical: bool = False) -> None:
        if last.get(key) == message:
            return
        last[key] = message
        original_alert(key, message, critical=critical)

    def evaluate(meta: MarketMeta, *, ws_stale: bool, event_group_cost: float) -> RiskDecision:
        decision = original_evaluate(meta, ws_stale=ws_stale, event_group_cost=event_group_cost)
        if decision.halt:
            return decision
        for key in list(last):
            if not key.startswith("risk_halt:"):
                continue
            logger.warning("alert cleared key=%s", key)
            del last[key]
        return decision

    def forget_order_errors(cid: str) -> None:
        key = f"order_errors:{cid[:8]}"
        if key not in last:
            return
        logger.warning("alert cleared key=%s", key)
        del last[key]

    alerter.alert = alert
    engine.risk.evaluate = evaluate
    cast(Any, engine.risk)._forget_order_errors = forget_order_errors


def _note_stale_block(token_id: str, internal: float, chain_size: float, block_ts: float) -> None:
    """Log a chain head that is too old to change sqlite, when the sizes differ."""
    if rest_size_within_tolerance(internal, chain_size):
        return
    logger.warning(
        "position_divergence stale block token=%s internal=%.2f onchain=%.2f block_ts=%.0f",
        token_id[:12],
        internal,
        chain_size,
        block_ts,
    )


def _note_onchain_excess(
    engine: Engine,
    excess_rounds: dict[str, int],
    store: WalletStateStore,
    token_id: str,
    internal: float,
    chain_size: float,
) -> None:
    """Count consecutive on-chain excess rounds and block new clips past the cap."""
    rounds = excess_rounds.get(token_id, 0) + 1
    excess_rounds[token_id] = rounds
    logger.warning(
        "position_divergence ignored rest-up token=%s internal=%.2f onchain=%.2f rounds=%d",
        token_id[:12],
        internal,
        chain_size,
        rounds,
    )
    if rounds < ONCHAIN_EXCESS_BLOCK_ROUNDS:
        engine.alerter.alert(
            f"divergence_up:{token_id[:8]}",
            f"REST size-up ignored: internal {internal:.1f} vs on-chain {chain_size:.1f}",
            critical=False,
        )
        return
    already_blocked = store.is_buy_blocked(token_id)
    store.block_buy(token_id)
    if already_blocked:
        return
    engine.alerter.alert(
        f"divergence_up:{token_id[:8]}",
        f"on-chain excess held {rounds} rounds: internal {internal:.1f} vs"
        f" on-chain {chain_size:.1f}; buys blocked",
        critical=True,
    )


def wrap_position_divergence(
    engine: Engine,
    read_snapshot: Callable[[list[str]], Awaitable[ChainSnapshot | None]],
) -> None:
    """Alert on drift. Trust a chain read only when its block is fresh for that token."""
    excess_rounds: dict[str, int] = {}

    async def check_position_divergence() -> None:
        tokens = [tok for tok in engine._token_cid if engine.state.inflight(tok) == 0]
        if not tokens:
            return
        sent_at = time.time()
        snapshot = await read_snapshot(tokens)
        if snapshot is None:
            return
        now = time.time()
        store = cast(WalletStateStore, engine.state)
        for tok in tokens:
            _apply_chain_balance(
                engine,
                store,
                excess_rounds,
                tok,
                snapshot=snapshot,
                sent_at=sent_at,
                now=now,
            )

    engine._check_position_divergence = check_position_divergence


def _apply_chain_balance(
    engine: Engine,
    store: WalletStateStore,
    excess_rounds: dict[str, int],
    token_id: str,
    *,
    snapshot: ChainSnapshot,
    sent_at: float,
    now: float,
) -> None:
    """Write sqlite down, restore it from the ledger, or leave it alone."""
    chain_size = snapshot.balances.get(token_id)
    if chain_size is None:
        return
    block_ts = snapshot.block_ts
    covers = snapshot_covers(snapshot, store.chain_read_floor(token_id), now)
    internal = store.position(token_id).size
    if not covers:
        _note_stale_block(token_id, internal, chain_size, block_ts)
        return
    store.note_chain_floor(token_id, block_ts)
    if rest_size_within_tolerance(internal, chain_size):
        store.clear_sell_freeze(token_id)
        store.unblock_buy(token_id)
        excess_rounds.pop(token_id, None)
        return
    if chain_size > internal:
        if store.inflight(token_id) != 0:
            return
        if share_qty_matches(chain_size, store.ledger_position(token_id).size):
            store.restore_ledger_position(token_id, block_ts)
            excess_rounds.pop(token_id, None)
            notify_in_background(
                f"trader position restored from ledger token={token_id[:12]} size={chain_size:.2f}"
            )
            cid = engine._token_cid.get(token_id)
            if cid:
                engine._wake_cid(cid)
            return
        _note_onchain_excess(engine, excess_rounds, store, token_id, internal, chain_size)
        return
    excess_rounds.pop(token_id, None)
    logger.error(
        "position_divergence token=%s internal=%.2f onchain=%.2f",
        token_id[:12],
        internal,
        chain_size,
    )
    engine.alerter.alert(
        f"divergence:{token_id[:8]}",
        f"position drift: internal {internal:.1f} vs on-chain {chain_size:.1f}",
        critical=True,
    )
    skip = rest_size_down_skip_reason(store, token_id, now)
    if skip is not None:
        logger.warning(
            "position divergence write skipped token=%s reason=%s internal=%.2f onchain=%.2f",
            token_id[:12],
            skip,
            internal,
            chain_size,
        )
        return
    store.write_down_chain_position(
        token_id,
        chain_size,
        store.position(token_id).avg_price,
        block_ts,
        min(sent_at, block_ts),
    )
    notify_in_background(
        f"position drift token={token_id[:12]} internal={internal:.2f} onchain={chain_size:.2f}"
    )
    cid = engine._token_cid.get(token_id)
    if cid:
        engine._wake_cid(cid)


class PlaceBatch(list[Quote]):
    """The quote list fork parses, plus the raw CLOB items captured on its thread."""

    items: tuple[object, ...] | None

    def __init__(self, quotes: list[Quote]) -> None:
        super().__init__(quotes)
        self.items = None


def sell_drop_reason(store: WalletStateStore, quote: Quote) -> str | None:
    """Why this SELL cannot leave: `frozen`, `size`, or None when it can."""
    if quote.side is not Side.SELL:
        return None
    if store.is_sell_frozen(quote.token_id):
        return "frozen"
    held = store.position(quote.token_id).size
    if quote.size > held + HALF_SHARE_TICK:
        return "size"
    return None


def sell_is_droppable(store: WalletStateStore, quote: Quote) -> bool:
    """True when this SELL is frozen or larger than sqlite plus the half-tick."""
    return sell_drop_reason(store, quote) is not None


class CidOrderErrors:
    """Per-condition place-error counters. The first trip sticks until reset."""

    def __init__(self, max_rate: float) -> None:
        self._max_rate = max_rate
        self._attempts: dict[str, int] = {}
        self._errors: dict[str, int] = {}
        self._tripped: dict[str, str] = {}

    def note(self, cid: str, ok: bool) -> None:
        """Count one place result against this condition only."""
        self._attempts[cid] = self._attempts.get(cid, 0) + 1
        if not ok:
            self._errors[cid] = self._errors.get(cid, 0) + 1
        if cid in self._tripped:
            return
        attempts = self._attempts[cid]
        if attempts < _ORDER_ERROR_ATTEMPT_FLOOR:
            return
        rate = self._errors.get(cid, 0) / attempts
        if rate < self._max_rate:
            return
        self._tripped[cid] = f"error_rate {rate:.2f} {cid[:8]}"

    def trip_reason(self, cid: str) -> str | None:
        """Return the reason fixed at the first time this cid crossed the cap."""
        return self._tripped.get(cid)

    def reset(self, cid: str) -> None:
        """Drop this cid's counters. Other markets stay put."""
        self._attempts.pop(cid, None)
        self._errors.pop(cid, None)
        self._tripped.pop(cid, None)


def _apply_cid_error_rate(
    decision: RiskDecision, tracker: CidOrderErrors, cid: str
) -> RiskDecision:
    """Drop the fork's process-wide error_rate halt; stop buys on this cid's breaker."""
    if decision.halt and "error_rate" in decision.reason:
        decision = RiskDecision(False, False, 1.0, "")
    reason = tracker.trip_reason(cid)
    if reason is None or decision.halt:
        return decision
    return RiskDecision(False, True, decision.size_scale, reason)


async def _place_guarded(
    quotes: list[Quote],
    meta: MarketMeta,
    store: WalletStateStore,
    cores: Mapping[str, LiveCore] | None,
    original_place: Callable[[list[Quote], MarketMeta], Awaitable[list[OpenOrder]]],
) -> list[OpenOrder]:
    """Drop doomed SELLs, place the rest, then freeze and classify the reply."""
    _PLACE_CID.set(meta.condition_id)
    _SKIP_ORDER_ERROR.set(False)
    kept = _esports_place_quotes(quotes, meta, store, cores)
    dropped_sell = len(kept) < len(quotes)
    if not kept:
        if unplaced_quotes_explained(dropped_sell, [], None):
            _SKIP_ORDER_ERROR.set(True)
        return []
    batch = PlaceBatch(kept)
    placed = await original_place(batch, meta)
    freeze_sell_rejects(store, batch, batch.items)
    if unplaced_quotes_explained(dropped_sell, batch, batch.items):
        _SKIP_ORDER_ERROR.set(True)
    return placed


def _alert_cid_breaker(
    engine: Engine, cid: str, decision: RiskDecision, tracker: CidOrderErrors
) -> None:
    """Alert once per cid when the breaker has stopped buys and the exit still quotes."""
    reason = tracker.trip_reason(cid)
    if reason is None or decision.halt:
        return
    cid8 = cid[:8]
    engine.alerter.alert(
        f"order_errors:{cid8}",
        f"{cid8} {reason}: buys stopped, exit keeps quoting",
        critical=True,
    )


def _esports_place_quotes(
    quotes: list[Quote],
    meta: MarketMeta,
    store: WalletStateStore,
    cores: Mapping[str, LiveCore] | None,
) -> list[Quote]:
    if cores is not None and meta.condition_id in cores:
        return quotes
    return [quote for quote in quotes if not sell_is_droppable(store, quote)]


def wrap_inventory_place_guard(engine: Engine, cores: Mapping[str, LiveCore] | None = None) -> None:
    """Drop doomed SELLs, freeze inventory rejects, and stop buys on this cid's breaker."""
    store = cast(WalletStateStore, engine.state)
    gateway = engine.gateway
    risk = engine.risk
    original_place = gateway.place
    original_parse = getattr(gateway, "_parse_place_response", None)
    original_evaluate = risk.evaluate
    tracker = CidOrderErrors(engine.cfg.risk.max_order_error_rate)
    cast(Any, risk)._cid_order_errors = tracker

    def parse_place_response(resp: object, quotes: list[Quote]) -> list[OpenOrder]:
        """Parse the CLOB body and keep the raw items for the place coroutine."""
        assert original_parse is not None
        if isinstance(quotes, PlaceBatch):
            quotes.items = clob_place_items(resp)
        return original_parse(resp, quotes)

    async def place(quotes: list[Quote], meta: MarketMeta) -> list[OpenOrder]:
        """Filter frozen/oversized SELLs, then place the rest."""
        return await _place_guarded(quotes, meta, store, cores, original_place)

    def note_order_result(ok: bool) -> None:
        """Count this place on the cid from place(); skip explained SELL rejects."""
        cid = _PLACE_CID.get()
        _PLACE_CID.set(None)
        if _SKIP_ORDER_ERROR.get():
            _SKIP_ORDER_ERROR.set(False)
            return
        if cid is None:
            return
        tracker.note(cid, ok)

    def evaluate(meta: MarketMeta, *, ws_stale: bool, event_group_cost: float) -> RiskDecision:
        """Keep fork kills global; stop buys when this cid's breaker has tripped."""
        decision = original_evaluate(meta, ws_stale=ws_stale, event_group_cost=event_group_cost)
        decision = _apply_cid_error_rate(decision, tracker, meta.condition_id)
        _alert_cid_breaker(engine, meta.condition_id, decision, tracker)
        return decision

    gateway.place = place
    if original_parse is not None:
        gateway._parse_place_response = parse_place_response
    risk.note_order_result = note_order_result
    risk.evaluate = evaluate


def wrap_risk_from_ledger(engine: Engine, store: WalletStateStore) -> None:
    """Cash and UTC-day stay in memory. evaluate HALTs on a sqlite error."""
    risk = engine.risk
    original_evaluate = risk.evaluate

    def equity_snapshot() -> float:
        mark_inventory_from_books(engine)
        return store.running_net_cash + risk.inventory_value

    def refresh_cash_and_day() -> None:
        mark_inventory_from_books(engine)
        risk._net_cash = store.running_net_cash
        day = store.ensure_utc_day()
        risk._day_start_equity = day.day_start_equity

    def note_fill(fill: Fill) -> None:
        del fill
        refresh_cash_and_day()

    def reset_day() -> None:
        refresh_cash_and_day()

    def evaluate(meta: MarketMeta, *, ws_stale: bool, event_group_cost: float) -> RiskDecision:
        try:
            refresh_cash_and_day()
        except sqlite3.Error:
            logger.warning("trader risk store failed during evaluate")
            return RiskDecision(True, False, 0.0, "store_error")
        return original_evaluate(meta, ws_stale=ws_stale, event_group_cost=event_group_cost)

    store.set_equity_snapshot(equity_snapshot)
    risk.note_fill = note_fill
    risk.reset_day = reset_day
    risk.evaluate = evaluate
    refresh_cash_and_day()


def pin_engine_identity(engine: Engine) -> None:
    """Pin signature/chain always; pin funder only when gateway.funder is nonempty."""
    if not isinstance(engine.state, WalletStateStore):
        raise TypeError("wallet host requires WalletStateStore")
    engine.state.pin_static(engine.cfg.wallet.signature_type, engine.cfg.wallet.chain_id)
    funder = engine.gateway.funder
    if funder:
        engine.state.pin_funder(funder)


def bind_user_fill_address(engine: Engine) -> None:
    """Match user-WS maker rows on the funder (Safe), not the signer EOA.

    Engine.start passes gateway.address into UserStream. Gnosis maker_address is
    BROWSER_ADDRESS. A miss drops every fill, so Telegram PnL stays 0.
    """
    user = engine.user
    if user is None:
        return
    funder = engine.gateway.funder
    if not funder:
        return
    user._address = funder


def reset_order_error_rate(risk: RiskManager, cid: str) -> None:
    """Zero the fork counters and this cid's place-error counts. Other cids stay."""
    risk._order_attempts = 0
    risk._order_errors = 0
    forget = getattr(risk, "_forget_order_errors", None)
    if callable(forget):
        forget(cid)
    tracker = getattr(risk, "_cid_order_errors", None)
    if not isinstance(tracker, CidOrderErrors):
        return
    tracker.reset(cid)


def mark_inventory_from_books(engine: Engine) -> None:
    """Push YES/NO book mids into risk marks. Dead leftover books keep the last mark."""
    for token_id, book in engine.md.books.items():
        bid = book.best_bid()
        ask = book.best_ask()
        if bid is None or ask is None:
            continue
        engine.risk.update_mark(token_id, (bid.price + ask.price) / 2.0)


def cash_only_on_fill(engine: Engine) -> Callable[[Fill], None]:
    """Engine._on_fill replacement: ledger cash only, no markout."""

    def on_fill(fill: Fill) -> None:
        engine.risk.note_fill(fill)

    return on_fill


def record_engine_markout(engine: Engine, fill: Fill) -> None:
    """Markout on CONFIRMED from last_fv. Skip when restart left no historical FV."""
    cid = engine._token_cid.get(fill.token_id)
    if cid is None:
        return
    meta = engine.metas.get(cid)
    estimators = getattr(engine, "est", None)
    if meta is None or estimators is None:
        return
    est = estimators.get(cid)
    if est is None:
        return
    fv = est.last_fv
    if fv is None:
        return
    token_fv = fv if fill.token_id == meta.yes.token_id else (1.0 - fv)
    est.markout.record_fill(fill.side, token_fv, fill.ts)


def attach_market(
    engine: Engine,
    meta: MarketMeta,
    profile: StrategyProfile,
    cell: StrategyCell,
    readiness: BookReadiness,
) -> None:
    """Copy-assign engine dicts; _token_cid last. Resubscribe market WS. Spawn the quoter.

    Refuses a cid already in engine.metas so a remake cannot replace live locks.
    """
    cid = meta.condition_id
    if cid in engine.metas:
        raise RuntimeError("attach_market refused: condition already attached")
    engine.metas = {**engine.metas, cid: meta}
    engine.profiles = {**engine.profiles, cid: profile}
    engine.est = {**engine.est, cid: engine._make_estimators(profile)}
    engine.regime_m = {**engine.regime_m, cid: GatedRegimeMachine(RegimeMachine(), cell)}
    engine._dirty = {**engine._dirty, cid: asyncio.Event()}
    engine._locks = {**engine._locks, cid: asyncio.Lock()}
    token_cid = dict(engine._token_cid)
    token_cid[meta.yes.token_id] = cid
    token_cid[meta.no.token_id] = cid
    engine._token_cid = token_cid
    if isinstance(engine.state, WalletStateStore):
        engine.state.persist_token_cid(meta.yes.token_id, cid)
        engine.state.persist_token_cid(meta.no.token_id, cid)
    engine.md.set_markets(market_subscription(engine))
    for token_id in (meta.yes.token_id, meta.no.token_id):
        readiness.attach_token(token_id)
        book = engine.md.book(token_id)
        if book is not None:
            book.set_tick_size(meta.tick_size)
    _resubscribe_market_ws(engine, readiness, cid)
    _spawn_quoter(engine, cid)
    reconnect_user_ws(engine)


def detach_market(engine: Engine, cid: str, readiness: BookReadiness) -> None:
    """Drop _token_cid first, then fork leftovers, then MDS books. Resubscribe remaining markets."""
    meta = engine.metas.get(cid)
    if meta is None:
        return
    token_cid = dict(engine._token_cid)
    token_cid.pop(meta.yes.token_id, None)
    token_cid.pop(meta.no.token_id, None)
    engine._token_cid = token_cid
    engine.metas = {key: value for key, value in engine.metas.items() if key != cid}
    engine.profiles = {key: value for key, value in engine.profiles.items() if key != cid}
    engine.est = {key: value for key, value in engine.est.items() if key != cid}
    engine.regime_m = {key: value for key, value in engine.regime_m.items() if key != cid}
    engine._dirty = {key: value for key, value in engine._dirty.items() if key != cid}
    engine._locks = {key: value for key, value in engine._locks.items() if key != cid}
    engine._sweep.pop(cid, None)
    engine._last_quote_fv.pop(cid, None)
    engine._merging.discard(cid)
    engine._halted.discard(cid)
    engine.risk._marks.pop(meta.yes.token_id, None)
    engine.risk._marks.pop(meta.no.token_id, None)
    quote_name = _quote_task_name(cid)
    engine._task_specs.pop(quote_name, None)
    engine._tasks.pop(quote_name, None)
    for token_id in (meta.yes.token_id, meta.no.token_id):
        engine.md.books.pop(token_id, None)
        engine.md._token_condition.pop(token_id, None)
    engine.md.set_markets(market_subscription(engine))
    _resubscribe_market_ws(engine, readiness, cid)
    reconnect_user_ws(engine)


async def stop_quoter(engine: Engine, cid: str) -> None:
    """Cancel the quote task, keep a no-op _task_specs factory, drop the _tasks entry."""
    name = _quote_task_name(cid)
    _install_idle_quoter_spec(engine, cid)
    task = engine._tasks.get(name)
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task
    engine._tasks.pop(name, None)


def reconnect_user_ws(engine: Engine) -> None:
    """Push the live market list and close the user socket so the next hello carries it."""
    user = engine.user
    if user is None:
        return
    user.set_markets(list(engine.metas))
    closer = getattr(user, "close_socket", None)
    if closer is not None:
        closer()


_book_only_markets: dict[str, tuple[str, str]] = {}


def market_subscription(engine: Engine) -> list[tuple[str, list[str]]]:
    """Attached markets plus book-only watches that are not already attached."""
    markets = [
        (item.condition_id, [item.yes.token_id, item.no.token_id]) for item in engine.metas.values()
    ]
    attached = {cid for cid, _tokens in markets}
    for cid, (yes, no) in _book_only_markets.items():
        if cid not in attached:
            markets.append((cid, [yes, no]))
    return markets


def apply_book_only_markets(
    engine: Engine, markets: Mapping[str, tuple[str, str]], readiness: BookReadiness
) -> None:
    """Subscribe extra books without a quoter. Reconnect only when the set changes."""
    if dict(_book_only_markets) == dict(markets):
        return
    _book_only_markets.clear()
    _book_only_markets.update(markets)
    engine.md.set_markets(market_subscription(engine))
    _resubscribe_market_ws(engine, readiness, "")


def reconnect_market_ws(engine: Engine) -> bool:
    """Close md._ws so md.run reconnects and subscribes the current _subs. True if a handle existed."""
    ws = engine.md._ws
    if ws is None:
        return False
    task = close_live_socket(ws)
    if task is not None:
        _market_ws_close_tasks.clear()
        _market_ws_close_tasks.append(task)
    engine.md._ws = None
    return True


def install_book_readiness(engine: Engine, readiness: BookReadiness) -> None:
    """Mark a token ready when MDS mutates its book. Wraps both paper and live _on_dirty."""
    original = engine.md._on_dirty

    def noted(condition_id: str, token_id: str) -> None:
        readiness.note_snapshot(token_id)
        original(condition_id, token_id)

    engine.md._on_dirty = noted


def _resubscribe_market_ws(engine: Engine, readiness: BookReadiness, skip_cid: str) -> None:
    """Close the live market socket and gate every other attached market until new books arrive."""
    if not reconnect_market_ws(engine):
        return
    for cid, meta in list(engine.metas.items()):
        if cid == skip_cid:
            continue
        readiness.attach_token(meta.yes.token_id)
        readiness.attach_token(meta.no.token_id)
        regime = engine.regime_m.get(cid)
        cell = getattr(regime, "_gate", None)
        if isinstance(cell, StrategyCell):
            cell.clear()
        dirty = engine._dirty.get(cid)
        if dirty is not None:
            dirty.set()


async def fence_no_orders(engine: Engine, token_ids: set[str]) -> bool:
    """True when live REST shows no orders on these tokens. Unproven is not proven."""
    try:
        live = await engine.gateway.open_orders()
    except RestUnproven:
        return False
    return not any(order.token_id in token_ids for order in live)


def install_core_quoter_wake(engine: Engine, cores: Mapping[str, LiveCore]) -> None:
    """Cap the fork idle timeout at the core's next SELL/cadence deadline.

    A past deadline fires once, like the backtest's _arm_wake. The fork skips the
    core on a one-sided book, so the same stale deadline would spin at timeout 0.
    """
    original = engine._next_wake_s
    fired_wake_ns: dict[str, int] = {}

    def next_wake_s(cid: str, base_tick: float) -> float:
        timeout = original(cid, base_tick)
        core = cores.get(cid)
        if core is None or core.next_wake_ns <= 0:
            return timeout
        wake_ns = core.next_wake_ns
        delay_s = (wake_ns - core_now_ns()) / 1e9
        if delay_s > 0.0:
            return min(timeout, delay_s)
        if fired_wake_ns.get(cid) == wake_ns:
            return timeout
        fired_wake_ns[cid] = wake_ns
        return 0.0

    engine._next_wake_s = next_wake_s


def _spawn_quoter(engine: Engine, cid: str) -> None:
    name = _quote_task_name(cid)

    def factory() -> Any:
        return engine._quoter(cid)

    engine._task_specs[name] = factory
    previous = engine._tasks.get(name)
    if previous is not None and not previous.done():
        previous.cancel()
    if engine._running:
        engine._tasks[name] = asyncio.create_task(factory(), name=name)


def _install_idle_quoter_spec(engine: Engine, cid: str) -> None:
    name = _quote_task_name(cid)

    async def idle() -> None:
        while engine._running:
            await asyncio.sleep(IDLE_QUOTER_SLEEP_S)

    engine._task_specs[name] = idle


def _quote_task_name(cid: str) -> str:
    """Engine task name for this market's quoter. Uses the full condition id."""
    return f"quote:{cid}"
