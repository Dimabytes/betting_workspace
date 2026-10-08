from dataclasses import replace

import pytest

from strategy.engine import step
from strategy.lifecycle import already_canceling, apply_merged, empty_state
from strategy.policy import Policy, TwoSidedPolicy, follow300_policy, two_sided_policy
from strategy.quoting import QuoteTarget
from strategy.scheduling import armed_deadline_ns, debounce_ns
from strategy.two_sided import (
    HALF_SPREAD_TICKS,
    NET_MAX_SHARES,
    ORDER_SHARES,
    REPRICE_HOLD_NS,
    SKEW_PER_SHARE,
    BidTicks,
    inventory_skew,
    price_bids,
    scale_order_size,
)
from strategy.two_sided_quoting import desired_bids
from strategy.types import (
    BlockReason,
    BookPair,
    BookUpdate,
    Budget,
    BuySettled,
    CancelAck,
    CancelTimeout,
    CancelUnsettled,
    ClockUpdate,
    EngineOutput,
    Fill,
    FreshnessLimits,
    GameClock,
    InboundEvent,
    MarketLimits,
    Merged,
    OrderAccepted,
    Permissions,
    PermissionsUpdate,
    RawDeltaSignal,
    Recovery,
    RecoveryVerified,
    StrategyState,
    TokenBook,
    TokenInventory,
    Wake,
)


def test_price_bids_matches_backtest_self_check() -> None:
    assert price_bids(fair=0.50, half_spread_ticks=1, skew=0.0) == BidTicks(49, 49)
    assert price_bids(fair=0.50, half_spread_ticks=1, skew=0.02) == BidTicks(47, 51)
    assert price_bids(fair=0.505, half_spread_ticks=1, skew=0.0) == BidTicks(50, 49)


def test_scale_order_size_matches_backtest_self_check() -> None:
    def size(net_shares: float, token_index: int) -> float:
        return scale_order_size(
            size_shares=20.0,
            net_shares=net_shares,
            net_max_shares=100.0,
            token_index=token_index,
        )

    assert size(150.0, 0) == 0.0
    assert size(150.0, 1) == 20.0
    assert size(-150.0, 1) == 0.0
    assert size(-150.0, 0) == 20.0
    assert size(100.0, 0) == 0.0
    assert size(50.0, 0) == 10.0
    assert size(0.0, 0) == 20.0


def test_inventory_skew_at_even_fair() -> None:
    assert inventory_skew(fair=0.5, net_shares=50.0, skew_per_share=2e-4) == 0.01


def test_two_sided_policy_bakes_live_constants() -> None:
    policy = two_sided_policy(debounce_ms=250, fallback_timer_s=1.0)
    assert isinstance(policy, TwoSidedPolicy)
    assert policy.order_shares == ORDER_SHARES == 20
    assert policy.net_max_shares == NET_MAX_SHARES == 50
    assert policy.half_spread_ticks == HALF_SPREAD_TICKS == 3
    assert policy.skew_per_share == SKEW_PER_SHARE
    assert policy.debounce_ms == 250
    assert policy.fallback_timer_s == 1.0
    follow = follow300_policy(level_usdc=20.0, debounce_ms=250, fallback_timer_s=1.0)
    policies: tuple[Policy, ...] = (follow, policy)
    assert all(item.debounce_ms == 250 for item in policies)


NS = 1_000_000_000
T0 = 10 * NS
T1 = T0 + 1_000_000
POLICY = two_sided_policy(debounce_ms=100, fallback_timer_s=1.0)
PERMISSIONS = Permissions(
    halt=False, reduce_only=False, allow_buy=True, allow_sell=True, sell_unconfirmed=False
)
WAKE = Wake(now_ns=T1, forced=True)


def _books(*, bid0: float, ask0: float, bid1: float, ask1: float, ts_ns: int) -> BookPair:
    return BookPair(
        tokens=(
            TokenBook(
                token_index=0, bid=bid0, ask=ask0, bid_size=100.0, ask_size=100.0, ts_ns=ts_ns
            ),
            TokenBook(
                token_index=1, bid=bid1, ask=ask1, bid_size=100.0, ask_size=100.0, ts_ns=ts_ns
            ),
        )
    )


