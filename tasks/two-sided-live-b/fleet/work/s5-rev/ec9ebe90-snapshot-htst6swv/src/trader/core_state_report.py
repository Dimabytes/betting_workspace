"""Dump the strategy state a core trace ends in, and say whether an order is stuck.

`session.jsonl` records fills and the places and cancels that reached the venue.
An order whose place never got there, or whose cancel the venue never proved, is
in neither tape: both hang off the same worker lookup. This replays
`core_trace.jsonl` and prints the state they hide, the plan `requote` would
produce on the next wake, and the digest evidence that the state is the recorded
one.
"""

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from shared.utils.jsonl_io import resolve_jsonl
from strategy.lifecycle import sell_occupied
from strategy.policy import Follow300Policy
from strategy.quoting import held_sell, requote
from strategy.types import Plan, RestingOrder, StrategyState
from trader.paths import CORE_TRACE_FILENAME
from trader.replay_core_trace import IdleSince, ReplayedState, TraceAction, replay_state

# A place or a cancel that the venue answers takes well under a second. An order
# outside `live` for this long is waiting on an answer that is not coming.
STUCK_ORDER_SECONDS = 30.0


@dataclass(frozen=True)
class StuckOrder:
    """One order the venue never resolved, and the slot it holds while it sits."""

    order_id: str
    side: str
    status: str
    age_s: float
    holds: str


@dataclass(frozen=True)
class CoreStateReport:
    """One match: the replayed state, what is stuck in it, and the next plan."""

    name: str
    replayed: ReplayedState
    trace_age_s: float
    stuck: tuple[StuckOrder, ...]
    plan: Plan

    @property
    def wedged(self) -> bool:
        return bool(self.stuck)


def order_age_s(*, order: RestingOrder, now_ns: int) -> float:
    return (now_ns - order.placed_ns) / 1e9


def find_stuck_orders(
    *,
    state: StrategyState,
    now_ns: int,
    idle: tuple[IdleSince, ...],
    max_idle_s: float,
) -> tuple[StuckOrder, ...]:
    """Orders outside `live` long enough that the venue owes them an answer.

    Age is `now_ns` minus the trace event where the order left `live`, not
    `placed_ns`. A SELL here holds the only exit slot through `sell_occupied`.
    A BUY pins its rung through `rung_occupied`.
    """
    since_ns = {row.order_id: row.since_ns for row in idle}
    stuck: list[StuckOrder] = []
    for order in state.orders:
        if order.status == "live":
            continue
        started = since_ns.get(order.order_id)
        if started is None:
            continue
        age_s = (now_ns - started) / 1e9
        if age_s < max_idle_s:
            continue
        holds = "exit slot" if order.side == "SELL" else f"rung {order.level_index}"
        stuck.append(
            StuckOrder(
                order_id=order.order_id,
                side=order.side,
                status=order.status,
                age_s=age_s,
                holds=holds,
            )
        )
    return tuple(stuck)


def next_plan(*, state: StrategyState, policy: Follow300Policy, now_ns: int) -> Plan:
    """The plan `requote` produces on this state, run on a copy the caller discards."""
    _state, plan = requote(state=state, policy=policy, now_ns=now_ns)
    return plan


def build_report(
    *, path: Path, use_current_policy: bool, max_idle_s: float, tail: int
) -> CoreStateReport:
    replayed = replay_state(path, use_current_policy=use_current_policy, tail=tail)
    stuck = find_stuck_orders(
        state=replayed.state,
        now_ns=replayed.now_ns,
        idle=replayed.idle,
        max_idle_s=max_idle_s,
    )
    plan = next_plan(state=replayed.state, policy=replayed.policy, now_ns=replayed.now_ns)
    return CoreStateReport(
        name=path.parent.name,
        replayed=replayed,
        trace_age_s=time.time() - path.stat().st_mtime,
        stuck=stuck,
        plan=plan,
    )


def format_verdict(report: CoreStateReport) -> str:
    if not report.stuck:
        return f"VERDICT: OK        {report.name}"
    first = report.stuck[0]
    return (
        f"VERDICT: WEDGED    {report.name}  {first.side} {first.order_id} "
        f"status={first.status} age={first.age_s:.0f}s holds={first.holds}"
    )


def _format_age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _format_digests(report: CoreStateReport) -> str:
    parts = [
        f"{check.group}={'OK' if check.matches else 'DIFF'}" for check in report.replayed.digests
    ]
    return " ".join(parts)


