"""Recovery classification, verification, and inventory selection."""

# pyright: reportPrivateUsage=false

import asyncio
import time
from dataclasses import replace
from pathlib import Path

import pytest
from polymaker.domain import Fill, Side

from shared.constants.strategy import LIVE_DOTA_MAX_POSITION_LEVELS
from strategy.engine import step
from strategy.lifecycle import apply_cancel_unsettled, empty_state
from strategy.policy import extraction_policy, follow300_policy
from strategy.types import (
    Budget,
    CancelAck,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    OrderRecord,
    Permissions,
    Position,
    Recovery,
    RecoveryVerified,
    RestingOrder,
    Rung,
    TokenInventory,
)
from trader.chain_balances import ChainSnapshot, fresh_token_balances
from trader.core_persistence import (
    CoreCommand,
    CoreSessionKey,
    get_session,
    insert_unsettled_buy,
    snapshot_checkpoint,
    unsettled_buy_notional,
    upsert_command,
    upsert_session,
)
from trader.core_recovery import (
    RecoveryCoordinator,
    VerifiedInventory,
    classify_session,
    prove_token_balances,
    select_sellable,
    should_reverify,
)
from trader.core_session_io import (
    SessionIdentity,
    consume_core_outbox,
    load_core_snapshot,
    persist_core_snapshot,
)
from trader.session_core import LiveCore
from trader.wallet_store import WalletStateStore

YES = "yes-token"
NO = "no-token"


def test_stale_chain_snapshot_does_not_prove_recovery(tmp_path: Path) -> None:
    """Sqlite already at zero must not be 'proved' by a block older than the floor."""
    store = WalletStateStore(tmp_path / "wallet.db")
    now = time.time()
    stale = ChainSnapshot(1, now - 31.0, {YES: 0.0, NO: 0.0})
    fresh = ChainSnapshot(2, now, {YES: 0.0, NO: 0.0})

    async def read_stale(tokens: list[str]) -> dict[str, float] | None:
        return fresh_token_balances(stale, tokens, store.chain_read_floor, now)

    async def read_fresh(tokens: list[str]) -> dict[str, float] | None:
        return fresh_token_balances(fresh, tokens, store.chain_read_floor, now)

    assert (
        asyncio.run(
            prove_token_balances(
                read_balances=read_stale,
                yes_token=YES,
                no_token=NO,
                store=store,
                revision_of=lambda: 0,
            )
        )
        is None
    )
    assert asyncio.run(
        prove_token_balances(
            read_balances=read_fresh,
            yes_token=YES,
            no_token=NO,
            store=store,
            revision_of=lambda: 0,
        )
    ) == (0.0, 0.0)
    store.close()


def _key() -> CoreSessionKey:
    return CoreSessionKey(
        condition_id="0xcond",
        game="dota",
        yes_token=YES,
        no_token=NO,
        yes_is_radiant=True,
    )


def _limits() -> MarketLimits:
    return MarketLimits(
        min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05, radiant_token_index=0
    )


