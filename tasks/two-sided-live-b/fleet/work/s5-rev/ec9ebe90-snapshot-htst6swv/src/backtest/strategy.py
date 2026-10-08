# Nautilus 1.226 ships these Cython APIs without static declarations.
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUntypedBaseClass=false

from bisect import bisect_right
from dataclasses import dataclass, replace
from decimal import Decimal

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, LiquiditySide, OrderSide, TimeInForce
from nautilus_trader.model.events import (
    OrderAccepted as VenueAccepted,
)
from nautilus_trader.model.events import (
    OrderCanceled as VenueCanceled,
)
from nautilus_trader.model.events import (
    OrderDenied as VenueDenied,
)
from nautilus_trader.model.events import (
    OrderExpired as VenueExpired,
)
from nautilus_trader.model.events import (
    OrderFilled as VenueFilled,
)
from nautilus_trader.model.events import (
    OrderRejected as VenueRejected,
)
from nautilus_trader.model.events import (
    OrderSubmitted as VenueSubmitted,
)
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from backtest.maker_orders import (
    CANCEL_ALERT_PREFIX,
    CANCEL_SEND_PREFIX,
    EMPTY_QUOTE_CONTEXT,
    TICK_SIZE,
    BuyLevel,
    DetachedOrder,
    LatchedDecision,
    LiveOrder,
    QuoteContext,
    QuoteTarget,
    SubmittedOrder,
    client_id_from_cancel_alert,
    client_id_from_cancel_send,
    is_on_tick_grid,
    level_column,
    remaining_buy_notional,
    remaining_qty,
    round_buy_price,
    round_sell_price,
    volume_at_price,
)
from backtest.signals import MatchSignals
from backtest.telemetry import (
    FillRecord,
    QuoteEvent,
    UptimeRecord,
    record_fill,
    record_quote_event,
    record_uptime,
)
from shared.constants.strategy import (
    BUY_LEVEL_COUNT,
    BUY_LEVEL_STEP_TICKS,
    MIN_ORDER_SIZE,
)
from shared.utils.match_time import NS_PER_SECOND
from strategy.budget import map_budget, reserve_buy_notional
from strategy.engine import step
from strategy.lifecycle import empty_state, is_settling, mark_canceling
from strategy.policy import Follow300Policy
from strategy.scheduling import next_wake_ns
from strategy.signals import is_fresh, token_fair
from strategy.types import (
    BlockReason,
    BookPair,
    BookUpdate,
    Budget,
    BudgetUpdate,
    CancelAck,
    ClockUpdate,
    EngineOutput,
    Fill,
    FreshnessLimits,
    GameClock,
    InboundEvent,
    KillGateUpdate,
    MarketLimits,
    OrderAccepted,
    OrderRejected,
    Permissions,
    PlaceOrder,
    Plan,
    Position,
    RawDeltaSignal,
    Recovery,
    RecoveryVerified,
    Side,
    SignalUpdate,
    StrategyState,
    TokenBook,
    TokenInventory,
    Wake,
    total_qty,
)

MIN_SELL_QTY = Decimal(str(MIN_ORDER_SIZE))
PLACEMENT_JOIN = "join"
_REQUOTE_EVENTS = (Wake, CancelAck, Recovery, RecoveryVerified)


def _executes_plan(event: InboundEvent) -> bool:
    """Wake-like events, plus a pause clock: its cancels never reach a later Wake."""
    if isinstance(event, _REQUOTE_EVENTS):
        return True
    return isinstance(event, ClockUpdate) and event.clock.paused


_STORE_EVENTS = (BookUpdate, SignalUpdate, KillGateUpdate, ClockUpdate, BudgetUpdate)
_HELD_NO_TARGET_REASONS: frozenset[BlockReason] = frozenset(
    {"", "fair", "min_delta", "min_price", "max_price", "wide_spread", "no_cash", "position_cap"}
)

__all__ = (
    "BUY_LEVEL_COUNT",
    "BUY_LEVEL_STEP_TICKS",
    "DotaMakerConfig",
    "DotaMakerStrategy",
    "MatchKernelConfig",
    "ObservedClockTape",
    "round_buy_price",
    "round_sell_price",
)


@dataclass(frozen=True)
class MatchKernelConfig:
    """The kernel config one match runs on: policy plus its market and freshness limits."""

    policy: Follow300Policy
    limits: MarketLimits
    freshness: FreshnessLimits
    max_position_levels: int


@dataclass(frozen=True)
class ObservedClockTape:
    """Observed feed clock for archive-schedule replays; aligned to feed_timestamps_ns."""

    game_seconds: tuple[int, ...]
    paused: tuple[bool, ...]
    terminal: tuple[bool, ...]


class DotaMakerConfig(StrategyConfig, frozen=True):  # type: ignore[call-arg]
    match_id: int
    instrument_ids: tuple[InstrumentId, InstrumentId]
    feed_timestamps_ns: tuple[int, ...]
    signal_timestamps_ns: tuple[int, ...]
    source_timestamps_ns: tuple[int, ...]
    predicted_deltas: tuple[float, ...]
    dataset_market_ps: tuple[float, ...]
    deaths_radiant: tuple[int, ...]
    deaths_dire: tuple[int, ...]
    kill_gates: tuple[KillGateUpdate, ...]
    board_tick_ns: tuple[int, ...]
    observed_clock: ObservedClockTape | None
    horn_ns: int
    buy_cutoff_ns: int
    game_end_ns: int
    order_latency_ns: int
    cancel_latency_ns: int
    kernel: MatchKernelConfig

    def __post_init__(self) -> None:
        if len(self.signal_timestamps_ns) != len(self.source_timestamps_ns):
            raise ValueError("timestamps and source_timestamps_ns must have the same length")
        if len(self.signal_timestamps_ns) != len(self.predicted_deltas):
            raise ValueError("timestamps and predicted_deltas must have the same length")
        if len(self.signal_timestamps_ns) != len(self.dataset_market_ps):
            raise ValueError("timestamps and dataset_market_ps must have the same length")
        if len(self.signal_timestamps_ns) != len(self.deaths_radiant):
            raise ValueError("timestamps and deaths_radiant must have the same length")
        if len(self.signal_timestamps_ns) != len(self.deaths_dire):
            raise ValueError("timestamps and deaths_dire must have the same length")
        tape = self.observed_clock
        if tape is not None and not (
            len(tape.game_seconds)
            == len(tape.paused)
            == len(tape.terminal)
            == len(self.feed_timestamps_ns)
        ):
            raise ValueError("observed_clock tapes must align with feed_timestamps_ns")