def _even_books(*, ts_ns: int) -> BookPair:
    return _books(bid0=0.49, ask0=0.51, bid1=0.49, ask1=0.51, ts_ns=ts_ns)


def _idle(*, radiant_index: int) -> StrategyState:
    return empty_state(
        limits=MarketLimits(
            min_order_size=5.0,
            tick_size=0.01,
            pair_sum_tolerance=0.05,
            radiant_token_index=radiant_index,
        ),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        permissions=PERMISSIONS,
        budget=Budget(cash_usdc=1000.0, cap_room_usdc=1000.0, account_cap_room_usdc=1000.0),
        clock=GameClock(now_ns=T0, game_second=100, paused=False, game_ended=False),
    )


def _run(*, state: StrategyState, events: tuple[InboundEvent, ...]) -> list[EngineOutput]:
    outputs: list[EngineOutput] = []
    for event in events:
        output = step(state=state, policy=POLICY, event=event)
        outputs.append(output)
        state = output.state
    return outputs


def _quote_outputs(*, radiant_index: int) -> list[EngineOutput]:
    opened = _run(
        state=_idle(radiant_index=radiant_index),
        events=(
            BookUpdate(now_ns=T0, books=_even_books(ts_ns=T0)),
            Wake(now_ns=T0, forced=True),
        ),
    )
    accepted = _run(
        state=opened[-1].state,
        events=tuple(
            OrderAccepted(now_ns=T0, order_id=place.order_id) for place in opened[-1].plan.places
        ),
    )
    return [*opened, *accepted]


def _quoting(*, radiant_index: int) -> StrategyState:
    return _quote_outputs(radiant_index=radiant_index)[-1].state


def _bid_ids(state: StrategyState) -> dict[int, str]:
    return {
        order.token_index: order.order_id
        for order in state.orders
        if order.side == "BUY" and order.status != "gone" and not already_canceling(order)
    }


def _inventory(
    *, radiant_index: int, radiant_qty: float, dire_qty: float
) -> tuple[TokenInventory, TokenInventory]:
    qty = [0.0, 0.0]
    qty[radiant_index] = radiant_qty
    qty[1 - radiant_index] = dire_qty
    return (
        TokenInventory(token_index=0, qty=qty[0], cost_basis=0.0, last_buy_ns=None),
        TokenInventory(token_index=1, qty=qty[1], cost_basis=0.0, last_buy_ns=None),
    )


def _target(targets: tuple[QuoteTarget, ...], token_index: int) -> QuoteTarget | None:
    for target in targets:
        if target.token_index == token_index:
            return target
    return None


PULL_EVENTS: dict[BlockReason, tuple[InboundEvent, ...]] = {
    "halt": (PermissionsUpdate(now_ns=T1, permissions=replace(PERMISSIONS, halt=True)), WAKE),
    "ownership_unresolved": (
        Fill(
            now_ns=T1,
            fill_id="stray",
            order_id="venue-stray",
            qty=5.0,
            price=0.47,
            token_index=0,
            side="BUY",
        ),
        WAKE,
    ),
    "recovery": (Recovery(now_ns=T1, restored_buy_ids=()), WAKE),
    "game_end": (
        ClockUpdate(
            now_ns=T1, clock=GameClock(now_ns=T1, game_second=100, paused=False, game_ended=True)
        ),
        WAKE,
    ),
    "paused": (
        ClockUpdate(
            now_ns=T1, clock=GameClock(now_ns=T1, game_second=100, paused=True, game_ended=False)
        ),
        WAKE,
    ),
    "cutoff": (
        ClockUpdate(
            now_ns=T1, clock=GameClock(now_ns=T1, game_second=-61, paused=False, game_ended=False)
        ),
        WAKE,
    ),
    "stale_signal": (Wake(now_ns=T0 + 16 * NS + 1, forced=False),),
    "reduce_only": (
        PermissionsUpdate(now_ns=T1, permissions=replace(PERMISSIONS, reduce_only=True)),
        WAKE,
    ),
    "fair": (
        PermissionsUpdate(now_ns=T1, permissions=replace(PERMISSIONS, allow_buy=False)),
        WAKE,
    ),
    "stale_book": (Wake(now_ns=T0 + 5 * NS + 1, forced=False),),
    "band": (
        BookUpdate(
            now_ns=T1,
            books=_books(bid0=0.91, ask0=0.93, bid1=0.07, ask1=0.09, ts_ns=T1),
        ),
        WAKE,
    ),
    "pair_tolerance": (
        BookUpdate(
            now_ns=T1,
            books=_books(bid0=0.55, ask0=0.57, bid1=0.53, ask1=0.55, ts_ns=T1),
        ),
        WAKE,
    ),
}


