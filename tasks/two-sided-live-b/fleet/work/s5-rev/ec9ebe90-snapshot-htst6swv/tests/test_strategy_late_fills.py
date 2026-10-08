"""Late-fill ownership: old-episode fills do not mutate the new episode."""

# pyright: reportPrivateUsage=false

import math
from dataclasses import replace

from test_strategy_core import (
    NS,
    _accept_all,
    _balanced,
    _buy_places,
    _by_id,
    _feed,
    _fill_of,
    _idle,
    _inventory,
    _prime,
    _sell_places,
    _signal,
    _wake,
)

from shared.utils.trading import HALF_SHARE_TICK
from strategy.budget import reserve_buy_notional
from strategy.engine import step
from strategy.lifecycle import (
    already_canceling,
    apply_buy_settled,
    apply_cancel_ack,
    apply_cancel_timeout,
    apply_cancel_unsettled,
    apply_fill,
    apply_submit_timeout,
    has_unresolved_orders,
    mark_canceling,
    replace_order,
)
from strategy.policy import Follow300Policy
from strategy.types import (
    CancelAck,
    CancelTimeout,
    CancelUnsettled,
    Fill,
    KillGate,
    KillGateUpdate,
    KillWait,
    OwnershipResolved,
    Permissions,
    PermissionsUpdate,
    Recovery,
    RecoveryVerified,
    StrategyState,
)


def _ghost(*, fill_id: str, order_id: str, qty: float, now_ns: int) -> Fill:
    return Fill(
        now_ns=now_ns,
        fill_id=fill_id,
        order_id=order_id,
        qty=qty,
        price=0.50,
        token_index=0,
        side="BUY",
    )


def _cancel_all(state: StrategyState, policy: Follow300Policy, now_ns: int) -> StrategyState:
    state = _feed(
        state,
        policy,
        now_ns,
        books=_balanced(ts_ns=now_ns),
        signal=_signal(delta=0.05, now_ns=0),
        second=540,
    )
    state, plan = _wake(state, policy, now_ns)
    for cancel in plan.cancels:
        state = step(
            state=state, policy=policy, event=CancelAck(now_ns=now_ns, order_id=cancel.order_id)
        ).state
    return state


def test_late_fill_after_cancel_credits_old_episode_not_e2() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    e1 = state.episode_id
    state = _cancel_all(state, policy, NS)
    late = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="late", qty=10.0, now_ns=2 * NS),
    ).state
    assert late.inventory[top.token_index].qty == 10.0
    assert late.pending_ownership == ()
    archive = next((item for item in late.archives if item.episode_id == e1), None)
    assert archive is not None
    assert top.level_index is not None
    rung = next(item for item in archive.rungs if item.index == top.level_index)
    assert rung.filled_qty == 10.0
    assert archive.episode_buy_notional == 5.0
    assert late.episode_buy_notional == 0.0
    state = _feed(
        late,
        policy,
        3 * NS,
        books=_balanced(ts_ns=3 * NS),
        signal=_signal(delta=-0.05, now_ns=3 * NS),
        second=100,
    )
    state, placed = _wake(state, policy, 3 * NS)
    assert _buy_places(placed) == ()
    assert placed.block_reason == "position_open"
    assert state.episode_id == 0
    assert state.episode_token_index is None
    assert state.inventory[top.token_index].qty == 10.0


def test_late_fill_opposite_token_does_not_merge() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    e1 = state.episode_id
    state = _cancel_all(state, policy, NS)
    state = _feed(
        state,
        policy,
        2 * NS,
        books=_balanced(ts_ns=2 * NS),
        signal=_signal(delta=-0.05, now_ns=2 * NS),
        second=100,
    )
    state, placed = _wake(state, policy, 2 * NS)
    e2_buys = _buy_places(placed)
    assert e2_buys
    assert e2_buys[0].token_index == 1
    state = _accept_all(state, policy, 2 * NS)
    e2_top = e2_buys[0]
    e2 = state.episode_id
    state = step(
        state=state,
        policy=policy,
        event=_fill_of(e2_top, fill_id="e2", qty=8.0, now_ns=2 * NS),
    ).state
    late = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="e1-late", qty=10.0, now_ns=3 * NS),
    ).state
    assert late.inventory[0].qty == 10.0
    assert late.inventory[1].qty == 8.0
    assert late.episode_id == e2
    assert late.episode_id != e1
    e2_rung = next(item for item in late.rungs if item.index == e2_top.level_index)
    assert e2_rung.filled_qty == 8.0
    assert late.episode_buy_notional == 8.0 * e2_top.price
    assert not any(rung.live_id == top.order_id for rung in late.rungs)


