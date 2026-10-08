"""Offline replay of core_trace.jsonl against strategy.engine.step."""

import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from shared.utils.engine_cadence import read_engine_cadence
from shared.utils.jsonl_io import open_maybe_gz
from strategy.engine import step
from strategy.lifecycle import empty_state
from strategy.policy import Follow300Policy, follow300_policy
from strategy.types import (
    Budget,
    CancelOrder,
    GameClock,
    Permissions,
    PlaceOrder,
    Plan,
    StrategyState,
)
from trader.core_persistence import apply_checkpoint, restore_last_buy
from trader.core_trace_codec import (
    CORE_TRACE_SCHEMA_VERSION,
    DIGEST_GROUPS,
    TraceHeader,
    checkpoint_from_object,
    decode_event,
    decode_header_row,
    decode_plan,
    digest_state,
    encode_plan,
)
from trader.paths import CORE_TRACE_FILENAME
from trader.strict_json import StrictJsonError, require_int, require_object, require_str

# ponytail: keep 256 states; revert only rewinds one persist attempt
_REWIND_CAP = 256
_IDENTITY_KEYS = ("session_id", "match_id", "game", "execution_mode", "yes_token", "no_token")
_PLAN_FIELDS = ("keep", "moves", "cancels", "places", "block_reason")


@dataclass(frozen=True)
class TraceMismatch:
    seq: int
    now_ns: int
    event_kind: str
    field: str
    recorded: object
    replayed: object
    input_state_differed: bool
    recorded_plan: dict[str, object]
    replayed_plan: dict[str, object]


@dataclass(frozen=True)
class DigestCheck:
    """One state group of the replayed state against the digest the live core wrote."""

    group: str
    recorded: str
    replayed: str

    @property
    def matches(self) -> bool:
        return self.recorded == self.replayed


@dataclass(frozen=True)
class IdleSince:
    """`now_ns` of the event where this order was last seen leaving `live`."""

    order_id: str
    since_ns: int


@dataclass(frozen=True)
class TraceAction:
    """One recorded plan that placed or canceled something."""

    seq: int
    event_kind: str
    places: tuple[PlaceOrder, ...]
    cancels: tuple[CancelOrder, ...]


@dataclass(frozen=True)
class ReplayedState:
    """A trace replayed to its end: the state, when it stopped, and its digest evidence."""

    path: Path
    state: StrategyState
    policy: Follow300Policy
    now_ns: int
    events: int
    plan_mismatches: int
    digests: tuple[DigestCheck, ...]
    truncated_reason: str
    idle: tuple[IdleSince, ...]
    actions: tuple[TraceAction, ...]
    block_reasons: tuple[str, ...]


@dataclass(frozen=True)
class TraceParity:
    path: Path
    events: int
    plan_mismatches: int
    wake_mismatches: int
    digest_mismatches: int
    first_divergence: TraceMismatch | None
    first_digest: TraceMismatch | None
    truncated_reason: str


