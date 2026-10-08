"""Unit tests for DotaMakerStrategy inventory, windows, and cancel/replace."""

# pyright: reportPrivateUsage=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
# pyright: reportAttributeAccessIssue=false
# pyright: reportOptionalMemberAccess=false

from dataclasses import replace
from decimal import Decimal
from math import isnan
from types import SimpleNamespace
from typing import cast

import pytest
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.orders import LimitOrder

from backtest.maker_orders import (
    LiveOrder,
    remaining_buy_notional,
    remaining_qty,
    volume_at_price,
)
from backtest.run import build_order_latency
from backtest.strategy import (
    BUY_LEVEL_COUNT,
    DotaMakerConfig,
    DotaMakerStrategy,
    MatchKernelConfig,
    ObservedClockTape,
    round_buy_price,
    round_sell_price,
)
from backtest.telemetry import UptimeRecord, clear_records, take_records
from shared.constants.strategy import (
    BACKTEST_DOTA_MAX_POSITION_LEVELS,
    BASE_SIZE_USDC,
    BUY_CUTOFF_SECOND,
    EXIT_FEED_STALE_SECONDS,
    GRID_FEED_STALE_SECONDS,
    MAX_ENTRY_PRICE,
    MIN_ABS_DELTA,
    MIN_ENTRY_PRICE,
    MIN_ORDER_SIZE,
    ORDER_CANCEL_LATENCY_MS,
    ORDER_INSERT_LATENCY_MS,
    QUOTE_GRID,
)
from shared.utils.match_time import NS_PER_SECOND
from shared.utils.telonex_book import MAX_BOOK_AGE_SECONDS, PAIR_SUM_TOLERANCE
from shared.utils.trading import buy_share_quantity
from strategy.lifecycle import mark_canceling
from strategy.policy import follow300_policy
from strategy.types import (
    CancelOrder,
    FreshnessLimits,
    KillGateUpdate,
    MarketLimits,
    OrderRejected,
    Plan,
)

INSERT_LATENCY_NS = round(ORDER_INSERT_LATENCY_MS * 1_000_000)
CANCEL_LATENCY_NS = round(ORDER_CANCEL_LATENCY_MS * 1_000_000)

VENUE = Venue("POLYMARKET")
RADIANT_ID = InstrumentId(Symbol("0xabc-111"), VENUE)
DIRE_ID = InstrumentId(Symbol("0xabc-222"), VENUE)
CLIP_AT_048 = Decimal("208.33")


def seed_held(
    strategy: DotaMakerStrategy,
    qty: Decimal,
    *,
    token_index: int = 0,
    last_buy_ns: int | None = None,
) -> None:
    strategy.seed_inventory(token_index=token_index, qty=float(qty), last_buy_ns=last_buy_ns)


class _FakeQuantity:
    """Stand-in for a Nautilus Quantity carrying a Decimal value."""

    def __init__(self, value: Decimal) -> None:
        self._value = value

    def as_decimal(self) -> Decimal:
        return self._value

    def __float__(self) -> float:
        return float(self._value)

    def __str__(self) -> str:
        return str(self._value)


class _FakePrice:
    """Stand-in for a Nautilus Price."""

    def __init__(self, value: float) -> None:
        self._value = value

    def __float__(self) -> float:
        return self._value

    def __str__(self) -> str:
        return f"{self._value:.3f}"


class _FakeInstrument:
    """Minimal instrument exposing only what the maker strategy reads."""

    def __init__(self, instrument_id: InstrumentId) -> None:
        self.id = instrument_id
        self.quote_currency = "USDC"
        self.min_quantity = _FakeQuantity(Decimal(5))

    def make_qty(self, value: Decimal) -> _FakeQuantity:
        return _FakeQuantity(Decimal(str(value)))

    def make_price(self, value: float) -> _FakePrice:
        return _FakePrice(float(value))


class _FakeBook:
    """Minimal L2 book with flat bid/ask and sizes."""

    def __init__(
        self,
        *,
        bid: float,
        ask: float,
        bid_size: float = 20.0,
        ask_size: float = 20.0,
        bid_levels: tuple[tuple[float, float], ...] | None = None,
    ) -> None:
        self._bid = bid
        self._ask = ask
        self._bid_size = bid_size
        self._ask_size = ask_size
        levels = ((bid, bid_size),) if bid_levels is None else bid_levels
        self.bids = lambda: [
            SimpleNamespace(price=price, size=lambda captured=size: captured)
            for price, size in levels
        ]

    def best_bid_price(self) -> float:
        return self._bid

    def best_ask_price(self) -> float:
        return self._ask

    def best_bid_size(self) -> float:
        return self._bid_size

    def best_ask_size(self) -> float:
        return self._ask_size


def build_maker_config(
    *,
    buy_cutoff_ns: int = 600 * NS_PER_SECOND,
    game_end_ns: int = 900 * NS_PER_SECOND,
    predicted_delta: float = 0.05,
    dataset_market_p: float = 0.50,
    signal_timestamps_ns: tuple[int, ...] = (0,),
    source_timestamps_ns: tuple[int, ...] | None = None,
    feed_timestamps_ns: tuple[int, ...] | None = None,
    predicted_deltas: tuple[float, ...] | None = None,
    dataset_market_ps: tuple[float, ...] | None = None,
    deaths_radiant: tuple[int, ...] | None = None,
    deaths_dire: tuple[int, ...] | None = None,
    kill_gates: tuple[KillGateUpdate, ...] = (),
    board_tick_ns: tuple[int, ...] = (),
    observed_clock: ObservedClockTape | None = None,
    level_usdc: float = 100.0,
    min_abs_delta: float = MIN_ABS_DELTA,
    mid_spike_enabled: bool = True,
    buy_cutoff_second: int = BUY_CUTOFF_SECOND,
) -> DotaMakerConfig:
    """Config with a single fresh signal row at t=0 unless tuples are passed."""
    latency = build_order_latency()
    resolved_deltas = predicted_deltas if predicted_deltas is not None else (predicted_delta,)
    resolved_markets = dataset_market_ps if dataset_market_ps is not None else (dataset_market_p,)
    resolved_deaths_r = (
        deaths_radiant if deaths_radiant is not None else (0,) * len(resolved_deltas)
    )
    resolved_deaths_d = deaths_dire if deaths_dire is not None else (0,) * len(resolved_deltas)
    resolved_feed = feed_timestamps_ns if feed_timestamps_ns is not None else signal_timestamps_ns
    resolved_source = (
        source_timestamps_ns if source_timestamps_ns is not None else signal_timestamps_ns
    )
    policy = follow300_policy(level_usdc=level_usdc, debounce_ms=0, fallback_timer_s=1.0)
    if not mid_spike_enabled:
        policy = replace(policy, mid_spike_lookback_s=0.0, mid_spike_cooloff_s=0.0)
    return DotaMakerConfig(
        match_id=1,
        instrument_ids=(RADIANT_ID, DIRE_ID),
        feed_timestamps_ns=resolved_feed,
        signal_timestamps_ns=signal_timestamps_ns,
        source_timestamps_ns=resolved_source,
        predicted_deltas=resolved_deltas,
        dataset_market_ps=resolved_markets,
        deaths_radiant=resolved_deaths_r,
        deaths_dire=resolved_deaths_d,
        kill_gates=kill_gates,
        board_tick_ns=board_tick_ns,
        observed_clock=observed_clock,
        horn_ns=0,
        buy_cutoff_ns=buy_cutoff_ns,
        game_end_ns=game_end_ns,
        order_latency_ns=latency[1],
        cancel_latency_ns=latency[2],
        kernel=MatchKernelConfig(
            policy=replace(
                policy,
                min_abs_delta=min_abs_delta,
                exit_abs_delta=min_abs_delta,
                buy_cutoff_second=buy_cutoff_second,
            ),
            limits=MarketLimits(
                min_order_size=MIN_ORDER_SIZE,
                tick_size=QUOTE_GRID,
                pair_sum_tolerance=PAIR_SUM_TOLERANCE,
                radiant_token_index=0,
            ),
            freshness=FreshnessLimits(
                book_stale_s=MAX_BOOK_AGE_SECONDS,
                entry_stale_s=GRID_FEED_STALE_SECONDS,
                exit_stale_s=EXIT_FEED_STALE_SECONDS,
            ),
            max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
        ),
    )


def build_maker_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    radiant_bid: float = 0.48,
    radiant_ask: float = 0.52,
    dire_bid: float = 0.48,
    dire_ask: float = 0.52,
    free_usdc: Decimal = Decimal("1000"),
    config: DotaMakerConfig | None = None,
    radiant_bid_levels: tuple[tuple[float, float], ...] | None = None,
) -> tuple[DotaMakerStrategy, list[SimpleNamespace], list[tuple[str, int]]]:
    """Build a DotaMakerStrategy with fake books and recording submit/cancel."""
    clear_records()
    resolved = config if config is not None else build_maker_config()
    strategy = DotaMakerStrategy(resolved)
    order_counter = {"n": 0}

    def make_limit(**kwargs: object) -> SimpleNamespace:
        order_counter["n"] += 1
        return SimpleNamespace(
            client_order_id=f"O-{order_counter['n']}",
            instrument_id=kwargs["instrument_id"],
            side=kwargs["order_side"],
            quantity=kwargs["quantity"],
            price=kwargs["price"],
            time_in_force=kwargs["time_in_force"],
            post_only=kwargs["post_only"],
        )

    monkeypatch.setattr(
        DotaMakerStrategy,
        "order_factory",
        property(lambda _self: SimpleNamespace(limit=make_limit)),
        raising=False,
    )
    monkeypatch.setattr(
        DotaMakerStrategy,
        "portfolio",
        property(
            lambda _self: SimpleNamespace(
                account=lambda _venue: SimpleNamespace(
                    balance_free=lambda _currency: _FakeQuantity(
                        free_usdc - remaining_buy_notional(_self._live)
                    ),
                )
            )
        ),
        raising=False,
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        DotaMakerStrategy,
        "log",
        property(
            lambda _self: SimpleNamespace(
                info=lambda _message: None,
                warning=lambda message: warnings.append(str(message)),
            )
        ),
        raising=False,
    )
    alerts: list[tuple[str, int]] = []
    alert_callbacks: list[object] = []

    def set_time_alert_ns(name: str, alert_time_ns: int, callback: object = None) -> None:
        alerts.append((name, alert_time_ns))
        if callback is not None:
            alert_callbacks.append((name, alert_time_ns, callback))

    fake_clock = SimpleNamespace(
        alerts=alerts,
        timestamp_ns=lambda: 0,
        set_time_alert_ns=set_time_alert_ns,
        set_timer_ns=lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        DotaMakerStrategy, "clock", property(lambda _self: fake_clock), raising=False
    )

    strategy._books[RADIANT_ID] = _FakeBook(
        bid=radiant_bid, ask=radiant_ask, bid_levels=radiant_bid_levels
    )
    strategy._books[DIRE_ID] = _FakeBook(bid=dire_bid, ask=dire_ask)
    strategy._book_ts_ns[RADIANT_ID] = 0
    strategy._book_ts_ns[DIRE_ID] = 0
    strategy._instruments[RADIANT_ID] = _FakeInstrument(RADIANT_ID)
    strategy._instruments[DIRE_ID] = _FakeInstrument(DIRE_ID)
    strategy._warnings = warnings
    strategy._alert_callbacks = alert_callbacks

    submitted: list[SimpleNamespace] = []
    canceled: list[SimpleNamespace] = []
    strategy.submit_order = submitted.append
    strategy.cancel_order = canceled.append
    strategy._canceled = canceled
    return strategy, submitted, alerts


def assert_ladder_invariants(strategy: DotaMakerStrategy) -> None:
    sells = 0
    rungs: list[int | None] = []
    for live in strategy._live.values():
        if live.order.side == OrderSide.SELL:
            sells += 1
            continue
        token = strategy._token_index_of(live.order.instrument_id)
        assert strategy._episode_token_index in (None, token)
        if not live.awaiting_cancel and not live.partially_filled:
            rungs.append(live.level_index)
    assert sells <= 1
    assert len(rungs) == len(set(rungs))


def only_live_state(strategy: DotaMakerStrategy) -> LiveOrder | None:
    assert_ladder_invariants(strategy)
    states = list(strategy._live.values())
    if not states:
        return None
    states.sort(key=lambda live: (live.level_index is None, live.level_index or 0))
    return states[0]