def test_unknown_fill_blocks_buy_until_resolved_once() -> None:
    state, policy, _plan = _prime()
    ghost = step(
        state=state,
        policy=policy,
        event=_ghost(fill_id="ghost", order_id="missing", qty=6.0, now_ns=NS),
    ).state
    assert ghost.pending_ownership
    assert ghost.inventory[0].qty == 6.0
    state = _feed(
        ghost,
        policy,
        2 * NS,
        books=_balanced(ts_ns=2 * NS),
        signal=_signal(delta=0.05, now_ns=2 * NS),
        second=100,
    )
    state, blocked = _wake(state, policy, 2 * NS)
    assert blocked.block_reason == "ownership_unresolved"
    assert _buy_places(blocked) == ()
    proven = step(
        state=state,
        policy=policy,
        event=OwnershipResolved(
            now_ns=3 * NS,
            order_id="missing",
            episode_id=1,
            token_index=0,
            side="BUY",
            price=0.50,
            submitted_qty=200.0,
            filled_qty=0.0,
            level_index=0,
            terminal=True,
        ),
    ).state
    assert proven.pending_ownership == ()
    assert proven.inventory[0].qty == 6.0
    assert proven.episode_buy_notional == 3.0
    assert "ghost" in proven.seen_fill_ids
    again = step(
        state=proven,
        policy=policy,
        event=_ghost(fill_id="ghost", order_id="missing", qty=6.0, now_ns=4 * NS),
    ).state
    assert again.inventory[0].qty == 6.0
    assert again.episode_buy_notional == 3.0


def test_contradictory_token_blocks_buy() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    fill = _fill_of(top, fill_id="bad", qty=5.0, now_ns=0)
    bad = replace(fill, token_index=1)
    state = step(state=state, policy=policy, event=bad).state
    assert state.pending_ownership
    assert state.inventory[0].qty == 0.0
    state, blocked = _wake(
        _feed(
            state,
            policy,
            NS,
            books=_balanced(ts_ns=NS),
            signal=_signal(delta=0.05, now_ns=0),
            second=100,
        ),
        policy,
        NS,
    )
    assert blocked.block_reason == "ownership_unresolved"


def test_duplicate_late_fill_is_ignored() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = _cancel_all(state, policy, NS)
    first = step(
        state=state, policy=policy, event=_fill_of(top, fill_id="late", qty=7.0, now_ns=2 * NS)
    ).state
    second = step(
        state=first, policy=policy, event=_fill_of(top, fill_id="late", qty=7.0, now_ns=3 * NS)
    ).state
    assert second.inventory[top.token_index].qty == first.inventory[top.token_index].qty == 7.0


def test_two_distinct_late_partials() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = _cancel_all(state, policy, NS)
    state = step(
        state=state, policy=policy, event=_fill_of(top, fill_id="p1", qty=5.0, now_ns=2 * NS)
    ).state
    state = step(
        state=state, policy=policy, event=_fill_of(top, fill_id="p2", qty=6.0, now_ns=3 * NS)
    ).state
    assert state.inventory[top.token_index].qty == 11.0


