# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false

import asyncio
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from polymaker.marketdata import service as service_mod
from polymaker.marketdata.orderbook import BookLevel
from py_clob_client_v2.http_helpers import helpers
from pydantic import ValidationError

from dashboard import balance, hub_types, live_hub
from dashboard.balance import (
    BalanceAccount,
    BalanceRead,
    BalanceTracker,
    load_balance_account,
    parse_collateral_usdc,
)
from dashboard.catalog import ArchiveEntry, MapStatus, MapView
from dashboard.collateral import BalanceClient, CollateralReader
from dashboard.hub_types import (
    EMPTY_DAY_STATE,
    DayJob,
    DesiredSubs,
    desired_subscriptions,
    merge_day_state,
)
from dashboard.live_hub import (
    BooksCallback,
    HubConfig,
    HubTiming,
    LiveHub,
    ScheduleFn,
    _DiskResult,
    _TailRead,
)
from dashboard.market_books import BookSnapshot, DesiredMarket, HubBooks
from dashboard.summarize import (
    DayFold,
    DayResult,
    PositionsResult,
    RebateAccrual,
)
from dashboard.tails import TailView
from dashboard.wallet import FillEvent, OutboxPage, TokenCid, WalletSnapshot
from trader.wallet_store import WalletStateStore

YES = "yes-token"
NO = "no-token"
T3 = "third-token"
CID = "0xcond"
CID2 = "0xcond2"
NOW = 1_800_000_000.0

FAST_TIMING = HubTiming(
    disk_s=0.02,
    catalog_s=0.05,
    subs_s=0.02,
    day_s=3600.0,
    logs_s=3600.0,
    drain_timeout_s=2.0,
    balance_min_s=0.05,
    balance_period_s=0.3,
)


def _wait_until(cond: Callable[[], bool], timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("condition not met before timeout")


def _ev(
    seq: int,
    key: str,
    event: str,
    *,
    side: str | None = "BUY",
    price: float | None = 0.5,
    size: float | None = 4.0,
    status: str | None = None,
    cash_delta: float | None = None,
) -> FillEvent:
    return FillEvent(
        seq=seq,
        fill_key=key,
        event=event,
        token_id=YES,
        side=side,
        price=price,
        size=size,
        ts=NOW,
        status=status,
        cash_delta=cash_delta,
        maker_order_id="v-1",
    )


def _page(*events: FillEvent, has_more: bool = False, max_seq: int | None = None) -> OutboxPage:
    seqs = [e.seq for e in events]
    last = seqs[-1] if seqs else 0
    return OutboxPage(
        events=tuple(events),
        next_seq=seqs[-1] if seqs else None,
        max_seq=max_seq if max_seq is not None else last,
        has_more=has_more,
    )


def _ok(amount: float) -> BalanceRead:
    return BalanceRead(
        ok=True,
        collateral_usdc=amount,
        error=None,
        status_code=None,
        retry_after_s=None,
    )


def _fail(error: str = "boom", retry_after: float | None = None) -> BalanceRead:
    return BalanceRead(
        ok=False,
        collateral_usdc=None,
        error=error,
        status_code=None,
        retry_after_s=retry_after,
    )


def _insert_fill(
    conn: sqlite3.Connection,
    fill_key: str,
    *,
    status: str = "MATCHED",
    cash: float = -2.0,
) -> None:
    conn.execute(
        "INSERT INTO fill_ledger(fill_key, clob_trade_id, maker_order_id, token_id,"
        " side, price, size, ts, status, pre_size, pre_avg, cash_delta, is_maker)"
        " VALUES(?,?,?,?,?,?,?,?,?,0,0,?,1)",
        (fill_key, "t-" + fill_key, "v-1", YES, "BUY", 0.5, 4.0, NOW, status, cash),
    )


def _insert_outbox(conn: sqlite3.Connection, fill_key: str, event: str) -> None:
    conn.execute("INSERT INTO fill_outbox(fill_key, event) VALUES(?,?)", (fill_key, event))


def _archive(
    root: Path,
    match_id: str,
    *,
    cid: str = CID,
    yes: str = YES,
    no: str = NO,
    records: list[dict[str, object]] | None = None,
) -> Path:
    archive = root / match_id
    archive.mkdir(parents=True)
    (archive / "match.json").write_text(
        json.dumps(
            {
                "match_id": match_id,
                "game": "dota",
                "joined_at_utc": "2026-08-29T10:00:00Z",
                "market": {
                    "condition_id": cid,
                    "market_slug": f"dota-m-{match_id}",
                    "yes_token_id": yes,
                    "no_token_id": no,
                    "yes_is_radiant": True,
                },
            }
        )
    )
    if records is not None:
        (archive / "session.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return archive


def _account(funder: str = "0xf") -> BalanceAccount:
    return BalanceAccount(
        host="https://clob.example",
        chain_id=137,
        signature_type=2,
        pk="k",
        funder=funder,
    )


def _empty_fetch(url: str, timeout: float) -> object:
    return []


def _ok_logs(argv: Sequence[str], timeout_s: float) -> bytes:
    return b"log line\n"


def _hub_config(tmp_path: Path) -> HubConfig:
    live_root = tmp_path / "trader_live"
    legacy_root = tmp_path / "live_paper"
    live_root.mkdir(parents=True, exist_ok=True)
    legacy_root.mkdir(parents=True, exist_ok=True)
    wallet_dir = live_root / "wallet"
    wallet_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    return HubConfig(
        wallet_db=wallet_dir / "live.db",
        live_root=live_root,
        legacy_root=legacy_root,
        compose_file=tmp_path / "compose.yaml",
        service="live",
        ws_url="ws://unused",
        proxy=None,
        account=None,
        account_error="no credentials",
        sidecar_roots={},
        sidecar_root_errors=(),
        timing=FAST_TIMING,
        fetch=_empty_fetch,
        log_runner=_ok_logs,
        read_balance=None,
        make_books=lambda schedule, on_books: _FakeBooks(schedule, on_books),
        wall=time.time,
        mono=time.monotonic,
    )


class _FakeBooks:
    def __init__(self, schedule: ScheduleFn, on_books: BooksCallback) -> None:
        self._schedule = schedule
        self._on_books = on_books
        self._desired: dict[str, str] = {}
        self._applied_at: float | None = None
        self._stop = asyncio.Event()
        self.calls: list[tuple[DesiredMarket, ...]] = []
        self.closes = 0
        self.stopped = False

    @property
    def applied_at(self) -> float | None:
        return self._applied_at

    async def run(self) -> None:
        await self._stop.wait()

    async def apply_subscriptions(self, markets: tuple[DesiredMarket, ...]) -> bool:
        desired = {tok: m.condition_id for m in markets for tok in m.token_ids}
        changed = desired != self._desired
        self.calls.append(markets)
        if changed:
            self._desired = desired
            self._applied_at = time.time()
            self.closes += 1
        return changed

    def emit(self, books: tuple[BookSnapshot, ...]) -> None:
        self._schedule(lambda: self._on_books(books))

    async def shutdown(self) -> None:
        self.stopped = True
        self._stop.set()


class _StubHub(LiveHub):
    def __init__(self, config: HubConfig, events: list[str]) -> None:
        super().__init__(config)
        self._events = events

    def start(self) -> "_StubHub":
        self._events.append("start")
        return self

    def close(self, timeout_s: float | None = None) -> bool:
        self._events.append("close")
        self._closed = True
        return True


class _Reader:
    def __init__(self, amount: float = 12.5, gate: threading.Event | None = None) -> None:
        self.amount = amount
        self.gate = gate
        self.calls = 0
        self.entered = threading.Event()

    def __call__(self) -> BalanceRead:
        self.calls += 1
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5.0)
        return _ok(self.amount)


def test_tracker_seed_and_initial_request() -> None:
    tracker = BalanceTracker()
    assert tracker.next_delay(0.0) is None
    tracker.seed(frozenset({"k1"}), cursor_seq=7)
    assert tracker.cursor == 7 and tracker.awaiting == frozenset({"k1"})
    assert tracker.next_delay(0.0) == 0.0
    tracker.begin_request(now_mono=100.0, now_wall=1000.0)
    assert tracker.next_delay(100.0) is None
    tracker.finish_request(_ok(12.0), now_mono=101.0, now_wall=1001.0)
    snap = tracker.snapshot(now_mono=101.0, funder="0xf", funder_mismatch=False, source_error=None)
    assert snap.collateral_usdc == 12.0
    assert snap.covered_seq == 7 and snap.request_cutoff_seq == 7
    assert snap.significant_seq is None
    assert tracker.next_delay(101.0) == pytest.approx(59.0)
    assert tracker.next_delay(159.0) == pytest.approx(1.0)
    assert tracker.next_delay(160.0) == 0.0


def test_tracker_matched_confirmed_one_refresh() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 0)
    assert tracker.apply_page(_page(_ev(1, "k1", "matched")))
    assert not tracker.apply_page(_page(_ev(2, "k1", "confirmed")))
    snap = tracker.snapshot(now_mono=0.0, funder=None, funder_mismatch=False, source_error=None)
    assert snap.significant_seq == 1 and tracker.cursor == 2
    assert [e.seq for e in snap.pending] == [1]
    tracker.begin_request(now_mono=10.0, now_wall=0.0)
    tracker.finish_request(_ok(5.0), now_mono=11.0, now_wall=1.0)
    snap = tracker.snapshot(now_mono=11.0, funder=None, funder_mismatch=False, source_error=None)
    assert snap.covered_seq == 2 and snap.pending == ()
    assert tracker.next_delay(11.0) == pytest.approx(59.0)


