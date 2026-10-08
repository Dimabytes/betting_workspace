"""Live/paper adapter: one market's Follow300 core, inbound queue, and venue plan."""

# pyright: reportPrivateUsage=false

import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Final, cast

from polymaker.domain import OpenOrder, Quote, Regime, Side, TargetQuotes
from polymaker.execution.reconciler import ReconcilePlan
from polymaker.marketdata.orderbook import BookView, OrderBook
from polymaker.strategy.quoting import QuoteInputs

from shared.utils.log import get_logger
from strategy.budget import reserve_buy_notional
from strategy.engine import step
from strategy.lifecycle import already_canceling, empty_state
from strategy.policy import Follow300Policy
from strategy.signals import price_cents
from strategy.types import (
    BlockReason,
    BookPair,
    BookUpdate,
    Budget,
    BudgetUpdate,
    BuySettled,
    CancelAck,
    CancelOrder,
    CancelTimeout,
    CancelUnsettled,
    ClockUpdate,
    Fill,
    FreshnessLimits,
    GameClock,
    InboundEvent,
    LimitsUpdate,
    MarketLimits,
    Merged,
    OrderAccepted,
    OrderRecord,
    OrderRejected,
    Permissions,
    PermissionsUpdate,
    PlaceOrder,
    Plan,
    RawDeltaSignal,
    Recovery,
    RecoveryVerified,
    RestingOrder,
    SignalUpdate,
    StrategyState,
    SubmitTimeout,
    TokenBook,
    TokenInventory,
    Wake,
)
from trader.core_persistence import (
    StructuralCheckpoint,
    apply_checkpoint,
    resolved_unsettled_buys,
    restore_last_buy,
    snapshot_checkpoint,
)
from trader.core_trace import CoreTrace
from trader.session_budget import budget_from_orders
from trader.session_types import EntryBlock
from trader.wallet_store import WalletStateStore, share_qty_matches

logger = get_logger(__name__)


@dataclass(frozen=True)
class PlacedQuote:
    order_id: str
    quote: Quote


@dataclass(frozen=True)
class PlannedBatch:
    cycle: int
    to_cancel: tuple[str, ...]
    to_place: tuple[PlacedQuote, ...]


@dataclass(frozen=True)
class DrainedPlan:
    places: tuple[PlaceOrder, ...]
    cancels: tuple[CancelOrder, ...]


@dataclass(frozen=True)
class BuyCancelClock:
    """Monotonic start of one BUY's cancel, kept until the core drops the order."""

    order_id: str
    started_ns: int


@dataclass(frozen=True)
class CoreMemory:
    state: StrategyState
    core_to_venue: tuple[tuple[str, str], ...]
    venue_to_core: tuple[tuple[str, str], ...]
    last_outbox_seq: int
    pending: tuple[InboundEvent, ...]
    deferred_plan: DrainedPlan
    last_batch: PlannedBatch | None
    trace_seq: int
    buy_cancel_clocks: tuple[BuyCancelClock, ...]


@dataclass(frozen=True)
class LiveSources:
    now_ns: int
    books: BookPair | None
    clock: GameClock
    limits: MarketLimits
    permissions: Permissions
    budget: Budget
    store_yes: float
    store_no: float
    settled_buys: Mapping[str, float]


@dataclass
class CollateralCache:
    value: float = 0.0


@dataclass(frozen=True)
class HeldPosition:
    token_index: int
    qty: float


# `_venue_cancels` resends a cancel every cycle, so an order older than this
# means the venue never proved it. A SELL left here holds the only exit slot.
STALE_CANCEL_SECONDS: Final[float] = 30.0


_ENTRY_BLOCK: Final[dict[str, EntryBlock]] = {
    "": EntryBlock.NONE,
    "cutoff": EntryBlock.CUTOFF,
    "min_delta": EntryBlock.MIN_DELTA,
    "nw_velocity": EntryBlock.NW_VELOCITY,
    "missing_nw": EntryBlock.MISSING_NW,
    "wide_spread": EntryBlock.WIDE_SPREAD,
    "min_price": EntryBlock.MIN_PRICE,
    "max_price": EntryBlock.MAX_PRICE,
    "recovery": EntryBlock.RECOVERY,
    "winding_down": EntryBlock.WINDING_DOWN,
    "ownership_unresolved": EntryBlock.OWNERSHIP_UNRESOLVED,
    "position_open": EntryBlock.POSITION_OPEN,
    "no_cash": EntryBlock.NO_CASH,
    "position_cap": EntryBlock.POSITION_CAP,
    "account_cap": EntryBlock.ACCOUNT_CAP,
    "paused": EntryBlock.PAUSED,
    "halt": EntryBlock.HALT,
    "mid_spike": EntryBlock.MID_SPIKE,
    "kill": EntryBlock.KILL,
}