def only_live(strategy: DotaMakerStrategy) -> object | None:
    state = only_live_state(strategy)
    if state is None:
        return None
    return state.order


def live_state(strategy: DotaMakerStrategy) -> LiveOrder:
    state = only_live_state(strategy)
    if state is None:
        raise AssertionError("expected one live order")
    return state


def set_only_live(strategy: DotaMakerStrategy, order: object) -> LiveOrder:
    keep = str(order.client_order_id)
    for client_order_id in list(strategy._live):
        if client_order_id == keep:
            continue
        core_id = strategy._venue_to_core.get(client_order_id)
        if core_id is not None:
            strategy._drive(event=OrderRejected(now_ns=0, order_id=core_id, reason="set_only_live"))
        strategy._detach_live(client_order_id)
    live = strategy._live.get(keep)
    if live is None:
        live = LiveOrder(order=cast(LimitOrder, order))
        strategy._live[keep] = live
    return live


def accept_live(
    strategy: DotaMakerStrategy, ts_event: int = 0, client_order_id: str | None = None
) -> None:
    resolved = client_order_id
    if resolved is None:
        order = only_live(strategy)
        if order is None:
            raise AssertionError("accept_live needs a live order or client_order_id")
        resolved = str(order.client_order_id)
    strategy.on_order_accepted(SimpleNamespace(ts_event=ts_event, client_order_id=resolved))


def fire_cancel_release(
    strategy: DotaMakerStrategy, now_ns: int, client_order_id: str | None = None
) -> None:
    callbacks = strategy._alert_callbacks
    resolved = client_order_id
    if resolved is None:
        pending = [
            name
            for name, _alert_time_ns, callback in callbacks
            if callable(callback) and str(name).startswith("CANCEL:")
        ]
        if len(pending) == 1:
            resolved = str(pending[0]).split(":", 1)[1]
        else:
            order = only_live(strategy)
            if order is None:
                raise AssertionError("fire_cancel_release needs a live order or client_order_id")
            resolved = str(order.client_order_id)
    name = f"CANCEL:{resolved}"
    fired = False
    for index, (alert_name, alert_time_ns, callback) in enumerate(list(callbacks)):
        if alert_name == name and callable(callback):
            del callbacks[index]
            callback(SimpleNamespace(ts_event=now_ns if now_ns else alert_time_ns, name=name))
            fired = True
            break
    if not fired:
        raise AssertionError(f"no _on_cancel_release callback was scheduled for {name}")
    send_name = f"CANCEL_SEND:{resolved}"
    for index, (alert_name, alert_time_ns, callback) in enumerate(list(callbacks)):
        if alert_name == send_name and callable(callback):
            del callbacks[index]
            callback(
                SimpleNamespace(
                    ts_event=(now_ns if now_ns else alert_time_ns),
                    name=send_name,
                )
            )
            return


def accept_all(strategy: DotaMakerStrategy, ts_event: int = 0) -> None:
    for client_order_id in list(strategy._live):
        accept_live(strategy, ts_event=ts_event, client_order_id=client_order_id)


def fill_order(
    strategy: DotaMakerStrategy,
    order: object,
    qty: Decimal,
    price: float,
    *,
    is_buy: bool,
    ts_event: int,
) -> None:
    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            last_qty=_FakeQuantity(qty),
            last_px=_FakePrice(price),
            is_buy=is_buy,
            ts_event=ts_event,
        )
    )


def buy_orders(submitted: list[SimpleNamespace]) -> list[SimpleNamespace]:
    buys = [order for order in submitted if order.side == OrderSide.BUY]
    buys.sort(key=lambda order: float(order.price), reverse=True)
    return buys


def test_join_buy_is_limit_gtc_post_only_dollar_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flat inventory posts the top rung as a $100 / 0.48 = 208.33 join on the 0.01 grid."""
    strategy, submitted, alerts = build_maker_harness(monkeypatch)

    strategy._evaluate(now_ns=0)

    assert len(submitted) == BUY_LEVEL_COUNT
    order = submitted[0]
    assert order.instrument_id == RADIANT_ID
    assert order.side == OrderSide.BUY
    assert order.time_in_force == TimeInForce.GTC
    assert order.post_only is True
    assert order.quantity.as_decimal() == CLIP_AT_048
    assert float(order.price) == pytest.approx(0.48)
    assert alerts[0] == ("RELEASE:O-1", INSERT_LATENCY_NS)


def test_buy_quantity_uses_fixed_backtest_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every backtest BUY uses the canonical $100 normalized clip."""
    strategy, submitted, _ = build_maker_harness(monkeypatch, config=build_maker_config())
    strategy._evaluate(now_ns=0)
    expected = buy_share_quantity(base_size_usdc=BASE_SIZE_USDC, price=float(submitted[0].price))
    assert float(submitted[0].quantity) == pytest.approx(expected)


def test_token_choice_is_max_fair_minus_bid_with_lower_index_tiebreak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When edges tie, the lower token index wins."""
    config = build_maker_config(predicted_delta=MIN_ABS_DELTA, dataset_market_p=0.50)
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.49,
        radiant_ask=0.51,
        dire_bid=0.49,
        dire_ask=0.51,
        config=config,
    )

    strategy._evaluate(now_ns=0)

    assert len(submitted) == BUY_LEVEL_COUNT
    assert {order.instrument_id for order in submitted} == {RADIANT_ID}


def test_one_order_per_rung_and_no_mirror_while_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second evaluate keeps the queue; a long position only sells the held token."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.48,
        radiant_ask=0.53,
        dire_bid=0.48,
        dire_ask=0.53,
        config=build_maker_config(
            signal_timestamps_ns=(0, 2 * NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    assert len(submitted) == BUY_LEVEL_COUNT
    live = buy_orders(submitted)[0]
    accept_all(strategy)

    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert len(submitted) == BUY_LEVEL_COUNT

    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(CLIP_AT_048),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=NS_PER_SECOND,
        )
    )
    assert strategy._position_qty == CLIP_AT_048
    assert str(live.client_order_id) not in strategy._live

    sell_ns = 11 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = sell_ns
    strategy._book_ts_ns[DIRE_ID] = sell_ns
    strategy._evaluate(now_ns=sell_ns)
    sells = [order for order in submitted if order.side == OrderSide.SELL]
    assert len(sells) == 1
    assert sells[0].instrument_id == RADIANT_ID
    assert not any(order.instrument_id == DIRE_ID for order in submitted)
    assert_ladder_invariants(strategy)


def test_board_decision_between_feed_ticks_reaches_the_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An archive board decision is not a feed tick, yet the quoter must see it."""
    strategy, _, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            feed_timestamps_ns=(0, 10 * NS_PER_SECOND),
            signal_timestamps_ns=(0, 2 * NS_PER_SECOND, 10 * NS_PER_SECOND),
            source_timestamps_ns=(0, 0, 10 * NS_PER_SECOND),
            predicted_deltas=(0.05, 0.08, 0.05),
            dataset_market_ps=(0.50, 0.50, 0.50),
            board_tick_ns=(2 * NS_PER_SECOND,),
        ),
    )
    strategy._evaluate(now_ns=0)
    strategy._evaluate(now_ns=2 * NS_PER_SECOND)
    signal = strategy._core.signal
    assert signal is not None
    assert signal.received_ns == 2 * NS_PER_SECOND
    assert signal.predicted_delta == pytest.approx(0.08)


def test_partial_buy_is_not_repriced(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a BUY fill below min (2 of 208.33) that remainder rests through reprice ticks."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    live = buy_orders(submitted)[0]
    accept_all(strategy)
    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(Decimal("2")),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=0,
        )
    )
    remainder = strategy._live[str(live.client_order_id)]
    assert remainder.partially_filled is True
    assert strategy._position_qty == Decimal("2")

    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.49, ask=0.53)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert strategy._live[str(live.client_order_id)].awaiting_cancel is False
    assert float(strategy._live[str(live.client_order_id)].order.price) == pytest.approx(0.48)
    assert live not in strategy._canceled


def test_partial_clip_at_or_above_min_keeps_the_ladder_and_sells_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fill 40 of 208.33: the rungs keep working and a SELL of 40 posts after exit settle."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.48,
        radiant_ask=0.53,
        dire_bid=0.48,
        dire_ask=0.53,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    live = buy_orders(submitted)[0]
    accept_all(strategy)
    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(Decimal("40")),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=0,
        )
    )
    assert strategy._position_qty == Decimal("40")
    assert strategy._live[str(live.client_order_id)].partially_filled is True

    sell_ns = 11 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = sell_ns
    strategy._book_ts_ns[DIRE_ID] = sell_ns
    strategy._evaluate(now_ns=sell_ns)
    sells = [order for order in submitted if order.side == OrderSide.SELL]
    assert len(sells) == 1
    assert sells[0].quantity.as_decimal() == Decimal("40")
    assert_ladder_invariants(strategy)


def test_partial_sell_remainder_rests_when_dust(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reduce-only SELL leftover below CLOB min stays on the book."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(predicted_delta=0.009),
    )
    seed_held(strategy, Decimal("10"))
    strategy._evaluate(now_ns=0)
    assert len(submitted) == 1
    sell = submitted[0]
    assert sell.side == OrderSide.SELL
    accept_live(strategy)
    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=sell.client_order_id,
            last_qty=_FakeQuantity(Decimal("7")),
            last_px=_FakePrice(0.52),
            is_buy=False,
            ts_event=0,
        )
    )
    assert strategy._position_qty == Decimal("3")
    assert live_state(strategy).partially_filled is True
    assert only_live(strategy) is sell

    later_ns = 10 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = later_ns
    strategy._book_ts_ns[DIRE_ID] = later_ns
    strategy._evaluate(now_ns=later_ns)
    assert live_state(strategy).awaiting_cancel is False
    assert strategy._canceled == []
    assert only_live(strategy) is sell
    assert len(submitted) == 1