def test_tracker_seeded_matched_confirmed_not_dirty() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset({"k1"}), 5)
    assert not tracker.apply_page(_page(_ev(6, "k1", "confirmed")))
    tracker.begin_request(now_mono=0.0, now_wall=0.0)
    tracker.finish_request(_ok(1.0), now_mono=1.0, now_wall=1.0)
    snap = tracker.snapshot(now_mono=1.0, funder=None, funder_mismatch=False, source_error=None)
    assert snap.covered_seq == 6 and snap.significant_seq is None
    assert tracker.next_delay(1.0) == pytest.approx(59.0)


def test_tracker_direct_confirmed_and_failed_dirty() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 0)
    assert tracker.apply_page(_page(_ev(1, "kX", "confirmed", cash_delta=-1.5)))
    assert tracker.apply_page(_page(_ev(2, "kY", "failed", cash_delta=0.0)))
    snap = tracker.snapshot(now_mono=0.0, funder=None, funder_mismatch=False, source_error=None)
    assert snap.significant_seq == 2
    assert [(e.seq, e.event) for e in snap.pending] == [(1, "confirmed"), (2, "failed")]


def test_tracker_failed_after_covered_matched_dirties() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 0)
    tracker.apply_page(_page(_ev(1, "k1", "matched")))
    tracker.begin_request(now_mono=0.0, now_wall=0.0)
    tracker.finish_request(_ok(9.0), now_mono=1.0, now_wall=1.0)
    assert not tracker.wants_request(1.0)
    assert tracker.apply_page(_page(_ev(2, "k1", "failed", cash_delta=0.0)))
    snap = tracker.snapshot(now_mono=1.0, funder=None, funder_mismatch=False, source_error=None)
    assert snap.significant_seq == 2 and snap.covered_seq == 1
    assert tracker.next_delay(1.0) == pytest.approx(4.0)