def test_first_quote_places_two_post_only_buys() -> None:
    wake = _quote_outputs(radiant_index=0)[1]
    assert {
        (
            place.token_index,
            place.level_index,
            place.side,
            place.price,
            place.quantity,
            place.reduce_only,
        )
        for place in wake.plan.places
    } == {
        (0, 0, "BUY", 0.47, 20.0, False),
        (1, 1, "BUY", 0.47, 20.0, False),
    }
    assert wake.plan.block_reason == ""


@pytest.mark.parametrize("radiant_index", [0, 1])
def test_skew_leans_against_inventory(radiant_index: int) -> None:
    state = replace(
        _quoting(radiant_index=radiant_index),
        inventory=_inventory(radiant_index=radiant_index, radiant_qty=30.0, dire_qty=0.0),
    )
    targets = desired_bids(state=state, policy=POLICY)
    radiant = _target(targets, radiant_index)
    dire_index = 1 - radiant_index
    dire = _target(targets, dire_index)
    assert radiant is not None and dire is not None
    assert (radiant.price, radiant.quantity, radiant.level_index, radiant.side) == (
        0.46,
        8.0,
        radiant_index,
        "BUY",
    )
    assert (dire.price, dire.quantity, dire.level_index, dire.side) == (
        0.48,
        20.0,
        dire_index,
        "BUY",
    )


@pytest.mark.parametrize("radiant_index", [0, 1])
def test_fill_shrinks_the_side_that_grew(radiant_index: int) -> None:
    state = _quoting(radiant_index=radiant_index)
    ids = _bid_ids(state)
    dire = 1 - radiant_index
    outputs = _run(
        state=state,
        events=(
            Fill(
                now_ns=T1,
                fill_id="f1",
                order_id=ids[radiant_index],
                qty=20.0,
                price=0.47,
                token_index=radiant_index,
                side="BUY",
            ),
            Wake(now_ns=T1, forced=True),
        ),
    )
    plan = outputs[-1].plan
    assert plan.cancels == ()
    assert len(plan.places) == 1
    place = plan.places[0]
    assert (place.token_index, place.price, place.quantity, place.side) == (
        radiant_index,
        0.47,
        12.0,
        "BUY",
    )
    assert [keep.order_id for keep in plan.keep] == [ids[dire]]
    kept = next(order for order in outputs[-1].state.orders if order.order_id == ids[dire])
    assert kept.submitted_qty == 20.0


@pytest.mark.parametrize(
    ("net", "radiant_size"),
    [
        (0.0, 20.0),
        (10.0, 16.0),
        (25.0, 10.0),
        (37.5, 5.0),
        (45.0, None),
        (50.0, None),
    ],
)
def test_net_cap_shrinks_the_heavy_side_to_zero(net: float, radiant_size: float | None) -> None:
    state = replace(
        _quoting(radiant_index=0),
        inventory=_inventory(radiant_index=0, radiant_qty=net, dire_qty=0.0),
    )
    targets = desired_bids(state=state, policy=POLICY)
    dire = _target(targets, 1)
    assert dire is not None
    assert dire.quantity == 20.0
    radiant = _target(targets, 0)
    if radiant_size is None:
        assert radiant is None
        return
    assert radiant is not None
    assert radiant.quantity == radiant_size