def test_ownership_resolution_does_not_clear_recovery() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    recovered = step(
        state=state,
        policy=policy,
        event=Recovery(now_ns=NS, restored_buy_ids=(top.order_id,)),
    ).state
    assert recovered.sell_only is True
    ghost = step(
        state=recovered,
        policy=policy,
        event=_ghost(fill_id="u1", order_id="gone", qty=5.0, now_ns=NS),
    ).state
    resolved = step(
        state=ghost,
        policy=policy,
        event=OwnershipResolved(
            now_ns=2 * NS,
            order_id="gone",
            episode_id=1,
            token_index=0,
            side="BUY",
            price=0.50,
            submitted_qty=200.0,
            filled_qty=0.0,
            level_index=0,
            terminal=True,
        ),
    ).state
    assert resolved.sell_only is True
    assert resolved.pending_ownership == ()


def test_both_token_dust_is_not_flat() -> None:
    state = replace(_idle(), inventory=_inventory(yes_qty=4.0, no_qty=3.0))
    assert state.position.qty == 4.0
    assert state.inventory[0].qty == 4.0
    assert state.inventory[1].qty == 3.0


def _rung_at(state: StrategyState, level_index: int | None):
    return next(rung for rung in state.rungs if rung.index == level_index)


def _record(state: StrategyState, order_id: str):
    return next(record for record in state.records if record.order_id == order_id)


def _gone_clip():
    state, policy, plan = _prime()
    state = _accept_all(state, policy, 0)
    top = _buy_places(plan)[0]
    clipped = replace(_by_id(state, top.order_id), submitted_qty=10.0)
    state = replace_order(state=state, order=clipped)
    state = apply_cancel_unsettled(state=state, order_id=top.order_id)
    return state, _by_id(state, top.order_id)


def test_hidden_fill_keeps_gone_rungs_until_the_fill() -> None:
    state, policy, _plan = _prime()
    state = _accept_all(state, policy, 0)
    episode = state.episode_id
    state = _feed(
        state,
        policy,
        NS,
        books=_balanced(ts_ns=NS),
        signal=_signal(delta=0.05, now_ns=0),
        second=540,
    )
    state, cut = _wake(state, policy, NS)
    assert cut.cancels
    for cancel in cut.cancels:
        before = _by_id(state, cancel.order_id)
        state = step(
            state=state,
            policy=policy,
            event=CancelUnsettled(now_ns=NS, order_id=cancel.order_id),
        ).state
        gone = _by_id(state, cancel.order_id)
        assert gone.status == "gone"
        assert gone.filled_qty == before.filled_qty
        assert gone.submitted_qty == before.submitted_qty
        assert gone.cancel_reason == before.cancel_reason
        assert gone.ack_reason == before.ack_reason
        assert gone.level_index == before.level_index
        assert _rung_at(state, gone.level_index).live_id == gone.order_id
    assert state.episode_id == episode
    assert state.inventory[0].qty == 0.0
    assert state.inventory[1].qty == 0.0
    assert state.episode_buy_notional == 0.0
    assert has_unresolved_orders(state=state) is False
    now = 2 * NS
    state = _feed(
        state,
        policy,
        now,
        books=_balanced(ts_ns=now),
        signal=_signal(delta=0.05, now_ns=now),
        second=100,
    )
    state, quiet = _wake(state, policy, now)
    occupied = {order.level_index for order in state.orders}
    assert occupied
    assert _buy_places(quiet) == ()
    assert quiet.block_reason == ""
    top = next(order for order in state.orders if order.level_index == 0)
    others = {order.order_id for order in state.orders if order.order_id != top.order_id}
    filled = step(
        state=state,
        policy=policy,
        event=_fill_of(top, fill_id="hidden", qty=top.submitted_qty, now_ns=3 * NS),
    ).state
    assert filled.inventory[top.token_index].qty == top.submitted_qty
    assert filled.episode_buy_notional == top.submitted_qty * top.price
    assert not any(order.order_id == top.order_id for order in filled.orders)
    record = _record(filled, top.order_id)
    assert record.terminal is True
    assert record.filled_qty == top.submitted_qty
    assert _rung_at(filled, top.level_index).done is True
    assert others <= {order.order_id for order in filled.orders}
    assert (
        apply_buy_settled(state=filled, order_id=top.order_id, matched_qty=top.submitted_qty)
        is filled
    )
    assert (
        apply_fill(
            state=filled,
            event=_fill_of(top, fill_id="hidden", qty=top.submitted_qty, now_ns=4 * NS),
        )
        is filled
    )


