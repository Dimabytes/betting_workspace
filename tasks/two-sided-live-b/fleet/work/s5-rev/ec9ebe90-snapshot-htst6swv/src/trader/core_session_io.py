"""One transaction for checkpoint, bindings, and the core outbox cursor."""

# pyright: reportPrivateUsage=false

import time
from collections.abc import Callable
from dataclasses import dataclass

from trader.core_persistence import (
    CoreSessionKey,
    OrderBinding,
    get_session,
    list_bindings,
    read_merge_leg,
    upsert_binding,
    upsert_session,
)
from trader.fill_parsing import split_fill_key
from trader.session_core import LiveCore, core_now_ns
from trader.wallet_store import OutboxItem, WalletStateStore


@dataclass(frozen=True)
class SessionIdentity:
    session_id: str
    key: CoreSessionKey


def persist_core_snapshot(
    *, store: WalletStateStore, core: LiveCore, identity: SessionIdentity
) -> None:
    now_ns = core_now_ns()
    checkpoint = core.export_checkpoint(now_ns=now_ns, now_wall_s=time.time())
    records = {record.order_id: record for record in core.state.records}
    for venue_id, core_id in core._venue_to_core.items():
        record = records.get(core_id)
        if record is None:
            continue
        upsert_binding(
            store._conn,
            OrderBinding(
                session_id=identity.session_id,
                core_order_id=core_id,
                venue_id=venue_id,
                episode_id=record.episode_id,
                token_index=record.token_index,
                side=record.side,
                submitted_qty=record.submitted_qty,
                level_index=record.level_index,
                terminal=record.terminal,
            ),
        )
    upsert_session(
        store._conn,
        session_id=identity.session_id,
        key=identity.key,
        revision=core.state.next_order_seq,
        recovery=core.state.sell_only,
        recovery_generation=core.state.recovery_generation,
        checkpoint=checkpoint,
        last_outbox_seq=core.last_outbox_seq,
    )
    store._conn.commit()


def load_core_snapshot(*, store: WalletStateStore, core: LiveCore, session_id: str) -> None:
    row = get_session(store._conn, session_id)
    if row is None:
        return
    now_ns = core_now_ns()
    core.restore_checkpoint(checkpoint=row.checkpoint, now_ns=now_ns, now_wall_s=time.time())
    core.last_outbox_seq = row.last_outbox_seq
    for binding in list_bindings(store._conn, session_id):
        if binding.venue_id is not None:
            core.bind_venue(core_id=binding.core_order_id, venue_id=binding.venue_id)


def apply_and_persist(
    *,
    store: WalletStateStore,
    core: LiveCore,
    identity: SessionIdentity,
    apply: Callable[[], None],
) -> None:
    memory = core.capture()
    try:
        apply()
        persist_core_snapshot(store=store, core=core, identity=identity)
    except Exception:
        core.revert(memory)
        raise


def consume_core_outbox(
    *,
    store: WalletStateStore,
    core: LiveCore,
    identity: SessionIdentity,
    tokens: frozenset[str],
) -> None:
    for item in store.core_outbox_after(after_seq=core.last_outbox_seq, tokens=tokens):
        memory = core.capture()
        try:
            if not _note_outbox_item(store=store, core=core, item=item):
                return
            core.drain_apply()
            core.last_outbox_seq = item.seq
            persist_core_snapshot(store=store, core=core, identity=identity)
        except Exception:
            core.revert(memory)
            raise


def _note_merge_leg(*, store: WalletStateStore, core: LiveCore, fill_key: str) -> bool:
    leg = read_merge_leg(store._conn, fill_key=fill_key)
    token_index = core.token_index(leg.token_id)
    if token_index is None:
        return False
    core.note_merge(token_index=token_index, qty=leg.qty, now_ns=core_now_ns())
    return True


def _note_outbox_item(*, store: WalletStateStore, core: LiveCore, item: OutboxItem) -> bool:
    if item.event == "merged":
        return _note_merge_leg(store=store, core=core, fill_key=item.fill_key)
    fill = store.fill_for_key(item.fill_key)
    if fill is None:
        return False
    if item.event == "failed":
        core.note_recovery(now_ns=core_now_ns())
        return True
    if item.event not in ("matched", "confirmed"):
        return False
    _clob, venue_id = split_fill_key(fill.trade_id)
    token_index = core.token_index(fill.token_id)
    if token_index is None:
        return False
    core.note_fill(
        fill_key=fill.trade_id,
        venue_id=venue_id,
        qty=fill.size,
        price=fill.price,
        now_ns=core_now_ns(),
        token_index=token_index,
        side="BUY" if fill.side.value == "BUY" else "SELL",
    )
    return True
