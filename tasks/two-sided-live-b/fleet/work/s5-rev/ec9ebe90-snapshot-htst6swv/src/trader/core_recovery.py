"""Classify restored sessions and prove inventory before a recovery SELL."""

# pyright: reportPrivateUsage=false

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from shared.constants.strategy import EXIT_SETTLE_SECONDS
from shared.utils.log import get_logger
from shared.utils.trading import share_floor
from strategy.lifecycle import has_unresolved_orders
from strategy.types import Position, TokenInventory
from trader.core_persistence import (
    CoreSchemaError,
    CoreSessionKey,
    CoreSessionRow,
    get_session,
    list_bindings,
    list_commands,
    list_sessions,
    rebase_delay_ns,
    unresolved_commands,
)
from trader.core_session_io import SessionIdentity, consume_core_outbox, persist_core_snapshot
from trader.session_core import LiveCore
from trader.wallet_store import WalletStateStore, share_qty_matches

logger = get_logger(__name__)

SessionKind = Literal["fresh", "recovery", "corrupt"]


@dataclass(frozen=True)
class SessionClass:
    kind: SessionKind
    session: CoreSessionRow | None
    reason: str


@dataclass(frozen=True)
class VerifiedInventory:
    yes_qty: float
    no_qty: float
    selected: Position
    last_buy_ns: int | None
    generation: int


def classify_session(
    conn: sqlite3.Connection,
    *,
    key: CoreSessionKey,
    has_inventory: bool,
    has_orders: bool,
) -> SessionClass:
    try:
        row = get_session(conn, key.condition_id)
    except CoreSchemaError as exc:
        return SessionClass(kind="corrupt", session=None, reason=str(exc))
    if row is not None and (
        row.key.yes_token != key.yes_token
        or row.key.no_token != key.no_token
        or row.key.yes_is_radiant != key.yes_is_radiant
    ):
        return SessionClass(kind="corrupt", session=row, reason="token_mismatch")
    if has_inventory or has_orders:
        reason = "legacy" if row is None else "checkpoint"
        return SessionClass(kind="recovery", session=row, reason=reason)
    reason = "new" if row is None else "idle_checkpoint"
    return SessionClass(kind="fresh", session=row, reason=reason)


def select_sellable(*, yes_qty: float, no_qty: float, min_size: float) -> Position:
    yes_sellable = share_floor(yes_qty) >= min_size
    no_sellable = share_floor(no_qty) >= min_size
    if yes_sellable and no_sellable:
        if yes_qty >= no_qty:
            return Position(token_index=0, qty=yes_qty, cost_basis=0.0)
        return Position(token_index=1, qty=no_qty, cost_basis=0.0)
    if yes_sellable:
        return Position(token_index=0, qty=yes_qty, cost_basis=0.0)
    if no_sellable:
        return Position(token_index=1, qty=no_qty, cost_basis=0.0)
    if yes_qty >= no_qty and yes_qty > 0:
        return Position(token_index=0, qty=yes_qty, cost_basis=0.0)
    if no_qty > 0:
        return Position(token_index=1, qty=no_qty, cost_basis=0.0)
    return Position(token_index=None, qty=0.0, cost_basis=0.0)


def should_reverify(*, previous: VerifiedInventory | None, fill_generation: int) -> bool:
    if previous is None:
        return True
    return previous.generation != fill_generation


