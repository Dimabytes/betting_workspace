"""Rung refills quote the full clip; the shared cap lives in the adapter budget."""

# pyright: reportPrivateUsage=false

import json
from dataclasses import dataclass, replace

import pytest
from test_strategy_core import (
    NS,
    _accept_all,
    _balanced,
    _buy_places,
    _feed,
    _fill_of,
    _idle,
    _policy,
    _signal,
    _wake,
)

from shared.utils.trading import buy_share_quantity
from strategy.budget import map_budget, reserve_buy_notional
from strategy.engine import step
from strategy.lifecycle import (
    apply_cancel_ack,
    apply_fill,
    end_episode_if_idle,
    mark_canceling,
    occupy_sell,
    rung_occupied,
)
from strategy.policy import Follow300Policy, follow300_policy
from strategy.types import (
    Budget,
    BudgetUpdate,
    CancelAck,
    CancelTimeout,
    OrderStatus,
    RestingOrder,
    Side,
    StrategyState,
    SubmitTimeout,
)
from trader.core_persistence import (
    apply_checkpoint,
    decode_checkpoint,
    encode_checkpoint,
    snapshot_checkpoint,
)


@dataclass(frozen=True)
class OpenedLadder:
    state: StrategyState
    policy: Follow300Policy
    top: RestingOrder


def open_ladder(level_usdc: float) -> OpenedLadder:
    policy = follow300_policy(level_usdc=level_usdc, debounce_ms=100, fallback_timer_s=2.0)
    state = _feed(
        _idle(), policy, 0, books=_balanced(ts_ns=0), signal=_signal(delta=0.05, now_ns=0)
    )
    state, _ = _wake(state, policy, 0)
    state = _accept_all(state, policy, 0)
    return OpenedLadder(state=state, policy=policy, top=state.orders[0])


def assert_budget(state: StrategyState, policy: Follow300Policy) -> None:
    assert all(
        order.price * order.submitted_qty <= policy.level_usdc + 1e-8
        for order in state.orders
        if order.side == "BUY"
    )


def test_map_budget_scales_cap_room_by_max_position_levels() -> None:
    budget = map_budget(
        cash_usdc=1000.0,
        level_usdc=300.0,
        max_position_levels=18,
        held_cost=500.0,
        reserved_usdc=250.0,
        account_cap_room_usdc=float("inf"),
    )
    assert budget.cap_room_usdc == pytest.approx(18 * 300.0 - 500.0 - 250.0)


@pytest.mark.parametrize("level_usdc", [10.0, 30.0, 100.0])
def test_repeated_partial_replacements_requote_the_full_rung(level_usdc: float) -> None:
    opened = open_ladder(level_usdc)
    state = opened.state
    other_ids = {order.order_id for order in state.orders[1:]}
    for index in range(6):
        top = next(order for order in state.orders if order.level_index == 0)
        state = apply_fill(
            state=state,
            event=_fill_of(top, fill_id=f"p{index}", qty=2.0, now_ns=index),
        )
        state = mark_canceling(state=state, order_id=top.order_id, reason="reprice")
        assert_budget(state, opened.policy)
        replaced = step(
            state=state, policy=opened.policy, event=CancelAck(now_ns=index, order_id=top.order_id)
        )
        buys = _buy_places(replaced.plan)
        assert len(buys) == 1
        assert buys[0].price * buys[0].quantity == pytest.approx(level_usdc, abs=0.01)
        state = _accept_all(replaced.state, opened.policy, index)
        assert other_ids <= {order.order_id for order in state.orders}
        assert state.episode_buy_notional == pytest.approx(index + 1)
        assert_budget(state, opened.policy)


@pytest.mark.parametrize("status", ["pending", "canceling", "unknown_submit", "unknown_cancel"])
def test_unfinished_orders_keep_their_reserve(status: str) -> None:
    opened = open_ladder(30.0)
    state = apply_fill(
        state=opened.state, event=_fill_of(opened.top, fill_id="p", qty=24.0, now_ns=0)
    )
    if status == "pending":
        state = replace(
            state, orders=tuple(replace(order, status="pending") for order in state.orders)
        )
    elif status == "unknown_submit":
        state = step(
            state=state,
            policy=opened.policy,
            event=SubmitTimeout(now_ns=0, order_id=opened.top.order_id),
        ).state
    else:
        state = mark_canceling(state=state, order_id=opened.top.order_id, reason="reprice")
        if status == "unknown_cancel":
            state = step(
                state=state,
                policy=opened.policy,
                event=CancelTimeout(now_ns=0, order_id=opened.top.order_id),
            ).state
    before = reserve_buy_notional(state)
    moved = _feed(
        state,
        opened.policy,
        NS,
        books=_balanced(ts_ns=NS, bid0=0.49, ask0=0.51),
        signal=_signal(delta=0.05, now_ns=NS, anchor=0.50),
    )
    moved, plan = _wake(moved, opened.policy, NS)
    placed = _buy_places(plan)
    for buy in placed:
        assert buy.price * buy.quantity == pytest.approx(30.0, abs=0.01)
    placed_notional = sum(buy.price * buy.quantity for buy in placed)
    assert reserve_buy_notional(moved) == pytest.approx(before + placed_notional)
    assert_budget(state, opened.policy)
    assert_budget(moved, opened.policy)


