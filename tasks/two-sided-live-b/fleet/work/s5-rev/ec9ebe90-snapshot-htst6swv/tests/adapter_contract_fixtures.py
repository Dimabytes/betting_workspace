"""Synthetic tape, drivers, and mismatch reporter for the adapter contract.

TapeSignal and TapeClock are not sequenced events on the backtest adapter.
Signals are preloaded into `build_maker_config` and applied inside `_evaluate`
via `_sync_signal`. Clocks are 0 before cutoff and `policy.buy_cutoff_second` after (`_clock_at`);
TapeClock.second must be one of those two values. Live enqueues SignalUpdate
and ClockUpdate and applies them on the next `quote_cycle`. A tape cannot
express "signal arrived after this wake" as a state change at the TapeSignal
row itself — both sides stay unchanged until the next Wake.

PlaceOk, PlaceMissing, fill, and cancel-timeout apply through `drain_apply`
and do not quote. The next TapeWake is the shared requote tick. Production
wakes on place/fill; the tape keeps those acks and Wake as separate rows.
Recovery and RecoveryVerified still `quote_cycle` on both adapters.
A successful BUY cancel does not: backtest `CancelAck` frees the rung, live
`CancelUnsettled` leaves the order `gone` until `BuySettled`. SELL cancels and
failed BUY cancels still match.
"""

# pyright: reportPrivateUsage=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, Literal, Protocol

import pytest
from nautilus_trader.model.enums import OrderSide
from polymaker.domain import OpenOrder, Position, Side
from polymaker.marketdata.orderbook import BookView
from test_backtest_maker import (
    DIRE_ID,
    RADIANT_ID,
    _FakeBook,
    accept_live,
    build_maker_config,
    build_maker_harness,
    fill_order,
    fire_cancel_release,
)
from test_trader_session_core import _freshness, _inputs, _limits
from trader_session_fixtures import NO_TOKEN, YES_TOKEN

from shared.constants.strategy import LIVE_DOTA_MAX_POSITION_LEVELS
from strategy.policy import Follow300Policy, follow300_policy
from strategy.types import (
    BookPair,
    Budget,
    BudgetUpdate,
    CancelTimeout,
    GameClock,
    OrderRejected,
    RawDeltaSignal,
    RestingOrder,
    SignalUpdate,
    StrategyState,
    SubmitTimeout,
    TokenInventory,
)
from strategy.types import (
    Position as CorePosition,
)
from trader.core_trace import CoreTrace
from trader.session_core import (
    CollateralCache,
    LiveCore,
    PlannedBatch,
    books_from_views,
    quote_cycle,
)
from trader.wallet_store import WalletStateStore

PINNED_LEVEL_USDC = 100.0
DEFAULT_CASH = 1000.0
BOOK_SIZE = 20.0

# Adapter-owned fill id namespaces. Backtest mints `{venue_id}:{now_ns}:{qty}`;
# live uses the ledger trade_id. Compare lengths instead.
# Backtest `_order_from_live` copies venue `cancel_reason` onto both fields after
# `_venue_cancel_reason` rewrite; live keeps the core reasons.
# Backtest adopt maps venue awaiting_cancel to canceling and has no unknown.
_ORDER_EXCLUSIONS = frozenset({"cancel_reason", "ack_reason", "status"})


@dataclass(frozen=True)
class TapeBook:
    now_ns: int
    bid0: float
    ask0: float
    bid1: float
    ask1: float


@dataclass(frozen=True)
class TapeSignal:
    now_ns: int
    delta: float
    anchor: float


@dataclass(frozen=True)
class TapeClock:
    now_ns: int
    second: int
    paused: bool
    game_ended: bool


@dataclass(frozen=True)
class TapeWake:
    now_ns: int


@dataclass(frozen=True)
class TapePlaceOk:
    now_ns: int
    refs: tuple[int, ...]


@dataclass(frozen=True)
class TapePlaceMissing:
    now_ns: int
    refs: tuple[int, ...]


@dataclass(frozen=True)
class TapePlaceRejected:
    now_ns: int
    refs: tuple[int, ...]


@dataclass(frozen=True)
class TapeFill:
    now_ns: int
    ref: int
    qty: float
    price: float


@dataclass(frozen=True)
class TapeCancel:
    now_ns: int
    ref: int
    ok: bool