def _empty(header: TraceHeader) -> StrategyState:
    return empty_state(
        limits=header.limits,
        freshness=header.freshness,
        permissions=Permissions(
            halt=False,
            reduce_only=False,
            allow_buy=True,
            allow_sell=True,
            sell_unconfirmed=False,
        ),
        budget=Budget(
            cash_usdc=0.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        clock=GameClock(now_ns=0, game_second=0, paused=False, game_ended=False),
    )


def _policy_for(header: TraceHeader, *, use_current_policy: bool) -> Follow300Policy:
    if not use_current_policy:
        return header.policy
    cadence = read_engine_cadence()
    return follow300_policy(
        level_usdc=header.policy.level_usdc,
        debounce_ms=cadence.debounce_ms,
        fallback_timer_s=cadence.quoter_tick_s,
    )


def _remember(states: dict[int, StrategyState], seq: int, state: StrategyState) -> None:
    states[seq] = state
    if len(states) <= _REWIND_CAP:
        return
    oldest = min(states)
    del states[oldest]


def _mismatch(
    *,
    seq: int,
    now_ns: int,
    event_kind: str,
    field: str,
    recorded: object,
    replayed: object,
    input_state_differed: bool,
    recorded_plan: dict[str, object],
    replayed_plan: dict[str, object],
) -> TraceMismatch:
    return TraceMismatch(
        seq=seq,
        now_ns=now_ns,
        event_kind=event_kind,
        field=field,
        recorded=recorded,
        replayed=replayed,
        input_state_differed=input_state_differed,
        recorded_plan=recorded_plan,
        replayed_plan=replayed_plan,
    )


def _compare_plan(
    *,
    seq: int,
    now_ns: int,
    event_kind: str,
    recorded_encoded: dict[str, object],
    replayed_encoded: dict[str, object],
    recorded_wake: int,
    replayed_wake: int,
) -> TraceMismatch | None:
    for name in _PLAN_FIELDS:
        left = recorded_encoded[name]
        right = replayed_encoded[name]
        if left != right:
            return _mismatch(
                seq=seq,
                now_ns=now_ns,
                event_kind=event_kind,
                field=f"plan.{name}",
                recorded=left,
                replayed=right,
                input_state_differed=False,
                recorded_plan=recorded_encoded,
                replayed_plan=replayed_encoded,
            )
    if recorded_wake != replayed_wake:
        return _mismatch(
            seq=seq,
            now_ns=now_ns,
            event_kind=event_kind,
            field="next_wake_ns",
            recorded=recorded_wake,
            replayed=replayed_wake,
            input_state_differed=False,
            recorded_plan=recorded_encoded,
            replayed_plan=replayed_encoded,
        )
    return None


def _compare_digests(
    *,
    seq: int,
    now_ns: int,
    event_kind: str,
    recorded_encoded: dict[str, object],
    replayed_encoded: dict[str, object],
    recorded_digests: dict[str, object],
    replayed_digests: dict[str, str],
) -> TraceMismatch | None:
    for group in DIGEST_GROUPS:
        left = recorded_digests.get(group)
        right = replayed_digests[group]
        if left != right:
            return _mismatch(
                seq=seq,
                now_ns=now_ns,
                event_kind=event_kind,
                field=f"digest.{group}",
                recorded=left,
                replayed=right,
                input_state_differed=True,
                recorded_plan=recorded_encoded,
                replayed_plan=replayed_encoded,
            )
    return None


def _header_identity(header: TraceHeader) -> tuple[str, ...]:
    return tuple(getattr(header, key) for key in _IDENTITY_KEYS)


@dataclass
class _Replay:
    first: TraceHeader | None = None
    policy: Follow300Policy | None = None
    policy_sha: str = ""
    state: StrategyState | None = None
    states: dict[int, StrategyState] = field(default_factory=dict)
    events: int = 0
    plan_mismatches: int = 0
    wake_mismatches: int = 0
    digest_mismatches: int = 0
    first_divergence: TraceMismatch | None = None
    first_digest: TraceMismatch | None = None
    truncated_reason: str = ""
    last_now_ns: int = 0
    last_recorded_digests: dict[str, str] = field(default_factory=dict)
    last_replayed_digests: dict[str, str] = field(default_factory=dict)
    idle_since: dict[str, int] = field(default_factory=dict)
    idle_at: dict[int, dict[str, int]] = field(default_factory=dict)
    tail_limit: int = 0
    actions: deque[TraceAction] = field(default_factory=deque)
    block_reasons: deque[str] = field(default_factory=deque)


def _sync_idle(replay: _Replay, now_ns: int) -> None:
    """Remember when each order left `live`. A return to `live` clears the mark."""
    state = replay.state
    if state is None:
        return
    present = {order.order_id for order in state.orders}
    idle = {order_id: since for order_id, since in replay.idle_since.items() if order_id in present}
    for order in state.orders:
        if order.status == "live":
            idle.pop(order.order_id, None)
            continue
        idle.setdefault(order.order_id, now_ns)
    replay.idle_since = idle


def _keep(replay: _Replay, seq: int) -> None:
    if replay.state is None:
        raise StrictJsonError("trace is missing a header")
    _remember(replay.states, seq, replay.state)
    replay.idle_at[seq] = dict(replay.idle_since)
    if len(replay.idle_at) <= _REWIND_CAP:
        return
    del replay.idle_at[min(replay.idle_at)]


def _note_tail(replay: _Replay, seq: int, event_kind: str, plan: Plan) -> None:
    if replay.tail_limit <= 0:
        return
    if plan.places or plan.cancels:
        replay.actions.append(
            TraceAction(
                seq=seq,
                event_kind=event_kind,
                places=plan.places,
                cancels=plan.cancels,
            )
        )
    reason = plan.block_reason
    if reason and (not replay.block_reasons or replay.block_reasons[-1] != reason):
        replay.block_reasons.append(reason)


def _need(replay: _Replay) -> tuple[Follow300Policy, StrategyState]:
    if replay.first is None or replay.policy is None or replay.state is None:
        raise StrictJsonError("trace is missing a header")
    return replay.policy, replay.state


def _on_header(
    replay: _Replay, fields: dict[str, object], *, seq: int, use_current_policy: bool
) -> None:
    header = decode_header_row(fields)
    schema_version = require_int(fields, "schema_version", "header")
    if replay.first is None:
        if schema_version != CORE_TRACE_SCHEMA_VERSION:
            raise StrictJsonError(
                f"trace schema_version {schema_version} != {CORE_TRACE_SCHEMA_VERSION}"
            )
        replay.first = header
        replay.policy = _policy_for(header, use_current_policy=use_current_policy)
        replay.policy_sha = header.policy_sha
        replay.state = _empty(header)
        _keep(replay, seq)
        return
    if _header_identity(header) != _header_identity(replay.first):
        raise StrictJsonError(
            f"header identity changed at seq={seq}: "
            f"{_header_identity(header)} != {_header_identity(replay.first)}"
        )
    new_sha = require_str(fields, "policy_sha", "header")
    if new_sha != replay.policy_sha and not use_current_policy:
        replay.policy = header.policy
        replay.policy_sha = new_sha


def _on_reset(replay: _Replay, fields: dict[str, object], *, seq: int, label: str) -> None:
    policy, state = _need(replay)
    now_ns = require_int(fields, "now_ns", label)
    now_wall_s = fields.get("now_wall_s")
    if type(now_wall_s) is not float and type(now_wall_s) is not int:
        raise StrictJsonError("reset now_wall_s must be a number")
    checkpoint = checkpoint_from_object(
        require_object(fields.get("checkpoint"), f"{label} checkpoint")
    )
    replay.state = apply_checkpoint(
        state,
        checkpoint=checkpoint,
        now_ns=now_ns,
        now_wall_s=float(now_wall_s),
        sell_min_life_s=policy.sell_min_life_s,
    )
    replay.last_now_ns = now_ns
    _sync_idle(replay, now_ns)
    _keep(replay, seq)


def _on_last_buy(replay: _Replay, fields: dict[str, object], *, seq: int, label: str) -> None:
    _policy, state = _need(replay)
    token_index = require_int(fields, "token_index", label)
    last_buy_ns = require_int(fields, "last_buy_ns", label)
    replay.state = restore_last_buy(state, last_buy_ns=last_buy_ns, token_index=token_index)
    _keep(replay, seq)


def _rewind(replay: _Replay, *, to_seq: int, seq: int) -> None:
    restored = replay.states.get(to_seq)
    idle = replay.idle_at.get(to_seq)
    if restored is None or idle is None:
        raise StrictJsonError(f"revert at seq={seq} points at unknown to_seq={to_seq}")
    replay.state = restored
    replay.idle_since = dict(idle)
    _keep(replay, seq)


def _on_revert(replay: _Replay, fields: dict[str, object], *, seq: int, label: str) -> None:
    _need(replay)
    _rewind(replay, to_seq=require_int(fields, "to_seq", label), seq=seq)


def _on_event(replay: _Replay, fields: dict[str, object], *, seq: int, label: str) -> None:
    policy, state = _need(replay)
    now_ns = require_int(fields, "now_ns", label)
    event = decode_event(require_object(fields.get("event"), f"{label} event"))
    recorded_plan = decode_plan(require_object(fields.get("plan"), f"{label} plan"))
    recorded_wake = require_int(fields, "next_wake_ns", label)
    recorded_digests = require_object(fields.get("digests"), f"{label} digests")
    out = step(state=state, policy=policy, event=event)
    event_kind = type(event).__name__
    recorded_encoded = encode_plan(recorded_plan)
    replayed_encoded = encode_plan(out.plan)
    replay.events += 1
    divergence = _compare_plan(
        seq=seq,
        now_ns=now_ns,
        event_kind=event_kind,
        recorded_encoded=recorded_encoded,
        replayed_encoded=replayed_encoded,
        recorded_wake=recorded_wake,
        replayed_wake=out.next_wake_ns,
    )
    if divergence is not None:
        if divergence.field == "next_wake_ns":
            replay.wake_mismatches += 1
        else:
            replay.plan_mismatches += 1
        if replay.first_divergence is None:
            replay.first_divergence = divergence
    digest = _compare_digests(
        seq=seq,
        now_ns=now_ns,
        event_kind=event_kind,
        recorded_encoded=recorded_encoded,
        replayed_encoded=replayed_encoded,
        recorded_digests=recorded_digests,
        replayed_digests=digest_state(out.state),
    )
    if digest is not None:
        replay.digest_mismatches += 1
        if replay.first_digest is None:
            replay.first_digest = digest
    replay.last_now_ns = now_ns
    replay.last_recorded_digests = {group: str(recorded_digests[group]) for group in DIGEST_GROUPS}
    replay.last_replayed_digests = digest_state(out.state)
    replay.state = out.state
    _sync_idle(replay, now_ns)
    _keep(replay, seq)
    _note_tail(replay, seq, event_kind, recorded_plan)


def _parity(replay: _Replay, path: Path, truncated_reason: str) -> TraceParity:
    return TraceParity(
        path=path,
        events=replay.events,
        plan_mismatches=replay.plan_mismatches,
        wake_mismatches=replay.wake_mismatches,
        digest_mismatches=replay.digest_mismatches,
        first_divergence=replay.first_divergence,
        first_digest=replay.first_digest,
        truncated_reason=truncated_reason,
    )


def _drive(path: Path, *, use_current_policy: bool, tail: int) -> _Replay:
    replay = _Replay(
        tail_limit=tail,
        actions=deque(maxlen=max(tail, 0)),
        block_reasons=deque(maxlen=max(tail, 0)),
    )
    with open_maybe_gz(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            fields = require_object(json.loads(line), f"row {line_number}")
            kind = require_str(fields, "kind", f"row {line_number}")
            seq = require_int(fields, "seq", f"row {line_number}")
            label = f"row {line_number}"
            if kind == "header":
                _on_header(replay, fields, seq=seq, use_current_policy=use_current_policy)
            elif kind == "truncated":
                replay.truncated_reason = require_str(fields, "reason", label)
                return replay
            elif kind == "reset":
                _on_reset(replay, fields, seq=seq, label=label)
            elif kind == "last_buy":
                _on_last_buy(replay, fields, seq=seq, label=label)
            elif kind == "revert":
                _on_revert(replay, fields, seq=seq, label=label)
            elif kind == "event":
                _on_event(replay, fields, seq=seq, label=label)
            else:
                raise StrictJsonError(f"unknown row kind {kind!r}")
    return replay


def replay_trace(path: Path, *, use_current_policy: bool) -> TraceParity:
    replay = _drive(path, use_current_policy=use_current_policy, tail=0)
    return _parity(replay, path, replay.truncated_reason)


def replay_state(path: Path, *, use_current_policy: bool, tail: int) -> ReplayedState:
    """The state a trace ends in, with the per-group evidence that it is the recorded one.

    A single mismatch count hides which part drifted. A group that matches means
    the fields this state prints are the ones the live core held, byte for byte;
    a group that differs is usually a struct the code changed after the trace was
    written. `idle` is when each order left `live`. `actions` and `block_reasons`
    are the last `tail` recorded rows, newest first.
    """
    replay = _drive(path, use_current_policy=use_current_policy, tail=tail)
    policy, state = _need(replay)
    digests = tuple(
        DigestCheck(
            group=group,
            recorded=replay.last_recorded_digests.get(group, ""),
            replayed=replay.last_replayed_digests.get(group, ""),
        )
        for group in DIGEST_GROUPS
    )
    return ReplayedState(
        path=path,
        state=state,
        policy=policy,
        now_ns=replay.last_now_ns,
        events=replay.events,
        plan_mismatches=replay.plan_mismatches,
        digests=digests,
        truncated_reason=replay.truncated_reason,
        idle=tuple(
            IdleSince(order_id=order_id, since_ns=since_ns)
            for order_id, since_ns in replay.idle_since.items()
        ),
        actions=tuple(reversed(replay.actions)),
        block_reasons=tuple(reversed(replay.block_reasons)),
    )


def _print_mismatch(mismatch: TraceMismatch) -> None:
    print(
        f"seq={mismatch.seq} now_ns={mismatch.now_ns} event={mismatch.event_kind} "
        f"field={mismatch.field} input_state_differed={mismatch.input_state_differed}"
    )
    print(f"recorded={mismatch.recorded!r}")
    print(f"replayed={mismatch.replayed!r}")
    print(f"recorded_plan={mismatch.recorded_plan}")
    print(f"replayed_plan={mismatch.replayed_plan}")


def _summary_line(parity: TraceParity) -> str:
    name = parity.path.parent.name
    first = parity.first_divergence
    tail = "" if first is None else f"  first={first.field}@seq={first.seq}"
    if parity.truncated_reason:
        tail = f"{tail}  truncated={parity.truncated_reason}"
    return (
        f"{name:<18} events={parity.events:>6} plan={parity.plan_mismatches:>4} "
        f"wake={parity.wake_mismatches:>4} digest={parity.digest_mismatches:>6}{tail}"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="replay_core_trace")
    parser.add_argument("--archive", type=Path, nargs="+", required=True)
    parser.add_argument("--policy", choices=("archive", "current"), default="archive")
    args = parser.parse_args(argv)
    use_current_policy = args.policy == "current"
    results = [
        replay_trace(directory / CORE_TRACE_FILENAME, use_current_policy=use_current_policy)
        for directory in args.archive
    ]
    for parity in results:
        print(_summary_line(parity))
    plan_total = sum(parity.plan_mismatches for parity in results)
    wake_total = sum(parity.wake_mismatches for parity in results)
    failed = plan_total + wake_total > 0
    print(
        f"TOTAL {len(results)} archives  plan={plan_total} wake={wake_total}  "
        f"{'FAIL' if failed else 'OK'}"
    )
    for parity in results:
        if parity.first_divergence is None:
            continue
        print(f"--- {parity.path.parent.name}")
        _print_mismatch(parity.first_divergence)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