def test_buy_blocked_after_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """No new BUY is submitted at or after buy_cutoff_ns."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(buy_cutoff_ns=NS_PER_SECOND)
    )

    strategy._evaluate(now_ns=NS_PER_SECOND)

    assert submitted == []


def test_buy_blocked_after_cutoff_past_540(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a clock tape, a cutoff above 540 still blocks BUY at buy_cutoff_ns."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(buy_cutoff_ns=NS_PER_SECOND, buy_cutoff_second=900)
    )

    strategy._evaluate(now_ns=NS_PER_SECOND)

    assert submitted == []


def test_flat_after_cutoff_does_not_record_no_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past BUY cutoff with a flat book does not emit 1 Hz cutoff no_quote events."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(buy_cutoff_ns=NS_PER_SECOND)
    )

    strategy._evaluate(now_ns=NS_PER_SECOND)

    assert submitted == []
    records = take_records()
    assert not any(event.kind == "no_quote" for event in records.quote_events)


def test_flat_after_cutoff_stops_later_wakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once cutoff, flat, and empty, a later core wake leaves the core untouched."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(buy_cutoff_ns=NS_PER_SECOND)
    )

    strategy._evaluate(now_ns=NS_PER_SECOND)

    assert submitted == []
    assert strategy._finished
    core = strategy._core
    strategy._on_core_wake(SimpleNamespace(name="CORE_WAKE:1:0", ts_event=2 * NS_PER_SECOND))
    assert strategy._core is core


def test_buy_skipped_below_min_abs_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new BUY is not posted when |predicted_delta| is below min_abs_delta."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(predicted_delta=0.009)
    )

    strategy._evaluate(now_ns=0)

    assert submitted == []
    records = take_records()
    assert any(event.reason == "min_delta" for event in records.quote_events)


def test_buy_skipped_when_config_raises_min_abs_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config min_abs_delta above |predicted_delta| blocks the BUY that 1¢ would admit."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(predicted_delta=0.015, min_abs_delta=0.02)
    )

    strategy._evaluate(now_ns=0)

    assert submitted == []
    records = take_records()
    assert any(event.reason == "min_delta" for event in records.quote_events)


def test_buy_posted_at_min_abs_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new BUY is posted when |predicted_delta| equals min_abs_delta."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(predicted_delta=MIN_ABS_DELTA)
    )

    strategy._evaluate(now_ns=0)

    assert len(submitted) == BUY_LEVEL_COUNT
    assert {order.side for order in submitted} == {OrderSide.BUY}


def test_sell_not_blocked_by_min_abs_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SELL still posts when |predicted_delta| is below the BUY-only delta gate."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.48,
        radiant_ask=0.52,
        dire_bid=0.48,
        dire_ask=0.52,
        config=build_maker_config(predicted_delta=0.009),
    )
    seed_held(strategy, Decimal("5"))
    strategy._evaluate(now_ns=0)

    assert len(submitted) == 1
    assert submitted[0].side == OrderSide.SELL


def test_buy_skipped_below_min_entry_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new BUY is not posted when the fair-passing join price is under MIN_ENTRY_PRICE."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.30,
        radiant_ask=0.32,
        dire_bid=0.68,
        dire_ask=0.70,
        config=build_maker_config(predicted_delta=0.05, dataset_market_p=0.31),
    )

    strategy._evaluate(now_ns=0)

    assert submitted == []
    records = take_records()
    assert any(event.reason == "min_price" for event in records.quote_events)


def test_buy_posted_at_min_entry_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new BUY is posted when the join price equals MIN_ENTRY_PRICE."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=MIN_ENTRY_PRICE,
        radiant_ask=MIN_ENTRY_PRICE + 0.02,
        dire_bid=round(1 - MIN_ENTRY_PRICE - 0.02, 2),
        dire_ask=round(1 - MIN_ENTRY_PRICE, 2),
        config=build_maker_config(predicted_delta=0.05, dataset_market_p=MIN_ENTRY_PRICE + 0.01),
    )

    strategy._evaluate(now_ns=0)

    assert len(submitted) == 1
    assert submitted[0].side == OrderSide.BUY
    assert float(submitted[0].price) == pytest.approx(MIN_ENTRY_PRICE)


def test_sell_not_blocked_by_min_entry_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """A held token still sells when the join price is under MIN_ENTRY_PRICE."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.29,
        radiant_ask=0.31,
        dire_bid=0.69,
        dire_ask=0.71,
        config=build_maker_config(predicted_delta=0.0, dataset_market_p=0.30),
    )
    seed_held(strategy, Decimal("5"))
    strategy._evaluate(now_ns=0)

    assert len(submitted) == 1
    assert submitted[0].side == OrderSide.SELL
    assert float(submitted[0].price) < MIN_ENTRY_PRICE


def test_buy_skipped_at_max_entry_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new BUY is not posted when the fair-passing join price is at MAX_ENTRY_PRICE."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.85,
        radiant_ask=0.87,
        dire_bid=0.13,
        dire_ask=0.15,
        config=build_maker_config(predicted_delta=0.05, dataset_market_p=0.86),
    )

    strategy._evaluate(now_ns=0)

    assert submitted == []
    records = take_records()
    assert any(event.reason == "max_price" for event in records.quote_events)


def test_buy_posted_below_max_entry_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new BUY is posted when the join price is one tick under MAX_ENTRY_PRICE."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.84,
        radiant_ask=0.86,
        dire_bid=0.14,
        dire_ask=0.16,
        config=build_maker_config(predicted_delta=0.05, dataset_market_p=0.85),
    )

    strategy._evaluate(now_ns=0)

    assert len(submitted) == 3
    assert all(order.side == OrderSide.BUY for order in submitted)
    assert float(submitted[0].price) == pytest.approx(MAX_ENTRY_PRICE - 0.01)
    assert all(float(order.price) < MAX_ENTRY_PRICE for order in submitted)


def test_sell_not_blocked_by_max_entry_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """A held token still sells when the join price is at or above MAX_ENTRY_PRICE."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.86,
        radiant_ask=0.88,
        dire_bid=0.12,
        dire_ask=0.14,
        config=build_maker_config(predicted_delta=0.0, dataset_market_p=0.87),
    )
    seed_held(strategy, Decimal("5"))
    strategy._evaluate(now_ns=0)

    assert len(submitted) == 1
    assert submitted[0].side == OrderSide.SELL
    assert float(submitted[0].price) >= MAX_ENTRY_PRICE


def test_buy_blocked_at_six_spread_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new BUY is not posted when both token books are 6 ticks wide."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.48,
        radiant_ask=0.54,
        dire_bid=0.48,
        dire_ask=0.54,
    )

    strategy._evaluate(now_ns=0)

    assert submitted == []
    records = take_records()
    assert any(event.reason == "wide_spread" for event in records.quote_events)


def test_buy_posted_at_five_spread_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new BUY still posts when both token books are 5 ticks wide."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.48,
        radiant_ask=0.53,
        dire_bid=0.48,
        dire_ask=0.53,
    )

    strategy._evaluate(now_ns=0)

    assert len(submitted) == BUY_LEVEL_COUNT
    assert {order.side for order in submitted} == {OrderSide.BUY}


def test_on_start_signal_alert_starts_at_first_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signal alerts start on the first feed timestamp, not a 1 Hz reprice loop."""
    first_signal_ns = -58 * NS_PER_SECOND
    strategy, _, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(signal_timestamps_ns=(first_signal_ns,))
    )
    alerts: list[dict[str, object]] = []

    def capture_alert(**kwargs: object) -> None:
        alerts.append(kwargs)

    strategy.clock.set_time_alert_ns = capture_alert
    monkeypatch.setattr(
        DotaMakerStrategy,
        "cache",
        property(
            lambda self: SimpleNamespace(
                instrument=lambda instrument_id: _FakeInstrument(instrument_id)
            )
        ),
        raising=False,
    )
    monkeypatch.setattr(
        DotaMakerStrategy,
        "subscribe_order_book_deltas",
        lambda _self, _instrument_id: None,
        raising=False,
    )
    strategy.on_start()
    signal = next(item for item in alerts if str(item["name"]).startswith("SIGNAL:"))
    assert signal["alert_time_ns"] == first_signal_ns


def test_on_start_rejects_empty_signal_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty signal series is a setup error, not a silent no-op strategy."""
    config = build_maker_config(
        signal_timestamps_ns=(),
        predicted_deltas=(),
        dataset_market_ps=(),
    )
    strategy, _, _ = build_maker_harness(monkeypatch, config=config)
    with pytest.raises(ValueError, match="signal timestamps must not be empty"):
        strategy.on_start()


def test_pause_clock_cancels_resting_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    """A paused feed tick cancels resting orders; the reason is paused."""
    pause_ns = 20 * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            feed_timestamps_ns=(0, pause_ns),
            signal_timestamps_ns=(0, pause_ns),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
            observed_clock=ObservedClockTape(
                game_seconds=(60, 80),
                paused=(False, True),
                terminal=(False, False),
            ),
        ),
    )
    strategy._evaluate(now_ns=0)
    accept_live(strategy)
    strategy._book_ts_ns[RADIANT_ID] = pause_ns
    strategy._book_ts_ns[DIRE_ID] = pause_ns

    strategy._evaluate(now_ns=pause_ns)

    assert live_state(strategy).awaiting_cancel is True
    assert live_state(strategy).cancel_reason == "paused"
    assert len(submitted) == BUY_LEVEL_COUNT


def test_buy_cutoff_cancels_live_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resting BUY is canceled at buy_cutoff_ns, not left on the book."""
    cutoff_ns = NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(buy_cutoff_ns=cutoff_ns)
    )
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    accept_live(strategy)

    strategy._on_buy_cutoff(SimpleNamespace(ts_event=cutoff_ns))
    assert live_state(strategy).awaiting_cancel is True
    assert live_state(strategy).cancel_reason == "cutoff"
    fire_cancel_release(strategy, cutoff_ns + CANCEL_LATENCY_NS)
    assert live in strategy._canceled
    assert len(submitted) == BUY_LEVEL_COUNT


def test_partial_buy_remainder_cancels_at_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial BUY at cutoff cancels the remainder instead of letting it rest."""
    cutoff_ns = NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(buy_cutoff_ns=cutoff_ns)
    )
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    accept_live(strategy)
    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(Decimal("2")),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=0,
        )
    )
    assert live_state(strategy).partially_filled is True
    assert strategy._position_qty == Decimal("2")

    strategy._book_ts_ns[RADIANT_ID] = cutoff_ns
    strategy._book_ts_ns[DIRE_ID] = cutoff_ns
    strategy._evaluate(now_ns=cutoff_ns)
    assert strategy._live[str(live.client_order_id)].awaiting_cancel is True
    fire_cancel_release(
        strategy, cutoff_ns + CANCEL_LATENCY_NS, client_order_id=str(live.client_order_id)
    )
    assert live in strategy._canceled
    assert len(submitted) == BUY_LEVEL_COUNT


def test_fill_during_cutoff_cancel_still_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fill during cutoff-cancel does not abort; the remainder leaves the book."""
    cutoff_ns = NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(buy_cutoff_ns=cutoff_ns)
    )
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    accept_live(strategy)

    strategy._on_buy_cutoff(SimpleNamespace(ts_event=cutoff_ns))
    assert live_state(strategy).cancel_reason == "cutoff"

    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(Decimal("2")),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=cutoff_ns + 40_000_000,
        )
    )
    assert live_state(strategy).partially_filled is True
    assert strategy._position_qty == Decimal("2")

    fire_cancel_release(strategy, cutoff_ns + CANCEL_LATENCY_NS)
    assert strategy._canceled == [live]
    assert only_live(strategy) is live


def test_cancel_ack_precedes_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reprice schedules cancel; replacement waits for OrderCanceled after release."""
    strategy, submitted, alerts = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    set_only_live(strategy, live)
    accept_live(strategy)

    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)

    assert strategy._live[str(live.client_order_id)].awaiting_cancel is True
    assert strategy._canceled == []
    assert ("CANCEL:O-1", CANCEL_LATENCY_NS) in alerts

    fire_cancel_release(
        strategy, NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=str(live.client_order_id)
    )
    assert strategy._canceled == [live]

    strategy.on_order_canceled(
        SimpleNamespace(
            ts_event=NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=live.client_order_id
        )
    )
    assert sorted(float(item.order.price) for item in strategy._live.values()) == pytest.approx(
        [0.45, 0.46, 0.47]
    )


def _reprice_tick_config(*, buy_cutoff_ns: int) -> DotaMakerConfig:
    """Two grid ticks so a book move at 1s can re-latch and reprice."""
    return build_maker_config(
        signal_timestamps_ns=(0, NS_PER_SECOND),
        predicted_deltas=(0.05, 0.05),
        dataset_market_ps=(0.50, 0.50),
        buy_cutoff_ns=buy_cutoff_ns,
    )


def test_fill_during_reprice_cancel_keeps_partial_resting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fill during deferred reprice cancel aborts the cancel so the remainder rests."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=_reprice_tick_config(buy_cutoff_ns=600 * NS_PER_SECOND)
    )
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    set_only_live(strategy, live)

    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert strategy._live[str(live.client_order_id)].awaiting_cancel is True
    assert strategy._live[str(live.client_order_id)].cancel_reason == "reprice"

    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(Decimal("2")),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=NS_PER_SECOND + 40_000_000,
        )
    )
    resting = strategy._live[str(live.client_order_id)]
    assert resting.partially_filled is True
    assert strategy._position_qty == Decimal("2")

    fire_cancel_release(
        strategy, NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=str(live.client_order_id)
    )
    assert strategy._canceled == []
    assert strategy._live[str(live.client_order_id)] is resting
    assert resting.awaiting_cancel is False


def test_fill_during_cancel_latency_updates_position(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fill that arrives while awaiting cancel still updates inventory."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    set_only_live(strategy, live)
    live_state(strategy).awaiting_cancel = True

    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(Decimal("5")),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=500_000_000,
        )
    )

    assert strategy._position_qty == Decimal("5")
    assert strategy._position_token_index == 0


def test_full_fill_uses_submit_quote_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """A full fill must keep the submit snapshot even after live context is cleared."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    submit_context = strategy._submitted[str(live.client_order_id)].context
    assert submit_context is not None
    expected_delta = submit_context.predicted_delta
    expected_fair = submit_context.fair
    expected_book = submit_context.book_p_radiant

    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(CLIP_AT_048),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=NS_PER_SECOND,
        )
    )
    assert str(live.client_order_id) not in strategy._live
    assert str(live.client_order_id) in strategy._submitted
    fills = take_records().fills
    assert len(fills) == 1
    assert fills[0].predicted_delta == pytest.approx(expected_delta)
    assert fills[0].fair == pytest.approx(expected_fair)
    assert fills[0].book_p_radiant == pytest.approx(expected_book)
    assert fills[0].order_id == str(live.client_order_id)