def core_now_ns() -> int:
    return time.monotonic_ns()


def entry_block_from_reason(reason: BlockReason) -> EntryBlock:
    return _ENTRY_BLOCK.get(reason, EntryBlock.NO_EDGE)


def exit_state_label(state: StrategyState) -> str:
    """Status of the resting SELL, or "none". A wedged exit reads `canceling`."""
    for order in state.orders:
        if order.side == "SELL":
            return order.status
    return "none"


def held_position(*, yes_size: float, no_size: float, min_size: float) -> HeldPosition | None:
    yes_ok = yes_size >= min_size
    no_ok = no_size >= min_size
    if yes_ok and no_ok:
        if yes_size >= no_size:
            return HeldPosition(0, yes_size)
        return HeldPosition(1, no_size)
    if yes_ok:
        return HeldPosition(0, yes_size)
    if no_ok:
        return HeldPosition(1, no_size)
    return None


def wall_age_to_mono_ns(*, wall_ts: float, now_ns: int, now_wall: float) -> int:
    return now_ns - int((now_wall - wall_ts) * 1e9)


def _quotes_match(order: OpenOrder, quote: Quote) -> bool:
    return (
        order.token_id == quote.token_id
        and order.side is quote.side
        and abs(order.price - quote.price) < 1e-12
        and abs(order.size - quote.size) < 1e-12
    )


def books_from_views(*, yes: BookView, no: BookView, ts_ns: int) -> BookPair | None:
    if (
        yes.best_bid is None
        or yes.best_ask is None
        or no.best_bid is None
        or no.best_ask is None
        or yes.best_bid <= 0
        or yes.best_ask <= 0
        or no.best_bid <= 0
        or no.best_ask <= 0
    ):
        return None
    return BookPair(
        tokens=(
            TokenBook(
                token_index=0,
                bid=yes.best_bid,
                ask=yes.best_ask,
                bid_size=yes.best_bid_size,
                ask_size=yes.best_ask_size,
                ts_ns=ts_ns,
            ),
            TokenBook(
                token_index=1,
                bid=no.best_bid,
                ask=no.best_ask,
                bid_size=no.best_bid_size,
                ask_size=no.best_ask_size,
                ts_ns=ts_ns,
            ),
        )
    )


def _own_remaining(
    orders: Iterable[RestingOrder], *, token_index: int, side: str, book_ts_ns: int
) -> dict[int, float]:
    """Accepted size this snapshot can already contain, per price cent.

    A pending place is not in the WS book. An accept newer than the snapshot
    is not in it either; subtracting either eats foreign size. An accept that
    has not echoed into a newer snapshot is still subtracted. That lag ends
    when a book containing the order arrives.
    """
    own: dict[int, float] = {}
    for order in orders:
        if order.status == "gone":
            continue
        if not order.accepted or order.token_index != token_index or order.side != side:
            continue
        if order.accepted_ns is not None and order.accepted_ns > book_ts_ns:
            continue
        leftover = order.submitted_qty - order.filled_qty
        if leftover <= 0.0:
            continue
        key = price_cents(order.price)
        own[key] = own.get(key, 0.0) + leftover
    return own


def _best_foreign(
    levels: Iterable[tuple[float, float]], own: Mapping[int, float]
) -> tuple[float, float] | None:
    for price, size in levels:
        left = size - own.get(price_cents(price), 0.0)
        if left > 0.0:
            return price, left
    return None


def _stripped_token_book(
    *,
    book: OrderBook,
    token_index: int,
    own_orders: Iterable[RestingOrder],
    ts_ns: int,
) -> TokenBook | None:
    bid = _best_foreign(
        cast(Iterable[tuple[float, float]], reversed(book.bids.items())),
        _own_remaining(own_orders, token_index=token_index, side="BUY", book_ts_ns=ts_ns),
    )
    ask = _best_foreign(
        cast(Iterable[tuple[float, float]], book.asks.items()),
        _own_remaining(own_orders, token_index=token_index, side="SELL", book_ts_ns=ts_ns),
    )
    if bid is None or ask is None:
        # Nautilus drops the snapshot when that side has no best price.
        return None
    bid_price, bid_size = bid
    ask_price, ask_size = ask
    if bid_price <= 0 or ask_price <= 0:
        return None
    return TokenBook(
        token_index=token_index,
        bid=bid_price,
        ask=ask_price,
        bid_size=bid_size,
        ask_size=ask_size,
        ts_ns=ts_ns,
    )


