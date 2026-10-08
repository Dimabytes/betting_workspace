# pyright: reportPrivateUsage=false

from dataclasses import replace

import pytest
from test_strategy_core import (
    NS,
    _accept_all,
    _balanced,
    _books,
    _buy_places,
    _feed,
    _idle,
    _inventory,
    _policy,
    _sell_places,
    _signal,
    _wake,
)

from strategy.engine import step
from strategy.types import (
    BookUpdate,
    CancelAck,
    Plan,
    Recovery,
    RecoveryVerified,
    StrategyState,
    Wake,
)


def _recover(*, token_index: int, verified: bool) -> StrategyState:
    policy = _policy()
    bid = 0.64 if token_index == 0 else 0.34
    delta = 0.03 if token_index == 0 else -0.03
    state = _feed(
        _idle(),
        policy,
        0,
        books=_balanced(ts_ns=0, bid0=bid, ask0=bid + 0.02),
        signal=_signal(delta=delta, now_ns=0, anchor=bid + 0.01),
    )
    inventory = _inventory(yes_qty=1760.0) if token_index == 0 else _inventory(no_qty=1760.0)
    state = replace(state, inventory=inventory, has_buy_fill=True)
    state, plan = _wake(state, policy, 0)
    assert _buy_places(plan) == ()
    assert _sell_places(plan)[0].price == 0.68
    state = _accept_all(state, policy, 0)
    out = step(state=state, policy=policy, event=Recovery(now_ns=NS, restored_buy_ids=()))
    assert _buy_places(out.plan) == ()
    state = out.state
    if verified:
        out = step(
            state=state,
            policy=policy,
            event=RecoveryVerified(
                now_ns=2 * NS,
                generation=state.recovery_generation,
                inventory=inventory,
            ),
        )
        assert _buy_places(out.plan) == ()
        state = out.state
    assert state.sell_only
    assert state.recovery_pending is not verified
    return state


def _ack_reprices(*, state: StrategyState, plan: Plan, now_ns: int) -> StrategyState:
    policy = _policy()
    assert _buy_places(plan) == ()
    for cancel in plan.cancels:
        out = step(
            state=state, policy=policy, event=CancelAck(now_ns=now_ns, order_id=cancel.order_id)
        )
        assert _buy_places(out.plan) == ()
        state = out.state
    return _accept_all(state, policy, now_ns)


@pytest.mark.parametrize("token_index", [0, 1])
@pytest.mark.parametrize("verified", [False, True])
def test_recovery_keeps_fair_fresh_past_original_expiry(token_index: int, verified: bool) -> None:
    policy = _policy()
    state = _recover(token_index=token_index, verified=verified)
    bid = 0.58 if token_index == 0 else 0.40
    delta = 0.04 if token_index == 0 else -0.04
    for second in (10, 20, 30, 40, 50):
        now_ns = second * NS
        state = _feed(
            state,
            policy,
            now_ns,
            books=_balanced(ts_ns=now_ns, bid0=bid, ask0=bid + 0.02),
            signal=_signal(delta=delta, now_ns=now_ns, anchor=bid + 0.01),
        )
        state, plan = _wake(state, policy, now_ns)
        assert state.latch is not None
        assert state.latch.fair_ts_ns == now_ns
        expected_fair = 0.63 if token_index == 0 else 0.37
        assert state.latch.fair_radiant == pytest.approx(expected_fair)
        state = _ack_reprices(state=state, plan=plan, now_ns=now_ns)
        sell = next(order for order in state.orders if order.side == "SELL")
        assert sell.price == 0.63
        assert sell.submitted_qty == 1760.0
        assert state.sell_only
        assert state.recovery_pending is not verified


