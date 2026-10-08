"""WS and REST proof for unsettled BUY rows that already exist."""

# pyright: reportPrivateUsage=false, reportUnknownLambdaType=false, reportAttributeAccessIssue=false

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polymaker.domain import Fill, Side, TradeState
from polymaker.engine import Engine
from polymaker.state.tracker import TradeEvent

from trader.core_persistence import (
    insert_unsettled_buy,
    open_unsettled_buys,
    prove_unsettled_buy,
    unsettled_buy_notional,
)
from trader.engine_seams import (
    ShutdownLatch,
    WalletUserStream,
    install_rest_fill_recovery,
    install_rest_snapshot_stamps,
    wrap_alert_transitions,
)
from trader.fill_parsing import fill_key
from trader.unsettled_buy_recovery import (
    ORDER_LIFETIME_S,
    BuyCancellation,
    BuyExecutionProof,
    parse_buy_cancellation,
    parse_terminal_buy_proof,
)
from trader.wallet_host import WalletHost
from trader.wallet_store import WalletFillProcessor, WalletStateStore

TOKEN = "yes-token"
FUNDER = "0xfunder"
NOW = 1_700_000_000.0
PRICE = 0.50


def _order(**overrides: object) -> dict[str, object]:
    msg: dict[str, object] = {
        "id": "venue-1",
        "side": "BUY",
        "type": "UPDATE",
        "status": "LIVE",
        "original_size": "10",
        "size_matched": "4",
        "asset_id": TOKEN,
        "price": "0.5",
    }
    msg.update(overrides)
    return msg


def _seed(
    store: WalletStateStore,
    venue_id: str,
    *,
    session_id: str = "sess",
    token_id: str = TOKEN,
    qty: float = 8.0,
    created_at: float = NOW - 120.0,
) -> None:
    insert_unsettled_buy(
        store._conn,
        venue_id=venue_id,
        session_id=session_id,
        token_id=token_id,
        price=PRICE,
        qty=qty,
    )
    store._conn.execute(
        "UPDATE unsettled_buys SET created_at=?, updated_at=? WHERE venue_id=?",
        (created_at, created_at, venue_id),
    )
    store._conn.commit()


def _flags(store: WalletStateStore, venue_id: str) -> tuple[float, bool, bool]:
    row = store._conn.execute(
        "SELECT qty, proven, resolved FROM unsettled_buys WHERE venue_id=?",
        (venue_id,),
    ).fetchone()
    assert row is not None
    return float(row["qty"]), bool(row["proven"]), bool(row["resolved"])


def _count(store: WalletStateStore) -> int:
    row = store._conn.execute("SELECT COUNT(*) FROM unsettled_buys").fetchone()
    assert row is not None
    return int(row[0])


class _Trades:
    def __init__(self, rows: dict[str, list[object]] | None = None) -> None:
        self.rows = {} if rows is None else rows
        self.params: list[object] = []
        self.fail: set[str] = set()
        self.malformed: set[str] = set()

    def get_trades(self, params: object) -> list[object]:
        self.params.append(params)
        asset = str(getattr(params, "asset_id", ""))
        if asset in self.fail:
            raise RuntimeError("page")
        if asset in self.malformed:
            return cast(list[object], [{"maker_orders": {"bad": True}}])
        return self.rows.get(asset, [])


