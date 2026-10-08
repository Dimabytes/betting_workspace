"""JSON codec for core_trace.jsonl rows. Schema only; the writer lives in core_trace."""

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from typing import cast, get_args

from shared.constants.strategy import LATCH_REANCHOR_OFF_S
from strategy.policy import Follow300Policy
from strategy.types import (
    BlockReason,
    BookPair,
    BookUpdate,
    Budget,
    BudgetUpdate,
    BuySettled,
    CancelAck,
    CancelOrder,
    CancelTimeout,
    CancelUnsettled,
    ClockUpdate,
    Fill,
    FreshnessLimits,
    GameClock,
    InboundEvent,
    KeepOrder,
    KillGate,
    KillGateUpdate,
    KillWait,
    Latch,
    LimitsUpdate,
    MarketLimits,
    Merged,
    MoveOrder,
    OrderAccepted,
    OrderRejected,
    OwnershipResolved,
    Permissions,
    PermissionsUpdate,
    PlaceOrder,
    Plan,
    RawDeltaSignal,
    Recovery,
    RecoveryVerified,
    SettlementKind,
    SettlementStatus,
    Side,
    SignalUpdate,
    StrategyState,
    SubmitTimeout,
    TokenBook,
    TokenInventory,
    Wake,
)
from trader.core_persistence import StructuralCheckpoint, decode_checkpoint, encode_checkpoint
from trader.strict_json import (
    StrictJsonError,
    require_bool,
    require_exact_keys,
    require_int,
    require_list,
    require_nullable_int,
    require_number,
    require_object,
    require_str,
    require_str_list,
)

CORE_TRACE_SCHEMA_VERSION = 1
DIGEST_LEN = 12
_EVENT_TYPE_KEY = "type"
DIGEST_GROUPS = (
    "orders",
    "rungs",
    "inventory",
    "episode",
    "schedule",
    "latch",
    "mid_spike",
    "kill_gate",
)
_BLOCK_REASONS: frozenset[str] = frozenset(get_args(BlockReason))

_TOKEN_BOOK_KEYS = frozenset({"token_index", "bid", "ask", "bid_size", "ask_size", "ts_ns"})
_SIGNAL_KEYS = frozenset(
    {
        "predicted_delta",
        "received_ns",
        "source_received_ns",
        "anchor_p",
        "deaths_radiant",
        "deaths_dire",
    }
)
_CLOCK_KEYS = frozenset({"now_ns", "game_second", "paused", "game_ended"})
_LIMITS_KEYS = frozenset(
    {"min_order_size", "tick_size", "pair_sum_tolerance", "radiant_token_index"}
)
_FRESHNESS_KEYS = frozenset({"book_stale_s", "entry_stale_s", "exit_stale_s"})
_PERM_KEYS = frozenset({"halt", "reduce_only", "allow_buy", "allow_sell", "sell_unconfirmed"})
_BUDGET_KEYS = frozenset({"cash_usdc", "cap_room_usdc"})
_BUDGET_WITH_ACCOUNT = _BUDGET_KEYS | {"account_cap_room_usdc"}
_BUDGET_LEGACY_KEYS = frozenset({"available_usdc"})
_INVENTORY_KEYS = frozenset({"token_index", "qty", "cost_basis", "last_buy_ns"})
_POLICY_KEYS = frozenset(
    {
        "version",
        "level_count",
        "step_ticks",
        "tick_size",
        "level_usdc",
        "min_abs_delta",
        "exit_abs_delta",
        "min_entry_price",
        "max_entry_price",
        "max_entry_spread_ticks",
        "buy_cutoff_second",
        "exit_settle_s",
        "debounce_ms",
        "fallback_timer_s",
        "sell_min_life_s",
        "hold_unconfirmed_sell",
        "mid_spike_lookback_s",
        "mid_spike_threshold",
        "mid_spike_cooloff_s",
        "latch_reanchor_s",
    }
)
_KEEP_KEYS = frozenset({"order_id", "level_index"})
_MOVE_KEYS = frozenset({"order_id", "from_level", "to_level"})
_CANCEL_KEYS = frozenset({"order_id", "reason"})
_PLACE_KEYS = frozenset(
    {
        "order_id",
        "episode_id",
        "token_index",
        "side",
        "price",
        "quantity",
        "level_index",
        "reduce_only",
    }
)
_PLAN_KEYS = frozenset({"keep", "moves", "cancels", "places", "block_reason"})
_MODEL_KEYS = frozenset({"name", "trained_at", "sha256"})
_HEADER_KEYS = frozenset(
    {
        "kind",
        "seq",
        "schema_version",
        "session_id",
        "match_id",
        "game",
        "execution_mode",
        "git_commit",
        "policy",
        "policy_sha",
        "limits",
        "freshness",
        "model",
        "yes_token",
        "no_token",
        "opened_wall_s",
        "opened_now_ns",
    }
)