def test_fill_stamps_submitted_quantity_from_live_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial fill records the live order qty, not the fill qty."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(Decimal("2")),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=0,
        )
    )
    fills = take_records().fills
    assert len(fills) == 1
    assert fills[0].quantity == pytest.approx(2.0)
    assert fills[0].submitted_quantity == pytest.approx(float(live.quantity))


def test_fill_on_a_replaced_order_keeps_its_own_submitted_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fill racing a cancel must carry its own order size, not the size that filled."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    replaced = submitted[0]
    accept_live(strategy)

    strategy.on_order_canceled(
        SimpleNamespace(ts_event=NS_PER_SECOND, client_order_id=replaced.client_order_id)
    )
    assert len(submitted) == BUY_LEVEL_COUNT + 1
    assert str(replaced.client_order_id) not in strategy._live
    assert str(submitted[-1].client_order_id) in strategy._live

    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=replaced.client_order_id,
            last_qty=_FakeQuantity(Decimal("5")),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=NS_PER_SECOND,
        )
    )

    fills = take_records().fills
    assert len(fills) == 1
    assert fills[0].quantity == pytest.approx(5.0)
    assert fills[0].submitted_quantity == pytest.approx(float(CLIP_AT_048))


def test_reprice_abort_past_game_end_schedules_game_end_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reprice abort after game end must not leave a resting order on a decided game."""
    game_end_ns = 2 * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            game_end_ns=game_end_ns,
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    accept_live(strategy)

    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._book_ts_ns[RADIANT_ID] = game_end_ns - 40_000_000
    strategy._book_ts_ns[DIRE_ID] = game_end_ns - 40_000_000
    strategy._evaluate(now_ns=game_end_ns - 40_000_000)
    assert live_state(strategy).awaiting_cancel is True
    assert live_state(strategy).cancel_reason == "reprice"

    strategy._on_game_end(SimpleNamespace(ts_event=game_end_ns))
    assert live_state(strategy).cancel_reason == "game_end"

    strategy.on_order_filled(
        SimpleNamespace(
            instrument_id=RADIANT_ID,
            client_order_id=live.client_order_id,
            last_qty=_FakeQuantity(Decimal("2")),
            last_px=_FakePrice(0.48),
            is_buy=True,
            ts_event=game_end_ns + 20_000_000,
        )
    )
    assert live_state(strategy).partially_filled is True

    fire_cancel_release(strategy, game_end_ns + 45_000_000)
    assert strategy._canceled == [live]


def test_game_end_cancel_ack_does_not_submit_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel at game end must not re-arm quoting when the cancel ack arrives later."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(game_end_ns=100 * NS_PER_SECOND)
    )
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    set_only_live(strategy, live)
    accept_live(strategy)

    strategy._on_game_end(SimpleNamespace(ts_event=100 * NS_PER_SECOND))
    assert live_state(strategy).awaiting_cancel is True
    fire_cancel_release(strategy, 100 * NS_PER_SECOND + CANCEL_LATENCY_NS)
    assert strategy._canceled == [live]

    strategy.on_order_canceled(
        SimpleNamespace(
            ts_event=100 * NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=live.client_order_id
        )
    )
    assert len(submitted) == BUY_LEVEL_COUNT


def test_exit_settle_blocks_sell_until_10s(monkeypatch: pytest.MonkeyPatch) -> None:
    """SELL stays off until last BUY + 10s."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(predicted_delta=0.0, dataset_market_p=0.50),
    )
    seed_held(strategy, Decimal("5"), last_buy_ns=0)

    blocked_ns = 9 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = blocked_ns
    strategy._book_ts_ns[DIRE_ID] = blocked_ns
    strategy._evaluate(now_ns=blocked_ns)
    assert submitted == []
    assert any(event.reason == "exit_settle" for event in take_records().quote_events)

    ready_ns = 10 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = ready_ns
    strategy._book_ts_ns[DIRE_ID] = ready_ns
    strategy._evaluate(now_ns=ready_ns)
    assert len(submitted) == 1
    assert submitted[0].side == OrderSide.SELL


def test_game_end_cancels_live_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """The game-end alert schedules a cancel of any residual live order."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    set_only_live(strategy, live)
    accept_live(strategy)

    strategy._on_game_end(SimpleNamespace(ts_event=900 * NS_PER_SECOND))

    assert live_state(strategy).awaiting_cancel is True
    fire_cancel_release(strategy, 900 * NS_PER_SECOND + CANCEL_LATENCY_NS)
    assert strategy._canceled == [live]


def test_hard_cutoff_forbids_buy_and_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    """At the 1200s wall, _evaluate submits neither a new BUY nor a SELL of held size."""
    cutoff_ns = 1200 * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            buy_cutoff_ns=540 * NS_PER_SECOND,
            game_end_ns=cutoff_ns,
        ),
    )
    strategy._book_ts_ns[RADIANT_ID] = cutoff_ns
    strategy._book_ts_ns[DIRE_ID] = cutoff_ns
    strategy._evaluate(now_ns=cutoff_ns)
    assert submitted == []

    seed_held(strategy, Decimal("5"))
    strategy._evaluate(now_ns=cutoff_ns)
    assert submitted == []


def test_hard_cutoff_cancel_uses_existing_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resting SELL is cancelled at the 1200s wall with game_end latency, not replaced."""
    cutoff_ns = 1200 * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            buy_cutoff_ns=540 * NS_PER_SECOND,
            game_end_ns=cutoff_ns,
            predicted_delta=0.0,
        ),
    )
    seed_held(strategy, Decimal("5"))
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    assert live.side == OrderSide.SELL
    set_only_live(strategy, live)
    accept_live(strategy)

    strategy._on_game_end(SimpleNamespace(ts_event=cutoff_ns))
    assert live_state(strategy).awaiting_cancel is True
    assert live_state(strategy).cancel_reason == "game_end"
    assert strategy._canceled == []

    fire_cancel_release(strategy, cutoff_ns + CANCEL_LATENCY_NS)
    assert live in strategy._canceled
    assert len(submitted) == 1


def test_accept_after_cutoff_cancels_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept past buy_cutoff_ns cancels a BUY so insert latency cannot leave a GTC."""
    cutoff_ns = NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(buy_cutoff_ns=cutoff_ns)
    )
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    strategy._on_buy_cutoff(SimpleNamespace(ts_event=cutoff_ns))
    fire_cancel_release(strategy, cutoff_ns + CANCEL_LATENCY_NS)
    assert live_state(strategy).awaiting_cancel is True
    assert only_live(strategy) is live

    strategy.on_order_accepted(
        SimpleNamespace(ts_event=cutoff_ns + 100_000_000, client_order_id=live.client_order_id)
    )
    assert live_state(strategy).cancel_reason == "cutoff"
    fire_cancel_release(strategy, cutoff_ns + 100_000_000 + CANCEL_LATENCY_NS)
    assert live in strategy._canceled


def _drive_stuck_cancel_before_accept(
    strategy: DotaMakerStrategy,
    submitted: list[SimpleNamespace],
) -> SimpleNamespace:
    """Submit, reprice-cancel, fire release with no OrderCanceled — stuck GTC state."""
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert live_state(strategy).awaiting_cancel is True
    assert live_state(strategy).cancel_reason == "reprice"
    fire_cancel_release(strategy, NS_PER_SECOND + CANCEL_LATENCY_NS)
    assert live_state(strategy).cancel_released is True
    assert live_state(strategy).awaiting_cancel is True
    assert only_live(strategy) is live
    return live


def test_evaluate_after_stuck_release_does_not_reschedule_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 Hz evaluate after a pre-Accept cancel release must not fire extra cancels."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=_reprice_tick_config(buy_cutoff_ns=600 * NS_PER_SECOND)
    )
    live = _drive_stuck_cancel_before_accept(strategy, submitted)
    canceled_before = len(strategy._canceled)

    strategy._book_ts_ns[RADIANT_ID] = 2 * NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = 2 * NS_PER_SECOND
    strategy._evaluate(now_ns=2 * NS_PER_SECOND)

    assert len(strategy._canceled) == canceled_before
    assert only_live(strategy) is live
    assert live_state(strategy).awaiting_cancel is True


def test_accept_before_cutoff_reschedules_stuck_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept before cutoff re-arms cancel when cancel_order ran before the order existed."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=_reprice_tick_config(buy_cutoff_ns=600 * NS_PER_SECOND)
    )
    live = _drive_stuck_cancel_before_accept(strategy, submitted)
    first_cancels = len(strategy._canceled)

    strategy.on_order_accepted(
        SimpleNamespace(ts_event=NS_PER_SECOND + 100_000_000, client_order_id=live.client_order_id)
    )
    assert live_state(strategy).cancel_reason == "reprice"
    assert live_state(strategy).cancel_released is False
    fire_cancel_release(strategy, NS_PER_SECOND + 100_000_000 + CANCEL_LATENCY_NS)
    assert len(strategy._canceled) == first_cancels + 1
    assert strategy._canceled[-1] is live


def test_cutoff_rearms_cancel_after_stuck_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cutoff re-arms cancel when a pre-Accept cancel already released without an ack."""
    cutoff_ns = 2 * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=_reprice_tick_config(buy_cutoff_ns=cutoff_ns)
    )
    live = _drive_stuck_cancel_before_accept(strategy, submitted)

    strategy._on_buy_cutoff(SimpleNamespace(ts_event=cutoff_ns))
    assert live_state(strategy).cancel_reason == "cutoff"
    assert live_state(strategy).cancel_released is False
    fire_cancel_release(strategy, cutoff_ns + CANCEL_LATENCY_NS)
    assert strategy._canceled == []
    assert live_state(strategy).cancel_released is True

    strategy.on_order_accepted(
        SimpleNamespace(ts_event=cutoff_ns + 100_000_000, client_order_id=live.client_order_id)
    )
    fire_cancel_release(strategy, cutoff_ns + 100_000_000 + CANCEL_LATENCY_NS)
    assert strategy._canceled == [live]


def test_game_end_cancel_not_skipped_while_awaiting_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """game_end upgrades a pending cancel instead of returning because _awaiting_cancel."""
    strategy, submitted, alerts = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    for live in strategy._live.values():
        live.awaiting_cancel = True
        live.cancel_reason = "reprice"
    cancel_alerts_before = sum(1 for name, _ in alerts if name.startswith("CANCEL:"))

    strategy._on_game_end(SimpleNamespace(ts_event=900 * NS_PER_SECOND))

    assert all(live.cancel_reason == "game_end" for live in strategy._live.values())
    assert all(live.awaiting_cancel for live in strategy._live.values())
    cancel_alerts_after = sum(1 for name, _ in alerts if name.startswith("CANCEL:"))
    assert cancel_alerts_after == cancel_alerts_before
    assert submitted


def test_game_end_rearms_cancel_after_stuck_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """game_end re-arms cancel when a pre-Accept cancel already released without an ack."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=_reprice_tick_config(buy_cutoff_ns=600 * NS_PER_SECOND)
    )
    live = _drive_stuck_cancel_before_accept(strategy, submitted)

    strategy._on_game_end(SimpleNamespace(ts_event=900 * NS_PER_SECOND))
    assert live_state(strategy).cancel_reason == "game_end"
    assert live_state(strategy).cancel_released is False
    fire_cancel_release(strategy, 900 * NS_PER_SECOND + CANCEL_LATENCY_NS)
    assert strategy._canceled == []
    assert live_state(strategy).cancel_released is True

    strategy.on_order_accepted(
        SimpleNamespace(
            ts_event=900 * NS_PER_SECOND + 100_000_000, client_order_id=live.client_order_id
        )
    )
    fire_cancel_release(strategy, 900 * NS_PER_SECOND + 100_000_000 + CANCEL_LATENCY_NS)
    assert strategy._canceled == [live]


def test_live_order_ticks_count_only_while_order_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elapsed live-order ns accrue only while at least one order is posted."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    assert submitted
    assert only_live(strategy) is not None
    assert strategy._live_order_ns == 0

    strategy._live.clear()
    strategy._touch_uptime(NS_PER_SECOND)
    assert strategy._live_order_ns == NS_PER_SECOND

    strategy._touch_uptime(2 * NS_PER_SECOND)
    assert strategy._live_order_ns == NS_PER_SECOND


def test_uptime_record_is_written_at_game_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Game end flushes the open live interval into UptimeRecord seconds."""
    strategy, _, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    strategy._on_game_end(SimpleNamespace(ts_event=900 * NS_PER_SECOND))
    records = take_records()
    assert records.uptimes == (UptimeRecord(match_id=1, live_order_seconds=900),)

    clear_records()
    idle, _, _ = build_maker_harness(monkeypatch)
    idle._on_game_end(SimpleNamespace(ts_event=900 * NS_PER_SECOND))
    idle_records = take_records()
    assert idle_records.uptimes == (UptimeRecord(match_id=1, live_order_seconds=0),)