def test_fill_during_cancel_requotes_the_full_rung() -> None:
    opened = open_ladder(30.0)
    state = apply_fill(
        state=opened.state, event=_fill_of(opened.top, fill_id="p1", qty=24.0, now_ns=0)
    )
    state = mark_canceling(state=state, order_id=opened.top.order_id, reason="reprice")
    state = apply_fill(state=state, event=_fill_of(opened.top, fill_id="p2", qty=14.0, now_ns=1))
    out = step(
        state=state, policy=opened.policy, event=CancelAck(now_ns=2, order_id=opened.top.order_id)
    )
    replacement = _buy_places(out.plan)[0]
    assert replacement.price * replacement.quantity == pytest.approx(30.0)
    assert_budget(out.state, opened.policy)


def test_spend_uses_execution_price_and_ignores_duplicate_fill() -> None:
    opened = open_ladder(30.0)
    fill = _fill_of(opened.top, fill_id="improved", qty=24.0, price=0.48, now_ns=0)
    state = apply_fill(state=opened.state, event=fill)
    state = apply_fill(state=state, event=fill)
    assert state.episode_buy_notional == pytest.approx(11.52)
    assert_budget(state, opened.policy)


def test_refill_after_partial_fill_is_full_size() -> None:
    opened = open_ladder(30.0)
    state = apply_fill(
        state=opened.state, event=_fill_of(opened.top, fill_id="p", qty=55.0, now_ns=0)
    )
    out = step(
        state=state, policy=opened.policy, event=CancelAck(now_ns=0, order_id=opened.top.order_id)
    )
    buys = _buy_places(out.plan)
    assert len(buys) == 1
    assert buys[0].price * buys[0].quantity == pytest.approx(30.0)
    assert_budget(out.state, opened.policy)


@pytest.mark.parametrize("price", [0.35, 0.37, 0.47, 0.49, 0.76])
def test_quantity_rounds_down_under_dollar_cap(price: float) -> None:
    qty = buy_share_quantity(base_size_usdc=30.0, price=price)
    assert qty * price <= 30.0 + 1e-12
    assert (qty + 0.01) * price > 30.0


def _drop_buys(state: StrategyState, _policy: Follow300Policy) -> StrategyState:
    for order in tuple(state.orders):
        if order.side != "BUY":
            continue
        state = mark_canceling(state=state, order_id=order.order_id, reason="reprice")
        state = apply_cancel_ack(state=state, order_id=order.order_id)
    return state


def test_selling_drains_the_oldest_rung_lot_first() -> None:
    opened = open_ladder(30.0)
    state = apply_fill(
        state=opened.state, event=_fill_of(opened.top, fill_id="buy", qty=40.0, now_ns=0)
    )
    state = _drop_buys(state, opened.policy)
    sell = replace(
        opened.top, order_id="sell", side="SELL", level_index=None, submitted_qty=40.0, price=0.60
    )
    state = occupy_sell(state=state, order=sell)
    state = apply_fill(state=state, event=_fill_of(sell, fill_id="sell", qty=20.0, now_ns=1))
    assert state.position.qty == 20.0
    assert state.rungs[0].held_cost == pytest.approx(10.0)
    assert_budget(state, opened.policy)


def test_partial_rung_replacement_quotes_the_full_clip() -> None:
    opened = open_ladder(30.0)
    state = apply_fill(
        state=opened.state, event=_fill_of(opened.top, fill_id="p", qty=24.0, now_ns=0)
    )
    for order in tuple(state.orders):
        if order.level_index == 0:
            continue
        state = mark_canceling(state=state, order_id=order.order_id, reason="reprice")
        state = step(
            state=state, policy=opened.policy, event=CancelAck(now_ns=0, order_id=order.order_id)
        ).state
    state = mark_canceling(state=state, order_id=opened.top.order_id, reason="reprice")
    out = step(
        state=state, policy=opened.policy, event=CancelAck(now_ns=0, order_id=opened.top.order_id)
    )
    replacement = _buy_places(out.plan)[0]
    assert replacement.level_index == 0
    assert replacement.price * replacement.quantity == pytest.approx(30.0)
    assert_budget(out.state, opened.policy)


