"""Unit cases for the dependency-free Follow300 core."""

import math
from dataclasses import replace

from shared.constants.strategy import (
    EXIT_ABS_DELTA,
    LATCH_REANCHOR_SECONDS,
    MIN_ABS_DELTA,
    MIN_ENTRY_PRICE,
    SELL_MIN_LIFE_SECONDS,
)
from shared.utils.trading import buy_share_quantity
from strategy.engine import step
from strategy.lifecycle import (
    apply_fill,
    begin_episode,
    empty_state,
    mark_canceling,
    occupy_buy,
    rung_occupied,
    sell_occupied,
)
from strategy.policy import Follow300Policy, extraction_policy, follow300_policy
from strategy.quoting import pick_episode_token
from strategy.signals import (
    clip_fair,
    entry_delta_blocked,
    next_delta_gate_open,
    passes_anchor,
)
from strategy.types import (
    BookPair,
    BookUpdate,
    Budget,
    BudgetUpdate,
    CancelAck,
    CancelOrder,
    CancelTimeout,
    ClockUpdate,
    Fill,
    FreshnessLimits,
    GameClock,
    LimitsUpdate,
    MarketLimits,
    OrderAccepted,
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

NS = 1_000_000_000


def _limits() -> MarketLimits:
    return MarketLimits(
        min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05, radiant_token_index=0
    )


def _freshness() -> FreshnessLimits:
    return FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0)