def test_tracker_event_during_request_stays_pending() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 0)
    tracker.apply_page(_page(_ev(1, "k1", "matched")))
    tracker.begin_request(now_mono=10.0, now_wall=0.0)
    tracker.apply_page(_page(_ev(2, "k2", "matched")))
    tracker.finish_request(_ok(10.0), now_mono=11.0, now_wall=1.0)
    snap = tracker.snapshot(now_mono=11.0, funder=None, funder_mismatch=False, source_error=None)
    assert snap.covered_seq == 1
    assert [e.seq for e in snap.pending] == [2]
    assert tracker.wants_request(11.0)
    assert tracker.next_delay(11.0) == pytest.approx(4.0)
    assert tracker.next_delay(15.0) == 0.0


def test_tracker_coalescing_and_five_second_floor() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 0)
    tracker.begin_request(now_mono=0.0, now_wall=0.0)
    tracker.finish_request(_ok(1.0), now_mono=1.0, now_wall=1.0)
    tracker.apply_page(_page(_ev(2, "k1", "matched")))
    tracker.apply_page(_page(_ev(3, "k2", "matched")))
    assert tracker.next_delay(2.0) == pytest.approx(3.0)
    assert tracker.next_delay(5.0) == 0.0
    tracker.begin_request(now_mono=5.0, now_wall=5.0)
    tracker.finish_request(_ok(1.0), now_mono=6.0, now_wall=6.0)
    snap = tracker.snapshot(now_mono=6.0, funder=None, funder_mismatch=False, source_error=None)
    assert snap.covered_seq == 3 and snap.pending == ()


def test_tracker_backoff_and_retry_after() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 0)
    tracker.begin_request(now_mono=0.0, now_wall=0.0)
    tracker.finish_request(_fail(), now_mono=1.0, now_wall=1.0)
    assert tracker.next_delay(1.0) == pytest.approx(5.0)
    tracker.apply_page(_page(_ev(1, "k1", "matched")))
    assert tracker.next_delay(1.0) == pytest.approx(5.0)
    tracker.begin_request(now_mono=6.0, now_wall=6.0)
    tracker.finish_request(_fail(), now_mono=7.0, now_wall=7.0)
    assert tracker.next_delay(7.0) == pytest.approx(10.0)
    tracker.begin_request(now_mono=17.0, now_wall=17.0)
    tracker.finish_request(_fail(retry_after=42.0), now_mono=18.0, now_wall=18.0)
    assert tracker.next_delay(18.0) == pytest.approx(42.0)
    tracker.begin_request(now_mono=60.0, now_wall=60.0)
    tracker.finish_request(_ok(3.0), now_mono=61.0, now_wall=61.0)
    snap = tracker.snapshot(now_mono=61.0, funder=None, funder_mismatch=False, source_error=None)
    assert snap.last_error is None and snap.collateral_usdc == 3.0
    assert tracker.next_delay(61.0) == pytest.approx(59.0)


def test_tracker_zero_is_a_real_value() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 0)
    tracker.begin_request(now_mono=0.0, now_wall=0.0)
    tracker.finish_request(_ok(0.0), now_mono=1.0, now_wall=1.0)
    snap = tracker.snapshot(now_mono=1.0, funder=None, funder_mismatch=False, source_error=None)
    assert snap.collateral_usdc == 0.0 and snap.response_at is not None


def test_tracker_evidence_gaps_and_two_legs() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 0)
    tracker.apply_page(
        _page(
            _ev(1, "k1", "matched"),
            _ev(2, "k2", "matched"),
            _ev(3, "k3", "matched", price=None),
            _ev(4, "k4", "bogus"),
        )
    )
    snap = tracker.snapshot(now_mono=0.0, funder=None, funder_mismatch=False, source_error=None)
    assert tracker.awaiting == frozenset({"k1", "k2", "k3"})
    assert snap.evidence_gaps == 2
    assert len(snap.pending) == 4


def test_tracker_ignores_events_at_or_below_cursor() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 4)
    assert not tracker.apply_page(
        _page(_ev(2, "k0", "matched"), _ev(3, "k9", "confirmed"), max_seq=4)
    )
    assert tracker.cursor == 4


def test_tracker_pending_evidence_capped() -> None:
    tracker = BalanceTracker()
    tracker.seed(frozenset(), 0)
    total = balance.PENDING_LIMIT + 6
    tracker.apply_page(_page(*(_ev(seq, f"k{seq}", "matched") for seq in range(1, total + 1))))
    snap = tracker.snapshot(now_mono=0.0, funder=None, funder_mismatch=False, source_error=None)
    assert len(snap.pending) == balance.PENDING_LIMIT
    assert snap.pending_dropped == 6
    assert snap.pending[-1].seq == total and snap.pending[0].seq == 7


def test_parse_collateral_converts_and_rejects() -> None:
    cases = {"0": 0.0, "1": 0.000001, "500000": 0.5, "1000000": 1.0, "125500000": 125.5}
    for raw, expected in cases.items():
        assert parse_collateral_usdc({"balance": raw}) == pytest.approx(expected)
    assert parse_collateral_usdc({"balance": 3000000}) == pytest.approx(3.0)
    for bad in (
        {},
        {"balance": ""},
        {"balance": "  "},
        {"balance": "-5"},
        {"balance": "abc"},
        {"balance": "1.5"},
        {"balance": float("nan")},
        {"balance": True},
        "raw-string",
        None,
    ):
        with pytest.raises(ValueError):
            parse_collateral_usdc(bad)