def test_buy_settled_waits_for_the_partial_fill() -> None:
    state, order = _gone_clip()
    waiting = apply_buy_settled(state=state, order_id=order.order_id, matched_qty=3.0)
    assert waiting is state
    assert _by_id(waiting, order.order_id).status == "gone"
    assert _rung_at(waiting, order.level_index).live_id == order.order_id
    credited = apply_fill(state=waiting, event=_fill_of(order, fill_id="p3", qty=3.0, now_ns=NS))
    kept = _by_id(credited, order.order_id)
    assert kept.status == "gone"
    assert kept.filled_qty == 3.0
    assert credited.inventory[order.token_index].qty == 3.0
    assert credited.episode_buy_notional == 3.0 * order.price
    rung = _rung_at(credited, order.level_index)
    assert rung.filled_qty == 3.0
    assert rung.done is False
    assert rung.live_id == order.order_id
    others = {item.order_id for item in credited.orders if item.order_id != order.order_id}
    retired = apply_buy_settled(state=credited, order_id=order.order_id, matched_qty=3.0)
    assert {item.order_id for item in retired.orders} == others
    assert retired.inventory == credited.inventory
    assert retired.episode_buy_notional == credited.episode_buy_notional
    assert _record(retired, order.order_id).terminal is True
    freed = _rung_at(retired, order.level_index)
    assert freed.live_id is None
    assert freed.done is False
    assert apply_buy_settled(state=retired, order_id=order.order_id, matched_qty=3.0) is retired


def test_fill_before_proof_ignores_a_repeated_fill_id() -> None:
    state, order = _gone_clip()
    fill = _fill_of(order, fill_id="p3", qty=3.0, now_ns=NS)
    credited = apply_fill(state=state, event=fill)
    assert apply_fill(state=credited, event=fill) is credited
    assert credited.inventory[order.token_index].qty == 3.0
    assert credited.episode_buy_notional == 3.0 * order.price
    assert _rung_at(credited, order.level_index).filled_qty == 3.0
    assert _by_id(credited, order.order_id).status == "gone"
    retired = apply_buy_settled(state=credited, order_id=order.order_id, matched_qty=3.0)
    assert not any(item.order_id == order.order_id for item in retired.orders)
    assert retired.inventory[order.token_index].qty == 3.0


def test_zero_proof_retires_canceling_without_gone() -> None:
    state, policy, _plan = _prime()
    state = _accept_all(state, policy, 0)
    episode = state.episode_id
    ids = [order.order_id for order in state.orders if order.side == "BUY"]
    assert len(ids) >= 2
    for order_id in ids[:-1]:
        state = mark_canceling(state=state, order_id=order_id, reason="reprice")
        state = apply_cancel_ack(state=state, order_id=order_id)
    last = ids[-1]
    state = mark_canceling(state=state, order_id=last, reason="reprice")
    assert _by_id(state, last).status == "canceling"
    retired = apply_buy_settled(state=state, order_id=last, matched_qty=0.0)
    assert retired.episode_id == 0
    assert retired.rungs == ()
    assert any(archive.episode_id == episode for archive in retired.archives)
    assert not any(order.order_id == last for order in retired.orders)
    assert apply_cancel_unsettled(state=retired, order_id=last) is retired
    assert (
        apply_cancel_timeout(state=retired, event=CancelTimeout(now_ns=NS, order_id=last))
        is retired
    )


