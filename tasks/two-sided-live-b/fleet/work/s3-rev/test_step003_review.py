import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from polymaker.domain import Fill, Side

from trader import engine_seams, wallet_store
from trader.chain_balances import ChainSnapshot
from trader.wallet_store import WalletStateStore


def seed_pair(store: WalletStateStore, yes_qty: float, no_qty: float) -> None:
    for token, price, qty in (("yes", 0.4, yes_qty), ("no", 0.55, no_qty)):
        key = f"trade-{token}:order-{token}"
        fill = Fill(token, Side.BUY, price, qty, key, 1000.0, is_maker=True)
        assert store.apply_confirmed_fill(fill, key).applied_new


def test_post_merge_chain_read_can_reduce_the_same_shares_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(wallet_store.time, "time", lambda: clock["now"])
    monkeypatch.setattr(engine_seams, "notify_in_background", lambda _message: None)
    store = WalletStateStore(tmp_path / "race.db")
    seed_pair(store, 200.0, 150.0)
    engine = SimpleNamespace(
        alerter=SimpleNamespace(alert=lambda *args, **kwargs: None),
        _token_cid={},
    )
    clock["now"] = 1100.0
    snapshot = ChainSnapshot(123, 1100.0, {"yes": 50.0, "no": 0.0})
    for token in ("yes", "no"):
        engine_seams._apply_chain_balance(
            engine,
            store,
            {},
            token,
            snapshot=snapshot,
            sent_at=1100.0,
            now=1100.0,
        )
    assert store.position("yes").size == 50.0
    assert store.position("no").size == 0.0
    clock["now"] = 1101.0
    assert store.apply_merge(tx_hash="0xconfirmed-merge", token_ids=("yes", "no"), qty=150.0)
    assert store.position("yes").size == 0.0
    assert store.ledger_position("yes").size == 50.0
    assert store.running_net_cash == pytest.approx(-12.5)
    print("race reproduced: cached YES=0, ledger YES=50, authoritative YES=50")
    store.close()


def test_merge_rolls_back_both_legs_and_memory_when_second_insert_fails(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "rollback.db")
    seed_pair(store, 30.0, 20.0)
    ledger_before = store._conn.execute("SELECT COUNT(*) FROM fill_ledger").fetchone()[0]
    outbox_before = store._conn.execute("SELECT COUNT(*) FROM fill_outbox").fetchone()[0]
    cash_before = store.running_net_cash
    floors_before = {token: store.chain_read_floor(token) for token in ("yes", "no")}
    store._conn.execute(
        "CREATE TRIGGER reject_no_merge BEFORE INSERT ON fill_ledger "
        "WHEN NEW.status='MERGED' AND NEW.token_id='no' "
        "BEGIN SELECT RAISE(ABORT, 'injected second-leg failure'); END"
    )
    store._conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.apply_merge(tx_hash="0xfails", token_ids=("yes", "no"), qty=20.0)
    assert store._conn.execute("SELECT COUNT(*) FROM fill_ledger").fetchone()[0] == ledger_before
    assert store._conn.execute("SELECT COUNT(*) FROM fill_outbox").fetchone()[0] == outbox_before
    assert store.position("yes").size == 30.0
    assert store.position("no").size == 20.0
    persisted = store._conn.execute("SELECT token_id, size FROM positions ORDER BY token_id").fetchall()
    assert [(row["token_id"], row["size"]) for row in persisted] == [("no", 20.0), ("yes", 30.0)]
    assert store.running_net_cash == cash_before
    assert {token: store.chain_read_floor(token) for token in ("yes", "no")} == floors_before
    store.close()


def test_repeat_after_restart_preserves_both_legs_and_cash(tmp_path: Path) -> None:
    db_path = tmp_path / "repeat.db"
    store = WalletStateStore(db_path)
    seed_pair(store, 30.0, 20.0)
    assert store.apply_merge(tx_hash="0xrepeat", token_ids=("yes", "no"), qty=20.0)
    store.close()
    reopened = WalletStateStore(db_path)
    before_cash = reopened.running_net_cash
    assert not reopened.apply_merge(tx_hash="0xrepeat", token_ids=("no", "yes"), qty=20.0)
    assert reopened.position("yes").size == 10.0
    assert reopened.position("no").size == 0.0
    assert reopened.running_net_cash == before_cash
    assert reopened._conn.execute("SELECT COUNT(*) FROM fill_ledger").fetchone()[0] == 4
    assert reopened._conn.execute("SELECT COUNT(*) FROM fill_outbox").fetchone()[0] == 4
    reopened.close()