class DotaMakerStrategy(Strategy):
    """Nautilus adapter: books/fills/timers in, core `step` out, existing place/cancel path."""

    def __init__(self, config: DotaMakerConfig) -> None:
        super().__init__(config)
        self._config = config
        self._signals = MatchSignals(
            feed_timestamps_ns=config.feed_timestamps_ns,
            timestamps_ns=config.signal_timestamps_ns,
            source_timestamps_ns=config.source_timestamps_ns,
            predicted_deltas=config.predicted_deltas,
            dataset_market_ps=config.dataset_market_ps,
            deaths_radiant=config.deaths_radiant,
            deaths_dire=config.deaths_dire,
            kill_gates=config.kill_gates,
            board_tick_ns=config.board_tick_ns,
        )
        self._books: dict[InstrumentId, OrderBook] = {}
        self._book_ts_ns: dict[InstrumentId, int] = {}
        self._instruments: dict[InstrumentId, Instrument] = {}
        self._position_token_index: int | None = None
        self._position_qty: Decimal = Decimal("0")
        self._live: dict[str, LiveOrder] = {}
        self._submitted: dict[str, SubmittedOrder] = {}
        self._buy_levels: dict[int, BuyLevel] = {}
        self._detached: dict[str, DetachedOrder] = {}
        self._episode_token_index: int | None = None
        self._episode_id: int = 0
        self._episode_counter: int = 0
        self._episode_buy_notional: Decimal = Decimal("0")
        self._episode_sell_proceeds: Decimal = Decimal("0")
        self._episode_buy_fills: int = 0
        self._position_cost_basis: Decimal = Decimal("0")
        self._episode_has_buy_fill: bool = False
        self._winding_down: bool = False
        self._last_buy_ns: int | None = None
        self._live_order_ns: int = 0
        self._live_since_ns: int | None = None
        self._wake_generation: int = 0
        self._armed_wake_ns: int | None = None
        # is_on_tick_grid is pure, so one check per distinct price is enough; the
        # snapshot asks four times per book delta.
        self._seen_prices: set[float] = set()
        self._last_synced_signal_ts: int | None = None
        self._kill_gates: dict[int, KillGateUpdate] = {}
        self._executed_buy_notional: Decimal = Decimal("0")
        self._sell_proceeds: Decimal = Decimal("0")
        self._peak_exposure: Decimal = Decimal("0")
        self._cancel_send_seq: int = 0
        self._core_to_venue: dict[str, str] = {}
        self._venue_to_core: dict[str, str] = {}
        self._policy: Follow300Policy = config.kernel.policy
        self._finished: bool = False
        self._core: StrategyState = empty_state(
            limits=config.kernel.limits,
            freshness=config.kernel.freshness,
            permissions=Permissions(
                halt=False,
                reduce_only=False,
                allow_buy=True,
                allow_sell=True,
                sell_unconfirmed=False,
            ),
            budget=Budget(
                cash_usdc=0.0,
                cap_room_usdc=float("inf"),
                account_cap_room_usdc=float("inf"),
            ),
            clock=GameClock(now_ns=0, game_second=0, paused=False, game_ended=False),
        )

    def on_start(self) -> None:
        """Load both instruments, subscribe books, and arm reprice, cutoff, and game-end alerts."""
        self._cancel_send_seq = 0
        timestamps_ns = self._signals.timestamps_ns
        feed_timestamps_ns = self._signals.feed_timestamps_ns
        if not timestamps_ns:
            raise ValueError("signal timestamps must not be empty")
        if not feed_timestamps_ns:
            raise ValueError("feed timestamps must not be empty")
        for instrument_id in self._config.instrument_ids:
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                raise ValueError(f"instrument {instrument_id} is not loaded")
            self._instruments[instrument_id] = instrument
            self.subscribe_order_book_deltas(instrument_id)

        seen_signal_ts: set[int] = set()
        # Archive board decisions are not feed ticks; they need their own wake.
        for ts_ns in (*self._signals.feed_timestamps_ns, *self._signals.board_tick_ns):
            if ts_ns in seen_signal_ts or ts_ns > self._config.game_end_ns:
                continue
            seen_signal_ts.add(ts_ns)
            self.clock.set_time_alert_ns(
                name=f"SIGNAL:{self._config.match_id}:{ts_ns}",
                alert_time_ns=ts_ns,
                callback=self._on_signal_alert,
            )
        self._kill_gates = {}
        for update in self._signals.kill_gates:
            if update.now_ns > self._config.game_end_ns:
                continue
            self._kill_gates[update.now_ns] = update
            self.clock.set_time_alert_ns(
                name=f"KILL:{self._config.match_id}:{update.now_ns}",
                alert_time_ns=update.now_ns,
                callback=self._on_kill_gate_alert,
            )
        self.clock.set_time_alert_ns(
            name=f"BUY_CUTOFF:{self._config.match_id}",
            alert_time_ns=self._config.buy_cutoff_ns,
            callback=self._on_buy_cutoff,
        )
        self.clock.set_time_alert_ns(
            name=f"GAME_END:{self._config.match_id}",
            alert_time_ns=self._config.game_end_ns,
            callback=self._on_game_end,
        )

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """Apply deltas, stamp freshness, and open the core coalescing window."""
        if self._finished:
            return
        instrument_id = deltas.instrument_id
        book = self._books.get(instrument_id)
        if book is None:
            book = OrderBook(instrument_id, book_type=BookType.L2_MBP)
            self._books[instrument_id] = book
        book.apply_deltas(deltas)
        now_ns = int(deltas.ts_event)
        self._book_ts_ns[instrument_id] = now_ns
        out = self._drive(
            event=BookUpdate(now_ns=now_ns, books=self._snapshot_books()), execute=False
        )
        self._arm_wake(out.next_wake_ns)

    def on_order_submitted(self, event: VenueSubmitted) -> None:
        """Ignore engine submitted events; submit is recorded at decision time."""
        del event

    def on_order_accepted(self, event: VenueAccepted) -> None:
        """Record venue acceptance; cancel if the BUY window or game already ended during insert."""
        now_ns = int(event.ts_event)
        client_order_id = str(event.client_order_id)
        live = self._live.get(client_order_id)
        if live is not None:
            live.accepted = True
        self._record_order_lifecycle(
            kind="accepted", event_ts_ns=now_ns, reason="", client_order_id=client_order_id
        )
        core_id = self._venue_to_core.get(client_order_id)
        if core_id is not None:
            self._drive(event=OrderAccepted(now_ns=now_ns, order_id=core_id))
        if live is None:
            return
        if now_ns >= self._config.game_end_ns:
            self._mark_and_cancel(live, reason="game_end")
        elif now_ns >= self._config.buy_cutoff_ns and live.order.side == OrderSide.BUY:
            self._mark_and_cancel(live, reason="cutoff")
        self._rearm_cancel_after_accept(live)

    def on_order_filled(self, event: VenueFilled) -> None:
        """Credit the core, then telemetry. Flattened SELL cancels leftover BUY at fill ts."""
        token_index = self._token_index_of(event.instrument_id)
        fill_qty = Decimal(str(event.last_qty))
        fill_price = float(event.last_px)
        client_order_id = str(event.client_order_id)
        submitted = self._submitted.get(client_order_id)
        if submitted is None:
            raise ValueError(
                f"fill on {event.client_order_id} with no recorded submit size; "
                "every fill must belong to an order this strategy submitted"
            )
        core_id = self._venue_to_core.get(client_order_id)
        if core_id is None:
            raise ValueError(f"fill on {client_order_id} has no core order id")
        live = self._live.get(client_order_id)
        assignment = self._assignment_of(client_order_id, live)
        now_ns = int(event.ts_event)
        side: Side = "BUY" if event.is_buy else "SELL"
        out = self._drive(
            event=Fill(
                now_ns=now_ns,
                fill_id=f"{client_order_id}:{now_ns}:{fill_qty}",
                order_id=core_id,
                qty=float(fill_qty),
                price=fill_price,
                token_index=token_index,
                side=side,
            )
        )
        # Fill dirties the schedule but is not a requote event; arm the debounce
        # wake so we cancel/reprice like live instead of waiting for the next book.
        self._arm_wake(out.next_wake_ns)
        self._credit_venue_fill(client_order_id, live, fill_qty)
        if event.is_buy:
            self._note_buy_fill(fill_qty, fill_price, assignment.episode_id)
        else:
            self._note_sell_fill(fill_qty, fill_price, assignment.episode_id)
        if self._core.winding_down and self._core.position.qty == 0:
            self._cancel_live_buys(reason="episode_end")
        self._touch_peak_exposure()
        self._record_fill_row(
            client_order_id=client_order_id,
            token_index=token_index,
            is_buy=bool(event.is_buy),
            fill_price=fill_price,
            fill_qty=fill_qty,
            submitted=submitted,
            assignment=assignment,
            ts_ns=now_ns,
            is_maker=getattr(event, "liquidity_side", LiquiditySide.MAKER) == LiquiditySide.MAKER,
        )

    def on_order_canceled(self, event: VenueCanceled) -> None:
        """Ack in the core, drop venue live, then requote on the same visit."""
        client_order_id = str(event.client_order_id)
        live = self._live.get(client_order_id)
        self._record_order_lifecycle(
            kind="cancel_ack",
            event_ts_ns=int(event.ts_event),
            reason=live.cancel_reason if live is not None else "",
            client_order_id=client_order_id,
        )
        now_ns = int(event.ts_event)
        core_id = self._venue_to_core.get(client_order_id)
        if core_id is None:
            self._detach_live(client_order_id)
            return
        self._sync_inputs(now_ns)
        out = self._drive(event=CancelAck(now_ns=now_ns, order_id=core_id), execute=False)
        self._detach_live(client_order_id)
        self._execute_plan(out.plan, now_ns=now_ns)
        self._record_block_if_idle(now_ns=now_ns, plan=out.plan)

    def on_order_rejected(self, event: VenueRejected) -> None:
        """Clear live state after a post_only rejection and keep quoting on later ticks."""
        self._drop_dead_order(
            client_order_id=str(event.client_order_id),
            kind="rejected",
            event_ts_ns=int(event.ts_event),
            reason=str(event.reason),
        )

    def on_order_denied(self, event: VenueDenied) -> None:
        """Clear live state when Nautilus denies the order before venue submit."""
        self._drop_dead_order(
            client_order_id=str(event.client_order_id),
            kind="denied",
            event_ts_ns=int(event.ts_event),
            reason=str(event.reason),
        )

    def on_order_expired(self, event: VenueExpired) -> None:
        """Clear live state when the resting order expires."""
        self._drop_dead_order(
            client_order_id=str(event.client_order_id),
            kind="expired",
            event_ts_ns=int(event.ts_event),
            reason="",
        )

    def _on_core_wake(self, event: TimeEvent) -> None:
        if self._finished:
            return
        name = event.name
        marker = name.rsplit(":", 1)[-1]
        if marker.isdigit() and int(marker) != self._wake_generation:
            return
        self._evaluate(now_ns=int(event.ts_event))

    def _on_signal_alert(self, event: TimeEvent) -> None:
        if self._finished:
            return
        now_ns = int(event.ts_event)
        self._sync_signal(now_ns)
        self._arm_wake(next_wake_ns(state=self._core, policy=self._policy, now_ns=now_ns))

    def _on_kill_gate_alert(self, event: TimeEvent) -> None:
        if self._finished:
            return
        update = self._kill_gates.get(int(event.ts_event))
        if update is None:
            return
        out = self._drive(event=update)
        self._arm_wake(out.next_wake_ns)

    def _arm_wake(self, next_wake_ns: int) -> None:
        if next_wake_ns >= self._config.game_end_ns:
            return
        if next_wake_ns == self._armed_wake_ns:
            return
        self._armed_wake_ns = next_wake_ns
        self._wake_generation += 1
        self.clock.set_time_alert_ns(
            name=f"CORE_WAKE:{self._config.match_id}:{self._wake_generation}",
            alert_time_ns=next_wake_ns,
            callback=self._on_core_wake,
        )

    def seed_inventory(self, *, token_index: int, qty: float, last_buy_ns: int | None) -> None:
        tokens = list(self._core.inventory)
        tokens[token_index] = TokenInventory(
            token_index=token_index,
            qty=qty,
            cost_basis=qty * 0.50,
            last_buy_ns=last_buy_ns,
        )
        self._core = replace(self._core, inventory=(tokens[0], tokens[1]))
        self._position_qty = Decimal(str(qty))
        self._position_token_index = token_index if qty > 0 else None
        self._last_buy_ns = last_buy_ns
        self._position_cost_basis = Decimal(str(qty * 0.50))

    def _touch_uptime(self, now_ns: int) -> None:
        if self._live_since_ns is not None:
            self._live_order_ns += now_ns - self._live_since_ns
            self._live_since_ns = None
        if self._live:
            self._live_since_ns = now_ns

    def _on_buy_cutoff(self, event: TimeEvent) -> None:
        now_ns = int(event.ts_event)
        self._drive(event=ClockUpdate(now_ns=now_ns, clock=self._clock_at(now_ns)))
        self._cancel_live_buys(reason="cutoff")

    def _on_game_end(self, event: TimeEvent) -> None:
        now_ns = int(event.ts_event)
        self._drive(event=ClockUpdate(now_ns=now_ns, clock=self._clock_at(now_ns)))
        record_uptime(
            UptimeRecord(
                match_id=self._config.match_id,
                live_order_seconds=int(self._live_order_ns / NS_PER_SECOND),
            )
        )
        for live in list(self._live.values()):
            self._mark_and_cancel(live, reason="game_end")

    def _on_cancel_release(self, event: TimeEvent) -> None:
        now_ns = int(event.ts_event)
        client_order_id = client_id_from_cancel_alert(event.name)
        live = self._live.get(client_order_id)
        if live is None:
            return
        if self._inventory_is_dust(live) and live.cancel_reason == "reprice":
            live.awaiting_cancel = False
            live.cancel_released = False
            live.cancel_reason = ""
            if now_ns >= self._config.game_end_ns:
                self._schedule_cancel(live, reason="game_end")
                return
            if now_ns >= self._config.buy_cutoff_ns and live.order.side == OrderSide.BUY:
                self._schedule_cancel(live, reason="cutoff")
            return
        if not live.accepted:
            live.cancel_released = True
            return
        reason = live.cancel_reason
        live.cancel_released = True
        self._cancel_send_seq += 1
        self.clock.set_time_alert_ns(
            f"{CANCEL_SEND_PREFIX}{client_order_id}",
            now_ns + self._cancel_send_seq,
            callback=self._on_cancel_send,
        )
        submitted = self._submitted.get(client_order_id)
        context = submitted.context if submitted is not None else EMPTY_QUOTE_CONTEXT
        record_quote_event(
            QuoteEvent(
                match_id=self._config.match_id,
                ts_ns=now_ns,
                kind="canceled",
                token_index=self._token_index_of(live.order.instrument_id),
                side=live.order.side.name,
                price=float(live.order.price),
                reason=reason,
                predicted_delta=context.predicted_delta,
                fair=context.fair,
                book_p_radiant=context.book_p_radiant,
                spread=context.spread,
                episode_id=self._episode_id_of(client_order_id),
                order_id=client_order_id,
                quantity=float(remaining_qty(live)),
                level_index=level_column(live.level_index),
                submit_level_index=level_column(
                    submitted.level_index if submitted is not None else None
                ),
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
            )
        )

    def _on_cancel_send(self, event: TimeEvent) -> None:
        client_order_id = client_id_from_cancel_send(event.name)
        live = self._live.get(client_order_id)
        if live is None:
            return
        if self._order_already_gone(live) or remaining_qty(live) <= 0:
            self._ack_gone_cancel(
                now_ns=int(event.ts_event), client_order_id=client_order_id, live=live
            )
            return
        self.cancel_order(live.order)

    def begin_recovery(self, *, now_ns: int) -> None:
        self._sync_inputs(now_ns)
        buys = tuple(order.order_id for order in self._core.orders if order.side == "BUY")
        self._drive(event=Recovery(now_ns=now_ns, restored_buy_ids=buys))

    def accept_recovery_verified(
        self,
        *,
        now_ns: int,
        generation: int,
        position: Position,
        last_buy_ns: int | None,
    ) -> None:
        self._sync_inputs(now_ns)
        tokens = [
            TokenInventory(token_index=0, qty=0.0, cost_basis=0.0, last_buy_ns=None),
            TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
        ]
        if position.token_index is not None:
            tokens[position.token_index] = TokenInventory(
                token_index=position.token_index,
                qty=position.qty,
                cost_basis=position.cost_basis,
                last_buy_ns=last_buy_ns,
            )
        self._drive(
            event=RecoveryVerified(
                now_ns=now_ns,
                generation=generation,
                inventory=(tokens[0], tokens[1]),
            )
        )

    def _evaluate(self, *, now_ns: int) -> None:
        if now_ns >= self._config.game_end_ns:
            return
        self._sync_inputs(now_ns)
        out = self._drive(event=Wake(now_ns=now_ns, forced=True))
        self._arm_wake(out.next_wake_ns)
        self._record_block_if_idle(now_ns=now_ns, plan=out.plan)
        self._mark_trading_done()

    def _mark_trading_done(self) -> None:
        """Past the core's BUY cutoff, flat on both tokens, nothing resting: no order can follow."""
        state = self._core
        if (
            state.clock.game_second >= self._policy.buy_cutoff_second
            and total_qty(state.inventory) == 0
            and not state.orders
            and not self._live
        ):
            self._finished = True

    def _drive(self, *, event: InboundEvent, execute: bool = True) -> EngineOutput:
        out = step(state=self._core, policy=self._policy, event=event)
        self._core = out.state
        if execute and _executes_plan(event):
            self._execute_plan(out.plan, now_ns=event.now_ns)
        if not isinstance(event, _STORE_EVENTS):
            self._shim_from_core()
        self._touch_uptime(event.now_ns)
        return out

    def _sync_inputs(self, now_ns: int) -> None:
        self._drive(event=ClockUpdate(now_ns=now_ns, clock=self._clock_at(now_ns)))
        books = self._snapshot_books()
        if books != self._core.books:
            self._drive(event=BookUpdate(now_ns=now_ns, books=books))
        self._sync_signal(now_ns)
        self._drive(event=BudgetUpdate(now_ns=now_ns, budget=self._budget()))

    def _execute_plan(self, plan: Plan, *, now_ns: int) -> None:
        for keep in plan.keep:
            live = self._live_for_core(keep.order_id)
            if live is None:
                continue
            live.level_index = keep.level_index
        for move in plan.moves:
            live = self._live_for_core(move.order_id)
            if live is None:
                continue
            live.level_index = move.to_level
            live.level_moves += 1
        for cancel in plan.cancels:
            live = self._live_for_core(cancel.order_id)
            if self._order_already_gone(live):
                self._retire_gone_cancel(now_ns=now_ns, order_id=cancel.order_id, live=live)
                continue
            assert live is not None
            self._request_cancel(live, reason=cancel.reason)
        for place in plan.places:
            self._submit_place(now_ns=now_ns, place=place)

    def _submit_place(self, *, now_ns: int, place: PlaceOrder) -> None:
        instrument_id = self._config.instrument_ids[place.token_index]
        instrument = self._instruments[instrument_id]
        qty = Decimal(str(place.quantity))
        order_side = OrderSide.BUY if place.side == "BUY" else OrderSide.SELL
        context = self._quote_context(
            token_index=place.token_index, price=place.price, is_buy=place.side == "BUY"
        )
        order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=order_side,
            quantity=instrument.make_qty(qty),
            price=instrument.make_price(place.price),
            time_in_force=TimeInForce.GTC,
            post_only=True,
            reduce_only=place.reduce_only,
        )
        client_order_id = str(order.client_order_id)
        live = LiveOrder(order=order, level_index=place.level_index)
        self._live[client_order_id] = live
        self._submitted[client_order_id] = SubmittedOrder(
            quantity=float(qty),
            context=context,
            token_index=place.token_index,
            side=order_side,
            price=place.price,
            level_index=place.level_index,
            episode_id=place.episode_id,
        )
        self._core_to_venue[place.order_id] = client_order_id
        self._venue_to_core[client_order_id] = place.order_id
        if place.level_index is not None:
            level = self._buy_levels.get(place.level_index)
            if level is not None:
                level.live_id = client_order_id
                level.price = place.price
        self._touch_peak_exposure()
        self.log.info(
            f"submitting {place.side} join token={place.token_index} "
            f"px={place.price:.4f} fair={context.fair:.4f}"
        )
        try:
            self.submit_order(order)
        except Exception:
            self._detach_live(client_order_id)
            self._submitted.pop(client_order_id, None)
            self._core_to_venue.pop(place.order_id, None)
            self._venue_to_core.pop(client_order_id, None)
            self._drive(
                event=OrderRejected(now_ns=now_ns, order_id=place.order_id, reason="submit")
            )
            raise
        self.clock.set_time_alert_ns(
            f"RELEASE:{order.client_order_id}",
            self.clock.timestamp_ns() + self._config.order_latency_ns,
        )
        record_quote_event(
            QuoteEvent(
                match_id=self._config.match_id,
                ts_ns=now_ns,
                kind="submitted",
                token_index=place.token_index,
                side=place.side,
                price=place.price,
                reason="",
                predicted_delta=context.predicted_delta,
                fair=context.fair,
                book_p_radiant=context.book_p_radiant,
                spread=context.spread,
                episode_id=place.episode_id,
                order_id=client_order_id,
                quantity=float(qty),
                level_index=level_column(place.level_index),
                submit_level_index=level_column(place.level_index),
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
            )
        )

    def _inventory_is_dust(self, live: LiveOrder) -> bool:
        return live.partially_filled and self._position_qty < MIN_SELL_QTY

    def _cancel_live_buys(self, *, reason: str) -> None:
        for live in list(self._live.values()):
            if live.order.side == OrderSide.BUY:
                self._mark_and_cancel(live, reason=reason)

    def _mark_and_cancel(self, live: LiveOrder, *, reason: str) -> None:
        core_id = self._venue_to_core.get(str(live.order.client_order_id))
        if core_id is not None:
            self._core = mark_canceling(state=self._core, order_id=core_id, reason=reason)
        self._request_cancel(live, reason=reason)

    def _rearm_cancel_after_accept(self, live: LiveOrder) -> None:
        if live.awaiting_cancel and live.cancel_released:
            self._schedule_cancel(live, reason=live.cancel_reason)

    def _venue_cancel_reason(self, reason: str, *, now_ns: int) -> str:
        if reason not in _HELD_NO_TARGET_REASONS:
            return reason
        qty = self._core.position.qty
        if qty == 0:
            return reason
        if qty >= MIN_ORDER_SIZE and is_settling(
            state=self._core,
            policy=self._policy,
            now_ns=now_ns,
            token_index=self._core.position.token_index,
        ):
            return "exit_settle"
        if qty >= MIN_ORDER_SIZE:
            return "fair"
        return "no_quote"

    def _request_cancel(self, live: LiveOrder, *, reason: str) -> None:
        reason = self._venue_cancel_reason(reason, now_ns=self._core.clock.now_ns)
        if live.awaiting_cancel:
            live.cancel_reason = reason
            if live.cancel_released and not live.accepted:
                self._schedule_cancel(live, reason=reason)
            return
        self._schedule_cancel(live, reason=reason)

    def _schedule_cancel(self, live: LiveOrder, *, reason: str) -> None:
        live.awaiting_cancel = True
        live.cancel_released = False
        live.cancel_reason = reason
        client_order_id = str(live.order.client_order_id)
        self._record_order_lifecycle(
            kind="cancel_request",
            event_ts_ns=self.clock.timestamp_ns(),
            reason=reason,
            client_order_id=client_order_id,
        )
        self.clock.set_time_alert_ns(
            f"{CANCEL_ALERT_PREFIX}{client_order_id}",
            self.clock.timestamp_ns() + self._config.cancel_latency_ns,
            callback=self._on_cancel_release,
        )

    def _detach_live(self, client_order_id: str) -> None:
        live = self._live.pop(client_order_id, None)
        if live is None:
            return
        self._detached[client_order_id] = DetachedOrder(
            level_index=live.level_index,
            level_moves=live.level_moves,
            episode_id=self._episode_id_of(client_order_id),
        )
        if live.level_index is not None:
            level = self._buy_levels.get(live.level_index)
            if level is not None and level.live_id == client_order_id:
                level.live_id = None
        self._touch_uptime(int(self.clock.timestamp_ns()))

    def _touch_peak_exposure(self) -> None:
        exposure = self._executed_buy_notional + remaining_buy_notional(self._live)
        if exposure > self._peak_exposure:
            self._peak_exposure = exposure

    def _drop_dead_order(
        self, *, client_order_id: str, kind: str, event_ts_ns: int, reason: str
    ) -> None:
        self._record_order_lifecycle(
            kind=kind, event_ts_ns=event_ts_ns, reason=reason, client_order_id=client_order_id
        )
        core_id = self._venue_to_core.get(client_order_id)
        if core_id is not None:
            self._drive(event=OrderRejected(now_ns=event_ts_ns, order_id=core_id, reason=reason))
        self._detach_live(client_order_id)

    def _latest_ts(self, timestamps_ns: tuple[int, ...], now_ns: int) -> int | None:
        index = bisect_right(timestamps_ns, now_ns) - 1
        if index < 0:
            return None
        return timestamps_ns[index]

    def _model_index_at(self, ts_ns: int) -> int | None:
        index = bisect_right(self._signals.timestamps_ns, ts_ns) - 1
        if index < 0 or self._signals.timestamps_ns[index] != ts_ns:
            return None
        return index

    def _fair_at(self, *, token_index: int, now_ns: int) -> float:
        latch = self._core.latch
        if latch is None or not latch.fair_valid:
            return float("nan")
        if not is_fresh(
            now_ns=now_ns, ts_ns=latch.fair_ts_ns, max_age_s=self._core.freshness.exit_stale_s
        ):
            return float("nan")
        return token_fair(
            fair_radiant=latch.fair_radiant,
            token_index=token_index,
            radiant_token_index=self._config.kernel.limits.radiant_token_index,
        )

    def _signal_age_seconds(self, now_ns: int) -> float:
        feed_ts = self._latest_ts(self._signals.feed_timestamps_ns, now_ns)
        if feed_ts is None:
            return float("nan")
        return (now_ns - feed_ts) / NS_PER_SECOND

    def _gate_reason_now(self) -> str:
        latch = self._core.latch
        if latch is None:
            return "stale_signal"
        return latch.no_buy_reason

    def _token_index_of(self, instrument_id: InstrumentId) -> int:
        for token_index, configured_id in enumerate(self._config.instrument_ids):
            if configured_id == instrument_id:
                return token_index
        raise ValueError(f"instrument {instrument_id} is not part of this strategy")

    def _warn_off_grid(self, *, token_index: int, side: str, price: float) -> None:
        if price in self._seen_prices:
            return
        self._seen_prices.add(price)
        if is_on_tick_grid(price):
            return
        self.log.warning(
            f"off-grid {side} token={token_index} price={price}; rounding to {TICK_SIZE} grid"
        )

    def _record_no_quote(self, *, now_ns: int, reason: str, context: QuoteContext) -> None:
        record_quote_event(
            QuoteEvent(
                match_id=self._config.match_id,
                ts_ns=now_ns,
                kind="no_quote",
                token_index=-1,
                side="",
                price=0.0,
                reason=reason,
                predicted_delta=context.predicted_delta,
                fair=context.fair,
                book_p_radiant=context.book_p_radiant,
                spread=context.spread,
                episode_id=self._episode_id,
                order_id="",
                quantity=0.0,
                level_index=-1,
                submit_level_index=-1,
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
            )
        )

    def _record_order_lifecycle(
        self, *, kind: str, event_ts_ns: int, reason: str, client_order_id: str
    ) -> None:
        live = self._live.get(client_order_id)
        submitted = self._submitted.get(client_order_id)
        assignment = self._assignment_of(client_order_id, live) if submitted is not None else None
        token_index = -1
        side = ""
        price = 0.0
        quantity = 0.0
        if live is not None:
            token_index = self._token_index_of(live.order.instrument_id)
            side = live.order.side.name
            price = float(live.order.price)
            quantity = float(remaining_qty(live))
        elif submitted is not None:
            token_index = submitted.token_index
            side = submitted.side.name
            price = submitted.price
        context = submitted.context if submitted is not None else EMPTY_QUOTE_CONTEXT
        record_quote_event(
            QuoteEvent(
                match_id=self._config.match_id,
                ts_ns=event_ts_ns,
                kind=kind,
                token_index=token_index,
                side=side,
                price=price,
                reason=reason,
                predicted_delta=context.predicted_delta,
                fair=context.fair,
                book_p_radiant=context.book_p_radiant,
                spread=context.spread,
                episode_id=assignment.episode_id if assignment is not None else self._episode_id,
                order_id=client_order_id,
                quantity=quantity,
                level_index=level_column(assignment.level_index if assignment else None),
                submit_level_index=level_column(
                    submitted.level_index if submitted is not None else None
                ),
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
            )
        )

    @property
    def _latched(self) -> LatchedDecision | None:
        latch = self._core.latch
        if latch is None:
            return None
        if latch.no_buy_reason:
            buy_targets: tuple[QuoteTarget, ...] = ()
        else:
            token_index = (
                0 if self._core.episode_token_index is None else self._core.episode_token_index
            )
            buy_targets = tuple(
                QuoteTarget(
                    token_index=token_index,
                    side=OrderSide.BUY,
                    price=rung.price,
                    level_index=rung.index,
                    context=EMPTY_QUOTE_CONTEXT,
                )
                for rung in self._core.rungs
                if not rung.done and rung.price > 0
            )
        return LatchedDecision(
            fair_radiant=latch.fair_radiant,
            predicted_delta=latch.predicted_delta,
            dataset_market_p=latch.anchor_p,
            book_p_radiant=latch.book_p_radiant,
            buy_targets=buy_targets,
            no_buy_reason=latch.no_buy_reason,
            fair_valid=latch.fair_valid,
            fair_ts_ns=latch.fair_ts_ns,
        )

    def _assignment_of(self, client_order_id: str, live: LiveOrder | None) -> DetachedOrder:
        if live is not None:
            return DetachedOrder(
                level_index=live.level_index,
                level_moves=live.level_moves,
                episode_id=self._episode_id_of(client_order_id),
            )
        detached = self._detached.get(client_order_id)
        if detached is not None:
            return detached
        submitted = self._submitted[client_order_id]
        return DetachedOrder(
            level_index=submitted.level_index, level_moves=0, episode_id=submitted.episode_id
        )

    def _episode_id_of(self, client_order_id: str) -> int:
        submitted = self._submitted.get(client_order_id)
        return submitted.episode_id if submitted is not None else self._episode_id

    def _clock_at(self, now_ns: int) -> GameClock:
        tape = self._config.observed_clock
        if tape is None:
            return GameClock(
                now_ns=now_ns,
                game_second=(
                    self._policy.buy_cutoff_second if now_ns >= self._config.buy_cutoff_ns else 0
                ),
                paused=False,
                game_ended=now_ns >= self._config.game_end_ns,
            )
        index = bisect_right(self._config.feed_timestamps_ns, now_ns) - 1
        if index < 0:
            return GameClock(now_ns=now_ns, game_second=0, paused=False, game_ended=False)
        return GameClock(
            now_ns=now_ns,
            game_second=tape.game_seconds[index],
            paused=tape.paused[index],
            game_ended=tape.terminal[index] or now_ns >= self._config.game_end_ns,
        )

    def _snapshot_books(self) -> BookPair | None:
        tokens: list[TokenBook] = []
        for token_index, instrument_id in enumerate(self._config.instrument_ids):
            book = self._books.get(instrument_id)
            ts_ns = self._book_ts_ns.get(instrument_id)
            if book is None or ts_ns is None:
                return None
            bid = book.best_bid_price()
            ask = book.best_ask_price()
            if bid is None or ask is None:
                return None
            bid_f = float(bid)
            ask_f = float(ask)
            self._warn_off_grid(token_index=token_index, side="bid", price=bid_f)
            self._warn_off_grid(token_index=token_index, side="ask", price=ask_f)
            bid_size = book.best_bid_size()
            ask_size = book.best_ask_size()
            tokens.append(
                TokenBook(
                    token_index=token_index,
                    bid=bid_f,
                    ask=ask_f,
                    bid_size=float(bid_size or 0.0),
                    ask_size=float(ask_size or 0.0),
                    ts_ns=ts_ns,
                )
            )
        if len(tokens) != 2:
            return None
        return BookPair(tokens=(tokens[0], tokens[1]))

    def _budget(self) -> Budget:
        instrument_id = self._config.instrument_ids[0]
        instrument = self._instruments[instrument_id]
        account = self.portfolio.account(instrument.id.venue)
        if account is None:
            raise ValueError(f"account for venue {instrument.id.venue} is not loaded")
        free_balance = account.balance_free(instrument.quote_currency)
        if free_balance is None:
            raise ValueError(f"free balance for {instrument.quote_currency} is unavailable")
        held_cost = sum(token.cost_basis for token in self._core.inventory)
        return map_budget(
            cash_usdc=float(free_balance.as_decimal()),
            level_usdc=self._policy.level_usdc,
            max_position_levels=self._config.kernel.max_position_levels,
            held_cost=held_cost,
            reserved_usdc=reserve_buy_notional(self._core),
            account_cap_room_usdc=float("inf"),
        )

    def _latest_signal_ts(self, now_ns: int) -> int | None:
        """Newest feed tick or board decision at or before now."""
        candidates = [
            ts
            for ts in (
                self._latest_ts(self._signals.feed_timestamps_ns, now_ns),
                self._latest_ts(self._signals.board_tick_ns, now_ns),
            )
            if ts is not None
        ]
        return max(candidates) if candidates else None

    def _sync_signal(self, now_ns: int) -> None:
        signal_ts = self._latest_signal_ts(now_ns)
        if signal_ts is None:
            self._clear_signal(now_ns)
            return
        model_index = self._model_index_at(signal_ts)
        if model_index is None:
            self._clear_signal(now_ns)
            self._last_synced_signal_ts = signal_ts
            return
        if self._last_synced_signal_ts == signal_ts:
            return
        source_ns = self._signals.source_timestamps_ns[model_index]
        self._drive(
            event=SignalUpdate(
                now_ns=now_ns,
                signal=RawDeltaSignal(
                    predicted_delta=self._signals.predicted_deltas[model_index],
                    source_received_ns=source_ns,
                    received_ns=signal_ts,
                    anchor_p=self._signals.dataset_market_ps[model_index],
                    deaths_radiant=self._signals.deaths_radiant[model_index],
                    deaths_dire=self._signals.deaths_dire[model_index],
                ),
            )
        )
        self._last_synced_signal_ts = signal_ts

    def _clear_signal(self, now_ns: int) -> None:
        if self._core.signal is None:
            return
        self._drive(event=SignalUpdate(now_ns=now_ns, signal=None))

    def _quote_context(self, *, token_index: int, price: float, is_buy: bool) -> QuoteContext:
        latch = self._core.latch
        predicted = 0.0 if latch is None else latch.predicted_delta
        book_p = 0.0 if latch is None else latch.book_p_radiant
        anchor = 0.0 if latch is None else latch.anchor_p
        fair = 0.0
        if latch is not None:
            fair = token_fair(
                fair_radiant=latch.fair_radiant,
                token_index=token_index,
                radiant_token_index=self._config.kernel.limits.radiant_token_index,
            )
        spread = 0.0
        depth = 0.0
        instrument_id = self._config.instrument_ids[token_index]
        book = self._books.get(instrument_id)
        if book is not None:
            bid = book.best_bid_price()
            ask = book.best_ask_price()
            if bid is not None and ask is not None:
                spread = float(ask) - float(bid)
            depth = volume_at_price(book, price, is_buy=is_buy)
        return QuoteContext(
            predicted_delta=predicted,
            fair=fair,
            book_p_radiant=book_p,
            dataset_market_p=anchor,
            spread=spread,
            queue_ahead=depth,
        )

    def _latch_quote_context(self) -> QuoteContext:
        latch = self._core.latch
        if latch is None:
            return EMPTY_QUOTE_CONTEXT
        return QuoteContext(
            predicted_delta=latch.predicted_delta,
            fair=latch.fair_radiant,
            book_p_radiant=latch.book_p_radiant,
            dataset_market_p=latch.anchor_p,
            spread=0.0,
            queue_ahead=0.0,
        )

    def _record_block_if_idle(self, *, now_ns: int, plan: Plan) -> None:
        if plan.places or not plan.block_reason:
            return
        if plan.block_reason == "cutoff" and self._core.position.qty == 0:
            return
        reason = self._venue_cancel_reason(plan.block_reason, now_ns=now_ns)
        self._record_no_quote(now_ns=now_ns, reason=reason, context=self._latch_quote_context())

    def _live_for_core(self, core_id: str) -> LiveOrder | None:
        venue_id = self._core_to_venue.get(core_id)
        if venue_id is None:
            return None
        return self._live.get(venue_id)

    def _order_already_gone(self, live: LiveOrder | None) -> bool:
        if live is None:
            return True
        return bool(getattr(live.order, "is_closed", False))

    def _retire_gone_cancel(self, *, now_ns: int, order_id: str, live: LiveOrder | None) -> None:
        if live is not None:
            self._detach_live(str(live.order.client_order_id))
        self._drive(event=OrderRejected(now_ns=now_ns, order_id=order_id, reason="missing_live"))

    def _ack_gone_cancel(self, *, now_ns: int, client_order_id: str, live: LiveOrder) -> None:
        """Nothing left for the venue to cancel, so no OrderCanceled arrives; finish it here."""
        self._record_order_lifecycle(
            kind="cancel_ack",
            event_ts_ns=now_ns,
            reason=live.cancel_reason,
            client_order_id=client_order_id,
        )
        core_id = self._venue_to_core.get(client_order_id)
        if core_id is None:
            self._detach_live(client_order_id)
            return
        self._sync_inputs(now_ns)
        out = self._drive(event=CancelAck(now_ns=now_ns, order_id=core_id), execute=False)
        self._detach_live(client_order_id)
        self._execute_plan(out.plan, now_ns=now_ns)
        self._record_block_if_idle(now_ns=now_ns, plan=out.plan)

    def _credit_venue_fill(
        self, client_order_id: str, live: LiveOrder | None, fill_qty: Decimal
    ) -> None:
        if live is None:
            return
        live.filled_qty += fill_qty
        if live.filled_qty < Decimal(str(live.order.quantity)):
            live.partially_filled = True
            return
        self._detach_live(client_order_id)

    def _note_buy_fill(self, fill_qty: Decimal, fill_price: float, episode_id: int) -> None:
        notional = fill_qty * Decimal(str(fill_price))
        self._executed_buy_notional += notional
        if episode_id == 0 or episode_id != self._episode_id:
            return
        self._episode_buy_notional += notional
        self._episode_buy_fills += 1

    def _note_sell_fill(self, fill_qty: Decimal, fill_price: float, episode_id: int) -> None:
        proceeds = fill_qty * Decimal(str(fill_price))
        self._sell_proceeds += proceeds
        if episode_id == 0 or episode_id != self._episode_id:
            return
        self._episode_sell_proceeds += proceeds

    def _record_fill_row(
        self,
        *,
        client_order_id: str,
        token_index: int,
        is_buy: bool,
        fill_price: float,
        fill_qty: Decimal,
        submitted: SubmittedOrder,
        assignment: DetachedOrder,
        ts_ns: int,
        is_maker: bool,
    ) -> None:
        record_fill(
            FillRecord(
                match_id=self._config.match_id,
                token_index=token_index,
                side="BUY" if is_buy else "SELL",
                price=fill_price,
                quantity=float(fill_qty),
                submitted_quantity=submitted.quantity,
                ts_ns=ts_ns,
                predicted_delta=submitted.context.predicted_delta,
                fair=submitted.context.fair,
                book_p_radiant=submitted.context.book_p_radiant,
                dataset_market_p=submitted.context.dataset_market_p,
                spread=submitted.context.spread,
                placement=PLACEMENT_JOIN,
                queue_ahead=submitted.context.queue_ahead,
                position_after=float(self._core.inventory[token_index].qty),
                order_id=client_order_id,
                episode_id=assignment.episode_id,
                level_index=level_column(assignment.level_index),
                submit_level_index=level_column(submitted.level_index),
                level_moves=assignment.level_moves,
                fair_at_fill=self._fair_at(token_index=token_index, now_ns=ts_ns),
                signal_age_seconds=self._signal_age_seconds(ts_ns),
                gate_reason_at_fill=self._gate_reason_now(),
                episode_buy_notional=float(self._episode_buy_notional),
                episode_sell_proceeds=float(self._episode_sell_proceeds),
                episode_buy_fill_index=self._episode_buy_fills - 1 if is_buy else -1,
                position_cost_basis=float(self._core.inventory[token_index].cost_basis),
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
                is_maker=is_maker,
            )
        )

    def _shim_from_core(self) -> None:
        state = self._core
        if state.episode_id != self._episode_id:
            self._episode_buy_notional = Decimal("0")
            self._episode_sell_proceeds = Decimal("0")
            self._episode_buy_fills = 0
        self._episode_id = state.episode_id
        self._episode_counter = state.episode_counter
        self._episode_token_index = state.episode_token_index
        self._episode_has_buy_fill = state.has_buy_fill
        self._winding_down = state.winding_down
        self._last_buy_ns = state.last_buy_ns
        self._position_qty = Decimal(str(state.position.qty))
        self._position_token_index = state.position.token_index
        self._position_cost_basis = Decimal(str(state.position.cost_basis))
        self._buy_levels = {
            rung.index: BuyLevel(
                index=rung.index,
                price=rung.price,
                filled_qty=Decimal(str(rung.filled_qty)),
                live_id=self._core_to_venue.get(rung.live_id) if rung.live_id is not None else None,
                done=rung.done,
            )
            for rung in state.rungs
        }
        for order in state.orders:
            if order.status != "canceling":
                continue
            live = self._live_for_core(order.order_id)
            if live is not None:
                live.cancel_reason = self._venue_cancel_reason(
                    order.ack_reason, now_ns=state.clock.now_ns
                )