def _idle(*, now_ns: int = 0, second: int = 100, usdc: float = 10_000.0) -> StrategyState:
    return empty_state(
        limits=_limits(),
        freshness=_freshness(),
        permissions=Permissions(
            halt=False,
            reduce_only=False,
            allow_buy=True,
            allow_sell=True,
            sell_unconfirmed=False,
        ),
        budget=Budget(
            cash_usdc=usdc, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        clock=GameClock(now_ns=now_ns, game_second=second, paused=False, game_ended=False),
    )


def _books(
    *,
    bid0: float,
    ask0: float,
    bid1: float,
    ask1: float,
    ts_ns: int,
) -> BookPair:
    return BookPair(
        tokens=(
            TokenBook(
                token_index=0,
                bid=bid0,
                ask=ask0,
                bid_size=100.0,
                ask_size=100.0,
                ts_ns=ts_ns,
            ),
            TokenBook(
                token_index=1,
                bid=bid1,
                ask=ask1,
                bid_size=100.0,
                ask_size=100.0,
                ts_ns=ts_ns,
            ),
        )
    )


def _balanced(*, ts_ns: int, bid0: float = 0.50, ask0: float = 0.52) -> BookPair:
    mid0 = (bid0 + ask0) / 2.0
    spread = ask0 - bid0
    mid1 = 1.0 - mid0
    bid1 = round(mid1 - spread / 2.0, 2)
    ask1 = round(mid1 + spread / 2.0, 2)
    return _books(bid0=bid0, ask0=ask0, bid1=bid1, ask1=ask1, ts_ns=ts_ns)


def _signal(*, delta: float, now_ns: int, anchor: float = 0.51) -> RawDeltaSignal:
    return RawDeltaSignal(
        predicted_delta=delta,
        source_received_ns=now_ns,
        received_ns=now_ns,
        anchor_p=anchor,
        deaths_radiant=0,
        deaths_dire=0,
    )


def _policy() -> Follow300Policy:
    return follow300_policy(level_usdc=100.0, debounce_ms=100, fallback_timer_s=2.0)


def _inventory(
    *, yes_qty: float = 0.0, no_qty: float = 0.0
) -> tuple[TokenInventory, TokenInventory]:
    return (
        TokenInventory(token_index=0, qty=yes_qty, cost_basis=0.0, last_buy_ns=None),
        TokenInventory(token_index=1, qty=no_qty, cost_basis=0.0, last_buy_ns=None),
    )


def _fill_of(
    order: PlaceOrder | RestingOrder,
    *,
    fill_id: str,
    qty: float,
    now_ns: int,
    price: float | None = None,
) -> Fill:
    return Fill(
        now_ns=now_ns,
        fill_id=fill_id,
        order_id=order.order_id,
        qty=qty,
        price=order.price if price is None else price,
        token_index=order.token_index,
        side=order.side,
    )


def _feed(
    state: StrategyState,
    policy: Follow300Policy,
    now_ns: int,
    *,
    books: BookPair,
    signal: RawDeltaSignal,
    second: int = 100,
) -> StrategyState:
    state = step(state=state, policy=policy, event=BookUpdate(now_ns=now_ns, books=books)).state
    state = step(state=state, policy=policy, event=SignalUpdate(now_ns=now_ns, signal=signal)).state
    clock = GameClock(now_ns=now_ns, game_second=second, paused=False, game_ended=False)
    return step(state=state, policy=policy, event=ClockUpdate(now_ns=now_ns, clock=clock)).state


def _wake(state: StrategyState, policy: Follow300Policy, now_ns: int) -> tuple[StrategyState, Plan]:
    out = step(state=state, policy=policy, event=Wake(now_ns=now_ns, forced=True))
    return out.state, out.plan


def _prime(
    *,
    now_ns: int = 0,
    delta: float = 0.05,
    books: BookPair | None = None,
    usdc: float = 10_000.0,
    second: int = 100,
    anchor: float = 0.51,
) -> tuple[StrategyState, Follow300Policy, Plan]:
    policy = _policy()
    pair = _balanced(ts_ns=now_ns) if books is None else books
    state = _feed(
        _idle(now_ns=now_ns, second=second, usdc=usdc),
        policy,
        now_ns,
        books=pair,
        signal=_signal(delta=delta, now_ns=now_ns, anchor=anchor),
        second=second,
    )
    state, plan = _wake(state, policy, now_ns)
    return state, policy, plan


def _buy_places(plan: Plan) -> tuple[PlaceOrder, ...]:
    return tuple(place for place in plan.places if place.side == "BUY")


def _sell_places(plan: Plan) -> tuple[PlaceOrder, ...]:
    return tuple(place for place in plan.places if place.side == "SELL")


def _accept_all(state: StrategyState, policy: Follow300Policy, now_ns: int) -> StrategyState:
    for order in state.orders:
        if order.accepted:
            continue
        state = step(
            state=state, policy=policy, event=OrderAccepted(now_ns=now_ns, order_id=order.order_id)
        ).state
    return state


def _by_id(state: StrategyState, order_id: str) -> RestingOrder:
    for order in state.orders:
        if order.order_id == order_id:
            return order
    raise KeyError(order_id)


def _full(ts_ns: int) -> BookPair:
    return _balanced(ts_ns=ts_ns)


def _thin(ts_ns: int) -> BookPair:
    """Foreign best bid is a cent under the bot's resting 0.50 L0."""
    return _balanced(ts_ns=ts_ns, bid0=0.49, ask0=0.51)


def _prime_policy(policy: Follow300Policy, *, books: BookPair) -> tuple[StrategyState, Plan]:
    state = _feed(_idle(), policy, 0, books=books, signal=_signal(delta=0.05, now_ns=0))
    return _wake(state, policy, 0)


def test_signal_helpers_match_extraction() -> None:
    assert clip_fair(1.2) == 1.0
    assert clip_fair(-0.1) == 0.0
    assert (
        entry_delta_blocked(
            predicted_delta=0.009, min_abs_delta=0.01, exit_abs_delta=0.01, gate_open=False
        )
        is True
    )
    assert (
        entry_delta_blocked(
            predicted_delta=0.01, min_abs_delta=0.01, exit_abs_delta=0.01, gate_open=False
        )
        is False
    )
    assert (
        entry_delta_blocked(
            predicted_delta=0.009, min_abs_delta=0.01, exit_abs_delta=0.005, gate_open=True
        )
        is False
    )
    assert passes_anchor(book_p=0.50, anchor_p=0.51) is True
    assert passes_anchor(book_p=0.50, anchor_p=0.52) is False


def test_min_delta_blocks_below_and_admits_equal() -> None:
    _, _, blocked = _prime(delta=0.009)
    assert _buy_places(blocked) == ()
    assert blocked.block_reason == "min_delta"
    _, _, admitted = _prime(delta=MIN_ABS_DELTA)
    assert len(_buy_places(admitted)) == 3
    _, _, neg_blocked = _prime(delta=-0.009)
    assert _buy_places(neg_blocked) == ()
    assert neg_blocked.block_reason == "min_delta"
    _, _, neg_ok = _prime(delta=-MIN_ABS_DELTA)
    assert len(_buy_places(neg_ok)) == 3
    assert all(place.token_index == 1 for place in _buy_places(neg_ok))


def test_follow300_policy_defaults_to_hysteresis() -> None:
    policy = _policy()
    assert policy.min_abs_delta == MIN_ABS_DELTA
    assert policy.exit_abs_delta == EXIT_ABS_DELTA
    assert not hasattr(policy, "max_abs_nw_delta_30")


def test_delta_gate_hysteresis_holds_through_the_band() -> None:
    """exit < entry: opens at entry, keeps quoting in the band, closes below exit."""
    policy = _policy()
    assert (
        next_delta_gate_open(was_open=False, abs_delta=0.018, entry=0.02, exit_threshold=0.015)
        is False
    )
    state = _feed(
        _idle(),
        policy,
        0,
        books=_balanced(ts_ns=0),
        signal=_signal(delta=0.018, now_ns=0),
    )
    state, blocked = _wake(state, policy, 0)
    assert blocked.block_reason == "min_delta"
    assert _buy_places(blocked) == ()
    assert state.delta_gate_open is False

    state = _feed(
        state,
        policy,
        NS,
        books=_balanced(ts_ns=NS),
        signal=_signal(delta=0.02, now_ns=NS),
    )
    state, opened = _wake(state, policy, NS)
    buy_ids = {place.order_id for place in _buy_places(opened)}
    assert len(buy_ids) == 3
    assert state.delta_gate_open is True

    state = _feed(
        state,
        policy,
        2 * NS,
        books=_balanced(ts_ns=2 * NS),
        signal=_signal(delta=0.016, now_ns=2 * NS),
    )
    state, held = _wake(state, policy, 2 * NS)
    assert state.delta_gate_open is True
    assert held.block_reason != "min_delta"
    assert {keep.order_id for keep in held.keep} >= buy_ids

    state = _feed(
        state,
        policy,
        3 * NS,
        books=_balanced(ts_ns=3 * NS),
        signal=_signal(delta=0.014, now_ns=3 * NS),
    )
    state, closed = _wake(state, policy, 3 * NS)
    assert state.delta_gate_open is False
    assert closed.block_reason == "min_delta"
    assert _buy_places(closed) == ()


def test_min_price_blocks_below_and_admits_equal() -> None:
    under = round(MIN_ENTRY_PRICE - 0.01, 2)
    low = _books(
        bid0=under,
        ask0=under + 0.02,
        bid1=round(1 - under - 0.02, 2),
        ask1=round(1 - under, 2),
        ts_ns=0,
    )
    _, _, blocked = _prime(books=low, anchor=MIN_ENTRY_PRICE)
    assert _buy_places(blocked) == ()
    assert blocked.block_reason == "min_price"
    floor = _books(
        bid0=MIN_ENTRY_PRICE,
        ask0=MIN_ENTRY_PRICE + 0.02,
        bid1=round(1 - MIN_ENTRY_PRICE - 0.02, 2),
        ask1=round(1 - MIN_ENTRY_PRICE, 2),
        ts_ns=0,
    )
    _, _, admitted = _prime(books=floor, anchor=MIN_ENTRY_PRICE + 0.01)
    floor_buys = _buy_places(admitted)
    assert floor_buys
    assert all(place.token_index == 0 for place in floor_buys)


def test_max_price_blocks_at_and_above_and_admits_below() -> None:
    high = _books(bid0=0.85, ask0=0.87, bid1=0.13, ask1=0.15, ts_ns=0)
    _, _, blocked = _prime(books=high, anchor=0.86)
    assert _buy_places(blocked) == ()
    assert blocked.block_reason == "max_price"
    under = _books(bid0=0.84, ask0=0.86, bid1=0.14, ask1=0.16, ts_ns=0)
    _, _, admitted = _prime(books=under, anchor=0.85)
    ceiling_buys = _buy_places(admitted)
    assert len(ceiling_buys) == 3
    assert all(place.token_index == 0 for place in ceiling_buys)
    assert all(place.price < 0.85 for place in ceiling_buys)


def test_wide_spread_blocks_six_ticks_admits_five() -> None:
    wide = _books(bid0=0.50, ask0=0.56, bid1=0.44, ask1=0.50, ts_ns=0)
    _, _, blocked = _prime(books=wide, anchor=0.53)
    assert _buy_places(blocked) == ()
    assert blocked.block_reason == "wide_spread"
    ok = _books(bid0=0.50, ask0=0.55, bid1=0.45, ask1=0.50, ts_ns=0)
    _, _, admitted = _prime(books=ok, anchor=0.525)
    assert len(_buy_places(admitted)) == 3


def test_plus_delta_picks_token_zero_minus_picks_token_one() -> None:
    _, _, plus = _prime(delta=0.05)
    assert {place.token_index for place in _buy_places(plus)} == {0}
    _, _, minus = _prime(delta=-0.05)
    assert {place.token_index for place in _buy_places(minus)} == {1}


def test_equal_edge_prefers_token_index_zero() -> None:
    policy = replace(_policy(), min_entry_price=0.0)
    state = replace(
        _idle(),
        books=_books(bid0=0.20, ask0=0.22, bid1=0.20, ask1=0.22, ts_ns=0),
        signal=_signal(delta=MIN_ABS_DELTA, now_ns=0, anchor=0.50),
    )
    assert pick_episode_token(state=state, policy=policy, fair=0.50, buy_blocked=frozenset()) == 0


def test_two_decimal_fill_sum_completes_the_rung() -> None:
    state = begin_episode(state=_idle(), token_index=0, level_count=3)
    order = RestingOrder(
        order_id="c0",
        episode_id=state.episode_id,
        token_index=0,
        side="BUY",
        price=0.76,
        submitted_qty=131.58,
        filled_qty=0.0,
        level_index=0,
        status="live",
        accepted=True,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=0,
        accepted_ns=0,
    )
    state = occupy_buy(state=state, order=order)
    state = apply_fill(
        state=state,
        event=_fill_of(order, fill_id="f1", qty=40.0, now_ns=0),
    )
    state = apply_fill(
        state=state,
        event=_fill_of(order, fill_id="f2", qty=91.58, now_ns=1),
    )
    rung = next(item for item in state.rungs if item.index == 0)
    assert rung.done is True
    assert not any(item.order_id == "c0" for item in state.orders)


def test_duplicate_fill_is_ignored() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    fill = _fill_of(top, fill_id="f1", qty=5.0, now_ns=0)
    once = step(state=state, policy=policy, event=fill).state
    twice = step(state=once, policy=policy, event=fill).state
    assert twice.position.qty == once.position.qty == 5.0
    rung = next(item for item in twice.rungs if item.index == top.level_index)
    first = next(item for item in once.rungs if item.index == top.level_index)
    assert rung.filled_qty == first.filled_qty


def test_cancel_then_fill_race() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = _feed(
        state,
        policy,
        NS,
        books=_balanced(ts_ns=NS),
        signal=_signal(delta=0.05, now_ns=0),
        second=540,
    )
    state, plan = _wake(state, policy, NS)
    assert any(cancel.order_id == top.order_id for cancel in plan.cancels)
    assert rung_occupied(state=state, level_index=top.level_index)
    filled = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f1", qty=5.0, now_ns=NS),
    ).state
    assert filled.position.qty == 5.0
    assert rung_occupied(state=filled, level_index=top.level_index)
    acked = step(
        state=filled, policy=policy, event=CancelAck(now_ns=NS, order_id=top.order_id)
    ).state
    assert not any(order.order_id == top.order_id for order in acked.orders)