@dataclass(frozen=True)
class TapeRecovery:
    now_ns: int


@dataclass(frozen=True)
class TapeRecoveryVerified:
    now_ns: int
    generation: int
    verified_qty: float
    verified_token_index: int | None
    last_buy_ns: int | None


TapeEvent = (
    TapeBook
    | TapeSignal
    | TapeClock
    | TapeWake
    | TapePlaceOk
    | TapePlaceMissing
    | TapePlaceRejected
    | TapeFill
    | TapeCancel
    | TapeRecovery
    | TapeRecoveryVerified
)


@dataclass(frozen=True)
class NormalCommand:
    kind: Literal["place", "cancel"]
    order_id: str
    token_index: int
    side: str
    price: float
    quantity: float


class ContractDriver(Protocol):
    def feed(self, event: TapeEvent) -> None: ...

    @property
    def state(self) -> StrategyState: ...

    def take_commands(self) -> tuple[NormalCommand, ...]: ...


def pinned_policy() -> Follow300Policy:
    return follow300_policy(level_usdc=PINNED_LEVEL_USDC, debounce_ms=100, fallback_timer_s=2.0)


def core_id(ref: int) -> str:
    return f"c{ref}"


def open_books(*, now_ns: int) -> TapeBook:
    return TapeBook(now_ns=now_ns, bid0=0.50, ask0=0.52, bid1=0.48, ask1=0.50)


def open_signal(*, now_ns: int) -> TapeSignal:
    return TapeSignal(now_ns=now_ns, delta=0.05, anchor=0.51)


def open_clock(*, now_ns: int, second: int = 0) -> TapeClock:
    return TapeClock(now_ns=now_ns, second=second, paused=False, game_ended=False)


def _place_command(
    *, order_id: str, token_index: int, side: str, price: float, quantity: float
) -> NormalCommand:
    return NormalCommand(
        kind="place",
        order_id=order_id,
        token_index=token_index,
        side=side,
        price=round(price, 4),
        quantity=round(quantity, 2),
    )


def _cancel_command(order_id: str) -> NormalCommand:
    return NormalCommand(
        kind="cancel", order_id=order_id, token_index=-1, side="", price=0.0, quantity=0.0
    )


def _first_seq_mismatch(label: str, actual: Sequence[object], expected: Sequence[object]) -> str:
    if len(actual) != len(expected):
        return f"{label} length {len(actual)} != {len(expected)}"
    for index, (got, want) in enumerate(zip(actual, expected, strict=True)):
        if got != want:
            return f"{label}[{index}] {got} != {want}"
    return ""


def _strip_order(order: RestingOrder) -> RestingOrder:
    blank = {name: "live" if name == "status" else "" for name in _ORDER_EXCLUSIONS}
    return replace(order, **blank)


def _comparable_state(state: StrategyState) -> StrategyState:
    return replace(
        state,
        seen_fill_ids=(),
        budget=Budget(
            cash_usdc=round(state.budget.cash_usdc, 6),
            cap_room_usdc=round(state.budget.cap_room_usdc, 6),
            account_cap_room_usdc=state.budget.account_cap_room_usdc,
        ),
        orders=tuple(_strip_order(order) for order in state.orders),
    )


def _first_value_mismatch(path: str, left: object, right: object) -> str:
    if left == right:
        return ""
    if is_dataclass(left) and is_dataclass(right) and type(left) is type(right):
        for field in fields(left):
            child = f"{path}.{field.name}"
            mismatch = _first_value_mismatch(
                child, getattr(left, field.name), getattr(right, field.name)
            )
            if mismatch:
                return mismatch
        return f"{path} {left} != {right}"
    if isinstance(left, tuple) and isinstance(right, tuple):
        if len(left) != len(right):
            return f"{path} length {len(left)} != {len(right)}"
        for index, (got, want) in enumerate(zip(left, right, strict=True)):
            mismatch = _first_value_mismatch(f"{path}[{index}]", got, want)
            if mismatch:
                return mismatch
        return ""
    return f"{path} {left} != {right}"