class RecoveryCoordinator:
    def __init__(
        self,
        *,
        store: WalletStateStore,
        session_id: str,
        yes_token: str,
        no_token: str,
        min_order_size: float,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._yes = yes_token
        self._no = no_token
        self._min = min_order_size
        self._log_key = ""

    def classify(
        self,
        *,
        key: CoreSessionKey,
        has_orders: bool,
    ) -> SessionClass:
        yes = self._store.position(self._yes).size
        no = self._store.position(self._no).size
        outstanding = has_orders or bool(unresolved_commands(self._store._conn, self._session_id))
        return classify_session(
            self._store._conn,
            key=key,
            has_inventory=yes > 0.0 or no > 0.0,
            has_orders=outstanding,
        )

    def unresolved_order_ids(self) -> tuple[str, ...]:
        known = tuple(
            binding.venue_id
            for binding in list_bindings(self._store._conn, self._session_id)
            if binding.venue_id is not None and not binding.terminal
        )
        pending = tuple(
            command.venue_id
            for command in unresolved_commands(self._store._conn, self._session_id)
            if command.venue_id is not None
        )
        return tuple(dict.fromkeys((*known, *pending)))

    def orphan_tokens(self) -> tuple[str, str]:
        return self._yes, self._no

    def matched_open(self) -> bool:
        return bool(self._store.matched_keys_for_tokens({self._yes, self._no}))

    def proof_blocks(self, core: LiveCore) -> str | None:
        if has_unresolved_orders(state=core.state):
            return "orders"
        if self.matched_open():
            return "matched"
        unknown = tuple(
            command
            for command in unresolved_commands(self._store._conn, self._session_id)
            if command.dispatch_state == "dispatch_started" and command.outcome in ("", "unknown")
        )
        if unknown:
            return "unknown_command"
        return None

    def accept_if_proven(
        self,
        *,
        core: LiveCore,
        now_ns: int,
        now_wall_s: float,
        rest_yes: float,
        rest_no: float,
        last_buy_unix: float | None,
    ) -> bool:
        if not core.state.recovery_pending:
            return False
        if self.proof_blocks(core) is not None:
            return False
        tokens = frozenset({self._yes, self._no})
        lagging = bool(self._store.core_outbox_after(after_seq=core.last_outbox_seq, tokens=tokens))
        if not self._replay_outbox(core=core):
            return False
        if self.proof_blocks(core) is not None:
            return False
        yes, no = self.ledger_sizes()
        if not share_qty_matches(rest_yes, yes):
            return False
        if not share_qty_matches(rest_no, no):
            return False
        remaining = wall_remaining_last_buy(
            last_buy_unix=last_buy_unix, now_wall=now_wall_s, settle_s=EXIT_SETTLE_SECONDS
        )
        inventory = self.verified_from_ledger(
            now_ns=now_ns, now_wall_s=now_wall_s, generation=core.state.recovery_generation
        )
        if remaining is not None:
            inventory = VerifiedInventory(
                yes_qty=inventory.yes_qty,
                no_qty=inventory.no_qty,
                selected=inventory.selected,
                last_buy_ns=last_buy_ns_from_remaining(
                    now_ns=now_ns, remaining_s=remaining, settle_s=EXIT_SETTLE_SECONDS
                ),
                generation=inventory.generation,
            )
        yes_ns = inventory.last_buy_ns if inventory.selected.token_index == 0 else None
        no_ns = inventory.last_buy_ns if inventory.selected.token_index == 1 else None
        core.note_recovery_verified(
            now_ns=now_ns,
            generation=inventory.generation,
            inventory=(
                TokenInventory(
                    token_index=0,
                    qty=inventory.yes_qty,
                    cost_basis=0.0,
                    last_buy_ns=yes_ns,
                ),
                TokenInventory(
                    token_index=1,
                    qty=inventory.no_qty,
                    cost_basis=0.0,
                    last_buy_ns=no_ns,
                ),
            ),
        )
        core.drain_apply()
        accepted = not core.state.recovery_pending
        if accepted and lagging:
            self._persist_recovered(core=core)
        return accepted

    def _replay_outbox(self, *, core: LiveCore) -> bool:
        tokens = frozenset({self._yes, self._no})
        if not self._store.core_outbox_after(after_seq=core.last_outbox_seq, tokens=tokens):
            return True
        session = get_session(self._store._conn, self._session_id)
        if session is None:
            return False
        consume_core_outbox(
            store=self._store,
            core=core,
            identity=SessionIdentity(session_id=self._session_id, key=session.key),
            tokens=tokens,
        )
        return not self._store.core_outbox_after(after_seq=core.last_outbox_seq, tokens=tokens)

    def _persist_recovered(self, *, core: LiveCore) -> None:
        session = get_session(self._store._conn, self._session_id)
        if session is None:
            return
        persist_core_snapshot(
            store=self._store,
            core=core,
            identity=SessionIdentity(session_id=self._session_id, key=session.key),
        )

    def ledger_sizes(self) -> tuple[float, float]:
        return self._store.position(self._yes).size, self._store.position(self._no).size

    def verified_from_ledger(
        self, *, now_ns: int, now_wall_s: float, generation: int
    ) -> VerifiedInventory:
        session = get_session(self._store._conn, self._session_id)
        yes, no = self.ledger_sizes()
        selected = select_sellable(yes_qty=yes, no_qty=no, min_size=self._min)
        remaining = None
        if session is not None and selected.token_index is not None:
            remaining = session.checkpoint.inventory[selected.token_index].last_buy_remaining_s
        last_buy_ns = None
        if session is not None:
            last_buy_ns = rebase_delay_ns(
                now_ns=now_ns,
                remaining_s=remaining,
                checkpoint_wall_s=session.checkpoint.timing.checkpoint_wall_s,
                now_wall_s=now_wall_s,
            )
        return VerifiedInventory(
            yes_qty=yes,
            no_qty=no,
            selected=selected,
            last_buy_ns=last_buy_ns,
            generation=generation,
        )

    def log_transition(
        self,
        *,
        reason: str,
        remaining_orders: int,
        unsettled: int,
        yes_qty: float,
        no_qty: float,
    ) -> None:
        key = f"{reason}:{remaining_orders}:{unsettled}:{yes_qty:.4f}:{no_qty:.4f}"
        if key == self._log_key:
            return
        self._log_key = key
        logger.warning(
            "trader recovery %s session=%s orders=%d unsettled=%d yes=%.4f no=%.4f",
            reason,
            self._session_id[:12],
            remaining_orders,
            unsettled,
            yes_qty,
            no_qty,
        )


async def prove_token_balances(
    *,
    read_balances: Callable[[list[str]], Awaitable[dict[str, float] | None]],
    yes_token: str,
    no_token: str,
    store: WalletStateStore,
    revision_of: Callable[[], int],
) -> tuple[float, float] | None:
    before = revision_of()
    snapshot = await read_balances([yes_token, no_token])
    if snapshot is None:
        return None
    if yes_token not in snapshot or no_token not in snapshot:
        return None
    if revision_of() != before:
        return None
    yes = snapshot[yes_token]
    no = snapshot[no_token]
    if not share_qty_matches(yes, store.position(yes_token).size):
        return None
    if not share_qty_matches(no, store.position(no_token).size):
        return None
    return yes, no


def durable_buy_reservation(conn: sqlite3.Connection, *, exclude_core_ids: frozenset[str]) -> float:
    reserved = 0.0
    for session in list_sessions(conn):
        for command in list_commands(conn, session.session_id):
            if command.core_order_id in exclude_core_ids:
                continue
            if command.kind != "place" or command.side != "BUY" or command.consumed:
                continue
            if command.dispatch_state not in ("prepared", "dispatch_started", "dispatched"):
                continue
            if command.price is None or command.quantity is None:
                continue
            reserved += command.price * command.quantity
    return reserved


def wall_remaining_last_buy(
    *, last_buy_unix: float | None, now_wall: float, settle_s: float
) -> float | None:
    if last_buy_unix is None:
        return None
    leftover = settle_s - max(0.0, now_wall - last_buy_unix)
    if leftover <= 0.0:
        return None
    return leftover


def last_buy_ns_from_remaining(
    *, now_ns: int, remaining_s: float | None, settle_s: float
) -> int | None:
    if remaining_s is None:
        return None
    return now_ns - int((settle_s - remaining_s) * 1_000_000_000)