def test_collateral_reader_calls_only_allowance() -> None:
    calls: list[str] = []
    params_seen: list[object] = []

    class _FakeClient:
        def derive_api_key(self) -> object:
            calls.append("derive")
            return object()

        def set_api_creds(self, creds: object) -> None:
            calls.append("creds")

        def get_balance_allowance(self, params: object) -> object:
            calls.append("balance")
            params_seen.append(params)
            return {"balance": "2500000"}

    built: list[BalanceAccount] = []

    def factory(account: BalanceAccount) -> BalanceClient:
        built.append(account)
        client = _FakeClient()
        client.set_api_creds(client.derive_api_key())
        return client

    reader = CollateralReader(_account(), client_factory=factory)
    first = reader.read()
    second = reader.read()
    assert len(built) == 1 and built[0].funder == "0xf"
    assert first.ok and first.collateral_usdc == 2.5
    assert second.ok
    assert calls == ["derive", "creds", "balance", "balance"]
    params = params_seen[0]
    assert "COLLATERAL" in repr(params).upper()


def test_collateral_reader_error_mapping() -> None:
    class _RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"Retry-After": "42"})

    class _BoomClient:
        def derive_api_key(self) -> object:
            return object()

        def set_api_creds(self, creds: object) -> None:
            return None

        def get_balance_allowance(self, params: object) -> object:
            raise _RateLimited("throttled")

    reader = CollateralReader(_account(), client_factory=lambda account: _BoomClient())
    result = reader.read()
    assert not result.ok and result.error == "_RateLimited"
    assert result.status_code == 429 and result.retry_after_s == 42.0


def test_sdk_transport_has_finite_timeouts() -> None:
    timeout = helpers._http_client.timeout
    assert timeout is not None
    for name in ("connect", "read", "write", "pool"):
        value = getattr(timeout, name, None)
        assert isinstance(value, (int, float)) and 0.0 < value < float("inf")


def test_load_balance_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = tmp_path / "trading.toml"
    toml.write_text("[wallet]\nsignature_type = 1\n")
    monkeypatch.setenv("PK", "0xkey")
    monkeypatch.setenv("BROWSER_ADDRESS", "0xFUNDER")
    account = load_balance_account(toml, os.environ.get)
    assert account is not None
    assert account.signature_type == 1 and account.funder == "0xFUNDER"
    monkeypatch.delenv("BROWSER_ADDRESS")
    assert load_balance_account(toml, os.environ.get) is None
    toml.write_text("[wallet]\nchain_id = 'abc'\n")
    monkeypatch.setenv("BROWSER_ADDRESS", "0xFUNDER")
    with pytest.raises(ValidationError):
        load_balance_account(toml, os.environ.get)


class _FakeSocket:
    def __init__(self, frames: list[str], *, block: bool = False) -> None:
        self.frames = frames
        self.block = block
        self.sent: list[str] = []
        self.closes = 0
        self._release: asyncio.Event | None = asyncio.Event() if block else None

    async def __aenter__(self) -> "_FakeSocket":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def send(self, data: str) -> None:
        self.sent.append(data)

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for frame in self.frames:
                yield frame
            if self._release is not None:
                await self._release.wait()

        return gen()

    async def close(self) -> None:
        self.closes += 1
        if self._release is not None:
            self._release.set()


def _book(token: str, *, cid: str = CID, price: float = 0.5, size: float = 10.0) -> str:
    return json.dumps(
        {
            "event_type": "book",
            "market": cid,
            "asset_id": token,
            "bids": [{"price": str(price), "size": str(size)}],
            "asks": [{"price": str(price + 0.1), "size": "7"}],
            "timestamp": 1750000000000,
            "hash": "h1",
            "tick_size": "0.01",
        }
    )


def _delta(token: str, *, cid: str = CID, price: float = 0.55) -> str:
    return json.dumps(
        {
            "event_type": "price_change",
            "market": cid,
            "timestamp": 1750000000000,
            "price_changes": [{"asset_id": token, "price": str(price), "size": "3", "side": "BUY"}],
        }
    )


def _make_adapter(
    monkeypatch: pytest.MonkeyPatch, sockets: list[_FakeSocket]
) -> tuple[HubBooks, list[Callable[[], None]], list[tuple[BookSnapshot, ...]]]:
    queue: list[Callable[[], None]] = []
    published: list[tuple[BookSnapshot, ...]] = []
    adapter = HubBooks(schedule=queue.append, on_books=published.append)
    iterator = iter(sockets)

    def connect(url: str, **kw: object) -> _FakeSocket:
        return next(iterator)

    monkeypatch.setattr(service_mod, "websockets", SimpleNamespace(connect=connect))
    return adapter, queue, published


def _drain(queue: list[Callable[[], None]]) -> None:
    while queue:
        queue.pop(0)()


def test_books_full_snapshot_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> tuple[HubBooks, tuple[BookSnapshot, ...], tuple[BookSnapshot, ...]]:
        sock = _FakeSocket([_book(YES), _book(NO)], block=True)
        adapter, queue, published = _make_adapter(monkeypatch, [sock])
        await adapter.apply_subscriptions((DesiredMarket(CID, (YES, NO)),))
        task = asyncio.create_task(adapter._connect_and_listen())
        await asyncio.sleep(0.05)
        _drain(queue)
        live = published[-1]
        await adapter._close_socket()
        await task
        _drain(queue)
        return adapter, live, published[-1]

    adapter, live, snap = asyncio.run(scenario())
    assert adapter.generation == 1
    by_token = {b.token_id: b for b in snap}
    assert set(by_token) == {YES, NO}
    yes = by_token[YES]
    assert yes.ready and yes.initialized and yes.generation == 1
    assert yes.bids == (BookLevel(0.5, 10.0),)
    assert yes.asks == (BookLevel(0.6, 7.0),)
    assert yes.book_hash == "h1" and yes.exchange_ts == 1750000000.0
    assert yes.local_ts is not None and yes.tick_size == 0.01
    assert not yes.connected and yes.disconnected_since is not None
    live_by = {b.token_id: b for b in live}
    assert live_by[YES].connected and live_by[YES].disconnected_since is None


