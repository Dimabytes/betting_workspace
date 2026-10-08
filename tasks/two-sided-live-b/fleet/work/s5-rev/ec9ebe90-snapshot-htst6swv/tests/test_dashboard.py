import gzip
import json
import os
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from dashboard import catalog, diagnostics, logs, reserve, summarize, tails, wallet
from shared.utils.match_time import parse_utc
from strategy.lifecycle import empty_state
from strategy.types import (
    Budget,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    OrderStatus,
    Permissions,
    RestingOrder,
    Side,
    TokenInventory,
)
from trader.core_persistence import (
    UNSETTLED_CANCEL_REASON,
    CoreCommand,
    CoreSessionKey,
    OrderBinding,
    encode_checkpoint,
    insert_unsettled_buy,
    snapshot_checkpoint,
    upsert_binding,
    upsert_command,
    upsert_session,
)
from trader.wallet_store import WalletStateStore

YES = "yes-token"
NO = "no-token"
CID = "0xcond"
SID = "sess-1"
NOW = 1_800_000_000.0


def _order(
    order_id: str,
    *,
    side: Side = "BUY",
    price: float = 0.5,
    submitted: float = 40.0,
    filled: float = 0.0,
    status: OrderStatus = "live",
    cancel_reason: str = "",
) -> RestingOrder:
    return RestingOrder(
        order_id=order_id,
        episode_id=1,
        token_index=0,
        side=side,
        price=price,
        submitted_qty=submitted,
        filled_qty=filled,
        level_index=0,
        status=status,
        accepted=True,
        partially_filled=False,
        cancel_reason=cancel_reason,
        ack_reason="",
        placed_ns=0,
        accepted_ns=0,
    )


def _state(orders: tuple[RestingOrder, ...], qty: float = 0.0):
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
        orders=orders,
        inventory=(
            TokenInventory(token_index=0, qty=qty, cost_basis=0.5 * qty, last_buy_ns=None),
            TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
        ),
        next_order_seq=1,
    )


def _command(command_id: str, core_order_id: str, *, venue_id: str | None = "v-1") -> CoreCommand:
    return CoreCommand(
        command_id=command_id,
        session_id=SID,
        revision=1,
        batch_id="b1",
        kind="place",
        core_order_id=core_order_id,
        token_index=0,
        side="BUY",
        price=0.5,
        quantity=10.0,
        venue_id=venue_id,
        order_hash=None,
        dispatch_state="dispatched",
        outcome="",
        consumed=False,
    )