def first_contract_mismatch(
    *,
    left_commands: tuple[NormalCommand, ...],
    left_state: StrategyState,
    right_commands: tuple[NormalCommand, ...],
    right_state: StrategyState,
) -> str:
    commands_mismatch = _first_seq_mismatch("commands", left_commands, right_commands)
    if commands_mismatch:
        return commands_mismatch
    if len(left_state.seen_fill_ids) != len(right_state.seen_fill_ids):
        return (
            f"state.seen_fill_ids length {len(left_state.seen_fill_ids)} != "
            f"{len(right_state.seen_fill_ids)}"
        )
    left = _comparable_state(left_state)
    right = _comparable_state(right_state)
    return _first_value_mismatch("state", left, right)


def _tape_view(bid: float, ask: float) -> BookView:
    return BookView(
        best_bid=bid,
        best_bid_size=BOOK_SIZE,
        best_ask=ask,
        best_ask_size=BOOK_SIZE,
        second_bid=None,
        second_ask=None,
        bid_depth=BOOK_SIZE,
        ask_depth=BOOK_SIZE,
    )


def _signals_from_tape(tape: tuple[TapeEvent, ...]) -> tuple[TapeSignal, ...]:
    signals = tuple(event for event in tape if isinstance(event, TapeSignal))
    if signals:
        return signals
    return (open_signal(now_ns=0),)


class BacktestDriver:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        tape: tuple[TapeEvent, ...],
        cash: float,
        policy: Follow300Policy,
    ) -> None:
        signals = _signals_from_tape(tape)
        config = build_maker_config(
            signal_timestamps_ns=tuple(signal.now_ns for signal in signals),
            predicted_deltas=tuple(signal.delta for signal in signals),
            dataset_market_ps=tuple(signal.anchor for signal in signals),
        )
        strategy, submitted, _alerts = build_maker_harness(
            monkeypatch, config=config, free_usdc=Decimal(str(cash))
        )
        strategy._policy = policy
        self._strategy = strategy
        self._submitted = submitted
        self._submitted_at = 0
        self._commands: list[NormalCommand] = []
        self._seen_cancels: set[str] = set()
        self._last_place_ids: tuple[str, ...] = ()
        self._instrument_ids = strategy._config.instrument_ids

    @property
    def state(self) -> StrategyState:
        return self._strategy._core

    def take_commands(self) -> tuple[NormalCommand, ...]:
        out = tuple(self._commands)
        self._commands.clear()
        return out

    def feed(self, event: TapeEvent) -> None:
        handler = self._FEED[type(event)]
        handler(self, event)

    def _feed_book(self, event: TapeBook) -> None:
        self._strategy._books[RADIANT_ID] = _FakeBook(bid=event.bid0, ask=event.ask0)
        self._strategy._books[DIRE_ID] = _FakeBook(bid=event.bid1, ask=event.ask1)
        self._strategy._book_ts_ns[RADIANT_ID] = event.now_ns
        self._strategy._book_ts_ns[DIRE_ID] = event.now_ns

    def _feed_ignore(self, event: TapeSignal | TapeClock) -> None:
        del event

    def _feed_wake(self, event: TapeWake) -> None:
        self._strategy._evaluate(now_ns=event.now_ns)
        self._harvest()

    def _feed_place_ok(self, event: TapePlaceOk) -> None:
        for index in event.refs:
            accept_live(
                self._strategy,
                ts_event=event.now_ns,
                client_order_id=self._venue_of(self._last_place_ids[index]),
            )

    def _feed_place_missing(self, event: TapePlaceMissing) -> None:
        missing = set(event.refs)
        for index, order_id in enumerate(self._last_place_ids):
            if index in missing:
                self._strategy._drive(event=SubmitTimeout(now_ns=event.now_ns, order_id=order_id))
                continue
            accept_live(
                self._strategy,
                ts_event=event.now_ns,
                client_order_id=self._venue_of(order_id),
            )

    def _feed_place_rejected(self, event: TapePlaceRejected) -> None:
        for index in event.refs:
            venue_id = self._venue_of(self._last_place_ids[index])
            self._strategy.on_order_rejected(
                SimpleNamespace(
                    client_order_id=venue_id,
                    ts_event=event.now_ns,
                    reason="post-only",
                )
            )

    def _feed_fill(self, event: TapeFill) -> None:
        order_id = core_id(event.ref)
        venue_id = self._venue_of(order_id)
        live = self._strategy._live[venue_id]
        fill_order(
            self._strategy,
            live.order,
            Decimal(str(event.qty)),
            event.price,
            is_buy=live.order.side == OrderSide.BUY,
            ts_event=event.now_ns,
        )

    def _feed_cancel(self, event: TapeCancel) -> None:
        order_id = core_id(event.ref)
        venue_id = self._venue_of(order_id)
        if event.ok:
            fire_cancel_release(self._strategy, event.now_ns, client_order_id=venue_id)
            self._strategy.on_order_canceled(
                SimpleNamespace(ts_event=event.now_ns, client_order_id=venue_id)
            )
            # on_order_canceled already synced the book while the BUY still rested.
            # A second snapshot drops bid levels and looks like a new book.
            self._strategy._drive(
                event=BudgetUpdate(
                    now_ns=event.now_ns,
                    budget=self._strategy._budget(),
                )
            )
            self._harvest()
            return
        self._strategy._drive(event=CancelTimeout(now_ns=event.now_ns, order_id=order_id))

    def _feed_recovery(self, event: TapeRecovery) -> None:
        self._strategy.begin_recovery(now_ns=event.now_ns)
        self._harvest()

    def _feed_recovery_verified(self, event: TapeRecoveryVerified) -> None:
        self._strategy.accept_recovery_verified(
            now_ns=event.now_ns,
            generation=event.generation,
            position=CorePosition(
                token_index=event.verified_token_index,
                qty=event.verified_qty,
                cost_basis=0.0,
            ),
            last_buy_ns=event.last_buy_ns,
        )
        # Live recomputes its budget after the verified-driven requote; mirror it
        # so a same-tick episode reopen does not leave a stale snapshot.
        self._strategy._drive(
            event=BudgetUpdate(
                now_ns=event.now_ns,
                budget=self._strategy._budget(),
            )
        )
        self._harvest()

    _FEED: ClassVar[dict[type, Callable[..., None]]] = {
        TapeBook: _feed_book,
        TapeSignal: _feed_ignore,
        TapeClock: _feed_ignore,
        TapeWake: _feed_wake,
        TapePlaceOk: _feed_place_ok,
        TapePlaceMissing: _feed_place_missing,
        TapePlaceRejected: _feed_place_rejected,
        TapeFill: _feed_fill,
        TapeCancel: _feed_cancel,
        TapeRecovery: _feed_recovery,
        TapeRecoveryVerified: _feed_recovery_verified,
    }

    def _venue_of(self, order_id: str) -> str:
        return self._strategy._core_to_venue[order_id]

    def _harvest(self) -> None:
        for venue_id, live in self._strategy._live.items():
            if not live.awaiting_cancel:
                continue
            order_id = self._strategy._venue_to_core[venue_id]
            if order_id in self._seen_cancels:
                continue
            self._seen_cancels.add(order_id)
            self._commands.append(_cancel_command(order_id))
        new_places = self._submitted[self._submitted_at :]
        self._submitted_at = len(self._submitted)
        place_ids: list[str] = []
        for order in new_places:
            venue_id = str(order.client_order_id)
            order_id = self._strategy._venue_to_core[venue_id]
            token_index = self._instrument_ids.index(order.instrument_id)
            side = "BUY" if order.side == OrderSide.BUY else "SELL"
            self._commands.append(
                _place_command(
                    order_id=order_id,
                    token_index=token_index,
                    side=side,
                    price=float(order.price),
                    quantity=float(order.quantity),
                )
            )
            place_ids.append(order_id)
        if place_ids:
            self._last_place_ids = tuple(place_ids)


