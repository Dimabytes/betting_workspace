"""100 ms coalescing window and 2 s fallback."""

# pyright: reportPrivateUsage=false

from dataclasses import replace

from test_strategy_core import (
    NS,
    _accept_all,
    _balanced,
    _buy_places,
    _feed,
    _fill_of,
    _idle,
    _policy,
    _prime,
    _sell_places,
    _signal,
    _wake,
)

from shared.constants.strategy import LATCH_REANCHOR_OFF_S, LATCH_REANCHOR_SECONDS
from strategy.engine import step
from strategy.scheduling import (
    armed_deadline_ns,
    debounce_ns,
    entry_stale_boundary_ns,
    next_wake_ns,
)
from strategy.types import (
    BookUpdate,
    BuySettled,
    CancelUnsettled,
    OrderAccepted,
    OrderRejected,
    Permissions,
    PermissionsUpdate,
    Wake,
)


def test_first_book_opens_fixed_window() -> None:
    policy = _policy()
    out = step(state=_idle(), policy=policy, event=BookUpdate(now_ns=0, books=_balanced(ts_ns=0)))
    assert out.plan.places == ()
    assert out.next_wake_ns == debounce_ns(policy)
    early = step(
        state=out.state, policy=policy, event=Wake(now_ns=debounce_ns(policy) - 1, forced=False)
    )
    assert early.plan.places == ()
    due = step(state=out.state, policy=policy, event=Wake(now_ns=debounce_ns(policy), forced=False))
    assert due.plan.block_reason in ("stale_signal", "stale_book", "fair", "")


def test_later_books_do_not_push_deadline() -> None:
    policy = _policy()
    first = step(state=_idle(), policy=policy, event=BookUpdate(now_ns=0, books=_balanced(ts_ns=0)))
    later = step(
        state=first.state,
        policy=policy,
        event=BookUpdate(
            now_ns=40_000_000, books=_balanced(ts_ns=40_000_000, bid0=0.49, ask0=0.51)
        ),
    )
    assert later.next_wake_ns == debounce_ns(policy)
    burst = step(
        state=later.state,
        policy=policy,
        event=BookUpdate(
            now_ns=90_000_000, books=_balanced(ts_ns=90_000_000, bid0=0.48, ask0=0.50)
        ),
    )
    assert burst.next_wake_ns == debounce_ns(policy)


def test_fallback_after_quote() -> None:
    state, policy, _plan = _prime()
    policy = replace(policy, latch_reanchor_s=LATCH_REANCHOR_OFF_S)
    expected = state.schedule.last_eval_ns + round(policy.fallback_timer_s * 1_000_000_000)
    assert next_wake_ns(state=state, policy=policy, now_ns=state.schedule.last_eval_ns) == expected


def test_halt_evaluates_immediately() -> None:
    state, policy, plan = _prime()
    assert _buy_places(plan)
    halted = step(
        state=state,
        policy=policy,
        event=PermissionsUpdate(
            now_ns=NS,
            permissions=Permissions(
                halt=True,
                reduce_only=False,
                allow_buy=False,
                allow_sell=False,
                sell_unconfirmed=False,
            ),
        ),
    )
    assert any(cancel.reason == "halt" for cancel in halted.plan.cancels)


def test_min_life_boundary_preempts_fallback() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = step(
        state=state, policy=policy, event=_fill_of(top, fill_id="b", qty=10.0, now_ns=0)
    ).state
    settle = 10 * NS
    state = _feed(
        state,
        policy,
        settle,
        books=_balanced(ts_ns=settle),
        signal=_signal(delta=0.05, now_ns=0),
        second=100,
    )
    state, placed = _wake(state, policy, settle)
    sells = _sell_places(placed)
    assert sells
    accepted = step(
        state=state,
        policy=policy,
        event=OrderAccepted(now_ns=settle, order_id=sells[0].order_id),
    )
    held = next(order for order in accepted.state.orders if order.side == "SELL")
    assert held.accepted_ns is not None
    life_end = held.accepted_ns + NS
    assert accepted.next_wake_ns <= life_end


def test_forced_wake_keeps_honest_now() -> None:
    policy = _policy()
    opened = step(
        state=_idle(), policy=policy, event=BookUpdate(now_ns=0, books=_balanced(ts_ns=0))
    )
    early = debounce_ns(policy) - 1
    forced = step(state=opened.state, policy=policy, event=Wake(now_ns=early, forced=True))
    assert forced.state.schedule.last_eval_ns == early