def test_usdc_affordability_blocks_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A BUY that would spend more than free USDC is skipped."""
    strategy, submitted, _ = build_maker_harness(monkeypatch, free_usdc=Decimal("1"))

    strategy._evaluate(now_ns=0)

    assert submitted == []
    records = take_records()
    assert any(event.reason == "no_cash" for event in records.quote_events)


def test_subtick_join_price_is_not_no_cash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bid that floors to 0.00 is skipped as unquotable, not labelled no_cash."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.005,
        radiant_ask=0.995,
        dire_bid=0.005,
        dire_ask=0.995,
    )

    strategy._evaluate(now_ns=0)

    assert submitted == []
    records = take_records()
    assert all(event.reason != "no_cash" for event in records.quote_events)


def test_off_grid_book_price_logs_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off-grid top-of-book logs a warning and still submits a rounded join."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.481,
        radiant_ask=0.521,
        dire_bid=0.479,
        dire_ask=0.519,
        config=build_maker_config(dataset_market_p=0.50, predicted_delta=0.05),
    )

    strategy._evaluate(now_ns=0)

    assert len(submitted) == BUY_LEVEL_COUNT
    assert float(submitted[0].price) == pytest.approx(round_buy_price(0.481))
    assert any("off-grid" in message for message in strategy._warnings)


def test_price_rounding_never_crosses() -> None:
    """BUY rounds down and SELL rounds up onto the 0.01 grid."""
    assert round_buy_price(0.481) == pytest.approx(0.48)
    assert round_sell_price(0.481) == pytest.approx(0.49)


def test_off_grid_bid_gates_on_rounded_join_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raw bid above fair still posts when floor(bid) is at or below fair."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.485,
        radiant_ask=0.515,
        dire_bid=0.485,
        dire_ask=0.515,
        config=build_maker_config(predicted_delta=MIN_ABS_DELTA, dataset_market_p=0.50),
    )

    strategy._evaluate(now_ns=0)

    assert len(submitted) == BUY_LEVEL_COUNT
    assert float(submitted[0].price) == pytest.approx(0.48)


def test_order_denied_rejected_expired_clear_live_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Denied, rejected, and expired events drop the live order so quoting can retry."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    assert len(strategy._live) == BUY_LEVEL_COUNT

    denied_id = str(submitted[-1].client_order_id)
    strategy.on_order_denied(
        SimpleNamespace(ts_event=0, reason="denied", client_order_id=denied_id)
    )
    assert denied_id not in strategy._live

    strategy._evaluate(now_ns=0)
    assert len(strategy._live) == BUY_LEVEL_COUNT
    rejected_id = str(submitted[-1].client_order_id)
    strategy.on_order_rejected(
        SimpleNamespace(ts_event=0, reason="post-only", client_order_id=rejected_id)
    )
    assert rejected_id not in strategy._live

    strategy._evaluate(now_ns=0)
    assert len(strategy._live) == BUY_LEVEL_COUNT
    expired_id = str(submitted[-1].client_order_id)
    strategy.on_order_expired(SimpleNamespace(ts_event=0, client_order_id=expired_id))
    assert expired_id not in strategy._live
    assert len(strategy._live) == BUY_LEVEL_COUNT - 1


def test_submit_failure_rolls_back_live_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising submit_order clears live state so the next tick can try again."""
    strategy, submitted, alerts = build_maker_harness(monkeypatch)

    def raise_on_submit(_order: object) -> None:
        raise RuntimeError("venue down")

    strategy.submit_order = raise_on_submit
    with pytest.raises(RuntimeError, match="venue down"):
        strategy._evaluate(now_ns=0)
    assert only_live(strategy) is None
    assert strategy._submitted == {}
    assert submitted == []
    assert alerts == []


def test_stale_book_blocks_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Books older than the 5s gate cancel/no-quote instead of joining."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._book_ts_ns[RADIANT_ID] = 0
    strategy._book_ts_ns[DIRE_ID] = 0
    strategy._evaluate(now_ns=10 * NS_PER_SECOND)
    assert submitted == []
    records = take_records()
    assert any(event.reason == "stale_book" for event in records.quote_events)


def test_pair_tolerance_blocks_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Broken complementary mids cancel/no-quote with pair_tolerance."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.80,
        radiant_ask=0.82,
        dire_bid=0.80,
        dire_ask=0.82,
    )
    strategy._evaluate(now_ns=0)
    assert submitted == []
    records = take_records()
    assert any(event.reason == "pair_tolerance" for event in records.quote_events)


def test_grid_book_move_reanchors_latched_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLOB movement between model ticks re-anchors fair and reprices the BUY."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(signal_timestamps_ns=(0,), feed_timestamps_ns=(0,))
    )
    strategy._evaluate(now_ns=0)
    assert len(submitted) == BUY_LEVEL_COUNT
    accept_all(strategy)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.52, ask=0.56)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.44, ask=0.48)
    now_ns = 5 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    assert strategy._latched is not None
    assert strategy._latched.fair_radiant == pytest.approx(0.59)
    assert len(submitted) == BUY_LEVEL_COUNT
    assert all(live.awaiting_cancel for live in strategy._live.values())
    assert {live.cancel_reason for live in strategy._live.values()} == {"reprice"}
    for client_order_id in list(strategy._live):
        fire_cancel_release(strategy, now_ns, client_order_id=client_order_id)
        strategy.on_order_canceled(
            SimpleNamespace(ts_event=now_ns, client_order_id=client_order_id)
        )
    repriced = buy_orders(submitted[BUY_LEVEL_COUNT:])
    assert [float(order.price) for order in repriced] == pytest.approx([0.52, 0.51, 0.50])


def test_grid_book_move_reanchors_fair_to_live_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-anchor applies the held delta once on the live mid; fair_ts keeps the tick."""
    strategy, _, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(signal_timestamps_ns=(0,), predicted_delta=0.05)
    )
    strategy._evaluate(now_ns=0)
    assert strategy._latched is not None
    assert strategy._latched.fair_radiant == pytest.approx(0.55)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.60, ask=0.64)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.36, ask=0.40)
    now_ns = 4 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    assert strategy._latched is not None
    assert strategy._latched.fair_radiant == pytest.approx(0.67)
    assert strategy._latched.predicted_delta == pytest.approx(0.05)
    assert strategy._latched.fair_ts_ns == 0


def test_grid_new_model_tick_replaces_fair_side_and_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh model tick rebuilds fair, BUY token, and BUY price from the current book."""
    second = 8 * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, second),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.42),
        ),
    )
    strategy._evaluate(now_ns=0)
    first = submitted[0]
    set_only_live(strategy, first)
    accept_live(strategy)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.40, ask=0.44)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.56, ask=0.60)
    strategy._book_ts_ns[RADIANT_ID] = second
    strategy._book_ts_ns[DIRE_ID] = second
    strategy._evaluate(now_ns=second)
    assert strategy._latched is not None
    assert strategy._latched.fair_radiant == pytest.approx(0.47)
    assert strategy._latched.buy_targets
    assert strategy._latched.buy_targets[0].price == pytest.approx(0.40)


def test_grid_sell_uses_current_ask_against_reanchored_fair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An open position SELL joins the live ask gated on the re-anchored fair."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_ask=0.52,
        config=build_maker_config(predicted_delta=0.05),
    )
    strategy._evaluate(now_ns=0)
    assert strategy._latched is not None
    seed_held(strategy, Decimal("5"))
    strategy._live.clear()
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.54, ask=0.56)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.44, ask=0.46)
    now_ns = 3 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    sell = submitted[-1]
    assert sell.side == OrderSide.SELL
    assert strategy._latched.fair_radiant == pytest.approx(0.60)
    assert float(sell.price) == pytest.approx(0.60)
    assert float(sell.price) + 1e-12 >= strategy._latched.fair_radiant


def test_sell_jumper_below_fair_reprices_to_ceil_fair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-anchor lifts fair to 0.6275; the resting 0.60 SELL reprices to 0.63."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(predicted_delta=0.0925),
    )
    strategy._evaluate(now_ns=0)
    assert strategy._latched is not None
    assert strategy._latched.fair_radiant == pytest.approx(0.5925)
    seed_held(strategy, Decimal("152"))
    strategy._live.clear()
    now_ns = 2 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    sell = next(order for order in submitted if order.side == OrderSide.SELL)
    assert float(sell.price) == pytest.approx(0.60)
    set_only_live(strategy, sell)
    accept_live(strategy)
    later = 3 * NS_PER_SECOND
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.48, ask=0.59)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.41, ask=0.52)
    strategy._book_ts_ns[RADIANT_ID] = later
    strategy._book_ts_ns[DIRE_ID] = later
    strategy._evaluate(now_ns=later)
    assert strategy._latched.fair_radiant == pytest.approx(0.6275)
    assert live_state(strategy).awaiting_cancel is True
    assert live_state(strategy).cancel_reason == "reprice"
    fire_cancel_release(strategy, later)
    strategy.on_order_canceled(
        SimpleNamespace(ts_event=later, client_order_id=str(sell.client_order_id))
    )
    assert float(only_live(strategy).price) == pytest.approx(0.63)


def test_grid_book_drift_reprices_without_anchor_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drift past the anchor band between ticks reprices the BUY; anchor stays tick-only."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch, config=build_maker_config(dataset_market_p=0.50)
    )
    strategy._evaluate(now_ns=0)
    set_only_live(strategy, submitted[0])
    accept_live(strategy)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.56, ask=0.60)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.40, ask=0.44)
    now_ns = 2 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    assert strategy._latched is not None
    assert strategy._latched.fair_radiant == pytest.approx(0.63)
    assert live_state(strategy).awaiting_cancel is True
    assert live_state(strategy).cancel_reason == "reprice"


def test_grid_entry_gates_reopen_on_reanchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spread block on the model tick re-evaluates on re-anchor once the book tightens."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.48,
        radiant_ask=0.56,
        dire_bid=0.48,
        dire_ask=0.56,
        config=build_maker_config(),
    )
    strategy._evaluate(now_ns=0)
    assert submitted == []
    assert strategy._latched is not None
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.48, ask=0.52)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.48, ask=0.52)
    now_ns = 3 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    buys = buy_orders(submitted)
    assert [float(order.price) for order in buys] == pytest.approx([0.48, 0.47, 0.46])