@pytest.mark.parametrize("reason", list(PULL_EVENTS))
def test_each_pull_reason_cancels_both_bids(reason: BlockReason) -> None:
    state = _quoting(radiant_index=0)
    ids = _bid_ids(state)
    outputs = _run(state=state, events=PULL_EVENTS[reason])
    cancels = {cancel.order_id: cancel.reason for out in outputs for cancel in out.plan.cancels}
    assert cancels == {ids[0]: reason, ids[1]: reason}
    last = outputs[-1]
    assert last.plan.block_reason == reason
    assert last.plan.places == ()
    assert all(
        order.status == "canceling" and order.cancel_reason == reason for order in last.state.orders
    )


def test_one_tick_move_waits_then_replaces_after_ack() -> None:
    state = _quoting(radiant_index=0)
    ids = _bid_ids(state)
    t1 = T0 + NS
    held = _run(
        state=state,
        events=(
            BookUpdate(
                now_ns=t1,
                books=_books(bid0=0.50, ask0=0.52, bid1=0.48, ask1=0.50, ts_ns=t1),
            ),
            Wake(now_ns=t1, forced=True),
        ),
    )[-1]
    assert held.plan.cancels == ()
    assert held.plan.places == ()
    assert {keep.order_id for keep in held.plan.keep} == set(ids.values())
    assert held.state.reprice_since_ns == (t1, t1)
    assert held.next_wake_ns == t1 + REPRICE_HOLD_NS
    early = _run(
        state=held.state,
        events=(Wake(now_ns=t1 + REPRICE_HOLD_NS - 1_000_000, forced=True),),
    )[-1]
    assert early.plan.cancels == ()
    released = _run(
        state=early.state,
        events=(Wake(now_ns=t1 + REPRICE_HOLD_NS, forced=False),),
    )[-1]
    assert {cancel.order_id: cancel.reason for cancel in released.plan.cancels} == {
        ids[0]: "reprice",
        ids[1]: "reprice",
    }
    assert released.plan.places == ()
    assert released.state.reprice_since_ns == (None, None)
    radiant = _run(
        state=released.state,
        events=(CancelAck(now_ns=t1 + REPRICE_HOLD_NS, order_id=ids[0]),),
    )[-1]
    assert len(radiant.plan.places) == 1
    assert (
        radiant.plan.places[0].token_index,
        radiant.plan.places[0].price,
        radiant.plan.places[0].quantity,
    ) == (
        0,
        0.48,
        20.0,
    )
    dire = _run(
        state=radiant.state,
        events=(CancelAck(now_ns=t1 + REPRICE_HOLD_NS, order_id=ids[1]),),
    )[-1]
    assert len(dire.plan.places) == 1
    assert (
        dire.plan.places[0].token_index,
        dire.plan.places[0].price,
        dire.plan.places[0].quantity,
    ) == (
        1,
        0.46,
        20.0,
    )


def test_two_tick_move_cancels_at_once_and_waits_for_settle() -> None:
    state = _quoting(radiant_index=0)
    ids = _bid_ids(state)
    t1 = T0 + NS
    moved = _run(
        state=state,
        events=(
            BookUpdate(
                now_ns=t1,
                books=_books(bid0=0.51, ask0=0.53, bid1=0.47, ask1=0.49, ts_ns=t1),
            ),
            Wake(now_ns=t1, forced=True),
        ),
    )[-1]
    assert {cancel.order_id: cancel.reason for cancel in moved.plan.cancels} == {
        ids[0]: "reprice",
        ids[1]: "reprice",
    }
    assert moved.plan.places == ()
    assert moved.state.reprice_since_ns == (None, None)
    radiant = _run(state=moved.state, events=(CancelAck(now_ns=t1, order_id=ids[0]),))[-1]
    assert len(radiant.plan.places) == 1
    assert (radiant.plan.places[0].token_index, radiant.plan.places[0].price) == (0, 0.49)
    unsettled = _run(state=radiant.state, events=(CancelUnsettled(now_ns=t1, order_id=ids[1]),))[-1]
    assert unsettled.plan.places == ()
    settled = _run(
        state=unsettled.state,
        events=(BuySettled(now_ns=t1, order_id=ids[1], matched_qty=0.0),),
    )[-1]
    assert len(settled.plan.places) == 1
    assert (settled.plan.places[0].token_index, settled.plan.places[0].price) == (1, 0.45)