def test_reject_opens_dirty_window() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    out = step(
        state=state,
        policy=policy,
        event=OrderRejected(now_ns=0, order_id=top.order_id, reason="denied"),
    )
    assert out.state.schedule.dirty_open_ns == 0
    assert out.next_wake_ns == debounce_ns(policy)


def test_entry_stale_pulls_wake_earlier_than_fallback() -> None:
    state, policy, _plan = _prime()
    policy = replace(policy, latch_reanchor_s=LATCH_REANCHOR_OFF_S)
    signal = state.signal
    assert signal is not None
    last_eval = state.schedule.last_eval_ns
    # Signal ages out 1s from now — sooner than the 2s fallback.
    fresh_signal = replace(
        signal, source_received_ns=last_eval - round(15.0 * NS), received_ns=last_eval
    )
    state = replace(state, signal=fresh_signal)
    stale_at = last_eval + round(1.0 * 1_000_000_000)
    assert entry_stale_boundary_ns(state=state) == stale_at
    wake = next_wake_ns(state=state, policy=policy, now_ns=last_eval)
    assert wake == stale_at


def test_wake_lands_on_reanchor_boundary() -> None:
    state, policy, _ = _prime()
    assert next_wake_ns(state=state, policy=policy, now_ns=0) == round(LATCH_REANCHOR_SECONDS * NS)


def test_unsettled_events_evaluate_inside_debounce() -> None:
    state, policy, plan = _prime()
    top = _buy_places(plan)[0]
    state = _accept_all(state, policy, 0)
    state = replace(state, schedule=replace(state.schedule, dirty_open_ns=None, last_eval_ns=0))
    early = 1_000_000
    unsettled = step(
        state=state,
        policy=policy,
        event=CancelUnsettled(now_ns=early, order_id=top.order_id),
    )
    assert unsettled.state.schedule.dirty_open_ns is None
    assert unsettled.state.schedule.last_eval_ns == early
    gone = next(order for order in unsettled.state.orders if order.order_id == top.order_id)
    assert gone.status == "gone"
    window = early + 1
    reopened = replace(
        unsettled.state,
        schedule=replace(unsettled.state.schedule, dirty_open_ns=window),
    )
    settled_at = window + 1
    assert settled_at < window + debounce_ns(policy)
    settled = step(
        state=reopened,
        policy=policy,
        event=BuySettled(now_ns=settled_at, order_id=top.order_id, matched_qty=0.0),
    )
    assert settled.state.schedule.dirty_open_ns is None
    assert settled.state.schedule.last_eval_ns == settled_at
    assert not any(order.order_id == top.order_id for order in settled.state.orders)


def test_gone_buy_does_not_arm_entry_expiry() -> None:
    state, policy, _plan = _prime()
    policy = replace(policy, latch_reanchor_s=LATCH_REANCHOR_OFF_S)
    signal = state.signal
    assert signal is not None
    last_eval = state.schedule.last_eval_ns
    aged = replace(signal, source_received_ns=last_eval - round(15.0 * NS), received_ns=last_eval)
    gone_orders = tuple(replace(order, status="gone") for order in state.orders)
    gone_only = replace(state, signal=aged, orders=gone_orders)
    assert entry_stale_boundary_ns(state=gone_only) is None
    fallback_at = last_eval + round(policy.fallback_timer_s * NS)
    assert next_wake_ns(state=gone_only, policy=policy, now_ns=last_eval) == fallback_at
    dirty = replace(gone_only, schedule=replace(gone_only.schedule, dirty_open_ns=last_eval))
    assert next_wake_ns(state=dirty, policy=policy, now_ns=last_eval) == last_eval + debounce_ns(
        policy
    )
    live = replace(gone_orders[0], status="live")
    mixed = replace(gone_only, orders=(live, *gone_orders[1:]))
    stale_at = int(aged.source_received_ns + round(state.freshness.entry_stale_s * NS))
    assert entry_stale_boundary_ns(state=mixed) == stale_at
    assert next_wake_ns(state=mixed, policy=policy, now_ns=last_eval) == stale_at


def test_past_reanchor_boundary_keeps_debounce_deadline() -> None:
    state, policy, _ = _prime()
    dirty = NS
    state = replace(state, schedule=replace(state.schedule, last_eval_ns=NS, dirty_open_ns=dirty))
    assert armed_deadline_ns(state=state, policy=policy, now_ns=NS) == dirty + debounce_ns(policy)