@dataclass(frozen=True)
class TraceHeader:
    session_id: str
    match_id: str
    game: str
    execution_mode: str
    git_commit: str
    policy: Follow300Policy
    policy_sha: str
    limits: MarketLimits
    freshness: FreshnessLimits
    model_name: str
    model_trained_at: str
    model_sha256: str
    yes_token: str
    no_token: str


def jsonable(value: object) -> object:
    if value is None or type(value) is str or type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        # NaN and infinity are not JSON. `dumps_trace` and `_canonical` both run with
        # allow_nan=False, so leaving one here raises ValueError and kills the writer
        # for the rest of the map.
        return value if math.isfinite(value) else None
    if isinstance(value, tuple):
        items = cast(tuple[object, ...], value)
        return [jsonable(item) for item in items]
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: jsonable(getattr(value, item.name)) for item in fields(value)}
    raise TypeError(f"cannot encode {type(value).__name__}")


def dumps_trace(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _canonical(payload: object) -> str:
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False, sort_keys=True
    )


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()[:DIGEST_LEN]


def policy_sha(policy: Follow300Policy, limits: MarketLimits, freshness: FreshnessLimits) -> str:
    payload = {
        "policy": jsonable(policy),
        "limits": jsonable(limits),
        "freshness": jsonable(freshness),
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _latch_payload(latch: Latch | None) -> object:
    payload = jsonable(latch)
    if payload is None:
        return None
    fields = cast(dict[str, object], payload)
    del fields["anchored_ns"]
    return fields


def digest_state(state: StrategyState) -> dict[str, str]:
    episode = {
        "episode_id": state.episode_id,
        "episode_counter": state.episode_counter,
        "episode_token_index": state.episode_token_index,
        "has_buy_fill": state.has_buy_fill,
        "episode_buy_notional": state.episode_buy_notional,
        "winding_down": state.winding_down,
        "sell_only": state.sell_only,
        "recovery_pending": state.recovery_pending,
        "recovery_generation": state.recovery_generation,
        "next_order_seq": state.next_order_seq,
        "pending_ownership": jsonable(state.pending_ownership),
        "unconfirmed_keys": jsonable(state.unconfirmed_keys),
        "seen_fill_ids": jsonable(state.seen_fill_ids),
        "archives": jsonable(state.archives),
    }
    return {
        "orders": _digest(jsonable(state.orders)),
        "rungs": _digest(jsonable(state.rungs)),
        "inventory": _digest(jsonable(state.inventory)),
        "episode": _digest(episode),
        "schedule": _digest(jsonable(state.schedule)),
        # v5 traces hash the latch without anchored_ns; strip it so re-anchor-on
        # replay of an archived trace still digests the same recorded fields.
        "latch": _digest(_latch_payload(state.latch)),
        "mid_spike": _digest(jsonable(state.mid_spike)),
        "kill_gate": _digest(jsonable(state.kill_gate)),
    }


def encode_event(event: InboundEvent) -> dict[str, object]:
    encoded = jsonable(event)
    if type(encoded) is not dict:
        raise TypeError(f"event {type(event).__name__} did not encode as an object")
    return {"type": type(event).__name__, **cast(dict[str, object], encoded)}


def encode_plan(plan: Plan) -> dict[str, object]:
    encoded = jsonable(plan)
    return cast(dict[str, object], encoded)


def _side(fields: Mapping[str, object], key: str, label: str) -> Side:
    value = require_str(fields, key, label)
    if value != "BUY" and value != "SELL":
        raise StrictJsonError(f"{label} {key!r} must be BUY or SELL")
    return value


def _block_reason(fields: Mapping[str, object], key: str, label: str) -> BlockReason:
    value = require_str(fields, key, label)
    if value not in _BLOCK_REASONS:
        raise StrictJsonError(f"{label} {key!r} is not a block reason")
    return cast(BlockReason, value)


def decode_token_book(fields: Mapping[str, object], label: str) -> TokenBook:
    merged = dict(fields)
    # Lonely-L0 was the only reader. Old traces still carry the ladder.
    merged.pop("bid_levels", None)
    require_exact_keys(merged, _TOKEN_BOOK_KEYS, label)
    return TokenBook(
        token_index=require_int(merged, "token_index", label),
        bid=require_number(merged, "bid", label),
        ask=require_number(merged, "ask", label),
        bid_size=require_number(merged, "bid_size", label),
        ask_size=require_number(merged, "ask_size", label),
        ts_ns=require_int(merged, "ts_ns", label),
    )


def decode_book_pair(value: object, label: str) -> BookPair | None:
    if value is None:
        return None
    fields = require_object(value, label)
    require_exact_keys(fields, frozenset({"tokens"}), label)
    tokens = require_list(fields, "tokens", label)
    if len(tokens) != 2:
        raise StrictJsonError(f"{label} tokens must have length 2")
    left = decode_token_book(require_object(tokens[0], f"{label} tokens[0]"), f"{label} tokens[0]")
    right = decode_token_book(require_object(tokens[1], f"{label} tokens[1]"), f"{label} tokens[1]")
    return BookPair(tokens=(left, right))


def decode_signal(value: object, label: str) -> RawDeltaSignal | None:
    if value is None:
        return None
    fields = require_object(value, label)
    dropped = frozenset({"nw_delta_30"})
    merged = dict(fields)
    # Traces from before the kill gate omit the death counters; zero reads as
    # "no deaths seen", which is what those replays assumed.
    merged.setdefault("deaths_radiant", 0)
    merged.setdefault("deaths_dire", 0)
    merged.setdefault("source_received_ns", merged.get("received_ns"))
    keys = frozenset(merged) - dropped
    if keys != _SIGNAL_KEYS:
        raise StrictJsonError(f"{label} keys do not match the expected set")
    return RawDeltaSignal(
        predicted_delta=require_number(merged, "predicted_delta", label),
        source_received_ns=require_int(merged, "source_received_ns", label),
        received_ns=require_int(merged, "received_ns", label),
        anchor_p=require_number(merged, "anchor_p", label),
        deaths_radiant=require_int(merged, "deaths_radiant", label),
        deaths_dire=require_int(merged, "deaths_dire", label),
    )


def decode_clock(fields: Mapping[str, object], label: str) -> GameClock:
    require_exact_keys(fields, _CLOCK_KEYS, label)
    return GameClock(
        now_ns=require_int(fields, "now_ns", label),
        game_second=require_int(fields, "game_second", label),
        paused=require_bool(fields, "paused", label),
        game_ended=require_bool(fields, "game_ended", label),
    )


def decode_limits(fields: Mapping[str, object], label: str) -> MarketLimits:
    require_exact_keys(fields, _LIMITS_KEYS, label)
    return MarketLimits(
        min_order_size=require_number(fields, "min_order_size", label),
        tick_size=require_number(fields, "tick_size", label),
        pair_sum_tolerance=require_number(fields, "pair_sum_tolerance", label),
        radiant_token_index=require_int(fields, "radiant_token_index", label),
    )


def decode_freshness(fields: Mapping[str, object], label: str) -> FreshnessLimits:
    require_exact_keys(fields, _FRESHNESS_KEYS, label)
    return FreshnessLimits(
        book_stale_s=require_number(fields, "book_stale_s", label),
        entry_stale_s=require_number(fields, "entry_stale_s", label),
        exit_stale_s=require_number(fields, "exit_stale_s", label),
    )


def decode_permissions(fields: Mapping[str, object], label: str) -> Permissions:
    require_exact_keys(fields, _PERM_KEYS, label)
    return Permissions(
        halt=require_bool(fields, "halt", label),
        reduce_only=require_bool(fields, "reduce_only", label),
        allow_buy=require_bool(fields, "allow_buy", label),
        allow_sell=require_bool(fields, "allow_sell", label),
        sell_unconfirmed=require_bool(fields, "sell_unconfirmed", label),
    )


def decode_budget(fields: Mapping[str, object], label: str) -> Budget:
    if frozenset(fields) == _BUDGET_LEGACY_KEYS:
        # Pre-v7 traces: a single spendable number, no cap leg.
        return Budget(
            cash_usdc=require_number(fields, "available_usdc", label),
            cap_room_usdc=float("inf"),
            account_cap_room_usdc=float("inf"),
        )
    keys = _BUDGET_WITH_ACCOUNT if "account_cap_room_usdc" in fields else _BUDGET_KEYS
    require_exact_keys(fields, keys, label)
    cap_room = fields["cap_room_usdc"]
    account_room = fields.get("account_cap_room_usdc")
    return Budget(
        cash_usdc=require_number(fields, "cash_usdc", label),
        cap_room_usdc=(
            require_number(fields, "cap_room_usdc", label) if cap_room is not None else float("inf")
        ),
        account_cap_room_usdc=(
            float("inf")
            if account_room is None
            else require_number(fields, "account_cap_room_usdc", label)
        ),
    )


def decode_inventory(fields: Mapping[str, object], label: str) -> TokenInventory:
    require_exact_keys(fields, _INVENTORY_KEYS, label)
    return TokenInventory(
        token_index=require_int(fields, "token_index", label),
        qty=require_number(fields, "qty", label),
        cost_basis=require_number(fields, "cost_basis", label),
        last_buy_ns=require_nullable_int(fields, "last_buy_ns", label),
    )


def decode_policy(fields: Mapping[str, object], label: str) -> Follow300Policy:
    dropped = frozenset(
        {
            "kill_gate_directional",
            "aged_sell",
            "sell_full_age_s",
            "sell_ask_age_s",
            "adverse_taker",
            "max_abs_nw_delta_30",
            "nw_window_s",
            "nw_asof_max_age_s",
            # v5 live recorded lonely_l0_s=3. Replay opens, then diverges at the first lonely cancel.
            "lonely_l0_s",
        }
    )
    merged = dict(fields)
    # Traces from before mid_spike omit these keys. Default them OFF so archive
    # replay matches the decisions that were recorded, not today's constants.
    merged.setdefault("mid_spike_lookback_s", 0.0)
    merged.setdefault("mid_spike_threshold", 1e9)
    merged.setdefault("mid_spike_cooloff_s", 0.0)
    # Traces from before the BUY ceiling omit this key. Default it OFF so archive
    # replay matches the decisions that were recorded, not today's constant.
    merged.setdefault("max_entry_price", 1.0)
    # Traces from before the delta Schmitt gate omit this key. Their entry floor
    # doubled as the exit floor, so defaulting to min keeps replay identical.
    merged.setdefault("exit_abs_delta", merged.get("min_abs_delta"))
    # Traces from before latch re-anchor omit this key. Off keeps archive replay
    # on the cadence that was recorded.
    merged.setdefault("latch_reanchor_s", LATCH_REANCHOR_OFF_S)
    keys = frozenset(merged) - dropped
    if keys != _POLICY_KEYS:
        raise StrictJsonError(f"{label} keys do not match the expected set")
    return Follow300Policy(
        version=require_str(merged, "version", label),
        level_count=require_int(merged, "level_count", label),
        step_ticks=require_int(merged, "step_ticks", label),
        tick_size=require_number(merged, "tick_size", label),
        level_usdc=require_number(merged, "level_usdc", label),
        min_abs_delta=require_number(merged, "min_abs_delta", label),
        exit_abs_delta=require_number(merged, "exit_abs_delta", label),
        min_entry_price=require_number(merged, "min_entry_price", label),
        max_entry_price=require_number(merged, "max_entry_price", label),
        max_entry_spread_ticks=require_int(merged, "max_entry_spread_ticks", label),
        buy_cutoff_second=require_int(merged, "buy_cutoff_second", label),
        exit_settle_s=require_number(merged, "exit_settle_s", label),
        debounce_ms=require_int(merged, "debounce_ms", label),
        fallback_timer_s=require_number(merged, "fallback_timer_s", label),
        sell_min_life_s=require_number(merged, "sell_min_life_s", label),
        hold_unconfirmed_sell=require_bool(merged, "hold_unconfirmed_sell", label),
        mid_spike_lookback_s=require_number(merged, "mid_spike_lookback_s", label),
        mid_spike_threshold=require_number(merged, "mid_spike_threshold", label),
        mid_spike_cooloff_s=require_number(merged, "mid_spike_cooloff_s", label),
        latch_reanchor_s=require_number(merged, "latch_reanchor_s", label),
    )


def _object_field(fields: Mapping[str, object], key: str, label: str) -> dict[str, object]:
    return require_object(fields.get(key), f"{label} {key}")


def _decode_book_update(fields: dict[str, object]) -> BookUpdate:
    label = "BookUpdate"
    require_exact_keys(fields, frozenset({"now_ns", "books"}), label)
    return BookUpdate(
        now_ns=require_int(fields, "now_ns", label),
        books=decode_book_pair(fields.get("books"), f"{label} books"),
    )


def _decode_signal_update(fields: dict[str, object]) -> SignalUpdate:
    label = "SignalUpdate"
    require_exact_keys(fields, frozenset({"now_ns", "signal"}), label)
    return SignalUpdate(
        now_ns=require_int(fields, "now_ns", label),
        signal=decode_signal(fields.get("signal"), f"{label} signal"),
    )


def _decode_kill_wait(value: object, label: str) -> KillWait:
    fields = require_object(value, label)
    require_exact_keys(fields, frozenset({"awaited_deaths", "until_ns"}), label)
    return KillWait(
        awaited_deaths=require_int(fields, "awaited_deaths", label),
        until_ns=require_int(fields, "until_ns", label),
    )


def _decode_kill_gate_update(fields: dict[str, object]) -> KillGateUpdate:
    label = "KillGateUpdate"
    require_exact_keys(fields, frozenset({"now_ns", "gate"}), label)
    gate = _object_field(fields, "gate", label)
    require_exact_keys(gate, frozenset({"radiant", "dire"}), f"{label} gate")
    return KillGateUpdate(
        now_ns=require_int(fields, "now_ns", label),
        gate=KillGate(
            radiant=_decode_kill_wait(gate.get("radiant"), f"{label} gate radiant"),
            dire=_decode_kill_wait(gate.get("dire"), f"{label} gate dire"),
        ),
    )


def _decode_clock_update(fields: dict[str, object]) -> ClockUpdate:
    label = "ClockUpdate"
    require_exact_keys(fields, frozenset({"now_ns", "clock"}), label)
    return ClockUpdate(
        now_ns=require_int(fields, "now_ns", label),
        clock=decode_clock(_object_field(fields, "clock", label), f"{label} clock"),
    )


def _decode_permissions_update(fields: dict[str, object]) -> PermissionsUpdate:
    label = "PermissionsUpdate"
    require_exact_keys(fields, frozenset({"now_ns", "permissions"}), label)
    return PermissionsUpdate(
        now_ns=require_int(fields, "now_ns", label),
        permissions=decode_permissions(
            _object_field(fields, "permissions", label), f"{label} permissions"
        ),
    )


def _decode_budget_update(fields: dict[str, object]) -> BudgetUpdate:
    label = "BudgetUpdate"
    require_exact_keys(fields, frozenset({"now_ns", "budget"}), label)
    return BudgetUpdate(
        now_ns=require_int(fields, "now_ns", label),
        budget=decode_budget(_object_field(fields, "budget", label), f"{label} budget"),
    )


def _decode_limits_update(fields: dict[str, object]) -> LimitsUpdate:
    label = "LimitsUpdate"
    require_exact_keys(fields, frozenset({"now_ns", "limits"}), label)
    return LimitsUpdate(
        now_ns=require_int(fields, "now_ns", label),
        limits=decode_limits(_object_field(fields, "limits", label), f"{label} limits"),
    )


def _decode_wake(fields: dict[str, object]) -> Wake:
    label = "Wake"
    require_exact_keys(fields, frozenset({"now_ns", "forced"}), label)
    return Wake(
        now_ns=require_int(fields, "now_ns", label), forced=require_bool(fields, "forced", label)
    )


def _decode_fill(fields: dict[str, object]) -> Fill:
    label = "Fill"
    require_exact_keys(
        fields,
        frozenset({"now_ns", "fill_id", "order_id", "qty", "price", "token_index", "side"}),
        label,
    )
    return Fill(
        now_ns=require_int(fields, "now_ns", label),
        fill_id=require_str(fields, "fill_id", label),
        order_id=require_str(fields, "order_id", label),
        qty=require_number(fields, "qty", label),
        price=require_number(fields, "price", label),
        token_index=require_int(fields, "token_index", label),
        side=_side(fields, "side", label),
    )


def _decode_accepted(fields: dict[str, object]) -> OrderAccepted:
    label = "OrderAccepted"
    require_exact_keys(fields, frozenset({"now_ns", "order_id"}), label)
    return OrderAccepted(
        now_ns=require_int(fields, "now_ns", label), order_id=require_str(fields, "order_id", label)
    )


def _decode_rejected(fields: dict[str, object]) -> OrderRejected:
    label = "OrderRejected"
    require_exact_keys(fields, frozenset({"now_ns", "order_id", "reason"}), label)
    return OrderRejected(
        now_ns=require_int(fields, "now_ns", label),
        order_id=require_str(fields, "order_id", label),
        reason=require_str(fields, "reason", label),
    )


def _decode_submit_timeout(fields: dict[str, object]) -> SubmitTimeout:
    label = "SubmitTimeout"
    require_exact_keys(fields, frozenset({"now_ns", "order_id"}), label)
    return SubmitTimeout(
        now_ns=require_int(fields, "now_ns", label), order_id=require_str(fields, "order_id", label)
    )


def _decode_cancel_ack(fields: dict[str, object]) -> CancelAck:
    label = "CancelAck"
    require_exact_keys(fields, frozenset({"now_ns", "order_id"}), label)
    return CancelAck(
        now_ns=require_int(fields, "now_ns", label), order_id=require_str(fields, "order_id", label)
    )


def _decode_cancel_timeout(fields: dict[str, object]) -> CancelTimeout:
    label = "CancelTimeout"
    require_exact_keys(fields, frozenset({"now_ns", "order_id"}), label)
    return CancelTimeout(
        now_ns=require_int(fields, "now_ns", label), order_id=require_str(fields, "order_id", label)
    )


def _decode_cancel_unsettled(fields: dict[str, object]) -> CancelUnsettled:
    label = "CancelUnsettled"
    require_exact_keys(fields, frozenset({"now_ns", "order_id"}), label)
    return CancelUnsettled(
        now_ns=require_int(fields, "now_ns", label), order_id=require_str(fields, "order_id", label)
    )


def _decode_buy_settled(fields: dict[str, object]) -> BuySettled:
    label = "BuySettled"
    require_exact_keys(fields, frozenset({"now_ns", "order_id", "matched_qty"}), label)
    return BuySettled(
        now_ns=require_int(fields, "now_ns", label),
        order_id=require_str(fields, "order_id", label),
        matched_qty=require_number(fields, "matched_qty", label),
    )


def _decode_recovery(fields: dict[str, object]) -> Recovery:
    label = "Recovery"
    leftover = dict(fields)
    leftover.pop("restored_sell_ids", None)
    require_exact_keys(leftover, frozenset({"now_ns", "restored_buy_ids"}), label)
    return Recovery(
        now_ns=require_int(fields, "now_ns", label),
        restored_buy_ids=tuple(require_str_list(fields, "restored_buy_ids", label)),
    )


def _decode_recovery_verified(fields: dict[str, object]) -> RecoveryVerified:
    label = "RecoveryVerified"
    require_exact_keys(fields, frozenset({"now_ns", "generation", "inventory"}), label)
    items = require_list(fields, "inventory", label)
    if len(items) != 2:
        raise StrictJsonError(f"{label} inventory must have length 2")
    left = decode_inventory(
        require_object(items[0], f"{label} inventory[0]"), f"{label} inventory[0]"
    )
    right = decode_inventory(
        require_object(items[1], f"{label} inventory[1]"), f"{label} inventory[1]"
    )
    return RecoveryVerified(
        now_ns=require_int(fields, "now_ns", label),
        generation=require_int(fields, "generation", label),
        inventory=(left, right),
    )


def _decode_ownership(fields: dict[str, object]) -> OwnershipResolved:
    label = "OwnershipResolved"
    require_exact_keys(
        fields,
        frozenset(
            {
                "now_ns",
                "order_id",
                "episode_id",
                "token_index",
                "side",
                "price",
                "submitted_qty",
                "filled_qty",
                "level_index",
                "terminal",
            }
        ),
        label,
    )
    return OwnershipResolved(
        now_ns=require_int(fields, "now_ns", label),
        order_id=require_str(fields, "order_id", label),
        episode_id=require_int(fields, "episode_id", label),
        token_index=require_int(fields, "token_index", label),
        side=_side(fields, "side", label),
        price=require_number(fields, "price", label),
        submitted_qty=require_number(fields, "submitted_qty", label),
        filled_qty=require_number(fields, "filled_qty", label),
        level_index=require_nullable_int(fields, "level_index", label),
        terminal=require_bool(fields, "terminal", label),
    )


def _decode_settlement(fields: dict[str, object]) -> SettlementStatus:
    label = "SettlementStatus"
    require_exact_keys(fields, frozenset({"now_ns", "fill_id", "status"}), label)
    status = require_str(fields, "status", label)
    if status != "matched" and status != "confirmed" and status != "failed":
        raise StrictJsonError(f"{label} status is not a settlement kind")
    kind: SettlementKind = status
    return SettlementStatus(
        now_ns=require_int(fields, "now_ns", label),
        fill_id=require_str(fields, "fill_id", label),
        status=kind,
    )


def _decode_merged(fields: dict[str, object]) -> Merged:
    label = "Merged"
    require_exact_keys(fields, frozenset({"now_ns", "token_index", "qty"}), label)
    return Merged(
        now_ns=require_int(fields, "now_ns", label),
        token_index=require_int(fields, "token_index", label),
        qty=require_number(fields, "qty", label),
    )


EVENT_DECODERS: dict[str, Callable[[dict[str, object]], InboundEvent]] = {
    "BookUpdate": _decode_book_update,
    "SignalUpdate": _decode_signal_update,
    "KillGateUpdate": _decode_kill_gate_update,
    "ClockUpdate": _decode_clock_update,
    "PermissionsUpdate": _decode_permissions_update,
    "BudgetUpdate": _decode_budget_update,
    "LimitsUpdate": _decode_limits_update,
    "Wake": _decode_wake,
    "Fill": _decode_fill,
    "OrderAccepted": _decode_accepted,
    "OrderRejected": _decode_rejected,
    "SubmitTimeout": _decode_submit_timeout,
    "CancelAck": _decode_cancel_ack,
    "CancelTimeout": _decode_cancel_timeout,
    "CancelUnsettled": _decode_cancel_unsettled,
    "BuySettled": _decode_buy_settled,
    "Recovery": _decode_recovery,
    "RecoveryVerified": _decode_recovery_verified,
    "OwnershipResolved": _decode_ownership,
    "SettlementStatus": _decode_settlement,
    "Merged": _decode_merged,
}


def decode_event(fields: dict[str, object]) -> InboundEvent:
    kind = require_str(fields, _EVENT_TYPE_KEY, "event")
    decoder = EVENT_DECODERS.get(kind)
    if decoder is None:
        raise StrictJsonError(f"unknown event type {kind!r}")
    body = {key: value for key, value in fields.items() if key != _EVENT_TYPE_KEY}
    return decoder(body)


def _decode_keep(value: object, label: str) -> KeepOrder:
    fields = require_object(value, label)
    require_exact_keys(fields, _KEEP_KEYS, label)
    return KeepOrder(
        order_id=require_str(fields, "order_id", label),
        level_index=require_nullable_int(fields, "level_index", label),
    )


def _decode_move(value: object, label: str) -> MoveOrder:
    fields = require_object(value, label)
    require_exact_keys(fields, _MOVE_KEYS, label)
    return MoveOrder(
        order_id=require_str(fields, "order_id", label),
        from_level=require_int(fields, "from_level", label),
        to_level=require_int(fields, "to_level", label),
    )


def _decode_cancel(value: object, label: str) -> CancelOrder:
    fields = require_object(value, label)
    require_exact_keys(fields, _CANCEL_KEYS, label)
    return CancelOrder(
        order_id=require_str(fields, "order_id", label),
        reason=require_str(fields, "reason", label),
    )


def _decode_place(value: object, label: str) -> PlaceOrder:
    fields = require_object(value, label)
    require_exact_keys(fields, _PLACE_KEYS, label)
    return PlaceOrder(
        order_id=require_str(fields, "order_id", label),
        episode_id=require_int(fields, "episode_id", label),
        token_index=require_int(fields, "token_index", label),
        side=_side(fields, "side", label),
        price=require_number(fields, "price", label),
        quantity=require_number(fields, "quantity", label),
        level_index=require_nullable_int(fields, "level_index", label),
        reduce_only=require_bool(fields, "reduce_only", label),
    )


def decode_plan(fields: dict[str, object]) -> Plan:
    label = "plan"
    require_exact_keys(fields, _PLAN_KEYS, label)
    keep = require_list(fields, "keep", label)
    moves = require_list(fields, "moves", label)
    cancels = require_list(fields, "cancels", label)
    places = require_list(fields, "places", label)
    return Plan(
        keep=tuple(_decode_keep(item, f"{label} keep") for item in keep),
        moves=tuple(_decode_move(item, f"{label} moves") for item in moves),
        cancels=tuple(_decode_cancel(item, f"{label} cancels") for item in cancels),
        places=tuple(_decode_place(item, f"{label} places") for item in places),
        block_reason=_block_reason(fields, "block_reason", label),
    )


def decode_header_row(fields: dict[str, object]) -> TraceHeader:
    require_exact_keys(fields, _HEADER_KEYS, "header")
    model = _object_field(fields, "model", "header")
    require_exact_keys(model, _MODEL_KEYS, "header model")
    return TraceHeader(
        session_id=require_str(fields, "session_id", "header"),
        match_id=require_str(fields, "match_id", "header"),
        game=require_str(fields, "game", "header"),
        execution_mode=require_str(fields, "execution_mode", "header"),
        git_commit=require_str(fields, "git_commit", "header"),
        policy=decode_policy(_object_field(fields, "policy", "header"), "header policy"),
        policy_sha=require_str(fields, "policy_sha", "header"),
        limits=decode_limits(_object_field(fields, "limits", "header"), "header limits"),
        freshness=decode_freshness(
            _object_field(fields, "freshness", "header"), "header freshness"
        ),
        model_name=require_str(model, "name", "header model"),
        model_trained_at=require_str(model, "trained_at", "header model"),
        model_sha256=require_str(model, "sha256", "header model"),
        yes_token=require_str(fields, "yes_token", "header"),
        no_token=require_str(fields, "no_token", "header"),
    )


def checkpoint_object(checkpoint: StructuralCheckpoint) -> dict[str, object]:
    return require_object(json.loads(encode_checkpoint(checkpoint)), "checkpoint")


def checkpoint_from_object(payload: dict[str, object]) -> StructuralCheckpoint:
    return decode_checkpoint(dumps_trace(payload))
