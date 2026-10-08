"""Strict REST, shutdown latch, daily kill, and live attach/detach."""

# The session composes private engine seams by design; pinning them is the
# point of these tests.
# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false, reportUnknownLambdaType=false

import asyncio
import atexit
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never, cast

import polymaker.engine as polymaker_engine
import pytest
from polymaker.config import RiskConfig, StrategyProfile
from polymaker.domain import (
    Fill,
    MarketMeta,
    OpenOrder,
    Position,
    Quote,
    Regime,
    Side,
    TargetQuotes,
    TradeState,
)
from polymaker.execution.gateway import ExecutionGateway
from polymaker.execution.reconciler import reconcile
from polymaker.journal import Journal
from polymaker.marketdata.service import MarketDataService
from polymaker.risk.manager import RiskDecision, RiskManager
from polymaker.state.tracker import TradeEvent
from trader_session_fixtures import YES_TOKEN, make_meta

import trader.engine_seams as seams_mod
from shared.constants.strategy import EXIT_SETTLE_SECONDS
from shared.utils.log import setup_logging
from trader.chain_balances import ChainSnapshot
from trader.engine_seams import (
    ONCHAIN_EXCESS_BLOCK_ROUNDS,
    BookReadiness,
    CidOrderErrors,
    GatewayClosed,
    RestUnproven,
    ShutdownLatch,
    SlimJournal,
    WalletUserStream,
    attach_market,
    bind_user_fill_address,
    detach_market,
    fence_no_orders,
    install_book_readiness,
    install_core_quoter_wake,
    install_heartbeat_boot_grace,
    install_strict_rest,
    patch_engine_classes,
    reset_order_error_rate,
    restore_engine_classes,
    sell_is_droppable,
    stop_quoter,
    wrap_alert_transitions,
    wrap_inventory_place_guard,
    wrap_position_divergence,
    wrap_risk_from_ledger,
)
from trader.fill_parsing import fill_key
from trader.session_engine import StrategyCell
from trader.wallet_store import WalletFillProcessor, WalletStateStore


@pytest.fixture(autouse=True)
def chain_telegrams(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Divergence tests must not send a real Telegram."""
    messages: list[str] = []
    monkeypatch.setattr(seams_mod, "notify_in_background", messages.append)
    return messages


class _FakeGateway:
    def __init__(self) -> None:
        self.place_calls = 0
        self.released = asyncio.Event()
        self.started = asyncio.Event()

    async def place(self, quotes: list[Quote], meta: MarketMeta) -> list[OpenOrder]:
        self.place_calls += 1
        self.started.set()
        await self.released.wait()
        return [
            OpenOrder("late", quotes[0].token_id, quotes[0].side, quotes[0].price, quotes[0].size)
        ]

    async def cancel(self, order_ids: list[str]) -> bool:
        del order_ids
        return True

    async def market_order(
        self,
        token_id: str,
        side: Side,
        amount: float,
        meta: MarketMeta,
        *,
        fak: bool = True,
    ) -> dict[str, str]:
        del token_id, side, amount, meta, fak
        return {"status": "matched"}


def _empty_engine(meta: MarketMeta | None = None) -> SimpleNamespace:
    engine = SimpleNamespace(
        metas={},
        profiles={},
        est={},
        regime_m={},
        _dirty={},
        _locks={},
        _token_cid={},
        _task_specs={},
        _tasks={},
        _sweep={},
        _last_quote_fv={},
        _merging=set(),
        _halted=set(),
        _running=False,
        user=None,
        md=MarketDataService(),
        state=SimpleNamespace(persist_token_cid=lambda *_args: None),
        risk=SimpleNamespace(_marks={}),
        _make_estimators=lambda _profile: object(),
    )
    if meta is not None:
        engine.metas[meta.condition_id] = meta
        engine.profiles[meta.condition_id] = StrategyProfile()
        engine.est[meta.condition_id] = object()
        engine.regime_m[meta.condition_id] = object()
        engine._dirty[meta.condition_id] = asyncio.Event()
        engine._locks[meta.condition_id] = asyncio.Lock()
        engine._token_cid[meta.yes.token_id] = meta.condition_id
        engine._token_cid[meta.no.token_id] = meta.condition_id
        engine.md.set_markets([(meta.condition_id, [meta.yes.token_id, meta.no.token_id])])
    return engine


def test_mocked_500_on_open_orders_does_not_wipe_local_orders() -> None:
    """Unproven REST must raise so reconcile cannot replace_open_orders with []."""

    async def boom() -> list[OpenOrder]:
        raise RestUnproven("open_orders unproven")

    async def run() -> None:
        with pytest.raises(RestUnproven):
            await boom()

    asyncio.run(run())


def test_fence_unproven_is_not_cleanup() -> None:
    """A fence must not treat Unproven open_orders as proven-empty."""

    async def boom() -> list[OpenOrder]:
        raise RestUnproven("open_orders unproven")

    engine = SimpleNamespace(gateway=SimpleNamespace(open_orders=boom))
    proven = asyncio.run(fence_no_orders(cast(Any, engine), {YES_TOKEN}))
    assert proven is False


def test_hung_place_after_shutdown_is_not_proven() -> None:
    """A place still in-flight when teardown starts keeps drain from proving."""
    gateway = _FakeGateway()
    latch = ShutdownLatch()
    latch.wrap_gateway(cast(Any, gateway))
    meta = make_meta()
    quote = Quote(meta.yes.token_id, Side.BUY, 0.4, 5.0)

    async def run() -> None:
        place_task = asyncio.create_task(gateway.place([quote], meta))
        await gateway.started.wait()
        latch.close()
        drained = await latch.drain(0.2)
        assert drained is False
        gateway.released.set()
        await place_task

    asyncio.run(run())


def test_place_after_latch_raises_without_calling_through() -> None:
    """Once closed, a non-empty place must raise GatewayClosed, not look like a partial batch."""
    gateway = _FakeGateway()
    latch = ShutdownLatch()
    latch.wrap_gateway(cast(Any, gateway))
    latch.close()
    meta = make_meta()
    quote = Quote(meta.yes.token_id, Side.BUY, 0.4, 5.0)

    async def run() -> None:
        with pytest.raises(GatewayClosed):
            await gateway.place([quote], meta)
        assert gateway.place_calls == 0

    asyncio.run(run())


def test_closed_place_empty_quotes_returns_empty() -> None:
    """An empty quote list may still return [] after close."""
    gateway = _FakeGateway()
    latch = ShutdownLatch()
    latch.wrap_gateway(cast(Any, gateway))
    latch.close()
    meta = make_meta()

    async def run() -> None:
        placed = await gateway.place([], meta)
        assert placed == []
        assert gateway.place_calls == 0

    asyncio.run(run())


def test_cancelled_place_stays_in_flight_until_inner_finishes() -> None:
    """Cancelling the awaiting task must not drop in_flight until the inner call ends."""
    gateway = _FakeGateway()
    latch = ShutdownLatch()
    latch.wrap_gateway(cast(Any, gateway))
    meta = make_meta()
    quote = Quote(meta.yes.token_id, Side.BUY, 0.4, 5.0)

    async def run() -> None:
        place_task = asyncio.create_task(gateway.place([quote], meta))
        await gateway.started.wait()
        place_task.cancel()
        await asyncio.sleep(0)
        assert latch.in_flight == 1
        gateway.released.set()
        assert await latch.drain(1.0) is True
        with pytest.raises(asyncio.CancelledError):
            await place_task
        assert latch.in_flight == 0

    asyncio.run(run())


def test_closed_market_order_returns_failed_without_calling_through() -> None:
    """Dust FAK after latch close must not hit the venue."""

    class Gateway(_FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.market_calls = 0

        async def market_order(
            self,
            token_id: str,
            side: Side,
            amount: float,
            meta: MarketMeta,
            *,
            fak: bool = True,
        ) -> dict[str, str]:
            del token_id, side, amount, meta, fak
            self.market_calls += 1
            return {"status": "matched"}

    gateway = Gateway()
    latch = ShutdownLatch()
    latch.wrap_gateway(cast(Any, gateway))
    latch.close()
    meta = make_meta()

    async def run() -> None:
        resp = await gateway.market_order(YES_TOKEN, Side.SELL, 1.0, meta, fak=True)
        assert resp == {"status": "failed", "error": "closed"}
        assert gateway.market_calls == 0

    asyncio.run(run())


def test_install_strict_rest_keeps_positions_and_wraps_cancel_paths() -> None:
    """Positions stays untouched while open-orders and both cancel paths gain proof."""

    async def original_positions() -> dict[str, tuple[float, float]]:
        raise RuntimeError("data-api 500")

    async def original_open() -> list[OpenOrder]:
        return []

    async def original_cancel(order_ids: list[str]) -> bool:
        del order_ids
        return True

    async def original_cancel_asset(asset_id: str) -> bool:
        del asset_id
        return True

    gateway = SimpleNamespace(
        open_orders=original_open,
        positions=original_positions,
        cancel=original_cancel,
        cancel_asset=original_cancel_asset,
        _paper=False,
        _client=object(),
        _io=None,
    )
    engine = SimpleNamespace(paper=False, gateway=gateway)
    install_strict_rest(cast(Any, engine))
    assert gateway.positions is original_positions
    assert gateway.cancel is not original_cancel
    assert gateway.cancel_asset is not original_cancel_asset

    async def run() -> None:
        with pytest.raises(RuntimeError, match="data-api 500"):
            await gateway.positions()
        with pytest.raises(RestUnproven):
            await gateway.open_orders()

    asyncio.run(run())


def test_empty_proxy_does_not_pass_empty_string_into_websockets_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty proxy env value is no proxy, not proxy=''."""
    captured: list[dict[str, object]] = []

    class _CM:
        async def __aenter__(self) -> Never:
            raise RuntimeError("connect reached")

        async def __aexit__(self, *args: object) -> bool:
            del args
            return False

    def fake_connect(*args: object, **kwargs: object) -> _CM:
        del args
        captured.append(dict(kwargs))
        return _CM()

    monkeypatch.setattr("trader.engine_seams.websockets.connect", fake_connect)
    creds = SimpleNamespace(api_key="k", api_secret="s", api_passphrase="p")
    stream = WalletUserStream(
        creds,
        "0xabc",
        cast(Any, SimpleNamespace()),
        other_token=lambda _token: None,
        condition_of_token=lambda _token: None,
        proxy="",
    )

    async def run() -> None:
        with pytest.raises(RuntimeError, match="connect reached"):
            await stream._connect_and_listen()

    asyncio.run(run())
    assert captured
    assert "proxy" not in captured[0]


def test_fill_then_new_engine_daily_pnl_matches_ledger(tmp_path: Path) -> None:
    """daily_pnl is ledger cash plus marks, not a wider in-memory counter."""
    db_path = tmp_path / "wallet.db"
    store = WalletStateStore(db_path)
    processor = WalletFillProcessor(store)
    key = fill_key("t1", "o1")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.MATCHED, 1.0),
        "cid",
    )
    store.close()

    restarted = WalletStateStore(db_path)
    risk = RiskManager(RiskConfig(daily_loss_kill_usdc=20.0), restarted)
    engine = SimpleNamespace(risk=risk, md=SimpleNamespace(books={}), state=restarted)
    wrap_risk_from_ledger(cast(Any, engine), restarted)
    assert risk.net_cash == restarted.ledger_net_cash()
    assert risk.daily_pnl == 0.0
    restarted.close()