def books_from_md(
    *,
    yes: OrderBook | None,
    no: OrderBook | None,
    now_ns: int,
    own_orders: Iterable[RestingOrder],
) -> BookPair | None:
    if yes is None or no is None:
        return None
    now_wall = time.time()
    yes_ts = wall_age_to_mono_ns(wall_ts=yes.local_ts, now_ns=now_ns, now_wall=now_wall)
    no_ts = wall_age_to_mono_ns(wall_ts=no.local_ts, now_ns=now_ns, now_wall=now_wall)
    yes_book = _stripped_token_book(book=yes, token_index=0, own_orders=own_orders, ts_ns=yes_ts)
    no_book = _stripped_token_book(book=no, token_index=1, own_orders=own_orders, ts_ns=no_ts)
    if yes_book is None or no_book is None:
        return None
    return BookPair(tokens=(yes_book, no_book))


def permissions_from_quote(
    *,
    regime: Regime,
    store: WalletStateStore,
    yes: str,
    no: str,
    sidecar_usable: bool,
    held_token: str | None,
) -> Permissions:
    allow_sell = True if held_token is None else not store.is_sell_frozen(held_token)
    return Permissions(
        halt=regime is Regime.HALTED,
        reduce_only=regime is Regime.REDUCE_ONLY,
        allow_buy=sidecar_usable and not store.is_buy_blocked(yes) and not store.is_buy_blocked(no),
        allow_sell=allow_sell,
        sell_unconfirmed=store.inflight(yes) > 0 or store.inflight(no) > 0,
    )


