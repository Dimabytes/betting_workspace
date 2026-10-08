"""SQLite checkpoints, bindings, and independent outbox cursors."""

# pyright: reportPrivateUsage=false

import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal, get_args

import pytest
from polymaker.domain import Fill, Side

from shared.constants.strategy import LIVE_DOTA_MAX_POSITION_LEVELS
from strategy.lifecycle import already_canceling, empty_state
from strategy.policy import follow300_policy
from strategy.types import (
    BlockReason,
    Budget,
    EpisodeArchive,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    OrderStatus,
    Permissions,
    RestingOrder,
    Rung,
    TokenInventory,
)
from trader.core_persistence import (
    _RESERVED_BUY_SQL,
    CORE_SCHEMA_VERSION,
    UNSETTLED_CANCEL_REASON,
    CommandKind,
    CommandOutcome,
    CoreCommand,
    CoreSchemaError,
    CoreSessionKey,
    DispatchState,
    OrderBinding,
    _require_status,
    apply_checkpoint,
    decode_checkpoint,
    encode_checkpoint,
    get_session,
    insert_unsettled_buy,
    list_bindings,
    list_commands,
    list_prepared_places,
    migrate_core_schema,
    open_unsettled_buys,
    outbox_after,
    prove_unsettled_buy,
    rebase_delay_ns,
    reserved_buy_notional,
    resolve_settled_buys,
    resolved_unsettled_buys,
    restore_inventory,
    restore_orders,
    snapshot_checkpoint,
    unsettled_buy_notional,
    upsert_binding,
    upsert_command,
    upsert_session,
)
from trader.core_session_io import SessionIdentity, consume_core_outbox
from trader.session_core import LiveCore
from trader.wallet_store import WalletStateStore

YES = "yes-token"
NO = "no-token"


def _key() -> CoreSessionKey:
    return CoreSessionKey(
        condition_id="0xcond",
        game="dota",
        yes_token=YES,
        no_token=NO,
        yes_is_radiant=True,
    )