def test_grid_entry_timeout_pulls_buy_keeps_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry timeout cancels BUY and keeps fair, matching live feed-timeout."""
    strategy, submitted, _ = build_maker_harness(monkeypatch, config=build_maker_config())
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    set_only_live(strategy, live)
    accept_live(strategy)
    now_ns = 17 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    assert strategy._latched is not None
    assert live_state(strategy).awaiting_cancel is True
    assert live_state(strategy).cancel_reason == "stale_signal"


def test_grid_entry_timeout_keeps_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry timeout leaves a SELL; 16s does not pull it as stale."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_ask=0.56,
        config=build_maker_config(predicted_delta=0.05),
    )
    strategy._evaluate(now_ns=0)
    seed_held(strategy, Decimal("5"))
    strategy._live.clear()
    now_ns = 3 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    sell = submitted[-1]
    assert sell.side == OrderSide.SELL
    set_only_live(strategy, sell)
    accept_live(strategy)
    later = 17 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = later
    strategy._book_ts_ns[DIRE_ID] = later
    strategy._evaluate(now_ns=later)
    assert strategy._latched is not None
    live = only_live(strategy)
    assert live is not None
    assert live.side == OrderSide.SELL
    if live is sell and live_state(strategy).awaiting_cancel:
        assert live_state(strategy).cancel_reason == "reprice"


def test_grid_exit_timeout_clears_latch_and_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    """v5: exit timeout drops the latch and pulls a resting SELL."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_ask=0.56,
        config=build_maker_config(predicted_delta=0.05),
    )
    strategy._evaluate(now_ns=0)
    seed_held(strategy, Decimal("5"))
    strategy._live.clear()
    now_ns = 3 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    sell = submitted[-1]
    set_only_live(strategy, sell)
    accept_live(strategy)
    later = int(EXIT_FEED_STALE_SECONDS + 1) * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = later
    strategy._book_ts_ns[DIRE_ID] = later
    strategy._evaluate(now_ns=later)
    assert strategy._latched is None
    assert live_state(strategy).awaiting_cancel is True
    assert live_state(strategy).cancel_reason == "stale_signal"


def test_grid_first_resumed_tick_is_recovery_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """v5: the first feed tick after exit timeout updates freshness but does not latch a decision."""
    resume = int(EXIT_FEED_STALE_SECONDS + 5) * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0,),
            feed_timestamps_ns=(0, resume),
        ),
    )
    strategy._evaluate(now_ns=0)
    if submitted:
        accept_live(strategy)
        strategy._live.clear()
    now_ns = int(EXIT_FEED_STALE_SECONDS + 1) * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    assert strategy._latched is None
    strategy._book_ts_ns[RADIANT_ID] = resume
    strategy._book_ts_ns[DIRE_ID] = resume
    strategy._evaluate(now_ns=resume)
    assert strategy._latched is None
    assert strategy._last_synced_signal_ts == resume
    assert strategy._core.signal is None


def test_grid_next_fresh_tick_restores_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """v5: the model tick after a recovery-stale feed rebuilds the BUY quote."""
    stale = int(EXIT_FEED_STALE_SECONDS + 5) * NS_PER_SECOND
    fresh = int(EXIT_FEED_STALE_SECONDS + 13) * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, fresh),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
            feed_timestamps_ns=(0, stale, fresh),
        ),
    )
    strategy._evaluate(now_ns=0)
    strategy._live.clear()
    now_ns = int(EXIT_FEED_STALE_SECONDS + 1) * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    strategy._book_ts_ns[RADIANT_ID] = stale
    strategy._book_ts_ns[DIRE_ID] = stale
    strategy._evaluate(now_ns=stale)
    assert strategy._latched is None
    strategy._book_ts_ns[RADIANT_ID] = fresh
    strategy._book_ts_ns[DIRE_ID] = fresh
    strategy._evaluate(now_ns=fresh)
    assert strategy._latched is not None
    assert strategy._latched.buy_targets
    assert submitted[-1].side == OrderSide.BUY


def _layer_qty(price: float) -> Decimal:
    return Decimal(str(buy_share_quantity(base_size_usdc=BASE_SIZE_USDC, price=price)))


def test_follow300_posts_three_initial_ladder_buys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bid 0.48 posts 0.48/0.47/0.46, each a full $100 rung."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    buys = buy_orders(submitted)
    assert [float(order.price) for order in buys] == pytest.approx([0.48, 0.47, 0.46])
    assert buys[0].quantity.as_decimal() == _layer_qty(0.48)
    assert buys[1].quantity.as_decimal() == _layer_qty(0.47)
    assert buys[2].quantity.as_decimal() == _layer_qty(0.46)
    assert len(strategy._live) == BUY_LEVEL_COUNT
    assert_ladder_invariants(strategy)


def test_a_second_evaluate_does_not_duplicate_a_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-evaluating on the same book keeps one live order per logical rung."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    assert len(submitted) == BUY_LEVEL_COUNT
    strategy._evaluate(now_ns=0)
    assert len(submitted) == BUY_LEVEL_COUNT
    assert len(strategy._live) == BUY_LEVEL_COUNT
    assert_ladder_invariants(strategy)


def test_book_move_before_first_fill_keeps_the_latched_rung_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A book-only tick before the first fill must not reprice any rung."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(signal_timestamps_ns=(0,), feed_timestamps_ns=(0,)),
    )
    strategy._evaluate(now_ns=0)
    assert len(submitted) == BUY_LEVEL_COUNT
    accept_live(strategy)
    latched = {
        str(live.order.client_order_id): float(live.order.price) for live in strategy._live.values()
    }
    assert sorted(latched.values()) == pytest.approx([0.46, 0.47, 0.48])
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.40, ask=0.44)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.56, ask=0.60)
    now_ns = 5 * NS_PER_SECOND
    strategy._book_ts_ns[RADIANT_ID] = now_ns
    strategy._book_ts_ns[DIRE_ID] = now_ns
    strategy._evaluate(now_ns=now_ns)
    assert len(submitted) == BUY_LEVEL_COUNT
    after = {
        str(live.order.client_order_id): float(live.order.price) for live in strategy._live.values()
    }
    assert after == pytest.approx(latched)


def test_late_fill_on_replaced_order_does_not_complete_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fill on a canceled order after its replacement is live must not mark the rung done."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top = buy_orders(submitted)[0]
    accept_all(strategy)
    fill_order(strategy, top, Decimal("20"), 0.48, is_buy=True, ts_event=0)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.49, ask=0.53)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    fire_cancel_release(
        strategy, NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=str(top.client_order_id)
    )
    strategy.on_order_canceled(
        SimpleNamespace(
            ts_event=NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=top.client_order_id
        )
    )
    replacement = [order for order in submitted if abs(float(order.price) - 0.47) < 0.005]
    assert replacement
    fill_order(strategy, top, Decimal("10"), 0.48, is_buy=True, ts_event=NS_PER_SECOND + 90_000_000)
    assert strategy._buy_levels[0].done is False
    assert str(replacement[-1].client_order_id) in strategy._live


def test_cutoff_after_cancel_sent_does_not_duplicate_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once cancel_order is out, cutoff upgrades the reason and does not arm another CANCEL."""
    strategy, submitted, alerts = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    accept_live(strategy)
    strategy._cancel_live_buys(reason="reprice")
    fire_cancel_release(strategy, CANCEL_LATENCY_NS, client_order_id=str(live.client_order_id))
    assert live_state(strategy).cancel_released is True
    assert live_state(strategy).accepted is True
    cancel_before = sum(1 for name, _ in alerts if name.startswith("CANCEL:"))
    strategy._on_buy_cutoff(SimpleNamespace(ts_event=600 * NS_PER_SECOND))
    cancel_after = sum(1 for name, _ in alerts if name.startswith("CANCEL:"))
    assert cancel_after == cancel_before
    assert live_state(strategy).cancel_reason == "cutoff"


def test_cancel_release_defers_venue_cancel_to_unique_ns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cancel_order waits for CANCEL_SEND so Nautilus cannot heap-compare it with SubmitOrder."""
    strategy, submitted, alerts = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    live = submitted[0]
    accept_live(strategy)
    strategy._cancel_live_buys(reason="reprice")
    strategy._on_cancel_release(
        SimpleNamespace(ts_event=CANCEL_LATENCY_NS, name=f"CANCEL:{live.client_order_id}")
    )
    assert strategy._canceled == []
    send = [row for row in alerts if row[0] == f"CANCEL_SEND:{live.client_order_id}"]
    assert len(send) == 1
    assert send[0][1] == CANCEL_LATENCY_NS + 1
    strategy._on_cancel_send(
        SimpleNamespace(ts_event=send[0][1], name=f"CANCEL_SEND:{live.client_order_id}")
    )
    assert strategy._canceled == [live]


def test_cancel_send_on_zero_leaf_sell_acks_and_requotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sell the venue already drained self-acks on send: no cancel_order, slot freed, resold."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(predicted_delta=0.009),
    )
    seed_held(strategy, Decimal("50"))
    strategy._evaluate(now_ns=0)
    sell = submitted[0]
    accept_live(strategy)
    live = live_state(strategy)
    live.filled_qty = sell.quantity.as_decimal()
    live.partially_filled = True
    strategy._mark_and_cancel(live, reason="reprice")
    fire_cancel_release(strategy, CANCEL_LATENCY_NS, client_order_id=str(sell.client_order_id))
    assert strategy._canceled == []
    assert str(sell.client_order_id) not in strategy._live
    core_id = strategy._venue_to_core[str(sell.client_order_id)]
    assert all(order.order_id != core_id for order in strategy._core.orders)
    assert len(submitted) == 2
    replacement = submitted[-1]
    assert replacement.side == OrderSide.SELL
    assert replacement.quantity.as_decimal() == Decimal("50")


def test_partial_fill_follows_the_book_and_a_full_fill_completes_the_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial fill opens the follow path; a full fill marks that logical rung done."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top, mid, _bottom = buy_orders(submitted)
    accept_all(strategy)
    fill_order(strategy, mid, Decimal("5"), 0.47, is_buy=True, ts_event=0)
    assert strategy._episode_has_buy_fill is True
    assert strategy._buy_levels[1].done is False
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.50, ask=0.52)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.48, ask=0.50)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    open_rung_prices = sorted(
        level.price for level in strategy._buy_levels.values() if not level.done
    )
    assert open_rung_prices == pytest.approx([0.48, 0.49, 0.50])
    assert strategy._live[str(top.client_order_id)].level_index == BUY_LEVEL_COUNT - 1
    for live in list(strategy._live.values()):
        if not live.awaiting_cancel:
            continue
        oid = str(live.order.client_order_id)
        fire_cancel_release(strategy, NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=oid)
        strategy.on_order_canceled(
            SimpleNamespace(
                ts_event=NS_PER_SECOND + CANCEL_LATENCY_NS,
                client_order_id=live.order.client_order_id,
            )
        )
    on_top_rung = next(live for live in strategy._live.values() if live.level_index == 0)
    top_order = on_top_rung.order
    fill_order(
        strategy,
        top_order,
        top_order.quantity.as_decimal(),
        float(top_order.price),
        is_buy=True,
        ts_event=NS_PER_SECOND,
    )
    assert strategy._buy_levels[0].done is True
    assert str(top_order.client_order_id) not in strategy._live
    assert_ladder_invariants(strategy)


def test_after_top_fill_the_filled_rung_refills_and_ladder_follows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Top fill + bid 0.47: rungs aim 0.47/0.46/0.45 — the 0.47 order refills the done rung."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    top, mid, bottom = buy_orders(submitted)
    accept_all(strategy)
    fill_order(strategy, top, top.quantity.as_decimal(), 0.48, is_buy=True, ts_event=0)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.50)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert strategy._live[str(mid.client_order_id)].level_index == 0
    assert strategy._live[str(bottom.client_order_id)].level_index == 1
    assert not [live for live in strategy._live.values() if live.awaiting_cancel]
    live_prices = sorted(float(live.order.price) for live in strategy._live.values())
    assert live_prices == pytest.approx([0.45, 0.46, 0.47])
    assert_ladder_invariants(strategy)


def test_partial_cancel_replace_requotes_the_full_rung(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reattachments keep their queue, and the freed rung quotes a fresh full clip."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top, _mid, _bottom = buy_orders(submitted)
    accept_all(strategy)
    fill_order(strategy, top, Decimal("40"), 0.48, is_buy=True, ts_event=0)
    incomplete_before = sum(1 for level in strategy._buy_levels.values() if not level.done)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.49, ask=0.53)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    fire_cancel_release(
        strategy, NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=str(top.client_order_id)
    )
    strategy.on_order_canceled(
        SimpleNamespace(
            ts_event=NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=top.client_order_id
        )
    )
    replacement = buy_orders(submitted)[BUY_LEVEL_COUNT:]
    assert len(replacement) == 1
    replaced = replacement[0]
    assert float(replaced.price) == pytest.approx(0.45)
    assert replaced.quantity.as_decimal() == Decimal("222.22")
    reserved = remaining_buy_notional(strategy._live)
    assert reserved <= Decimal(str(BACKTEST_DOTA_MAX_POSITION_LEVELS * 100.0))
    incomplete_after = sum(1 for level in strategy._buy_levels.values() if not level.done)
    assert incomplete_after == incomplete_before