class LiveCore:
    """One market's Follow300 core: inbound queue, id map, plan for this cycle."""

    def __init__(
        self,
        *,
        policy: Follow300Policy,
        limits: MarketLimits,
        freshness: FreshnessLimits,
        yes_token: str,
        no_token: str,
        drop_sell: Callable[[Quote], bool],
        trace: CoreTrace | None,
        max_position_levels: int,
    ) -> None:
        self._policy = policy
        self._yes = yes_token
        self._no = no_token
        self._drop_sell = drop_sell
        self._trace = trace
        self._state = empty_state(
            limits=limits,
            freshness=freshness,
            permissions=Permissions(
                halt=False,
                reduce_only=False,
                allow_buy=True,
                allow_sell=True,
                sell_unconfirmed=False,
            ),
            budget=Budget(
                cash_usdc=0.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
            ),
            clock=GameClock(now_ns=0, game_second=0, paused=False, game_ended=False),
        )
        self._pending: deque[InboundEvent] = deque()
        self._deferred_plan = DrainedPlan(places=(), cancels=())
        self._core_to_venue: dict[str, str] = {}
        self._venue_to_core: dict[str, str] = {}
        self._planned: PlannedBatch | None = None
        self._last_batch: PlannedBatch | None = None
        self._undispatched: tuple[str, ...] = ()
        self._canceling_since: dict[str, int] = {}
        self._buy_cancel_timing: dict[str, BuyCancelClock] = {}
        self._canceling_warned: set[str] = set()
        self._stale_cancels: tuple[str, ...] = ()
        self._cycle = 0
        self.session_id = ""
        self.max_position_levels = max_position_levels
        self.last_block: BlockReason = ""
        self.sidecar_usable = True
        self.latest_clock = self._state.clock
        self.last_outbox_seq = 0
        self.next_wake_ns = 0

    def set_clock(self, clock: GameClock) -> None:
        self.latest_clock = clock
        self.enqueue(ClockUpdate(now_ns=clock.now_ns, clock=clock))

    @property
    def state(self) -> StrategyState:
        return self._state

    @property
    def policy(self) -> Follow300Policy:
        return self._policy

    def token_id(self, token_index: int) -> str:
        return self._yes if token_index == 0 else self._no

    def token_index(self, token_id: str) -> int | None:
        if token_id == self._yes:
            return 0
        if token_id == self._no:
            return 1
        return None

    def reserved_buy_notional(self) -> float:
        return reserve_buy_notional(self._state)

    def venue_ids(self) -> frozenset[str]:
        return frozenset(self._venue_to_core)

    def enqueue(self, event: InboundEvent) -> None:
        self._pending.append(event)

    def preview_block_reason(self) -> BlockReason:
        """Block a Wake would report for events already queued. Does not drain.

        ponytail: books, budget, and halt stay as last synced. Those arrive in
        the quoter cycle. Upgrade path: write entry_block from stash.
        """
        state = self._state
        now_ns = self.latest_clock.now_ns
        for event in self._pending:
            out = step(state=state, policy=self._policy, event=event)
            state = out.state
            now_ns = event.now_ns
        out = step(state=state, policy=self._policy, event=Wake(now_ns=now_ns, forced=True))
        return out.plan.block_reason

    def drain(self) -> list[InboundEvent]:
        events = list(self._pending)
        self._pending.clear()
        return events

    def clear_plan(self) -> None:
        self._planned = None

    def take_plan(self) -> PlannedBatch | None:
        planned = self._planned
        self._planned = None
        if planned is None:
            return None
        if planned.cycle != self._cycle:
            raise AssertionError(f"stale plan cycle={planned.cycle} current={self._cycle}")
        self._last_batch = planned
        return planned

    def restore_last_buy_ns(self, last_buy_ns: int, token_index: int) -> None:
        self._state = restore_last_buy(
            self._state, last_buy_ns=last_buy_ns, token_index=token_index
        )
        if self._trace is not None:
            self._trace.write_last_buy(token_index=token_index, last_buy_ns=last_buy_ns)

    def bind_venue(self, *, core_id: str, venue_id: str) -> None:
        self._core_to_venue[core_id] = venue_id
        self._venue_to_core[venue_id] = core_id

    def find_owned_order(self, venue_id: str) -> RestingOrder | OrderRecord | None:
        """Active order for this venue id, or the retained record after it leaves the book."""
        core_id = self._venue_to_core.get(venue_id)
        if core_id is None:
            return None
        for order in self._state.orders:
            if order.order_id == core_id:
                return order
        for record in self._state.records:
            if record.order_id == core_id:
                return record
        return None

    def capture(self) -> CoreMemory:
        return CoreMemory(
            state=self._state,
            core_to_venue=tuple(self._core_to_venue.items()),
            venue_to_core=tuple(self._venue_to_core.items()),
            last_outbox_seq=self.last_outbox_seq,
            pending=tuple(self._pending),
            deferred_plan=self._deferred_plan,
            last_batch=self._last_batch,
            trace_seq=0 if self._trace is None else self._trace.last_seq,
            buy_cancel_clocks=tuple(self._buy_cancel_timing.values()),
        )

    def revert(self, memory: CoreMemory) -> None:
        self._state = memory.state
        self._core_to_venue = dict(memory.core_to_venue)
        self._venue_to_core = dict(memory.venue_to_core)
        self.last_outbox_seq = memory.last_outbox_seq
        self._pending = deque(memory.pending)
        self._deferred_plan = memory.deferred_plan
        self._last_batch = memory.last_batch
        self._buy_cancel_timing = {clock.order_id: clock for clock in memory.buy_cancel_clocks}
        if self._trace is not None:
            self._trace.write_revert(to_seq=memory.trace_seq)

    def note_unsent(self, core_ids: tuple[str, ...], now_ns: int) -> None:
        for order_id in core_ids:
            self.enqueue(SubmitTimeout(now_ns=now_ns, order_id=order_id))

    def note_fill(
        self,
        *,
        fill_key: str,
        venue_id: str,
        qty: float,
        price: float,
        now_ns: int,
        token_index: int,
        side: str,
    ) -> None:
        core_id = self._venue_to_core.get(venue_id)
        fill_side = "BUY" if side == "BUY" else "SELL"
        if core_id is None:
            logger.warning("trader core unknown venue fill %s", venue_id[:12])
            self.enqueue(
                Fill(
                    now_ns=now_ns,
                    fill_id=fill_key,
                    order_id=venue_id,
                    qty=qty,
                    price=price,
                    token_index=token_index,
                    side=fill_side,
                )
            )
            return
        self.enqueue(
            Fill(
                now_ns=now_ns,
                fill_id=fill_key,
                order_id=core_id,
                qty=qty,
                price=price,
                token_index=token_index,
                side=fill_side,
            )
        )

    def note_merge(self, *, token_index: int, qty: float, now_ns: int) -> None:
        self.enqueue(Merged(now_ns=now_ns, token_index=token_index, qty=qty))

    def note_recovery(self, *, now_ns: int) -> None:
        buys = tuple(order.order_id for order in self._state.orders if order.side == "BUY")
        self.enqueue(Recovery(now_ns=now_ns, restored_buy_ids=buys))

    def note_recovery_verified(
        self,
        *,
        now_ns: int,
        generation: int,
        inventory: tuple[TokenInventory, TokenInventory],
    ) -> None:
        self.enqueue(
            RecoveryVerified(
                now_ns=now_ns,
                generation=generation,
                inventory=inventory,
            )
        )

    def export_checkpoint(self, *, now_ns: int, now_wall_s: float) -> StructuralCheckpoint:
        return snapshot_checkpoint(
            state=self._state,
            now_ns=now_ns,
            now_wall_s=now_wall_s,
            sell_min_life_s=self._policy.sell_min_life_s,
        )

    def restore_checkpoint(
        self, *, checkpoint: StructuralCheckpoint, now_ns: int, now_wall_s: float
    ) -> None:
        self._state = apply_checkpoint(
            self._state,
            checkpoint=checkpoint,
            now_ns=now_ns,
            now_wall_s=now_wall_s,
            sell_min_life_s=self._policy.sell_min_life_s,
        )
        if self._trace is not None:
            self._trace.write_reset(now_ns=now_ns, now_wall_s=now_wall_s, checkpoint=checkpoint)

    def note_placed(self, placed: list[OpenOrder], now_ns: int) -> None:
        batch = self._last_batch
        if batch is None:
            return
        leftover = list(placed)
        if len(placed) > len(batch.to_place):
            logger.warning(
                "trader place extra orders: got=%d planned=%d",
                len(placed),
                len(batch.to_place),
            )
        for item in batch.to_place:
            match_at = next(
                (index for index, order in enumerate(leftover) if _quotes_match(order, item.quote)),
                None,
            )
            if match_at is None:
                self.enqueue(SubmitTimeout(now_ns=now_ns, order_id=item.order_id))
                continue
            order = leftover.pop(match_at)
            self._core_to_venue[item.order_id] = order.order_id
            self._venue_to_core[order.order_id] = item.order_id
            self.enqueue(OrderAccepted(now_ns=now_ns, order_id=item.order_id))

    def note_cancel(self, venue_ids: list[str], ok: bool, now_ns: int) -> None:
        for venue_id in venue_ids:
            core_id = self._venue_to_core.get(venue_id)
            if core_id is None:
                logger.warning("trader core cancel ack unmapped venue=%s", venue_id[:12])
                continue
            owner = self.find_owned_order(venue_id)
            if owner is None:
                continue
            if not ok:
                self.enqueue(CancelTimeout(now_ns=now_ns, order_id=core_id))
                continue
            if owner.side == "BUY":
                self.enqueue(CancelUnsettled(now_ns=now_ns, order_id=core_id))
                continue
            self.enqueue(CancelAck(now_ns=now_ns, order_id=core_id))

    def sync_inputs(self, sources: LiveSources) -> list[InboundEvent]:
        events: list[InboundEvent] = [
            LimitsUpdate(now_ns=sources.now_ns, limits=sources.limits),
            ClockUpdate(now_ns=sources.now_ns, clock=sources.clock),
            PermissionsUpdate(now_ns=sources.now_ns, permissions=sources.permissions),
            BudgetUpdate(now_ns=sources.now_ns, budget=sources.budget),
        ]
        if sources.books != self._state.books:
            events.insert(2, BookUpdate(now_ns=sources.now_ns, books=sources.books))
        mismatch = self._position_mismatch(sources)
        if mismatch is not None:
            events.append(mismatch)
        events.extend(self._settled_buy_events(sources))
        return events

    def _settled_buy_events(self, sources: LiveSources) -> tuple[BuySettled, ...]:
        """Repeat BuySettled for each active BUY whose venue row has resolved."""
        events: list[BuySettled] = []
        for order in self._state.orders:
            if order.side != "BUY":
                continue
            venue_id = self._core_to_venue.get(order.order_id)
            if venue_id is None or venue_id not in sources.settled_buys:
                continue
            events.append(
                BuySettled(
                    now_ns=sources.now_ns,
                    order_id=order.order_id,
                    matched_qty=sources.settled_buys[venue_id],
                )
            )
        return tuple(events)

    def _position_mismatch(self, sources: LiveSources) -> Recovery | None:
        if self._state.sell_only:
            return None
        if share_qty_matches(self._state.inventory[0].qty, sources.store_yes) and share_qty_matches(
            self._state.inventory[1].qty, sources.store_no
        ):
            return None
        logger.warning(
            "trader core/ledger position mismatch core=%s/%s store=%s/%s",
            self._state.inventory[0].qty,
            self._state.inventory[1].qty,
            sources.store_yes,
            sources.store_no,
        )
        buys = tuple(order.order_id for order in self._state.orders if order.side == "BUY")
        return Recovery(now_ns=sources.now_ns, restored_buy_ids=buys)

    def apply(self, event: InboundEvent) -> Plan:
        prior = {order.order_id: order for order in self._state.orders}
        out = step(state=self._state, policy=self._policy, event=event)
        self._state = out.state
        self._observe_buy_lifetime(prior, event)
        self.next_wake_ns = out.next_wake_ns
        if self._trace is not None:
            self._trace.write_event(
                now_ns=event.now_ns,
                event=event,
                plan=out.plan,
                next_wake_ns=out.next_wake_ns,
                state=out.state,
            )
        return out.plan

    def _observe_buy_lifetime(self, prior: Mapping[str, RestingOrder], event: InboundEvent) -> None:
        """Log a BUY entering gone, and the wait from cancel until the core drops it."""
        current = {order.order_id: order for order in self._state.orders}
        for order_id, before in prior.items():
            if before.side != "BUY":
                continue
            after = current.get(order_id)
            if after is None:
                self._log_buy_removal(before, event)
                continue
            if order_id not in self._buy_cancel_timing:
                self._arm_buy_cancel(before, after, event)
            if before.status != "gone" and after.status == "gone":
                logger.info("trader core buy gone id=%s", order_id)
        for order_id in list(self._buy_cancel_timing):
            if order_id not in current:
                del self._buy_cancel_timing[order_id]

    def _arm_buy_cancel(
        self, before: RestingOrder, after: RestingOrder, event: InboundEvent
    ) -> None:
        entered = not already_canceling(before) and already_canceling(after)
        venue_cancel = isinstance(event, CancelUnsettled) and before.status != "gone"
        if venue_cancel:
            venue_cancel = after.status == "gone"
        if not entered and not venue_cancel:
            return
        self._buy_cancel_timing[before.order_id] = BuyCancelClock(
            order_id=before.order_id, started_ns=event.now_ns
        )

    def _log_buy_removal(self, before: RestingOrder, event: InboundEvent) -> None:
        if isinstance(event, BuySettled):
            cause = "buy_settled"
        elif isinstance(event, Fill) and before.status == "gone":
            cause = "fill"
        else:
            return
        clock = self._buy_cancel_timing.get(before.order_id)
        if clock is None:
            logger.info(
                "trader core buy settled id=%s wait_ms=unavailable origin=restored cause=%s",
                before.order_id,
                cause,
            )
            return
        wait_ms = (event.now_ns - clock.started_ns) // 1_000_000
        logger.info(
            "trader core buy settled id=%s wait_ms=%s cause=%s",
            before.order_id,
            wait_ms,
            cause,
        )

    def detach_trace(self) -> None:
        trace = self._trace
        self._trace = None
        if trace is not None:
            trace.close()

    def drain_apply(self) -> None:
        places = list(self._deferred_plan.places)
        cancels = list(self._deferred_plan.cancels)
        for event in self.drain():
            plan = self.apply(event)
            places.extend(plan.places)
            cancels.extend(plan.cancels)
        self._deferred_plan = DrainedPlan(places=tuple(places), cancels=tuple(cancels))

    def finish_cycle(self, sources: LiveSources) -> None:
        places = list(self._deferred_plan.places)
        cancels = list(self._deferred_plan.cancels)
        self._deferred_plan = DrainedPlan(places=(), cancels=())
        for event in self.sync_inputs(sources):
            plan = self.apply(event)
            places.extend(plan.places)
            cancels.extend(plan.cancels)
        self._forget_unmapped(keep_ids=self._dispatching_ids(places), now_ns=sources.now_ns)
        for event in self.drain():
            plan = self.apply(event)
            places.extend(plan.places)
            cancels.extend(plan.cancels)
        wake_plan = self.apply(Wake(now_ns=sources.now_ns, forced=True))
        places.extend(wake_plan.places)
        cancels.extend(wake_plan.cancels)
        pending = {order.order_id for order in self._state.orders if order.status == "pending"}
        kept: list[PlaceOrder] = []
        for place in places:
            if place.order_id in pending:
                kept.append(place)
                continue
            self._forget_undispatched(place.order_id, sources.now_ns, "canceled_mid_cycle")
        self.stash(replace(wake_plan, cancels=tuple(cancels), places=tuple(kept)))
        for order_id in self._take_undispatched():
            self._forget_undispatched(order_id, sources.now_ns, "sell_dropped")
        self._watch_stale_cancels(sources.now_ns)

    def _dispatching_ids(self, places: list[PlaceOrder]) -> frozenset[str]:
        ids = {place.order_id for place in places}
        if self._planned is not None:
            ids.update(item.order_id for item in self._planned.to_place)
        if self._last_batch is not None:
            ids.update(item.order_id for item in self._last_batch.to_place)
        return frozenset(ids)

    def _forget_unmapped(self, *, keep_ids: frozenset[str], now_ns: int) -> None:
        """Reject orders with no venue id that this cycle will not dispatch.

        CancelAck can mint a SELL inside `drain_apply`. `_deferred_plan` carries
        that place to the next `finish_cycle`, but it lives in memory only: no
        sqlite checkpoint holds it. A restart between the ack and that cycle
        leaves a pending order with no venue mapping. The next Wake then
        `keep`s it, `_venue_cancels` skips it, and `sell_occupied` holds the
        only exit for the rest of the map. In-flight places stay: they are
        pending and listed in `keep_ids` until `note_placed`.
        """
        for order in self._state.orders:
            if order.order_id in self._core_to_venue:
                continue
            if order.status == "pending" and order.order_id in keep_ids:
                continue
            self._forget_undispatched(order.order_id, now_ns, "never_dispatched")

    def _watch_stale_cancels(self, now_ns: int) -> None:
        """Warn once per order whose cancel the venue never proved.

        `_venue_cancels` resends the cancel every cycle, so a pinned `canceling`
        order means the ack never came back. Nothing here retires the order: the
        venue is the only truth about a resting order, and a fabricated ack would
        let a second SELL onto the book. This warns and names the order instead.
        """
        awaiting: set[str] = set()
        stale: list[str] = []
        for order in self._state.orders:
            if not already_canceling(order):
                continue
            awaiting.add(order.order_id)
            since = self._canceling_since.setdefault(order.order_id, now_ns)
            age_s = (now_ns - since) / 1e9
            if age_s < STALE_CANCEL_SECONDS or order.order_id in self._canceling_warned:
                continue
            self._canceling_warned.add(order.order_id)
            stale.append(order.order_id)
            logger.warning(
                "trader core cancel unproven id=%s side=%s age_s=%.0f reason=%s",
                order.order_id,
                order.side,
                age_s,
                order.cancel_reason,
            )
        self._canceling_since = {
            order_id: since
            for order_id, since in self._canceling_since.items()
            if order_id in awaiting
        }
        self._canceling_warned &= awaiting
        self._stale_cancels = (*self._stale_cancels, *stale)

    def take_stale_cancels(self) -> tuple[str, ...]:
        """Orders the watchdog newly flagged, handed over once for alerting."""
        ids = self._stale_cancels
        self._stale_cancels = ()
        return ids

    def _forget_undispatched(self, order_id: str, now_ns: int, reason: str) -> None:
        """Reject an order whose place never reached the venue.

        It holds no venue id, so neither an accept nor a cancel ack can ever retire
        it. Left in state it occupies its rung for the rest of the map, and a SELL
        left this way occupies the only exit slot.
        """
        logger.warning("trader core order undispatched id=%s reason=%s", order_id, reason)
        self.enqueue(OrderRejected(now_ns=now_ns, order_id=order_id, reason=reason))

    def run_cycle(self, sources: LiveSources) -> None:
        self.clear_plan()
        self.drain_apply()
        self.finish_cycle(sources)

    def target_quotes(self, inp: QuoteInputs) -> TargetQuotes:
        planned = self._planned
        quotes = () if planned is None else tuple(item.quote for item in planned.to_place)
        return TargetQuotes(inp.meta.condition_id, inp.regime, quotes)

    def stash(self, plan: Plan) -> None:
        self._cycle += 1
        places: list[PlacedQuote] = []
        dropped: list[str] = []
        for place in plan.places:
            item = self._placed_quote(place)
            if item is None:
                dropped.append(place.order_id)
                continue
            places.append(item)
        # Accumulate: `finish_cycle` stashes before it drains, and a stash from
        # outside a cycle must not lose its withheld ids to the next one.
        self._undispatched = (*self._undispatched, *dropped)
        self._planned = PlannedBatch(
            cycle=self._cycle,
            to_cancel=self._venue_cancels(plan.cancels),
            to_place=tuple(places),
        )
        self.last_block = plan.block_reason

    def _take_undispatched(self) -> tuple[str, ...]:
        """Order ids whose place `_drop_sell` withheld from the last stash."""
        ids = self._undispatched
        self._undispatched = ()
        return ids

    def _placed_quote(self, place: PlaceOrder) -> PlacedQuote | None:
        quote = Quote(
            self.token_id(place.token_index),
            Side.BUY if place.side == "BUY" else Side.SELL,
            place.price,
            place.quantity,
        )
        if self._drop_sell(quote):
            return None
        return PlacedQuote(order_id=place.order_id, quote=quote)

    def _venue_cancels(self, cancels: tuple[CancelOrder, ...]) -> tuple[str, ...]:
        ids: list[str] = []
        seen: set[str] = set()
        for cancel in cancels:
            venue = self._core_to_venue.get(cancel.order_id)
            if venue is None or venue in seen:
                continue
            ids.append(venue)
            seen.add(venue)
        for order in self._state.orders:
            if not already_canceling(order):
                continue
            venue = self._core_to_venue.get(order.order_id)
            if venue is None or venue in seen:
                continue
            ids.append(venue)
            seen.add(venue)
        return tuple(ids)