def _host(
    store: WalletStateStore,
    trades: _Trades | None = None,
    *,
    funder: str = FUNDER,
    client: object | None = None,
    use_client: bool = True,
) -> tuple[WalletHost, SimpleNamespace, list[str]]:
    book = trades if trades is not None else _Trades()
    if use_client and client is None:
        client = book
    wakes: list[str] = []
    alerts: list[tuple[str, str]] = []

    async def _io(fn: object, *args: object) -> object:
        del args
        fetched = cast(Any, fn)()
        return fetched

    gateway = SimpleNamespace(funder=funder, _client=client, _io=_io, positions=None)
    engine = SimpleNamespace(
        state=store,
        user=None,
        paper=False,
        metas={},
        _token_cid={},
        _wake_cid=wakes.append,
        _other_token=lambda _token: None,
        gateway=gateway,
        alerter=SimpleNamespace(alert=_remember_alert(alerts)),
        risk=SimpleNamespace(evaluate=lambda *args, **kwargs: None),
        _alerts=alerts,
    )
    host = object.__new__(WalletHost)
    host.engine = cast(Any, engine)
    host.store = store
    host._worker_by_cid = {}
    host._worker_by_token = {}
    return host, engine, wakes


def _remember_alert(alerts: list[tuple[str, str]]) -> object:
    def alert(key: str, message: str, *, critical: bool = False) -> None:
        del critical
        alerts.append((key, message))

    return alert


def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trader.wallet_host.time.time", lambda: NOW)


def test_parse_terminal_buy_proof_accepts_cancel_matched_and_full_size() -> None:
    assert parse_terminal_buy_proof(
        _order(type="CANCELLATION", status="CANCELED", size_matched="0")
    ) == (BuyExecutionProof("venue-1", 0.0))
    assert parse_terminal_buy_proof(
        _order(status="MATCHED", size_matched="4")
    ) == BuyExecutionProof("venue-1", 4.0)
    assert parse_terminal_buy_proof(
        _order(status="UPDATE", size_matched="10", original_size="10")
    ) == (BuyExecutionProof("venue-1", 10.0))
    assert parse_terminal_buy_proof(_order(size_matched="3")) is None
    assert (
        parse_terminal_buy_proof(_order(side="SELL", type="CANCELLATION", size_matched="0")) is None
    )
    for raw in (None, "", "   ", "nope", True, -1, float("nan")):
        assert parse_terminal_buy_proof(_order(type="CANCELLATION", size_matched=raw)) is None
    missing_size = _order(type="CANCELLATION")
    del missing_size["size_matched"]
    assert parse_terminal_buy_proof(missing_size) is None
    assert parse_terminal_buy_proof(
        _order(status="MATCHED", original_size=None, size_matched="1")
    ) == (BuyExecutionProof("venue-1", 1.0))


def test_parse_buy_cancellation_accepts_a_missing_quantity() -> None:
    assert parse_buy_cancellation(
        _order(type="CANCELLATION", status="CANCELED", size_matched="")
    ) == BuyCancellation("venue-1")
    missing = _order(type="CANCELLATION", status="CANCELED")
    del missing["size_matched"]
    assert parse_buy_cancellation(missing) == BuyCancellation("venue-1")
    assert parse_buy_cancellation(
        _order(type="CANCELLATION", status="canceled", size_matched=" ")
    ) == (BuyCancellation("venue-1"))
    assert (
        parse_buy_cancellation(_order(status="MATCHED", size_matched="10", original_size="10"))
        is None
    )
    assert (
        parse_buy_cancellation(_order(type="CANCELLATION", status="CANCELLED", size_matched="0"))
        is None
    )
    assert (
        parse_buy_cancellation(_order(side="SELL", type="CANCELLATION", status="CANCELED")) is None
    )


def test_ws_proof_of_an_existing_row_commits_before_wake(tmp_path: Path) -> None:
    path = tmp_path / "w.db"
    store = WalletStateStore(path)
    _seed(store, "venue-1", qty=8.0)
    host, _engine, wakes = _host(store)
    host._worker_by_cid["sess"] = cast(Any, SimpleNamespace(core=None))
    seen: list[tuple[bool, bool]] = []

    def wake(session_id: str) -> None:
        other = sqlite3.connect(path)
        other.row_factory = sqlite3.Row
        row = other.execute(
            "SELECT proven, resolved FROM unsettled_buys WHERE venue_id='venue-1'"
        ).fetchone()
        assert row is not None
        seen.append((bool(row["proven"]), bool(row["resolved"])))
        other.close()
        wakes.append(session_id)

    host.engine._wake_cid = wake
    host._on_order_terminal(_order(type="CANCELLATION", status="CANCELED", size_matched="0"))
    assert seen == [(True, True)]
    assert wakes == ["sess"]
    host._on_order_terminal(_order(type="CANCELLATION", status="CANCELED", size_matched="0"))
    assert wakes == ["sess"]
    assert _count(store) == 1
    store.close()