def _wallet(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    path = tmp_path / "live.db"
    WalletStateStore(path).close()
    return sqlite3.connect(path), path


def _insert_session(
    conn: sqlite3.Connection,
    orders: tuple[RestingOrder, ...],
    *,
    session_id: str = SID,
    condition_id: str = CID,
    qty: float = 0.0,
) -> None:
    checkpoint = snapshot_checkpoint(
        state=_state(orders, qty), now_ns=0, now_wall_s=NOW, sell_min_life_s=1.0
    )
    upsert_session(
        conn,
        session_id=session_id,
        key=CoreSessionKey(
            condition_id=condition_id,
            game="dota",
            yes_token=YES,
            no_token=NO,
            yes_is_radiant=True,
        ),
        revision=1,
        recovery=False,
        recovery_generation=0,
        checkpoint=checkpoint,
        last_outbox_seq=0,
    )


def _insert_fill(
    conn: sqlite3.Connection,
    fill_key: str,
    *,
    maker_order_id: str = "v-1",
    token_id: str = YES,
    side: Side = "BUY",
    price: float = 0.5,
    size: float = 4.0,
    ts: float = NOW,
    status: str = "MATCHED",
    cash_delta: float = -2.0,
) -> None:
    conn.execute(
        "INSERT INTO fill_ledger(fill_key, clob_trade_id, maker_order_id, token_id,"
        " side, price, size, ts, status, pre_size, pre_avg, cash_delta, is_maker)"
        " VALUES(?,?,?,?,?,?,?,?,?,0,0,?,1)",
        (
            fill_key,
            "t-" + fill_key,
            maker_order_id,
            token_id,
            side,
            price,
            size,
            ts,
            status,
            cash_delta,
        ),
    )


def _insert_outbox(conn: sqlite3.Connection, fill_key: str, event: str) -> None:
    conn.execute("INSERT INTO fill_outbox(fill_key, event) VALUES(?,?)", (fill_key, event))


def _archive(root: Path, match_id: str, *, game: str = "dota", cid: str = "0xm") -> Path:
    archive = root / match_id
    archive.mkdir(parents=True)
    (archive / "match.json").write_text(
        json.dumps(
            {
                "match_id": match_id,
                "game": game,
                "joined_at_utc": "2026-08-29T10:00:00Z",
                "market": {
                    "condition_id": cid,
                    "market_slug": f"{game}-m-{match_id}",
                    "yes_token_id": "y" + match_id,
                    "no_token_id": "n" + match_id,
                    "yes_is_radiant": True,
                },
                "teams": {"radiant": "R", "dire": "D"},
            }
        )
    )
    return archive


def _write_journal(archive: Path, records: list[dict[str, object]]) -> Path:
    journal = archive / "session.jsonl"
    journal.write_text("".join(json.dumps(r) + "\n" for r in records))
    return journal


def _signal(second: float = 100.0, recorded: str = "2026-08-29T10:05:00Z") -> dict[str, object]:
    return {
        "kind": "signal",
        "second": second,
        "reason": "model",
        "entry_block": "min_delta",
        "recorded_at_utc": recorded,
        "feed_received_at_utc": "2026-08-29T10:04:59Z",
        "model_evaluated": True,
        "raw_delta": 0.011,
        "pos_yes": 0.0,
        "pos_no": 0.0,
    }


def _session_start() -> dict[str, object]:
    return {"kind": "session_start", "execution_mode": "live", "schema_version": 7}


def test_readonly_missing_path_never_creates_db(tmp_path: Path) -> None:
    missing = tmp_path / "absent.db"
    with pytest.raises(sqlite3.Error):
        wallet.open_readonly(missing)
    assert not missing.exists()
    snap = wallet.read_wallet_snapshot(missing)
    assert not snap.ok
    assert snap.error is not None


def test_readonly_connection_rejects_writes(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    conn.close()
    ro = wallet.open_readonly(path)
    with pytest.raises(sqlite3.Error):
        ro.execute("INSERT INTO wallet_identity(k, v) VALUES('x', 'y')")
    ro.close()


def test_wallet_snapshot_reads_all_tables(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_session(conn, (_order("c0"),))
    upsert_binding(
        conn,
        OrderBinding(
            session_id=SID,
            core_order_id="c0",
            venue_id="v-1",
            episode_id=1,
            token_index=0,
            side="BUY",
            submitted_qty=40.0,
            level_index=0,
            terminal=False,
        ),
    )
    upsert_command(conn, _command("cmd-1", "c0"))
    insert_unsettled_buy(conn, venue_id="v-9", session_id=SID, token_id=YES, price=0.4, qty=10.0)
    conn.execute(
        "INSERT INTO positions(token_id, size, avg_price, updated_ts) VALUES(?,?,?,?)",
        (YES, 3.0, 0.5, NOW),
    )
    conn.execute("INSERT INTO wallet_identity(k, v) VALUES('funder', '0xfunder')")
    conn.execute("INSERT INTO token_cid(token_id, condition_id) VALUES(?,?)", (YES, CID))
    _insert_fill(conn, "k1")
    _insert_outbox(conn, "k1", "matched")
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    assert snap.ok and snap.error is None
    assert snap.funder == "0xfunder"
    assert len(snap.sessions) == 1 and snap.sessions[0].condition_id == CID
    assert snap.sessions[0].checkpoint is not None
    assert len(snap.bindings) == 1
    assert len(snap.open_buy_commands) == 1
    assert len(snap.unsettled_buys) == 1
    assert snap.positions[0].size == 3.0
    assert snap.token_cids[0].condition_id == CID
    assert snap.max_outbox_seq == 1


def test_outbox_page_is_bounded_and_independent(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_fill(conn, "k1")
    _insert_fill(conn, "k2", side="SELL", cash_delta=2.0)
    _insert_outbox(conn, "k1", "matched")
    _insert_outbox(conn, "k2", "matched")
    _insert_outbox(conn, "k1", "failed")
    conn.execute("UPDATE fill_outbox SET acked=1 WHERE seq=1")
    conn.commit()
    page = wallet.read_outbox_page(path, after_seq=0, limit=2)
    conn.close()
    assert page is not None
    assert len(page.events) == 2
    assert page.has_more and page.next_seq == 2 and page.max_seq == 3
    rest = wallet.read_outbox_page(path, after_seq=page.next_seq, limit=10)
    assert rest is not None and rest.events[0].event == "failed"
    assert not rest.has_more


def test_checkpoint_cache_invalidates_on_updated_at() -> None:
    cache = wallet.CheckpointCache()
    checkpoint = snapshot_checkpoint(
        state=_state((_order("c0"),)), now_ns=0, now_wall_s=NOW, sell_min_life_s=1.0
    )
    raw = encode_checkpoint(checkpoint)
    first, err = cache.decode(session_id=SID, updated_at=1.0, revision=1, raw=raw)
    assert err is None and first is not None
    again, _ = cache.decode(session_id=SID, updated_at=1.0, revision=1, raw=raw)
    assert again is first
    newer, _ = cache.decode(session_id=SID, updated_at=2.0, revision=1, raw=raw)
    assert newer is not first
    bad, err = cache.decode(session_id=SID, updated_at=3.0, revision=1, raw="not json")
    assert bad is None and err is not None


def test_checkpoint_cache_retain_evicts_dead_sessions() -> None:
    cache = wallet.CheckpointCache()
    checkpoint = snapshot_checkpoint(
        state=_state((_order("c0"),)), now_ns=0, now_wall_s=NOW, sell_min_life_s=1.0
    )
    raw = encode_checkpoint(checkpoint)
    kept, _ = cache.decode(session_id="s-keep", updated_at=1.0, revision=1, raw=raw)
    cache.decode(session_id="s-dead", updated_at=1.0, revision=1, raw=raw)
    cache.retain(frozenset({"s-keep"}))
    again, _ = cache.decode(session_id="s-keep", updated_at=1.0, revision=1, raw=raw)
    assert again is kept
    reloaded, _ = cache.decode(session_id="s-dead", updated_at=1.0, revision=1, raw=raw)
    assert reloaded is not None
    reread, _ = cache.decode(session_id="s-dead", updated_at=1.0, revision=1, raw=raw)
    assert reread is reloaded


def test_wallet_snapshot_seeds_matched_boundary(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_fill(conn, "k1")
    _insert_fill(conn, "k2", status="CONFIRMED")
    _insert_fill(conn, "k3", status="FAILED")
    _insert_outbox(conn, "k1", "matched")
    _insert_outbox(conn, "k2", "matched")
    _insert_outbox(conn, "k2", "confirmed")
    _insert_outbox(conn, "k3", "matched")
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    assert snap.ok
    assert snap.fill_boundary_ok
    assert snap.matched_fill_keys == frozenset({"k1"})
    assert snap.max_outbox_seq == 4


def test_wallet_snapshot_boundary_bad_when_fill_tables_missing(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    conn.execute("DROP TABLE fill_ledger")
    conn.execute("DROP TABLE fill_outbox")
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    assert not snap.fill_boundary_ok
    assert snap.matched_fill_keys == frozenset()


def test_reserve_complete_when_nothing_is_open(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    report = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=None,
        collateral_usdc=100.0,
    )
    assert not report.incomplete
    assert report.limitations == ()
    assert report.available_cash == pytest.approx(100.0)


def test_reserve_counts_core_remainder_and_dedupes_command(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_session(conn, (_order("c0", price=0.5, submitted=40.0, filled=10.0),))
    upsert_binding(
        conn,
        OrderBinding(
            session_id=SID,
            core_order_id="c0",
            venue_id="v-1",
            episode_id=1,
            token_index=0,
            side="BUY",
            submitted_qty=40.0,
            level_index=0,
            terminal=False,
        ),
    )
    upsert_command(conn, _command("cmd-1", "c0"))
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    report = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=None,
        collateral_usdc=100.0,
    )
    assert report.core_buy == pytest.approx(30.0 * 0.5)
    assert report.command_buy == 0.0
    assert report.total_known == pytest.approx(15.0)
    assert report.available_cash == pytest.approx(85.0)


def test_reserve_command_uncovered_and_consumed_skip(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_session(conn, ())
    upsert_command(conn, _command("cmd-1", "c0", venue_id=None))
    consumed = replace(_command("cmd-2", "c9"), consumed=True)
    upsert_command(conn, consumed)
    accepted = replace(_command("cmd-3", "c10"), outcome="accepted")
    upsert_command(conn, accepted)
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    report = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=None,
        collateral_usdc=None,
    )
    assert report.command_buy == pytest.approx(0.5 * 10.0)
    assert report.available_cash is None


def test_reserve_gone_order_covered_by_unsettled(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    gone = _order("c0", status="unknown", cancel_reason=UNSETTLED_CANCEL_REASON)
    _insert_session(conn, (gone,))
    upsert_binding(
        conn,
        OrderBinding(
            session_id=SID,
            core_order_id="c0",
            venue_id="v-9",
            episode_id=1,
            token_index=0,
            side="BUY",
            submitted_qty=40.0,
            level_index=0,
            terminal=False,
        ),
    )
    insert_unsettled_buy(conn, venue_id="v-9", session_id=SID, token_id=YES, price=0.5, qty=10.0)
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    report = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=None,
        collateral_usdc=None,
    )
    assert report.core_buy == 0.0
    assert report.unsettled_buy == pytest.approx(10.0 * 0.5)
    assert not report.incomplete


def test_reserve_unsettled_net_of_booked(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_session(conn, ())
    insert_unsettled_buy(conn, venue_id="v-9", session_id=SID, token_id=YES, price=0.4, qty=10.0)
    _insert_fill(conn, "k1", maker_order_id="v-9", size=4.0, status="CONFIRMED", side="BUY")
    _insert_fill(conn, "k2", maker_order_id="v-9", size=1.0, status="FAILED", side="BUY")
    _insert_fill(conn, "k3", maker_order_id="v-9", size=2.0, status="SUPERSEDED", side="BUY")
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    report = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=None,
        collateral_usdc=None,
    )
    assert report.unsettled_buy == pytest.approx((10.0 - 4.0) * 0.4)


def test_reserve_open_order_counts_shared_venue_once(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_session(conn, (_order("c0", price=0.5, submitted=40.0, filled=10.0),))
    upsert_binding(
        conn,
        OrderBinding(
            session_id=SID,
            core_order_id="c0",
            venue_id="v-9",
            episode_id=1,
            token_index=0,
            side="BUY",
            submitted_qty=40.0,
            level_index=0,
            terminal=False,
        ),
    )
    insert_unsettled_buy(conn, venue_id="v-9", session_id=SID, token_id=YES, price=0.5, qty=10.0)
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    report = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=None,
        collateral_usdc=None,
    )
    assert report.core_buy == pytest.approx(30.0 * 0.5)
    assert report.unsettled_buy == 0.0
    assert report.total_known == pytest.approx(30.0 * 0.5)
    assert report.incomplete


def test_reserve_finished_session_excluded_unsettled_kept(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_session(conn, (_order("c0", submitted=40.0, filled=10.0),))
    insert_unsettled_buy(conn, venue_id="v-8", session_id=SID, token_id=YES, price=0.6, qty=5.0)
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    report = reserve.compute_reserve(
        snap,
        finished_cids=frozenset({CID}),
        catalog_cids=frozenset({CID}),
        run_started_at=None,
        collateral_usdc=None,
    )
    assert report.core_buy == 0.0
    assert report.unsettled_buy == pytest.approx(5.0 * 0.6)


def test_reserve_undecodable_checkpoint_marks_incomplete(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_session(conn, ())
    conn.execute("UPDATE core_sessions SET checkpoint='garbage' WHERE session_id=?", (SID,))
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    assert snap.sessions[0].checkpoint is None
    report = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=None,
        collateral_usdc=None,
    )
    assert report.incomplete


def test_uncatalogued_session_reserves_only_after_start(tmp_path: Path) -> None:
    conn, path = _wallet(tmp_path)
    _insert_session(conn, (_order("c0", price=0.5, submitted=10.0, filled=0.0),))
    conn.execute("UPDATE core_sessions SET updated_at=? WHERE session_id=?", (1_000.0, SID))
    conn.commit()
    snap = wallet.read_wallet_snapshot(path)
    conn.close()
    before = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=2_000.0,
        collateral_usdc=None,
    )
    after = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=500.0,
        collateral_usdc=None,
    )
    unknown = reserve.compute_reserve(
        snap,
        finished_cids=frozenset(),
        catalog_cids=frozenset(),
        run_started_at=None,
        collateral_usdc=None,
    )
    assert before.core_buy == 0.0
    assert after.core_buy == pytest.approx(5.0)
    assert unknown.core_buy == pytest.approx(5.0)


def test_tail_cache_bounds_and_invalidates(tmp_path: Path) -> None:
    cache = tails.TailCache(tail_bytes=2000, max_records=512)
    journal = tmp_path / "session.jsonl"
    journal.write_text(json.dumps(_session_start()) + "\n" + json.dumps(_signal()) + "\n")
    view = cache.read(journal)
    assert view is not None and view.last_signal is not None
    assert view.saw_session_end is False
    again = cache.read(journal)
    assert again is view
    with journal.open("a") as handle:
        handle.write(json.dumps({"kind": "session_end", "net_cash": 1.0}) + "\n")
    view2 = cache.read(journal)
    assert view2 is not None and view2 is not view and view2.saw_session_end


def test_tail_truncation_drops_first_cut_record(tmp_path: Path) -> None:
    cache = tails.TailCache(tail_bytes=420, max_records=512)
    journal = tmp_path / "session.jsonl"
    records = [_session_start(), *(_signal(float(s)) for s in range(5))]
    journal.write_text("".join(json.dumps(r) + "\n" for r in records))
    view = cache.read(journal)
    assert view is not None and view.truncated_start
    assert view.last_signal is not None
    assert view.last_signal["second"] == 4.0


def test_tail_drops_torn_last_line_and_counts_malformed(tmp_path: Path) -> None:
    cache = tails.TailCache()
    journal = tmp_path / "session.jsonl"
    journal.write_text(
        json.dumps(_signal(50.0)) + "\n" + "not json\n" + '{"kind": "signal", "second": 9'
    )
    view = cache.read(journal)
    assert view is not None
    assert view.last_signal is not None
    assert view.last_signal["second"] == 50.0
    assert view.malformed == 1


def test_tail_gzip_and_head(tmp_path: Path) -> None:
    cache = tails.TailCache()
    journal = tmp_path / "session.jsonl"
    journal.write_text(json.dumps(_session_start()) + "\n")
    gz = tmp_path / "session.jsonl.gz"
    journal.unlink()
    with gzip.open(gz, "wt") as handle:
        handle.write(json.dumps(_session_start()) + "\n")
        handle.write(json.dumps(_signal()) + "\n")
    view = cache.read(journal)
    assert view is not None and view.compressed and view.last_signal is not None
    head = cache.read_head(journal)
    assert head is not None and head[0]["kind"] == "session_start"


def test_tail_missing_file(tmp_path: Path) -> None:
    cache = tails.TailCache()
    assert cache.read(tmp_path / "nope.jsonl") is None


def test_catalog_classifies_maps(tmp_path: Path) -> None:
    root = tmp_path / "trader_live"
    live = _archive(root, "m1", cid="0x1")
    _write_journal(live, [_session_start(), _signal()])
    finished = _archive(root, "m2", cid="0x2")
    _write_journal(finished, [_session_start(), _signal()])
    meta = json.loads((finished / "match.json").read_text())
    meta["final"] = {"winner": "radiant"}
    (finished / "match.json").write_text(json.dumps(meta))
    ended = _archive(root, "m3", cid="0x3")
    _write_journal(
        ended,
        [_session_start(), _signal(), {"kind": "session_end", "net_cash": 1.0}],
    )
    stale = _archive(root, "m4", cid="0x4")
    _write_journal(stale, [_session_start(), _signal()])
    empty = _archive(root, "m5", cid="0x5")
    del empty
    feed_only = root / "m5"
    (feed_only / "match.json").write_text(json.dumps({"match_id": "m5", "record_only": True}))
    old = time.time() - 2000.0
    for file in stale.iterdir():
        os.utime(file, (old, old))
    cat = catalog.MatchCatalog(root, "live", refresh_s=60.0)
    now = time.time()
    snap = cat.snapshot(now_mono=time.monotonic(), now_wall=now)
    entries = {e.match_id: e for e in snap.entries}
    assert set(entries) == {"m1", "m2", "m3", "m4", "m5"}
    views = {
        e.match_id: catalog.classify_entry(e, now_wall=now, tail=None, run_started_at=None)
        for e in snap.entries
    }
    assert views["m1"].status == "live"
    assert views["m2"].status == "final"
    assert views["m3"].status == "terminal"
    assert views["m4"].status == "stale"
    assert views["m5"].status == "incomplete"


def test_stale_map_before_run_start_is_terminal(tmp_path: Path) -> None:
    root = tmp_path / "trader_live"
    archive = _archive(root, "m1", cid="0x1")
    _write_journal(archive, [_session_start(), _signal()])
    old = time.time() - 2000.0
    for file in archive.iterdir():
        os.utime(file, (old, old))
    cat = catalog.MatchCatalog(root, "live", refresh_s=0.0)
    now = time.time()
    snap = cat.snapshot(now_mono=time.monotonic(), now_wall=now)
    entry = snap.entries[0]
    assert entry.last_write is not None
    gone = catalog.classify_entry(
        entry, now_wall=now, tail=None, run_started_at=entry.last_write + 1.0
    )
    assert gone.status == "terminal"
    assert gone.evidence == ("writer_gone",)
    stale = catalog.classify_entry(
        entry, now_wall=now, tail=None, run_started_at=entry.last_write - 1.0
    )
    assert stale.status == "stale"
    off = catalog.classify_entry(entry, now_wall=now, tail=None, run_started_at=None)
    assert off.status == "stale"


def test_catalog_cache_reuses_within_refresh(tmp_path: Path) -> None:
    root = tmp_path / "trader_live"
    _archive(root, "m1")
    cat = catalog.MatchCatalog(root, "live", refresh_s=60.0)
    now_mono = time.monotonic()
    first = cat.snapshot(now_mono=now_mono, now_wall=NOW)
    second = cat.snapshot(now_mono=now_mono + 1.0, now_wall=NOW)
    assert second is first
    third = cat.snapshot(now_mono=now_mono + 120.0, now_wall=NOW)
    assert third is not first


def test_open_session_realized_uses_last_fill_cash(tmp_path: Path) -> None:
    root = tmp_path / "trader_live"
    archive = _archive(root, "m1")
    _write_journal(
        archive,
        [
            _session_start(),
            {"kind": "fill", "price": 0.4, "size": 10, "is_maker": True, "net_cash": -4.0},
            {"kind": "fill", "price": 0.6, "size": 5, "is_maker": False, "net_cash": 2.5},
        ],
    )
    summary = summarize.summarize_session(archive)
    assert summary.end is None
    assert summary.realized == 2.5


def test_late_fill_does_not_reactivate_terminal(tmp_path: Path) -> None:
    root = tmp_path / "trader_live"
    archive = _archive(root, "m1", cid="0x1")
    journal = _write_journal(
        archive,
        [_session_start(), {"kind": "session_end", "net_cash": 1.0}],
    )
    with journal.open("a") as handle:
        handle.write(json.dumps({"kind": "late_fill", "side": "SELL", "net_cash": 2.0}) + "\n")
    cat = catalog.MatchCatalog(root, "live", refresh_s=0.0)
    now = time.time()
    snap = cat.snapshot(now_mono=time.monotonic(), now_wall=now)
    view = catalog.classify_entry(snap.entries[0], now_wall=now, tail=None, run_started_at=None)
    assert view.status == "terminal"


def test_skip_reasons_and_halts() -> None:
    log = (
        "2026-08-29T10:00:00Z trader skip: reason=pre_horn match_id=m1 cid=0xabc\n"
        "2026-08-29T10:05:00Z trader skip: reason=sidecar_fault match_id=m1 cid=0xabc\n"
        "2026-08-29T10:06:00Z trader skip: reason=missing_book match_id=m2 cid=0xdef\n"
        "2026-08-29T10:07:00Z trader risk_halt day pnl\n"
    )
    notes = logs.latest_skip_reasons(log)
    assert notes["0xabc"].reason == "sidecar_fault"
    assert notes["0xdef"].reason == "missing_book"
    halts = logs.halt_events(log)
    assert len(halts) == 1


def test_service_logs_fake_runner(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n")
    seen: list[Sequence[str]] = []

    def runner(argv: Sequence[str], timeout_s: float) -> bytes:
        del timeout_s
        seen.append(list(argv))
        return b"line1\nline2\n"

    result = logs.read_service_logs(compose_file=compose, service="live", runner=runner)
    assert result.ok and result.text == "line1\nline2\n"
    assert seen[0][-1] == "live" and "--tail" in seen[0]
    missing = logs.read_service_logs(
        compose_file=tmp_path / "none.yml", service="live", runner=runner
    )
    assert not missing.ok

    def failing(argv: Sequence[str], timeout_s: float) -> bytes:
        raise OSError("no docker")

    bad = logs.read_service_logs(compose_file=compose, service="live", runner=failing)
    assert not bad.ok and bad.error == "OSError"


def test_nontrading_markets(tmp_path: Path) -> None:
    root = tmp_path / "dota_archive"
    markets = root / "metadata" / "markets"
    markets.mkdir(parents=True)
    now = time.time()
    sidecar = {
        "schemaVersion": 1,
        "eventId": "e1",
        "eventSlug": "ev",
        "conditionId": "0xskipped",
        "marketSlug": "s",
        "question": "q",
        "marketKind": "map_winner",
        "mapNumber": 1,
        "outcomes": [
            {"index": 0, "name": "A", "tokenId": "t0"},
            {"index": 1, "name": "B", "tokenId": "t1"},
        ],
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "negRisk": False,
    }
    (markets / "0xskipped.json").write_text(json.dumps(sidecar))
    closed = dict(sidecar, conditionId="0xclosed", closed=True, marketSlug="s2")
    (markets / "0xclosed.json").write_text(json.dumps(closed))
    traded = dict(sidecar, conditionId="0xtraded", marketSlug="s3")
    (markets / "0xtraded.json").write_text(json.dumps(traded))
    skips = logs.latest_skip_reasons(
        "2026-08-29T10:00:00Z trader skip: reason=outside_window cid=0xskipped\n"
    )
    events = root / "metadata" / "events"
    events.mkdir()
    (events / "e1.json").write_text(
        json.dumps(
            {
                "event": {
                    "markets": [
                        {
                            "conditionId": "0xskipped",
                            "sports": {"gameStartTime": "2026-10-06 17:00:00+00"},
                        }
                    ]
                }
            }
        )
    )
    result = logs.list_nontrading(
        roots={"dota": root},
        traded_cids=frozenset({"0xtraded"}),
        skips=skips,
        now_epoch=now,
    )
    by_cid = {m.condition_id: m for m in result.markets}
    assert set(by_cid) == {"0xskipped"}
    assert by_cid["0xskipped"].reason == "outside_window"
    assert by_cid["0xskipped"].reason_source == "log"
    assert by_cid["0xskipped"].starts_at == parse_utc("2026-10-06 17:00:00+00").timestamp()


def test_freshness_priority() -> None:
    base = diagnostics.FreshnessInput(
        now_s=1000.0,
        threshold_s=120.0,
        has_value=True,
        value_age_s=10.0,
        attempt_failed=False,
        coverage_complete=True,
        pending=False,
        book_connected=None,
        awaiting_book=False,
        error=None,
    )
    assert diagnostics.freshness(base).state == "fresh"
    assert diagnostics.freshness(replace(base, pending=True)).state == "updating"
    assert diagnostics.freshness(replace(base, attempt_failed=True)).state == "stale"
    assert diagnostics.freshness(replace(base, value_age_s=500.0)).state == "stale"
    assert diagnostics.freshness(replace(base, coverage_complete=False)).state == "stale"
    assert diagnostics.freshness(replace(base, book_connected=False)).state == "stale"
    assert diagnostics.freshness(replace(base, awaiting_book=True)).state == "stale"
    assert diagnostics.freshness(replace(base, has_value=False)).state == "no_data"
    assert diagnostics.freshness(replace(base, has_value=False, error="boom")).detail == "boom"


def test_sell_wedge_continuous_only() -> None:
    state = diagnostics.SellWatchState(None, None)
    obs = diagnostics.SellObservation(
        session_key="s1:g0",
        token_id=YES,
        order_id="c0",
        status="pending",
        price=0.7,
        remaining_qty=5.0,
        venue_id="v1",
        held_qty=10.0,
    )
    state, verdict = diagnostics.observe_sell(state, obs, 100.0)
    assert not verdict.wedged
    state, verdict = diagnostics.observe_sell(state, obs, 131.0)
    assert verdict.wedged and verdict.continuous_s == 31.0
    state, verdict = diagnostics.observe_sell(state, replace(obs, status="canceling"), 135.0)
    assert not verdict.wedged
    state, verdict = diagnostics.observe_sell(state, replace(obs, status="live"), 140.0)
    assert not verdict.wedged and state.key is None
    state, verdict = diagnostics.observe_sell(state, None, 150.0)
    assert state.key is None and not verdict.wedged
    state, verdict = diagnostics.observe_sell(
        state, replace(obs, status="pending", held_qty=0.0), 160.0
    )
    assert not verdict.wedged and state.key is None


def test_reason_and_block_labels() -> None:
    assert diagnostics.reason_label("model") == "решение модели"
    assert diagnostics.reason_label("outside_window") == "вне окна модели"
    assert diagnostics.reason_label("brand_new_code") == "brand_new_code"
    assert diagnostics.reason_label(None) == "нет данных"
    label = diagnostics.entry_block_label(
        "min_delta", model_evaluated=True, raw_delta=0.011, min_abs_delta=0.02
    )
    assert label == "|Δ| 0.011 < порога входа 0.02"
    assert (
        diagnostics.entry_block_label(
            "min_delta", model_evaluated=False, raw_delta=0.011, min_abs_delta=0.02
        )
        == "min_delta"
    )
    assert diagnostics.entry_block_label(
        "min_delta", model_evaluated=True, raw_delta=0.03, min_abs_delta=0.02
    ).startswith("min_delta: |Δ| 0.03")
    assert (
        diagnostics.entry_block_label(
            "account_cap", model_evaluated=True, raw_delta=None, min_abs_delta=None
        )
        == "лимит счёта"
    )


def test_explain_exit() -> None:
    live = diagnostics.SellObservation(
        session_key="s",
        token_id=YES,
        order_id="o1",
        status="live",
        price=0.7,
        remaining_qty=3.0,
        venue_id=None,
        held_qty=5.0,
    )
    pending = replace(live, status="pending")
    assert (
        diagnostics.explain_exit(
            held_qty=0.0,
            min_order_size=5.0,
            sells=(),
            sell_only=False,
            recovery_pending=False,
            unconfirmed=0,
            pending_ownership=0,
            wedge=None,
        ).kind
        == "no_position"
    )
    assert (
        diagnostics.explain_exit(
            held_qty=5.0,
            min_order_size=5.0,
            sells=(live,),
            sell_only=False,
            recovery_pending=False,
            unconfirmed=0,
            pending_ownership=0,
            wedge=None,
        ).kind
        == "sell_live"
    )
    assert (
        diagnostics.explain_exit(
            held_qty=5.0,
            min_order_size=5.0,
            sells=(pending,),
            sell_only=False,
            recovery_pending=False,
            unconfirmed=0,
            pending_ownership=0,
            wedge=None,
        ).kind
        == "sell_pending"
    )
    assert (
        diagnostics.explain_exit(
            held_qty=2.0,
            min_order_size=5.0,
            sells=(),
            sell_only=False,
            recovery_pending=False,
            unconfirmed=0,
            pending_ownership=0,
            wedge=None,
        ).kind
        == "dust"
    )
    assert (
        diagnostics.explain_exit(
            held_qty=8.0,
            min_order_size=5.0,
            sells=(),
            sell_only=True,
            recovery_pending=False,
            unconfirmed=0,
            pending_ownership=0,
            wedge=None,
        ).kind
        == "blocked"
    )
    assert (
        diagnostics.explain_exit(
            held_qty=8.0,
            min_order_size=None,
            sells=(),
            sell_only=False,
            recovery_pending=False,
            unconfirmed=0,
            pending_ownership=0,
            wedge=None,
        ).kind
        == "unknown"
    )


def test_signal_ages_and_second() -> None:
    signal = _signal()
    now = datetime(2026, 8, 29, 10, 6, 0, tzinfo=UTC).timestamp()
    decision_age, feed_age = diagnostics.signal_freshness(signal, now_s=now)
    assert decision_age == pytest.approx(60.0)
    assert feed_age == pytest.approx(61.0)
    assert diagnostics.signal_second(signal) == 100.0
    assert diagnostics.signal_freshness(None, now_s=now) == (None, None)


def test_activity_url_contract() -> None:
    urls: list[str] = []

    def fetch(url: str, timeout: float) -> object:
        del timeout
        urls.append(url)
        return []

    result = summarize.fetch_activity(
        "0xAbC", end=1000.0, day_start=0.0, day_end=100.0, fetch=fetch
    )
    assert result.day_complete
    assert "user=0xAbC" in urls[0]
    assert "limit=500" in urls[0] and "offset=0" in urls[0]
    assert "end=1000" in urls[0]
    pos_urls: list[str] = []

    def pos_fetch(url: str, timeout: float) -> object:
        del timeout
        pos_urls.append(url)
        return []

    positions = summarize.fetch_positions("0xAbC", fetch=pos_fetch)
    assert positions.traversal_complete
    assert "sizeThreshold=0" in pos_urls[0] and "includeArchived=true" in pos_urls[0]


def test_activity_incomplete_surfaces() -> None:
    def boom(url: str, timeout: float) -> object:
        raise OSError("net down")

    result = summarize.fetch_activity("0x", end=100.0, day_start=0.0, day_end=50.0, fetch=boom)
    assert not result.day_complete and not result.payout_search_complete
    assert result.error == "OSError"

    def invalid(url: str, timeout: float) -> object:
        return {"error": "x"}

    result = summarize.fetch_activity("0x", end=100.0, day_start=0.0, day_end=50.0, fetch=invalid)
    assert result.stop_reason == "invalid" and not result.day_complete


def test_fold_and_publish() -> None:
    day = date(2026, 8, 29)
    day_start, _ = summarize.berlin_day_bounds(day)
    ts = day_start + 43200.0
    rows = (
        summarize.ActivityEntry(timestamp=ts, kind="TRADE", side="BUY", usdc_size=50.0),
        summarize.ActivityEntry(timestamp=ts, kind="REDEEM", side="", usdc_size=52.08),
        summarize.ActivityEntry(timestamp=ts, kind="MAKER_REBATE", side="", usdc_size=0.2),
    )
    fold = summarize.fold_polymarket_day(
        rows, (summarize.PositionEntry("a", None, None, 4.0, 0.3, None),), day
    )
    assert round(fold.cash, 2) == 2.28 and round(fold.pnl, 2) == 3.48
    stale = summarize.DayResult(
        day=day.isoformat(),
        fold=None,
        fetched_at=None,
        complete=False,
        error="net",
        activity=None,
        positions=None,
    )
    kept = summarize.publish_day(
        summarize.DayResult(
            day=day.isoformat(),
            fold=fold,
            fetched_at=ts,
            complete=True,
            error=None,
            activity=None,
            positions=None,
        ),
        stale,
    )
    assert kept.fold is fold and not kept.complete
    rolled = summarize.publish_day(
        summarize.DayResult(
            day=(day - timedelta(days=1)).isoformat(),
            fold=fold,
            fetched_at=ts,
            complete=True,
            error=None,
            activity=None,
            positions=None,
        ),
        stale,
    )
    assert rolled.fold is None


def test_berlin_day_bounds_dst() -> None:
    start, end = summarize.berlin_day_bounds(date(2026, 10, 25))
    assert end - start == 25.0 * 3600.0
    start, end = summarize.berlin_day_bounds(date(2026, 3, 29))
    assert end - start == 23.0 * 3600.0