def test_shared_position_cap_stops_refills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fills spend cap room; refills fit until the map cap leaves less than a rung."""
    cap_wakes = BACKTEST_DOTA_MAX_POSITION_LEVELS // BUY_LEVEL_COUNT
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=tuple(i * NS_PER_SECOND for i in range(cap_wakes + 1)),
            predicted_deltas=(0.05,) * (cap_wakes + 1),
            dataset_market_ps=(0.50,) * (cap_wakes + 1),
        ),
    )
    strategy._evaluate(now_ns=0)
    accept_all(strategy)
    for wake in range(cap_wakes):
        batch = submitted[wake * BUY_LEVEL_COUNT :]
        assert len(batch) == BUY_LEVEL_COUNT
        for order in batch:
            assert order.side == OrderSide.BUY
            fill_order(
                strategy,
                order,
                order.quantity.as_decimal(),
                float(order.price),
                is_buy=True,
                ts_event=wake * NS_PER_SECOND,
            )
        strategy._evaluate(now_ns=(wake + 1) * NS_PER_SECOND)
        accept_all(strategy)
    assert submitted[cap_wakes * BUY_LEVEL_COUNT :] == []
    assert strategy._core.budget.cap_room_usdc < strategy._policy.level_usdc


def test_fill_during_cancel_and_repeat_cancel_and_reject_keep_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Racing fill, cancel-before-accept, repeat cancel, and reject do not double-reserve."""
    strategy, submitted, alerts = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top = buy_orders(submitted)[0]
    oid = str(top.client_order_id)
    assert oid in strategy._submitted
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    cancel_alerts = [name for name, _ in alerts if name == f"CANCEL:{oid}"]
    assert len(cancel_alerts) == 1
    strategy._on_buy_cutoff(SimpleNamespace(ts_event=NS_PER_SECOND))
    cancel_alerts = [name for name, _ in alerts if name == f"CANCEL:{oid}"]
    assert len(cancel_alerts) == 1
    fill_order(strategy, top, Decimal("5"), 0.48, is_buy=True, ts_event=NS_PER_SECOND + 40_000_000)
    assert oid in strategy._submitted
    fire_cancel_release(strategy, NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=oid)
    assert oid in strategy._live
    strategy2, submitted2, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(),
    )
    strategy2._evaluate(now_ns=0)
    first = buy_orders(submitted2)[0]
    strategy2._cancel_live_buys(reason="reprice")
    fire_cancel_release(strategy2, CANCEL_LATENCY_NS, client_order_id=str(first.client_order_id))
    assert strategy2._live[str(first.client_order_id)].cancel_released is True
    reserved_before = remaining_buy_notional(strategy2._live)
    strategy2.on_order_rejected(
        SimpleNamespace(ts_event=0, reason="post-only", client_order_id=first.client_order_id)
    )
    assert str(first.client_order_id) not in strategy2._live
    assert str(first.client_order_id) in strategy2._submitted
    assert remaining_buy_notional(strategy2._live) < reserved_before


def test_sell_flatten_waits_for_buy_cancels_and_late_fill_keeps_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flattening SELL does not reset the episode until BUY cancels confirm; a late fill keeps token."""
    sell_ns = 11 * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.48,
        radiant_ask=0.53,
        dire_bid=0.48,
        dire_ask=0.53,
        config=build_maker_config(
            signal_timestamps_ns=(0, sell_ns),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top, mid, _bottom = buy_orders(submitted)
    accept_all(strategy)
    fill_order(strategy, top, top.quantity.as_decimal(), 0.48, is_buy=True, ts_event=0)
    token = strategy._episode_token_index
    strategy._book_ts_ns[RADIANT_ID] = sell_ns
    strategy._book_ts_ns[DIRE_ID] = sell_ns
    strategy._evaluate(now_ns=sell_ns)
    sells = [order for order in submitted if order.side == OrderSide.SELL]
    assert sells
    sell = sells[-1]
    accept_live(strategy, ts_event=sell_ns, client_order_id=str(sell.client_order_id))
    fill_order(
        strategy,
        sell,
        sell.quantity.as_decimal(),
        float(sell.price),
        is_buy=False,
        ts_event=sell_ns,
    )
    assert strategy._position_qty == Decimal("0")
    assert strategy._winding_down is True
    assert strategy._episode_token_index == token
    assert str(mid.client_order_id) in strategy._live
    fill_order(strategy, mid, Decimal("8"), 0.46, is_buy=True, ts_event=sell_ns + 1)
    assert strategy._position_qty == Decimal("8")
    assert strategy._episode_token_index == token
    assert strategy._winding_down is False


def test_signal_flip_does_not_buy_opposite_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signal flip with position or a canceling BUY never posts the other token."""
    second = NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, second),
            predicted_deltas=(0.05, -0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top = buy_orders(submitted)[0]
    accept_all(strategy)
    fill_order(strategy, top, Decimal("20"), 0.48, is_buy=True, ts_event=0)
    strategy._book_ts_ns[RADIANT_ID] = second
    strategy._book_ts_ns[DIRE_ID] = second
    strategy._evaluate(now_ns=second)
    dire_buys = [
        order
        for order in submitted
        if order.side == OrderSide.BUY and order.instrument_id == DIRE_ID
    ]
    assert dire_buys == []
    assert strategy._position_token_index == 0


def test_extra_buy_while_sell_live_updates_settle_and_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An extra BUY fill restarts settle and grows inventory while a SELL is outstanding."""
    sell_ns = 11 * NS_PER_SECOND
    extra_ns = 12 * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.48,
        radiant_ask=0.53,
        dire_bid=0.48,
        dire_ask=0.53,
        config=build_maker_config(
            signal_timestamps_ns=(0, sell_ns, extra_ns),
            predicted_deltas=(0.05, 0.05, 0.05),
            dataset_market_ps=(0.50, 0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top, mid, _bottom = buy_orders(submitted)
    accept_all(strategy)
    fill_order(strategy, top, Decimal("40"), 0.48, is_buy=True, ts_event=0)
    strategy._book_ts_ns[RADIANT_ID] = sell_ns
    strategy._book_ts_ns[DIRE_ID] = sell_ns
    strategy._evaluate(now_ns=sell_ns)
    sell = [order for order in submitted if order.side == OrderSide.SELL][-1]
    assert sell.quantity.as_decimal() == Decimal("40")
    accept_live(strategy, ts_event=sell_ns, client_order_id=str(sell.client_order_id))
    fill_order(strategy, mid, Decimal("10"), 0.46, is_buy=True, ts_event=extra_ns)
    assert strategy._position_qty == Decimal("50")
    assert strategy._last_buy_ns == extra_ns
    strategy._book_ts_ns[RADIANT_ID] = extra_ns
    strategy._book_ts_ns[DIRE_ID] = extra_ns
    strategy._evaluate(now_ns=extra_ns)
    live_sells = [live for live in strategy._live.values() if live.order.side == OrderSide.SELL]
    assert live_sells
    leftover = live_sells[0]
    assert remaining_qty(leftover) <= strategy._position_qty
    assert leftover.awaiting_cancel is True


def test_cancel_without_venue_order_frees_the_sell_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(predicted_delta=0.009),
    )
    seed_held(strategy, Decimal("50"))
    strategy._evaluate(now_ns=0)
    sell = submitted[0]
    accept_live(strategy)
    core_id = strategy._venue_to_core[str(sell.client_order_id)]
    strategy._detach_live(str(sell.client_order_id))
    strategy._core = mark_canceling(state=strategy._core, order_id=core_id, reason="reprice")
    strategy._execute_plan(
        Plan(
            keep=(),
            moves=(),
            cancels=(CancelOrder(order_id=core_id, reason="reprice"),),
            places=(),
            block_reason="",
        ),
        now_ns=0,
    )
    assert not any(order.side == "SELL" for order in strategy._core.orders)


def test_cancel_of_filled_venue_order_frees_the_sell_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(predicted_delta=0.009),
    )
    seed_held(strategy, Decimal("50"))
    strategy._evaluate(now_ns=0)
    sell = submitted[0]
    accept_live(strategy)
    sell.is_closed = True
    core_id = strategy._venue_to_core[str(sell.client_order_id)]
    strategy._core = mark_canceling(state=strategy._core, order_id=core_id, reason="reprice")
    strategy._execute_plan(
        Plan(
            keep=(),
            moves=(),
            cancels=(CancelOrder(order_id=core_id, reason="reprice"),),
            places=(),
            block_reason="",
        ),
        now_ns=0,
    )
    assert not any(order.side == "SELL" for order in strategy._core.orders)
    assert strategy._canceled == []


def test_cutoff_and_lost_buy_gate_still_service_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cutoff / lost BUY gate cancel BUYs without blocking SELL."""
    cutoff_ns = 11 * NS_PER_SECOND
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.48,
        radiant_ask=0.53,
        dire_bid=0.48,
        dire_ask=0.53,
        config=build_maker_config(
            buy_cutoff_ns=cutoff_ns,
            signal_timestamps_ns=(0, cutoff_ns),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top = buy_orders(submitted)[0]
    accept_all(strategy)
    fill_order(strategy, top, Decimal("40"), 0.48, is_buy=True, ts_event=0)
    strategy._book_ts_ns[RADIANT_ID] = cutoff_ns
    strategy._book_ts_ns[DIRE_ID] = cutoff_ns
    strategy._evaluate(now_ns=cutoff_ns)
    assert any(
        live.awaiting_cancel and live.order.side == OrderSide.BUY
        for live in strategy._live.values()
    )
    sells = [order for order in submitted if order.side == OrderSide.SELL]
    assert sells
    assert sells[-1].quantity.as_decimal() == Decimal("40")


def test_dust_remainder_does_not_block_other_buys_or_game_end_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dust remainder rests that order only; other rungs and game_end cancel still run."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top, mid, bottom = buy_orders(submitted)
    accept_all(strategy)
    fill_order(strategy, top, Decimal("2"), 0.48, is_buy=True, ts_event=0)
    assert strategy._position_qty == Decimal("2")
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert str(top.client_order_id) in strategy._live
    assert strategy._live[str(top.client_order_id)].awaiting_cancel is False
    strategy._on_game_end(SimpleNamespace(ts_event=900 * NS_PER_SECOND))
    assert all(live.awaiting_cancel for live in strategy._live.values())
    fire_cancel_release(
        strategy, 900 * NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=str(top.client_order_id)
    )
    assert top in strategy._canceled
    _ = (mid, bottom)


def test_queue_ahead_is_per_level_not_copied_from_tob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deep rungs record size at their own price; a missing off-TOB level is unavailable, not 0."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(),
    )
    strategy._books[RADIANT_ID] = _FakeBook(
        bid=0.48,
        ask=0.52,
        bid_levels=((0.48, 20.0), (0.47, 50.0), (0.46, 10.0)),
    )
    strategy._evaluate(now_ns=0)
    buys = buy_orders(submitted)
    ahead = [strategy._submitted[str(order.client_order_id)].context.queue_ahead for order in buys]
    assert ahead == pytest.approx([20.0, 50.0, 10.0])
    unknown = volume_at_price(
        SimpleNamespace(best_bid_price=lambda: 0.48, best_bid_size=lambda: 20.0),
        0.44,
        is_buy=True,
    )
    assert isnan(unknown)
    empty_book = _FakeBook(bid=0.48, ask=0.52, bid_levels=((0.48, 20.0),))
    assert volume_at_price(empty_book, 0.44, is_buy=True) == 0.0


def test_shared_balance_does_not_double_spend_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """$150 free with three $100 rungs posts one BUY; the next rungs see the reserve."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        free_usdc=Decimal("150"),
        config=build_maker_config(),
    )
    strategy._evaluate(now_ns=0)
    buys = buy_orders(submitted)
    assert len(buys) == 1
    reserved = remaining_buy_notional(strategy._live)
    assert reserved <= Decimal("150")
    assert reserved > Decimal("90")


def test_full_fill_after_reattach_completes_current_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reused order completes the level it holds now, not the level it was submitted for."""
    config = build_maker_config()
    strategy, submitted, _ = build_maker_harness(monkeypatch, config=config)
    strategy._evaluate(now_ns=0)
    accept_all(strategy)
    initial = buy_orders(submitted)
    bottom = initial[2]
    fill_order(
        strategy,
        bottom,
        bottom.quantity.as_decimal(),
        0.46,
        is_buy=True,
        ts_event=0,
    )
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._evaluate(now_ns=NS_PER_SECOND)
    moved = initial[1]
    moved_id = str(moved.client_order_id)
    assert strategy._live[moved_id].level_index == 0
    fill_order(
        strategy,
        moved,
        moved.quantity.as_decimal(),
        0.47,
        is_buy=True,
        ts_event=2 * NS_PER_SECOND,
    )
    assert strategy._buy_levels[0].done
    assert not strategy._buy_levels[1].done


def test_partial_fill_before_reattach_does_not_complete_a_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial fill leaves the rung open; the later move carries the partial with it."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(),
    )
    strategy._evaluate(now_ns=0)
    accept_all(strategy)
    initial = buy_orders(submitted)
    middle = initial[1]
    fill_order(strategy, middle, Decimal("5"), 0.47, is_buy=True, ts_event=0)
    assert not strategy._buy_levels[1].done
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._evaluate(now_ns=NS_PER_SECOND)
    moved_id = str(middle.client_order_id)
    assert strategy._live[moved_id].level_index == 0
    assert not strategy._buy_levels[0].done


