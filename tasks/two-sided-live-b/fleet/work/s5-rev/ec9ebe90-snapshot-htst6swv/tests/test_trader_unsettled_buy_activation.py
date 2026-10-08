"""Live cancel paths open unsettled BUY rows and keep the rung until settlement."""

# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportUnknownLambdaType=false

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from polymaker.domain import Fill, OpenOrder, Side
from test_trader_session_core import _md_book, _no_book
from trader_session_fixtures import NO_TOKEN, YES_TOKEN

from shared.constants.strategy import LIVE_DOTA_MAX_POSITION_LEVELS
from strategy.policy import follow300_policy
from strategy.types import (
    BookPair,
    Budget,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    Permissions,
    RawDeltaSignal,
    RestingOrder,
    Rung,
    SignalUpdate,
    TokenBook,
    TokenInventory,
)
from trader.core_persistence import (
    CORE_SCHEMA_VERSION,
    get_unsettled_buy,
    resolved_unsettled_buys,
    unsettled_buy_notional,
)
from trader.core_recovery import RecoveryCoordinator
from trader.core_session_io import consume_core_outbox, load_core_snapshot
from trader.engine_seams import WalletUserStream
from trader.match_worker import MatchWorker
from trader.session_budget import budget_from_orders
from trader.session_core import CollateralCache, LiveCore, LiveSources, books_from_md
from trader.session_engine import StrategyCell
from trader.wallet_host import WalletHost, _durable_cancel
from trader.wallet_store import WalletFillProcessor, WalletStateStore

CASH = 1_000.0
CID = "sess"
NS = 1_000_000_000
MISSING = object()


@dataclass(frozen=True)
class BuyRig:
    path: Path
    store: WalletStateStore
    host: WalletHost
    core: LiveCore
    worker: MatchWorker
    venue_id: str
    order_id: str
    price: float
    qty: float
    wakes: list[str]


