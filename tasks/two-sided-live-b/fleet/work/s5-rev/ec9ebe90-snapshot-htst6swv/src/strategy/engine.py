"""One inbound event in; state, plan, and next wake out."""

from dataclasses import replace

from strategy.lifecycle import (
    apply_accepted,
    apply_buy_settled,
    apply_cancel_ack,
    apply_cancel_timeout,
    apply_cancel_unsettled,
    apply_fill,
    apply_merged,
    apply_ownership_resolved,
    apply_recovery,
    apply_recovery_verified,
    apply_rejected,
    apply_settlement,
    apply_submit_timeout,
)
from strategy.policy import Policy, TwoSidedPolicy
from strategy.quoting import empty_plan, requote
from strategy.scheduling import mark_evaluated, next_wake_ns, note_schedule, should_evaluate
from strategy.two_sided_quoting import requote_two_sided
from strategy.types import (
    BookUpdate,
    BudgetUpdate,
    BuySettled,
    CancelAck,
    CancelOrder,
    CancelTimeout,
    CancelUnsettled,
    ClockUpdate,
    EngineOutput,
    Fill,
    InboundEvent,
    KillGateUpdate,
    LimitsUpdate,
    Merged,
    OrderAccepted,
    OrderRejected,
    OwnershipResolved,
    PermissionsUpdate,
    Plan,
    Recovery,
    RecoveryVerified,
    SettlementStatus,
    SignalUpdate,
    StrategyState,
    SubmitTimeout,
    Wake,
)


def _store(*, state: StrategyState, event: InboundEvent) -> StrategyState | None:
    match event:
        case BookUpdate():
            return replace(state, books=event.books)
        case SignalUpdate():
            return replace(state, signal=event.signal)
        case ClockUpdate():
            return replace(state, clock=event.clock)
        case KillGateUpdate():
            return replace(state, kill_gate=event.gate)
        case PermissionsUpdate():
            return replace(state, permissions=event.permissions)
        case BudgetUpdate():
            return replace(state, budget=event.budget)
        case LimitsUpdate():
            return replace(state, limits=event.limits)
        case Wake():
            return state
        case _:
            return None


def _apply_session_event(*, state: StrategyState, event: InboundEvent) -> StrategyState:
    match event:
        case Recovery():
            return apply_recovery(state=state, event=event)
        case RecoveryVerified():
            return apply_recovery_verified(state=state, event=event)
        case OwnershipResolved():
            return apply_ownership_resolved(state=state, event=event)
        case SettlementStatus():
            return apply_settlement(state=state, event=event)
        case Merged():
            return apply_merged(state=state, event=event)
        case _:
            return state


def _apply_fill_path(*, state: StrategyState, event: InboundEvent) -> StrategyState:
    match event:
        case Fill():
            return apply_fill(state=state, event=event)
        case OrderAccepted():
            return apply_accepted(state=state, order_id=event.order_id, now_ns=event.now_ns)
        case OrderRejected():
            return apply_rejected(state=state, event=event)
        case SubmitTimeout():
            return apply_submit_timeout(state=state, order_id=event.order_id)
        case CancelAck():
            return apply_cancel_ack(state=state, order_id=event.order_id)
        case CancelUnsettled():
            return apply_cancel_unsettled(state=state, order_id=event.order_id)
        case BuySettled():
            return apply_buy_settled(
                state=state, order_id=event.order_id, matched_qty=event.matched_qty
            )
        case CancelTimeout():
            return apply_cancel_timeout(state=state, event=event)
        case Recovery() | RecoveryVerified() | OwnershipResolved() | SettlementStatus() | Merged():
            return _apply_session_event(state=state, event=event)
        case _:
            return state


def _apply(*, state: StrategyState, event: InboundEvent) -> StrategyState:
    stored = _store(state=state, event=event)
    if stored is not None:
        return stored
    return _apply_fill_path(state=state, event=event)


def _canceling_ids(state: StrategyState) -> frozenset[str]:
    return frozenset(order.order_id for order in state.orders if order.status == "canceling")


def _cancels_opened(*, prior: frozenset[str], state: StrategyState) -> tuple[CancelOrder, ...]:
    return tuple(
        CancelOrder(order_id=order.order_id, reason=order.cancel_reason)
        for order in state.orders
        if order.status == "canceling" and order.order_id not in prior
    )


def _requote(*, state: StrategyState, policy: Policy, now_ns: int) -> tuple[StrategyState, Plan]:
    if isinstance(policy, TwoSidedPolicy):
        return requote_two_sided(state=state, policy=policy, now_ns=now_ns)
    return requote(state=state, policy=policy, now_ns=now_ns)


def step(*, state: StrategyState, policy: Policy, event: InboundEvent) -> EngineOutput:
    prior = _canceling_ids(state)
    state = _apply(state=state, event=event)
    state = note_schedule(state=state, event=event)
    if should_evaluate(event=event, state=state, policy=policy):
        state, plan = _requote(state=state, policy=policy, now_ns=event.now_ns)
        plan = replace(plan, cancels=_cancels_opened(prior=prior, state=state))
        state = mark_evaluated(state=state, now_ns=event.now_ns)
    else:
        plan = empty_plan(reason="")
    return EngineOutput(
        state=state,
        plan=plan,
        next_wake_ns=next_wake_ns(state=state, policy=policy, now_ns=event.now_ns),
    )