def _state():
    state = empty_state(
        limits=MarketLimits(
            min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05, radiant_token_index=0
        ),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        permissions=Permissions(
            halt=False, reduce_only=False, allow_buy=True, allow_sell=True, sell_unconfirmed=False
        ),
        budget=Budget(
            cash_usdc=1000.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        clock=GameClock(now_ns=0, game_second=10, paused=False, game_ended=False),
    )
    return replace(
        state,
        episode_id=1,
        episode_counter=1,
        episode_token_index=0,
        rungs=(Rung(index=0, price=0.50, filled_qty=0.0, live_id="c0", done=False),),
        orders=(
            RestingOrder(
                order_id="c0",
                episode_id=1,
                token_index=0,
                side="BUY",
                price=0.50,
                submitted_qty=40.0,
                filled_qty=0.0,
                level_index=0,
                status="live",
                accepted=True,
                partially_filled=False,
                cancel_reason="",
                ack_reason="",
                placed_ns=0,
                accepted_ns=0,
            ),
        ),
        next_order_seq=1,
    )


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    migrate_core_schema(store._conn)
    migrate_core_schema(store._conn)
    row = store._conn.execute("PRAGMA synchronous").fetchone()
    assert int(row[0]) == 2
    store.close()
    again = WalletStateStore(tmp_path / "w.db")
    assert get_session(again._conn, "0xcond") is None
    again.close()


def test_binding_and_rung_move_restore(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    state = replace(_state(), episode_buy_notional=12.0)
    checkpoint = snapshot_checkpoint(
        state=state, now_ns=1_000_000_000, now_wall_s=100.0, sell_min_life_s=1.0
    )
    upsert_session(
        store._conn,
        session_id="0xcond",
        key=_key(),
        revision=1,
        recovery=True,
        recovery_generation=1,
        checkpoint=checkpoint,
        last_outbox_seq=0,
    )
    upsert_binding(
        store._conn,
        OrderBinding(
            session_id="0xcond",
            core_order_id="c0",
            venue_id="v1",
            episode_id=1,
            token_index=0,
            side="BUY",
            submitted_qty=40.0,
            level_index=1,
            terminal=False,
        ),
    )
    store._conn.commit()
    loaded = get_session(store._conn, "0xcond")
    assert loaded is not None
    assert loaded.checkpoint.rungs[0].live_id == "c0"
    assert loaded.checkpoint.episode_buy_notional == 12.0
    moved = replace(list_bindings(store._conn, "0xcond")[0], level_index=2, venue_id="v1")
    upsert_binding(store._conn, moved)
    store._conn.commit()
    assert list_bindings(store._conn, "0xcond")[0].level_index == 2
    store.close()


def test_terminal_binding_is_retained(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    upsert_binding(
        store._conn,
        OrderBinding(
            session_id="0xcond",
            core_order_id="c0",
            venue_id="v-old",
            episode_id=3,
            token_index=1,
            side="BUY",
            submitted_qty=20.0,
            level_index=2,
            terminal=True,
        ),
    )
    store._conn.commit()
    binding = list_bindings(store._conn, "0xcond")[0]
    assert binding.terminal is True
    assert binding.episode_id == 3
    assert binding.venue_id == "v-old"
    store.close()


def test_qualified_ids_do_not_collide(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    upsert_command(
        store._conn,
        CoreCommand(
            command_id="cid-a:b0:c0",
            session_id="cid-a",
            revision=1,
            batch_id="b0",
            kind="place",
            core_order_id="c0",
            token_index=0,
            side="BUY",
            price=0.50,
            quantity=40.0,
            venue_id=None,
            order_hash="h1",
            dispatch_state="prepared",
            outcome="",
            consumed=False,
        ),
    )
    upsert_command(
        store._conn,
        CoreCommand(
            command_id="cid-b:b0:c0",
            session_id="cid-b",
            revision=1,
            batch_id="b0",
            kind="place",
            core_order_id="c0",
            token_index=0,
            side="BUY",
            price=0.40,
            quantity=50.0,
            venue_id=None,
            order_hash="h2",
            dispatch_state="prepared",
            outcome="",
            consumed=False,
        ),
    )
    store._conn.commit()
    assert reserved_buy_notional(store._conn) == pytest.approx(40.0)
    store.close()


def _command(
    command_id: str,
    session_id: str,
    kind: CommandKind,
    side: Literal["BUY", "SELL"] | None,
    dispatch_state: DispatchState,
    outcome: CommandOutcome,
    consumed: bool,
    price: float,
    quantity: float,
) -> CoreCommand:
    return CoreCommand(
        command_id=command_id,
        session_id=session_id,
        revision=1,
        batch_id="b0",
        kind=kind,
        core_order_id=command_id,
        token_index=0,
        side=side,
        price=price,
        quantity=quantity,
        venue_id=None,
        order_hash=None,
        dispatch_state=dispatch_state,
        outcome=outcome,
        consumed=consumed,
    )


def test_reserved_buy_notional_counts_only_open_buys(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    counted = (
        _command("s1:prepared", "s1", "place", "BUY", "prepared", "", False, 0.50, 40.0),
        _command("s1:started", "s1", "place", "BUY", "dispatch_started", "", False, 0.40, 25.0),
        _command("s1:dispatched", "s1", "place", "BUY", "dispatched", "", False, 0.20, 15.0),
        _command("s1:null-outcome", "s1", "place", "BUY", "prepared", "", False, 0.10, 50.0),
        _command("s2:prepared", "s2", "place", "BUY", "prepared", "", False, 0.30, 10.0),
    )
    skipped = (
        _command("s1:sell", "s1", "place", "SELL", "prepared", "", False, 0.60, 100.0),
        _command("s1:cancel", "s1", "cancel", "BUY", "prepared", "", False, 0.70, 100.0),
        _command("s1:not-sent", "s1", "place", "BUY", "known_not_sent", "", False, 0.50, 100.0),
        _command("s1:consumed", "s1", "place", "BUY", "prepared", "", True, 0.50, 100.0),
        _command("s1:accepted", "s1", "place", "BUY", "prepared", "accepted", False, 0.50, 100.0),
        _command("s1:unknown", "s1", "place", "BUY", "dispatched", "unknown", False, 0.50, 100.0),
    )
    for command in (*counted, *skipped):
        upsert_command(store._conn, command)
    store._conn.execute(
        "UPDATE core_commands SET outcome=NULL WHERE command_id=?",
        ("s1:null-outcome",),
    )
    store._conn.commit()
    assert reserved_buy_notional(store._conn) == pytest.approx(20.0 + 10.0 + 3.0 + 5.0 + 3.0)
    store.close()


def test_list_prepared_places_matches_python_filter(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    rows = (
        _command("s1:buy", "s1", "place", "BUY", "prepared", "", False, 0.50, 40.0),
        _command("s1:sell", "s1", "place", "SELL", "prepared", "", False, 0.60, 10.0),
        _command("s1:started", "s1", "place", "BUY", "dispatch_started", "", False, 0.40, 10.0),
        _command("s1:dispatched", "s1", "place", "BUY", "dispatched", "", False, 0.30, 10.0),
        _command("s1:not-sent", "s1", "place", "BUY", "known_not_sent", "", True, 0.20, 10.0),
        _command("s1:cancel", "s1", "cancel", "BUY", "prepared", "", False, 0.70, 10.0),
        _command("s2:buy", "s2", "place", "BUY", "prepared", "", False, 0.30, 10.0),
    )
    for command in rows:
        upsert_command(store._conn, command)
    store._conn.commit()
    loaded = list_commands(store._conn, "s1")
    expected = tuple(
        command
        for command in loaded
        if command.kind == "place" and command.dispatch_state == "prepared"
    )
    assert list_prepared_places(store._conn, "s1") == expected
    assert [command.command_id for command in expected] == ["s1:buy", "s1:sell"]
    store.close()


def test_open_buy_index_serves_reserved_notional(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    names = {
        str(row["name"])
        for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "core_commands_open_buy" in names
    plan = store._conn.execute(f"EXPLAIN QUERY PLAN {_RESERVED_BUY_SQL}").fetchall()
    assert any("core_commands_open_buy" in str(row["detail"]) for row in plan)
    store.close()


def test_v1_settle_timer_follows_position_token() -> None:
    raw = (
        '{"schema_version":1,"policy_version":"follow300-v1","episode_id":1,'
        '"episode_counter":1,"episode_token_index":1,"has_buy_fill":true,'
        '"winding_down":false,"recovery_generation":0,'
        '"timing":{"last_buy_remaining_s":8.0,"checkpoint_wall_s":100.0},'
        '"orders":[],"seen_fill_ids":[],'
        '"position":{"token_index":1,"qty":20.0,"cost_basis":10.0},'
        '"next_order_seq":1}'
    )
    restored = decode_checkpoint(raw)
    assert restored.inventory[0].last_buy_remaining_s is None
    assert restored.inventory[1].last_buy_remaining_s == pytest.approx(8.0)
    assert restored.inventory[1].qty == pytest.approx(20.0)
    tokens = restore_inventory(checkpoint=restored, now_ns=0, now_wall_s=100.0)
    assert tokens[0].last_buy_ns is None
    assert tokens[1].last_buy_ns is not None


def test_v1_checkpoint_forces_recovery_drain() -> None:
    state = _state()
    checkpoint = snapshot_checkpoint(state=state, now_ns=0, now_wall_s=1.0, sell_min_life_s=1.0)
    raw = encode_checkpoint(checkpoint).replace(
        f'"schema_version":{CORE_SCHEMA_VERSION}', '"schema_version":1'
    )
    restored = decode_checkpoint(raw)
    assert restored.sell_only is True
    assert restored.recovery_pending is True
    assert restored.rungs == ()


def test_unsupported_schema_cannot_enable_buy(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    state = _state()
    checkpoint = snapshot_checkpoint(state=state, now_ns=0, now_wall_s=1.0, sell_min_life_s=1.0)
    raw = encode_checkpoint(checkpoint).replace(
        f'"schema_version":{CORE_SCHEMA_VERSION}', '"schema_version":99'
    )
    with pytest.raises(CoreSchemaError):
        decode_checkpoint(raw)
    store.close()


def test_clock_going_backwards_keeps_remaining_delay() -> None:
    rebased = rebase_delay_ns(
        now_ns=5_000_000_000,
        remaining_s=4.0,
        checkpoint_wall_s=100.0,
        now_wall_s=90.0,
    )
    assert rebased is not None


def test_outbox_cursors_are_independent(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    store.apply_matched_fill(Fill(YES, Side.BUY, 0.40, 10.0, "t1:v1", 1.0, is_maker=True), "t1:v1")
    store.apply_matched_fill(Fill(NO, Side.BUY, 0.40, 10.0, "t2:v2", 1.0, is_maker=True), "t2:v2")
    yes_rows = outbox_after(store._conn, after_seq=0, tokens=frozenset({YES}))
    no_rows = outbox_after(store._conn, after_seq=0, tokens=frozenset({NO}))
    assert len(yes_rows) == 1
    assert len(no_rows) == 1
    assert yes_rows[0][1] != no_rows[0][1]
    after_yes = outbox_after(store._conn, after_seq=yes_rows[0][0], tokens=frozenset({YES}))
    assert after_yes == ()
    store.close()


def test_journal_acked_rows_still_reach_stale_cursor(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    store.apply_matched_fill(Fill(YES, Side.BUY, 0.40, 10.0, "t1:v1", 1.0, is_maker=True), "t1:v1")
    pending = store.pending_outbox()
    store.ack_outbox(pending[0].seq)
    rows = store.core_outbox_after(after_seq=0, tokens=frozenset({YES}))
    assert rows
    assert rows[0].event == "matched"
    store.close()


def _live_core() -> LiveCore:
    return LiveCore(
        policy=follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=MarketLimits(
            min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05, radiant_token_index=0
        ),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        yes_token=YES,
        no_token=NO,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )


def test_outbox_consume_advances_cursor_only_after_apply(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    core = _live_core()
    identity = SessionIdentity(session_id="0xcond", key=_key())
    store.apply_matched_fill(Fill(YES, Side.BUY, 0.40, 10.0, "t1:v1", 1.0, is_maker=True), "t1:v1")
    consume_core_outbox(store=store, core=core, identity=identity, tokens=frozenset({YES}))
    row = get_session(store._conn, "0xcond")
    assert row is not None
    assert row.last_outbox_seq == core.last_outbox_seq
    assert core.last_outbox_seq > 0
    assert core.state.seen_fill_ids or core.state.pending_ownership
    store.close()


def test_outbox_consume_applies_merge_without_recovery(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    core = _live_core()
    identity = SessionIdentity(session_id="0xcond", key=_key())
    tokens = frozenset({YES, NO})
    store.apply_confirmed_fill(
        Fill(YES, Side.BUY, 0.40, 30.0, "t1:v1", 1.0, is_maker=True), "t1:v1"
    )
    store.apply_confirmed_fill(Fill(NO, Side.BUY, 0.55, 20.0, "t2:v2", 1.0, is_maker=True), "t2:v2")
    core.last_outbox_seq = store.core_outbox_after(after_seq=0, tokens=tokens)[-1].seq
    core.note_recovery(now_ns=1)
    core.drain_apply()
    core.note_recovery_verified(
        now_ns=2,
        generation=core.state.recovery_generation,
        inventory=(
            TokenInventory(token_index=0, qty=30.0, cost_basis=12.0, last_buy_ns=None),
            TokenInventory(token_index=1, qty=20.0, cost_basis=11.0, last_buy_ns=None),
        ),
    )
    core.drain_apply()
    before = core.state
    assert before.recovery_pending is False
    assert before.sell_only is True
    assert before.recovery_generation == 1

    assert store.apply_merge(tx_hash="0xmerge1", token_ids=(YES, NO), qty=20.0) is True
    consume_core_outbox(store=store, core=core, identity=identity, tokens=tokens)

    yes, no = core.state.inventory
    assert yes.qty == 10.0
    assert yes.cost_basis == pytest.approx(4.0)
    assert no == TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None)
    assert yes.qty == store.position(YES).size
    assert no.qty == store.position(NO).size
    assert core.state.recovery_pending is False
    assert core.state.sell_only is True
    assert core.state.recovery_generation == 1
    assert core.state.orders == before.orders
    rows = store.core_outbox_after(after_seq=0, tokens=tokens)
    assert [row.event for row in rows[-2:]] == ["merged", "merged"]
    assert core.last_outbox_seq == rows[-1].seq
    session = get_session(store._conn, "0xcond")
    assert session is not None
    assert session.last_outbox_seq == rows[-1].seq

    consume_core_outbox(store=store, core=core, identity=identity, tokens=tokens)
    assert core.state.inventory == (yes, no)
    store.close()


def test_session_upsert_updates_schema_and_policy(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    checkpoint = snapshot_checkpoint(
        state=replace(_state(), episode_buy_notional=12.0),
        now_ns=0,
        now_wall_s=1.0,
        sell_min_life_s=1.0,
    )
    legacy = replace(checkpoint, schema_version=2, policy_version="follow300-v4")
    for revision, current in enumerate((legacy, checkpoint), start=1):
        upsert_session(
            store._conn,
            session_id="0xcond",
            key=_key(),
            revision=revision,
            recovery=True,
            recovery_generation=1,
            checkpoint=current,
            last_outbox_seq=0,
        )
        store._conn.commit()
    loaded = get_session(store._conn, "0xcond")
    assert loaded is not None
    assert loaded.schema_version == checkpoint.schema_version
    assert loaded.policy_version == checkpoint.policy_version
    assert loaded.checkpoint.episode_buy_notional == 12.0
    store.close()


PRICE = 0.5


def _seed(
    conn: sqlite3.Connection,
    venue_id: str,
    *,
    session_id: str = "s1",
    token_id: str = "tok",
    price: float = PRICE,
    qty: float = 8.0,
) -> None:
    insert_unsettled_buy(
        conn,
        venue_id=venue_id,
        session_id=session_id,
        token_id=token_id,
        price=price,
        qty=qty,
    )


def _buy(
    key: str, size: float, *, token: str = "tok", ts: float = 10.0, side: Side = Side.BUY
) -> Fill:
    return Fill(token, side, PRICE, size, key, ts, is_maker=True)


def _flags(store: WalletStateStore, venue_id: str) -> tuple[float, bool, bool]:
    row = store._conn.execute(
        "SELECT qty, proven, resolved FROM unsettled_buys WHERE venue_id=?",
        (venue_id,),
    ).fetchone()
    assert row is not None
    return float(row["qty"]), bool(row["proven"]), bool(row["resolved"])


def test_unsettled_buys_survive_reopen(tmp_path: Path) -> None:
    path = tmp_path / "w.db"
    store = WalletStateStore(path)
    migrate_core_schema(store._conn)
    migrate_core_schema(store._conn)
    names = {
        str(row["name"])
        for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "unsettled_buys_resolved_session" in names
    info = store._conn.execute("PRAGMA index_info(unsettled_buys_resolved_session)").fetchall()
    assert [str(row["name"]) for row in info] == ["resolved", "session_id"]
    assert open_unsettled_buys(store._conn) == ()
    assert resolved_unsettled_buys(store._conn, "s1") == {}
    assert unsettled_buy_notional(store._conn, None) == 0.0
    _seed(store._conn, "v1")
    store._conn.commit()
    store.close()
    again = WalletStateStore(path)
    assert open_unsettled_buys(again._conn)[0].venue_id == "v1"
    assert unsettled_buy_notional(again._conn, "s1") == pytest.approx(4.0)
    again.close()


def test_matched_failure_returns_unsettled_reserve(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "v1")
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(4.0)
    fill = _buy("t1:v1", 8.0)
    assert store.apply_matched_fill(fill, "t1:v1") is True
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(0.0)
    assert resolve_settled_buys(store._conn) == ()
    opened = open_unsettled_buys(store._conn)[0]
    assert opened.resolved is False
    assert opened.qty == pytest.approx(8.0)
    assert store.apply_failed_fill(fill, "t1:v1") is True
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(4.0)
    assert open_unsettled_buys(store._conn)[0].qty == pytest.approx(8.0)
    assert store.position("tok").size == pytest.approx(0.0)
    store.close()


def test_confirmed_resolves_unsettled_buy_once(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "v1")
    fill = _buy("t1:v1", 8.0)
    store.apply_matched_fill(fill, "t1:v1")
    confirmed = store.apply_confirmed_fill(fill, "t1:v1")
    assert confirmed.confirmed is True
    booked = store._conn.execute(
        "SELECT COALESCE(SUM(size), 0) FROM fill_ledger "
        "WHERE maker_order_id=? AND side='BUY' AND status IN ('MATCHED', 'CONFIRMED')",
        ("v1",),
    ).fetchone()
    assert float(booked[0]) == pytest.approx(8.0)
    assert resolve_settled_buys(store._conn) == ("v1",)
    assert resolve_settled_buys(store._conn) == ()
    assert unsettled_buy_notional(store._conn, None) == 0.0
    assert resolved_unsettled_buys(store._conn, "s1") == {"v1": 8.0}
    assert open_unsettled_buys(store._conn) == ()
    store.close()


def test_partial_proof_waits_for_final_total(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "v1")
    store.apply_confirmed_fill(_buy("t1:v1", 3.0), "t1:v1")
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(2.5)
    prove_unsettled_buy(store._conn, "v1", 5.0)
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(1.0)
    assert resolve_settled_buys(store._conn) == ()
    store.apply_confirmed_fill(_buy("t2:v1", 2.0, ts=11.0), "t2:v1")
    assert resolve_settled_buys(store._conn) == ("v1",)
    assert resolved_unsettled_buys(store._conn, "s1") == {"v1": 5.0}
    store.close()


def test_proof_zero_resolves_without_fills(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "v1")
    prove_unsettled_buy(store._conn, "v1", 0.0)
    opened = open_unsettled_buys(store._conn)[0]
    assert opened.proven is True
    assert opened.qty == pytest.approx(0.0)
    assert unsettled_buy_notional(store._conn, None) == 0.0
    assert resolve_settled_buys(store._conn) == ("v1",)
    assert resolved_unsettled_buys(store._conn, "s1") == {"v1": 0.0}
    store.close()


def test_full_confirmed_resolves_without_proof(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "v1")
    store.apply_confirmed_fill(_buy("t1:v1", 8.0), "t1:v1")
    assert resolve_settled_buys(store._conn) == ("v1",)
    qty, proven, resolved = _flags(store, "v1")
    assert qty == pytest.approx(8.0)
    assert proven is False
    assert resolved is True
    store.close()


def test_superseded_fill_does_not_release_reserve(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "v1")
    store.note_writedown_snapshot("tok", 100.0)
    fill = _buy("t1:v1", 8.0, ts=50.0)
    assert store.apply_matched_fill(fill, "t1:v1") is False
    assert store.ledger_status("t1:v1") == "SUPERSEDED"
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(4.0)
    assert resolve_settled_buys(store._conn) == ()
    assert open_unsettled_buys(store._conn)[0].proven is False
    store.close()


def test_sell_and_other_maker_stay_isolated(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "v1")
    _seed(store._conn, "v2", qty=4.0)
    store.set_position("tok", 8.0, PRICE)
    store.apply_confirmed_fill(_buy("sell:v1", 8.0, side=Side.SELL), "sell:v1")
    store.apply_confirmed_fill(_buy("same-trade:v2", 4.0, ts=11.0), "same-trade:v2")
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(4.0)
    assert resolve_settled_buys(store._conn) == ("v2",)
    assert open_unsettled_buys(store._conn)[0].venue_id == "v1"
    store.close()


def test_overfill_floors_only_that_row(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "v1")
    _seed(store._conn, "v2")
    store.apply_confirmed_fill(_buy("t1:v1", 10.0), "t1:v1")
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(4.0)
    store.close()


def test_duplicate_insert_preserves_proven_and_resolved_rows(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "v1", session_id="s1", token_id="tok", price=PRICE, qty=8.0)
    time.sleep(0.01)
    prove_unsettled_buy(store._conn, "v1", 5.0)
    proven = open_unsettled_buys(store._conn)[0]
    assert proven.updated_at > proven.created_at
    _seed(store._conn, "v1", session_id="other", token_id="nope", price=0.1, qty=99.0)
    assert open_unsettled_buys(store._conn) == (proven,)
    store.apply_confirmed_fill(_buy("t1:v1", 5.0), "t1:v1")
    assert resolve_settled_buys(store._conn) == ("v1",)
    settled = _flags(store, "v1")
    _seed(store._conn, "v1", session_id="other", token_id="nope", price=0.1, qty=99.0)
    assert _flags(store, "v1") == settled
    kept = store._conn.execute("SELECT * FROM unsettled_buys WHERE venue_id='v1'").fetchone()
    assert str(kept["session_id"]) == "s1"
    assert str(kept["token_id"]) == "tok"
    assert float(kept["price"]) == pytest.approx(PRICE)
    assert float(kept["created_at"]) == pytest.approx(proven.created_at)
    assert float(kept["updated_at"]) > proven.updated_at
    store.close()


def test_proof_and_resolve_do_not_insert_or_reopen(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    prove_unsettled_buy(store._conn, "missing", 1.0)
    assert open_unsettled_buys(store._conn) == ()
    _seed(store._conn, "v1")
    prove_unsettled_buy(store._conn, "v1", 4.0)
    prove_unsettled_buy(store._conn, "v1", 4.0)
    opened = open_unsettled_buys(store._conn)
    assert len(opened) == 1
    assert opened[0].qty == pytest.approx(4.0)
    assert opened[0].proven is True
    store.apply_confirmed_fill(_buy("t1:v1", 4.0), "t1:v1")
    assert resolve_settled_buys(store._conn) == ("v1",)
    assert resolve_settled_buys(store._conn) == ()
    prove_unsettled_buy(store._conn, "v1", 99.0)
    qty, proven, resolved = _flags(store, "v1")
    assert qty == pytest.approx(4.0)
    assert proven is True
    assert resolved is True
    store.close()


def test_unsettled_buys_span_sessions_without_a_core_row(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "b1", session_id="b", qty=4.0)
    _seed(store._conn, "a2", session_id="a", qty=2.0)
    _seed(store._conn, "a1", session_id="a", qty=8.0)
    for venue_id, created_at in (("b1", 1.0), ("a2", 2.0), ("a1", 3.0)):
        store._conn.execute(
            "UPDATE unsettled_buys SET created_at=?, updated_at=? WHERE venue_id=?",
            (created_at, created_at, venue_id),
        )
    prove_unsettled_buy(store._conn, "a2", 2.0)
    assert unsettled_buy_notional(store._conn, "a") == pytest.approx(5.0)
    assert unsettled_buy_notional(store._conn, "b") == pytest.approx(2.0)
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(7.0)
    assert [row.venue_id for row in open_unsettled_buys(store._conn)] == ["b1", "a2", "a1"]
    assert get_session(store._conn, "a") is None
    store.apply_confirmed_fill(_buy("t1:a1", 8.0), "t1:a1")
    store.apply_confirmed_fill(_buy("t1:b1", 4.0, ts=11.0), "t1:b1")
    assert resolve_settled_buys(store._conn) == ("a1", "b1")
    assert resolved_unsettled_buys(store._conn, "a") == {"a1": 8.0}
    assert resolved_unsettled_buys(store._conn, "b") == {"b1": 4.0}
    assert [row.venue_id for row in open_unsettled_buys(store._conn)] == ["a2"]
    store.close()


def test_confirmation_within_half_tick_resolves(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store._conn, "short")
    _seed(store._conn, "near")
    store.apply_confirmed_fill(_buy("t1:short", 7.99), "t1:short")
    store.apply_confirmed_fill(_buy("t1:near", 8.0 - 0.004, ts=11.0), "t1:near")
    assert resolve_settled_buys(store._conn) == ("near",)
    assert open_unsettled_buys(store._conn)[0].venue_id == "short"
    store.close()


def _gone_holding_state():
    return replace(
        _state(),
        episode_id=7,
        episode_counter=4,
        episode_token_index=0,
        has_buy_fill=True,
        episode_buy_notional=15.5,
        sell_only=True,
        recovery_pending=False,
        rungs=(
            Rung(
                index=0,
                price=0.50,
                filled_qty=3.0,
                live_id="c0",
                done=False,
                held_qty=16.0,
                held_cost=6.0,
            ),
        ),
        orders=(
            RestingOrder(
                order_id="c0",
                episode_id=7,
                token_index=0,
                side="BUY",
                price=0.50,
                submitted_qty=40.0,
                filled_qty=3.0,
                level_index=0,
                status="gone",
                accepted=True,
                partially_filled=True,
                cancel_reason="reprice",
                ack_reason="reprice",
                placed_ns=0,
                accepted_ns=0,
            ),
        ),
        inventory=(
            TokenInventory(token_index=0, qty=10.0, cost_basis=4.0, last_buy_ns=None),
            TokenInventory(token_index=1, qty=6.0, cost_basis=2.0, last_buy_ns=None),
        ),
        archives=(
            EpisodeArchive(
                episode_id=3,
                token_index=1,
                rungs=(),
                has_buy_fill=True,
                episode_buy_notional=9.0,
                winding_down=False,
            ),
        ),
    )


def _resting(order_id: str, status: OrderStatus, reason: str) -> RestingOrder:
    return RestingOrder(
        order_id=order_id,
        episode_id=1,
        token_index=0,
        side="BUY",
        price=0.50,
        submitted_qty=40.0,
        filled_qty=0.0,
        level_index=0,
        status=status,
        accepted=True,
        partially_filled=False,
        cancel_reason=reason,
        ack_reason="",
        placed_ns=0,
        accepted_ns=0,
    )


def test_gone_checkpoint_roundtrip() -> None:
    state = _gone_holding_state()
    checkpoint = snapshot_checkpoint(state=state, now_ns=0, now_wall_s=100.0, sell_min_life_s=1.0)
    assert state.orders[0].status == "gone"
    assert state.orders[0].cancel_reason == "reprice"
    stored = checkpoint.orders[0]
    assert stored.status == "unknown"
    assert stored.cancel_reason == UNSETTLED_CANCEL_REASON
    assert stored.ack_reason == "reprice"
    assert stored.submitted_qty == pytest.approx(40.0)
    assert stored.filled_qty == pytest.approx(3.0)
    assert checkpoint.schema_version == CORE_SCHEMA_VERSION == 3
    decoded = decode_checkpoint(encode_checkpoint(checkpoint))
    assert decoded.orders[0].status == "unknown"
    assert decoded.orders[0].cancel_reason == UNSETTLED_CANCEL_REASON
    restored = apply_checkpoint(
        empty_state(
            limits=state.limits,
            freshness=state.freshness,
            permissions=state.permissions,
            budget=state.budget,
            clock=state.clock,
        ),
        checkpoint=decoded,
        now_ns=0,
        now_wall_s=100.0,
        sell_min_life_s=1.0,
    )
    order = restored.orders[0]
    assert order.status == "gone"
    assert order.cancel_reason == UNSETTLED_CANCEL_REASON
    assert order.ack_reason == "reprice"
    assert order.submitted_qty == pytest.approx(40.0)
    assert order.filled_qty == pytest.approx(3.0)
    assert restored.rungs[0].live_id == "c0"
    assert restored.episode_id == 7
    assert restored.episode_counter == 4
    assert restored.episode_buy_notional == pytest.approx(15.5)
    assert restored.sell_only is True
    assert restored.recovery_pending is False
    assert restored.inventory[0].qty == pytest.approx(10.0)
    assert restored.inventory[0].cost_basis == pytest.approx(4.0)
    assert restored.inventory[1].qty == pytest.approx(6.0)
    assert restored.inventory[1].cost_basis == pytest.approx(2.0)
    assert restored.archives[0].episode_id == 3
    assert restored.archives[0].episode_buy_notional == pytest.approx(9.0)


def test_ordinary_checkpoint_statuses_stay() -> None:
    state = replace(
        _state(),
        orders=(
            _resting("p", "pending", ""),
            _resting("l", "live", ""),
            _resting("c", "canceling", "kill"),
            _resting("u", "unknown", ""),
            _resting("k", "unknown", "kill"),
        ),
    )
    checkpoint = snapshot_checkpoint(state=state, now_ns=0, now_wall_s=100.0, sell_min_life_s=1.0)
    restored = restore_orders(
        checkpoint=checkpoint, now_ns=0, now_wall_s=100.0, sell_min_life_s=1.0
    )
    assert [(order.order_id, order.status, order.cancel_reason) for order in restored] == [
        ("p", "pending", ""),
        ("l", "live", ""),
        ("c", "canceling", "kill"),
        ("u", "unknown", ""),
        ("k", "unknown", "kill"),
    ]
    raw_gone = replace(checkpoint, orders=(replace(checkpoint.orders[0], status="gone"),))
    with pytest.raises(CoreSchemaError):
        restore_orders(checkpoint=raw_gone, now_ns=0, now_wall_s=100.0, sell_min_life_s=1.0)


def test_unsettled_reason_is_reserved() -> None:
    assert UNSETTLED_CANCEL_REASON not in get_args(BlockReason)
    assert UNSETTLED_CANCEL_REASON not in {"reprice", "kill", "recovery"}


def test_gone_checkpoint_loads_with_old_status_set(monkeypatch: pytest.MonkeyPatch) -> None:
    def _legacy_status(status: str, cancel_reason: str):
        del cancel_reason
        return _require_status(status)

    monkeypatch.setattr("trader.core_persistence._decode_checkpoint_status", _legacy_status)
    state = _gone_holding_state()
    checkpoint = snapshot_checkpoint(state=state, now_ns=0, now_wall_s=100.0, sell_min_life_s=1.0)
    restored = apply_checkpoint(
        empty_state(
            limits=state.limits,
            freshness=state.freshness,
            permissions=state.permissions,
            budget=state.budget,
            clock=state.clock,
        ),
        checkpoint=checkpoint,
        now_ns=0,
        now_wall_s=100.0,
        sell_min_life_s=1.0,
    )
    order = restored.orders[0]
    assert order.status == "unknown"
    assert order.cancel_reason == UNSETTLED_CANCEL_REASON
    assert already_canceling(order) is True
    assert restored.episode_id == 7
    assert restored.episode_counter == 4
    assert restored.episode_buy_notional == pytest.approx(15.5)
    assert restored.sell_only is True
    assert restored.inventory[0].qty == pytest.approx(10.0)
    assert restored.inventory[1].cost_basis == pytest.approx(2.0)
    assert restored.archives[0].episode_buy_notional == pytest.approx(9.0)