def _settled_buys(store: WalletStateStore, quoting: LiveCore) -> dict[str, float]:
    """Resolved qty for this session, read only when an active BUY has a venue id."""
    for order in quoting.state.orders:
        if order.side != "BUY" or order.order_id not in quoting._core_to_venue:
            continue
        return resolved_unsettled_buys(store._conn, quoting.session_id)
    return {}


def live_sources(
    *,
    now_ns: int,
    books: BookPair | None,
    clock: GameClock,
    limits: MarketLimits,
    regime: Regime,
    store: WalletStateStore,
    cache: CollateralCache,
    cores: Iterable[LiveCore],
    quoting: LiveCore,
    account_cap_usdc: float,
    sidecar_usable: bool,
    yes: str,
    no: str,
) -> LiveSources:
    yes_size = store.position(yes).size
    no_size = store.position(no).size
    return LiveSources(
        now_ns=now_ns,
        books=books,
        clock=clock,
        limits=limits,
        permissions=permissions_from_quote(
            regime=regime,
            store=store,
            yes=yes,
            no=no,
            sidecar_usable=sidecar_usable,
            held_token=held_token(
                yes=yes,
                no=no,
                yes_size=yes_size,
                no_size=no_size,
                min_size=limits.min_order_size,
            ),
        ),
        budget=budget_from_orders(
            cache=cache,
            cores=cores,
            store=store,
            quoting=quoting,
            account_cap_usdc=account_cap_usdc,
        ),
        store_yes=yes_size,
        store_no=no_size,
        settled_buys=_settled_buys(store, quoting),
    )