def test_ws_ignores_partial_sell_unknown_and_resolved_rows(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-1", qty=8.0)
    host, _engine, wakes = _host(store)
    before = _count(store)
    host._on_order_terminal(_order(size_matched="3"))
    host._on_order_terminal(_order(side="SELL", type="CANCELLATION", size_matched="0"))
    host._on_order_terminal(_order(id="other", type="CANCELLATION", size_matched="0"))
    host._on_order_terminal(_order(type="CANCELLATION", size_matched=""))
    host._on_order_terminal("not-a-dict")
    assert _flags(store, "venue-1") == (8.0, False, False)
    assert _count(store) == before
    assert wakes == []
    host._on_order_terminal(_order(status="MATCHED", size_matched="5"))
    qty, proven, resolved = _flags(store, "venue-1")
    assert (qty, proven, resolved) == (5.0, True, False)
    store.apply_matched_fill(
        Fill(TOKEN, Side.BUY, PRICE, 3.0, "t1:venue-1", 1.0, is_maker=True),
        "t1:venue-1",
    )
    assert unsettled_buy_notional(store._conn, "sess") == pytest.approx(1.0)
    assert _flags(store, "venue-1")[2] is False
    store.note_writedown_snapshot(TOKEN, 50.0)
    store.apply_confirmed_fill(
        Fill(TOKEN, Side.BUY, PRICE, 5.0, "t2:venue-1", 10.0, is_maker=True),
        "t2:venue-1",
    )
    assert store.ledger_status("t2:venue-1") == "SUPERSEDED"
    host._apply_unsettled_proofs(())
    assert _flags(store, "venue-1")[2] is False
    store.close()


def test_positive_ws_proof_waits_for_confirmed(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-1", qty=8.0)
    host, _engine, wakes = _host(store)
    host._worker_by_cid["sess"] = cast(Any, SimpleNamespace())
    host._on_order_terminal(_order(status="UPDATE", original_size="5", size_matched="5"))
    host._on_order_terminal(_order(status="UPDATE", original_size="5", size_matched="5"))
    assert _flags(store, "venue-1") == (5.0, True, False)
    assert wakes == ["sess"]
    store.apply_confirmed_fill(
        Fill(TOKEN, Side.BUY, PRICE, 5.0, "t1:venue-1", 2.0, is_maker=True),
        "t1:venue-1",
    )
    host._apply_unsettled_proofs(())
    assert _flags(store, "venue-1")[2] is True
    host._on_order_terminal(_order(status="MATCHED", size_matched="99"))
    assert _flags(store, "venue-1") == (5.0, True, True)
    store.close()


def test_confirmed_transition_resolves_without_the_credited_handler(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-1", qty=5.0)
    host, engine, wakes = _host(store)
    host._worker_by_cid["sess"] = cast(Any, SimpleNamespace())
    host._worker_by_token[TOKEN] = cast(Any, SimpleNamespace(note_fill=lambda _fill: None))
    credited: list[Fill] = []
    store.set_fill_credited_handler(credited.append)
    processor = WalletFillProcessor(store, on_fill=host._dispatch_fill)
    key = fill_key("t1", "venue-1")
    processor.on_trade(
        TradeEvent(TOKEN, Side.BUY, PRICE, 5.0, key, TradeState.MATCHED, 1.0), "sess"
    )
    assert _flags(store, "venue-1")[2] is False
    credited.clear()
    processor.on_trade(
        TradeEvent(TOKEN, Side.BUY, PRICE, 5.0, key, TradeState.CONFIRMED, 2.0),
        "sess",
    )
    assert credited == []
    assert _flags(store, "venue-1")[2] is True
    assert wakes == ["sess"]
    del engine
    store.close()


def test_dispatch_resolves_when_the_confirmed_seq_is_already_acked(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-1", qty=5.0, session_id="gone-session")
    host, _engine, wakes = _host(store)
    key = fill_key("t1", "venue-1")
    fill = Fill(TOKEN, Side.BUY, PRICE, 5.0, key, 2.0, is_maker=True)
    store.apply_confirmed_fill(fill, key)
    seq = store.unacked_seq(key, "confirmed")
    assert seq is not None
    store.ack_outbox(seq)
    journals: list[Fill] = []
    host._worker_by_token[TOKEN] = cast(Any, SimpleNamespace(note_fill=journals.append))
    recorded = store.fill_for_key(key)
    assert recorded is not None
    host._dispatch_fill(recorded)
    assert journals == []
    assert _flags(store, "venue-1")[2] is True
    assert wakes == []
    store.close()


def test_resolution_wakes_one_session_and_survives_without_a_worker(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-1", session_id="sess", qty=4.0)
    _seed(store, "venue-2", session_id="sess", qty=4.0)
    _seed(store, "venue-3", session_id="ended", qty=4.0)
    host, _engine, wakes = _host(store)
    for venue_id, size in (("venue-1", 4.0), ("venue-2", 4.0), ("venue-3", 4.0)):
        store.apply_confirmed_fill(
            Fill(TOKEN, Side.BUY, PRICE, size, f"t:{venue_id}", 1.0, is_maker=True),
            f"t:{venue_id}",
        )
    host._apply_unsettled_proofs(())
    assert wakes == []
    assert open_unsettled_buys(store._conn) == ()
    store.close()

    store = WalletStateStore(tmp_path / "w2.db")
    _seed(store, "venue-1", session_id="sess", qty=4.0)
    _seed(store, "venue-2", session_id="sess", qty=4.0)
    host, _engine, wakes = _host(store)
    host._worker_by_cid["sess"] = cast(Any, SimpleNamespace())
    for venue_id in ("venue-1", "venue-2"):
        store.apply_confirmed_fill(
            Fill(TOKEN, Side.BUY, PRICE, 4.0, f"t:{venue_id}", 1.0, is_maker=True),
            f"t:{venue_id}",
        )
    host._apply_unsettled_proofs(())
    assert wakes == ["sess"]
    store.close()


def test_rest_proof_boundary_groups_tokens_and_sums_maker_amounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "young", created_at=NOW - 59.999)
    _seed(store, "proven-row", created_at=NOW - 100.0)
    prove_unsettled_buy(store._conn, "proven-row", 1.0)
    store._conn.commit()
    trades = _Trades()
    host, _engine, _wakes = _host(store, trades)
    asyncio.run(host._reconcile_unsettled_buys())
    assert trades.params == []
    _seed(store, "old-a", token_id="token-a", created_at=NOW - 100.0)
    _seed(store, "old-b", session_id="other", token_id="token-a", created_at=NOW - 80.0)
    _seed(store, "old-c", token_id="token-b", created_at=NOW - 90.0)
    trades.rows = {
        "token-a": [
            {
                "size": "999",
                "maker_orders": [
                    {"order_id": "old-a", "matched_amount": "2.5"},
                    {"order_id": "stranger", "matched_amount": "100"},
                    {"order_id": "old-a", "matched_amount": "1.5"},
                ],
            },
            {"maker_orders": [{"order_id": "old-b", "matched_amount": "0"}]},
        ],
        "token-b": [],
    }
    asyncio.run(host._reconcile_unsettled_buys())
    assert [getattr(params, "asset_id", None) for params in trades.params] == ["token-a", "token-b"]
    assert all(getattr(params, "maker_address", None) == FUNDER for params in trades.params)
    oldest = min(NOW - 100.0, NOW - 80.0)
    token_a = next(
        params for params in trades.params if getattr(params, "asset_id", None) == "token-a"
    )
    assert getattr(token_a, "after", None) == int(oldest) - ORDER_LIFETIME_S
    assert _flags(store, "old-a") == (4.0, True, False)
    assert _flags(store, "old-b") == (0.0, True, True)
    assert _flags(store, "old-c") == (0.0, True, True)
    assert _flags(store, "young") == (8.0, False, False)
    store.close()


def test_rest_errors_do_not_invent_zero_and_one_token_does_not_block_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "bad-amount", token_id="token-bad", created_at=NOW - 90.0)
    _seed(store, "good", token_id="token-bad", created_at=NOW - 90.0)
    _seed(store, "broken", token_id="token-broken", created_at=NOW - 90.0)
    _seed(store, "ok", token_id="token-ok", created_at=NOW - 90.0)
    trades = _Trades(
        {
            "token-bad": [
                {
                    "maker_orders": [
                        {"order_id": "bad-amount", "matched_amount": "nope"},
                        {"order_id": "good", "matched_amount": "3"},
                    ]
                }
            ],
            "token-ok": [],
        }
    )
    trades.fail.add("token-broken")
    host, _engine, _wakes = _host(store, trades)
    asyncio.run(host._reconcile_unsettled_buys())
    assert _flags(store, "bad-amount")[1] is False
    assert _flags(store, "good") == (3.0, True, False)
    assert _flags(store, "broken")[1] is False
    assert _flags(store, "ok")[2] is True
    trades.malformed.add("token-ok")
    _seed(store, "struct", token_id="token-ok", created_at=NOW - 90.0)
    asyncio.run(host._reconcile_unsettled_buys())
    assert _flags(store, "struct")[1] is False
    store.close()

    store = WalletStateStore(tmp_path / "w2.db")
    _seed(store, "venue-1", created_at=NOW - 90.0)
    host, _engine, _wakes = _host(store, use_client=False)
    asyncio.run(host._reconcile_unsettled_buys())
    assert _flags(store, "venue-1")[1] is False
    host, _engine, _wakes = _host(store, funder="")
    asyncio.run(host._reconcile_unsettled_buys())
    assert _flags(store, "venue-1")[1] is False
    store.close()


def test_rest_zero_does_not_overwrite_a_ws_proof_that_arrived_during_the_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-1", qty=8.0, created_at=NOW - 90.0)
    host, engine, _wakes = _host(store)
    in_txn: list[bool] = []

    def get_trades(params: object) -> list[object]:
        del params
        in_txn.append(store._conn.in_transaction)
        return []

    async def _io(fn: object, *args: object) -> object:
        del args
        host._on_order_terminal(_order(id="venue-1", status="MATCHED", size_matched="4"))
        assert store._conn.in_transaction is False
        return cast(Any, fn)()

    engine.gateway._client = SimpleNamespace(get_trades=get_trades)
    engine.gateway._io = _io
    asyncio.run(host._reconcile_unsettled_buys())
    assert in_txn == [False]
    assert _flags(store, "venue-1") == (4.0, True, False)
    store.close()


def test_long_window_trades_are_backfilled_before_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-1", qty=5.0, created_at=NOW - 5_000.0)
    trade = {
        "id": "clob-old",
        "status": "CONFIRMED",
        "asset_id": TOKEN,
        "side": "SELL",
        "outcome": "Yes",
        "match_time": "1600000000",
        "size": "999",
        "maker_orders": [
            {
                "maker_address": FUNDER,
                "order_id": "venue-1",
                "matched_amount": "5",
                "price": "0.5",
                "outcome": "Yes",
            }
        ],
    }
    trades = _Trades({TOKEN: [trade]})
    host, _engine, _wakes = _host(store, trades)
    asyncio.run(host._reconcile_unsettled_buys())
    assert store.ledger_status(fill_key("clob-old", "venue-1")) == "CONFIRMED"
    assert _flags(store, "venue-1")[2] is True
    assert getattr(trades.params[0], "after", None) == int(NOW - 5_000.0) - ORDER_LIFETIME_S
    store.close()


def test_proven_row_resolves_from_backfill_without_a_proof_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-1", qty=5.0, created_at=NOW - 10.0)
    prove_unsettled_buy(store._conn, "venue-1", 5.0)
    store._conn.commit()
    store.apply_matched_fill(
        Fill(TOKEN, Side.BUY, PRICE, 5.0, "t1:venue-1", 1.0, is_maker=True),
        "t1:venue-1",
    )
    store.apply_confirmed_fill(
        Fill(TOKEN, Side.BUY, PRICE, 5.0, "t1:venue-1", 1.0, is_maker=True),
        "t1:venue-1",
    )
    trades = _Trades()
    host, _engine, _wakes = _host(store, trades)
    asyncio.run(host._reconcile_unsettled_buys())
    assert trades.params == []
    assert _flags(store, "venue-1")[2] is True
    store.close()


def test_old_open_rows_alert_once_without_changing_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-young", created_at=NOW - 599.0)
    _seed(store, "venue-old-1", created_at=NOW - 601.0)
    _seed(store, "venue-proved", created_at=NOW - 601.0)
    prove_unsettled_buy(store._conn, "venue-proved", 3.0)
    store._conn.commit()
    store.note_writedown_snapshot(TOKEN, 50.0)
    store.apply_confirmed_fill(
        Fill(TOKEN, Side.BUY, PRICE, 3.0, "t1:venue-proved", 10.0, is_maker=True),
        "t1:venue-proved",
    )
    host, engine, _wakes = _host(store)
    wrap_alert_transitions(cast(Any, engine))
    before = _flags(store, "venue-old-1")
    host._alert_stale_unsettled_buys(NOW)
    host._alert_stale_unsettled_buys(NOW)
    keys = [key for key, _message in engine._alerts]
    assert f"unsettled_buy:{'venue-young'[:8]}" not in keys
    assert keys.count(f"unsettled_buy:{'venue-old-1'[:8]}") == 1
    assert keys.count(f"unsettled_buy:{'venue-proved'[:8]}") == 1
    assert _flags(store, "venue-old-1") == before
    assert unsettled_buy_notional(store._conn, "sess") == pytest.approx((8.0 + 8.0 + 3.0) * PRICE)
    _seed(store, "venue-done", qty=4.0, created_at=NOW - 601.0)
    store.apply_confirmed_fill(
        Fill(TOKEN, Side.BUY, PRICE, 4.0, "t1:venue-done", 100.0, is_maker=True),
        "t1:venue-done",
    )
    host._apply_unsettled_proofs(())
    host._alert_stale_unsettled_buys(NOW)
    assert f"unsettled_buy:{'venue-done'[:8]}" not in [key for key, _message in engine._alerts]
    message = next(
        text for key, text in engine._alerts if key == f"unsettled_buy:{'venue-old-1'[:8]}"
    )
    assert message == "venue venue-old-1 session sess reserve remains held"
    assert "601" not in message
    store.close()


def test_user_stream_forwards_the_raw_order_after_super() -> None:
    seen: list[str] = []
    raw = _order(size_matched="4")

    class _Journal:
        def write(self, kind: str, payload: object, ts: float) -> None:
            del kind, ts
            seen.append("journal")
            assert payload is raw

    class _Proc:
        def on_order(self, event: object, condition_id: str) -> None:
            del event, condition_id
            seen.append("proc")

    stream = WalletUserStream(
        SimpleNamespace(api_key="k", api_secret="s", api_passphrase="p"),
        "0xabc",
        cast(Any, _Proc()),
        other_token=lambda _token: None,
        condition_of_token=lambda _token: None,
        journal=cast(Any, _Journal()),
    )

    def callback(msg: object) -> None:
        seen.append("callback")
        assert msg is raw

    stream.on_order_terminal = callback
    stream._on_order(cast(Any, raw))
    assert seen == ["journal", "proc", "callback"]
    assert raw["size_matched"] == "4"


def test_user_stream_is_bound_on_the_host_run_path(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = WalletUserStream(
        SimpleNamespace(api_key="k", api_secret="s", api_passphrase="p"),
        "0xabc",
        cast(Any, SimpleNamespace()),
        other_token=lambda _token: None,
        condition_of_token=lambda _token: None,
    )
    assert stream.on_order_terminal.__name__ == "_ignore_order_terminal"

    class _Gateway:
        async def cancel_all(self) -> None:
            return None

    class _Engine:
        def __init__(self) -> None:
            self.gateway = _Gateway()
            self.metas: dict[str, object] = {}
            self.user: WalletUserStream | None = None

        async def start(self) -> None:
            self.user = stream

        async def shutdown(self) -> None:
            return None

    host = object.__new__(WalletHost)
    host.engine = cast(Any, _Engine())
    host._games = ()
    host._latch = ShutdownLatch()
    host._matches_by_cid = {}
    host._closed = False

    def close() -> None:
        host._closed = True

    host.close = close  # type: ignore[method-assign]

    def _noop(_engine: object) -> None:
        del _engine

    monkeypatch.setattr("trader.wallet_host.pin_engine_identity", _noop)
    monkeypatch.setattr("trader.wallet_host.bind_user_fill_address", _noop)

    async def stop() -> None:
        raise RuntimeError("bound")

    host._boot_scan = stop  # type: ignore[method-assign]

    async def run() -> None:
        with pytest.raises(RuntimeError, match="bound"):
            await host.run()

    asyncio.run(run())
    bound = host.engine.user.on_order_terminal
    assert bound.__func__ is WalletHost._on_order_terminal
    assert bound.__self__ is host


def test_reconcile_runs_from_positions_without_a_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    _seed(store, "venue-1", qty=4.0, created_at=NOW - 90.0)
    store.apply_confirmed_fill(
        Fill(TOKEN, Side.BUY, PRICE, 4.0, "t1:venue-1", 1.0, is_maker=True),
        "t1:venue-1",
    )
    order: list[str] = []

    async def original() -> dict[str, tuple[float, float]]:
        order.append("positions")
        return {"tok": (1.0, 0.5)}

    def note(sent_at: float) -> None:
        del sent_at
        order.append("watermark")

    async def pull(_engine: object, _store: object) -> None:
        order.append("pull")

    monkeypatch.setattr("trader.engine_seams._pull_missed_fills", pull)
    monkeypatch.setattr("trader.wallet_host.time.time", lambda: NOW)
    host, engine, _wakes = _host(store, client=None)
    engine.gateway.positions = original
    store.note_rest_positions_sent = note  # type: ignore[method-assign]
    install_rest_snapshot_stamps(cast(Any, engine))

    async def sweep() -> None:
        order.append("sweep")
        await host._reconcile_unsettled_buys()

    install_rest_fill_recovery(cast(Any, engine), store, lambda: order.append("drain"), sweep)
    engine._reconcile_now = __import__("asyncio").Event()
    Engine._on_user_reconnect(cast(Any, engine))
    assert engine._reconcile_now.is_set()

    async def run() -> dict[str, tuple[float, float]]:
        return await engine.gateway.positions()

    assert asyncio.run(run()) == {"tok": (1.0, 0.5)}
    assert order == ["pull", "sweep", "watermark", "positions", "drain"]
    assert _flags(store, "venue-1")[2] is True
    store.close()