def test_books_delta_before_snapshot_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> list[tuple[BookSnapshot, ...]]:
        adapter, queue, published = _make_adapter(monkeypatch, [_FakeSocket([_delta(YES)])])
        await adapter.apply_subscriptions((DesiredMarket(CID, (YES, NO)),))
        await adapter._connect_and_listen()
        _drain(queue)
        adapter.feed_frame(_book(YES))
        _drain(queue)
        return published

    published = asyncio.run(scenario())
    first = {b.token_id: b for b in published[0]}
    assert not first[YES].ready and first[YES].bids == ()
    assert not first[YES].initialized
    last = {b.token_id: b for b in published[-1]}
    assert last[YES].ready and last[YES].bids == (BookLevel(0.5, 10.0),)
    assert not last[NO].ready and not last[NO].initialized


def test_books_empty_snapshot_is_received(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = json.dumps(
        {
            "event_type": "book",
            "market": CID,
            "asset_id": YES,
            "bids": [],
            "asks": [],
            "timestamp": 1750000000000,
            "hash": "",
            "tick_size": "0.01",
        }
    )

    async def scenario() -> list[tuple[BookSnapshot, ...]]:
        adapter, queue, published = _make_adapter(monkeypatch, [_FakeSocket([frame])])
        await adapter.apply_subscriptions((DesiredMarket(CID, (YES, NO)),))
        await adapter._connect_and_listen()
        _drain(queue)
        return published

    published = asyncio.run(scenario())
    last = {b.token_id: b for b in published[-1]}
    assert last[YES].ready and last[YES].bids == () and last[YES].asks == ()


def test_books_reconnect_resets_readiness_and_retains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> tuple[tuple[BookSnapshot, ...], tuple[BookSnapshot, ...], HubBooks]:
        adapter, queue, published = _make_adapter(
            monkeypatch,
            [_FakeSocket([_book(YES), _book(NO)]), _FakeSocket([_delta(YES)])],
        )
        await adapter.apply_subscriptions((DesiredMarket(CID, (YES, NO)),))
        await adapter._connect_and_listen()
        _drain(queue)
        await adapter._connect_and_listen()
        _drain(queue)
        before_feed = published[-1]
        adapter.feed_frame(_book(YES, price=0.7))
        _drain(queue)
        return before_feed, published[-1], adapter

    before_feed, last_snap, adapter = asyncio.run(scenario())
    assert adapter.generation == 2
    by_token = {b.token_id: b for b in before_feed}
    assert not by_token[YES].ready and not by_token[NO].ready
    assert by_token[YES].initialized and by_token[NO].initialized
    assert by_token[YES].bids == (BookLevel(0.5, 10.0),)
    last = {b.token_id: b for b in last_snap}
    assert last[YES].ready and last[YES].bids == (BookLevel(0.7, 10.0),)
    assert not last[NO].ready and last[NO].bids == (BookLevel(0.5, 10.0),)


def test_books_apply_subscriptions_closes_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> tuple[_FakeSocket, HubBooks, tuple[BookSnapshot, ...]]:
        sock = _FakeSocket([_book(YES), _book(NO)], block=True)
        adapter, queue, published = _make_adapter(monkeypatch, [sock])
        await adapter.apply_subscriptions((DesiredMarket(CID, (YES, NO)),))
        task = asyncio.create_task(adapter._connect_and_listen())
        await asyncio.sleep(0.05)
        _drain(queue)
        same = await adapter.apply_subscriptions((DesiredMarket(CID, (YES, NO)),))
        assert not same and sock.closes == 0
        changed = await adapter.apply_subscriptions(
            (DesiredMarket(CID, (YES,)), DesiredMarket(CID2, (T3, "fourth")))
        )
        assert changed and sock.closes == 1
        await task
        _drain(queue)
        return sock, adapter, published[-1]

    sock, adapter, last_snap = asyncio.run(scenario())
    _ = sock
    last = {b.token_id: b for b in last_snap}
    assert set(last) == {YES, T3, "fourth"}
    assert NO not in adapter.books and NO not in adapter._token_condition
    assert adapter.generation == 1


def test_books_empty_set_closes_socket() -> None:
    async def scenario() -> HubBooks:
        adapter = HubBooks(schedule=lambda fn: fn(), on_books=lambda snaps: None)
        sock = _FakeSocket([], block=True)
        await adapter.apply_subscriptions((DesiredMarket(CID, (YES, NO)),))
        adapter._ws = sock
        changed = await adapter.apply_subscriptions(())
        assert changed and sock.closes == 1
        return adapter

    adapter = asyncio.run(scenario())
    assert adapter.applied_at is not None


def _wallet_snap(token_cids: tuple[TokenCid, ...]) -> WalletSnapshot:
    return WalletSnapshot(
        db_path=Path("x.db"),
        ok=True,
        error=None,
        read_at=NOW,
        funder="0xf",
        sessions=(),
        bindings=(),
        open_buy_commands=(),
        unsettled_buys=(),
        positions=(),
        token_cids=token_cids,
        booked_buy_qty=(),
        max_outbox_seq=0,
        matched_fill_keys=frozenset(),
        fill_boundary_ok=True,
        notes=(),
    )


def _entry(
    match_id: str,
    cid: str | None,
    yes: str | None,
    no: str | None,
    *,
    finished: bool = False,
    cleanup_proven: bool = False,
    session_ended: bool = False,
    has_journal: bool = True,
    record_only: bool = False,
) -> ArchiveEntry:
    return ArchiveEntry(
        match_id=match_id,
        archive_dir=Path("/x") / match_id,
        tree="live",
        game="dota",
        slug=None,
        event_slug=None,
        joined_at_utc=None,
        condition_id=cid,
        yes_token=yes,
        no_token=no,
        yes_is_radiant=True,
        radiant=None,
        dire=None,
        map_number=None,
        outcome_0_name=None,
        outcome_1_name=None,
        finished=finished,
        cleanup_proven=cleanup_proven,
        session_ended=session_ended,
        has_journal=has_journal,
        record_only=record_only,
        last_write=None,
        realized=None,
        imv=None,
        rebate=None,
        net=None,
        fill_count=None,
        closed_observed_at=None,
        last_decision=None,
        params=None,
    )


def _view(entry: ArchiveEntry, status: MapStatus = "live") -> MapView:
    return MapView(
        entry=entry,
        status=status,
        write_age_s=0.0,
        decision_age_s=None,
        feed_age_s=None,
        decision=None,
        evidence=(),
    )


def test_desired_subs_and_conflicts() -> None:
    views = (
        _view(_entry("m1", CID, YES, NO)),
        _view(_entry("m2", CID2, T3, "fourth"), status="stale"),
        _view(_entry("m3", None, None, None)),
        _view(_entry("m4", "0x4", "dup", "dup")),
    )
    token_cids = (TokenCid(YES, CID), TokenCid(T3, "0xOTHER"))
    desired = desired_subscriptions(views, token_cids)
    assert desired == DesiredSubs(
        markets=(DesiredMarket(CID, (YES, NO)),),
        missing=("m3", "m4"),
        conflicts=(),
    )
    views = (*views, _view(_entry("m5", "0x5", T3, "other")))
    desired = desired_subscriptions(views, token_cids)
    assert desired.conflicts == ("m5",)


def _tail_view(path: Path) -> TailView:
    return TailView(
        path=path,
        mtime_ns=1,
        size=2,
        compressed=False,
        truncated_start=False,
        records_kept=1,
        malformed=0,
        saw_session_end=False,
        last_signal=None,
        last_quote=None,
        last_fill=None,
        last_error=None,
        last_record=None,
    )


def test_tail_views_rebuilt_per_disk_pass(tmp_path: Path) -> None:
    hub = LiveHub(_hub_config(tmp_path))
    p1, p2, p3 = Path("a"), Path("b"), Path("c")
    result = _DiskResult(
        wallet=_wallet_snap(()),
        page=None,
        tails=(_TailRead(p1, _tail_view(p1)), _TailRead(p2, _tail_view(p2))),
    )
    hub._apply_disk(result)
    assert set(hub._tail_views) == {p1, p2}
    result = _DiskResult(
        wallet=_wallet_snap(()),
        page=None,
        tails=(_TailRead(p3, _tail_view(p3)),),
    )
    hub._apply_disk(result)
    assert set(hub._tail_views) == {p3}


def _day_result(
    day: str,
    *,
    fold: DayFold | None = None,
    complete: bool = True,
    error: str | None = None,
    fetched_at: float | None = 1000.0,
    positions: PositionsResult | None = None,
) -> DayResult:
    return DayResult(
        day=day,
        fold=fold,
        fetched_at=fetched_at,
        complete=complete,
        error=error,
        activity=None,
        positions=positions,
    )


def _fold() -> DayFold:
    return DayFold(
        buy=10.0,
        sell=5.0,
        redeem=1.0,
        rebate=0.5,
        cash=-4.0,
        open_mark=2.0,
        pnl=-1.5,
        n_buy=1,
        n_sell=1,
        n_redeem=1,
        n_rebate=1,
        n_open=1,
    )


def _positions(complete: bool) -> PositionsResult:
    return PositionsResult(
        positions=(),
        as_of=NOW,
        started_at=NOW,
        completed_at=NOW,
        pages=1,
        traversal_complete=complete,
        stop_reason="done" if complete else "timeout",
        error=None,
    )


def _day_job(
    day: str,
    *,
    generation: int = 1,
    funder: str = "0xf",
    result: DayResult | None = None,
    payout_known: bool = True,
    payout_ts: float | None = 500.0,
    accrual: RebateAccrual | None = None,
    accrual_error: str | None = None,
    started_at: float = 1999.0,
    finished_at: float = 2000.0,
) -> DayJob:
    return DayJob(
        funder=funder,
        generation=generation,
        started_at=started_at,
        finished_at=finished_at,
        day=result if result is not None else _day_result(day, fold=_fold()),
        payout_known=payout_known,
        payout_ts=payout_ts,
        accrual=accrual,
        accrual_error=accrual_error,
    )


def test_day_merge_retains_fold_positions_and_accrual() -> None:
    accrual = RebateAccrual(total=1.5, per_game={"dota": 1.5}, fills=3, matches=1)
    state = merge_day_state(
        EMPTY_DAY_STATE,
        _day_job(
            "2026-10-05",
            result=_day_result("2026-10-05", fold=_fold(), positions=_positions(True)),
            accrual=accrual,
        ),
        generation=1,
        funder="0xf",
    )
    snap = state.snapshot
    assert snap.fold is not None and snap.fold.pnl == -1.5
    assert snap.complete and snap.fold_at == 1000.0
    assert snap.positions is not None and snap.payout_ts == 500.0
    assert snap.accrual_total == 1.5 and snap.accrual_per_game["dota"] == 1.5
    assert isinstance(snap.accrual_per_game, MappingProxyType)
    state = merge_day_state(
        state,
        _day_job(
            "2026-10-05",
            result=_day_result(
                "2026-10-05",
                fold=None,
                complete=False,
                error="timeout",
                fetched_at=None,
                positions=_positions(False),
            ),
            payout_known=False,
            payout_ts=None,
            accrual_error="payout search incomplete: timeout",
            finished_at=2001.0,
        ),
        generation=1,
        funder="0xf",
    )
    snap = state.snapshot
    assert snap.fold is not None and snap.fold.pnl == -1.5
    assert not snap.complete and snap.error == "timeout"
    assert snap.positions is not None
    assert snap.accrual_total == 1.5
    assert snap.accrual_error == "payout search incomplete: timeout"
    state = merge_day_state(
        state,
        _day_job(
            "2026-10-06",
            result=_day_result(
                "2026-10-06", fold=None, complete=False, error="timeout", fetched_at=None
            ),
            finished_at=2002.0,
        ),
        generation=1,
        funder="0xf",
    )
    snap = state.snapshot
    assert snap.day == "2026-10-06" and snap.fold is None


def test_day_merge_discards_stale_generation_and_funder() -> None:
    state = merge_day_state(
        EMPTY_DAY_STATE,
        _day_job("2026-10-05", funder="0xother"),
        generation=1,
        funder="0xf",
    )
    retired = DayJob(
        funder="0xf",
        generation=0,
        started_at=0.5,
        finished_at=1.0,
        day=_day_result("2026-10-05", fold=_fold()),
        payout_known=True,
        payout_ts=1.0,
        accrual=None,
        accrual_error=None,
    )
    state = merge_day_state(state, retired, generation=1, funder="0xf")
    assert state.snapshot.fold is None
    state = merge_day_state(state, _day_job("2026-10-05"), generation=1, funder="0xf")
    assert state.snapshot.fold is not None


def test_day_merge_requires_funder() -> None:
    state = merge_day_state(EMPTY_DAY_STATE, _day_job("2026-10-05"), generation=1, funder=None)
    assert state.snapshot.fold is None


def test_day_job_empty_api() -> None:
    job = hub_types.run_day_job(
        funder="0xf",
        generation=1,
        trees=(),
        fetch=_empty_fetch,
        wall=lambda: NOW,
    )
    assert job.day.complete and job.day.fold is None
    assert job.payout_known and job.payout_ts is None
    assert job.accrual is None and job.accrual_error == "no known rebate payout"


def test_day_job_payout_failure_keeps_accrual_unknown() -> None:
    def fetch(url: str, timeout: float) -> object:
        if "activity" in url:
            raise OSError("net down")
        return []

    job = hub_types.run_day_job(
        funder="0xf",
        generation=1,
        trees=(),
        fetch=fetch,
        wall=lambda: NOW,
    )
    assert not job.day.complete and job.day.error is not None
    assert not job.payout_known
    assert job.accrual is None and job.accrual_error is not None


def test_hub_singleton_and_replace(tmp_path: Path) -> None:
    live_hub.close_live_hub()
    events: list[str] = []
    made: list[LiveHub] = []
    cfg = _hub_config(tmp_path)

    def factory() -> LiveHub:
        stub = _StubHub(cfg, events)
        made.append(stub)
        return stub

    try:
        first = live_hub._acquire_hub(factory)
        assert live_hub._acquire_hub(factory) is first
        results: list[LiveHub] = []
        threads = [
            threading.Thread(target=lambda: results.append(live_hub._acquire_hub(factory)))
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert all(h is first for h in results) and len(made) == 1
        second = live_hub._swap_hub(factory)
        assert second is not first and events == ["start", "close", "start"]
        live_hub.close_live_hub()
        assert second.closed
        third = live_hub._acquire_hub(factory)
        assert third is not second
    finally:
        live_hub.close_live_hub()
    assert events[-1] == "close"


def test_hub_snapshot_before_start(tmp_path: Path) -> None:
    hub = LiveHub(_hub_config(tmp_path))
    snap = hub.get_snapshot()
    assert not snap.running and snap.error is None
    assert snap.balance.collateral_usdc is None and snap.balance.funder is None
    assert snap.wallet.error == "no wallet read yet"
    assert snap.day.error is None and snap.day.fold is None
    assert snap.books == () and snap.maps == ()


def test_hub_lifecycle(tmp_path: Path) -> None:
    cfg = _hub_config(tmp_path)
    WalletStateStore(cfg.wallet_db).close()
    hub = LiveHub(cfg).start()
    _wait_until(lambda: hub.get_snapshot().running)
    _wait_until(lambda: hub.get_snapshot().wallet.ok)
    assert hub.get_snapshot().reserve is not None
    assert hub.close()
    assert not hub.get_snapshot().running
    assert hub.close()


def test_hub_startup_failure_cleans_up(tmp_path: Path) -> None:
    def bad_factory(schedule: ScheduleFn, on_books: BooksCallback) -> _FakeBooks:
        raise RuntimeError("no books")

    cfg = replace(_hub_config(tmp_path), make_books=bad_factory)
    hub = LiveHub(cfg).start()
    _wait_until(lambda: hub.get_snapshot().error == "RuntimeError", timeout_s=5.0)
    _wait_until(lambda: hub.closed, timeout_s=5.0)
    assert hub.close() and not hub.get_snapshot().running


def test_hub_snapshot_while_balance_held(tmp_path: Path) -> None:
    cfg = _hub_config(tmp_path)
    WalletStateStore(cfg.wallet_db).close()
    conn = sqlite3.connect(cfg.wallet_db)
    conn.execute("INSERT INTO wallet_identity(k, v) VALUES('funder', '0xf')")
    conn.commit()
    conn.close()
    gate = threading.Event()
    reader = _Reader(42.0, gate=gate)
    cfg = replace(cfg, account=_account(), account_error=None, read_balance=reader)
    hub = LiveHub(cfg).start()
    try:
        _wait_until(reader.entered.is_set)
        started = time.monotonic()
        snap = hub.get_snapshot()
        assert time.monotonic() - started < 1.0
        assert snap.balance.inflight
        assert snap.balance.collateral_usdc is None
        gate.set()
        _wait_until(lambda: hub.get_snapshot().balance.collateral_usdc == 42.0)
    finally:
        gate.set()
        assert hub.close()


def test_hub_end_to_end(tmp_path: Path) -> None:
    cfg = _hub_config(tmp_path)
    timing = replace(cfg.timing, balance_period_s=60.0)
    WalletStateStore(cfg.wallet_db).close()
    conn = sqlite3.connect(cfg.wallet_db)
    _insert_fill(conn, "k1")
    _insert_outbox(conn, "k1", "matched")
    conn.execute("INSERT INTO wallet_identity(k, v) VALUES('funder', '0xf')")
    conn.execute("INSERT INTO token_cid(token_id, condition_id) VALUES(?,?)", (YES, CID))
    conn.commit()
    conn.close()
    _archive(
        cfg.live_root,
        "m1",
        cid=CID,
        yes=YES,
        no=NO,
        records=[{"kind": "session_start", "execution_mode": "live", "schema_version": 7}],
    )
    reader = _Reader(12.5)
    books: list[_FakeBooks] = []

    def factory(schedule: ScheduleFn, on_books: BooksCallback) -> _FakeBooks:
        fake = _FakeBooks(schedule, on_books)
        books.append(fake)
        return fake

    cfg = replace(
        cfg,
        timing=timing,
        account=_account(),
        account_error=None,
        read_balance=reader,
        make_books=factory,
    )
    hub = LiveHub(cfg).start()
    try:
        _wait_until(lambda: hub.get_snapshot().balance.collateral_usdc == 12.5)
        snap = hub.get_snapshot()
        assert snap.wallet.ok and snap.wallet.funder == "0xf"
        assert snap.balance.covered_seq == 1 and snap.balance.collateral_usdc == 12.5
        assert not snap.balance.funder_mismatch
        _wait_until(
            lambda: any(m.condition_id == CID for m in hub.get_snapshot().subscriptions.markets)
        )
        assert books and books[0].applied_at is not None
        maps = {v.entry.match_id: v.status for v in hub.get_snapshot().maps}
        assert maps["m1"] == "live"
        books[0].emit(
            (
                BookSnapshot(
                    token_id=YES,
                    condition_id=CID,
                    bids=(BookLevel(0.5, 10.0),),
                    asks=(BookLevel(0.6, 7.0),),
                    tick_size=0.01,
                    book_hash="h1",
                    exchange_ts=1000.0,
                    local_ts=1001.0,
                    generation=1,
                    initialized=True,
                    ready=True,
                    connected=True,
                    disconnected_since=None,
                    truncated=False,
                ),
            )
        )
        _wait_until(lambda: len(hub.get_snapshot().books) == 1)
        old = hub.get_snapshot()
        books[0].emit(())
        _wait_until(lambda: hub.get_snapshot().books == ())
        assert old.books[0].bids == (BookLevel(0.5, 10.0),)
        conn = sqlite3.connect(cfg.wallet_db)
        conn.execute("UPDATE fill_ledger SET status='CONFIRMED' WHERE fill_key='k1'")
        _insert_outbox(conn, "k1", "confirmed")
        conn.commit()
        conn.close()
        _wait_until(lambda: hub.get_snapshot().wallet.max_outbox_seq == 2)
        time.sleep(0.15)
        assert reader.calls == 1
        conn = sqlite3.connect(cfg.wallet_db)
        _insert_fill(conn, "k2", status="FAILED", cash=0.0)
        _insert_outbox(conn, "k2", "matched")
        _insert_outbox(conn, "k2", "failed")
        conn.commit()
        conn.close()
        _wait_until(lambda: hub.get_snapshot().balance.covered_seq == 4)
        assert reader.calls >= 2
        snap = hub.get_snapshot()
        assert snap.reserve is not None and not snap.reserve.incomplete
        _wait_until(lambda: hub.get_snapshot().day.attempt_at is not None)
        snap = hub.get_snapshot()
        assert snap.day.complete and snap.day.fold is None
        assert snap.day.accrual_error == "no known rebate payout"
        _wait_until(lambda: hub.get_snapshot().service_logs.ok)
        assert hub.get_snapshot().service_logs.text == "log line\n"
    finally:
        assert hub.close()


def test_hub_funder_mismatch_withholds_collateral(tmp_path: Path) -> None:
    cfg = _hub_config(tmp_path)
    WalletStateStore(cfg.wallet_db).close()
    conn = sqlite3.connect(cfg.wallet_db)
    conn.execute("INSERT INTO wallet_identity(k, v) VALUES('funder', '0xf')")
    conn.commit()
    conn.close()
    reader = _Reader(7.0)
    cfg = replace(cfg, account=_account("0xOTHER"), account_error=None, read_balance=reader)
    hub = LiveHub(cfg).start()
    try:
        _wait_until(lambda: reader.calls >= 1)
        _wait_until(lambda: hub.get_snapshot().wallet.ok)
        snap = hub.get_snapshot()
        assert snap.balance.funder_mismatch
        assert snap.balance.collateral_usdc is None
    finally:
        assert hub.close()


def test_hub_missing_db_not_created(tmp_path: Path) -> None:
    cfg = _hub_config(tmp_path)
    assert not cfg.wallet_db.exists()
    hub = LiveHub(cfg).start()
    try:
        _wait_until(lambda: hub.get_snapshot().wallet.error is not None)
        assert not cfg.wallet_db.exists()
        assert not hub.get_snapshot().wallet.ok
    finally:
        assert hub.close()