def test_sell_fifo_unlocks_l0_before_l1() -> None:
    opened = open_ladder(30.0)
    state = opened.state
    l0 = next(order for order in state.orders if order.level_index == 0)
    l1 = next(order for order in state.orders if order.level_index == 1)
    state = apply_fill(state=state, event=_fill_of(l0, fill_id="b0", qty=20.0, now_ns=0))
    state = apply_fill(state=state, event=_fill_of(l1, fill_id="b1", qty=20.0, now_ns=1))
    state = _drop_buys(state, opened.policy)
    sell = replace(
        l0, order_id="sell", side="SELL", level_index=None, submitted_qty=20.0, price=0.60
    )
    state = occupy_sell(state=state, order=sell)
    state = apply_fill(state=state, event=_fill_of(sell, fill_id="s0", qty=20.0, now_ns=2))
    assert state.rungs[0].held_qty == pytest.approx(0.0)
    assert state.rungs[1].held_qty == pytest.approx(20.0)
    assert_budget(state, opened.policy)


def test_budget_resets_only_when_episode_finishes() -> None:
    opened = open_ladder(30.0)
    state = opened.state
    for index, order in enumerate(state.orders):
        state = apply_fill(
            state=state,
            event=_fill_of(order, fill_id=f"b{index}", qty=order.submitted_qty, now_ns=0),
        )
    spent = state.episode_buy_notional
    sell = replace(
        opened.top,
        order_id="sell",
        side="SELL",
        level_index=None,
        submitted_qty=state.position.qty,
        price=0.60,
    )
    state = occupy_sell(state=state, order=sell)
    state = apply_fill(
        state=state, event=_fill_of(sell, fill_id="sold", qty=sell.submitted_qty, now_ns=1)
    )
    state = end_episode_if_idle(state=state)
    assert state.episode_id == 0
    assert state.episode_buy_notional == 0.0
    assert state.archives[-1].episode_buy_notional == spent
    state = _feed(
        state, opened.policy, 2, books=_balanced(ts_ns=2), signal=_signal(delta=0.05, now_ns=2)
    )
    state, plan = _wake(state, opened.policy, 2)
    assert len(_buy_places(plan)) == 3
    assert state.episode_id == opened.state.episode_id + 1
    assert_budget(state, opened.policy)


def test_late_fill_credits_inventory_without_disturbing_the_ladder() -> None:
    opened = open_ladder(30.0)
    state = apply_fill(
        state=opened.state, event=_fill_of(opened.top, fill_id="p", qty=24.0, now_ns=0)
    )
    state = step(
        state=state, policy=opened.policy, event=CancelAck(now_ns=0, order_id=opened.top.order_id)
    ).state
    state = apply_fill(state=state, event=_fill_of(opened.top, fill_id="late", qty=14.0, now_ns=1))
    assert state.episode_buy_notional == 19.0
    assert state.position.qty == 38.0
    state, plan = _wake(state, opened.policy, 2)
    assert _buy_places(plan) == ()
    assert plan.cancels == ()
    assert_budget(state, opened.policy)


def test_checkpoint_restores_gross_spend_and_fill_deduplication() -> None:
    opened = open_ladder(30.0)
    fill = _fill_of(opened.top, fill_id="p", qty=24.0, now_ns=0)
    state = apply_fill(state=opened.state, event=fill)
    checkpoint = snapshot_checkpoint(state=state, now_ns=0, now_wall_s=1.0, sell_min_life_s=1.0)
    checkpoint = decode_checkpoint(encode_checkpoint(checkpoint))
    restored = apply_checkpoint(
        _idle(), checkpoint=checkpoint, now_ns=0, now_wall_s=1.0, sell_min_life_s=1.0
    )
    restored = _feed(
        restored, opened.policy, 0, books=_balanced(ts_ns=0), signal=_signal(delta=0.05, now_ns=0)
    )
    restored = apply_fill(state=restored, event=fill)
    assert restored.episode_buy_notional == 12.0
    assert reserve_buy_notional(restored) == pytest.approx(reserve_buy_notional(state))
    assert restored.inventory[0].cost_basis == pytest.approx(state.inventory[0].cost_basis)
    out = step(
        state=restored,
        policy=opened.policy,
        event=CancelAck(now_ns=0, order_id=opened.top.order_id),
    )
    assert _buy_places(out.plan)[0].quantity == 60.0
    assert_budget(out.state, opened.policy)


