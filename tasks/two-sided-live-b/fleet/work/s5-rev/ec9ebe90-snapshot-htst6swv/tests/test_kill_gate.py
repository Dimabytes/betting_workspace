"""Kill gate: a board kill cancels victim BUYs and killer SELLs until the table shows it."""

# pyright: reportPrivateUsage=false

from dataclasses import replace

import pytest

from strategy.engine import step
from strategy.kill_gate import kill_exposure, kill_gate_boundary_ns, kill_victims
from strategy.lifecycle import empty_state
from strategy.mid_spike import observe_mid_spike
from strategy.policy import follow300_policy
from strategy.quoting import decide_sell
from strategy.types import (
    BookPair,
    Budget,
    FreshnessLimits,
    GameClock,
    KillGate,
    KillGateUpdate,
    KillWait,
    MarketLimits,
    Permissions,
    RawDeltaSignal,
    RestingOrder,
    SignalUpdate,
    StrategyState,
    TokenBook,
    TokenInventory,
    Wake,
)

NS = 1_000_000_000


def _idle(*, now_ns: int = 0) -> StrategyState:
    return empty_state(
        limits=MarketLimits(
            min_order_size=5.0,
            tick_size=0.01,
            pair_sum_tolerance=0.02,
            radiant_token_index=0,
        ),
        freshness=FreshnessLimits(book_stale_s=30.0, entry_stale_s=30.0, exit_stale_s=45.0),
        permissions=Permissions(
            halt=False, reduce_only=False, allow_buy=True, allow_sell=True, sell_unconfirmed=False
        ),
        budget=Budget(
            cash_usdc=10_000.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        clock=GameClock(now_ns=now_ns, game_second=100, paused=False, game_ended=False),
    )


def _books(*, now_ns: int, mid: float) -> BookPair:
    half = 0.01
    bid = mid - half
    ask = mid + half
    return BookPair(
        tokens=(
            TokenBook(
                token_index=0,
                bid=bid,
                ask=ask,
                bid_size=100.0,
                ask_size=100.0,
                ts_ns=now_ns,
            ),
            TokenBook(
                token_index=1,
                bid=1.0 - ask,
                ask=1.0 - bid,
                bid_size=100.0,
                ask_size=100.0,
                ts_ns=now_ns,
            ),
        )
    )


def _policy(*, fallback_timer_s: float = 2.0):
    return follow300_policy(level_usdc=100.0, debounce_ms=0, fallback_timer_s=fallback_timer_s)


def _signal(
    *,
    deaths_radiant: int,
    deaths_dire: int,
    received_ns: int = 0,
    delta: float = 0.05,
    anchor: float = 0.60,
) -> RawDeltaSignal:
    return RawDeltaSignal(
        predicted_delta=delta,
        source_received_ns=received_ns,
        received_ns=received_ns,
        anchor_p=anchor,
        deaths_radiant=deaths_radiant,
        deaths_dire=deaths_dire,
    )


def _order(*, order_id: str, side: str, token_index: int, now_ns: int) -> RestingOrder:
    return RestingOrder(
        order_id=order_id,
        episode_id=1,
        token_index=token_index,
        side=side,  # type: ignore[arg-type]
        price=0.50,
        submitted_qty=100.0,
        filled_qty=0.0,
        level_index=None,
        status="live",
        accepted=True,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=now_ns,
        accepted_ns=now_ns,
    )


def _four_orders(*, now_ns: int) -> tuple[RestingOrder, ...]:
    return (
        _order(order_id="buy-0", side="BUY", token_index=0, now_ns=now_ns),
        _order(order_id="sell-0", side="SELL", token_index=0, now_ns=now_ns),
        _order(order_id="buy-1", side="BUY", token_index=1, now_ns=now_ns),
        _order(order_id="sell-1", side="SELL", token_index=1, now_ns=now_ns),
    )


def _gate_update(
    *,
    now_ns: int,
    radiant_awaited: int = 0,
    dire_awaited: int = 0,
    hold_s: float = 10.0,
) -> KillGateUpdate:
    until_ns = now_ns + int(hold_s * NS)
    return KillGateUpdate(
        now_ns=now_ns,
        gate=KillGate(
            radiant=KillWait(
                awaited_deaths=radiant_awaited,
                until_ns=until_ns if radiant_awaited else now_ns,
            ),
            dire=KillWait(
                awaited_deaths=dire_awaited,
                until_ns=until_ns if dire_awaited else now_ns,
            ),
        ),
    )


def _quoted(
    state: StrategyState, *, deaths_radiant: int = 5, deaths_dire: int = 3
) -> StrategyState:
    return replace(
        state,
        books=_books(now_ns=0, mid=0.60),
        signal=_signal(deaths_radiant=deaths_radiant, deaths_dire=deaths_dire),
        orders=_four_orders(now_ns=0),
        inventory=(
            TokenInventory(token_index=0, qty=100.0, cost_basis=50.0, last_buy_ns=0),
            TokenInventory(token_index=1, qty=100.0, cost_basis=50.0, last_buy_ns=0),
        ),
        episode_id=1,
        has_buy_fill=True,
    )


def _by_id(state: StrategyState, order_id: str) -> RestingOrder:
    return next(order for order in state.orders if order.order_id == order_id)


def test_kill_gate_update_stores_gate_and_waits_for_debounce() -> None:
    policy = _policy()
    state = _quoted(_idle(now_ns=0))
    out = step(state=state, policy=policy, event=_gate_update(now_ns=0, radiant_awaited=6))
    assert out.plan.places == ()
    assert out.plan.cancels == ()
    assert out.state.kill_gate.radiant.awaited_deaths == 6
    assert out.state.kill_gate.radiant.until_ns == 10 * NS


def test_kill_gate_cancels_victim_buy_and_killer_sell() -> None:
    """Radiant died: victim is token0 -> cancel BUY@0 and SELL@1; the rest stays."""
    policy = _policy()
    state = _quoted(_idle(now_ns=0))
    out = step(state=state, policy=policy, event=_gate_update(now_ns=0, radiant_awaited=6))
    out = step(state=out.state, policy=policy, event=Wake(now_ns=0, forced=True))
    assert all(place.token_index != 0 or place.side != "BUY" for place in out.plan.places)
    assert all(place.token_index != 1 or place.side != "SELL" for place in out.plan.places)
    assert _by_id(out.state, "buy-0").cancel_reason == "kill"
    assert _by_id(out.state, "sell-1").cancel_reason == "kill"
    assert _by_id(out.state, "buy-1").cancel_reason != "kill"
    assert _by_id(out.state, "sell-0").status == "live"
    assert {cancel.order_id for cancel in out.plan.cancels if cancel.reason == "kill"} == {
        "buy-0",
        "sell-1",
    }


def test_kill_gate_cancels_dire_victim_pair() -> None:
    """Dire died: victim is token1 -> cancel BUY@1 and SELL@0."""
    policy = _policy()
    state = _quoted(_idle(now_ns=0))
    out = step(state=state, policy=policy, event=_gate_update(now_ns=0, dire_awaited=4))
    out = step(state=out.state, policy=policy, event=Wake(now_ns=0, forced=True))
    assert _by_id(out.state, "buy-1").cancel_reason == "kill"
    assert _by_id(out.state, "sell-0").cancel_reason == "kill"
    assert _by_id(out.state, "buy-0").cancel_reason != "kill"
    assert _by_id(out.state, "sell-1").status == "live"


def test_kill_gate_trade_both_sides_cancels_all() -> None:
    """Both waits pending (a trade of kills): every exposed class cancels."""
    policy = _policy()
    state = _quoted(_idle(now_ns=0))
    out = step(
        state=state,
        policy=policy,
        event=_gate_update(now_ns=0, radiant_awaited=6, dire_awaited=4),
    )
    out = step(state=out.state, policy=policy, event=Wake(now_ns=0, forced=True))
    for order_id in ("buy-0", "sell-0", "buy-1", "sell-1"):
        assert _by_id(out.state, order_id).cancel_reason == "kill"


def test_kill_gate_clears_on_signal_with_deaths() -> None:
    """First signal carrying the awaited death count ends the wait."""
    policy = _policy()
    state = _quoted(_idle(now_ns=0), deaths_radiant=5)
    out = step(state=state, policy=policy, event=_gate_update(now_ns=0, radiant_awaited=6))
    out = step(state=out.state, policy=policy, event=Wake(now_ns=0, forced=True))
    state = replace(
        out.state,
        orders=_four_orders(now_ns=0),
        signal=_signal(deaths_radiant=6, deaths_dire=3, received_ns=NS),
    )
    out = step(state=state, policy=policy, event=Wake(now_ns=NS, forced=True))
    assert not kill_victims(state=out.state, now_ns=out.state.schedule.last_eval_ns)


def test_kill_gate_expires_at_until() -> None:
    policy = _policy(fallback_timer_s=30.0)
    state = _quoted(_idle(now_ns=0))
    state = replace(state, orders=_four_orders(now_ns=-60 * NS))
    out = step(state=state, policy=policy, event=_gate_update(now_ns=0, radiant_awaited=6))
    out = step(state=out.state, policy=policy, event=Wake(now_ns=0, forced=True))
    assert kill_gate_boundary_ns(state=out.state, now_ns=0) == 10 * NS
    assert out.next_wake_ns <= 10 * NS
    out = step(state=out.state, policy=policy, event=Wake(now_ns=10 * NS, forced=True))
    assert not kill_victims(state=out.state, now_ns=out.state.schedule.last_eval_ns)


def test_kill_gate_inactive_without_signal() -> None:
    """signal None means stale_signal wipes everything; the gate adds nothing."""
    policy = _policy()
    state = replace(
        _idle(now_ns=0),
        books=_books(now_ns=0, mid=0.60),
        signal=None,
        orders=_four_orders(now_ns=0),
        episode_id=1,
        has_buy_fill=True,
    )
    out = step(state=state, policy=policy, event=_gate_update(now_ns=0, radiant_awaited=6))
    out = step(state=out.state, policy=policy, event=Wake(now_ns=0, forced=True))
    assert out.plan.block_reason == "stale_signal"
    for order_id in ("buy-0", "sell-0", "buy-1", "sell-1"):
        assert _by_id(out.state, order_id).status == "canceling"


def test_kill_gate_cancels_killer_sell_when_entry_stale() -> None:
    """Stale entry alone pulls only BUYs; the gate still pulls the killer SELL."""
    policy = _policy()
    state = _quoted(_idle(now_ns=0))
    stale_signal = _signal(deaths_radiant=5, deaths_dire=3, received_ns=0)
    state = replace(state, signal=stale_signal)
    out = step(state=state, policy=policy, event=_gate_update(now_ns=100 * NS, radiant_awaited=6))
    out = step(state=out.state, policy=policy, event=Wake(now_ns=100 * NS, forced=True))
    assert _by_id(out.state, "sell-1").cancel_reason == "kill"


def test_kill_gate_beats_mid_spike() -> None:
    """An active mid_spike does not shield the killer SELL: reason stays 'kill'."""
    policy = _policy()
    state = _quoted(_idle(now_ns=0))
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    state = replace(state, books=_books(now_ns=NS, mid=0.49))
    out = step(state=state, policy=policy, event=_gate_update(now_ns=NS, radiant_awaited=6))
    out = step(state=out.state, policy=policy, event=Wake(now_ns=NS, forced=True))
    assert out.state.mid_spike.cooloff_until_ns > 0
    assert _by_id(out.state, "sell-1").cancel_reason == "kill"


def test_kill_gate_directional_keeps_safe_sides() -> None:
    """Directional gate: radiant died, so victim BUY and killer SELL are pulled,
    but the plan still quotes killer BUY and the held victim SELL."""
    policy = _policy()
    state = _quoted(_idle(now_ns=0))
    state = replace(
        state,
        books=_books(now_ns=0, mid=0.49),
        signal=_signal(deaths_radiant=5, deaths_dire=3, delta=-0.05, anchor=0.49),
        episode_token_index=1,
        inventory=(
            TokenInventory(token_index=0, qty=100.0, cost_basis=50.0, last_buy_ns=-60 * NS),
            TokenInventory(token_index=1, qty=100.0, cost_basis=50.0, last_buy_ns=-60 * NS),
        ),
    )
    out = step(state=state, policy=policy, event=_gate_update(now_ns=0, radiant_awaited=6))
    out = step(state=out.state, policy=policy, event=Wake(now_ns=0, forced=True))
    assert _by_id(out.state, "buy-0").cancel_reason == "kill"
    assert _by_id(out.state, "sell-1").cancel_reason == "kill"
    assert out.plan.places
    assert all(place.side == "BUY" and place.token_index == 1 for place in out.plan.places)
    assert _by_id(out.state, "sell-0").status == "live"


def test_kill_gate_directional_blocks_killer_sell() -> None:
    """Holding only the killer token while its victim side is gated: no exit."""
    policy = _policy()
    state = _quoted(_idle(now_ns=0))
    state = replace(
        state,
        signal=_signal(deaths_radiant=5, deaths_dire=3, delta=-0.05),
        orders=(),
        inventory=(
            TokenInventory(token_index=0, qty=0.0, cost_basis=0.0, last_buy_ns=0),
            TokenInventory(token_index=1, qty=100.0, cost_basis=50.0, last_buy_ns=0),
        ),
    )
    out = step(state=state, policy=policy, event=_gate_update(now_ns=0, radiant_awaited=6))
    out = step(state=out.state, policy=policy, event=Wake(now_ns=0, forced=True))
    assert all(place.side != "SELL" for place in out.plan.places)


@pytest.mark.parametrize("held_killer", [False, True])
def test_sell_selects_allowed_inventory_before_size(held_killer: bool) -> None:
    state = replace(
        _idle(),
        books=_books(now_ns=0, mid=0.5),
        signal=_signal(deaths_radiant=0, deaths_dire=0),
        kill_gate=_gate_update(now_ns=0, radiant_awaited=1).gate,
        orders=(_order(order_id="killer-sell", side="SELL", token_index=1, now_ns=0),)
        if held_killer
        else (),
        inventory=(
            TokenInventory(0, 100.0, 50.0, -60 * NS),
            TokenInventory(1, 200.0, 100.0, -60 * NS),
        ),
    )
    sell = decide_sell(
        state=state,
        policy=_policy(),
        now_ns=0,
        fair=0.5,
        exposure=kill_exposure(state=state, now_ns=0),
    )
    assert sell is not None
    assert sell.token_index == 0
    assert sell.quantity == 100


def test_board_prediction_updates_latch_without_renewing_table_age() -> None:
    state = replace(_idle(), books=_books(now_ns=0, mid=0.5))
    initial = replace(
        _signal(deaths_radiant=0, deaths_dire=0, delta=0.04, anchor=0.5),
        source_received_ns=0,
        received_ns=0,
    )
    out = step(state=state, policy=_policy(), event=SignalUpdate(0, initial))
    out = step(state=out.state, policy=_policy(), event=Wake(0, True))
    board_signal = replace(initial, received_ns=5 * NS, predicted_delta=0.08)
    out = step(state=out.state, policy=_policy(), event=SignalUpdate(5 * NS, board_signal))
    out = step(state=out.state, policy=_policy(), event=Wake(5 * NS, True))
    assert out.state.latch is not None
    assert out.state.latch.predicted_delta == 0.08
    assert out.state.latch.signal_ts_ns == 5 * NS
    assert out.state.latch.fair_ts_ns == 0
    current = replace(out.state, books=_books(now_ns=46 * NS, mid=0.5))
    out = step(state=current, policy=_policy(), event=Wake(46 * NS, True))
    assert out.state.latch is None