def _format_book(state: StrategyState) -> str:
    books = state.books
    if books is None:
        return "book      (none)"
    yes, no = books.tokens
    return (
        f"book      YES bid={yes.bid:.3f} ask={yes.ask:.3f} | NO bid={no.bid:.3f} ask={no.ask:.3f}"
    )


def _format_fair(state: StrategyState, now_ns: int) -> str:
    latch = state.latch
    if latch is None:
        return "fair      (none)"
    age_s = (now_ns - latch.fair_ts_ns) / 1e9
    return (
        f"fair      radiant={latch.fair_radiant:.4f} valid={latch.fair_valid} "
        f"age={_format_age(age_s)} no_buy_reason={latch.no_buy_reason or '-'}"
    )


def _format_orders(report: CoreStateReport) -> list[str]:
    state = report.replayed.state
    if not state.orders:
        return ["orders    (none)"]
    since_ns = {row.order_id: row.since_ns for row in report.replayed.idle}
    stuck_ids = {order.order_id for order in report.stuck}
    now_ns = report.replayed.now_ns
    lines = ["orders"]
    for order in state.orders:
        started = since_ns.get(order.order_id)
        age_s = (
            (now_ns - started) / 1e9
            if started is not None
            else order_age_s(order=order, now_ns=now_ns)
        )
        mark = " <- STUCK" if order.order_id in stuck_ids else ""
        lines.append(
            f"  {order.order_id:>5} {order.side} @{order.price} qty={order.submitted_qty} "
            f"filled={order.filled_qty} status={order.status} "
            f"reason={order.cancel_reason or '-'} age={_format_age(age_s)}{mark}"
        )
    return lines


def _format_action(action: TraceAction) -> str:
    places = [f"{place.side}@{place.price}x{place.quantity:.1f}" for place in action.places]
    cancels = [cancel.order_id for cancel in action.cancels]
    return f"  seq={action.seq} {action.event_kind} places={places} cancels={cancels}"


def format_report(report: CoreStateReport) -> list[str]:
    replayed = report.replayed
    state = replayed.state
    truncated = replayed.truncated_reason or "-"
    held = held_sell(state, frozenset())
    lines = [
        format_verdict(report),
        f"trace     events={replayed.events} plan_mismatch={replayed.plan_mismatches} "
        f"truncated={truncated} written={_format_age(report.trace_age_s)} ago",
        f"digests   {_format_digests(report)}",
        f"clock     t={state.clock.game_second}s paused={state.clock.paused} "
        f"ended={state.clock.game_ended}",
        f"flags     sell_only={state.sell_only} winding_down={state.winding_down} "
        f"recovery_pending={state.recovery_pending}",
        f"perms     halt={state.permissions.halt} reduce_only={state.permissions.reduce_only} "
        f"allow_buy={state.permissions.allow_buy} allow_sell={state.permissions.allow_sell}",
        _format_fair(state, replayed.now_ns),
        _format_book(state),
        f"inventory YES={state.inventory[0].qty} NO={state.inventory[1].qty}  "
        f"sell_occupied={sell_occupied(state=state)} "
        f"held_sell={held.order_id if held is not None else None}",
    ]
    lines.extend(_format_orders(report))
    places = [(place.side, place.price, round(place.quantity, 1)) for place in report.plan.places]
    cancels = [cancel.order_id for cancel in report.plan.cancels]
    lines.append(
        f"requote   block_reason={report.plan.block_reason or '-'} "
        f"places={places} cancels={cancels}"
    )
    if report.replayed.block_reasons:
        lines.append(f"blocks    newest first: {list(report.replayed.block_reasons)}")
    if report.replayed.actions:
        lines.append("last plans that acted, newest first")
        lines.extend(_format_action(action) for action in report.replayed.actions)
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="core_state_report")
    parser.add_argument("--archive", type=Path, nargs="+", required=True)
    parser.add_argument("--policy", choices=("archive", "current"), default="archive")
    parser.add_argument("--max-idle-s", type=float, default=STUCK_ORDER_SECONDS)
    parser.add_argument("--quiet", action="store_true", help="print the verdict line only")
    parser.add_argument("--tail", type=int, default=10, help="how many acting plans to show")
    args = parser.parse_args(argv)
    reports = [
        build_report(
            path=resolve_jsonl(directory / CORE_TRACE_FILENAME),
            use_current_policy=args.policy == "current",
            max_idle_s=args.max_idle_s,
            tail=0 if args.quiet else args.tail,
        )
        for directory in args.archive
    ]
    for report in reports:
        if args.quiet:
            print(format_verdict(report))
            continue
        print("\n".join(format_report(report)))
        print("")
    return 1 if any(report.wedged for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