def test_bind_user_fill_address_uses_funder_not_signer() -> None:
    """User-WS fill matching must use the Safe, not the EOA that signs."""
    user = SimpleNamespace(_address="0xsigner")
    engine = SimpleNamespace(
        user=user,
        gateway=SimpleNamespace(funder="0x941aa5589961e33c54365a27a3223c916e6a24d9"),
    )
    bind_user_fill_address(cast(Any, engine))
    assert user._address == "0x941aa5589961e33c54365a27a3223c916e6a24d9"


def test_reset_order_error_rate_clears_the_sticky_breaker() -> None:
    """Fork counters and this cid reset; a sibling cid keeps its place-error count."""
    store = SimpleNamespace()
    risk = RiskManager(RiskConfig(), cast(Any, store))
    risk._order_attempts = 20
    risk._order_errors = 9
    tracker = CidOrderErrors(0.25)
    cid_a = "0xaaaaaaaa"
    cid_b = "0xbbbbbbbb"
    for _ in range(14):
        tracker.note(cid_a, True)
        tracker.note(cid_b, True)
    for _ in range(6):
        tracker.note(cid_a, False)
        tracker.note(cid_b, False)
    cast(Any, risk)._cid_order_errors = tracker
    assert risk.error_rate == 0.45
    assert tracker.trip_reason(cid_a) is not None
    assert cid_a in tracker._tripped
    reset_order_error_rate(risk, cid_a)
    assert risk.error_rate == 0.0
    assert tracker.trip_reason(cid_a) is None
    assert cid_a not in tracker._tripped
    assert tracker.trip_reason(cid_b) is not None


def test_matched_then_confirmed_does_not_double_net_cash_through_note_fill(
    tmp_path: Path,
) -> None:
    """note_fill ignores Engine amounts and copies in-memory ledger cash."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("t1", "o1")
    risk = RiskManager(RiskConfig(), store)
    engine = SimpleNamespace(risk=risk, md=SimpleNamespace(books={}), state=store)
    wrap_risk_from_ledger(cast(Any, engine), store)
    processor._on_fill = risk.note_fill
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.MATCHED, 1.0), "cid"
    )
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.CONFIRMED, 2.0), "cid"
    )
    assert risk.net_cash == store.ledger_net_cash() == -4.0
    store.close()


def test_attach_during_reconcile_iteration_does_not_raise() -> None:
    """Copy-assign metas so a live reconcile for-loop does not see a size change."""
    meta_a = make_meta(condition_id="cid-a")
    meta_b = make_meta(condition_id="cid-b")
    engine = _empty_engine(meta_a)

    async def reconcile() -> None:
        for cid, meta in engine.metas.items():
            await asyncio.sleep(0)
            assert cid == meta.condition_id

    async def run() -> None:
        task = asyncio.create_task(reconcile())
        await asyncio.sleep(0)
        attach_market(cast(Any, engine), meta_b, StrategyProfile(), StrategyCell(), BookReadiness())
        await task

    asyncio.run(run())
    assert meta_b.condition_id in engine.metas
    assert engine._token_cid[meta_b.yes.token_id] == meta_b.condition_id


def test_attach_writes_token_cid_last() -> None:
    """_token_cid is assigned after the other per-market dicts."""
    meta = make_meta()
    order: list[str] = []

    class _Engine:
        def __init__(self) -> None:
            self._metas: dict[str, MarketMeta] = {}
            self.profiles: dict[str, object] = {}
            self.est: dict[str, object] = {}
            self.regime_m: dict[str, object] = {}
            self._dirty: dict[str, object] = {}
            self._locks: dict[str, object] = {}
            self._tc: dict[str, str] = {}
            self._task_specs: dict[str, object] = {}
            self._tasks: dict[str, object] = {}
            self._running = False
            self.user = None
            self.md = MarketDataService()
            self.state = SimpleNamespace(persist_token_cid=lambda *_args: None)

        def _make_estimators(self, profile: StrategyProfile) -> object:
            del profile
            return object()

        @property
        def metas(self) -> dict[str, MarketMeta]:
            return self._metas

        @metas.setter
        def metas(self, value: dict[str, MarketMeta]) -> None:
            order.append("metas")
            self._metas = value

        @property
        def _token_cid(self) -> dict[str, str]:
            return self._tc

        @_token_cid.setter
        def _token_cid(self, value: dict[str, str]) -> None:
            order.append("token_cid")
            self._tc = value

    engine = _Engine()
    attach_market(cast(Any, engine), meta, StrategyProfile(), StrategyCell(), BookReadiness())
    assert order[-1] == "token_cid"
    assert "metas" in order[:-1]


def test_core_quoter_wake_caps_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trader.engine_seams.core_now_ns", lambda: 1_000_000_000)
    engine = SimpleNamespace(_next_wake_s=lambda _cid, base_tick: base_tick)
    core = SimpleNamespace(next_wake_ns=1_400_000_000)
    install_core_quoter_wake(cast(Any, engine), {"cid-a": cast(Any, core)})
    assert engine._next_wake_s("cid-a", 2.0) == pytest.approx(0.4)
    assert engine._next_wake_s("missing", 2.0) == 2.0


def test_core_quoter_wake_fires_a_past_deadline_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A core the fork skipped keeps its stale deadline; the quoter must not spin on it."""
    monkeypatch.setattr("trader.engine_seams.core_now_ns", lambda: 5_000_000_000)
    engine = SimpleNamespace(_next_wake_s=lambda _cid, base_tick: base_tick)
    core = SimpleNamespace(next_wake_ns=3_000_000_000)
    install_core_quoter_wake(cast(Any, engine), {"cid-a": cast(Any, core)})
    assert engine._next_wake_s("cid-a", 2.0) == 0.0
    assert engine._next_wake_s("cid-a", 2.0) == 2.0
    assert engine._next_wake_s("cid-a", 2.0) == 2.0
    core.next_wake_ns = 5_000_000_000
    assert engine._next_wake_s("cid-a", 2.0) == 0.0
    assert engine._next_wake_s("cid-a", 2.0) == 2.0


def test_stop_quoter_keeps_supervisor_spec() -> None:
    """Removing the live quote task must leave a no-op factory so _supervise cannot KeyError."""

    async def run() -> None:
        engine = SimpleNamespace(_running=True, _task_specs={}, _tasks={})

        async def quote() -> None:
            raise RuntimeError("quote died")

        engine._task_specs["quote:cid-aaaaaa"] = quote
        engine._tasks["quote:cid-aaaaaa"] = asyncio.create_task(quote(), name="quote:cid-aaaaaa")
        with pytest.raises(RuntimeError):
            await engine._tasks["quote:cid-aaaaaa"]
        await stop_quoter(cast(Any, engine), "cid-aaaaaa")
        factory = engine._task_specs["quote:cid-aaaaaa"]
        restarted = asyncio.create_task(cast(Callable[[], Any], factory)(), name="idle")
        restarted.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restarted

    asyncio.run(run())


def test_detach_removes_mds_books() -> None:
    """Detach must drop books, token_condition, and subs for that market."""
    meta = make_meta()
    engine = _empty_engine(meta)
    assert meta.yes.token_id in engine.md.books
    detach_market(cast(Any, engine), meta.condition_id, BookReadiness())
    assert meta.yes.token_id not in engine.md.books
    assert meta.yes.token_id not in engine.md._token_condition
    assert meta.yes.token_id not in engine.md._subs
    assert meta.condition_id not in engine.metas
    assert meta.yes.token_id not in engine._token_cid


def test_user_ws_attach_sets_markets_and_closes_socket() -> None:
    """Live attach must push markets and force a user-WS reconnect."""
    meta = make_meta()
    closed = {"n": 0}

    class _User:
        def __init__(self) -> None:
            self.markets: list[str] = []

        def set_markets(self, condition_ids: list[str]) -> None:
            self.markets = condition_ids

        def close_socket(self) -> None:
            closed["n"] += 1

    engine = _empty_engine()
    engine.user = _User()
    attach_market(cast(Any, engine), meta, StrategyProfile(), StrategyCell(), BookReadiness())
    assert engine.user.markets == [meta.condition_id]
    assert closed["n"] == 1


class _FakeMarketWs:
    """Sync close stand-in for md._ws."""

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_attach_after_live_market_ws_closes_socket() -> None:
    """Live attach must set_markets then close md._ws so md.run resubscribes."""
    meta = make_meta()
    engine = _empty_engine()
    ws = _FakeMarketWs()
    engine.md._ws = ws
    attach_market(cast(Any, engine), meta, StrategyProfile(), StrategyCell(), BookReadiness())
    assert meta.yes.token_id in engine.md._subs
    assert ws.close_calls == 1
    assert engine.md._ws is None