class LiveDriver:
    def __init__(self, *, cash: float, policy: Follow300Policy, trace: CoreTrace | None) -> None:
        self._core = LiveCore(
            policy=policy,
            limits=_limits(),
            freshness=_freshness(),
            yes_token=YES_TOKEN,
            no_token=NO_TOKEN,
            drop_sell=lambda _quote: False,
            trace=trace,
            max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
        )
        self._cache = CollateralCache(value=cash)
        self._tmp = tempfile.TemporaryDirectory()
        self._store = WalletStateStore(Path(self._tmp.name) / "w.db")
        self._books: BookPair | None = None
        self._commands: list[NormalCommand] = []
        self._last_place_ids: tuple[str, ...] = ()
        self._venue_seq = 0
        self._seen_cancels: set[str] = set()
        self._credited_keys: set[str] = set()

    @property
    def state(self) -> StrategyState:
        return self._core.state

    def take_commands(self) -> tuple[NormalCommand, ...]:
        out = tuple(self._commands)
        self._commands.clear()
        return out

    def feed(self, event: TapeEvent) -> None:
        handler = self._FEED[type(event)]
        handler(self, event)

    def _feed_book(self, event: TapeBook) -> None:
        self._books = books_from_views(
            yes=_tape_view(event.bid0, event.ask0),
            no=_tape_view(event.bid1, event.ask1),
            ts_ns=event.now_ns,
        )

    def _feed_signal(self, event: TapeSignal) -> None:
        self._core.enqueue(
            SignalUpdate(
                now_ns=event.now_ns,
                signal=RawDeltaSignal(
                    predicted_delta=event.delta,
                    source_received_ns=event.now_ns,
                    received_ns=event.now_ns,
                    anchor_p=event.anchor,
                    deaths_radiant=0,
                    deaths_dire=0,
                ),
            )
        )

    def _feed_clock(self, event: TapeClock) -> None:
        self._core.set_clock(
            GameClock(
                now_ns=event.now_ns,
                game_second=event.second,
                paused=event.paused,
                game_ended=event.game_ended,
            )
        )

    def _feed_wake(self, event: TapeWake) -> None:
        self._quote(event.now_ns)

    def _feed_place_ok(self, event: TapePlaceOk) -> None:
        self._core.note_placed(self._open_orders(event.refs), event.now_ns)
        self._core.drain_apply()

    def _feed_place_missing(self, event: TapePlaceMissing) -> None:
        keep = tuple(index for index in range(len(self._last_place_ids)) if index not in event.refs)
        self._core.note_placed(self._open_orders(keep), event.now_ns)
        self._core.drain_apply()

    def _feed_place_rejected(self, event: TapePlaceRejected) -> None:
        for index in event.refs:
            self._core.apply(
                OrderRejected(
                    now_ns=event.now_ns,
                    order_id=self._last_place_ids[index],
                    reason="post-only",
                )
            )

    def _feed_fill(self, event: TapeFill) -> None:
        order_id = core_id(event.ref)
        venue_id = self._core._core_to_venue[order_id]
        fill_id = f"{venue_id}:{event.now_ns}:{event.qty}"
        self._credit_store(event, fill_id)
        owner = next(
            (order for order in self._core.state.orders if order.order_id == order_id),
            next(
                (record for record in self._core.state.records if record.order_id == order_id),
                None,
            ),
        )
        token_index = 0 if owner is None else owner.token_index
        side = "BUY" if owner is None else owner.side
        self._core.note_fill(
            fill_key=fill_id,
            venue_id=venue_id,
            qty=event.qty,
            price=event.price,
            now_ns=event.now_ns,
            token_index=token_index,
            side=side,
        )
        self._core.drain_apply()

    def _feed_cancel(self, event: TapeCancel) -> None:
        order_id = core_id(event.ref)
        venue_id = self._core._core_to_venue[order_id]
        self._core.note_cancel([venue_id], event.ok, event.now_ns)
        if event.ok:
            self._quote(event.now_ns)
            return
        self._core.drain_apply()

    def _feed_recovery(self, event: TapeRecovery) -> None:
        self._core.note_recovery(now_ns=event.now_ns)
        self._quote(event.now_ns)

    def _feed_recovery_verified(self, event: TapeRecoveryVerified) -> None:
        self._store.positions.pop(YES_TOKEN, None)
        self._store.positions.pop(NO_TOKEN, None)
        if event.verified_token_index == 0:
            self._store.positions[YES_TOKEN] = Position(YES_TOKEN, event.verified_qty, 0.0)
        elif event.verified_token_index == 1:
            self._store.positions[NO_TOKEN] = Position(NO_TOKEN, event.verified_qty, 0.0)

        tokens = [
            TokenInventory(token_index=0, qty=0.0, cost_basis=0.0, last_buy_ns=None),
            TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
        ]
        if event.verified_token_index is not None:
            tokens[event.verified_token_index] = TokenInventory(
                token_index=event.verified_token_index,
                qty=event.verified_qty,
                cost_basis=0.0,
                last_buy_ns=event.last_buy_ns,
            )
        self._core.note_recovery_verified(
            now_ns=event.now_ns,
            generation=event.generation,
            inventory=(tokens[0], tokens[1]),
        )
        self._quote(event.now_ns)

    _FEED: ClassVar[dict[type, Callable[..., None]]] = {
        TapeBook: _feed_book,
        TapeSignal: _feed_signal,
        TapeClock: _feed_clock,
        TapeWake: _feed_wake,
        TapePlaceOk: _feed_place_ok,
        TapePlaceMissing: _feed_place_missing,
        TapePlaceRejected: _feed_place_rejected,
        TapeFill: _feed_fill,
        TapeCancel: _feed_cancel,
        TapeRecovery: _feed_recovery,
        TapeRecoveryVerified: _feed_recovery_verified,
    }

    def _quote(self, now_ns: int) -> None:
        quote_cycle(
            self._core,
            _inputs(),
            now_ns=now_ns,
            books=self._books,
            clock=replace(self._core.latest_clock, now_ns=now_ns),
            limits=_limits(),
            store=self._store,
            cache=self._cache,
            cores=(self._core,),
            account_cap_usdc=float("inf"),
        )
        planned = self._core.take_plan()
        if planned is None:
            return
        self._record_batch(planned)

    def _credit_store(self, event: TapeFill, fill_id: str) -> None:
        if fill_id in self._credited_keys:
            return
        self._credited_keys.add(fill_id)
        order = next(
            item for item in self._core.state.orders if item.order_id == core_id(event.ref)
        )
        token_id = self._core.token_id(order.token_index)
        held = self._store.position(token_id)
        signed = event.qty if order.side == "BUY" else -event.qty
        size = max(0.0, held.size + signed)
        if size <= 0.0:
            avg_price = 0.0
        elif order.side == "BUY":
            avg_price = (held.avg_price * held.size + event.price * event.qty) / size
        else:
            avg_price = held.avg_price
        self._store.positions[token_id] = Position(token_id, size, avg_price)

    def _open_orders(self, refs: tuple[int, ...]) -> list[OpenOrder]:
        batch = self._core._last_batch
        if batch is None:
            raise AssertionError("place ack needs a prior take_plan batch")
        placed: list[OpenOrder] = []
        for index in refs:
            item = batch.to_place[index]
            self._venue_seq += 1
            placed.append(
                OpenOrder(
                    f"v{self._venue_seq}",
                    item.quote.token_id,
                    item.quote.side,
                    item.quote.price,
                    item.quote.size,
                )
            )
        return placed

    def _record_batch(self, planned: PlannedBatch) -> None:
        for venue_id in planned.to_cancel:
            order_id = self._core._venue_to_core[venue_id]
            if order_id in self._seen_cancels:
                continue
            self._seen_cancels.add(order_id)
            self._commands.append(_cancel_command(order_id))
        place_ids: list[str] = []
        for item in planned.to_place:
            token_index = self._core.token_index(item.quote.token_id)
            if token_index is None:
                raise AssertionError(f"unknown token {item.quote.token_id}")
            side = "BUY" if item.quote.side is Side.BUY else "SELL"
            self._commands.append(
                _place_command(
                    order_id=item.order_id,
                    token_index=token_index,
                    side=side,
                    price=item.quote.price,
                    quantity=item.quote.size,
                )
            )
            place_ids.append(item.order_id)
        if place_ids:
            self._last_place_ids = tuple(place_ids)


def build_drivers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tape: tuple[TapeEvent, ...],
    cash: float = DEFAULT_CASH,
) -> tuple[BacktestDriver, LiveDriver]:
    policy = pinned_policy()
    backtest = BacktestDriver(monkeypatch, tape=tape, cash=cash, policy=policy)
    live = LiveDriver(cash=cash, policy=policy, trace=None)
    return backtest, live


def assert_drivers_match(left: ContractDriver, right: ContractDriver, *, at: TapeEvent) -> None:
    mismatch = first_contract_mismatch(
        left_commands=left.take_commands(),
        left_state=left.state,
        right_commands=right.take_commands(),
        right_state=right.state,
    )
    if mismatch:
        raise AssertionError(f"after {at}: {mismatch}")


def play_tape(
    monkeypatch: pytest.MonkeyPatch,
    tape: tuple[TapeEvent, ...],
    *,
    cash: float = DEFAULT_CASH,
) -> tuple[BacktestDriver, LiveDriver]:
    backtest, live = build_drivers(monkeypatch, tape=tape, cash=cash)
    for event in tape:
        backtest.feed(event)
        live.feed(event)
        assert_drivers_match(backtest, live, at=event)
    return backtest, live