def _core(yes: str, no: str) -> LiveCore:
    core = LiveCore(
        policy=follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=MarketLimits(5.0, 0.01, 0.05, 0),
        freshness=FreshnessLimits(5.0, 16.0, 45.0),
        yes_token=yes,
        no_token=no,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    core.session_id = CID
    return core


def _resting(order_id: str, side: Literal["BUY", "SELL"], price: float, qty: float) -> RestingOrder:
    return RestingOrder(
        order_id=order_id,
        episode_id=1,
        token_index=0,
        side=side,
        price=price,
        submitted_qty=qty,
        filled_qty=0.0,
        level_index=0 if side == "BUY" else None,
        status="live",
        accepted=True,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=0,
        accepted_ns=-1_000_000_000_000,
    )


def open_buy_rig(
    tmp_path: Path,
    *,
    qty: float = 40.0,
    price: float = 0.50,
    venue_id: str = "venue-buy",
    order_id: str = "c0",
) -> BuyRig:
    path = tmp_path / "w.db"
    store = WalletStateStore(path)
    core = _core(YES_TOKEN, NO_TOKEN)
    order = _resting(order_id, "BUY", price, qty)
    core._state = replace(
        core.state,
        episode_id=1,
        episode_counter=1,
        episode_token_index=0,
        orders=(order,),
        next_order_seq=1,
        rungs=(Rung(index=0, price=price, filled_qty=0.0, live_id=order_id, done=False),),
    )
    core.bind_venue(core_id=order_id, venue_id=venue_id)
    wakes: list[str] = []
    engine = SimpleNamespace(
        state=store,
        user=None,
        paper=False,
        metas={},
        _token_cid={},
        _wake_cid=wakes.append,
        _other_token=lambda _token: None,
        gateway=SimpleNamespace(),
        alerter=SimpleNamespace(alert=lambda *_args, **_kwargs: None),
        _alerts=wakes,
    )
    host = object.__new__(WalletHost)
    host.store = store
    host.engine = cast(Any, engine)
    host._worker_by_cid = {}
    host._worker_by_token = {}
    worker = object.__new__(MatchWorker)
    worker._core = core
    worker._cid = CID
    worker._host = host
    worker._yes = YES_TOKEN
    worker._no = NO_TOKEN
    worker._yes_is_radiant = True
    worker._discovered = SimpleNamespace(game="dota")
    worker._journal = None
    worker._cell = StrategyCell()
    host._worker_by_cid[CID] = worker
    host._worker_by_token[YES_TOKEN] = worker
    return BuyRig(path, store, host, core, worker, venue_id, order_id, price, qty, wakes)


def add_order(
    rig: BuyRig,
    *,
    order_id: str,
    venue_id: str,
    side: Literal["BUY", "SELL"],
    qty: float,
    price: float,
    replace_rung: bool = False,
) -> None:
    order = _resting(order_id, side, price, qty)
    rungs = rig.core.state.rungs
    if side == "BUY":
        rung = Rung(index=0, price=price, filled_qty=0.0, live_id=order_id, done=False)
        rungs = (rung,) if replace_rung else (*rungs, rung)
    rig.core._state = replace(rig.core.state, orders=(*rig.core.state.orders, order), rungs=rungs)
    rig.core.bind_venue(core_id=order_id, venue_id=venue_id)


def run_cancel(
    rig: BuyRig,
    order_ids: list[str],
    *,
    ok: bool = True,
    during: Callable[[list[str]], None] | None = None,
) -> bool:
    async def cancel(ids: list[str]) -> bool:
        if during is not None:
            during(ids)
        return ok

    return asyncio.run(_durable_cancel(rig.host, cancel, order_ids))


def _msg(
    venue_id: str,
    size: object,
    *,
    side: str = "BUY",
    kind: str = "CANCELLATION",
    status: str = "CANCELED",
) -> dict[str, object]:
    msg: dict[str, object] = {
        "id": venue_id,
        "side": side,
        "type": kind,
        "status": status,
        "original_size": "40",
        "asset_id": YES_TOKEN,
        "price": "0.5",
    }
    if size is not MISSING:
        msg["size_matched"] = size
    return msg


def _sources(rig: BuyRig, now_ns: int, *, books: BookPair | None = None) -> LiveSources:
    return LiveSources(
        now_ns=now_ns,
        books=books,
        clock=GameClock(now_ns=now_ns, game_second=100, paused=False, game_ended=False),
        limits=rig.core.state.limits,
        permissions=Permissions(False, False, True, True, False),
        budget=Budget(CASH, float("inf"), float("inf")),
        store_yes=rig.store.position(YES_TOKEN).size,
        store_no=rig.store.position(NO_TOKEN).size,
        settled_buys=resolved_unsettled_buys(rig.store._conn, rig.core.session_id),
    )


def _budget(
    rig: BuyRig, cores: tuple[LiveCore, ...] | None = None, quoting: LiveCore | None = None
):
    owned = (rig.core,) if cores is None else cores
    card = rig.core if quoting is None else quoting
    return budget_from_orders(
        cache=CollateralCache(CASH),
        cores=owned,
        store=rig.store,
        quoting=card,
        account_cap_usdc=CASH,
    )


def _lines(caplog: pytest.LogCaptureFixture, prefix: str) -> list[str]:
    return [record.message for record in caplog.records if record.message.startswith(prefix)]


def test_ws_stream_cancel_removes_the_fork_order_before_insert(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    rig.store.upsert_order(OpenOrder(rig.venue_id, YES_TOKEN, Side.BUY, rig.price, rig.qty))
    stream = WalletUserStream(
        SimpleNamespace(api_key="k", api_secret="s", api_passphrase="p"),
        "0xabc",
        WalletFillProcessor(rig.store),
        other_token=lambda _token: None,
        condition_of_token=lambda _token: None,
    )
    stream.on_order_terminal = rig.host._on_order_terminal
    stream._on_order(cast(Any, _msg(rig.venue_id, "3")))
    assert rig.venue_id not in rig.store.orders
    row = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert row is not None
    assert row.proven is True
    assert row.qty == 3.0
    assert row.token_id == YES_TOKEN
    assert row.session_id == CID
    assert row.price == rig.price
    order = next(item for item in rig.core.state.orders if item.order_id == rig.order_id)
    assert order.status == "gone"
    assert rig.core.state.rungs[0].live_id == rig.order_id
    assert rig.core.reserved_buy_notional() == 0.0
    assert unsettled_buy_notional(rig.store._conn, None) == pytest.approx(1.5)
    rig.store.close()


@pytest.mark.parametrize("raw", [MISSING, "", "   ", "nope", True, -1, float("nan")])
def test_ws_cancel_without_a_quantity_holds_the_submitted_reserve(
    tmp_path: Path, raw: object
) -> None:
    rig = open_buy_rig(tmp_path)
    rig.host._on_order_terminal(_msg(rig.venue_id, raw))
    row = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert row is not None
    assert (row.qty, row.proven, row.resolved) == (rig.qty, False, False)
    assert unsettled_buy_notional(rig.store._conn, CID) == pytest.approx(rig.qty * rig.price)
    assert rig.wakes == [CID]
    order = next(item for item in rig.core.state.orders if item.order_id == rig.order_id)
    assert order.status == "gone"
    rig.store.close()


@pytest.mark.parametrize("raw", ["0", 0, 0.0])
def test_ws_zero_quantity_resolves_the_new_row(tmp_path: Path, raw: object) -> None:
    rig = open_buy_rig(tmp_path)
    rig.host._on_order_terminal(_msg(rig.venue_id, raw))
    row = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert row is not None
    assert (row.qty, row.proven, row.resolved) == (0.0, True, True)
    assert unsettled_buy_notional(rig.store._conn, None) == 0.0
    rig.core.finish_cycle(_sources(rig, NS))
    assert all(order.order_id != rig.order_id for order in rig.core.state.orders)
    rig.store.close()


def test_ws_skip_full_execution_sell_and_unknown_venue(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    rig.host._on_order_terminal(_msg(rig.venue_id, "40", kind="UPDATE", status="MATCHED"))
    rig.host._on_order_terminal(_msg(rig.venue_id, "0", side="SELL"))
    rig.host._on_order_terminal(_msg("someone-else", "0"))
    rig.host._on_order_terminal("not-a-dict")
    assert get_unsettled_buy(rig.store._conn, rig.venue_id) is None
    order = next(item for item in rig.core.state.orders if item.order_id == rig.order_id)
    assert order.status == "live"
    rig.store.close()


def test_ws_during_http_keeps_the_proved_row(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    stamp: dict[str, float] = {}

    def during(_ids: list[str]) -> None:
        rig.host._on_order_terminal(_msg(rig.venue_id, "3"))
        row = get_unsettled_buy(rig.store._conn, rig.venue_id)
        assert row is not None
        stamp["created"] = row.created_at
        stamp["updated"] = row.updated_at

    assert run_cancel(rig, [rig.venue_id], during=during) is True
    row = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert row is not None
    assert (row.qty, row.proven, row.resolved) == (3.0, True, False)
    assert row.created_at == stamp["created"]
    assert row.updated_at == stamp["updated"]
    assert sum(1 for _ in rig.store._conn.execute("SELECT 1 FROM unsettled_buys")) == 1
    order = next(item for item in rig.core.state.orders if item.order_id == rig.order_id)
    assert order.status == "gone"
    rig.store.close()


def test_zero_proof_then_late_http_leaves_the_new_rung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("trader.match_worker.core_now_ns", lambda: NS)
    rig = open_buy_rig(tmp_path)
    rig.core.enqueue(SignalUpdate(now_ns=NS, signal=RawDeltaSignal(0.05, NS, NS, 0.51, 0, 0)))

    def during(_ids: list[str]) -> None:
        rig.host._on_order_terminal(_msg(rig.venue_id, "0"))
        rig.core.finish_cycle(_sources(rig, NS, books=_quoted_books(NS)))
        assert all(order.order_id != rig.order_id for order in rig.core.state.orders)
        rig.host._on_order_terminal(_msg(rig.venue_id, "0"))

    assert run_cancel(rig, [rig.venue_id], during=during) is True
    row = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert row is not None
    assert (row.qty, row.resolved) == (0.0, True)
    assert sum(1 for _ in rig.store._conn.execute("SELECT 1 FROM unsettled_buys")) == 1
    newbie = next(order for order in rig.core.state.orders if order.order_id != rig.order_id)
    assert newbie.status in ("live", "pending")
    assert rig.core.state.rungs[0].live_id == newbie.order_id
    rig.store.close()


def test_partial_proof_survives_a_late_cancel_timeout(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    rig.host._on_order_terminal(_msg(rig.venue_id, "3"))
    assert run_cancel(rig, [rig.venue_id], ok=False) is False
    order = next(item for item in rig.core.state.orders if item.order_id == rig.order_id)
    assert order.status == "gone"
    assert rig.core.reserved_buy_notional() == 0.0
    assert unsettled_buy_notional(rig.store._conn, None) == pytest.approx(1.5)
    assert rig.core.state.rungs[0].live_id == rig.order_id
    rig.core.enqueue(SignalUpdate(now_ns=NS, signal=RawDeltaSignal(0.05, NS, NS, 0.51, 0, 0)))
    rig.core.run_cycle(_sources(rig, NS, books=_quoted_books(NS)))
    planned = rig.core.take_plan()
    cancels = () if planned is None else planned.to_cancel
    assert rig.venue_id not in cancels
    rig.store.close()


def test_cancelled_buy_keeps_the_bid_and_reserves_once(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    assert run_cancel(rig, [rig.venue_id]) is True
    yes = _md_book([(0.50, 40.0)], [(0.52, 80.0)])
    pair = books_from_md(yes=yes, no=_no_book(), now_ns=0, own_orders=rig.core.state.orders)
    assert pair is not None
    assert pair.tokens[0].bid == 0.50
    assert pair.tokens[0].bid_size == 40.0
    assert rig.core.reserved_buy_notional() == 0.0
    budget = _budget(rig)
    assert budget.cash_usdc == pytest.approx(CASH - 20.0)
    assert budget.account_cap_room_usdc == pytest.approx(CASH - 20.0)
    rig.core.enqueue(SignalUpdate(now_ns=NS, signal=RawDeltaSignal(0.05, NS, NS, 0.51, 0, 0)))
    rig.core.run_cycle(_sources(rig, NS, books=_quoted_books(NS)))
    assert any(
        order.order_id == rig.order_id and order.status == "gone" for order in rig.core.state.orders
    )
    assert rig.core.state.rungs[0].live_id == rig.order_id
    rig.store.close()


def test_hidden_partial_fill_keeps_the_rung_until_confirmed(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    run_cancel(rig, [rig.venue_id])
    key = f"t1:{rig.venue_id}"
    rig.store.apply_matched_fill(
        Fill(YES_TOKEN, Side.BUY, rig.price, 10.0, key, 1.0, is_maker=True),
        key,
    )
    consume_core_outbox(
        store=rig.store,
        core=rig.core,
        identity=rig.worker._identity(),
        tokens=frozenset({YES_TOKEN, NO_TOKEN}),
    )
    order = next(item for item in rig.core.state.orders if item.order_id == rig.order_id)
    assert order.status == "gone"
    assert order.filled_qty == 10.0
    assert rig.core.state.rungs[0].live_id == rig.order_id
    assert unsettled_buy_notional(rig.store._conn, None) == pytest.approx(15.0)
    assert (
        rig.store.apply_matched_fill(
            Fill(YES_TOKEN, Side.BUY, rig.price, 10.0, key, 2.0, is_maker=True),
            key,
        )
        is False
    )
    consume_core_outbox(
        store=rig.store,
        core=rig.core,
        identity=rig.worker._identity(),
        tokens=frozenset({YES_TOKEN, NO_TOKEN}),
    )
    again = next(item for item in rig.core.state.orders if item.order_id == rig.order_id)
    assert again.filled_qty == 10.0
    row = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert row is not None and row.resolved is False
    rig.store.close()


def test_hidden_full_fill_retires_the_core_before_the_row(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    run_cancel(rig, [rig.venue_id])
    key = f"t1:{rig.venue_id}"
    rig.store.apply_matched_fill(
        Fill(YES_TOKEN, Side.BUY, rig.price, rig.qty, key, 1.0, is_maker=True),
        key,
    )
    consume_core_outbox(
        store=rig.store,
        core=rig.core,
        identity=rig.worker._identity(),
        tokens=frozenset({YES_TOKEN, NO_TOKEN}),
    )
    assert all(order.order_id != rig.order_id for order in rig.core.state.orders)
    row = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert row is not None and row.resolved is False
    assert unsettled_buy_notional(rig.store._conn, None) == 0.0
    rig.store.apply_confirmed_fill(
        Fill(YES_TOKEN, Side.BUY, rig.price, rig.qty, key, 2.0, is_maker=True),
        key,
    )
    rig.host._apply_unsettled_proofs(())
    closed = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert closed is not None and closed.resolved is True and closed.qty == rig.qty
    resolved = resolved_unsettled_buys(rig.store._conn, CID)
    assert resolved[rig.venue_id] == rig.qty
    rig.store.close()


def test_matched_failed_then_later_confirmed_restores_reserve(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path, qty=8.0)
    run_cancel(rig, [rig.venue_id])
    assert unsettled_buy_notional(rig.store._conn, None) == pytest.approx(4.0)
    matched = f"t-matched:{rig.venue_id}"
    rig.store.apply_matched_fill(
        Fill(YES_TOKEN, Side.BUY, rig.price, 8.0, matched, 1.0, is_maker=True),
        matched,
    )
    assert unsettled_buy_notional(rig.store._conn, None) == 0.0
    assert get_unsettled_buy(rig.store._conn, rig.venue_id) is not None
    failed = Fill(YES_TOKEN, Side.BUY, rig.price, 8.0, matched, 1.0, is_maker=True)
    assert rig.store.apply_failed_fill(failed, matched) is True
    assert unsettled_buy_notional(rig.store._conn, None) == pytest.approx(4.0)
    confirmed = f"t-confirmed:{rig.venue_id}"
    rig.store.apply_confirmed_fill(
        Fill(YES_TOKEN, Side.BUY, rig.price, 8.0, confirmed, 3.0, is_maker=True),
        confirmed,
    )
    rig.host._apply_unsettled_proofs(())
    closed = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert closed is not None and closed.resolved is True and closed.qty == 8.0
    assert unsettled_buy_notional(rig.store._conn, None) == 0.0
    assert rig.store.position(YES_TOKEN).size == pytest.approx(8.0)
    budget = _budget(rig)
    assert budget.cash_usdc == pytest.approx(CASH)
    assert budget.account_cap_room_usdc == pytest.approx(CASH - 4.0)
    rig.store.close()


def test_superseded_keeps_reserve_and_still_sells(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path, qty=8.0)
    run_cancel(rig, [rig.venue_id])
    rig.store.note_writedown_snapshot(YES_TOKEN, 50.0)
    key = f"t-old:{rig.venue_id}"
    assert (
        rig.store.apply_matched_fill(
            Fill(YES_TOKEN, Side.BUY, rig.price, 8.0, key, 10.0, is_maker=True),
            key,
        )
        is False
    )
    assert rig.store.ledger_status(key) == "SUPERSEDED"
    assert unsettled_buy_notional(rig.store._conn, None) == pytest.approx(4.0)
    assert all(order.filled_qty == 0.0 for order in rig.core.state.orders)
    rig.core._state = replace(
        rig.core.state,
        books=_quoted_books(2),
        clock=GameClock(now_ns=2, game_second=100, paused=False, game_ended=False),
    )
    rig.store.set_position(YES_TOKEN, 8.0, 0.5)
    rig.core.note_recovery(now_ns=1)
    rig.core.drain_apply()
    coordinator = RecoveryCoordinator(
        store=rig.store,
        session_id=CID,
        yes_token=YES_TOKEN,
        no_token=NO_TOKEN,
        min_order_size=5.0,
    )
    assert coordinator.proof_blocks(rig.core) is None
    assert (
        coordinator.accept_if_proven(
            core=rig.core,
            now_ns=2,
            now_wall_s=1.0,
            rest_yes=8.0,
            rest_no=0.0,
            last_buy_unix=None,
        )
        is True
    )
    assert rig.core.state.inventory[0].qty == 8.0
    assert any(
        order.order_id == rig.order_id and order.status == "gone" for order in rig.core.state.orders
    )
    sells = [place for place in rig.core._deferred_plan.places if place.side == "SELL"]
    assert len(sells) == 1
    assert sells[0].quantity == 8.0
    rig.store._conn.execute(
        "UPDATE unsettled_buys SET created_at=? WHERE venue_id=?",
        (0.0, rig.venue_id),
    )
    rig.store._conn.commit()
    alerts: list[str] = []

    def alert(key: str, _message: str) -> None:
        alerts.append(key)

    rig.host.engine.alerter = SimpleNamespace(alert=alert)
    before = unsettled_buy_notional(rig.store._conn, None)
    rig.host._alert_stale_unsettled_buys(UNSETTLED_NOW)
    assert alerts == [f"unsettled_buy:{rig.venue_id[:8]}"]
    assert unsettled_buy_notional(rig.store._conn, None) == before
    rig.store.close()


UNSETTLED_NOW = 10_000.0


def _quoted_books(now_ns: int) -> BookPair:
    return BookPair(
        tokens=(
            TokenBook(0, 0.50, 0.52, 40.0, 100.0, now_ns),
            TokenBook(1, 0.48, 0.50, 70.0, 70.0, now_ns),
        )
    )


def test_restart_keeps_the_row_and_retires_after_workerless_proof(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="trader.session_core")
    rig = open_buy_rig(tmp_path)
    rig.core._state = replace(
        rig.core.state,
        sell_only=True,
        inventory=(
            TokenInventory(0, 8.0, 4.0, None),
            TokenInventory(1, 0.0, 0.0, None),
        ),
    )
    run_cancel(rig, [rig.venue_id])
    path = rig.path
    rig.store.close()
    store = WalletStateStore(path)
    other = _core("OTHER_YES", "OTHER_NO")
    other.session_id = "other-map"
    budget = budget_from_orders(
        cache=CollateralCache(CASH),
        cores=(other,),
        store=store,
        quoting=other,
        account_cap_usdc=CASH,
    )
    assert budget.cash_usdc == pytest.approx(CASH - 20.0)
    assert budget.account_cap_room_usdc == pytest.approx(CASH - 20.0)
    assert budget.cap_room_usdc == pytest.approx(other.max_position_levels * 65.0)
    store.set_position(YES_TOKEN, 8.0, 0.5)
    raw = store._conn.execute("SELECT schema_version, checkpoint FROM core_sessions").fetchone()
    assert raw is not None
    assert int(raw["schema_version"]) == CORE_SCHEMA_VERSION
    payload = json.loads(str(raw["checkpoint"]))
    assert payload["schema_version"] == CORE_SCHEMA_VERSION
    assert payload["orders"][0]["status"] == "unknown"
    assert payload["orders"][0]["cancel_reason"] == "unsettled"
    fresh = _core(YES_TOKEN, NO_TOKEN)
    load_core_snapshot(store=store, core=fresh, session_id=CID)
    assert fresh.state.orders[0].status == "gone"
    assert fresh.state.rungs[0].live_id == "c0"
    assert fresh.state.sell_only is True
    assert fresh.state.inventory[0].qty == 8.0
    assert fresh.state.episode_id == 1
    host = object.__new__(WalletHost)
    host.store = store
    host.engine = cast(
        Any,
        SimpleNamespace(
            state=store,
            _token_cid={},
            _wake_cid=lambda _cid: None,
            alerter=SimpleNamespace(alert=lambda *_args, **_kwargs: None),
        ),
    )
    host._worker_by_cid = {}
    host._worker_by_token = {}
    host._on_order_terminal(_msg("venue-buy", "0"))
    proved = get_unsettled_buy(store._conn, "venue-buy")
    assert proved is not None and proved.resolved is True and proved.qty == 0.0
    retired = _core(YES_TOKEN, NO_TOKEN)
    load_core_snapshot(store=store, core=retired, session_id=CID)
    retired_rig = BuyRig(path, store, host, retired, rig.worker, "venue-buy", "c0", 0.5, 40.0, [])
    retired.finish_cycle(_sources(retired_rig, NS))
    assert all(order.order_id != "c0" for order in retired.state.orders)
    assert any(
        "wait_ms=unavailable origin=restored" in line
        for line in _lines(caplog, "trader core buy settled")
    )
    store.close()


def test_cancel_callback_logs_wait_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger="trader.session_core")
    rig = open_buy_rig(tmp_path)
    monkeypatch.setattr("trader.match_worker.core_now_ns", lambda: 2 * NS)
    rig.host._on_order_terminal(_msg(rig.venue_id, "0"))
    assert _lines(caplog, "trader core buy gone") == [f"trader core buy gone id={rig.order_id}"]
    monkeypatch.setattr("trader.match_worker.core_now_ns", lambda: 9 * NS)
    rig.worker.note_cancel_result([rig.venue_id], True)
    assert _lines(caplog, "trader core buy gone") == [f"trader core buy gone id={rig.order_id}"]
    assert rig.core._buy_cancel_timing[rig.order_id].started_ns == 2 * NS
    rig.core.finish_cycle(_sources(rig, 5 * NS))
    assert _lines(caplog, "trader core buy settled") == [
        f"trader core buy settled id={rig.order_id} wait_ms=3000 cause=buy_settled"
    ]
    rig.store.close()