def core_books(
    *,
    core: LiveCore,
    yes: OrderBook | None,
    no: OrderBook | None,
    now_ns: int,
    live: bool,
) -> BookPair | None:
    """Book the kernel quotes. Paper's simulated orders are not in the WS book."""
    own_orders = core.state.orders if live else ()
    return books_from_md(yes=yes, no=no, now_ns=now_ns, own_orders=own_orders)


def quote_cycle(
    core: LiveCore,
    inp: QuoteInputs,
    *,
    now_ns: int,
    books: BookPair | None,
    clock: GameClock,
    limits: MarketLimits,
    store: WalletStateStore,
    cache: CollateralCache,
    cores: Iterable[LiveCore],
    account_cap_usdc: float,
) -> TargetQuotes:
    core.clear_plan()
    core.drain_apply()
    sources = live_sources(
        now_ns=now_ns,
        books=books,
        clock=clock,
        limits=limits,
        regime=inp.regime,
        store=store,
        cache=cache,
        cores=cores,
        quoting=core,
        account_cap_usdc=account_cap_usdc,
        sidecar_usable=core.sidecar_usable,
        yes=inp.meta.yes.token_id,
        no=inp.meta.no.token_id,
    )
    core.finish_cycle(sources)
    return core.target_quotes(inp)