def test_gone_buys_drop_out_of_kernel_reserve() -> None:
    opened = open_ladder(30.0)
    prototype = opened.top

    def buy(status: OrderStatus, filled: float, order_id: str, side: Side = "BUY") -> RestingOrder:
        return replace(
            prototype,
            order_id=order_id,
            status=status,
            filled_qty=filled,
            side=side,
            submitted_qty=60.0,
            price=0.50,
        )

    pending = buy("pending", 0.0, "p")
    live = buy("live", 0.0, "l")
    canceling = buy("canceling", 10.0, "c")
    unknown = buy("unknown", 0.0, "u")
    overfill = buy("live", 80.0, "over")
    gone_flat = buy("gone", 0.0, "g0")
    gone_partial = buy("gone", 25.0, "g1")
    sell = buy("live", 0.0, "s", side="SELL")
    mixed = replace(
        opened.state,
        orders=(pending, live, canceling, unknown, overfill, gone_flat, gone_partial, sell),
    )
    assert reserve_buy_notional(mixed) == pytest.approx(115.0)
    assert reserve_buy_notional(replace(opened.state, orders=(gone_flat, gone_partial))) == 0.0


def test_v2_checkpoint_without_spend_must_drain() -> None:
    opened = open_ladder(30.0)
    checkpoint = snapshot_checkpoint(
        state=opened.state, now_ns=0, now_wall_s=1.0, sell_min_life_s=1.0
    )
    raw = json.loads(encode_checkpoint(checkpoint))
    raw["schema_version"] = 2
    raw.pop("episode_buy_notional")
    checkpoint = decode_checkpoint(json.dumps(raw))
    restored = apply_checkpoint(
        _idle(), checkpoint=checkpoint, now_ns=0, now_wall_s=1.0, sell_min_life_s=1.0
    )
    assert restored.sell_only
    restored, plan = _wake(restored, opened.policy, 0)
    assert _buy_places(plan) == ()
    assert all(order.status == "canceling" for order in restored.orders)


def _one_rung_usdc(policy: Follow300Policy) -> float:
    price = 0.50
    return price * buy_share_quantity(base_size_usdc=policy.level_usdc, price=price)


def _budget_update(*, cash_usdc: float, cap_room_usdc: float) -> BudgetUpdate:
    return BudgetUpdate(
        now_ns=0,
        budget=Budget(
            cash_usdc=cash_usdc,
            cap_room_usdc=cap_room_usdc,
            account_cap_room_usdc=float("inf"),
        ),
    )


def _fed_with_budget(
    *, policy: Follow300Policy, cash_usdc: float, cap_room_usdc: float
) -> StrategyState:
    state = _feed(
        _idle(),
        policy,
        0,
        books=_balanced(ts_ns=0),
        signal=_signal(delta=0.05, now_ns=0),
    )
    return step(
        state=state,
        policy=policy,
        event=_budget_update(cash_usdc=cash_usdc, cap_room_usdc=cap_room_usdc),
    ).state


def _assert_second_step_blocked(
    *, policy: Follow300Policy, cash_usdc: float, cap_room_usdc: float, reason: str
) -> None:
    one = _one_rung_usdc(policy)
    state = _fed_with_budget(policy=policy, cash_usdc=cash_usdc, cap_room_usdc=cap_room_usdc)
    state, first = _wake(state, policy, 0)
    buys = _buy_places(first)
    assert len(buys) == 1
    assert buys[0].price * buys[0].quantity == pytest.approx(one)
    assert rung_occupied(state=state, level_index=0)
    assert not rung_occupied(state=state, level_index=1)
    assert not rung_occupied(state=state, level_index=2)
    _, second = _wake(state, policy, 0)
    assert _buy_places(second) == ()
    assert second.block_reason == reason


def test_position_cap_blocks_the_next_step_without_a_budget_update() -> None:
    policy = _policy()
    one = _one_rung_usdc(policy)
    _assert_second_step_blocked(
        policy=policy, cash_usdc=10_000.0, cap_room_usdc=one, reason="position_cap"
    )


def test_no_cash_blocks_the_next_step_without_a_budget_update() -> None:
    policy = _policy()
    one = _one_rung_usdc(policy)
    _assert_second_step_blocked(
        policy=policy, cash_usdc=one, cap_room_usdc=float("inf"), reason="no_cash"
    )


def test_budget_update_with_room_places_the_next_rung() -> None:
    policy = _policy()
    one = _one_rung_usdc(policy)
    state = _fed_with_budget(policy=policy, cash_usdc=10_000.0, cap_room_usdc=one)
    state, first = _wake(state, policy, 0)
    assert len(_buy_places(first)) == 1
    _, blocked = _wake(state, policy, 0)
    assert blocked.block_reason == "position_cap"
    state = step(
        state=state,
        policy=policy,
        event=_budget_update(cash_usdc=10_000.0, cap_room_usdc=one),
    ).state
    state, opened = _wake(state, policy, 0)
    buys = _buy_places(opened)
    assert len(buys) == 1
    assert buys[0].level_index == 1
    assert rung_occupied(state=state, level_index=1)