def test_keep_on_second_wake_same_bid() -> None:
    state, policy, first = _prime()
    live_ids = {place.order_id for place in _buy_places(first)}
    state = _accept_all(state, policy, 0)
    state, second = _wake(state, policy, NS)
    keep_ids = {keep.order_id for keep in second.keep}
    assert live_ids <= keep_ids
    assert second.cancels == ()
    assert second.places == ()


def test_move_after_top_fill_when_join_drops() -> None:
    state, policy, plan = _prime()
    top, _mid, bottom = _buy_places(plan)
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f-top", qty=top.quantity, now_ns=0),
    ).state
    state = _feed(
        state,
        policy,
        NS,
        books=_balanced(ts_ns=NS, bid0=0.49, ask0=0.51),
        signal=_signal(delta=0.05, now_ns=0, anchor=0.51),
        second=100,
    )
    state, moved = _wake(state, policy, NS)
    assert moved.moves
    assert all(move.order_id != top.order_id for move in moved.moves)
    leftover = _by_id(state, bottom.order_id)
    assert leftover.status != "canceling"
    assert not any(cancel.order_id == bottom.order_id for cancel in moved.cancels)


def test_replace_after_partial_requotes_the_full_rung() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f-part", qty=5.0, now_ns=0),
    ).state
    state = _feed(
        state,
        policy,
        NS,
        books=_balanced(ts_ns=NS, bid0=0.49, ask0=0.51),
        signal=_signal(delta=0.05, now_ns=0, anchor=0.51),
        second=100,
    )
    state, replaced = _wake(state, policy, NS)
    assert any(cancel.order_id == top.order_id for cancel in replaced.cancels)
    new_buys = _buy_places(replaced)
    assert len(new_buys) == 1
    assert math.isclose(new_buys[0].price * new_buys[0].quantity, 100.0, abs_tol=0.01)
    acked = step(state=state, policy=policy, event=CancelAck(now_ns=NS, order_id=top.order_id))
    assert _buy_places(acked.plan) == ()
    assert acked.state.episode_buy_notional == 2.5


def test_filled_rung_requotes_a_full_buy_on_the_next_wake() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="full", qty=top.quantity, now_ns=0),
    ).state
    rung = next(item for item in state.rungs if item.index == top.level_index)
    assert rung.done is True
    state, refilled = _wake(state, policy, NS)
    buys = _buy_places(refilled)
    assert len(buys) == 1
    assert buys[0].level_index == top.level_index
    assert buys[0].price == top.price
    assert buys[0].quantity == top.quantity


def test_cutoff_during_submit_cancels_pending_and_holds_rung() -> None:
    state, policy, plan = _prime()
    pending = _buy_places(plan)[0]
    assert _by_id(state, pending.order_id).status == "pending"
    state = _feed(
        state,
        policy,
        NS,
        books=_balanced(ts_ns=NS),
        signal=_signal(delta=0.05, now_ns=0),
        second=540,
    )
    state, cut = _wake(state, policy, NS)
    assert _buy_places(cut) == ()
    assert any(cancel.order_id == pending.order_id for cancel in cut.cancels)
    assert rung_occupied(state=state, level_index=pending.level_index)
    acked = step(
        state=state, policy=policy, event=CancelAck(now_ns=NS, order_id=pending.order_id)
    ).state
    assert not any(order.order_id == pending.order_id for order in acked.orders)