def test_detach_after_live_market_ws_closes_socket() -> None:
    """Detach must close md._ws so the remaining (or next) subscribe set is sent."""
    meta = make_meta()
    engine = _empty_engine(meta)
    ws = _FakeMarketWs()
    engine.md._ws = ws
    detach_market(cast(Any, engine), meta.condition_id, BookReadiness())
    assert ws.close_calls == 1
    assert engine.md._ws is None


def test_second_attach_closes_market_ws() -> None:
    """A second CID must close the live market socket so md.run subscribes the new set."""
    meta_a = make_meta(condition_id="cid-a")
    meta_b = make_meta(condition_id="cid-b")
    engine = _empty_engine(meta_a)
    ws = _FakeMarketWs()
    engine.md._ws = ws
    attach_market(cast(Any, engine), meta_b, StrategyProfile(), StrategyCell(), BookReadiness())
    assert ws.close_calls == 1
    assert engine.md._ws is None
    assert meta_a.condition_id in engine.metas
    assert meta_b.condition_id in engine.metas


def test_first_attach_does_not_close_missing_market_ws() -> None:
    """First attach while md.run is waiting for _subs must not require a socket."""
    meta = make_meta()
    engine = _empty_engine()
    assert engine.md._ws is None
    attach_market(cast(Any, engine), meta, StrategyProfile(), StrategyCell(), BookReadiness())
    assert engine.md._ws is None
    assert meta.yes.token_id in engine.md._subs


def test_reconfigure_clears_other_live_market_cell() -> None:
    """Closing the shared market socket must clear the other market's cell and bump generation."""
    meta_a = make_meta(condition_id="cid-a")
    meta_b = make_meta(condition_id="cid-b")
    engine = _empty_engine()
    cell_a = StrategyCell()
    readiness = BookReadiness()
    attach_market(cast(Any, engine), meta_a, StrategyProfile(), cell_a, readiness)
    cell_a.publish(0.55)
    readiness.note_snapshot(meta_a.yes.token_id)
    readiness.note_snapshot(meta_a.no.token_id)
    assert readiness.ready(meta_a.yes.token_id, 0.0) is True
    ws = _FakeMarketWs()
    engine.md._ws = ws
    attach_market(cast(Any, engine), meta_b, StrategyProfile(), StrategyCell(), readiness)
    assert ws.close_calls == 1
    assert cell_a.forced is True
    assert readiness.ready(meta_a.yes.token_id, 0.0) is False
    assert readiness.ready(meta_b.yes.token_id, 0.0) is False


def test_detach_gates_remaining_live_market() -> None:
    """Detach closes the socket and bumps the remaining market so it cannot quote on a dead book."""
    meta_a = make_meta(condition_id="cid-a")
    meta_b = make_meta(condition_id="cid-b")
    engine = _empty_engine()
    cell_a = StrategyCell()
    readiness = BookReadiness()
    attach_market(cast(Any, engine), meta_a, StrategyProfile(), cell_a, readiness)
    attach_market(cast(Any, engine), meta_b, StrategyProfile(), StrategyCell(), readiness)
    cell_a.publish(0.55)
    readiness.note_snapshot(meta_a.yes.token_id)
    readiness.note_snapshot(meta_a.no.token_id)
    ws = _FakeMarketWs()
    engine.md._ws = ws
    detach_market(cast(Any, engine), meta_b.condition_id, readiness)
    assert ws.close_calls == 1
    assert cell_a.forced is True
    assert readiness.ready(meta_a.yes.token_id, 0.0) is False


def test_mds_dirty_notes_book_readiness() -> None:
    """Live and paper MDS mutations must mark the current attach generation ready."""
    engine = _empty_engine()
    readiness = BookReadiness()
    install_book_readiness(cast(Any, engine), readiness)
    readiness.attach_token("tok")
    assert readiness.ready("tok", 0.0) is False
    engine.md._on_dirty("cid", "tok")
    assert readiness.ready("tok", 0.0) is True


def test_empty_book_last_update_ts_is_not_ready() -> None:
    """setdefault books from set_markets have last_update_ts 0 and must not open the gate."""
    readiness = BookReadiness()
    readiness.attach_token("tok")
    assert readiness.ready("tok", 0.0) is False
    assert readiness.ready("tok", 4_000_000_000.0) is True


def test_quote_task_name_uses_full_cid() -> None:
    """Two CIDs that share an 8-char prefix must not share a quote task spec."""
    meta_a = make_meta(condition_id="cid-aaaaaa")
    meta_b = make_meta(condition_id="cid-aaaaab")
    engine = _empty_engine()
    attach_market(cast(Any, engine), meta_a, StrategyProfile(), StrategyCell(), BookReadiness())
    attach_market(cast(Any, engine), meta_b, StrategyProfile(), StrategyCell(), BookReadiness())
    assert "quote:cid-aaaaaa" in engine._task_specs
    assert "quote:cid-aaaaab" in engine._task_specs
    assert "quote:cid-aaaa" not in engine._task_specs


def test_attach_refuses_live_cid() -> None:
    """A second attach of the same cid must not replace the live lock."""
    meta = make_meta()
    engine = _empty_engine(meta)
    lock_before = engine._locks[meta.condition_id]
    with pytest.raises(RuntimeError, match="already attached"):
        attach_market(cast(Any, engine), meta, StrategyProfile(), StrategyCell(), BookReadiness())
    assert engine._locks[meta.condition_id] is lock_before


def test_detach_drops_fork_leftovers() -> None:
    """Detach must drop sweep/FV/merge/halt, risk marks, and the quote task spec."""
    meta = make_meta()
    engine = _empty_engine(meta)
    cid = meta.condition_id
    engine._sweep[cid] = True
    engine._last_quote_fv[cid] = 0.55
    engine._merging.add(cid)
    engine._halted.add(cid)
    engine.risk._marks[meta.yes.token_id] = 0.4
    engine.risk._marks[meta.no.token_id] = 0.6
    engine._task_specs[f"quote:{cid}"] = lambda: None
    engine._tasks[f"quote:{cid}"] = object()
    detach_market(cast(Any, engine), cid, BookReadiness())
    assert cid not in engine._sweep
    assert cid not in engine._last_quote_fv
    assert cid not in engine._merging
    assert cid not in engine._halted
    assert meta.yes.token_id not in engine.risk._marks
    assert meta.no.token_id not in engine.risk._marks
    assert f"quote:{cid}" not in engine._task_specs
    assert f"quote:{cid}" not in engine._tasks
    assert cid not in engine.metas


def test_evaluate_store_error_returns_halt_not_raise(tmp_path: Path) -> None:
    """A sqlite failure inside evaluate must HALT instead of leaving resting orders."""
    store = WalletStateStore(tmp_path / "wallet.db")
    risk = RiskManager(RiskConfig(), store)
    engine = SimpleNamespace(risk=risk, md=SimpleNamespace(books={}), state=store)
    wrap_risk_from_ledger(cast(Any, engine), store)

    def boom() -> Never:
        raise sqlite3.OperationalError("disk I/O error")

    store.ensure_utc_day = boom
    decision = risk.evaluate(make_meta(), ws_stale=False, event_group_cost=0.0)
    assert decision.halt is True
    assert decision.reason == "store_error"
    store.close()


def test_evaluate_does_not_sum_the_ledger(tmp_path: Path) -> None:
    """evaluate copies running cash; it must not scan fill_ledger on every tick."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("t1", "o1")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.MATCHED, 1.0),
        "cid",
    )
    risk = RiskManager(RiskConfig(), store)
    engine = SimpleNamespace(risk=risk, md=SimpleNamespace(books={}), state=store)
    wrap_risk_from_ledger(cast(Any, engine), store)
    calls = {"n": 0}
    original = store.ledger_net_cash

    def counted() -> float:
        calls["n"] += 1
        return original()

    store.ledger_net_cash = counted
    decision = risk.evaluate(make_meta(), ws_stale=False, event_group_cost=0.0)
    assert calls["n"] == 0
    assert decision.halt is False
    assert risk.net_cash == -4.0
    store.close()


def test_open_utc_day_reads_by_utc_key_and_ignores_conflict(tmp_path: Path) -> None:
    """A non-latest seq row for today must not collide; lookup is by utc_day."""
    store = WalletStateStore(tmp_path / "wallet.db")
    today = "2026-08-19"
    yesterday = "2026-08-18"
    store._conn.execute(
        "INSERT INTO wallet_day(utc_day, day_start_equity, seq) VALUES(?,?,?)",
        (today, 1.0, 1),
    )
    store._conn.execute(
        "INSERT INTO wallet_day(utc_day, day_start_equity, seq) VALUES(?,?,?)",
        (yesterday, 2.0, 2),
    )
    store._conn.commit()
    row = store.open_utc_day(today, 99.0)
    assert row.utc_day == today
    assert row.day_start_equity == 1.0
    assert row.seq == 1
    store.close()


def test_alert_transitions_forward_once_and_log_clear(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Identical (key, message) alerts are dropped; halt=False logs the key once more."""
    forwarded: list[tuple[str, str, bool]] = []

    def original_alert(key: str, message: str, *, critical: bool = False) -> None:
        forwarded.append((key, message, critical))

    meta = make_meta()
    halt_reason = f"error_rate 0.45 {meta.condition_id[:8]}"
    halt_key = f"risk_halt:{halt_reason}"
    halt = RiskDecision(True, False, 0.0, halt_reason)
    clear = RiskDecision(False, False, 1.0, "")
    decisions = iter([halt, halt, clear])

    def original_evaluate(
        inner_meta: MarketMeta, *, ws_stale: bool, event_group_cost: float
    ) -> RiskDecision:
        del inner_meta, ws_stale, event_group_cost
        return next(decisions)

    engine = SimpleNamespace(
        alerter=SimpleNamespace(alert=original_alert),
        risk=SimpleNamespace(evaluate=original_evaluate),
    )
    wrap_alert_transitions(cast(Any, engine))
    halt_message = f"risk halt: {halt_reason}"
    engine.alerter.alert(halt_key, halt_message, critical=True)
    engine.alerter.alert(halt_key, halt_message, critical=True)
    assert forwarded == [(halt_key, halt_message, True)]
    first = engine.risk.evaluate(meta, ws_stale=False, event_group_cost=0.0)
    second = engine.risk.evaluate(meta, ws_stale=False, event_group_cost=0.0)
    assert first.halt is True
    assert second.halt is True
    with caplog.at_level(logging.WARNING, logger="trader.engine_seams"):
        third = engine.risk.evaluate(meta, ws_stale=False, event_group_cost=0.0)
    assert third.halt is False
    cleared = [record.getMessage() for record in caplog.records]
    assert any(f"alert cleared key={halt_key}" in line for line in cleared)
    engine.alerter.alert(halt_key, halt_message, critical=True)
    assert len(forwarded) == 2


