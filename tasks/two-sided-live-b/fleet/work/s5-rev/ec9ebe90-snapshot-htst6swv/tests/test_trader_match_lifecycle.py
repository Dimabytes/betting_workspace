"""MatchWorker quiesce on every exit, leftover drop, attach-before-drain."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false

import asyncio
import json
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, cast

import pytest
from lol_grid_widget_fixtures import (
    LEC_SCOREBOARD,
    LEC_SERIES_ID,
    LEC_SERIES_TABLE,
    wrap,
)
from polymaker.domain import Fill, OpenOrder, Quote, Side, TradeState
from polymaker.state.tracker import TradeEvent
from test_grid_feed import _FrameSocket
from trader_session_fixtures import (
    CONDITION_ID,
    MATCH_ID,
    NO_TOKEN,
    YES_TOKEN,
    FakeLiveFeed,
    FakeModelServer,
    build_attached_worker,
    build_event,
    make_binding,
    make_meta,
    read_session_records,
)

from shared.utils.match_time import HORN_OFFSET_SECONDS
from strategy.lifecycle import empty_state
from strategy.types import (
    Budget,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    Permissions,
    RestingOrder,
    SignalUpdate,
)
from trader import match_worker, session_journal
from trader.bindings import TeamSides
from trader.core_persistence import CoreSessionKey, snapshot_checkpoint, upsert_session
from trader.fill_parsing import fill_key
from trader.game_profile import GAME_PROFILES
from trader.grid_live_feed import GridLiveFeed
from trader.grid_widget_types import ScoreboardPayload
from trader.live_feed import FeedSource, KillTick, MatchPhase, SideWait
from trader.match_meta import match_has_final
from trader.paths import (
    CORE_TRACE_FILENAME,
    EXECUTION_CLEANUP_FILENAME,
    GRID_STATE_ARCHIVE_FILENAME,
    MATCH_META_FILENAME,
    SESSION_JOURNAL_FILENAME,
)
from trader.session_quoting import fill_ts_utc
from trader.session_types import SidecarMeta, SignalReason
from trader.wallet_host import WalletHost
from trader.wallet_store import WalletFillProcessor, WalletStateStore


def _patch_archive_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point match archive writers at the test tmp dir."""
    monkeypatch.setattr(match_worker, "TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)


def _attach_without_engine(worker: match_worker.MatchWorker) -> None:
    """Mark the worker attached and quoting on its fake host/engine."""
    host = worker._host
    worker._attached = True
    worker._quoting = True
    worker._cell.publish(0.55)
    host.engine.metas[worker._cid] = make_meta()
    host.register_worker(worker._cid, {worker._yes, worker._no}, worker)


async def _fake_attach(worker: match_worker.MatchWorker, *_args: object, **_kwargs: object) -> None:
    """Stand-in for _try_attach used by run() exit-path tests."""
    _attach_without_engine(worker)


def test_disconnect_runs_quiesce_without_steam_final(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A feed that ends without finished still clears the cell and does not write final."""
    _patch_archive_root(monkeypatch, tmp_path)
    worker, fake = build_attached_worker(tmp_path, request, feed=FakeLiveFeed((build_event(10),)))
    fake.state.set_position(YES_TOKEN, 10.0, 0.4)
    monkeypatch.setattr(worker, "_try_attach", MethodType(_fake_attach, worker))

    asyncio.run(worker.run())

    assert worker._cell.forced is True
    assert worker._quoting is False
    assert worker._cid not in fake.metas
    assert fake.state.position(YES_TOKEN).size == 10.0
    assert match_has_final(tmp_path, MATCH_ID) is False
    assert not (tmp_path / MATCH_ID / EXECUTION_CLEANUP_FILENAME).exists()
    assert worker._host.keep_quiet_calls == []
    assert worker._host.detached_cids == [worker._cid]


def test_worker_tapes_every_non_terminal_snapshot(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """History records every non-terminal tick; the terminal tick never lands on the tape."""
    worker, _fake = build_attached_worker(tmp_path, request)
    worker._record_history(build_event(70).snapshot)
    worker._record_history(build_event(80).snapshot)
    worker._record_history(build_event(90, phase=MatchPhase.FINISHED).snapshot)
    assert list(worker._history._seconds) == [70, 80]


def test_history_gap_tick_journals_and_keeps_its_snapshot(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tick whose lag pivot falls in a tape gap is journaled, never reaches the
    model, and keeps its snapshot on the tape. consume is never called, so the
    watchdog timers keep running from the last valid tick and the resting SELL
    and published fair are untouched."""
    _patch_archive_root(monkeypatch, tmp_path)
    model = FakeModelServer(radiant_fair=0.60)
    worker, fake = build_attached_worker(tmp_path, request, model=model)
    _ready_books(worker)
    meta = make_meta()
    placed = asyncio.run(fake.gateway.place([Quote(NO_TOKEN, Side.SELL, 0.55, 5.0)], meta))
    for order in placed:
        fake.state.upsert_order(order)

    records_before = 0

    async def scenario() -> None:
        nonlocal records_before
        for second in (60, 70):
            event = build_event(second)
            worker._record_history(event.snapshot)
            await worker.handle_event(event)
        assert model.calls == 2
        generation = worker._watchdog._generation
        fair = worker._cell.yes_fair
        records_before = len(read_session_records(tmp_path / "journal"))
        gap = build_event(500)
        worker._record_history(gap.snapshot)
        await worker.handle_event(gap)
        assert worker._watchdog._generation == generation
        assert worker._cell.yes_fair == fair

    asyncio.run(scenario())
    assert model.calls == 2
    assert list(worker._history._seconds) == [500]
    assert worker._history._first_second == 60
    remaining = list(fake.state.orders.values())
    assert len(remaining) == 1
    assert remaining[0].side is Side.SELL
    records = read_session_records(tmp_path / "journal")
    new_records = records[records_before:]
    assert [record["kind"] for record in new_records] == ["history_gap"]
    assert new_records[0]["second"] == 500
    signals = [record for record in records if record["kind"] == "signal"]
    assert [record["second"] for record in signals] == [60, 70]


def test_first_tick_locks_feed_orientation_into_match_start(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GRID first tick rewrites MatchStart; a later tick cannot flip YES↔Radiant."""
    _patch_archive_root(monkeypatch, tmp_path)
    first = replace(
        build_event(-10, phase=MatchPhase.PRE_HORN, yes_is_radiant=False),
        source=FeedSource.GRID,
    )
    second = replace(
        build_event(10, yes_is_radiant=True),
        source=FeedSource.GRID,
    )
    worker, _fake = build_attached_worker(tmp_path, request, feed=FakeLiveFeed((first, second)))
    monkeypatch.setattr(worker, "_try_attach", MethodType(_fake_attach, worker))
    assert worker._yes_is_radiant is True

    asyncio.run(worker.run())

    assert worker._yes_is_radiant is False
    assert worker._discovered.market.yes_is_radiant is False
    assert worker._discovered.sides.radiant == "Team Secret"
    assert worker._discovered.sides.dire == "Aurora"
    document = json.loads((tmp_path / MATCH_ID / MATCH_META_FILENAME).read_text(encoding="utf-8"))
    assert document["market"]["yes_is_radiant"] is False
    assert document["teams"]["radiant"] == "Team Secret"
    assert document["teams"]["dire"] == "Aurora"


def test_exception_runs_quiesce_without_cleanup(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write fault after attach still quiesces and skips Steam-final artifacts."""
    _patch_archive_root(monkeypatch, tmp_path)
    worker, fake = build_attached_worker(
        tmp_path,
        request,
        feed=FakeLiveFeed((build_event(10),), error=RuntimeError("disk full")),
    )
    monkeypatch.setattr(worker, "_try_attach", MethodType(_fake_attach, worker))
    with pytest.raises(RuntimeError, match="disk full"):
        asyncio.run(worker.run())
    assert worker._cell.forced is True
    assert worker._cid not in fake.metas
    assert match_has_final(tmp_path, MATCH_ID) is False
    assert not (tmp_path / MATCH_ID / EXECUTION_CLEANUP_FILENAME).exists()


def test_cancellation_runs_quiesce(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling the worker task still stops quotes."""
    _patch_archive_root(monkeypatch, tmp_path)
    worker, fake = build_attached_worker(
        tmp_path, request, feed=FakeLiveFeed((build_event(10),), hang=True)
    )
    attached = asyncio.Event()

    async def fake_attach(*_args: object, **_kwargs: object) -> None:
        _attach_without_engine(worker)
        attached.set()

    monkeypatch.setattr(worker, "_try_attach", fake_attach)

    async def run() -> None:
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(attached.wait(), 5.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert worker._cell.forced is True
    assert worker._quoting is False
    assert worker._cid not in fake.metas
    assert match_has_final(tmp_path, MATCH_ID) is False
    assert not (tmp_path / MATCH_ID / EXECUTION_CLEANUP_FILENAME).exists()


def test_unattached_steam_final_writes_cleanup(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archive-only finish still writes execution_cleanup so --live and boot_scan drop it."""
    _patch_archive_root(monkeypatch, tmp_path)
    worker, _fake = build_attached_worker(tmp_path, request)
    worker._attached = False
    worker._quoting = False
    asyncio.run(worker._quiesce(True))
    assert (tmp_path / MATCH_ID / EXECUTION_CLEANUP_FILENAME).is_file()
    assert worker._host.keep_quiet_calls == []
    assert worker._host.detached_cids == []


def test_record_only_worker_archives_grid_feed_without_a_session(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """record_only writes the GRID archive, final, and cleanup; no session/attach/alerts."""
    _patch_archive_root(monkeypatch, tmp_path)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)
    finished_board = cast(ScoreboardPayload, json.loads(json.dumps(LEC_SCOREBOARD)))
    finished_board["games"][1]["status"] = "finished"
    finished_board["games"][1]["teams"][0]["won"] = True
    frames = (
        wrap("series_scoreboard_v2", 0, LEC_SCOREBOARD, LEC_SERIES_ID),
        wrap("series_table", 8, LEC_SERIES_TABLE, LEC_SERIES_ID),
        wrap("series_scoreboard_v2", 0, finished_board, LEC_SERIES_ID),
    )

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    alerts: list[str] = []
    finished_calls: list[object] = []
    monkeypatch.setattr(match_worker, "notify_in_background", alerts.append)
    monkeypatch.setattr(
        match_worker, "notify_session_finished", lambda *a: finished_calls.append(a)
    )
    match_id = "grid-2966907-m2"
    feed = GridLiveFeed(LEC_SERIES_ID, 2, "GIANTX", "Natus Vincere", match_id, GAME_PROFILES["lol"])
    attached, _fake = build_attached_worker(tmp_path, request, game="lol")
    host = attached._host
    host.unregister_worker(attached._cid)
    market = replace(
        attached._discovered.market,
        outcome_0_name="GIANTX",
        outcome_1_name="Natus Vincere",
        grid_series_id=LEC_SERIES_ID,
    )
    discovered = replace(
        attached._discovered,
        match_id=match_id,
        steam_match_id=None,
        sides=TeamSides(radiant="GIANTX", dire="Natus Vincere"),
        map_number=2,
        market=market,
        record_only=True,
    )
    worker = match_worker.MatchWorker(host, discovered, attached._model, "paper", feed, 3.0)

    asyncio.run(worker.run())

    archive_dir = tmp_path / match_id
    records = (archive_dir / GRID_STATE_ARCHIVE_FILENAME).read_text().splitlines()
    assert len(records) == len(frames)
    meta = json.loads((archive_dir / MATCH_META_FILENAME).read_text(encoding="utf-8"))
    assert meta["feed_source"] == "grid"
    assert meta["record_only"] is True
    assert meta["final"]["snapshot_count"] == 2
    assert meta["final"]["pnl"] is None
    assert (archive_dir / EXECUTION_CLEANUP_FILENAME).is_file()
    assert not (archive_dir / SESSION_JOURNAL_FILENAME).exists()
    assert not (archive_dir / CORE_TRACE_FILENAME).exists()
    assert worker._attached is False
    assert worker.core is None
    assert worker._journal is None
    assert worker._cid not in host._worker_by_cid
    assert finished_calls == []
    assert alerts == []


def test_proven_steam_final_zeros_sqlite_and_detaches(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proven fence after Steam-final zeros YES/NO, detaches, and does not keep_quiet."""
    _patch_archive_root(monkeypatch, tmp_path)
    worker, fake = build_attached_worker(tmp_path, request)
    store = WalletStateStore(tmp_path / "wallet.db")
    request.addfinalizer(store.close)
    store.set_position(YES_TOKEN, 12.0, 0.4)
    store.set_position(NO_TOKEN, 3.0, 0.6)
    fake.state = store
    worker._host.store = store
    worker._host.fence_result = True
    asyncio.run(worker._quiesce(True))
    assert store.position(YES_TOKEN).size == 0.0
    assert store.position(NO_TOKEN).size == 0.0
    assert worker._cid not in fake.metas
    assert worker._host.keep_quiet_calls == []
    assert worker._host.detached_cids == [worker._cid]
    assert (tmp_path / MATCH_ID / EXECUTION_CLEANUP_FILENAME).is_file()


def test_unproven_fence_keeps_quiet_and_does_not_zero(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unproven fence holds the market; sqlite sizes stay; no cleanup file."""
    _patch_archive_root(monkeypatch, tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr(match_worker, "notify_in_background", alerts.append)
    worker, fake = build_attached_worker(tmp_path, request)
    store = WalletStateStore(tmp_path / "wallet.db")
    request.addfinalizer(store.close)
    store.set_position(YES_TOKEN, 12.0, 0.4)
    fake.state = store
    worker._host.store = store
    worker._host.fence_result = False
    asyncio.run(worker._quiesce(True))
    assert store.position(YES_TOKEN).size == 12.0
    assert worker._cid in fake.metas
    assert worker._host.keep_quiet_calls == [worker._cid]
    assert worker._host.detached_cids == []
    assert worker._cid in worker._host._cells
    assert worker._cell.forced is True
    assert not (tmp_path / MATCH_ID / EXECUTION_CLEANUP_FILENAME).exists()
    assert len(alerts) == 1
    assert MATCH_ID in alerts[0]


def test_quiesce_is_idempotent(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second quiesce call is a no-op."""
    _patch_archive_root(monkeypatch, tmp_path)
    worker, _fake = build_attached_worker(tmp_path, request)
    asyncio.run(worker._quiesce(False))
    first = list(worker._host.detached_cids)
    asyncio.run(worker._quiesce(False))
    assert worker._host.detached_cids == first


def test_try_attach_attaches_before_outbox_drain(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending CONFIRMED outbox fill is journaled only after attach_market has run."""
    worker, fake = build_attached_worker(tmp_path, request)
    host = worker._host
    host.unregister_worker(worker._cid)
    fake.metas.pop(worker._cid, None)
    worker._attached = False
    worker._quoting = False
    order: list[str] = []
    noted_after_attach: list[bool] = []

    store = WalletStateStore(tmp_path / "wallet.db")
    request.addfinalizer(store.close)
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", "order-ours")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.CONFIRMED, 1.0),
        CONDITION_ID,
    )
    host.store = store
    fake.state = store

    def fake_attach(*_args: object, **_kwargs: object) -> None:
        order.append("attach")
        fake.metas[worker._cid] = make_meta()

    def fake_sidecar_meta(*_args: object, **_kwargs: object) -> SidecarMeta:
        return SidecarMeta(make_meta(), "0.01", make_binding())

    def first_start(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(
            binding=make_binding(),
            clip=SimpleNamespace(clip_usdc=5.0, reason="pinned"),
        )

    def scan_open_round(
        yes_token_id: str, no_token_id: str, min_order_size: float
    ) -> session_journal.JournalRound:
        del yes_token_id, no_token_id, min_order_size
        return session_journal.JournalRound(10.0, 0.0, None)

    monkeypatch.setattr(match_worker, "attach_market", fake_attach)
    monkeypatch.setattr(match_worker, "build_sidecar_meta", fake_sidecar_meta)
    host.drain_outbox = MethodType(WalletHost.drain_outbox, host)
    host._journal_fill = MethodType(WalletHost._journal_fill, host)

    def note_fill(fill: object) -> None:
        del fill
        order.append("drain")
        noted_after_attach.append(worker._cid in fake.metas)

    worker.note_fill = note_fill
    journal = SimpleNamespace(
        first_start=first_start,
        scan_open_round=scan_open_round,
    )
    asyncio.run(worker._try_attach(tmp_path, cast(Any, journal)))
    assert order == ["attach", "drain"]
    assert noted_after_attach == [True]
    assert worker._attached is True


@pytest.mark.parametrize(
    ("game", "feed_source", "profile_name"),
    [
        ("dota", FeedSource.GRID, "dota-map"),
        ("dota", FeedSource.ODDIN, "dota-oddin-map"),
        ("lol", FeedSource.GRID, "lol-map"),
    ],
)
def test_try_attach_asks_profile_name_for_discovered_game(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    game: str,
    feed_source: FeedSource,
    profile_name: str,
) -> None:
    """Attach asks Config for lol-map on LoL, dota-map on Steam/GRID, satellite on Oddin."""
    worker, fake = build_attached_worker(tmp_path, request)
    host = worker._host
    host.unregister_worker(worker._cid)
    fake.metas.pop(worker._cid, None)
    worker._attached = False
    worker._quoting = False
    worker._discovered = replace(worker._discovered, game=game)
    worker._feed.source = feed_source
    asked: list[str] = []

    class _RecordingCfg:
        def profile_for(self, entry: object) -> object:
            asked.append(cast(Any, entry).profile)
            return SimpleNamespace(base_size_usdc=65.0)

    monkeypatch.setattr(fake, "cfg", _RecordingCfg())

    def fake_attach(*_args: object, **_kwargs: object) -> None:
        fake.metas[worker._cid] = make_meta()

    def fake_sidecar_meta(*_args: object, **_kwargs: object) -> SidecarMeta:
        return SidecarMeta(make_meta(), "0.01", make_binding())

    def first_start(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(
            binding=make_binding(),
            clip=SimpleNamespace(clip_usdc=5.0, reason="pinned"),
        )

    def scan_open_round(
        yes_token_id: str, no_token_id: str, min_order_size: float
    ) -> session_journal.JournalRound:
        del yes_token_id, no_token_id, min_order_size
        return session_journal.JournalRound(10.0, 0.0, None)

    monkeypatch.setattr(match_worker, "attach_market", fake_attach)
    monkeypatch.setattr(match_worker, "build_sidecar_meta", fake_sidecar_meta)
    journal = SimpleNamespace(
        first_start=first_start,
        scan_open_round=scan_open_round,
    )
    asyncio.run(worker._try_attach(tmp_path, cast(Any, journal)))
    assert asked == [profile_name]


def _ready_books(worker: match_worker.MatchWorker) -> None:
    """Mark both token books ready for this attach generation."""
    readiness = worker._host.readiness
    readiness.attach_token(YES_TOKEN)
    readiness.attach_token(NO_TOKEN)
    readiness.note_snapshot(YES_TOKEN)
    readiness.note_snapshot(NO_TOKEN)


def _signal_records(tmp_path: Path) -> list[dict[str, object]]:
    """Session journal signal rows for this test's worker."""
    return [
        record
        for record in read_session_records(tmp_path / "journal")
        if record["kind"] == "signal"
    ]


def test_prior_is_fetched_once_per_map(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first state 4 tick starts one prices-history fetch; later ticks reuse it."""
    _patch_archive_root(monkeypatch, tmp_path)
    calls: list[int] = []

    def fake_fetch(
        yes_token_id: str, no_token_id: str, anchor_ts: int, *, yes_is_radiant: bool
    ) -> float:
        del yes_token_id, no_token_id, yes_is_radiant
        calls.append(anchor_ts)
        return 0.42

    monkeypatch.setattr(match_worker, "fetch_market_prior", fake_fetch)
    worker, _fake = build_attached_worker(
        tmp_path, request, model=FakeModelServer(radiant_fair=0.60), prior=None, prior_ready=False
    )
    _ready_books(worker)
    first = build_event(-60, phase=MatchPhase.PRE_HORN)
    expected_anchor = first.horn_unix_seconds - HORN_OFFSET_SECONDS

    async def scenario() -> None:
        await worker.handle_event(first)
        await worker.handle_event(build_event(-59, phase=MatchPhase.PRE_HORN))
        assert worker._prior_task is not None
        await worker._prior_task
        await worker.handle_event(build_event(-58, phase=MatchPhase.PRE_HORN))

    asyncio.run(scenario())
    assert calls == [expected_anchor]
    assert worker._prior == pytest.approx(0.42)
    signals = _signal_records(tmp_path)
    assert signals[0]["reason"] == SignalReason.MISSING_PRIOR
    assert signals[-1]["market_radiant_prior"] == pytest.approx(0.42)


def test_no_buy_before_game_second_minus_30(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRE_HORN seconds before -30 still yield model decisions."""
    _patch_archive_root(monkeypatch, tmp_path)
    worker, _fake = build_attached_worker(
        tmp_path, request, model=FakeModelServer(radiant_fair=0.60)
    )
    _ready_books(worker)

    async def scenario() -> None:
        for second in range(-60, -30):
            await worker.handle_event(build_event(second, phase=MatchPhase.PRE_HORN))

    asyncio.run(scenario())
    signals = _signal_records(tmp_path)
    assert all(record["reason"] == SignalReason.MODEL for record in signals)
    assert worker._cell.yes_fair is not None


def test_buy_opens_at_game_second_minus_30(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prehorn tick at -30 with clean gates publishes an entry."""
    _patch_archive_root(monkeypatch, tmp_path)
    worker, _fake = build_attached_worker(
        tmp_path, request, model=FakeModelServer(radiant_fair=0.60)
    )
    _ready_books(worker)

    async def scenario() -> None:
        for second in range(-60, -29):
            await worker.handle_event(build_event(second, phase=MatchPhase.PRE_HORN))

    asyncio.run(scenario())
    last = _signal_records(tmp_path)[-1]
    assert last["second"] == -30
    assert last["reason"] == SignalReason.MODEL
    assert last["market_radiant_prior"] == pytest.approx(0.5)
    assert worker._cell.yes_fair is not None


def test_missing_prior_blocks_buys_for_the_map(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed prior fetch never calls the model and never opens a BUY."""
    _patch_archive_root(monkeypatch, tmp_path)

    def failed_prior(
        yes_token_id: str, no_token_id: str, anchor_ts: int, *, yes_is_radiant: bool
    ) -> float | None:
        del yes_token_id, no_token_id, yes_is_radiant, anchor_ts
        return None

    monkeypatch.setattr(match_worker, "fetch_market_prior", failed_prior)
    model = FakeModelServer(radiant_fair=0.60)
    worker, _fake = build_attached_worker(
        tmp_path, request, model=model, prior=None, prior_ready=False
    )
    _ready_books(worker)

    async def scenario() -> None:
        for second in range(-60, -29):
            await worker.handle_event(build_event(second, phase=MatchPhase.PRE_HORN))
        assert worker._prior_task is not None
        await worker._prior_task
        await worker.handle_event(build_event(-28, phase=MatchPhase.PRE_HORN))

    asyncio.run(scenario())
    assert model.calls == 0
    assert worker._prior is None
    signals = _signal_records(tmp_path)
    assert all(record["reason"] == SignalReason.MISSING_PRIOR for record in signals)
    assert worker._cell.forced is True


def _fill_stamp(seconds_ago: float) -> str:
    """UTC fill stamp `seconds_ago` before now, matching `fill_applied_at_utc`."""
    filled_at = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return filled_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _write_yes_fill(
    worker: match_worker.MatchWorker, side: Side, position_after: float, seconds_ago: float
) -> None:
    """Write one YES fill onto the worker journal for resume restore tests."""
    journal = worker._journal
    assert journal is not None
    fill = Fill(YES_TOKEN, side, 0.40, 10.0, "k", 1.0, is_maker=True)
    journal.write_fill(fill, position_after, -4.0, 12, _fill_stamp(seconds_ago), "k")


def test_new_map_attach_keeps_buy_enabled(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    worker, _fake = build_attached_worker(tmp_path, request)
    assert worker._core is not None
    assert worker._core.state.sell_only is False
    assert worker._core.state.recovery_pending is False


def test_resume_mismatch_exits_held_inventory_without_model_fair(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """A journal/SQLite mismatch blocks buys and enables only the emergency SELL path."""
    worker, fake = build_attached_worker(
        tmp_path, request, model=FakeModelServer(radiant_fair=0.60)
    )
    fake.state.set_position(YES_TOKEN, 10.0, 0.4)
    _write_yes_fill(worker, Side.BUY, 5.0, 30.0)
    journal = worker._journal
    assert journal is not None
    worker._restore_open_round(journal)

    assert worker._core is not None
    assert worker._core.state.sell_only is True
    assert worker._cell.resume_exit is True


def test_restart_keeps_remaining_settle(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """The leftover EXIT_SETTLE_SECONDS after a BUY survives process restart."""
    worker, fake = build_attached_worker(tmp_path, request)
    store = _swap_wallet_store(worker, fake, tmp_path)
    store.set_position(YES_TOKEN, 10.0, 0.4)
    _write_yes_fill(worker, Side.BUY, 10.0, 3.0)
    journal = worker._journal
    assert journal is not None
    worker._restore_open_round(journal)
    assert worker._core is not None
    assert worker._core.state.recovery_pending is True
    assert worker.continue_recovery(rest_yes=10.0, rest_no=0.0) is True
    assert worker._core.state.last_buy_ns is not None


def test_restored_buy_checkpoint_reopens_buys_after_cancel(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A checkpoint holding a live BUY locks buys; the proven-empty proof reopens them."""
    worker, fake = build_attached_worker(tmp_path, request)
    store = _swap_wallet_store(worker, fake, tmp_path)
    key = CoreSessionKey(
        condition_id=CONDITION_ID,
        game="dota",
        yes_token=YES_TOKEN,
        no_token=NO_TOKEN,
        yes_is_radiant=True,
    )
    state = empty_state(
        limits=MarketLimits(
            min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05, radiant_token_index=0
        ),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        permissions=Permissions(
            halt=False, reduce_only=False, allow_buy=True, allow_sell=True, sell_unconfirmed=False
        ),
        budget=Budget(
            cash_usdc=0.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        clock=GameClock(now_ns=0, game_second=0, paused=False, game_ended=False),
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
    upsert_session(
        store._conn,
        session_id=CONDITION_ID,
        key=key,
        revision=1,
        recovery=True,
        recovery_generation=1,
        checkpoint=snapshot_checkpoint(state=state, now_ns=0, now_wall_s=1.0, sell_min_life_s=1.0),
        last_outbox_seq=0,
    )
    store._conn.commit()
    worker.open_core(level_usdc=65.0, min_order_size=5.0, tick_size=0.01, trace=None)
    core = worker._core
    assert core is not None
    journal = worker._journal
    assert journal is not None
    worker._restore_open_round(journal)
    assert core.state.recovery_pending is True
    assert core.state.sell_only is True
    core.bind_venue(core_id="c0", venue_id="v0")
    worker.note_cancel_result(["v0"], True)
    assert worker.continue_recovery(rest_yes=0.0, rest_no=0.0) is True
    assert core.state.sell_only is False


def _swap_wallet_store(
    worker: match_worker.MatchWorker,
    fake: Any,
    tmp_path: Path,
) -> WalletStateStore:
    """Point the worker at a real WalletStateStore so dust write-off can run."""
    fake.state.close()
    store = WalletStateStore(tmp_path / "wallet.db")
    fake.state = store
    worker._host.store = store
    return store


def test_sell_dust_zeros_sqlite_so_the_next_buy_is_only_the_fill(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A SELL leftover below min is written off; the next BUY is not fill+dust."""
    worker, fake = build_attached_worker(tmp_path, request)
    store = _swap_wallet_store(worker, fake, tmp_path)
    store.set_position(YES_TOKEN, 0.009068, 0.57)
    sell = Fill(YES_TOKEN, Side.SELL, 0.59, 17.27, "sell-dust", 1.0, is_maker=True)
    worker.note_fill(sell)
    assert store.position(YES_TOKEN).size == 0.0
    assert store.position(YES_TOKEN).avg_price == 0.0
    buy = Fill(YES_TOKEN, Side.BUY, 0.57, 41.279068, "buy-clip-2", 1.0, is_maker=True)
    assert store.apply_fill(buy) is True
    assert store.position(YES_TOKEN).size == pytest.approx(41.279068)


def test_sell_remainder_above_a_share_tick_stays_in_sqlite(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A 3.18 remainder is real inventory the chain holds; only sub-tick residue is zeroed."""
    worker, fake = build_attached_worker(tmp_path, request)
    store = _swap_wallet_store(worker, fake, tmp_path)
    store.set_position(YES_TOKEN, 3.18, 0.57)
    sell = Fill(YES_TOKEN, Side.SELL, 0.59, 4.91, "sell-partial", 1.0, is_maker=True)
    worker.note_fill(sell)
    assert store.position(YES_TOKEN).size == pytest.approx(3.18)
    assert store.position(YES_TOKEN).avg_price == pytest.approx(0.57)


def test_restore_keeps_a_real_remainder_without_locking_buys(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Attach with 3.18 held keeps sqlite true and still reads the round as closed."""
    worker, fake = build_attached_worker(tmp_path, request)
    store = _swap_wallet_store(worker, fake, tmp_path)
    store.set_position(YES_TOKEN, 3.18, 0.57)
    _write_yes_fill(worker, Side.BUY, 10.0, 300.0)
    _write_yes_fill(worker, Side.SELL, 3.18, 30.0)
    journal = worker._journal
    assert journal is not None
    worker._restore_open_round(journal)
    assert store.position(YES_TOKEN).size == pytest.approx(3.18)
    assert worker._core is not None
    assert worker._core.state.sell_only is True


def test_partial_buy_below_min_is_not_zeroed(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A BUY fill below min is a real holding; write-off is post-SELL residue only."""
    worker, fake = build_attached_worker(tmp_path, request)
    store = _swap_wallet_store(worker, fake, tmp_path)
    store.set_position(YES_TOKEN, 4.31, 0.57)
    buy = Fill(YES_TOKEN, Side.BUY, 0.57, 4.31, "buy-partial", 1.0, is_maker=True)
    worker.note_fill(buy)
    assert store.position(YES_TOKEN).size == pytest.approx(4.31)


def test_restore_zeros_sqlite_dust_before_quoting(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Attach leftover 0.009 is written off and does not lock buys or restore settle."""
    worker, fake = build_attached_worker(tmp_path, request)
    store = _swap_wallet_store(worker, fake, tmp_path)
    store.set_position(YES_TOKEN, 0.009, 0.57)
    _write_yes_fill(worker, Side.BUY, 10.0, 300.0)
    _write_yes_fill(worker, Side.SELL, 0.009, 30.0)
    journal = worker._journal
    assert journal is not None
    worker._restore_open_round(journal)
    assert store.position(YES_TOKEN).size == 0.0
    assert worker._core is not None
    assert worker._core.state.sell_only is False


def test_place_and_cancel_append_quote_records(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Successful gateway place/cancel write kind=quote with the model gate and full token."""
    worker, fake = build_attached_worker(tmp_path, request)
    worker._last_second = 12
    worker._cell.publish(0.45)
    WalletHost._wrap_quote_journal(worker._host)
    meta = fake.metas[CONDITION_ID]
    quote = Quote(YES_TOKEN, Side.BUY, 0.40, 5.0)

    async def run() -> None:
        placed = await fake.gateway.place([quote], meta)
        assert placed
        for order in placed:
            fake.state.upsert_order(order)
        ok = await fake.gateway.cancel([placed[0].order_id])
        assert ok is True

    asyncio.run(run())
    records = read_session_records(tmp_path / "journal")
    quotes = [record for record in records if record["kind"] == "quote"]
    assert len(quotes) == 2
    placed_record, canceled_record = quotes
    assert placed_record["fv_source"] == "model"
    assert placed_record["decision"] == "normal"
    assert placed_record["second"] == 12
    assert placed_record["placed"] == [
        {"token_id": YES_TOKEN, "side": "BUY", "price": 0.40, "size": 5.0}
    ]
    assert placed_record["canceled"] == []
    assert canceled_record["placed"] == []
    assert canceled_record["canceled"]
    assert canceled_record["fv_source"] == "model"


def test_note_fill_writes_fill_record_and_logs(
    tmp_path: Path, request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture
) -> None:
    """note_fill still writes kind=fill and emits one docker INFO line."""
    worker, fake = build_attached_worker(tmp_path, request)
    store = _swap_wallet_store(worker, fake, tmp_path)
    store.set_position(YES_TOKEN, 10.0, 0.40)
    worker._last_second = 12
    fill = Fill(YES_TOKEN, Side.BUY, 0.40, 10.0, "clob-1:order-1", 1.0, is_maker=True)
    with caplog.at_level(logging.INFO, logger="trader.match_worker"):
        worker.note_fill(fill)
    records = read_session_records(tmp_path / "journal")
    fills = [record for record in records if record["kind"] == "fill"]
    assert len(fills) == 1
    assert fills[0]["side"] == "BUY"
    assert fills[0]["price"] == 0.40
    assert fills[0]["size"] == 10.0
    assert fills[0]["ts_utc"] == fill_ts_utc(1.0)
    logged = [
        record.getMessage() for record in caplog.records if record.name == "trader.match_worker"
    ]
    assert any("fill match=" in line for line in logged)
    line = next(line for line in logged if line.startswith("fill match="))
    assert MATCH_ID in line
    assert "second=12" in line
    assert "side=BUY" in line
    assert "token=TOKEN0" in line


def test_wrapped_sell_cancel_does_not_wait_min_life(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Halt, quarantine and shutdown cancel at once; min-life never delays a cancel."""
    worker, fake = build_attached_worker(tmp_path, request)
    WalletHost._wrap_quote_journal(worker._host)
    order = OpenOrder("oid-s", YES_TOKEN, Side.SELL, 0.60, 10.0)
    fake.state.upsert_order(order)
    started = time.monotonic()
    asyncio.run(fake.gateway.cancel(["oid-s"]))
    assert time.monotonic() - started < 0.2


def test_feed_timeout_keeps_fair_and_sell_until_exit_timeout(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Entry timeout cancels BUYs only; exit timeout then drops fair. Next tick quotes again."""
    worker, fake = build_attached_worker(
        tmp_path,
        request,
        model=FakeModelServer(radiant_fair=0.60),
        feed_timeout_seconds=0.05,
        exit_timeout_seconds=0.12,
    )
    _ready_books(worker)
    meta = make_meta()
    placed = asyncio.run(
        fake.gateway.place(
            [
                Quote(YES_TOKEN, Side.BUY, 0.45, 5.0),
                Quote(NO_TOKEN, Side.SELL, 0.55, 5.0),
            ],
            meta,
        )
    )
    for order in placed:
        fake.state.upsert_order(order)

    async def scenario() -> None:
        await worker.handle_event(build_event(100, phase=MatchPhase.IN_PROGRESS))
        assert worker._cell.forced is False
        worker._watchdog.arm()
        await asyncio.sleep(0.08)
        await asyncio.sleep(0)
        assert worker._cell.forced is False
        assert worker._cell.yes_fair is not None
        remaining = list(fake.state.orders.values())
        assert remaining
        assert all(order.side is Side.SELL for order in remaining)
        await asyncio.sleep(0.08)
        await asyncio.sleep(0)
        assert worker._cell.forced is True
        await worker.handle_event(build_event(101, phase=MatchPhase.IN_PROGRESS))
        await worker.handle_event(build_event(102, phase=MatchPhase.IN_PROGRESS))
        assert worker._cell.forced is False
        assert worker._quoting is True
        assert worker._attached is True
        assert worker._quiesced is False

    asyncio.run(scenario())


def _seed_pm_mid(worker: match_worker.MatchWorker, yes_mid: float) -> None:
    """Set YES/NO books so radiant_mid is yes_mid when yes_is_radiant."""
    half = 0.01
    yes_bid = round(yes_mid - half, 2)
    yes_ask = round(yes_mid + half, 2)
    no_mid = round(1.0 - yes_mid, 2)
    no_bid = round(no_mid - half, 2)
    no_ask = round(no_mid + half, 2)
    fake = worker._host.engine
    yes_book = fake.md.book(YES_TOKEN)
    no_book = fake.md.book(NO_TOKEN)
    assert yes_book is not None and no_book is not None
    yes_book.apply_snapshot([(yes_bid, 10.0)], [(yes_ask, 10.0)], 100.0, "hash")
    no_book.apply_snapshot([(no_bid, 10.0)], [(no_ask, 10.0)], 100.0, "hash")
    _ready_books(worker)


def test_tick_quotes_pm_from_the_book_mid(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One tick: PM predict_fair on the book mid, one journal signal, one wake."""
    _patch_archive_root(monkeypatch, tmp_path)
    model = FakeModelServer(radiant_fair=0.55)
    worker, fake = build_attached_worker(tmp_path, request, model=model, prior=0.48)
    _seed_pm_mid(worker, 0.40)
    asyncio.run(worker.handle_event(build_event(10, phase=MatchPhase.IN_PROGRESS)))
    assert model.fair_args == [(0.40, 0.48)]
    assert fake.wake_calls >= 1
    signals = _signal_records(tmp_path)
    assert signals
    assert signals[-1]["venue"] == "polymarket"
    assert signals[-1]["market_p_radiant"] == pytest.approx(0.40)


@pytest.mark.parametrize("unusable", ["fresh", "aged", "history_gap"])
def test_live_board_uses_received_snapshot_without_rearming_watchdog(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, unusable: str
) -> None:
    model = FakeModelServer(radiant_fair=0.55)
    worker, _fake = build_attached_worker(tmp_path, request, model=model, feed_timeout_seconds=16.0)
    _ready_books(worker)
    now = [time.time_ns()]
    monkeypatch.setattr(match_worker, "core_now_ns", lambda: now[0])

    async def scenario() -> None:
        event = build_event(100, radiant_nw_adv=100)
        worker._record_history(event.snapshot)
        await worker._on_event(event)
        assert model.calls == 1
        source = worker._board_source
        assert source is not None
        source_ns = source.received_ns
        generation = worker._watchdog._generation
        if unusable == "aged":
            now[0] += 20_000_000_000
        elif unusable == "history_gap":
            invalid = build_event(200, radiant_nw_adv=200)
            worker._record_history(invalid.snapshot)
            await worker._on_event(invalid)
            assert worker._board_source is None
        else:
            now[0] += 1_000_000_000
        tick = KillTick(event.received_at_utc, SideWait(2, 10.0), SideWait(3, 0.0))
        worker._on_kill_tick(tick)
        now[0] += 110_000_000
        worker._quote_board()
        assert worker._watchdog._generation == generation
        if unusable == "fresh":
            assert model.calls == 2
            assert model.board_args[-1].deaths_radiant == 2
            assert model.board_args[-1].pending_deaths_radiant == 1
            assert model.board_args[-1].board_death_age_radiant_s == pytest.approx(0.11)
            assert worker._board_source is not None
            assert worker._board_source.received_ns == source_ns
            core = worker.core
            assert core is not None
            updates = [item for item in core._pending if isinstance(item, SignalUpdate)]
            signal = updates[-1].signal
            assert signal is not None
            assert signal.received_ns == now[0]
            assert signal.source_received_ns == source_ns
            assert signal.deaths_radiant == 1
        else:
            assert model.calls == 1
        worker._stop_feed_timers()

    asyncio.run(scenario())