def test_cancel_timeout_keeps_unknown_occupancy() -> None:
    state, policy, plan = _prime()
    pending = _buy_places(plan)[0]
    state = _feed(
        state,
        policy,
        NS,
        books=_balanced(ts_ns=NS),
        signal=_signal(delta=0.05, now_ns=0),
        second=540,
    )
    state, cut = _wake(state, policy, NS)
    assert any(cancel.order_id == pending.order_id for cancel in cut.cancels)
    timed = step(
        state=state, policy=policy, event=CancelTimeout(now_ns=NS, order_id=pending.order_id)
    ).state
    stuck = _by_id(timed, pending.order_id)
    assert stuck.status == "unknown"
    assert rung_occupied(state=timed, level_index=pending.level_index)
    later, again = _wake(timed, policy, 2 * NS)
    assert not any(place.level_index == pending.level_index for place in _buy_places(again))
    assert rung_occupied(state=later, level_index=pending.level_index)


def test_recovery_pending_still_places_exit() -> None:
    """grid-2965528-m3: recovery canceled the SELL and then never replaced it."""
    state, policy, _plan = _prime()
    state = replace(
        state,
        inventory=_inventory(no_qty=198.35),
        has_buy_fill=True,
    )
    out = step(
        state=state,
        policy=policy,
        event=Recovery(now_ns=NS, restored_buy_ids=()),
    )
    sells = _sell_places(out.plan)
    assert out.state.recovery_pending is True
    assert out.state.sell_only is True
    assert _buy_places(out.plan) == ()
    assert len(sells) == 1
    assert sells[0].token_index == 1
    assert sells[0].quantity == 198.35
    accepted = step(
        state=out.state,
        policy=policy,
        event=OrderAccepted(now_ns=NS, order_id=sells[0].order_id),
    ).state
    again = step(
        state=accepted,
        policy=policy,
        event=Recovery(now_ns=2 * NS, restored_buy_ids=()),
    )
    assert _by_id(again.state, sells[0].order_id).status != "canceling"


def test_live_sell_does_not_block_recovery_verified() -> None:
    state, policy, _plan = _prime()
    state = replace(
        state,
        inventory=_inventory(no_qty=198.35),
        has_buy_fill=True,
    )
    out = step(state=state, policy=policy, event=Recovery(now_ns=NS, restored_buy_ids=()))
    sell_id = _sell_places(out.plan)[0].order_id
    for cancel in out.plan.cancels:
        out = step(
            state=out.state, policy=policy, event=CancelAck(now_ns=NS, order_id=cancel.order_id)
        )
    accepted = step(
        state=out.state, policy=policy, event=OrderAccepted(now_ns=NS, order_id=sell_id)
    ).state
    verified = step(
        state=accepted,
        policy=policy,
        event=RecoveryVerified(
            now_ns=2 * NS,
            generation=accepted.recovery_generation,
            inventory=_inventory(no_qty=190.0),
        ),
    )
    assert verified.state.recovery_pending is False
    assert verified.state.inventory[1].qty == 190.0


def test_verified_empty_reopens_on_the_same_fresh_signal() -> None:
    """Acks close the idle episode. The still-fresh signal opens the next one."""
    state, policy, plan = _prime()
    assert state.episode_id == 1
    restored = _buy_places(plan)[0]
    out = step(
        state=state,
        policy=policy,
        event=Recovery(now_ns=NS, restored_buy_ids=(restored.order_id,)),
    )
    state = out.state
    for cancel in out.plan.cancels:
        state = step(
            state=state, policy=policy, event=CancelAck(now_ns=NS, order_id=cancel.order_id)
        ).state
    assert state.episode_id == 0
    verified = step(
        state=state,
        policy=policy,
        event=RecoveryVerified(
            now_ns=NS,
            generation=state.recovery_generation,
            inventory=_inventory(),
        ),
    )
    assert verified.state.sell_only is False
    assert verified.state.episode_id == 2
    assert [place.price for place in _buy_places(verified.plan)] == [0.50, 0.49, 0.48]
    later, later_plan = _wake(verified.state, policy, 2 * NS)
    assert later.episode_id == 2
    assert _buy_places(later_plan) == ()


def test_recovery_is_sell_only_and_cancels_restored_buy() -> None:
    state, policy, plan = _prime()
    restored = _buy_places(plan)[0]
    out = step(
        state=state,
        policy=policy,
        event=Recovery(now_ns=NS, restored_buy_ids=(restored.order_id,)),
    )
    assert out.state.sell_only is True
    assert out.state.recovery_pending is True
    assert _by_id(out.state, restored.order_id).status == "canceling"
    assert any(cancel.order_id == restored.order_id for cancel in out.plan.cancels)
    later, wake_plan = _wake(out.state, policy, 2 * NS)
    assert _buy_places(wake_plan) == ()
    assert later.sell_only is True
    assert later.recovery_pending is True


def test_recovery_verified_opens_sell_only() -> None:
    state, policy, plan = _prime()
    restored = _buy_places(plan)[0]
    out = step(
        state=state,
        policy=policy,
        event=Recovery(now_ns=NS, restored_buy_ids=(restored.order_id,)),
    )
    acked = step(
        state=out.state, policy=policy, event=CancelAck(now_ns=NS, order_id=restored.order_id)
    ).state
    for order in list(acked.orders):
        acked = step(
            state=acked, policy=policy, event=CancelAck(now_ns=NS, order_id=order.order_id)
        ).state
    stale = step(
        state=acked,
        policy=policy,
        event=RecoveryVerified(
            now_ns=NS,
            generation=0,
            inventory=_inventory(yes_qty=10.0),
        ),
    ).state
    assert stale.recovery_pending is True
    verified = step(
        state=acked,
        policy=policy,
        event=RecoveryVerified(
            now_ns=NS,
            generation=acked.recovery_generation,
            inventory=_inventory(yes_qty=10.0),
        ),
    )
    assert verified.state.recovery_pending is False
    assert verified.state.sell_only is True
    assert verified.state.position.qty == 10.0


def test_recovery_verified_empty_inventory_reopens_buys() -> None:
    state, policy, _plan = _prime()
    recovered = step(
        state=state, policy=policy, event=Recovery(now_ns=NS, restored_buy_ids=())
    ).state
    for order in list(recovered.orders):
        recovered = step(
            state=recovered,
            policy=policy,
            event=CancelAck(now_ns=NS, order_id=order.order_id),
        ).state
    verified = step(
        state=recovered,
        policy=policy,
        event=RecoveryVerified(
            now_ns=NS,
            generation=recovered.recovery_generation,
            inventory=_inventory(),
        ),
    ).state
    assert verified.recovery_pending is False
    assert verified.sell_only is False