def test_recovery_chain_keeps_quoting_both_sides() -> None:
    chain: list[EngineOutput] = []
    opened = _run(
        state=_idle(radiant_index=0),
        events=(
            BookUpdate(now_ns=T0, books=_even_books(ts_ns=T0)),
            Recovery(now_ns=T0, restored_buy_ids=()),
        ),
    )
    chain.extend(opened)
    recovery = opened[-1]
    assert recovery.plan.block_reason == "recovery"
    assert recovery.plan.places == ()
    inventory = (
        TokenInventory(token_index=0, qty=30.0, cost_basis=0.0, last_buy_ns=None),
        TokenInventory(token_index=1, qty=10.0, cost_basis=0.0, last_buy_ns=None),
    )
    verified_outs = _run(
        state=recovery.state,
        events=(RecoveryVerified(now_ns=T0, generation=1, inventory=inventory),),
    )
    chain.extend(verified_outs)
    verified = verified_outs[-1]
    assert {
        (place.token_index, place.price, place.quantity, place.side)
        for place in verified.plan.places
    } == {(0, 0.47, 12.0, "BUY"), (1, 0.47, 20.0, "BUY")}
    assert verified.state.sell_only is False
    assert verified.state.recovery_pending is False
    accepted = _run(
        state=verified.state,
        events=tuple(
            OrderAccepted(now_ns=T0, order_id=place.order_id) for place in verified.plan.places
        ),
    )
    chain.extend(accepted)
    token0 = next(place.order_id for place in verified.plan.places if place.token_index == 0)
    token1 = next(place.order_id for place in verified.plan.places if place.token_index == 1)
    filled_outs = _run(
        state=accepted[-1].state,
        events=(
            Fill(
                now_ns=T1,
                fill_id="f-rec",
                order_id=token0,
                qty=12.0,
                price=0.47,
                token_index=0,
                side="BUY",
            ),
            Wake(now_ns=T1, forced=True),
        ),
    )
    chain.extend(filled_outs)
    filled = filled_outs[-1]
    assert len(filled.plan.places) == 1
    place = filled.plan.places[0]
    assert (place.token_index, place.price, place.quantity, place.side) == (0, 0.46, 7.2, "BUY")
    assert any(keep.order_id == token1 for keep in filled.plan.keep)
    live_tokens = {
        order.token_index
        for order in filled.state.orders
        if order.side == "BUY" and order.status not in ("canceling", "gone")
    }
    assert live_tokens == {0, 1}
    assert filled.state.recovery_pending is False
    assert filled.state.sell_only is False
    assert filled.state.recovery_generation == 1
    assert all(place.side == "BUY" for out in chain for place in out.plan.places)


def test_two_sided_deadline_ignores_follow300_boundaries() -> None:
    state = replace(
        _quoting(radiant_index=0),
        signal=RawDeltaSignal(
            predicted_delta=0.05,
            received_ns=0,
            source_received_ns=0,
            anchor_p=0.5,
            deaths_radiant=0,
            deaths_dire=0,
        ),
    )
    now = T0 + 20 * NS
    follow = follow300_policy(level_usdc=20.0, debounce_ms=100, fallback_timer_s=1.0)
    assert armed_deadline_ns(state=state, policy=follow, now_ns=now) == now
    assert armed_deadline_ns(state=state, policy=POLICY, now_ns=now) == T0 + debounce_ns(POLICY)