def test_canceling_order_still_reserves_after_a_reattach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A BUY that lost its rung to a reattach keeps reserving its own remaining notional."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(),
    )
    strategy._evaluate(now_ns=0)
    accept_all(strategy)
    initial = buy_orders(submitted)
    bottom = initial[2]
    fill_order(strategy, bottom, bottom.quantity.as_decimal(), 0.46, is_buy=True, ts_event=0)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._evaluate(now_ns=NS_PER_SECOND)
    top_id = str(initial[0].client_order_id)
    assert strategy._live[top_id].awaiting_cancel is True
    reserved = remaining_buy_notional(strategy._live)
    assert reserved >= remaining_qty(strategy._live[top_id]) * Decimal("0.48")


def test_fill_after_cancel_ack_completes_the_level_it_last_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fill that lands after the cancel ack uses the assignment saved before the drop."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    accept_all(strategy)
    initial = buy_orders(submitted)
    top = initial[0]
    top_id = str(top.client_order_id)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.49, ask=0.53)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert strategy._live[top_id].awaiting_cancel is True
    fire_cancel_release(strategy, NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=top_id)
    strategy.on_order_canceled(
        SimpleNamespace(
            ts_event=NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=top.client_order_id
        )
    )
    assert top_id not in strategy._live
    fill_order(
        strategy,
        top,
        top.quantity.as_decimal(),
        0.48,
        is_buy=True,
        ts_event=NS_PER_SECOND + 90_000_000,
    )
    assert strategy._position_qty == top.quantity.as_decimal()


def test_anchor_failure_blocks_new_buys_while_long_on_the_other_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed anchor must not turn a missing fair into fair 1.0 for the Dire token."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(-0.05, -0.05),
            dataset_market_ps=(0.50, 0.20),
            mid_spike_enabled=False,
        ),
    )
    strategy._evaluate(now_ns=0)
    accept_all(strategy)
    dire_top = buy_orders(submitted)[0]
    assert dire_top.instrument_id == DIRE_ID
    fill_order(strategy, dire_top, Decimal("5"), 0.48, is_buy=True, ts_event=0)
    buys_before = len(buy_orders(submitted))
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.10, ask=0.14)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.86, ask=0.90)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert len(buy_orders(submitted)) == buys_before
    reasons = {
        live.cancel_reason for live in strategy._live.values() if live.order.side == OrderSide.BUY
    }
    assert reasons == {"anchor"}
    events = take_records().quote_events
    assert any(event.kind == "no_quote" and event.reason == "anchor" for event in events)


def test_anchor_failure_cancels_existing_buy_with_anchor_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A BUY placed while the anchor held is canceled with reason anchor on the failing tick."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.20),
        ),
    )
    strategy._evaluate(now_ns=0)
    accept_all(strategy)
    top = buy_orders(submitted)[0]
    fill_order(strategy, top, Decimal("5"), 0.48, is_buy=True, ts_event=0)
    strategy._evaluate(now_ns=NS_PER_SECOND)
    reasons = {
        live.cancel_reason for live in strategy._live.values() if live.order.side == OrderSide.BUY
    }
    assert reasons == {"anchor"}


def test_anchor_failure_does_not_sell_on_a_fabricated_fair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v5: with no valid fair the failed anchor places no SELL instead of quoting fair 0 or 1."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        radiant_bid=0.10,
        radiant_ask=0.14,
        dire_bid=0.86,
        dire_ask=0.90,
        config=build_maker_config(
            signal_timestamps_ns=(0,),
            predicted_deltas=(0.05,),
            dataset_market_ps=(0.20,),
        ),
    )
    strategy._position_token_index = 0
    seed_held(strategy, Decimal("50"))
    strategy._evaluate(now_ns=0)
    assert submitted == []


def test_episode_survives_a_late_buy_fill_and_then_starts_a_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial SELL keeps L0 done; a late BUY fill stays in the old episode; then L0 reopens."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            buy_cutoff_ns=600 * NS_PER_SECOND,
            signal_timestamps_ns=(0, 20 * NS_PER_SECOND, 40 * NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05, 0.05),
            dataset_market_ps=(0.50, 0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    accept_all(strategy)
    initial = buy_orders(submitted)
    top, middle = initial[0], initial[1]
    fill_order(strategy, top, top.quantity.as_decimal(), 0.48, is_buy=True, ts_event=0)
    first_episode = strategy._episode_id
    assert first_episode > 0
    assert strategy._buy_levels[0].done is True
    held = strategy._position_qty

    strategy._book_ts_ns[RADIANT_ID] = 20 * NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = 20 * NS_PER_SECOND
    strategy._evaluate(now_ns=20 * NS_PER_SECOND)
    sell = [order for order in submitted if order.side == OrderSide.SELL][-1]
    fill_order(
        strategy, sell, held / 2, float(sell.price), is_buy=False, ts_event=20 * NS_PER_SECOND
    )
    assert strategy._buy_levels[0].done is True
    assert strategy._episode_id == first_episode

    fill_order(
        strategy,
        sell,
        strategy._position_qty,
        float(sell.price),
        is_buy=False,
        ts_event=21 * NS_PER_SECOND,
    )
    assert strategy._position_qty == Decimal("0")
    canceling = [
        live
        for live in strategy._live.values()
        if live.order.side == OrderSide.BUY and live.awaiting_cancel
    ]
    assert canceling
    assert {live.cancel_reason for live in canceling} == {"episode_end"}

    fill_order(strategy, middle, Decimal("5"), 0.47, is_buy=True, ts_event=22 * NS_PER_SECOND)
    assert strategy._episode_id == first_episode
    assert strategy._buy_levels[0].done is True

    fill_order(
        strategy,
        sell,
        strategy._position_qty,
        float(sell.price),
        is_buy=False,
        ts_event=23 * NS_PER_SECOND,
    )
    for live in list(strategy._live.values()):
        order_id = str(live.order.client_order_id)
        fire_cancel_release(strategy, 24 * NS_PER_SECOND, client_order_id=order_id)
        strategy.on_order_canceled(
            SimpleNamespace(ts_event=24 * NS_PER_SECOND, client_order_id=order_id)
        )
    assert strategy._live == {}
    assert all(not level.done for level in strategy._buy_levels.values())
    assert strategy._episode_id == 0

    before = len(submitted)
    strategy._book_ts_ns[RADIANT_ID] = 40 * NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = 40 * NS_PER_SECOND
    strategy._evaluate(now_ns=40 * NS_PER_SECOND)
    assert len(submitted) > before
    assert strategy._episode_id > first_episode
    assert strategy._buy_levels[0].done is False


def test_a_new_model_tick_re_anchors_the_ladder_before_and_after_the_first_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_rebuild_latch re-snaps rung prices on a fresh model tick in both episode states."""
    ticks = (0, NS_PER_SECOND, 2 * NS_PER_SECOND)
    strategy, _submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=ticks,
            feed_timestamps_ns=ticks,
            predicted_deltas=(0.05, 0.05, 0.05),
            dataset_market_ps=(0.50, 0.49, 0.49),
        ),
    )
    strategy._evaluate(now_ns=0)
    accept_all(strategy)
    assert sorted(level.price for level in strategy._buy_levels.values()) == pytest.approx(
        [0.46, 0.47, 0.48]
    )

    strategy._books[RADIANT_ID] = _FakeBook(bid=0.49, ask=0.51)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.49, ask=0.51)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert strategy._episode_has_buy_fill is False
    assert sorted(level.price for level in strategy._buy_levels.values()) == pytest.approx(
        [0.47, 0.48, 0.49]
    )

    for live in list(strategy._live.values()):
        if not live.awaiting_cancel:
            continue
        oid = str(live.order.client_order_id)
        fire_cancel_release(strategy, NS_PER_SECOND + CANCEL_LATENCY_NS, client_order_id=oid)
        strategy.on_order_canceled(
            SimpleNamespace(
                ts_event=NS_PER_SECOND + CANCEL_LATENCY_NS,
                client_order_id=live.order.client_order_id,
            )
        )
    top = next(live.order for live in strategy._live.values() if live.level_index == 0)
    fill_order(
        strategy,
        top,
        top.quantity.as_decimal(),
        float(top.price),
        is_buy=True,
        ts_event=NS_PER_SECOND,
    )
    assert strategy._episode_has_buy_fill is True

    strategy._books[RADIANT_ID] = _FakeBook(bid=0.48, ask=0.50)
    strategy._books[DIRE_ID] = _FakeBook(bid=0.50, ask=0.52)
    strategy._book_ts_ns[RADIANT_ID] = 2 * NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = 2 * NS_PER_SECOND
    strategy._evaluate(now_ns=2 * NS_PER_SECOND)
    open_prices = sorted(level.price for level in strategy._buy_levels.values() if not level.done)
    assert open_prices == pytest.approx([0.46, 0.47])
    assert strategy._buy_levels[0].done is True
    assert_ladder_invariants(strategy)


def test_clock_at_uses_observed_feed_tapes() -> None:
    """Archive feeds drive the strategy clock: observed second, pause, terminal."""
    config = build_maker_config(
        feed_timestamps_ns=(10 * NS_PER_SECOND, 20 * NS_PER_SECOND, 40 * NS_PER_SECOND),
        signal_timestamps_ns=(10 * NS_PER_SECOND,),
        observed_clock=ObservedClockTape(
            game_seconds=(60, 120, 300),
            paused=(False, True, False),
            terminal=(False, False, True),
        ),
        game_end_ns=100 * NS_PER_SECOND,
    )
    strategy = DotaMakerStrategy(config)

    pre_first = strategy._clock_at(5 * NS_PER_SECOND)
    assert (pre_first.game_second, pre_first.paused, pre_first.game_ended) == (
        0,
        False,
        False,
    )
    at_first = strategy._clock_at(10 * NS_PER_SECOND)
    assert (at_first.game_second, at_first.paused, at_first.game_ended) == (60, False, False)
    between = strategy._clock_at(35 * NS_PER_SECOND)
    assert (between.game_second, between.paused, between.game_ended) == (120, True, False)
    at_terminal = strategy._clock_at(40 * NS_PER_SECOND)
    assert (at_terminal.game_second, at_terminal.paused, at_terminal.game_ended) == (
        300,
        False,
        True,
    )


def test_clock_at_game_end_ns_is_a_defensive_terminal_fallback() -> None:
    """game_end_ns still ends the game when no terminal tick was observed."""
    config = build_maker_config(
        feed_timestamps_ns=(10 * NS_PER_SECOND, 20 * NS_PER_SECOND),
        signal_timestamps_ns=(10 * NS_PER_SECOND,),
        observed_clock=ObservedClockTape(
            game_seconds=(60, 120),
            paused=(False, False),
            terminal=(False, False),
        ),
        game_end_ns=50 * NS_PER_SECOND,
    )
    strategy = DotaMakerStrategy(config)

    before = strategy._clock_at(30 * NS_PER_SECOND)
    assert before.game_ended is False
    after = strategy._clock_at(60 * NS_PER_SECOND)
    assert after.game_second == 120
    assert after.game_ended is True