def test_recovery_verified_with_inventory_stays_sell_only() -> None:
    state, policy, _plan = _prime()
    recovered = step(
        state=state, policy=policy, event=Recovery(now_ns=NS, restored_buy_ids=())
    ).state
    for order in list(recovered.orders):
        recovered = step(
            state=recovered,
            policy=policy,
            event=CancelAck(now_ns=NS, order_id=order.order_id),
        ).state
    verified = step(
        state=recovered,
        policy=policy,
        event=RecoveryVerified(
            now_ns=NS,
            generation=recovered.recovery_generation,
            inventory=_inventory(yes_qty=10.0),
        ),
    ).state
    assert verified.recovery_pending is False
    assert verified.sell_only is True


def test_submit_timeout_does_not_drop_pending() -> None:
    state, policy, plan = _prime()
    pending = _buy_places(plan)[0]
    timed = step(
        state=state, policy=policy, event=SubmitTimeout(now_ns=0, order_id=pending.order_id)
    ).state
    stuck = _by_id(timed, pending.order_id)
    assert stuck.status == "unknown"
    assert rung_occupied(state=timed, level_index=pending.level_index)


def test_settling_cancels_live_sell_then_places_after_ten_seconds() -> None:
    state, policy, plan = _prime()
    top, mid, _bottom = _buy_places(plan)
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f-buy", qty=10.0, now_ns=0),
    ).state
    settle_end = 10 * NS
    state = _feed(
        state,
        policy,
        settle_end,
        books=_balanced(ts_ns=settle_end),
        signal=_signal(delta=0.05, now_ns=0, anchor=0.51),
        second=100,
    )
    state, placed = _wake(state, policy, settle_end)
    sells = _sell_places(placed)
    assert sells
    sell_id = sells[0].order_id
    state = step(
        state=state, policy=policy, event=OrderAccepted(now_ns=settle_end, order_id=sell_id)
    ).state
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(mid, fill_id="f-buy2", qty=5.0, now_ns=settle_end),
    ).state
    inside = settle_end + NS
    state = _feed(
        state,
        policy,
        inside,
        books=_balanced(ts_ns=inside),
        signal=_signal(delta=0.05, now_ns=0, anchor=0.51),
        second=100,
    )
    state, early = _wake(state, policy, inside)
    assert any(cancel.order_id == sell_id for cancel in early.cancels)
    state = step(state=state, policy=policy, event=CancelAck(now_ns=inside, order_id=sell_id)).state
    done = settle_end + 10 * NS
    state = _feed(
        state,
        policy,
        done,
        books=_balanced(ts_ns=done),
        signal=_signal(delta=0.05, now_ns=0, anchor=0.51),
        second=100,
    )
    _, late = _wake(state, policy, done)
    assert _sell_places(late)


def test_dust_keeps_episode_and_skips_sell() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    dust = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f-dust", qty=4.0, now_ns=0),
    ).state
    assert dust.position.qty == 4.0
    assert dust.episode_id != 0
    later, plan = _wake(dust, policy, 10 * NS)
    assert _sell_places(plan) == ()
    assert later.position.qty == 4.0
    assert later.episode_token_index is not None


def _ack_orders(state: StrategyState, policy: Follow300Policy, now_ns: int) -> StrategyState:
    for order in tuple(state.orders):
        state = step(
            state=state, policy=policy, event=CancelAck(now_ns=now_ns, order_id=order.order_id)
        ).state
    return state


def test_unsellable_partial_without_done_rung_keeps_episode() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f-part", qty=4.0, now_ns=0),
    ).state
    state = _ack_orders(state, policy, 0)
    assert state.episode_id != 0
    assert state.position.qty == 4.0
    live_buys = [order for order in state.orders if order.side == "BUY"]
    assert len(live_buys) == 3


def test_sell_to_dust_after_done_rung_starts_fresh_ladder() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f-buy", qty=top.quantity, now_ns=0),
    ).state
    settle = 10 * NS
    state, exiting = _wake(
        _feed(
            state,
            policy,
            settle,
            books=_balanced(ts_ns=settle),
            signal=_signal(delta=0.0, now_ns=settle),
        ),
        policy,
        settle,
    )
    for cancel in exiting.cancels:
        state = step(
            state=state, policy=policy, event=CancelAck(now_ns=settle, order_id=cancel.order_id)
        ).state
    sells = _sell_places(exiting)
    assert sells
    state = step(
        state=state, policy=policy, event=OrderAccepted(now_ns=settle, order_id=sells[0].order_id)
    ).state
    leftover = 0.02
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(sells[0], fill_id="f-sell", qty=top.quantity - leftover, now_ns=settle),
    ).state
    assert state.episode_id == 0
    assert math.isclose(state.position.qty, leftover)
    later, opened = _wake(
        _feed(
            state,
            policy,
            settle + NS,
            books=_balanced(ts_ns=settle + NS),
            signal=_signal(delta=0.05, now_ns=settle + NS),
        ),
        policy,
        settle + NS,
    )
    assert later.episode_id != 0
    assert len(_buy_places(opened)) == 3


def test_dust_sell_after_episode_end_does_not_latch_winding_down() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f-buy", qty=top.quantity, now_ns=0),
    ).state
    settle = 10 * NS
    state, exiting = _wake(
        _feed(
            state,
            policy,
            settle,
            books=_balanced(ts_ns=settle),
            signal=_signal(delta=0.0, now_ns=settle),
        ),
        policy,
        settle,
    )
    for cancel in exiting.cancels:
        state = step(
            state=state, policy=policy, event=CancelAck(now_ns=settle, order_id=cancel.order_id)
        ).state
    sell = _sell_places(exiting)[0]
    state = step(
        state=state, policy=policy, event=OrderAccepted(now_ns=settle, order_id=sell.order_id)
    ).state
    dust = 0.02
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(sell, fill_id="f-sell", qty=top.quantity - dust, now_ns=settle),
    ).state
    assert state.episode_id == 0
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(sell, fill_id="f-dust", qty=dust, now_ns=settle + 1),
    ).state
    assert state.position.qty == 0.0
    assert state.winding_down is False
    _, opened = _wake(
        _feed(
            state,
            policy,
            settle + NS,
            books=_balanced(ts_ns=settle + NS),
            signal=_signal(delta=0.05, now_ns=settle + NS),
        ),
        policy,
        settle + NS,
    )
    assert _buy_places(opened)