def test_zero_proof_retires_live_unknown_and_gone() -> None:
    state, _policy, plan = _prime()
    state = _accept_all(state, _policy, 0)
    buys = _buy_places(plan)
    assert len(buys) >= 3
    live_id = buys[0].order_id
    unknown_id = buys[1].order_id
    gone_id = buys[2].order_id
    state = apply_submit_timeout(state=state, order_id=unknown_id)
    state = apply_cancel_unsettled(state=state, order_id=gone_id)
    assert _by_id(state, unknown_id).status == "unknown"
    assert _by_id(state, gone_id).status == "gone"
    retired = apply_buy_settled(state=state, order_id=live_id, matched_qty=0.0)
    assert not any(order.order_id == live_id for order in retired.orders)
    retired = apply_buy_settled(state=retired, order_id=unknown_id, matched_qty=0.0)
    assert not any(order.order_id == unknown_id for order in retired.orders)
    retired = apply_buy_settled(state=retired, order_id=gone_id, matched_qty=0.0)
    assert not any(order.order_id == gone_id for order in retired.orders)


def test_buy_settled_threshold_is_half_a_share() -> None:
    state, order = _gone_clip()
    matched = 4.0
    threshold = matched - HALF_SHARE_TICK
    below = math.nextafter(threshold, -math.inf)

    def at_filled(qty: float) -> StrategyState:
        updated = replace(_by_id(state, order.order_id), filled_qty=qty)
        return replace_order(state=state, order=updated)

    waiting = apply_buy_settled(
        state=at_filled(below), order_id=order.order_id, matched_qty=matched
    )
    assert _by_id(waiting, order.order_id).status == "gone"
    for qty in (threshold, matched):
        retired = apply_buy_settled(
            state=at_filled(qty), order_id=order.order_id, matched_qty=matched
        )
        assert not any(item.order_id == order.order_id for item in retired.orders)
    pending = replace_order(
        state=state, order=replace(_by_id(state, order.order_id), status="pending")
    )
    assert apply_buy_settled(state=pending, order_id=order.order_id, matched_qty=0.0) is pending


def test_cancel_unsettled_ignores_repeats_sells_and_missing_ids() -> None:
    state, _policy, plan = _prime()
    state = _accept_all(state, _policy, 0)
    top = _buy_places(plan)[0]
    assert apply_cancel_unsettled(state=state, order_id="missing") is state
    sell = replace_order(state=state, order=replace(_by_id(state, top.order_id), side="SELL"))
    assert apply_cancel_unsettled(state=sell, order_id=top.order_id) is sell
    assert apply_buy_settled(state=sell, order_id=top.order_id, matched_qty=0.0) is sell
    once = apply_cancel_unsettled(state=state, order_id=top.order_id)
    assert _by_id(once, top.order_id).status == "gone"
    assert apply_cancel_unsettled(state=once, order_id=top.order_id) is once
    assert (
        apply_cancel_timeout(state=once, event=CancelTimeout(now_ns=NS, order_id=top.order_id))
        is once
    )
    assert already_canceling(_by_id(once, top.order_id)) is False