class _DivergenceStore:
    """In-memory store for wrap_position_divergence tests."""

    def __init__(self, size: float, last_fill_ts: float) -> None:
        self.positions = {YES_TOKEN: Position(YES_TOKEN, size, 0.69)}
        self._inflight: dict[str, int] = {}
        self._last_fill_ts = {YES_TOKEN: last_fill_ts}
        self._orders: list[OpenOrder] = []
        self.forced: list[tuple[str, float]] = []
        self.writedowns: list[tuple[str, float]] = []
        self.buy_blocked: set[str] = set()
        self.floors: dict[str, float] = {}
        self.ledger: dict[str, Position] = {}
        self.restored: list[str] = []

    def position(self, token_id: str) -> Position:
        return self.positions.get(token_id, Position(token_id))

    def inflight(self, token_id: str) -> int:
        return self._inflight.get(token_id, 0)

    def merge_is_held(self, token_id: str) -> bool:
        del token_id
        return False

    def is_settling(self, token_id: str, now: float) -> bool:
        """True while this token is inside EXIT_SETTLE_SECONDS after the last fill stamp."""
        return now - self._last_fill_ts.get(token_id, 0.0) < EXIT_SETTLE_SECONDS

    def orders_for(self, token_id: str) -> list[OpenOrder]:
        """Resting orders on this token, including a live SELL under test."""
        return [order for order in self._orders if order.token_id == token_id]

    def mark_inflight(self, token_id: str) -> None:
        self._inflight[token_id] = self._inflight.get(token_id, 0) + 1

    def force_set_position(
        self, token_id: str, size: float, avg_price: float, *, source: str
    ) -> None:
        del avg_price, source
        self.forced.append((token_id, size))
        self.positions[token_id] = Position(token_id, size, 0.0)

    def clear_sell_freeze(self, token_id: str) -> None:
        del token_id

    def is_sell_frozen(self, token_id: str) -> bool:
        del token_id
        return False

    def note_writedown_snapshot(self, token_id: str, snapshot_ts: float) -> None:
        """Record the snapshot time a write-down dates from."""
        self.writedowns.append((token_id, snapshot_ts))

    def block_buy(self, token_id: str) -> None:
        """Stop new entry BUYs on this token."""
        self.buy_blocked.add(token_id)

    def unblock_buy(self, token_id: str) -> None:
        """Allow entry BUYs on this token again."""
        self.buy_blocked.discard(token_id)

    def is_buy_blocked(self, token_id: str) -> bool:
        """True while an on-chain excess blocks new clips on this token."""
        return token_id in self.buy_blocked

    def chain_read_floor(self, token_id: str) -> float:
        """Test floor. Zero accepts any fresh block."""
        return self.floors.get(token_id, 0.0)

    def note_chain_floor(self, token_id: str, ts: float) -> None:
        """Record the block time a write-down trusted."""
        if ts > self.floors.get(token_id, 0.0):
            self.floors[token_id] = ts

    def ledger_position(self, token_id: str) -> Position:
        """Ledger size the test planted. Default is an empty position."""
        return self.ledger.get(token_id, Position(token_id))

    def restore_ledger_position(self, token_id: str, block_ts: float) -> None:
        """Put the planted ledger size back and ignore any older chain block."""
        self.note_chain_floor(token_id, block_ts)
        self.positions[token_id] = self.ledger_position(token_id)
        self.restored.append(token_id)
        self.buy_blocked.discard(token_id)
        self.writedowns = [item for item in self.writedowns if item[0] != token_id]

    def write_down_chain_position(
        self,
        token_id: str,
        size: float,
        avg_price: float,
        block_ts: float,
        writedown_ts: float,
    ) -> None:
        """Accept a lower chain size and ignore any older chain block."""
        del avg_price
        self.note_chain_floor(token_id, block_ts)
        self.note_writedown_snapshot(token_id, writedown_ts)
        self.force_set_position(token_id, size, 0.0, source="onchain")
        self.clear_sell_freeze(token_id)


def _divergence_engine(
    store: _DivergenceStore,
    on_balances: Callable[[_DivergenceStore, list[str]], dict[str, float]],
    *,
    block_ts: float | None = None,
) -> SimpleNamespace:
    wakes: list[str] = []
    alerts: list[tuple[str, str, bool]] = []

    def alert(key: str, message: str, *, critical: bool = False) -> None:
        alerts.append((key, message, critical))

    async def read_snapshot(tokens: list[str]) -> ChainSnapshot:
        balances = on_balances(store, tokens)
        ts = time.time() if block_ts is None else block_ts
        return ChainSnapshot(block_number=10, block_ts=ts, balances=balances)

    engine = SimpleNamespace(
        _token_cid={YES_TOKEN: "cid-1"},
        state=store,
        alerter=SimpleNamespace(alert=alert),
        _wake_cid=wakes.append,
        wakes=wakes,
        alerts=alerts,
    )
    wrap_position_divergence(cast(Any, engine), read_snapshot)
    return engine