def _dust_sell_with(
    *, yes_qty: float, no_qty: float
) -> tuple[StrategyState, Follow300Policy, PlaceOrder, int]:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f-buy", qty=top.quantity, now_ns=0),
    ).state
    settle = 10 * NS
    state, exiting = _wake(
        _feed(
            state,
            policy,
            settle,
            books=_balanced(ts_ns=settle),
            signal=_signal(delta=0.0, now_ns=settle),
        ),
        policy,
        settle,
    )
    for cancel in exiting.cancels:
        state = step(
            state=state, policy=policy, event=CancelAck(now_ns=settle, order_id=cancel.order_id)
        ).state
    sell = _sell_places(exiting)[0]
    state = step(
        state=state, policy=policy, event=OrderAccepted(now_ns=settle, order_id=sell.order_id)
    ).state
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(sell, fill_id="f-sell", qty=top.quantity - 0.02, now_ns=settle),
    ).state
    inventory = (
        TokenInventory(token_index=0, qty=yes_qty, cost_basis=0.0, last_buy_ns=settle),
        TokenInventory(token_index=1, qty=no_qty, cost_basis=0.0, last_buy_ns=settle),
    )
    later = settle + 30 * NS
    state = _feed(
        replace(state, inventory=inventory),
        policy,
        later,
        books=_balanced(ts_ns=later),
        signal=_signal(delta=0.0, now_ns=later),
    )
    return state, policy, sell, later


def test_dust_sell_does_not_hold_exit_for_other_token() -> None:
    state, policy, sell, later = _dust_sell_with(yes_qty=0.02, no_qty=100.0)
    assert sell.token_index == 0
    assert not sell_occupied(state=state)
    _, plan = _wake(state, policy, later)
    assert [place.token_index for place in _sell_places(plan)] == [1]
    assert not any(cancel.order_id == sell.order_id for cancel in plan.cancels)


def test_dust_sell_reprices_when_its_token_regrows() -> None:
    state, policy, sell, later = _dust_sell_with(yes_qty=50.0, no_qty=0.0)
    _, plan = _wake(state, policy, later)
    assert any(cancel.order_id == sell.order_id for cancel in plan.cancels)
    assert not _sell_places(plan)


def test_subtick_sell_residue_flattens() -> None:
    state, _policy, _plan = _prime()
    state = replace(
        state,
        inventory=(
            TokenInventory(token_index=0, qty=10.008926, cost_basis=4.0, last_buy_ns=None),
            state.inventory[1],
        ),
    )
    state = apply_fill(
        state=state,
        event=Fill(
            now_ns=0,
            fill_id="f-subtick",
            order_id="missing",
            qty=10.0,
            price=0.44,
            token_index=0,
            side="SELL",
        ),
    )
    assert state.inventory[0].qty == 0.0
    assert state.inventory[0].cost_basis == 0.0


def test_short_budget_skips_rung_without_shrinking_size() -> None:
    join = 0.50
    one_rung = join * buy_share_quantity(base_size_usdc=100.0, price=join)
    state, policy, plan = _prime(usdc=one_rung + 1.0)
    buys = _buy_places(plan)
    assert len(buys) == 1
    assert buys[0].quantity == buy_share_quantity(base_size_usdc=100.0, price=buys[0].price)
    assert rung_occupied(state=state, level_index=0)
    assert not rung_occupied(state=state, level_index=1)
    assert not rung_occupied(state=state, level_index=2)
    assert tuple(rung.price for rung in state.rungs) == (0.50, 0.49, 0.48)
    leftover = one_rung + 1.0 - buys[0].price * buys[0].quantity
    state = step(
        state=state,
        policy=policy,
        event=BudgetUpdate(
            now_ns=0,
            budget=Budget(
                cash_usdc=leftover, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
            ),
        ),
    ).state
    state, second = _wake(state, policy, NS)
    assert tuple(rung.price for rung in state.rungs) == (0.50, 0.49, 0.48)
    assert _buy_places(second) == ()
    _, _, none = _prime(usdc=one_rung - 1.0)
    assert _buy_places(none) == ()
    assert none.block_reason == "no_cash"


def test_extraction_policy_sell_guards_off() -> None:
    policy = extraction_policy(level_usdc=100.0)
    assert policy.sell_min_life_s == 0.0
    assert policy.hold_unconfirmed_sell is False
    assert _idle().permissions.sell_unconfirmed is False


def _open_live_sell(
    policy: Follow300Policy, *, fill_qty: float = 10.0
) -> tuple[StrategyState, PlaceOrder]:
    state, plan = _wake(
        _feed(
            _idle(),
            policy,
            0,
            books=_balanced(ts_ns=0),
            signal=_signal(delta=0.05, now_ns=0),
        ),
        policy,
        0,
    )
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="f-buy", qty=fill_qty, now_ns=0),
    ).state
    settle = 10 * NS
    state, placed = _wake(
        _feed(
            state,
            policy,
            settle,
            books=_balanced(ts_ns=settle),
            signal=_signal(delta=0.05, now_ns=0),
        ),
        policy,
        settle,
    )
    sells = _sell_places(placed)
    assert sells
    state = step(
        state=state, policy=policy, event=OrderAccepted(now_ns=settle, order_id=sells[0].order_id)
    ).state
    return state, sells[0]


def test_sell_min_life_keeps_then_reprices() -> None:
    policy = replace(_policy(), hold_unconfirmed_sell=False)
    state, sell = _open_live_sell(policy)
    held_ns = 10 * NS + NS // 2
    state, held = _wake(
        _feed(
            state,
            policy,
            held_ns,
            books=_balanced(ts_ns=held_ns),
            signal=_signal(delta=0.02, now_ns=held_ns),
        ),
        policy,
        held_ns,
    )
    assert any(keep.order_id == sell.order_id for keep in held.keep)
    assert not any(cancel.order_id == sell.order_id for cancel in held.cancels)
    later_ns = 10 * NS + round(SELL_MIN_LIFE_SECONDS * 1e9)
    state, later = _wake(
        _feed(
            state,
            policy,
            later_ns,
            books=_balanced(ts_ns=later_ns),
            signal=_signal(delta=0.02, now_ns=later_ns),
        ),
        policy,
        later_ns,
    )
    assert any(cancel.order_id == sell.order_id for cancel in later.cancels)
    replaced = step(
        state=state, policy=policy, event=CancelAck(now_ns=later_ns, order_id=sell.order_id)
    ).plan
    assert _sell_places(replaced)
    assert _sell_places(replaced)[0].price != sell.price