def _token_books(
    *,
    radiant_index: int,
    radiant_bid: float,
    radiant_ask: float,
    dire_bid: float,
    dire_ask: float,
    ts_ns: int,
) -> BookPair:
    bid = [0.0, 0.0]
    ask = [0.0, 0.0]
    bid[radiant_index] = radiant_bid
    ask[radiant_index] = radiant_ask
    bid[1 - radiant_index] = dire_bid
    ask[1 - radiant_index] = dire_ask
    return _books(bid0=bid[0], ask0=ask[0], bid1=bid[1], ask1=ask[1], ts_ns=ts_ns)


def _low_books(*, radiant_index: int, ts_ns: int) -> BookPair:
    return _token_books(
        radiant_index=radiant_index,
        radiant_bid=0.14,
        radiant_ask=0.16,
        dire_bid=0.84,
        dire_ask=0.86,
        ts_ns=ts_ns,
    )


def _high_books(*, radiant_index: int, ts_ns: int) -> BookPair:
    return _token_books(
        radiant_index=radiant_index,
        radiant_bid=0.84,
        radiant_ask=0.86,
        dire_bid=0.14,
        dire_ask=0.16,
        ts_ns=ts_ns,
    )


def _accepted_at(*, radiant_index: int, books: BookPair) -> tuple[StrategyState, dict[int, str]]:
    opened = _run(
        state=_idle(radiant_index=radiant_index),
        events=(BookUpdate(now_ns=T0, books=books), Wake(now_ns=T0, forced=True)),
    )
    accepted = _run(
        state=opened[-1].state,
        events=tuple(
            OrderAccepted(now_ns=T0, order_id=place.order_id) for place in opened[-1].plan.places
        ),
    )
    state = accepted[-1].state
    return state, _bid_ids(state)


def _bid_price(state: StrategyState, token_index: int) -> float:
    return next(
        order.price
        for order in state.orders
        if order.side == "BUY" and order.token_index == token_index and not already_canceling(order)
    )


def _pair_sums_within_cap(state: StrategyState) -> None:
    buys = [order for order in state.orders if order.side == "BUY"]
    for index, left in enumerate(buys):
        for right in buys[index + 1 :]:
            if left.token_index == right.token_index:
                continue
            ticks = round(left.price / POLICY.tick) + round(right.price / POLICY.tick)
            assert ticks <= POLICY.max_bid_sum_ticks


def _move_to_high(*, state: StrategyState, radiant_index: int) -> EngineOutput:
    return _run(
        state=state,
        events=(
            BookUpdate(
                now_ns=T1,
                books=_high_books(radiant_index=radiant_index, ts_ns=T1),
            ),
            Wake(now_ns=T1, forced=True),
        ),
    )[-1]


@pytest.mark.parametrize("radiant_index", [0, 1])
def test_large_move_waits_for_the_opposite_bid(radiant_index: int) -> None:
    state, ids = _accepted_at(
        radiant_index=radiant_index, books=_low_books(radiant_index=radiant_index, ts_ns=T0)
    )
    dire = 1 - radiant_index
    assert _bid_price(state, radiant_index) == 0.12
    assert _bid_price(state, dire) == 0.82
    moved = _move_to_high(state=state, radiant_index=radiant_index)
    assert {cancel.order_id: cancel.reason for cancel in moved.plan.cancels} == {
        ids[radiant_index]: "reprice",
        ids[dire]: "reprice",
    }
    assert moved.plan.places == ()
    _pair_sums_within_cap(moved.state)
    first = _run(state=moved.state, events=(CancelAck(now_ns=T1, order_id=ids[radiant_index]),))[-1]
    assert first.plan.places == ()
    _pair_sums_within_cap(first.state)
    second = _run(state=first.state, events=(CancelAck(now_ns=T1, order_id=ids[dire]),))[-1]
    assert {(place.token_index, place.price, place.quantity) for place in second.plan.places} == {
        (radiant_index, 0.82, 20.0),
        (dire, 0.12, 20.0),
    }
    _pair_sums_within_cap(second.state)