def test_divergence_skips_when_fill_lands_during_rpc() -> None:
    """MATCHED during token_balances must not force-flat or wake the quoter."""
    store = _DivergenceStore(28.99, 0.0)

    def during_rpc(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del tokens
        inner.mark_inflight(YES_TOKEN)
        return {YES_TOKEN: 0.0}

    engine = _divergence_engine(store, during_rpc)
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == 28.99
    assert store.forced == []
    assert store.writedowns == []
    assert engine.wakes == []
    assert engine.alerts == [
        ("divergence:TOKEN0", "position drift: internal 29.0 vs on-chain 0.0", True)
    ]


def test_divergence_skips_within_settle_window() -> None:
    """A REST/MATCHED stamp inside 10s skips force even when inflight is already 0."""
    store = _DivergenceStore(28.99, time.time())

    def chain_zero(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: 0.0}

    engine = _divergence_engine(store, chain_zero)
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == 28.99
    assert store.forced == []
    assert engine.wakes == []


def test_divergence_forces_stale_ghost_after_settle() -> None:
    """After the 10s window, on-chain 0 still flattens a leftover internal size."""
    store = _DivergenceStore(29.0, time.time() - 11.0)

    def chain_zero(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: 0.0}

    engine = _divergence_engine(store, chain_zero)
    asyncio.run(engine._check_position_divergence())
    assert store.forced == [(YES_TOKEN, 0.0)]
    assert store.position(YES_TOKEN).size == 0.0
    assert len(store.writedowns) == 1
    assert store.writedowns[0][0] == YES_TOKEN
    assert store.writedowns[0][1] <= time.time()
    assert engine.wakes == ["cid-1"]
    assert engine.alerts == [
        ("divergence:TOKEN0", "position drift: internal 29.0 vs on-chain 0.0", True)
    ]


def test_divergence_ignores_rest_size_up() -> None:
    """Lagged REST extra shares must not force sqlite up or wake the quoter."""
    store = _DivergenceStore(0.0, time.time() - 11.0)

    def rest_up(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: 73.53}

    engine = _divergence_engine(store, rest_up)
    asyncio.run(engine._check_position_divergence())
    assert store.forced == []
    assert store.restored == []
    assert store.position(YES_TOKEN).size == 0.0
    assert engine.wakes == []
    assert engine.alerts == [
        ("divergence_up:TOKEN0", "REST size-up ignored: internal 0.0 vs on-chain 73.5", False)
    ]


def test_divergence_skips_size_down_while_live_sell() -> None:
    """REST already seeing a live SELL fill must not shrink sqlite before MATCHED."""
    store = _DivergenceStore(13.0, time.time() - 11.0)
    store._orders = [OpenOrder("oid-s", YES_TOKEN, Side.SELL, 0.58, 13.0)]

    def rest_partial(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: 8.09}

    engine = _divergence_engine(store, rest_partial)
    asyncio.run(engine._check_position_divergence())
    assert store.forced == []
    assert store.writedowns == []
    assert store.position(YES_TOKEN).size == 13.0
    assert engine.wakes == []
    assert engine.alerts == [
        ("divergence:TOKEN0", "position drift: internal 13.0 vs on-chain 8.1", True)
    ]


def test_divergence_blocks_buys_after_persistent_onchain_excess() -> None:
    """On-chain excess that holds for the cap is inventory sqlite lost, not REST lag."""
    store = _DivergenceStore(0.0, time.time() - 11.0)

    def rest_up(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: 8.09}

    engine = _divergence_engine(store, rest_up)
    for _round in range(ONCHAIN_EXCESS_BLOCK_ROUNDS - 1):
        asyncio.run(engine._check_position_divergence())
        assert store.buy_blocked == set()
    asyncio.run(engine._check_position_divergence())
    assert store.buy_blocked == {YES_TOKEN}
    assert store.forced == []
    assert engine.alerts[-1] == (
        "divergence_up:TOKEN0",
        "on-chain excess held 3 rounds: internal 0.0 vs on-chain 8.1; buys blocked",
        True,
    )
    alerts_at_block = len(engine.alerts)
    asyncio.run(engine._check_position_divergence())
    assert len(engine.alerts) == alerts_at_block


def test_divergence_unblocks_buys_when_sizes_agree() -> None:
    """A matching round clears the block and restarts the excess count."""
    store = _DivergenceStore(0.0, time.time() - 11.0)
    chain_size = 8.09

    def rest_read(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: chain_size}

    engine = _divergence_engine(store, rest_read)
    for _round in range(ONCHAIN_EXCESS_BLOCK_ROUNDS):
        asyncio.run(engine._check_position_divergence())
    assert store.buy_blocked == {YES_TOKEN}
    store.positions[YES_TOKEN] = Position(YES_TOKEN, 8.09, 0.69)
    asyncio.run(engine._check_position_divergence())
    assert store.buy_blocked == set()
    store.positions[YES_TOKEN] = Position(YES_TOKEN, 0.0, 0.0)
    asyncio.run(engine._check_position_divergence())
    assert store.buy_blocked == set()


def test_divergence_ignores_a_block_older_than_the_last_fill(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Today's zero: the block is from before the bot learned the fill was CONFIRMED."""
    now = time.time()
    store = _DivergenceStore(754.06, now - 11.0)
    store.floors[YES_TOKEN] = now

    def chain_zero(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: 0.0}

    engine = _divergence_engine(store, chain_zero, block_ts=now - 5.0)
    with caplog.at_level(logging.WARNING, logger="trader.engine_seams"):
        asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == 754.06
    assert store.forced == []
    assert any(
        "position_divergence stale block" in record.getMessage() for record in caplog.records
    )


def test_divergence_ignores_a_block_older_than_thirty_seconds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A head more than 30s behind is not inventory, even when the fill floor is old."""
    store = _DivergenceStore(29.0, time.time() - 11.0)

    def chain_zero(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: 0.0}

    engine = _divergence_engine(store, chain_zero, block_ts=time.time() - 31.0)
    with caplog.at_level(logging.WARNING, logger="trader.engine_seams"):
        asyncio.run(engine._check_position_divergence())
    assert store.forced == []
    assert any(
        "position_divergence stale block" in record.getMessage() for record in caplog.records
    )


def test_divergence_restores_when_fresh_chain_matches_the_ledger() -> None:
    """One fresh block that matches the ledger puts a false write-down back."""
    store = _DivergenceStore(0.0, time.time() - 11.0)
    store.ledger[YES_TOKEN] = Position(YES_TOKEN, 754.06, 0.55)
    store.block_buy(YES_TOKEN)

    def chain_full(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: 754.06}

    engine = _divergence_engine(store, chain_full)
    asyncio.run(engine._check_position_divergence())
    assert store.restored == [YES_TOKEN]
    assert store.position(YES_TOKEN).size == 754.06
    assert store.buy_blocked == set()
    assert engine.wakes == ["cid-1"]
    assert store.forced == []


def test_older_block_cannot_flatten_a_restored_position() -> None:
    """Block 2 flats, block 4 restores, block 3 must not flat the position again."""
    now = time.time()
    store = _DivergenceStore(10.0, now - 11.0)
    store.ledger[YES_TOKEN] = Position(YES_TOKEN, 10.0, 0.5)
    snapshots = [
        ChainSnapshot(2, now - 4.0, {YES_TOKEN: 0.0}),
        ChainSnapshot(4, now - 1.0, {YES_TOKEN: 10.0}),
        ChainSnapshot(3, now - 2.0, {YES_TOKEN: 0.0}),
    ]

    async def read_snapshot(tokens: list[str]) -> ChainSnapshot:
        del tokens
        return snapshots.pop(0)

    engine = SimpleNamespace(
        _token_cid={YES_TOKEN: "cid-1"},
        state=store,
        alerter=SimpleNamespace(alert=lambda *_args, **_kwargs: None),
        _wake_cid=lambda _cid: None,
    )
    wrap_position_divergence(cast(Any, engine), read_snapshot)
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == 0.0
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == 10.0
    assert store.chain_read_floor(YES_TOKEN) == pytest.approx(now - 1.0)
    quote = Quote(YES_TOKEN, Side.SELL, 0.5, 10.0)
    assert sell_is_droppable(cast(Any, store), quote) is False
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == 10.0
    assert sell_is_droppable(cast(Any, store), quote) is False


def test_divergence_does_not_restore_while_a_fill_is_inflight() -> None:
    """A MATCHED that lands during the read is newer than the block."""
    store = _DivergenceStore(0.0, time.time() - 11.0)
    store.ledger[YES_TOKEN] = Position(YES_TOKEN, 10.0, 0.4)

    def during_rpc(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del tokens
        inner.mark_inflight(YES_TOKEN)
        return {YES_TOKEN: 10.0}

    engine = _divergence_engine(store, during_rpc)
    asyncio.run(engine._check_position_divergence())
    assert store.restored == []
    assert store.position(YES_TOKEN).size == 0.0


def test_divergence_watermark_is_min_of_send_and_block() -> None:
    """A fill after the block, but before the RPC returned, was not in that balance."""
    store = _DivergenceStore(29.0, time.time() - 11.0)
    block_ts = time.time() - 2.0

    def chain_zero(inner: _DivergenceStore, tokens: list[str]) -> dict[str, float]:
        del inner, tokens
        return {YES_TOKEN: 0.0}

    engine = _divergence_engine(store, chain_zero, block_ts=block_ts)
    asyncio.run(engine._check_position_divergence())
    assert store.writedowns == [(YES_TOKEN, block_ts)]
    assert store.floors[YES_TOKEN] == block_ts


def test_chain_write_down_supersedes_an_older_fill_and_keeps_a_later_one(tmp_path: Path) -> None:
    """The block's timestamp, not the REST reply, is what an older fill is measured against."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 13.0, 0.60)
    block_ts = store.chain_read_floor(YES_TOKEN)

    async def read_snapshot(tokens: list[str]) -> ChainSnapshot:
        del tokens
        return ChainSnapshot(block_number=5, block_ts=block_ts, balances={YES_TOKEN: 8.09})

    wakes: list[str] = []
    engine = SimpleNamespace(
        _token_cid={YES_TOKEN: "cid-1"},
        state=store,
        alerter=SimpleNamespace(alert=lambda *_args, **_kwargs: None),
        _wake_cid=wakes.append,
    )
    wrap_position_divergence(cast(Any, engine), read_snapshot)
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == pytest.approx(8.09)
    assert store._writedown_snapshot_ts[YES_TOKEN] == pytest.approx(block_ts)
    late = Fill(YES_TOKEN, Side.SELL, 0.58, 4.91, "trade-late", block_ts - 5.0, is_maker=True)
    assert store.apply_matched_fill(late, "trade-late") is False
    assert store.position(YES_TOKEN).size == pytest.approx(8.09)
    later = Fill(YES_TOKEN, Side.SELL, 0.58, 3.09, "trade-new", block_ts + 5.0, is_maker=True)
    assert store.apply_matched_fill(later, "trade-new") is True
    assert store.position(YES_TOKEN).size == pytest.approx(5.0)
    store.close()


def test_stale_zero_keeps_bought_shares_and_a_later_block_restores(
    tmp_path: Path,
    chain_telegrams: list[str],
) -> None:
    """Seven CONFIRMED buys survive a stale zero. A later fresh block restores a false flat."""
    db_path = tmp_path / "wallet.db"
    store = WalletStateStore(db_path)
    fill_ts = time.time() - 11.0
    for index, size in enumerate((100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 154.06)):
        fill = Fill(YES_TOKEN, Side.BUY, 0.55, size, f"buy-{index}", fill_ts, is_maker=True)
        assert store.apply_matched_fill(fill, f"buy-{index}") is True
        assert store.apply_confirmed_fill(fill, f"buy-{index}").confirmed is True
    assert store.position(YES_TOKEN).size == pytest.approx(754.06)
    quote = Quote(YES_TOKEN, Side.SELL, 0.55, 754.06)
    assert sell_is_droppable(store, quote) is False
    floor = store.chain_read_floor(YES_TOKEN)
    snapshots = [
        ChainSnapshot(1, floor - 5.0, {YES_TOKEN: 0.0}),
        ChainSnapshot(2, floor + 1.0, {YES_TOKEN: 0.0}),
        ChainSnapshot(1, floor - 1.0, {YES_TOKEN: 754.06}),
        ChainSnapshot(3, floor + 2.0, {YES_TOKEN: 754.06}),
    ]

    async def read_snapshot(tokens: list[str]) -> ChainSnapshot:
        del tokens
        return snapshots.pop(0)

    engine = SimpleNamespace(
        _token_cid={YES_TOKEN: "cid-pari"},
        state=store,
        alerter=SimpleNamespace(alert=lambda *_args, **_kwargs: None),
        _wake_cid=lambda _cid: None,
    )
    wrap_position_divergence(cast(Any, engine), read_snapshot)
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == pytest.approx(754.06)
    assert sell_is_droppable(store, quote) is False
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == 0.0
    assert sell_is_droppable(store, quote) is True
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == 0.0
    asyncio.run(engine._check_position_divergence())
    assert store.position(YES_TOKEN).size == pytest.approx(754.06)
    assert sell_is_droppable(store, quote) is False
    assert any("restored from ledger" in message for message in chain_telegrams)
    store.close()
    restarted = WalletStateStore(db_path)
    assert restarted.position(YES_TOKEN).size == pytest.approx(754.06)
    assert restarted.chain_read_floor(YES_TOKEN) == restarted._boot_wall
    restarted.close()


class _InventoryGateway:
    """Records place batches and parses the canned body on a real gateway I/O thread."""

    def __init__(self, resp: object) -> None:
        self.resp = resp
        self.sent: list[list[Quote]] = []
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-clob")
        self._closed = False
        atexit.register(self.close)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _parse_place_response(self, resp: object, quotes: list[Quote]) -> list[OpenOrder]:
        del resp, quotes
        return []

    async def place(self, quotes: list[Quote], meta: MarketMeta) -> list[OpenOrder]:
        del meta
        self.sent.append(list(quotes))

        def _place() -> list[OpenOrder]:
            return self._parse_place_response(self.resp, quotes)

        return await ExecutionGateway._io(cast(Any, self), _place)


def _pass_evaluate(meta: MarketMeta, *, ws_stale: bool, event_group_cost: float) -> RiskDecision:
    del meta, ws_stale, event_group_cost
    return RiskDecision(False, False, 1.0, "")


async def _note_places(engine: Any, meta: MarketMeta, ok_count: int, fail_count: int) -> None:
    """Drive place+note_order_result so the cid ContextVar is set for each result."""
    quote = Quote(YES_TOKEN, Side.BUY, 0.40, 10.0)
    for _ in range(ok_count):
        await engine.gateway.place([quote], meta)
        engine.risk.note_order_result(True)
    for _ in range(fail_count):
        await engine.gateway.place([quote], meta)
        engine.risk.note_order_result(False)


def test_balance_reject_freezes_sell_and_skips_error_rate(tmp_path: Path) -> None:
    """A not-enough-balance SELL freezes the token and does not increment error_rate."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 66.47, 0.36)
    notes: list[bool] = []
    resp = [
        {
            "errorMsg": (
                "not enough balance / allowance: the balance is not enough "
                "-> balance: 0, order amount: 73530000"
            )
        }
    ]
    gateway = _InventoryGateway(resp)
    engine = SimpleNamespace(
        state=store,
        gateway=gateway,
        risk=SimpleNamespace(note_order_result=notes.append, evaluate=_pass_evaluate),
        cfg=SimpleNamespace(risk=RiskConfig()),
    )
    wrap_inventory_place_guard(cast(Any, engine))
    quote = Quote(YES_TOKEN, Side.SELL, 0.40, 66.47)

    async def place_then_note() -> list[OpenOrder]:
        placed = await engine.gateway.place([quote], make_meta())
        engine.risk.note_order_result(False)
        return placed

    placed = asyncio.run(place_then_note())
    assert placed == []
    assert notes == []
    assert store.is_sell_frozen(YES_TOKEN) is True
    tracker = engine.risk._cid_order_errors
    assert isinstance(tracker, CidOrderErrors)
    assert tracker._attempts == {}
    assert tracker.trip_reason(make_meta().condition_id) is None
    gateway.close()
    store.close()


def test_oversized_sell_is_dropped_before_place(tmp_path: Path) -> None:
    """A SELL larger than sqlite never hits the gateway and is not an order error."""
    store = WalletStateStore(tmp_path / "wallet.db")
    notes: list[bool] = []
    gateway = _InventoryGateway([])
    engine = SimpleNamespace(
        state=store,
        gateway=gateway,
        risk=SimpleNamespace(note_order_result=notes.append, evaluate=_pass_evaluate),
        cfg=SimpleNamespace(risk=RiskConfig()),
    )
    wrap_inventory_place_guard(cast(Any, engine))
    quote = Quote(YES_TOKEN, Side.SELL, 0.40, 73.53)

    async def place_then_note() -> list[OpenOrder]:
        placed = await engine.gateway.place([quote], make_meta())
        engine.risk.note_order_result(False)
        return placed

    placed = asyncio.run(place_then_note())
    assert placed == []
    assert gateway.sent == []
    assert notes == []
    gateway.close()
    store.close()


def test_sell_post_only_cross_skips_error_rate_and_holds(tmp_path: Path) -> None:
    """A crosses-book SELL is not an order error and freezes that token for 3s."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 31.99, 0.62)
    gateway = _InventoryGateway([{"errorMsg": "order crosses book"}])
    engine = SimpleNamespace(
        state=store,
        gateway=gateway,
        risk=SimpleNamespace(note_order_result=lambda _ok: None, evaluate=_pass_evaluate),
        cfg=SimpleNamespace(risk=RiskConfig()),
    )
    wrap_inventory_place_guard(cast(Any, engine))
    quote = Quote(YES_TOKEN, Side.SELL, 0.60, 31.99)

    async def place_then_note() -> None:
        await engine.gateway.place([quote], make_meta())
        engine.risk.note_order_result(False)

    asyncio.run(place_then_note())
    tracker = engine.risk._cid_order_errors
    assert isinstance(tracker, CidOrderErrors)
    assert tracker._attempts == {}
    assert store.is_sell_frozen(YES_TOKEN) is True
    remaining = store._sell_frozen_until[YES_TOKEN] - time.monotonic()
    assert remaining == pytest.approx(3.0, abs=0.5)
    gateway.close()
    store.close()


def test_buy_post_only_cross_counts_error(tmp_path: Path) -> None:
    """A crosses-book BUY is an ordinary place error and does not freeze SELL."""
    store = WalletStateStore(tmp_path / "wallet.db")
    gateway = _InventoryGateway([{"errorMsg": "order crosses book"}])
    engine = SimpleNamespace(
        state=store,
        gateway=gateway,
        risk=SimpleNamespace(note_order_result=lambda _ok: None, evaluate=_pass_evaluate),
        cfg=SimpleNamespace(risk=RiskConfig()),
    )
    wrap_inventory_place_guard(cast(Any, engine))
    quote = Quote(YES_TOKEN, Side.BUY, 0.40, 10.0)
    meta = make_meta()

    async def place_then_note() -> None:
        await engine.gateway.place([quote], meta)
        engine.risk.note_order_result(False)

    asyncio.run(place_then_note())
    tracker = engine.risk._cid_order_errors
    assert isinstance(tracker, CidOrderErrors)
    assert tracker._attempts[meta.condition_id] == 1
    assert tracker._errors[meta.condition_id] == 1
    assert store.is_sell_frozen(YES_TOKEN) is False
    gateway.close()
    store.close()


def test_mixed_place_response_counts_error(tmp_path: Path) -> None:
    """A batch counts unless every unplaced quote is an explained SELL reject."""
    sell = Quote(YES_TOKEN, Side.SELL, 0.60, 10.0)
    buy = Quote(YES_TOKEN, Side.BUY, 0.40, 10.0)
    meta = make_meta()

    def run(name: str, resp: object) -> CidOrderErrors:
        store = WalletStateStore(tmp_path / f"{name}.db")
        store.set_position(YES_TOKEN, 40.0, 0.50)
        gateway = _InventoryGateway(resp)
        engine = SimpleNamespace(
            state=store,
            gateway=gateway,
            risk=SimpleNamespace(note_order_result=lambda _ok: None, evaluate=_pass_evaluate),
            cfg=SimpleNamespace(risk=RiskConfig()),
        )
        wrap_inventory_place_guard(cast(Any, engine))

        async def place_then_note() -> None:
            await engine.gateway.place([sell, buy], meta)
            engine.risk.note_order_result(False)

        asyncio.run(place_then_note())
        tracker = engine.risk._cid_order_errors
        assert isinstance(tracker, CidOrderErrors)
        gateway.close()
        store.close()
        return tracker

    mixed = run(
        "mixed",
        [
            {"errorMsg": "order crosses book"},
            {"errorMsg": "invalid amount"},
        ],
    )
    assert mixed._errors[meta.condition_id] == 1
    short = run("short", [{"errorMsg": "order crosses book"}])
    assert short._errors[meta.condition_id] == 1


def test_error_rate_stops_buys_per_cid(tmp_path: Path) -> None:
    """Six fails in twenty stop buys on A only; a real ws_stale halt still wins."""
    store = WalletStateStore(tmp_path / "wallet.db")
    risk = RiskManager(RiskConfig(), store)
    gateway = _InventoryGateway([])
    engine = SimpleNamespace(
        state=store,
        gateway=gateway,
        risk=risk,
        cfg=SimpleNamespace(risk=RiskConfig()),
        alerter=SimpleNamespace(alert=lambda *_args, **_kwargs: None),
    )
    wrap_inventory_place_guard(cast(Any, engine))
    meta_a = make_meta(condition_id="0xaaaaaaaa")
    meta_b = make_meta(condition_id="0xbbbbbbbb")
    asyncio.run(_note_places(engine, meta_a, 14, 6))
    stopped_a = engine.risk.evaluate(meta_a, ws_stale=False, event_group_cost=0.0)
    healthy_b = engine.risk.evaluate(meta_b, ws_stale=False, event_group_cost=0.0)
    assert stopped_a.halt is False
    assert stopped_a.reduce_only is True
    assert stopped_a.size_scale == 1.0
    assert "error_rate" in stopped_a.reason
    assert meta_a.condition_id[:8] in stopped_a.reason
    assert healthy_b.halt is False
    assert healthy_b.reduce_only is False
    stale_a = engine.risk.evaluate(meta_a, ws_stale=True, event_group_cost=0.0)
    assert stale_a.halt is True
    assert stale_a.reason == "ws_stale"
    assert risk._order_attempts == 0
    assert risk.error_rate == 0.0
    gateway.close()
    store.close()


def test_tripped_breaker_survives_successful_sells(tmp_path: Path) -> None:
    """Later successes do not clear a breaker that already tripped."""
    store = WalletStateStore(tmp_path / "wallet.db")
    risk = RiskManager(RiskConfig(), store)
    gateway = _InventoryGateway([])
    engine = SimpleNamespace(
        state=store,
        gateway=gateway,
        risk=risk,
        cfg=SimpleNamespace(risk=RiskConfig()),
        alerter=SimpleNamespace(alert=lambda *_args, **_kwargs: None),
    )
    wrap_inventory_place_guard(cast(Any, engine))
    meta = make_meta(condition_id="0xaaaaaaaa")
    asyncio.run(_note_places(engine, meta, 14, 6))
    tripped = engine.risk.evaluate(meta, ws_stale=False, event_group_cost=0.0)
    asyncio.run(_note_places(engine, meta, 5, 0))
    tracker = cast(Any, risk)._cid_order_errors
    assert isinstance(tracker, CidOrderErrors)
    assert tracker.trip_reason(meta.condition_id) is not None
    again = engine.risk.evaluate(meta, ws_stale=False, event_group_cost=0.0)
    assert again.halt is False
    assert again.reduce_only is True
    assert again.reason == tripped.reason
    gateway.close()
    store.close()


def test_error_rate_swallows_fork_global_breaker(tmp_path: Path) -> None:
    """A leftover fork error_rate must not halt a cid that has no place errors."""
    store = WalletStateStore(tmp_path / "wallet.db")
    risk = RiskManager(RiskConfig(), store)
    risk._order_attempts = 20
    risk._order_errors = 9
    gateway = _InventoryGateway([])
    engine = SimpleNamespace(
        state=store,
        gateway=gateway,
        risk=risk,
        cfg=SimpleNamespace(risk=RiskConfig()),
    )
    wrap_inventory_place_guard(cast(Any, engine))
    meta = make_meta(condition_id="0xaaaaaaaa")
    decision = engine.risk.evaluate(meta, ws_stale=False, event_group_cost=0.0)
    assert decision.halt is False
    assert decision.reduce_only is False
    assert risk.error_rate == 0.45
    gateway.close()
    store.close()


def test_order_error_alerts_once_per_cid(tmp_path: Path) -> None:
    """Two maps get two order_errors keys; a repeat evaluate does not resend."""
    store = WalletStateStore(tmp_path / "wallet.db")
    risk = RiskManager(RiskConfig(), store)
    forwarded: list[tuple[str, str, bool]] = []

    def original_alert(key: str, message: str, *, critical: bool = False) -> None:
        forwarded.append((key, message, critical))

    gateway = _InventoryGateway([])
    engine = SimpleNamespace(
        state=store,
        gateway=gateway,
        risk=risk,
        cfg=SimpleNamespace(risk=RiskConfig()),
        alerter=SimpleNamespace(alert=original_alert),
    )
    wrap_alert_transitions(cast(Any, engine))
    wrap_inventory_place_guard(cast(Any, engine))
    meta_a = make_meta(condition_id="0xaaaaaaaa")
    meta_b = make_meta(condition_id="0xbbbbbbbb")
    asyncio.run(_note_places(engine, meta_a, 14, 6))
    asyncio.run(_note_places(engine, meta_b, 14, 6))
    decision_a = engine.risk.evaluate(meta_a, ws_stale=False, event_group_cost=0.0)
    decision_b = engine.risk.evaluate(meta_b, ws_stale=False, event_group_cost=0.0)
    engine.risk.evaluate(meta_a, ws_stale=False, event_group_cost=0.0)
    assert decision_a.halt is False
    assert decision_a.reduce_only is True
    assert decision_b.halt is False
    assert decision_b.reduce_only is True
    assert forwarded == [
        (
            f"order_errors:{meta_a.condition_id[:8]}",
            f"{meta_a.condition_id[:8]} {decision_a.reason}: buys stopped, exit keeps quoting",
            True,
        ),
        (
            f"order_errors:{meta_b.condition_id[:8]}",
            f"{meta_b.condition_id[:8]} {decision_b.reason}: buys stopped, exit keeps quoting",
            True,
        ),
    ]
    gateway.close()
    store.close()


def test_reset_lets_the_same_error_rate_alert_again(tmp_path: Path) -> None:
    """Reset forgets this cid's order_errors dedup; a sibling cid's alert stays put."""
    store = WalletStateStore(tmp_path / "wallet.db")
    risk = RiskManager(RiskConfig(), store)
    forwarded: list[str] = []

    def original_alert(key: str, message: str, *, critical: bool = False) -> None:
        del message, critical
        forwarded.append(key)

    gateway = _InventoryGateway([])
    engine = SimpleNamespace(
        state=store,
        gateway=gateway,
        risk=risk,
        cfg=SimpleNamespace(risk=RiskConfig()),
        alerter=SimpleNamespace(alert=original_alert),
    )
    wrap_alert_transitions(cast(Any, engine))
    wrap_inventory_place_guard(cast(Any, engine))
    meta_a = make_meta(condition_id="0xaaaaaaaa")
    meta_b = make_meta(condition_id="0xbbbbbbbb")
    key_a = f"order_errors:{meta_a.condition_id[:8]}"
    key_b = f"order_errors:{meta_b.condition_id[:8]}"
    asyncio.run(_note_places(engine, meta_a, 14, 6))
    asyncio.run(_note_places(engine, meta_b, 14, 6))
    first = engine.risk.evaluate(meta_a, ws_stale=False, event_group_cost=0.0)
    engine.risk.evaluate(meta_b, ws_stale=False, event_group_cost=0.0)
    reset_order_error_rate(risk, meta_a.condition_id)
    healthy = engine.risk.evaluate(meta_a, ws_stale=False, event_group_cost=0.0)
    engine.risk.evaluate(meta_b, ws_stale=False, event_group_cost=0.0)
    asyncio.run(_note_places(engine, meta_a, 14, 6))
    again = engine.risk.evaluate(meta_a, ws_stale=False, event_group_cost=0.0)
    assert healthy.halt is False
    assert healthy.reduce_only is False
    assert again.reduce_only is True
    assert again.reason == first.reason
    assert forwarded == [key_a, key_b, key_a]
    gateway.close()
    store.close()


def test_slim_journal_drops_public_book_kinds(tmp_path: Path) -> None:
    """book/price_change/last_trade_price are dropped; user_trade and orders stay."""
    journal = SlimJournal(tmp_path, enabled=True, day="live")
    journal.write("book", {"asset": "x"}, 1.0)
    journal.write("price_change", {"asset": "x"}, 2.0)
    journal.write("last_trade_price", {"asset": "x"}, 3.0)
    journal.write("user_trade", {"id": "t1"}, 4.0)
    journal.write("user_order", {"id": "o1"}, 5.0)
    journal.write("orders_out", [{"id": "q1"}], 6.0)
    journal.close()
    kinds = [
        json.loads(line)["kind"] for line in (tmp_path / "live.jsonl").read_text().splitlines()
    ]
    assert kinds == ["user_trade", "user_order", "orders_out"]


def test_patch_engine_classes_installs_and_restores_slim_journal() -> None:
    """open_wallet_host patches Journal before Engine(); teardown puts the fork back."""
    original = polymaker_engine.Journal
    restore = patch_engine_classes()
    try:
        assert polymaker_engine.Journal is SlimJournal
    finally:
        restore_engine_classes(restore)
    assert polymaker_engine.Journal is original
    assert polymaker_engine.Journal is Journal


def test_setup_logging_sets_httpx_and_engine_to_warning() -> None:
    """Docker INFO spam from httpx URLs and fork requote lines is muted."""
    setup_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("engine").level == logging.WARNING


class _FakeClob:
    def __init__(self) -> None:
        self.open_payload: object = []
        self.cancel_payload: object = {"canceled": []}
        self.cancel_queue: list[object] = []
        self.cancel_calls: list[list[str]] = []
        self.cancel_asset_calls: list[str] = []

    def get_open_orders(self) -> object:
        return self.open_payload

    def cancel_orders(self, order_ids: list[str]) -> object:
        self.cancel_calls.append(list(order_ids))
        if self.cancel_queue:
            return self.cancel_queue.pop(0)
        return self.cancel_payload


class _MemOrders:
    def __init__(self) -> None:
        self.orders: dict[str, OpenOrder] = {}

    def remove_order(self, order_id: str) -> None:
        self.orders.pop(order_id, None)


async def _sync_io(fn: Callable[[], object]) -> object:
    return fn()


def _live_order(order_id: str, price: float) -> OpenOrder:
    """Build a live YES BUY for the strict-REST harness."""
    return OpenOrder(order_id, YES_TOKEN, Side.BUY, price, 10.0)


def _install_strict(client: _FakeClob, state: _MemOrders) -> tuple[Any, list[str]]:
    wakes: list[str] = []

    async def cancel_asset(asset_id: str) -> bool:
        client.cancel_asset_calls.append(asset_id)
        return True

    gateway = SimpleNamespace(
        _paper=False,
        _client=client,
        _io=_sync_io,
        open_orders=None,
        positions=None,
        cancel=None,
        cancel_asset=cancel_asset,
    )

    def wake(cid: str) -> None:
        wakes.append(cid)

    engine = SimpleNamespace(
        paper=False,
        gateway=gateway,
        state=state,
        _token_cid={YES_TOKEN: "cid-yes"},
        _wake_cid=wake,
    )
    install_strict_rest(cast(Any, engine))
    return engine, wakes


def test_canceled_body_drops_local_id() -> None:
    """A canceled[] id is terminal and is removed from local state."""
    client = _FakeClob()
    client.open_payload = []
    client.cancel_payload = {"canceled": ["old"]}
    state = _MemOrders()
    state.orders["old"] = _live_order("old", 0.64)
    engine, wakes = _install_strict(client, state)

    async def run() -> None:
        assert await engine.gateway.cancel_asset(YES_TOKEN) is True
        assert "old" not in state.orders
        assert client.cancel_asset_calls == [YES_TOKEN]
        assert client.cancel_calls == [["old"]]
        assert wakes == ["cid-yes"]

    asyncio.run(run())


def test_not_found_cancel_is_terminal() -> None:
    """Documented not-found / already-canceled is terminal like canceled[]."""
    client = _FakeClob()
    client.open_payload = []
    client.cancel_payload = {
        "not_canceled": {"old": "Can't be found/already canceled"},
    }
    state = _MemOrders()
    state.orders["old"] = _live_order("old", 0.64)
    engine, wakes = _install_strict(client, state)

    async def run() -> None:
        assert await engine.gateway.cancel_asset(YES_TOKEN) is True
        assert "old" not in state.orders
        assert wakes == ["cid-yes"]

    asyncio.run(run())


def test_empty_cancel_body_keeps_id_and_skips_error_rate() -> None:
    """HTTP 200 with an empty cancel body is unproven; error_rate stays put."""
    client = _FakeClob()
    client.cancel_payload = {}
    state = _MemOrders()
    store = SimpleNamespace()
    risk = RiskManager(RiskConfig(), cast(Any, store))
    engine, _wakes = _install_strict(client, state)
    engine.risk = risk
    state.orders["old"] = _live_order("old", 0.64)

    async def run() -> None:
        assert await engine.gateway.cancel(["old"]) is False
        assert "old" in state.orders
        assert risk._order_attempts == 0
        assert risk._order_errors == 0

    asyncio.run(run())


def test_incomplete_cancel_body_keeps_id_and_skips_error_rate() -> None:
    """A cancel body that omits a requested id is unproven, not a strategy error."""
    client = _FakeClob()
    client.cancel_payload = {"canceled": ["other"]}
    state = _MemOrders()
    store = SimpleNamespace()
    risk = RiskManager(RiskConfig(), cast(Any, store))
    engine, _wakes = _install_strict(client, state)
    engine.risk = risk

    async def run() -> None:
        assert await engine.gateway.cancel(["old"]) is False
        assert risk._order_errors == 0
        assert await engine.gateway.cancel([]) is True
        assert client.cancel_calls == [["old"]]

    asyncio.run(run())


def test_empty_rest_with_no_local_orders_is_valid() -> None:
    """An empty open-orders snapshot is fine when we also hold nothing."""
    client = _FakeClob()
    client.open_payload = []
    state = _MemOrders()
    engine, _wakes = _install_strict(client, state)

    async def run() -> None:
        assert await engine.gateway.open_orders() == []
        assert client.cancel_calls == []

    asyncio.run(run())


def test_fence_for_one_market_does_not_cancel_another_market() -> None:
    """A token-scoped fence must not mutate an unrelated market's local order."""
    client = _FakeClob()
    client.open_payload = []
    client.cancel_payload = {"canceled": ["market-b-order"]}
    state = _MemOrders()
    market_b_token = "market-b-token"
    market_b_order = OpenOrder("market-b-order", market_b_token, Side.BUY, 0.64, 10.0)
    state.orders[market_b_order.order_id] = market_b_order
    engine, _wakes = _install_strict(client, state)
    engine._token_cid[market_b_token] = "cid-market-b"

    async def run() -> None:
        assert await fence_no_orders(engine, {YES_TOKEN}) is True
        assert client.cancel_calls == []
        assert state.orders == {"market-b-order": market_b_order}

    asyncio.run(run())


def test_missing_local_order_is_kept_without_cancel() -> None:
    """A read-only REST snapshot keeps an omitted local order without canceling it."""
    client = _FakeClob()
    client.open_payload = []
    state = _MemOrders()
    state.orders["local"] = _live_order("local", 0.50)
    engine, wakes = _install_strict(client, state)

    async def run() -> None:
        live = await engine.gateway.open_orders()
        assert [order.order_id for order in live] == ["local"]
        assert "local" in state.orders
        assert client.cancel_calls == []
        assert wakes == []

    asyncio.run(run())


def test_cancel_asset_proves_and_removes_missing_local_order() -> None:
    """Asset cancel resolves a REST-omitted local order through terminal per-id proof."""
    client = _FakeClob()
    client.open_payload = []
    client.cancel_payload = {"canceled": ["old"]}
    state = _MemOrders()
    state.orders["old"] = _live_order("old", 0.64)
    engine, wakes = _install_strict(client, state)

    async def run() -> None:
        assert await engine.gateway.cancel_asset(YES_TOKEN) is True
        assert "old" not in state.orders
        assert client.cancel_calls == [["old"]]
        assert wakes == ["cid-yes"]

    asyncio.run(run())


def test_asset_cancel_does_not_touch_another_market() -> None:
    """Per-id proof after an asset cancel includes only that asset's orders."""
    client = _FakeClob()
    client.cancel_payload = {"canceled": ["market-a-order"]}
    state = _MemOrders()
    market_a_order = _live_order("market-a-order", 0.40)
    market_b_order = OpenOrder("market-b-order", "market-b-token", Side.BUY, 0.60, 10.0)
    state.orders = {
        market_a_order.order_id: market_a_order,
        market_b_order.order_id: market_b_order,
    }
    engine, wakes = _install_strict(client, state)
    engine._token_cid[market_b_order.token_id] = "cid-market-b"

    async def run() -> None:
        assert await engine.gateway.cancel_asset(YES_TOKEN) is True
        assert set(state.orders) == {"market-b-order"}
        assert client.cancel_calls == [["market-a-order"]]
        assert wakes == ["cid-yes"]

    asyncio.run(run())


def test_incomplete_asset_cancel_keeps_every_local_id() -> None:
    """Partial terminal proof never drops any order from the requested asset batch."""
    client = _FakeClob()
    client.open_payload = [
        {
            "id": "seen",
            "asset_id": YES_TOKEN,
            "side": "BUY",
            "price": 0.40,
            "original_size": 10,
            "size_matched": 0,
        }
    ]
    client.cancel_payload = {"canceled": ["gone"]}
    state = _MemOrders()
    state.orders["seen"] = _live_order("seen", 0.40)
    state.orders["gone"] = _live_order("gone", 0.41)
    state.orders["ghost"] = _live_order("ghost", 0.42)
    engine, wakes = _install_strict(client, state)

    async def run() -> None:
        live = await engine.gateway.open_orders()
        ids = {order.order_id for order in live}
        assert ids == {"seen", "gone", "ghost"}
        assert await engine.gateway.cancel_asset(YES_TOKEN) is False
        assert set(state.orders) == {"seen", "gone", "ghost"}
        assert client.cancel_calls == [["seen", "gone", "ghost"]]
        assert wakes == []

    asyncio.run(run())


def test_order_placed_during_open_orders_rest_is_kept() -> None:
    """A place that lands while REST/cancel is in flight must still be in the snapshot."""
    client = _FakeClob()
    client.open_payload = []
    client.cancel_payload = {"canceled": ["old"]}
    state = _MemOrders()
    state.orders["old"] = _live_order("old", 0.64)

    def cancel_orders(order_ids: list[str]) -> object:
        client.cancel_calls.append(list(order_ids))
        state.orders["new"] = _live_order("new", 0.55)
        return client.cancel_payload

    client.cancel_orders = cancel_orders
    engine, _wakes = _install_strict(client, state)

    async def run() -> None:
        assert await engine.gateway.cancel_asset(YES_TOKEN) is True
        live = await engine.gateway.open_orders()
        ids = [order.order_id for order in live]
        assert ids == ["new"]
        assert "new" in state.orders
        assert "old" not in state.orders

    asyncio.run(run())


def test_malformed_open_orders_raises_without_touching_state() -> None:
    """A non-list open-orders body is Unproven and must not cancel or drop ids."""
    client = _FakeClob()
    client.open_payload = {"data": {"not": "a-list"}}
    state = _MemOrders()
    state.orders["old"] = _live_order("old", 0.64)
    engine, _wakes = _install_strict(client, state)

    async def run() -> None:
        with pytest.raises(RestUnproven):
            await engine.gateway.open_orders()
        assert "old" in state.orders
        assert client.cancel_calls == []

    asyncio.run(run())


def test_empty_rest_unproven_cancel_blocks_reprice_then_terminal_allows() -> None:
    """Incident: BUY 0.64 + empty REST + ambiguous cancel must not place 0.75."""
    client = _FakeClob()
    client.open_payload = []
    client.cancel_payload = {}
    state = _MemOrders()
    old = _live_order("buy-64", 0.64)
    old.size = 312.5
    state.orders["buy-64"] = old
    engine, wakes = _install_strict(client, state)
    target = TargetQuotes(
        condition_id="cid-yes",
        regime=Regime.QUIET,
        quotes=(Quote(YES_TOKEN, Side.BUY, 0.75, 266.67),),
    )

    async def run() -> None:
        live = await engine.gateway.open_orders()
        assert [order.order_id for order in live] == ["buy-64"]
        assert "buy-64" in state.orders
        blocked = reconcile(target, live, tick=0.01, reprice_ticks=1, resize_frac=0.25)
        assert blocked.to_cancel == ["buy-64"]
        assert [quote.price for quote in blocked.to_place] == [0.75]
        assert await engine.gateway.cancel(blocked.to_cancel) is False
        client.cancel_payload = {"canceled": ["buy-64"]}
        assert await engine.gateway.cancel_asset(YES_TOKEN) is True
        after = await engine.gateway.open_orders()
        assert after == []
        assert "buy-64" not in state.orders
        assert wakes == ["cid-yes"]
        allowed = reconcile(target, after, tick=0.01, reprice_ticks=1, resize_frac=0.25)
        assert allowed.to_cancel == []
        assert [quote.price for quote in allowed.to_place] == [0.75]

    asyncio.run(run())


def _heartbeat_gateway(results: list[bool]) -> SimpleNamespace:
    """A gateway whose heartbeat replays `results` and counts failures like the fork."""
    gateway = SimpleNamespace(_hb_failures=0, heartbeat=None)

    async def original_heartbeat() -> bool:
        ok = results.pop(0)
        if ok:
            gateway._hb_failures = 0
            return True
        gateway._hb_failures += 1
        return False

    gateway.heartbeat = original_heartbeat
    return gateway


def test_heartbeat_boot_grace_swallows_the_unstarted_chain() -> None:
    """The first rejected beats neither count nor arm the halt."""
    gateway = _heartbeat_gateway([False, False, False])
    engine = SimpleNamespace(paper=False, gateway=gateway)
    install_heartbeat_boot_grace(cast(Any, engine))

    async def run() -> None:
        for _ in range(3):
            assert await gateway.heartbeat() is True
        assert gateway._hb_failures == 0

    asyncio.run(run())


def test_heartbeat_boot_grace_ends_at_the_first_success() -> None:
    """Once the chain works, a later failure counts and halts as before."""
    gateway = _heartbeat_gateway([False, True, False, False])
    engine = SimpleNamespace(paper=False, gateway=gateway)
    install_heartbeat_boot_grace(cast(Any, engine))

    async def run() -> None:
        assert await gateway.heartbeat() is True
        assert await gateway.heartbeat() is True
        assert await gateway.heartbeat() is False
        assert await gateway.heartbeat() is False
        assert gateway._hb_failures == 2

    asyncio.run(run())


def test_heartbeat_boot_grace_expires_on_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chain that never starts stops being excused once the grace window closes."""
    monkeypatch.setattr(seams_mod, "HEARTBEAT_BOOT_GRACE_SECONDS", 0.0)
    gateway = _heartbeat_gateway([False])
    engine = SimpleNamespace(paper=False, gateway=gateway)
    install_heartbeat_boot_grace(cast(Any, engine))

    async def run() -> None:
        assert await gateway.heartbeat() is False
        assert gateway._hb_failures == 1

    asyncio.run(run())


def test_heartbeat_boot_grace_skips_paper() -> None:
    """Paper has no exchange dead-man switch, so the seam leaves the gateway alone."""
    gateway = _heartbeat_gateway([])
    original = gateway.heartbeat
    engine = SimpleNamespace(paper=True, gateway=gateway)
    install_heartbeat_boot_grace(cast(Any, engine))
    assert gateway.heartbeat is original