def test_sell_unconfirmed_holds_sell_while_buys_continue() -> None:
    policy = _policy()
    state, sell = _open_live_sell(policy)
    state = step(
        state=state,
        policy=policy,
        event=PermissionsUpdate(
            now_ns=10 * NS,
            permissions=Permissions(
                halt=False,
                reduce_only=False,
                allow_buy=True,
                allow_sell=True,
                sell_unconfirmed=True,
            ),
        ),
    ).state
    later_ns = 12 * NS
    state, held = _wake(
        _feed(
            state,
            policy,
            later_ns,
            books=_balanced(ts_ns=later_ns),
            signal=_signal(delta=0.02, now_ns=later_ns),
        ),
        policy,
        later_ns,
    )
    assert any(keep.order_id == sell.order_id for keep in held.keep)
    assert not any(cancel.order_id == sell.order_id for cancel in held.cancels)
    assert _buy_places(held) or held.keep


def test_undersized_sell_is_replaced_after_more_buys() -> None:
    policy = replace(_policy(), sell_min_life_s=0.0, hold_unconfirmed_sell=True)
    state, sell = _open_live_sell(policy, fill_qty=40.0)
    state = step(
        state=state,
        policy=policy,
        event=PermissionsUpdate(
            now_ns=10 * NS,
            permissions=Permissions(
                halt=False,
                reduce_only=False,
                allow_buy=True,
                allow_sell=True,
                sell_unconfirmed=True,
            ),
        ),
    ).state
    state = step(
        state=state, policy=policy, event=_fill_of(sell, fill_id="s-part", qty=10.0, now_ns=10 * NS)
    ).state
    l1 = next(order for order in state.orders if order.side == "BUY" and order.level_index == 1)
    buy_ns = 11 * NS
    state = step(
        state=state, policy=policy, event=_fill_of(l1, fill_id="b1", qty=40.0, now_ns=buy_ns)
    ).state
    later_ns = buy_ns + 10 * NS
    _, plan = _wake(
        _feed(
            state,
            policy,
            later_ns,
            books=_balanced(ts_ns=later_ns),
            signal=_signal(delta=0.05, now_ns=later_ns),
        ),
        policy,
        later_ns,
    )
    assert any(cancel.order_id == sell.order_id for cancel in plan.cancels)


def test_undersized_sell_is_replaced_when_delta_blocks_buys() -> None:
    policy = replace(_policy(), sell_min_life_s=0.0, hold_unconfirmed_sell=False)
    state, sell = _open_live_sell(policy, fill_qty=40.0)
    state = step(
        state=state, policy=policy, event=_fill_of(sell, fill_id="s-part", qty=10.0, now_ns=10 * NS)
    ).state
    l1 = next(order for order in state.orders if order.side == "BUY" and order.level_index == 1)
    buy_ns = 11 * NS
    state = step(
        state=state, policy=policy, event=_fill_of(l1, fill_id="b1", qty=40.0, now_ns=buy_ns)
    ).state
    later_ns = buy_ns + 10 * NS
    _, plan = _wake(
        _feed(
            state,
            policy,
            later_ns,
            books=_balanced(ts_ns=later_ns),
            signal=_signal(delta=0.009, now_ns=later_ns),
        ),
        policy,
        later_ns,
    )
    assert any(cancel.order_id == sell.order_id for cancel in plan.cancels)


def test_refilled_rung_buy_is_kept_on_second_wake() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="full", qty=top.quantity, now_ns=0),
    ).state
    settle = 10 * NS
    state, exiting = _wake(
        _feed(
            state,
            policy,
            settle,
            books=_balanced(ts_ns=settle),
            signal=_signal(delta=0.05, now_ns=0),
        ),
        policy,
        settle,
    )
    sells = _sell_places(exiting)
    assert sells
    state = step(
        state=state, policy=policy, event=OrderAccepted(now_ns=settle, order_id=sells[0].order_id)
    ).state
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(sells[0], fill_id="s-half", qty=top.quantity / 2, now_ns=settle),
    ).state
    later = settle + 10 * NS
    state, placed = _wake(
        _feed(
            state,
            policy,
            later,
            books=_balanced(ts_ns=later),
            signal=_signal(delta=0.05, now_ns=later),
        ),
        policy,
        later,
    )
    for cancel in placed.cancels:
        state = step(
            state=state, policy=policy, event=CancelAck(now_ns=later, order_id=cancel.order_id)
        ).state
    refill = next(
        order
        for order in state.orders
        if order.side == "BUY" and order.level_index == 0 and order.status != "canceling"
    )
    if refill.status == "pending":
        state = step(
            state=state,
            policy=policy,
            event=OrderAccepted(now_ns=later, order_id=refill.order_id),
        ).state
    held_id = refill.order_id
    state, again = _wake(
        _feed(
            state,
            policy,
            later + NS,
            books=_balanced(ts_ns=later + NS),
            signal=_signal(delta=0.05, now_ns=later),
        ),
        policy,
        later + NS,
    )
    assert not any(cancel.order_id == held_id for cancel in again.cancels)
    assert any(keep.order_id == held_id for keep in again.keep)


def test_done_rung_follows_the_book_price() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="full", qty=top.quantity, now_ns=0),
    ).state
    later = NS
    state, out = _wake(
        _feed(
            state,
            policy,
            later,
            books=_balanced(ts_ns=later, bid0=0.48, ask0=0.50),
            signal=_signal(delta=0.05, now_ns=later, anchor=0.49),
        ),
        policy,
        later,
    )
    assert tuple(rung.price for rung in state.rungs) == (0.48, 0.47, 0.46)
    assert tuple(place.price for place in _buy_places(out)) == (0.46,)


def test_dust_rung_is_requoted_at_full_size() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="almost", qty=top.quantity - 0.01, now_ns=0),
    ).state
    out = step(state=state, policy=policy, event=CancelAck(now_ns=0, order_id=top.order_id))
    refill = _buy_places(out.plan)[0]
    assert refill.level_index == 0
    assert math.isclose(refill.price * refill.quantity, 100.0, abs_tol=0.01)


def test_canceling_sell_is_not_held() -> None:
    policy = _policy()
    state, sell = _open_live_sell(policy)
    state = mark_canceling(state=state, order_id=sell.order_id, reason="halt")
    state = step(
        state=state,
        policy=policy,
        event=PermissionsUpdate(
            now_ns=10 * NS + NS // 2,
            permissions=Permissions(
                halt=False,
                reduce_only=False,
                allow_buy=True,
                allow_sell=True,
                sell_unconfirmed=True,
            ),
        ),
    ).state
    later_ns = 10 * NS + NS // 2
    _, plan = _wake(
        _feed(
            state,
            policy,
            later_ns,
            books=_balanced(ts_ns=later_ns),
            signal=_signal(delta=0.05, now_ns=0),
        ),
        policy,
        later_ns,
    )
    assert not _sell_places(plan)


