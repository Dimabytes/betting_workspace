"""Pure quote-cadence deadlines. Adapters fire Wake at next_wake_ns."""

from dataclasses import replace

from strategy.kill_gate import kill_gate_boundary_ns
from strategy.mid_spike import mid_spike_boundary_ns
from strategy.policy import Policy, TwoSidedPolicy
from strategy.quoting import reanchor_boundary_ns, sell_boundary_ns
from strategy.two_sided_quoting import reprice_hold_boundary_ns
from strategy.types import (
    BookUpdate,
    BuySettled,
    CancelAck,
    CancelTimeout,
    CancelUnsettled,
    ClockUpdate,
    Fill,
    InboundEvent,
    KillGateUpdate,
    Merged,
    OrderAccepted,
    OrderRejected,
    OwnershipResolved,
    PermissionsUpdate,
    Recovery,
    RecoveryVerified,
    SettlementStatus,
    SignalUpdate,
    StrategyState,
    SubmitTimeout,
    Wake,
)

NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000


def debounce_ns(policy: Policy) -> int:
    return int(policy.debounce_ms) * NS_PER_MS


def fallback_ns(policy: Policy) -> int:
    return round(policy.fallback_timer_s * NS_PER_S)


def note_schedule(*, state: StrategyState, event: InboundEvent) -> StrategyState:
    """Open the coalescing window on the first dirtying event.

    A later dirtying event finds the window already open, and the old copy of the
    whole state reproduced it field for field. Book updates arrive tens of thousands
    of times per map, so that copy is skipped.
    """
    dirtying = isinstance(
        event,
        BookUpdate
        | SignalUpdate
        | Fill
        | Merged
        | OrderAccepted
        | OrderRejected
        | SubmitTimeout
        | CancelTimeout
        | SettlementStatus
        | KillGateUpdate,
    )
    if dirtying and state.schedule.dirty_open_ns is None:
        return replace(state, schedule=replace(state.schedule, dirty_open_ns=event.now_ns))
    return state


def mark_evaluated(*, state: StrategyState, now_ns: int) -> StrategyState:
    schedule = state.schedule
    return replace(
        state,
        schedule=replace(schedule, dirty_open_ns=None, last_eval_ns=now_ns),
    )


def entry_stale_boundary_ns(*, state: StrategyState) -> int | None:
    """When the open entry signal ages out — cancel BUYs; must wake exactly then."""
    signal = state.signal
    if signal is None:
        return None
    if not state.orders:
        return None
    if not any(
        order.side == "BUY" and order.status not in ("canceling", "gone") for order in state.orders
    ):
        return None
    return int(signal.source_received_ns + round(state.freshness.entry_stale_s * NS_PER_S))


def _cadence_deadline_ns(*, state: StrategyState, policy: Policy, now_ns: int) -> int:
    schedule = state.schedule
    dirty = schedule.dirty_open_ns
    if dirty is not None:
        return dirty + debounce_ns(policy)
    origin = schedule.last_eval_ns if schedule.last_eval_ns > 0 else now_ns
    return origin + fallback_ns(policy)


def _hold_deadline_ns(
    *, state: StrategyState, policy: TwoSidedPolicy, deadline: int, now_ns: int
) -> int:
    hold = reprice_hold_boundary_ns(state=state, policy=policy)
    if hold is None:
        return deadline
    if hold <= now_ns:
        return now_ns
    return min(deadline, hold)


def armed_deadline_ns(*, state: StrategyState, policy: Policy, now_ns: int) -> int:
    deadline = _cadence_deadline_ns(state=state, policy=policy, now_ns=now_ns)
    if isinstance(policy, TwoSidedPolicy):
        return _hold_deadline_ns(state=state, policy=policy, deadline=deadline, now_ns=now_ns)
    waking = (
        sell_boundary_ns(state=state, policy=policy, now_ns=now_ns),
        mid_spike_boundary_ns(state=state, now_ns=now_ns),
        kill_gate_boundary_ns(state=state, now_ns=now_ns),
        entry_stale_boundary_ns(state=state),
    )
    for boundary in waking:
        if boundary is None:
            continue
        if boundary <= now_ns:
            return now_ns
        if boundary < deadline:
            deadline = boundary
    tightening = (reanchor_boundary_ns(state=state, policy=policy),)
    for boundary in tightening:
        if boundary is not None and now_ns < boundary < deadline:
            deadline = boundary
    return deadline


def next_wake_ns(*, state: StrategyState, policy: Policy, now_ns: int) -> int:
    deadline = armed_deadline_ns(state=state, policy=policy, now_ns=now_ns)
    if deadline > now_ns:
        return deadline
    if state.schedule.dirty_open_ns is not None:
        return now_ns
    return now_ns + fallback_ns(policy)


def should_evaluate(*, event: InboundEvent, state: StrategyState, policy: Policy) -> bool:
    match event:
        case (
            CancelAck()
            | CancelUnsettled()
            | BuySettled()
            | Recovery()
            | RecoveryVerified()
            | OwnershipResolved()
        ):
            return True
        case PermissionsUpdate() if event.permissions.halt:
            return True
        case ClockUpdate() if event.clock.paused or event.clock.game_ended:
            return True
        case Wake():
            return event.forced or event.now_ns >= armed_deadline_ns(
                state=state, policy=policy, now_ns=event.now_ns
            )
        case _:
            return False