def drive(core: LiveCore, inp: QuoteInputs, sources: LiveSources) -> TargetQuotes:
    core.run_cycle(sources)
    return core.target_quotes(inp)


def feed_core(
    core: LiveCore,
    *,
    now_ns: int,
    second: int,
    paused: bool,
    finished: bool,
    signal: RawDeltaSignal | None,
) -> None:
    core.set_clock(GameClock(now_ns=now_ns, game_second=second, paused=paused, game_ended=finished))
    core.enqueue(SignalUpdate(now_ns=now_ns, signal=signal))


def queue_resume_recovery(
    core: LiveCore, *, yes_size: float, no_size: float, min_size: float, now_ns: int
) -> HeldPosition | None:
    core.note_recovery(now_ns=now_ns)
    return held_position(yes_size=yes_size, no_size=no_size, min_size=min_size)


def make_esports_reconcile(cores: Mapping[str, LiveCore]) -> Callable[..., ReconcilePlan]:
    def esports_reconcile(
        targets: TargetQuotes,
        live: list[OpenOrder],
        *,
        tick: float,
        reprice_ticks: int,
        resize_frac: float,
    ) -> ReconcilePlan:
        del tick, reprice_ticks, resize_frac
        core = cores.get(targets.condition_id)
        if core is None:
            return ReconcilePlan(to_cancel=[order.order_id for order in live], to_place=[])
        planned = core.take_plan()
        if planned is None:
            return ReconcilePlan(to_cancel=[order.order_id for order in live], to_place=[])
        return ReconcilePlan(
            to_cancel=list(planned.to_cancel),
            to_place=[item.quote for item in planned.to_place],
        )

    return esports_reconcile


def held_token(
    *, yes: str, no: str, yes_size: float, no_size: float, min_size: float
) -> str | None:
    held = held_position(yes_size=yes_size, no_size=no_size, min_size=min_size)
    if held is None:
        return None
    return yes if held.token_index == 0 else no