def test_gone_buy_is_not_recanceled_or_matched() -> None:
    state, policy, plan = _prime()
    state = _accept_all(state, policy, 0)
    top = _buy_places(plan)[0]
    state = mark_canceling(state=state, order_id=top.order_id, reason="reprice")
    out = step(state=state, policy=policy, event=CancelUnsettled(now_ns=0, order_id=top.order_id))
    gone = _by_id(out.state, top.order_id)
    assert gone.status == "gone"
    assert gone.cancel_reason == "reprice"
    assert gone.ack_reason == "reprice"
    assert gone.level_index == top.level_index
    assert already_canceling(gone) is False
    assert top.order_id not in {item.order_id for item in out.plan.keep}
    assert top.order_id not in {item.order_id for item in out.plan.moves}
    assert not any(place.level_index == top.level_index for place in _buy_places(out.plan))
    assert any(item.order_id != top.order_id for item in out.plan.keep)
    siblings = sum(
        max(0.0, (order.submitted_qty - order.filled_qty) * order.price)
        for order in out.state.orders
        if order.side == "BUY" and order.order_id != top.order_id
    )
    assert reserve_buy_notional(out.state) == siblings

    def unchanged(checked: StrategyState) -> None:
        order = _by_id(checked, top.order_id)
        assert order.status == "gone"
        assert order.cancel_reason == "reprice"
        assert order.ack_reason == "reprice"
        assert order.level_index == top.level_index

    shifted = _feed(
        out.state,
        policy,
        2 * NS,
        books=_balanced(ts_ns=2 * NS, bid0=0.40, ask0=0.42),
        signal=_signal(delta=0.05, now_ns=2 * NS, anchor=0.41),
        second=100,
    )
    shifted, reprice = _wake(shifted, policy, 2 * NS)
    assert not any(cancel.order_id == top.order_id for cancel in reprice.cancels)
    unchanged(shifted)
    halted = step(
        state=out.state,
        policy=policy,
        event=PermissionsUpdate(
            now_ns=2 * NS,
            permissions=Permissions(
                halt=True,
                reduce_only=False,
                allow_buy=False,
                allow_sell=False,
                sell_unconfirmed=False,
            ),
        ),
    )
    assert not any(cancel.order_id == top.order_id for cancel in halted.plan.cancels)
    unchanged(halted.state)
    gated = step(
        state=out.state,
        policy=policy,
        event=KillGateUpdate(
            now_ns=2 * NS,
            gate=KillGate(
                radiant=KillWait(awaited_deaths=1, until_ns=12 * NS),
                dire=KillWait(awaited_deaths=0, until_ns=0),
            ),
        ),
    ).state
    killed, kill_plan = _wake(gated, policy, 2 * NS)
    assert kill_plan.block_reason == "kill"
    assert not any(cancel.order_id == top.order_id for cancel in kill_plan.cancels)
    unchanged(killed)
    recovered = step(
        state=out.state,
        policy=policy,
        event=Recovery(now_ns=2 * NS, restored_buy_ids=(top.order_id,)),
    )
    assert not any(cancel.order_id == top.order_id for cancel in recovered.plan.cancels)
    unchanged(recovered.state)


def test_recovery_verified_keeps_gone_and_plans_the_sell() -> None:
    state, policy, plan = _prime()
    state = _accept_all(state, policy, 0)
    buys = _buy_places(plan)
    for buy in buys:
        state = apply_cancel_unsettled(state=state, order_id=buy.order_id)
    assert state.inventory[0].qty == 0.0
    assert has_unresolved_orders(state=state) is False
    assert not any(order.status == "live" for order in state.orders)
    bindings = {order.level_index: order.order_id for order in state.orders}
    recovered = step(
        state=state,
        policy=policy,
        event=Recovery(now_ns=NS, restored_buy_ids=tuple(buy.order_id for buy in buys)),
    )
    assert recovered.state.recovery_pending is True
    assert {order.level_index: order.order_id for order in recovered.state.orders} == bindings
    assert all(_by_id(recovered.state, buy.order_id).status == "gone" for buy in buys)
    wrong = step(
        state=recovered.state,
        policy=policy,
        event=RecoveryVerified(now_ns=NS, generation=0, inventory=_inventory(yes_qty=8.0)),
    )
    assert wrong.state.recovery_pending is True
    assert wrong.state.inventory[0].qty == 0.0
    verified = step(
        state=recovered.state,
        policy=policy,
        event=RecoveryVerified(
            now_ns=NS,
            generation=recovered.state.recovery_generation,
            inventory=_inventory(yes_qty=8.0),
        ),
    )
    assert verified.state.recovery_pending is False
    assert verified.state.inventory[0].qty == 8.0
    buy_bindings = {
        order.level_index: order.order_id for order in verified.state.orders if order.side == "BUY"
    }
    assert buy_bindings == bindings
    sells = _sell_places(verified.plan)
    assert len(sells) == 1
    assert sells[0].quantity == 8.0
    assert sells[0].token_index == 0
    top = _by_id(verified.state, buys[0].order_id)
    refilled = apply_fill(
        state=verified.state, event=_fill_of(top, fill_id="after", qty=1.0, now_ns=2 * NS)
    )
    assert refilled.recovery_pending is True
    assert refilled.recovery_generation == verified.state.recovery_generation + 1
