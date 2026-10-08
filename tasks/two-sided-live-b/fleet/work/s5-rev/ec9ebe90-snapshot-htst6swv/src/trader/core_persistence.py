"""Typed Follow300 checkpoints and command rows in the wallet SQLite file."""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import sqlite3
import time
from dataclasses import dataclass, replace
from typing import Literal

import msgspec

from shared.constants.strategy import BUY_POLICY_VERSION, EXIT_SETTLE_SECONDS
from shared.utils.trading import HALF_SHARE_TICK
from strategy.types import (
    EpisodeArchive,
    OrderRecord,
    OrderStatus,
    PendingFill,
    RestingOrder,
    Rung,
    Side,
    StrategyState,
    TokenInventory,
    total_qty,
)

CORE_SCHEMA_VERSION = 3
V1_SCHEMA_VERSION = 1
V2_SCHEMA_VERSION = 2
UNSETTLED_CANCEL_REASON = "unsettled"
DispatchState = Literal["prepared", "dispatch_started", "known_not_sent", "dispatched"]
CommandKind = Literal["place", "cancel"]
CommandOutcome = Literal["accepted", "rejected", "timeout", "canceled", "unknown", ""]

_OPEN_BUY_WHERE = (
    "kind='place' AND side='BUY' AND consumed=0 "
    "AND dispatch_state IN ('prepared','dispatch_started','dispatched') "
    "AND (outcome IS NULL OR outcome='')"
)
_RESERVED_BUY_SQL = (
    "SELECT COALESCE(SUM(price * quantity), 0) FROM core_commands WHERE " + _OPEN_BUY_WHERE
)

_CORE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS core_sessions (
    session_id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL UNIQUE,
    game TEXT NOT NULL,
    yes_token TEXT NOT NULL,
    no_token TEXT NOT NULL,
    yes_is_radiant INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    revision INTEGER NOT NULL,
    recovery INTEGER NOT NULL,
    recovery_generation INTEGER NOT NULL,
    checkpoint TEXT NOT NULL,
    last_outbox_seq INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS core_order_bindings (
    session_id TEXT NOT NULL,
    core_order_id TEXT NOT NULL,
    venue_id TEXT,
    episode_id INTEGER NOT NULL,
    token_index INTEGER NOT NULL,
    side TEXT NOT NULL,
    submitted_qty REAL NOT NULL,
    level_index INTEGER,
    terminal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, core_order_id)
);
CREATE INDEX IF NOT EXISTS core_bindings_venue
    ON core_order_bindings(session_id, venue_id);