def test_limits_update_raises_sell_dust_floor() -> None:
    policy = _policy()
    state, _sell = _open_live_sell(policy, fill_qty=10.0)
    state = step(
        state=state,
        policy=policy,
        event=CancelAck(now_ns=10 * NS, order_id=_sell.order_id),
    ).state
    state = step(
        state=state,
        policy=policy,
        event=LimitsUpdate(now_ns=10 * NS, limits=replace(state.limits, min_order_size=20.0)),
    ).state
    later_ns = 12 * NS
    _, plan = _wake(
        _feed(
            state,
            policy,
            later_ns,
            books=_balanced(ts_ns=later_ns),
            signal=_signal(delta=0.05, now_ns=0),
        ),
        policy,
        later_ns,
    )
    assert _sell_places(plan) == ()


def test_first_signal_after_long_gap_is_accepted() -> None:
    state, policy, _plan = _prime()
    gap = 100 * NS
    state, plan = _wake(
        _feed(
            state,
            policy,
            gap,
            books=_balanced(ts_ns=gap),
            signal=_signal(delta=0.05, now_ns=gap),
        ),
        policy,
        gap,
    )
    assert _buy_places(plan) == ()
    assert plan.keep
    assert state.latch is not None
    assert state.latch.fair_ts_ns == gap
    assert state.latch.fair_valid is True


def test_reanchor_leaves_an_anchor_block_until_the_next_tick() -> None:
    """A failed anchor stays blocked across the re-anchor window."""
    state, policy, plan = _prime(anchor=0.60)
    assert plan.block_reason == "anchor"
    assert _buy_places(plan) == ()
    latch = state.latch
    assert latch is not None
    assert latch.fair_valid is False
    assert latch.no_buy_reason == "anchor"

    later_ns = NS // 2
    state = step(
        state=state,
        policy=policy,
        event=BookUpdate(now_ns=later_ns, books=_balanced(ts_ns=later_ns)),
    ).state
    state, plan = _wake(state, policy, later_ns)
    assert plan.block_reason == "anchor"
    assert _buy_places(plan) == ()
    latch = state.latch
    assert latch is not None
    assert latch.fair_valid is False
    assert latch.no_buy_reason == "anchor"


def test_reanchor_reprices_a_thin_l0() -> None:
    policy = _policy()
    state, plan = _prime_policy(policy, books=_full(0))
    l0 = next(place.order_id for place in _buy_places(plan) if place.level_index == 0)
    state = _accept_all(state, policy, 0)
    later_ns = round(LATCH_REANCHOR_SECONDS * NS) + NS // 10
    state = step(
        state=state, policy=policy, event=BookUpdate(now_ns=later_ns, books=_thin(later_ns))
    ).state
    out = step(state=state, policy=policy, event=Wake(now_ns=later_ns, forced=True))
    assert out.plan.cancels == (CancelOrder(order_id=l0, reason="reprice"),)


def test_latch_reanchors_to_book_between_signals() -> None:
    """Held delta re-anchors to the live book; entry staleness is unchanged."""
    reanchor_ns = round(LATCH_REANCHOR_SECONDS * NS)
    state, policy, plan = _prime()
    assert tuple(place.price for place in _buy_places(plan)) == (0.50, 0.49, 0.48)
    latch = state.latch
    assert latch is not None
    assert math.isclose(latch.fair_radiant, 0.56)
    assert latch.anchored_ns == 0

    # Book moves 3¢ with no new signal; inside the window nothing re-prices.
    early_ns = reanchor_ns - NS // 10
    state = step(
        state=state,
        policy=policy,
        event=BookUpdate(now_ns=early_ns, books=_balanced(ts_ns=early_ns, bid0=0.53, ask0=0.55)),
    ).state
    state, plan = _wake(state, policy, early_ns)
    assert _buy_places(plan) == ()
    assert plan.cancels == ()
    latch = state.latch
    assert latch is not None
    assert math.isclose(latch.fair_radiant, 0.56)
    assert latch.anchored_ns == 0

    # Past the window the latch re-anchors: fair = live book + held delta.
    later_ns = reanchor_ns + NS // 10
    state = step(
        state=state,
        policy=policy,
        event=BookUpdate(now_ns=later_ns, books=_balanced(ts_ns=later_ns, bid0=0.53, ask0=0.55)),
    ).state
    state, plan = _wake(state, policy, later_ns)
    latch = state.latch
    assert latch is not None
    assert math.isclose(latch.fair_radiant, 0.59)
    assert math.isclose(latch.book_p_radiant, 0.54)
    assert latch.fair_ts_ns == 0
    assert latch.anchored_ns == later_ns
    # Reprice is cancel → ack → place: rungs stay occupied while cancels fly.
    assert _buy_places(plan) == ()
    assert {cancel.reason for cancel in plan.cancels} == {"reprice"}
    repriced: list[float] = []
    for cancel in plan.cancels:
        out = step(
            state=state,
            policy=policy,
            event=CancelAck(now_ns=later_ns, order_id=cancel.order_id),
        )
        state = out.state
        repriced.extend(place.price for place in _buy_places(out.plan))
    assert tuple(repriced) == (0.53, 0.52, 0.51)

    # 16 s after the signal the BUYs still drop with stale_signal.
    stale_ns = 16 * NS + NS // 2
    state = step(
        state=state,
        policy=policy,
        event=BookUpdate(now_ns=stale_ns, books=_balanced(ts_ns=stale_ns, bid0=0.53, ask0=0.55)),
    ).state
    state, plan = _wake(state, policy, stale_ns)
    assert plan.block_reason == "stale_signal"
    assert _buy_places(plan) == ()
    assert all(order.status == "canceling" for order in state.orders)


def test_deep_ladder_past_zero_does_not_divide_by_zero() -> None:
    # A deep rung prices at or below 0.00; the reject check must run before sizing.
    policy = replace(_policy(), level_count=60, min_entry_price=0.0)
    now_ns = 0
    state = _feed(
        _idle(now_ns=now_ns),
        policy,
        now_ns,
        books=_balanced(ts_ns=now_ns),
        signal=_signal(delta=0.05, now_ns=now_ns),
    )
    state, plan = _wake(state, policy, now_ns)
    assert all(place.price > 0.0 for place in _buy_places(plan))