def test_orphaned_inventory_selects_recovery(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    classified = classify_session(store._conn, key=_key(), has_inventory=True, has_orders=False)
    assert classified.kind == "recovery"
    assert classified.reason == "legacy"
    store.close()


def _checkpoint_row(store: WalletStateStore) -> None:
    state = empty_state(
        limits=_limits(),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        permissions=Permissions(
            halt=False, reduce_only=False, allow_buy=True, allow_sell=True, sell_unconfirmed=False
        ),
        budget=Budget(
            cash_usdc=0.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        clock=GameClock(now_ns=0, game_second=0, paused=False, game_ended=False),
    )
    upsert_session(
        store._conn,
        session_id="0xcond",
        key=_key(),
        revision=1,
        recovery=True,
        recovery_generation=1,
        checkpoint=snapshot_checkpoint(state=state, now_ns=0, now_wall_s=1.0, sell_min_life_s=1.0),
        last_outbox_seq=0,
    )
    store._conn.commit()


def test_idle_checkpoint_is_fresh(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _checkpoint_row(store)
    classified = classify_session(store._conn, key=_key(), has_inventory=False, has_orders=False)
    assert classified.kind == "fresh"
    assert classified.reason == "idle_checkpoint"
    store.close()


def test_checkpoint_with_orders_selects_recovery(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _checkpoint_row(store)
    classified = classify_session(store._conn, key=_key(), has_inventory=False, has_orders=True)
    assert classified.kind == "recovery"
    assert classified.reason == "checkpoint"
    store.close()


def test_checkpoint_with_inventory_selects_recovery(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _checkpoint_row(store)
    classified = classify_session(store._conn, key=_key(), has_inventory=True, has_orders=False)
    assert classified.kind == "recovery"
    assert classified.reason == "checkpoint"
    store.close()


def test_unresolved_command_selects_recovery(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    upsert_command(
        store._conn,
        CoreCommand(
            command_id="0xcond:b0:c0",
            session_id="0xcond",
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
            dispatch_state="dispatch_started",
            outcome="unknown",
            consumed=False,
        ),
    )
    store._conn.commit()
    coordinator = RecoveryCoordinator(
        store=store,
        session_id="0xcond",
        yes_token=YES,
        no_token=NO,
        min_order_size=5.0,
    )
    classified = coordinator.classify(key=_key(), has_orders=False)
    assert classified.kind == "recovery"
    store.close()


def test_new_condition_is_fresh(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    classified = classify_session(store._conn, key=_key(), has_inventory=False, has_orders=False)
    assert classified.kind == "fresh"
    store.close()


def test_yes_no_drain_serially() -> None:
    first = select_sellable(yes_qty=10.0, no_qty=8.0, min_size=5.0)
    assert first.token_index == 0
    second = select_sellable(yes_qty=3.18, no_qty=8.0, min_size=5.0)
    assert second.token_index == 1
    dust = select_sellable(yes_qty=3.18, no_qty=0.0, min_size=5.0)
    assert dust.token_index == 0
    assert dust.qty == 3.18


def test_fill_invalidates_prior_verification() -> None:
    previous = VerifiedInventory(
        yes_qty=10.0,
        no_qty=0.0,
        selected=Position(token_index=0, qty=10.0, cost_basis=0.0),
        last_buy_ns=None,
        generation=1,
    )
    assert should_reverify(previous=previous, fill_generation=2) is True
    assert should_reverify(previous=previous, fill_generation=1) is False


def test_stale_generation_does_not_clear_pending() -> None:
    policy = extraction_policy(level_usdc=100.0)
    state = empty_state(
        limits=_limits(),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        permissions=Permissions(
            halt=False, reduce_only=False, allow_buy=True, allow_sell=True, sell_unconfirmed=False
        ),
        budget=Budget(
            cash_usdc=1000.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        clock=GameClock(now_ns=0, game_second=10, paused=False, game_ended=False),
    )
    state = replace(
        state,
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
    )
    recovered = step(
        state=state,
        policy=policy,
        event=Recovery(now_ns=0, restored_buy_ids=("c0",)),
    ).state
    acked = step(state=recovered, policy=policy, event=CancelAck(now_ns=0, order_id="c0")).state
    stale = step(
        state=acked,
        policy=policy,
        event=RecoveryVerified(
            now_ns=0,
            generation=0,
            inventory=(
                TokenInventory(token_index=0, qty=10.0, cost_basis=0.0, last_buy_ns=None),
                TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
            ),
        ),
    ).state
    assert stale.recovery_pending is True
    ok = step(
        state=acked,
        policy=policy,
        event=RecoveryVerified(
            now_ns=0,
            generation=acked.recovery_generation,
            inventory=(
                TokenInventory(token_index=0, qty=10.0, cost_basis=0.0, last_buy_ns=None),
                TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
            ),
        ),
    ).state
    assert ok.recovery_pending is False
    assert ok.sell_only is True


def test_journal_ack_does_not_clear_matched(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    fill = Fill(YES, Side.BUY, 0.40, 10.0, "t1:v1", 1.0, is_maker=True)
    store.apply_matched_fill(fill, "t1:v1")
    store.ack_outbox(store.pending_outbox()[0].seq)
    coordinator = RecoveryCoordinator(
        store=store,
        session_id="0xcond",
        yes_token=YES,
        no_token=NO,
        min_order_size=5.0,
    )
    assert coordinator.matched_open() is True
    core = LiveCore(
        policy=follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=_limits(),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        yes_token=YES,
        no_token=NO,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    core.note_recovery(now_ns=0)
    core.drain_apply()
    assert (
        coordinator.accept_if_proven(
            core=core,
            now_ns=0,
            now_wall_s=1.0,
            rest_yes=10.0,
            rest_no=0.0,
            last_buy_unix=None,
        )
        is False
    )
    assert core.state.recovery_pending is True
    store.close()


def test_cancel_then_venue_balances_clear_pending(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    store.set_position(YES, 10.0, 0.4)
    coordinator = RecoveryCoordinator(
        store=store,
        session_id="0xcond",
        yes_token=YES,
        no_token=NO,
        min_order_size=5.0,
    )
    core = LiveCore(
        policy=follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=_limits(),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        yes_token=YES,
        no_token=NO,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    core._state = replace(
        core.state,
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
    )
    core.note_recovery(now_ns=0)
    core.drain_apply()
    assert coordinator.proof_blocks(core) == "orders"
    core.note_cancel(["missing"], True, 0)
    core.bind_venue(core_id="c0", venue_id="v0")
    core.note_cancel(["v0"], True, 0)
    core.drain_apply()
    assert coordinator.proof_blocks(core) is None
    assert (
        coordinator.accept_if_proven(
            core=core,
            now_ns=0,
            now_wall_s=1.0,
            rest_yes=10.0,
            rest_no=0.0,
            last_buy_unix=None,
        )
        is True
    )
    assert core.state.recovery_pending is False
    store.close()


def test_live_sell_does_not_block_recovery_proof(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    store.set_position(YES, 10.0, 0.4)
    coordinator = RecoveryCoordinator(
        store=store,
        session_id="0xcond",
        yes_token=YES,
        no_token=NO,
        min_order_size=5.0,
    )
    core = LiveCore(
        policy=follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=_limits(),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        yes_token=YES,
        no_token=NO,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    core._state = replace(
        core.state,
        inventory=(
            TokenInventory(token_index=0, qty=10.0, cost_basis=4.0, last_buy_ns=None),
            TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
        ),
        orders=(
            RestingOrder(
                order_id="s0",
                episode_id=1,
                token_index=0,
                side="SELL",
                price=0.56,
                submitted_qty=10.0,
                filled_qty=0.0,
                level_index=None,
                status="live",
                accepted=True,
                partially_filled=False,
                cancel_reason="",
                ack_reason="",
                placed_ns=0,
                accepted_ns=0,
            ),
        ),
    )
    core.note_recovery(now_ns=0)
    core.drain_apply()
    assert coordinator.proof_blocks(core) is None
    assert (
        coordinator.accept_if_proven(
            core=core,
            now_ns=0,
            now_wall_s=1.0,
            rest_yes=10.0,
            rest_no=0.0,
            last_buy_unix=None,
        )
        is True
    )
    assert core.state.recovery_pending is False
    store.close()


def _live_core() -> LiveCore:
    return LiveCore(
        policy=follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=_limits(),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        yes_token=YES,
        no_token=NO,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )


def test_restored_gone_keeps_its_rung_through_recovery(tmp_path: Path) -> None:
    path = tmp_path / "w.db"
    store = WalletStateStore(path)
    core = _live_core()
    core._state = replace(
        core.state,
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
        records=(
            OrderRecord(
                order_id="c0",
                episode_id=1,
                token_index=0,
                side="BUY",
                price=0.50,
                submitted_qty=40.0,
                filled_qty=0.0,
                level_index=0,
                terminal=False,
                accepted=True,
                partially_filled=False,
                placed_ns=0,
                accepted_ns=0,
            ),
        ),
    )
    core.bind_venue(core_id="c0", venue_id="v1")
    core._state = apply_cancel_unsettled(state=core.state, order_id="c0")
    insert_unsettled_buy(
        store._conn,
        venue_id="v1",
        session_id="0xcond",
        token_id=YES,
        price=0.50,
        qty=40.0,
    )
    persist_core_snapshot(
        store=store,
        core=core,
        identity=SessionIdentity(session_id="0xcond", key=_key()),
    )
    store.close()
    opened = WalletStateStore(path)
    assert unsettled_buy_notional(opened._conn, "0xcond") == pytest.approx(20.0)
    fresh = _live_core()
    assert fresh.state.orders == ()
    load_core_snapshot(store=opened, core=fresh, session_id="0xcond")
    assert fresh.state.orders[0].status == "gone"
    assert fresh.state.orders[0].order_id == "c0"
    assert fresh.state.rungs[0].live_id == "c0"
    assert fresh.venue_ids() == frozenset({"v1"})
    assert unsettled_buy_notional(opened._conn, "0xcond") == pytest.approx(20.0)
    fresh.note_recovery(now_ns=0)
    fresh.drain_apply()
    assert fresh.state.orders[0].status == "gone"
    assert fresh.state.rungs[0].live_id == "c0"
    opened.close()


def test_recovery_does_not_reapply_undelivered_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    core = _live_core()
    identity = SessionIdentity(session_id="0xcond", key=_key())
    tokens = frozenset({YES, NO})
    store.apply_confirmed_fill(
        Fill(YES, Side.BUY, 0.40, 50.0, "t1:v1", 1.0, is_maker=True), "t1:v1"
    )
    store.apply_confirmed_fill(Fill(NO, Side.BUY, 0.55, 40.0, "t2:v2", 1.0, is_maker=True), "t2:v2")
    core.last_outbox_seq = store.core_outbox_after(after_seq=0, tokens=tokens)[-1].seq
    core._state = replace(
        core.state,
        inventory=(
            TokenInventory(token_index=0, qty=50.0, cost_basis=0.0, last_buy_ns=None),
            TokenInventory(token_index=1, qty=40.0, cost_basis=0.0, last_buy_ns=None),
        ),
    )
    assert store.apply_merge(tx_hash="0xmerge1", token_ids=(YES, NO), qty=20.0) is True
    snapshots = {"n": 0}

    def fail_second_snapshot(
        *, store: WalletStateStore, core: LiveCore, identity: SessionIdentity
    ) -> None:
        snapshots["n"] += 1
        if snapshots["n"] == 2:
            raise RuntimeError("snapshot failed")
        persist_core_snapshot(store=store, core=core, identity=identity)

    monkeypatch.setattr("trader.core_session_io.persist_core_snapshot", fail_second_snapshot)
    with pytest.raises(RuntimeError, match="snapshot failed"):
        consume_core_outbox(store=store, core=core, identity=identity, tokens=tokens)
    monkeypatch.setattr("trader.core_session_io.persist_core_snapshot", persist_core_snapshot)
    yes, no = core.state.inventory
    assert yes.qty == 30.0
    assert no.qty == 40.0

    core.note_recovery(now_ns=3)
    core.drain_apply()
    coordinator = RecoveryCoordinator(
        store=store,
        session_id="0xcond",
        yes_token=YES,
        no_token=NO,
        min_order_size=5.0,
    )
    assert (
        coordinator.accept_if_proven(
            core=core,
            now_ns=4,
            now_wall_s=1.0,
            rest_yes=30.0,
            rest_no=20.0,
            last_buy_unix=None,
        )
        is True
    )
    yes, no = core.state.inventory
    assert yes.qty == 30.0
    assert no.qty == 20.0
    assert yes.qty == store.position(YES).size
    assert no.qty == store.position(NO).size
    assert core.state.recovery_pending is False
    session = get_session(store._conn, "0xcond")
    assert session is not None
    assert session.checkpoint.recovery_pending is False
    assert session.checkpoint.inventory[0].qty == 30.0
    assert session.checkpoint.inventory[1].qty == 20.0
    assert session.last_outbox_seq == core.last_outbox_seq
    consume_core_outbox(store=store, core=core, identity=identity, tokens=tokens)
    assert core.state.inventory[0].qty == 30.0
    assert core.state.inventory[1].qty == 20.0
    store.close()