def test_recovery_reanchors_on_scheduled_boundary_without_refreshing_signal_age() -> None:
    policy = _policy()
    state = _recover(token_index=0, verified=False)
    book_ns = NS + NS // 10
    state = step(
        state=state,
        policy=policy,
        event=BookUpdate(now_ns=book_ns, books=_balanced(ts_ns=book_ns, bid0=0.65, ask0=0.67)),
    ).state
    early = step(state=state, policy=policy, event=Wake(now_ns=NS + NS // 5, forced=True))
    assert early.plan.cancels == ()
    boundary_ns = NS + round(policy.latch_reanchor_s * NS)
    assert early.next_wake_ns == boundary_ns
    out = step(state=early.state, policy=policy, event=Wake(now_ns=boundary_ns, forced=True))
    assert out.state.latch is not None
    assert out.state.latch.fair_radiant == pytest.approx(0.69)
    assert out.state.latch.fair_ts_ns == 0
    assert out.state.latch.anchored_ns == boundary_ns
    assert out.next_wake_ns > boundary_ns
    state = _ack_reprices(state=out.state, plan=out.plan, now_ns=boundary_ns)
    assert next(order for order in state.orders if order.side == "SELL").price == 0.69


@pytest.mark.parametrize("missing_signal", [False, True])
def test_recovery_joins_ask_without_fresh_signal_then_restores_fair(missing_signal: bool) -> None:
    policy = _policy()
    state = _recover(token_index=0, verified=False)
    now_ns = 46 * NS
    state = replace(
        state,
        books=_balanced(ts_ns=now_ns, bid0=0.64, ask0=0.66),
        signal=None if missing_signal else state.signal,
    )
    state, plan = _wake(state, policy, now_ns)
    assert state.latch is None
    state = _ack_reprices(state=state, plan=plan, now_ns=now_ns)
    assert next(order for order in state.orders if order.side == "SELL").price == 0.66
    now_ns += NS
    state = _feed(
        state,
        policy,
        now_ns,
        books=_balanced(ts_ns=now_ns, bid0=0.64, ask0=0.66),
        signal=_signal(delta=0.05, now_ns=now_ns, anchor=0.65),
    )
    state, plan = _wake(state, policy, now_ns)
    state = _ack_reprices(state=state, plan=plan, now_ns=now_ns)
    assert state.latch is not None
    assert state.latch.fair_ts_ns == now_ns
    assert next(order for order in state.orders if order.side == "SELL").price == 0.70


def test_recovery_preserves_anchor_rejection_until_valid_signal() -> None:
    policy = _policy()
    state = _recover(token_index=0, verified=False)
    previous = state.latch
    assert previous is not None
    for second in (10, 11):
        now_ns = second * NS
        state = _feed(
            state,
            policy,
            now_ns,
            books=_balanced(ts_ns=now_ns, bid0=0.65, ask0=0.67),
            signal=_signal(delta=0.05, now_ns=10 * NS, anchor=0.80),
        )
        state, plan = _wake(state, policy, now_ns)
        assert state.latch is not None
        assert state.latch.no_buy_reason == "anchor"
        assert state.latch.fair_radiant == previous.fair_radiant
        assert plan.cancels == ()
        assert _buy_places(plan) == ()
    now_ns = 12 * NS
    state = _feed(
        state,
        policy,
        now_ns,
        books=_balanced(ts_ns=now_ns, bid0=0.65, ask0=0.67),
        signal=_signal(delta=0.05, now_ns=now_ns, anchor=0.66),
    )
    state, plan = _wake(state, policy, now_ns)
    state = _ack_reprices(state=state, plan=plan, now_ns=now_ns)
    assert state.latch is not None
    assert state.latch.no_buy_reason == ""
    assert next(order for order in state.orders if order.side == "SELL").price == 0.71


def test_recovery_preserves_exit_when_pair_mid_is_unusable() -> None:
    policy = _policy()
    state = _recover(token_index=0, verified=False)
    previous = state.latch
    now_ns = 10 * NS
    state = _feed(
        state,
        policy,
        now_ns,
        books=_books(bid0=0.64, ask0=0.66, bid1=0.44, ask1=0.46, ts_ns=now_ns),
        signal=_signal(delta=0.05, now_ns=now_ns, anchor=0.65),
    )
    state, plan = _wake(state, policy, now_ns)
    assert state.latch == previous
    assert plan.cancels == ()
    assert plan.keep
    assert _buy_places(plan) == ()


@pytest.mark.parametrize("missing_books", [False, True])
def test_recovery_blocks_exit_on_unavailable_books(missing_books: bool) -> None:
    policy = _policy()
    state = _recover(token_index=0, verified=False)
    previous = state.latch
    if missing_books:
        state = replace(state, books=None)
    state, plan = _wake(state, policy, 10 * NS)
    assert plan.block_reason == "stale_book"
    assert _sell_places(plan) == ()
    assert _buy_places(plan) == ()
    assert plan.cancels
    assert state.latch == previous


def test_recovery_refreshes_fair_while_sell_min_life_holds_price() -> None:
    policy = _policy()
    state = _recover(token_index=0, verified=False)
    for offset_ns in (0, NS // 2, NS):
        now_ns = 10 * NS + offset_ns
        delta = 0.05 if offset_ns == 0 else 0.06
        state = _feed(
            state,
            policy,
            now_ns,
            books=_balanced(ts_ns=now_ns, bid0=0.64, ask0=0.66),
            signal=_signal(delta=delta, now_ns=now_ns, anchor=0.65),
        )
        state, plan = _wake(state, policy, now_ns)
        assert state.latch is not None
        assert state.latch.fair_radiant == pytest.approx(0.65 + delta)
        if now_ns == 10 * NS + NS // 2:
            assert plan.cancels == ()
            assert next(order for order in state.orders if order.side == "SELL").price == 0.70
        state = _ack_reprices(state=state, plan=plan, now_ns=now_ns)
    assert next(order for order in state.orders if order.side == "SELL").price == 0.71