@pytest.mark.parametrize("radiant_index", [0, 1])
def test_unsettled_replace_waits_for_the_opposite_cancel(radiant_index: int) -> None:
    state, ids = _accepted_at(
        radiant_index=radiant_index, books=_low_books(radiant_index=radiant_index, ts_ns=T0)
    )
    dire = 1 - radiant_index
    moved = _move_to_high(state=state, radiant_index=radiant_index)
    timed_out = _run(state=moved.state, events=(CancelTimeout(now_ns=T1, order_id=ids[dire]),))[-1]
    assert timed_out.plan.places == ()
    unsettled = _run(
        state=timed_out.state,
        events=(CancelUnsettled(now_ns=T1, order_id=ids[radiant_index]),),
    )[-1]
    assert unsettled.plan.places == ()
    settled = _run(
        state=unsettled.state,
        events=(BuySettled(now_ns=T1, order_id=ids[radiant_index], matched_qty=0.0),),
    )[-1]
    assert settled.plan.places == ()
    _pair_sums_within_cap(settled.state)
    resumed = _run(state=settled.state, events=(CancelAck(now_ns=T1, order_id=ids[dire]),))[-1]
    assert {(place.token_index, place.price, place.quantity) for place in resumed.plan.places} == {
        (radiant_index, 0.82, 20.0),
        (dire, 0.12, 20.0),
    }
    _pair_sums_within_cap(resumed.state)


def test_zero_clock_quotes_only_after_feed_tick() -> None:
    state = replace(
        _idle(radiant_index=0),
        clock=GameClock(now_ns=0, game_second=0, paused=False, game_ended=False),
    )
    blocked = _run(
        state=state,
        events=(
            BookUpdate(now_ns=T0, books=_even_books(ts_ns=T0)),
            Wake(now_ns=T0, forced=True),
        ),
    )[-1]
    assert blocked.plan.places == ()
    assert blocked.plan.block_reason == "stale_signal"
    quoted = _run(
        state=blocked.state,
        events=(
            ClockUpdate(
                now_ns=T0,
                clock=GameClock(now_ns=T0, game_second=100, paused=False, game_ended=False),
            ),
            Wake(now_ns=T0, forced=True),
        ),
    )[-1]
    assert {
        (place.token_index, place.price, place.quantity, place.reduce_only)
        for place in quoted.plan.places
    } == {(0, 0.47, 20.0, False), (1, 0.47, 20.0, False)}


def test_merged_cuts_both_legs_and_keeps_bids_and_recovery() -> None:
    held = replace(
        _quoting(radiant_index=0),
        inventory=(
            TokenInventory(token_index=0, qty=30.0, cost_basis=12.0, last_buy_ns=T0),
            TokenInventory(token_index=1, qty=20.0, cost_basis=11.0, last_buy_ns=T0),
        ),
    )
    settled = _run(state=held, events=(Wake(now_ns=T1, forced=True),))[-1].state
    merge_ns = T1 + 1_000
    outputs = _run(
        state=settled,
        events=(
            Merged(now_ns=merge_ns, token_index=0, qty=20.0),
            Merged(now_ns=merge_ns, token_index=1, qty=20.0),
        ),
    )
    merged = outputs[-1].state
    radiant, dire = merged.inventory
    assert radiant.qty == 10.0
    assert radiant.cost_basis == pytest.approx(4.0)
    assert radiant.last_buy_ns == T0
    assert dire == TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None)
    assert merged.orders == settled.orders
    assert merged.sell_only == settled.sell_only
    assert merged.recovery_pending == settled.recovery_pending
    assert merged.recovery_generation == settled.recovery_generation
    assert merged.schedule.dirty_open_ns == merge_ns
    assert all(out.plan.places == () and out.plan.cancels == () for out in outputs)
    assert desired_bids(state=merged, policy=POLICY) == desired_bids(state=settled, policy=POLICY)
    over = apply_merged(state=merged, event=Merged(now_ns=merge_ns, token_index=0, qty=25.0))
    assert over.inventory[0] == TokenInventory(
        token_index=0, qty=0.0, cost_basis=0.0, last_buy_ns=None
    )