CREATE TABLE IF NOT EXISTS core_commands (
    command_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    batch_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    core_order_id TEXT NOT NULL,
    token_index INTEGER,
    side TEXT,
    price REAL,
    quantity REAL,
    venue_id TEXT,
    order_hash TEXT,
    dispatch_state TEXT NOT NULL,
    outcome TEXT,
    consumed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS core_commands_session
    ON core_commands(session_id, revision);
CREATE INDEX IF NOT EXISTS core_commands_open_buy
    ON core_commands(session_id) WHERE {_OPEN_BUY_WHERE};
CREATE TABLE IF NOT EXISTS unsettled_buys (
    venue_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    proven INTEGER NOT NULL DEFAULT 0,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS unsettled_buys_resolved_session
    ON unsettled_buys(resolved, session_id);
"""


class CoreSchemaError(Exception):
    """Checkpoint schema is newer or corrupt; do not open a fresh BUY session."""


@dataclass(frozen=True)
class CoreSessionKey:
    condition_id: str
    game: str
    yes_token: str
    no_token: str
    yes_is_radiant: bool


@dataclass(frozen=True)
class TimingCheckpoint:
    checkpoint_wall_s: float


@dataclass(frozen=True)
class TokenInventoryCheckpoint:
    token_index: int
    qty: float
    cost_basis: float
    last_buy_remaining_s: float | None


@dataclass(frozen=True)
class OrderCheckpoint:
    order_id: str
    episode_id: int
    token_index: int
    side: Side
    price: float
    submitted_qty: float
    filled_qty: float
    level_index: int | None
    status: str
    accepted: bool
    partially_filled: bool
    cancel_reason: str
    ack_reason: str
    min_life_remaining_s: float


@dataclass(frozen=True)
class StructuralCheckpoint:
    schema_version: int
    policy_version: str
    episode_id: int
    episode_counter: int
    episode_token_index: int | None
    has_buy_fill: bool
    episode_buy_notional: float
    winding_down: bool
    sell_only: bool
    recovery_pending: bool
    recovery_generation: int
    timing: TimingCheckpoint
    rungs: tuple[Rung, ...]
    orders: tuple[OrderCheckpoint, ...]
    seen_fill_ids: tuple[str, ...]
    inventory: tuple[TokenInventoryCheckpoint, TokenInventoryCheckpoint]
    records: tuple[OrderRecord, ...]
    archives: tuple[EpisodeArchive, ...]
    pending_ownership: tuple[PendingFill, ...]
    unconfirmed_keys: tuple[str, ...]
    next_order_seq: int


@dataclass(frozen=True)
class CoreSessionRow:
    session_id: str
    key: CoreSessionKey
    schema_version: int
    policy_version: str
    revision: int
    recovery: bool
    recovery_generation: int
    checkpoint: StructuralCheckpoint
    last_outbox_seq: int


@dataclass(frozen=True)
class OrderBinding:
    session_id: str
    core_order_id: str
    venue_id: str | None
    episode_id: int
    token_index: int
    side: Side
    submitted_qty: float
    level_index: int | None
    terminal: bool


@dataclass(frozen=True)
class CoreCommand:
    command_id: str
    session_id: str
    revision: int
    batch_id: str
    kind: CommandKind
    core_order_id: str
    token_index: int | None
    side: Side | None
    price: float | None
    quantity: float | None
    venue_id: str | None
    order_hash: str | None
    dispatch_state: DispatchState
    outcome: CommandOutcome
    consumed: bool


@dataclass(frozen=True)
class UnsettledBuy:
    venue_id: str
    session_id: str
    token_id: str
    price: float
    qty: float
    proven: bool
    resolved: bool
    created_at: float
    updated_at: float


def migrate_core_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(_CORE_SCHEMA)


def encode_checkpoint(checkpoint: StructuralCheckpoint) -> str:
    return msgspec.json.encode(checkpoint).decode()


def decode_checkpoint(raw: str) -> StructuralCheckpoint:
    try:
        payload = msgspec.json.decode(raw)
    except msgspec.DecodeError as exc:
        raise CoreSchemaError("checkpoint is not JSON") from exc
    if type(payload) is not dict:
        raise CoreSchemaError("checkpoint is not an object")
    version = payload.get("schema_version")
    if version == V1_SCHEMA_VERSION:
        payload = _upgrade_v1(payload)
    if version in (V1_SCHEMA_VERSION, V2_SCHEMA_VERSION):
        payload = _upgrade_v2(payload)
    elif version != CORE_SCHEMA_VERSION:
        raise CoreSchemaError(f"unsupported schema_version {version}")
    try:
        return msgspec.convert(payload, type=StructuralCheckpoint)
    except (msgspec.ValidationError, TypeError, KeyError) as exc:
        raise CoreSchemaError(str(exc)) from exc


def _upgrade_v1(payload: dict[str, object]) -> dict[str, object]:
    timing = payload.get("timing")
    if type(timing) is not dict:
        raise CoreSchemaError("timing is required")
    position = payload.get("position")
    remaining = timing.get("last_buy_remaining_s")
    token_index = position.get("token_index") if type(position) is dict else None
    upgraded = {
        **payload,
        "schema_version": CORE_SCHEMA_VERSION,
        "sell_only": True,
        "recovery_pending": True,
        "rungs": [],
        "timing": {"checkpoint_wall_s": timing.get("checkpoint_wall_s")},
        "inventory": _v1_inventory(payload.get("inventory"), position, remaining, token_index),
        "records": payload.get("records") or [],
        "archives": payload.get("archives") or [],
        "pending_ownership": payload.get("pending_ownership") or [],
        "unconfirmed_keys": payload.get("unconfirmed_keys") or [],
    }
    upgraded.pop("position", None)
    return upgraded


def _upgrade_v2(payload: dict[str, object]) -> dict[str, object]:
    """Old checkpoints have no gross spend ledger; recovered sessions must drain."""
    archives = payload.get("archives")
    if type(archives) is not list or any(type(item) is not dict for item in archives):
        raise CoreSchemaError("archives must be an array of objects")
    return {
        **payload,
        "schema_version": CORE_SCHEMA_VERSION,
        "episode_buy_notional": 0.0,
        "sell_only": True,
        "recovery_pending": True,
        "archives": [{**item, "episode_buy_notional": 0.0} for item in archives],
    }


def _v1_inventory(
    raw: object, position: object, remaining: object, token_index: object
) -> list[dict[str, object]]:
    tokens: list[dict[str, object]] = [
        {"token_index": 0, "qty": 0.0, "cost_basis": 0.0, "last_buy_remaining_s": None},
        {"token_index": 1, "qty": 0.0, "cost_basis": 0.0, "last_buy_remaining_s": None},
    ]
    if raw is None and type(position) is dict and token_index in (0, 1):
        tokens[token_index] = {
            "token_index": token_index,
            "qty": position.get("qty"),
            "cost_basis": position.get("cost_basis"),
            "last_buy_remaining_s": remaining,
        }
        return tokens
    if type(raw) is not list:
        return tokens
    for item in raw:
        if type(item) is not dict:
            raise CoreSchemaError("inventory item is not an object")
        index = item.get("token_index")
        if index not in (0, 1):
            raise CoreSchemaError("inventory token_index must be 0 or 1")
        leftover = remaining if index == token_index else item.get("last_buy_remaining_s")
        tokens[index] = {
            "token_index": index,
            "qty": item.get("qty"),
            "cost_basis": item.get("cost_basis"),
            "last_buy_remaining_s": leftover,
        }
    return tokens


def _require_status(value: str) -> OrderStatus:
    if value not in ("pending", "live", "canceling", "unknown"):
        raise CoreSchemaError("order status is not valid")
    return value


def _decode_checkpoint_status(status: str, cancel_reason: str) -> OrderStatus:
    if status == "unknown" and cancel_reason == UNSETTLED_CANCEL_REASON:
        return "gone"
    return _require_status(status)


def remaining_delay_s(*, now_ns: int, start_ns: int | None, duration_s: float) -> float | None:
    if start_ns is None:
        return None
    elapsed_s = max(0.0, (now_ns - start_ns) / 1_000_000_000)
    return max(0.0, duration_s - elapsed_s)


def rebase_delay_ns(
    *, now_ns: int, remaining_s: float | None, checkpoint_wall_s: float, now_wall_s: float
) -> int | None:
    if remaining_s is None:
        return None
    elapsed_s = 0.0 if now_wall_s < checkpoint_wall_s else now_wall_s - checkpoint_wall_s
    leftover = max(0.0, remaining_s - elapsed_s)
    if leftover <= 0.0:
        return None
    return now_ns - int((EXIT_SETTLE_SECONDS - leftover) * 1_000_000_000)


def rebase_placed_ns(
    *,
    now_ns: int,
    remaining_s: float,
    checkpoint_wall_s: float,
    now_wall_s: float,
    min_life_s: float,
) -> int:
    elapsed_s = 0.0 if now_wall_s < checkpoint_wall_s else now_wall_s - checkpoint_wall_s
    leftover = max(0.0, remaining_s - elapsed_s)
    return now_ns - int((min_life_s - leftover) * 1_000_000_000)


def snapshot_checkpoint(
    *, state: StrategyState, now_ns: int, now_wall_s: float, sell_min_life_s: float
) -> StructuralCheckpoint:
    return StructuralCheckpoint(
        schema_version=CORE_SCHEMA_VERSION,
        policy_version=BUY_POLICY_VERSION,
        episode_id=state.episode_id,
        episode_counter=state.episode_counter,
        episode_token_index=state.episode_token_index,
        has_buy_fill=state.has_buy_fill,
        episode_buy_notional=state.episode_buy_notional,
        winding_down=state.winding_down,
        sell_only=state.sell_only,
        recovery_pending=state.recovery_pending,
        recovery_generation=state.recovery_generation,
        timing=TimingCheckpoint(checkpoint_wall_s=now_wall_s),
        rungs=state.rungs,
        orders=tuple(
            OrderCheckpoint(
                order_id=order.order_id,
                episode_id=order.episode_id,
                token_index=order.token_index,
                side=order.side,
                price=order.price,
                submitted_qty=order.submitted_qty,
                filled_qty=order.filled_qty,
                level_index=order.level_index,
                status="unknown" if order.status == "gone" else order.status,
                accepted=order.accepted,
                partially_filled=order.partially_filled,
                cancel_reason=(
                    UNSETTLED_CANCEL_REASON if order.status == "gone" else order.cancel_reason
                ),
                ack_reason=order.ack_reason,
                min_life_remaining_s=remaining_delay_s(
                    now_ns=now_ns,
                    start_ns=order.accepted_ns
                    if order.accepted_ns is not None
                    else order.placed_ns,
                    duration_s=sell_min_life_s,
                )
                or 0.0,
            )
            for order in state.orders
        ),
        seen_fill_ids=state.seen_fill_ids,
        inventory=(
            TokenInventoryCheckpoint(
                token_index=0,
                qty=state.inventory[0].qty,
                cost_basis=state.inventory[0].cost_basis,
                last_buy_remaining_s=remaining_delay_s(
                    now_ns=now_ns,
                    start_ns=state.inventory[0].last_buy_ns,
                    duration_s=EXIT_SETTLE_SECONDS,
                ),
            ),
            TokenInventoryCheckpoint(
                token_index=1,
                qty=state.inventory[1].qty,
                cost_basis=state.inventory[1].cost_basis,
                last_buy_remaining_s=remaining_delay_s(
                    now_ns=now_ns,
                    start_ns=state.inventory[1].last_buy_ns,
                    duration_s=EXIT_SETTLE_SECONDS,
                ),
            ),
        ),
        records=state.records,
        archives=state.archives,
        pending_ownership=state.pending_ownership,
        unconfirmed_keys=state.unconfirmed_keys,
        next_order_seq=state.next_order_seq,
    )


def restore_orders(
    *,
    checkpoint: StructuralCheckpoint,
    now_ns: int,
    now_wall_s: float,
    sell_min_life_s: float,
) -> tuple[RestingOrder, ...]:
    return tuple(
        RestingOrder(
            order_id=order.order_id,
            episode_id=order.episode_id,
            token_index=order.token_index,
            side=order.side,
            price=order.price,
            submitted_qty=order.submitted_qty,
            filled_qty=order.filled_qty,
            level_index=order.level_index,
            status=_decode_checkpoint_status(order.status, order.cancel_reason),
            accepted=order.accepted,
            partially_filled=order.partially_filled,
            cancel_reason=order.cancel_reason,
            ack_reason=order.ack_reason,
            placed_ns=rebase_placed_ns(
                now_ns=now_ns,
                remaining_s=order.min_life_remaining_s,
                checkpoint_wall_s=checkpoint.timing.checkpoint_wall_s,
                now_wall_s=now_wall_s,
                min_life_s=sell_min_life_s,
            ),
            accepted_ns=(
                None
                if not order.accepted
                else rebase_placed_ns(
                    now_ns=now_ns,
                    remaining_s=order.min_life_remaining_s,
                    checkpoint_wall_s=checkpoint.timing.checkpoint_wall_s,
                    now_wall_s=now_wall_s,
                    min_life_s=sell_min_life_s,
                )
            ),
        )
        for order in checkpoint.orders
    )


def restore_inventory(
    *, checkpoint: StructuralCheckpoint, now_ns: int, now_wall_s: float
) -> tuple[TokenInventory, TokenInventory]:
    left, right = (
        TokenInventory(
            token_index=item.token_index,
            qty=item.qty,
            cost_basis=item.cost_basis,
            last_buy_ns=rebase_delay_ns(
                now_ns=now_ns,
                remaining_s=item.last_buy_remaining_s,
                checkpoint_wall_s=checkpoint.timing.checkpoint_wall_s,
                now_wall_s=now_wall_s,
            ),
        )
        for item in checkpoint.inventory
    )
    return (left, right)


def apply_checkpoint(
    state: StrategyState,
    *,
    checkpoint: StructuralCheckpoint,
    now_ns: int,
    now_wall_s: float,
    sell_min_life_s: float,
) -> StrategyState:
    inventory = restore_inventory(checkpoint=checkpoint, now_ns=now_ns, now_wall_s=now_wall_s)
    sell_only = checkpoint.sell_only
    recovery_pending = checkpoint.recovery_pending
    held_qty = sum(rung.held_qty for rung in checkpoint.rungs)
    if total_qty(inventory) > 0.0 and held_qty <= 0.0:
        sell_only = True
        recovery_pending = True
    return replace(
        state,
        episode_id=checkpoint.episode_id,
        episode_counter=checkpoint.episode_counter,
        episode_token_index=checkpoint.episode_token_index,
        has_buy_fill=checkpoint.has_buy_fill,
        episode_buy_notional=checkpoint.episode_buy_notional,
        winding_down=checkpoint.winding_down,
        sell_only=sell_only,
        recovery_pending=recovery_pending,
        recovery_generation=checkpoint.recovery_generation,
        rungs=checkpoint.rungs,
        orders=restore_orders(
            checkpoint=checkpoint,
            now_ns=now_ns,
            now_wall_s=now_wall_s,
            sell_min_life_s=sell_min_life_s,
        ),
        records=checkpoint.records,
        archives=checkpoint.archives,
        seen_fill_ids=checkpoint.seen_fill_ids,
        pending_ownership=checkpoint.pending_ownership,
        unconfirmed_keys=checkpoint.unconfirmed_keys,
        inventory=inventory,
        next_order_seq=checkpoint.next_order_seq,
    )


def restore_last_buy(state: StrategyState, *, last_buy_ns: int, token_index: int) -> StrategyState:
    held = state.inventory[token_index]
    tokens = list(state.inventory)
    tokens[token_index] = replace(held, last_buy_ns=last_buy_ns)
    return replace(state, inventory=(tokens[0], tokens[1]))


def get_session(conn: sqlite3.Connection, condition_id: str) -> CoreSessionRow | None:
    row = conn.execute(
        "SELECT * FROM core_sessions WHERE condition_id=?", (condition_id,)
    ).fetchone()
    if row is None:
        return None
    return _session_from_row(row)


def list_sessions(conn: sqlite3.Connection) -> tuple[CoreSessionRow, ...]:
    rows = conn.execute("SELECT * FROM core_sessions ORDER BY condition_id").fetchall()
    return tuple(_session_from_row(row) for row in rows)


def _session_from_row(row: sqlite3.Row) -> CoreSessionRow:
    schema_version = int(row["schema_version"])
    if schema_version not in (V1_SCHEMA_VERSION, V2_SCHEMA_VERSION, CORE_SCHEMA_VERSION):
        raise CoreSchemaError(f"unsupported schema_version {schema_version}")
    return CoreSessionRow(
        session_id=str(row["session_id"]),
        key=CoreSessionKey(
            condition_id=str(row["condition_id"]),
            game=str(row["game"]),
            yes_token=str(row["yes_token"]),
            no_token=str(row["no_token"]),
            yes_is_radiant=bool(row["yes_is_radiant"]),
        ),
        schema_version=schema_version,
        policy_version=str(row["policy_version"]),
        revision=int(row["revision"]),
        recovery=bool(row["recovery"]),
        recovery_generation=int(row["recovery_generation"]),
        checkpoint=decode_checkpoint(str(row["checkpoint"])),
        last_outbox_seq=int(row["last_outbox_seq"]),
    )


def upsert_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    key: CoreSessionKey,
    revision: int,
    recovery: bool,
    recovery_generation: int,
    checkpoint: StructuralCheckpoint,
    last_outbox_seq: int,
) -> None:
    if key.yes_token == key.no_token:
        raise CoreSchemaError("yes and no tokens must differ")
    now = time.time()
    encoded = encode_checkpoint(checkpoint)
    conn.execute(
        "INSERT INTO core_sessions("
        "session_id, condition_id, game, yes_token, no_token, yes_is_radiant,"
        " schema_version, policy_version, revision, recovery, recovery_generation,"
        " checkpoint, last_outbox_seq, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(session_id) DO UPDATE SET "
        "schema_version=excluded.schema_version, policy_version=excluded.policy_version,"
        "revision=excluded.revision, recovery=excluded.recovery,"
        " recovery_generation=excluded.recovery_generation,"
        " checkpoint=excluded.checkpoint, last_outbox_seq=excluded.last_outbox_seq,"
        " updated_at=excluded.updated_at",
        (
            session_id,
            key.condition_id,
            key.game,
            key.yes_token,
            key.no_token,
            int(key.yes_is_radiant),
            checkpoint.schema_version,
            checkpoint.policy_version,
            revision,
            int(recovery),
            recovery_generation,
            encoded,
            last_outbox_seq,
            now,
            now,
        ),
    )


def upsert_binding(conn: sqlite3.Connection, binding: OrderBinding) -> None:
    conn.execute(
        "INSERT INTO core_order_bindings("
        "session_id, core_order_id, venue_id, episode_id, token_index, side,"
        " submitted_qty, level_index, terminal)"
        " VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(session_id, core_order_id) DO UPDATE SET "
        "venue_id=excluded.venue_id, episode_id=excluded.episode_id,"
        " token_index=excluded.token_index, side=excluded.side,"
        " submitted_qty=excluded.submitted_qty, level_index=excluded.level_index,"
        " terminal=excluded.terminal",
        (
            binding.session_id,
            binding.core_order_id,
            binding.venue_id,
            binding.episode_id,
            binding.token_index,
            binding.side,
            binding.submitted_qty,
            binding.level_index,
            int(binding.terminal),
        ),
    )


def list_bindings(conn: sqlite3.Connection, session_id: str) -> tuple[OrderBinding, ...]:
    rows = conn.execute(
        "SELECT * FROM core_order_bindings WHERE session_id=? ORDER BY core_order_id",
        (session_id,),
    ).fetchall()
    return tuple(_binding_from_row(row) for row in rows)


def binding_for_venue(
    conn: sqlite3.Connection, *, session_id: str, venue_id: str
) -> OrderBinding | None:
    row = conn.execute(
        "SELECT * FROM core_order_bindings WHERE session_id=? AND venue_id=?",
        (session_id, venue_id),
    ).fetchone()
    if row is None:
        return None
    return _binding_from_row(row)


def _binding_from_row(row: sqlite3.Row) -> OrderBinding:
    side = str(row["side"])
    if side not in ("BUY", "SELL"):
        raise CoreSchemaError("binding side must be BUY or SELL")
    level = row["level_index"]
    return OrderBinding(
        session_id=str(row["session_id"]),
        core_order_id=str(row["core_order_id"]),
        venue_id=None if row["venue_id"] is None else str(row["venue_id"]),
        episode_id=int(row["episode_id"]),
        token_index=int(row["token_index"]),
        side=side,
        submitted_qty=float(row["submitted_qty"]),
        level_index=None if level is None else int(level),
        terminal=bool(row["terminal"]),
    )


def upsert_command(conn: sqlite3.Connection, command: CoreCommand) -> None:
    conn.execute(
        "INSERT INTO core_commands("
        "command_id, session_id, revision, batch_id, kind, core_order_id,"
        " token_index, side, price, quantity, venue_id, order_hash,"
        " dispatch_state, outcome, consumed)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(command_id) DO UPDATE SET "
        "venue_id=excluded.venue_id, order_hash=excluded.order_hash,"
        " dispatch_state=excluded.dispatch_state, outcome=excluded.outcome,"
        " consumed=excluded.consumed",
        (
            command.command_id,
            command.session_id,
            command.revision,
            command.batch_id,
            command.kind,
            command.core_order_id,
            command.token_index,
            command.side,
            command.price,
            command.quantity,
            command.venue_id,
            command.order_hash,
            command.dispatch_state,
            command.outcome,
            int(command.consumed),
        ),
    )


def list_commands(conn: sqlite3.Connection, session_id: str) -> tuple[CoreCommand, ...]:
    rows = conn.execute(
        "SELECT * FROM core_commands WHERE session_id=? ORDER BY command_id",
        (session_id,),
    ).fetchall()
    return tuple(_command_from_row(row) for row in rows)


def list_prepared_places(conn: sqlite3.Connection, session_id: str) -> tuple[CoreCommand, ...]:
    rows = conn.execute(
        "SELECT * FROM core_commands WHERE session_id=? AND kind='place' "
        "AND dispatch_state='prepared' ORDER BY command_id",
        (session_id,),
    ).fetchall()
    return tuple(_command_from_row(row) for row in rows)


def unresolved_commands(conn: sqlite3.Connection, session_id: str) -> tuple[CoreCommand, ...]:
    rows = conn.execute(
        "SELECT * FROM core_commands WHERE session_id=? AND consumed=0 "
        "AND dispatch_state IN ('prepared','dispatch_started','dispatched') "
        "ORDER BY command_id",
        (session_id,),
    ).fetchall()
    return tuple(_command_from_row(row) for row in rows)


def reserved_buy_notional(conn: sqlite3.Connection) -> float:
    row = conn.execute(_RESERVED_BUY_SQL).fetchone()
    return float(row[0])


def list_open_buy_commands(conn: sqlite3.Connection) -> tuple[CoreCommand, ...]:
    rows = conn.execute(
        "SELECT * FROM core_commands WHERE " + _OPEN_BUY_WHERE + " ORDER BY command_id"
    ).fetchall()
    return tuple(_command_from_row(row) for row in rows)


def reserved_buy_notional_for_session(conn: sqlite3.Connection, session_id: str) -> float:
    row = conn.execute(_RESERVED_BUY_SQL + " AND session_id=?", (session_id,)).fetchone()
    return float(row[0])


_UNSETTLED_NOTIONAL_SQL = (
    "SELECT COALESCE(SUM("
    "CASE WHEN u.qty > COALESCE(b.booked, 0.0) "
    "THEN (u.qty - COALESCE(b.booked, 0.0)) * u.price ELSE 0.0 END"
    "), 0.0) "
    "FROM unsettled_buys u "
    "LEFT JOIN ("
    "SELECT maker_order_id, SUM(size) AS booked FROM fill_ledger "
    "WHERE side='BUY' AND status IN ('MATCHED', 'CONFIRMED') "
    "GROUP BY maker_order_id"
    ") b ON b.maker_order_id = u.venue_id "
    "WHERE u.resolved=0"
)


def insert_unsettled_buy(
    conn: sqlite3.Connection,
    *,
    venue_id: str,
    session_id: str,
    token_id: str,
    price: float,
    qty: float,
) -> None:
    now = time.time()
    conn.execute(
        "INSERT OR IGNORE INTO unsettled_buys("
        "venue_id, session_id, token_id, price, qty, proven, resolved, created_at, updated_at)"
        " VALUES(?,?,?,?,?,0,0,?,?)",
        (venue_id, session_id, token_id, price, qty, now, now),
    )


def prove_unsettled_buy(conn: sqlite3.Connection, venue_id: str, matched_qty: float) -> None:
    conn.execute(
        "UPDATE unsettled_buys SET qty=?, proven=1, updated_at=? WHERE venue_id=? AND resolved=0",
        (matched_qty, time.time(), venue_id),
    )


def resolve_settled_buys(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute(
        "UPDATE unsettled_buys SET resolved=1, updated_at=? "
        "WHERE venue_id IN ("
        "SELECT u.venue_id FROM unsettled_buys u "
        "LEFT JOIN ("
        "SELECT maker_order_id, SUM(size) AS final_qty FROM fill_ledger "
        "WHERE side='BUY' AND status='CONFIRMED' GROUP BY maker_order_id"
        ") f ON f.maker_order_id = u.venue_id "
        "WHERE u.resolved=0 AND COALESCE(f.final_qty, 0.0) >= u.qty - ?"
        ") RETURNING venue_id",
        (time.time(), HALF_SHARE_TICK),
    ).fetchall()
    return tuple(sorted(str(row["venue_id"]) for row in rows))


def unsettled_buy_notional(conn: sqlite3.Connection, session_id: str | None) -> float:
    sql = _UNSETTLED_NOTIONAL_SQL
    params: tuple[str, ...] = ()
    if session_id is not None:
        sql += " AND u.session_id=?"
        params = (session_id,)
    row = conn.execute(sql, params).fetchone()
    return float(row[0])


def get_unsettled_buy(conn: sqlite3.Connection, venue_id: str) -> UnsettledBuy | None:
    row = conn.execute(
        "SELECT * FROM unsettled_buys WHERE venue_id=?",
        (venue_id,),
    ).fetchone()
    if row is None:
        return None
    return _unsettled_from_row(row)


def open_unsettled_buys(conn: sqlite3.Connection) -> tuple[UnsettledBuy, ...]:
    rows = conn.execute(
        "SELECT * FROM unsettled_buys WHERE resolved=0 ORDER BY created_at, venue_id"
    ).fetchall()
    return tuple(_unsettled_from_row(row) for row in rows)


def resolved_unsettled_buys(conn: sqlite3.Connection, session_id: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT venue_id, qty FROM unsettled_buys WHERE resolved=1 AND session_id=? "
        "ORDER BY venue_id",
        (session_id,),
    ).fetchall()
    return {str(row["venue_id"]): float(row["qty"]) for row in rows}


def _unsettled_from_row(row: sqlite3.Row) -> UnsettledBuy:
    return UnsettledBuy(
        venue_id=str(row["venue_id"]),
        session_id=str(row["session_id"]),
        token_id=str(row["token_id"]),
        price=float(row["price"]),
        qty=float(row["qty"]),
        proven=bool(row["proven"]),
        resolved=bool(row["resolved"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _command_from_row(row: sqlite3.Row) -> CoreCommand:
    kind = str(row["kind"])
    if kind not in ("place", "cancel"):
        raise CoreSchemaError("command kind must be place or cancel")
    dispatch = str(row["dispatch_state"])
    if dispatch not in ("prepared", "dispatch_started", "known_not_sent", "dispatched"):
        raise CoreSchemaError("unknown dispatch_state")
    outcome_raw = row["outcome"]
    outcome = "" if outcome_raw is None else str(outcome_raw)
    side_raw = row["side"]
    side = None if side_raw is None else str(side_raw)
    if side is not None and side not in ("BUY", "SELL"):
        raise CoreSchemaError("command side must be BUY or SELL")
    token = row["token_index"]
    price = row["price"]
    quantity = row["quantity"]
    return CoreCommand(
        command_id=str(row["command_id"]),
        session_id=str(row["session_id"]),
        revision=int(row["revision"]),
        batch_id=str(row["batch_id"]),
        kind=kind,
        core_order_id=str(row["core_order_id"]),
        token_index=None if token is None else int(token),
        side=side,  # type: ignore[arg-type]
        price=None if price is None else float(price),
        quantity=None if quantity is None else float(quantity),
        venue_id=None if row["venue_id"] is None else str(row["venue_id"]),
        order_hash=None if row["order_hash"] is None else str(row["order_hash"]),
        dispatch_state=dispatch,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        consumed=bool(row["consumed"]),
    )


def outbox_after(
    conn: sqlite3.Connection, *, after_seq: int, tokens: frozenset[str]
) -> tuple[tuple[int, str, str], ...]:
    if not tokens:
        return ()
    placeholders = ",".join("?" * len(tokens))
    rows = conn.execute(
        "SELECT o.seq, o.fill_key, o.event FROM fill_outbox o "
        "JOIN fill_ledger l ON l.fill_key = o.fill_key "
        f"WHERE o.seq > ? AND l.token_id IN ({placeholders}) ORDER BY o.seq",
        (after_seq, *tokens),
    ).fetchall()
    return tuple((int(row["seq"]), str(row["fill_key"]), str(row["event"])) for row in rows)


@dataclass(frozen=True)
class MergeLeg:
    token_id: str
    qty: float


def read_merge_leg(conn: sqlite3.Connection, *, fill_key: str) -> MergeLeg:
    row = conn.execute(
        "SELECT token_id, size FROM fill_ledger WHERE fill_key=?", (fill_key,)
    ).fetchone()
    return MergeLeg(token_id=str(row["token_id"]), qty=float(row["size"]))
