"""WalletHost helpers: flock, boot scan, cancel isolation, retry, no live-report."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false, reportUnknownLambdaType=false, reportAttributeAccessIssue=false, reportUnknownArgumentType=false

import asyncio
import importlib.util
import json
import logging
import shutil
import sqlite3
import subprocess
import tomllib
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, cast

import pytest
from polymaker.config import Config, MarketEntry, RiskConfig
from polymaker.domain import Fill, OpenOrder, Side, TradeState
from polymaker.risk.manager import RiskManager
from polymaker.state.tracker import TradeEvent
from test_trader_unsettled_buy_activation import (
    BuyRig,
    _budget,
    add_order,
    open_buy_rig,
    run_cancel,
)
from trader_discovery_fixtures import sidecar_body, write_sidecar
from trader_session_fixtures import (
    CONDITION_ID,
    MATCH_ID,
    NO_TOKEN,
    YES_TOKEN,
    FakeModelServer,
    build_discovered,
    make_meta,
)

from shared.constants.paths import BASE_DIR
from shared.constants.strategy import GRID_FEED_STALE_SECONDS
from shared.utils.hashing import sha256_file
from shared.utils.json_io import write_json
from shared.utils.top_players import ZERO_TOP
from trader.archive_paths import write_execution_cleanup
from trader.archived_markets import ArchivedMarketIndex
from trader.bindings import DiscoveredMatch, ModelReference, TeamSides
from trader.core_persistence import get_unsettled_buy, unsettled_buy_notional
from trader.engine_seams import (
    CidOrderErrors,
    ShutdownLatch,
    fence_no_orders,
    pin_engine_identity,
    record_engine_markout,
)
from trader.feed_selection import CorruptFeedPin, select_feed
from trader.fill_parsing import fill_key
from trader.game_profile import GAME_PROFILES, GameProfile, strategy_catalogs
from trader.grid_feed import GridOrientationError
from trader.grid_live_feed import GridLiveFeed
from trader.live_feed import FeedEvent, FeedSource, GameSnapshot, MatchPhase
from trader.match_meta import match_has_final, write_match_start
from trader.match_worker import MatchWorker
from trader.notify import SESSION_STARTED_PREFIX
from trader.oddin_feed import ODDIN_FEED_STALE_SECONDS
from trader.oddin_live_feed import OddinLiveFeed
from trader.oddin_types import OddinFeedError
from trader.paths import EXECUTION_CLEANUP_FILENAME, MATCH_META_FILENAME
from trader.process_lock import FileLockHeld, acquire_file_lock
from trader.session_config import materialize_wallet_config_dir, read_template
from trader.trading_mode import ExecutionMode
from trader.wallet_host import (
    LIVE_TITLE_BLACKLIST,
    MAX_CRASH_RESTARTS,
    RESTART_BACKOFF_SECONDS,
    WalletHost,
    _dead_feed_id,
    _ManagedMatch,
    _match_bindings,
    cancel_token_orders,
    fence_until,
    list_boot_scan_targets,
    lock_or_exit,
    select_title_blacklist,
    wallet_db_path,
)
from trader.wallet_store import WalletFillProcessor, WalletStateStore


def _keep_map(_oddin_match_id: str, _map_number: int) -> bool:
    return False


def _write_final_meta(
    archive_dir: Path, match_id: str, condition_id: str, yes: str, no: str
) -> None:
    """Write a match.json with a non-null final and the market token ids."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": 3,
        "match_id": match_id,
        "final": {"duration_seconds": 100, "winner": "radiant"},
        "market": {
            "condition_id": condition_id,
            "yes_token_id": yes,
            "no_token_id": no,
        },
    }
    (archive_dir / "match.json").write_text(json.dumps(document), encoding="utf-8")


def test_select_title_blacklist_scopes_to_live_dota() -> None:
    """The pinned league blacklist applies to live Dota only, never to paper or LoL."""
    assert select_title_blacklist("live", "dota") == LIVE_TITLE_BLACKLIST
    assert select_title_blacklist("paper", "dota") == ()
    assert select_title_blacklist("live", "lol") == ()
    assert select_title_blacklist("paper", "lol") == ()


def test_second_flock_on_wallet_db_raises(tmp_path: Path) -> None:
    """Two exclusive flocks on the same wallet db cannot overlap."""
    path = tmp_path / "paper.db"
    held = acquire_file_lock(path)
    try:
        with pytest.raises(FileLockHeld):
            acquire_file_lock(path)
    finally:
        held.close()
    released = acquire_file_lock(path)
    released.close()


def test_lock_or_exit_when_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second process must exit 1 instead of opening another Engine."""
    monkeypatch.setattr("trader.wallet_host.TRADER_WALLET_DIR", tmp_path)
    held = acquire_file_lock(wallet_db_path("paper"))
    try:
        with pytest.raises(SystemExit) as exc:
            lock_or_exit("paper")
        assert exc.value.code == 1
    finally:
        held.close()


def test_boot_scan_lists_final_without_cleanup_and_skips_wallet_dir(tmp_path: Path) -> None:
    """Boot scan picks finalized matches that still lack execution_cleanup.json."""
    match_a = "8944931337"
    match_b = "8944931338"
    _write_final_meta(tmp_path / match_a, match_a, "0xA", "YES-A", "NO-A")
    _write_final_meta(tmp_path / match_b, match_b, "0xB", "YES-B", "NO-B")
    write_execution_cleanup(tmp_path / match_b, match_b, "0xB")
    (tmp_path / "wallet").mkdir()
    _write_final_meta(tmp_path / "wallet", "wallet", "0xW", "YES-W", "NO-W")
    targets = list_boot_scan_targets(tmp_path)
    assert len(targets) == 1
    assert targets[0].match_id == match_a
    assert targets[0].yes_token_id == "YES-A"
    assert targets[0].no_token_id == "NO-A"
    assert match_has_final(tmp_path, match_a) is True
    assert match_has_final(tmp_path, match_b) is True


@pytest.mark.parametrize("schema_version", [1, 2])
def test_boot_scan_skips_foreign_schema(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, schema_version: int
) -> None:
    """Leftover v1/v2 match.json is not a boot-scan target and logs once."""
    match_id = "8944931337"
    archive = tmp_path / match_id
    archive.mkdir()
    document = {
        "schema_version": schema_version,
        "match_id": match_id,
        "final": {"duration_seconds": 100, "winner": "radiant"},
        "market": {
            "condition_id": "0xA",
            "yes_token_id": "YES-A",
            "no_token_id": "NO-A",
        },
    }
    (archive / "match.json").write_text(json.dumps(document), encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="trader.match_meta"):
        assert match_has_final(tmp_path, match_id) is False
        assert list_boot_scan_targets(tmp_path) == []
    assert any(
        f"foreign schema match={match_id} schema={schema_version}" in record.getMessage()
        for record in caplog.records
    )


def test_boot_scan_accepts_schema_4(tmp_path: Path) -> None:
    """A schema-4 finalized file is still a boot-scan target."""
    match_id = "8944931337"
    archive = tmp_path / match_id
    archive.mkdir()
    document = {
        "schema_version": 4,
        "match_id": match_id,
        "final": {"duration_seconds": 100, "winner": "radiant"},
        "market": {
            "condition_id": "0xA",
            "yes_token_id": "YES-A",
            "no_token_id": "NO-A",
        },
    }
    (archive / "match.json").write_text(json.dumps(document), encoding="utf-8")
    targets = list_boot_scan_targets(tmp_path)
    assert len(targets) == 1
    assert targets[0].match_id == match_id
    assert match_has_final(tmp_path, match_id) is True


def test_boot_scan_accepts_schema_5(tmp_path: Path) -> None:
    """A schema-5 finalized file is a boot-scan target even without game."""
    match_id = "8944931337"
    archive = tmp_path / match_id
    archive.mkdir()
    document = {
        "schema_version": 5,
        "match_id": match_id,
        "final": {"duration_seconds": 100, "winner": "radiant"},
        "market": {
            "condition_id": "0xA",
            "yes_token_id": "YES-A",
            "no_token_id": "NO-A",
        },
    }
    (archive / "match.json").write_text(json.dumps(document), encoding="utf-8")
    targets = list_boot_scan_targets(tmp_path)
    assert len(targets) == 1
    assert targets[0].match_id == match_id
    assert match_has_final(tmp_path, match_id) is True


def test_cancel_market_a_does_not_cancel_market_b() -> None:
    """cancel_asset is called only for the tokens of the requested market."""

    class RecordingGateway:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def cancel_asset(self, asset_id: str) -> bool:
            self.cancelled.append(asset_id)
            return True

    gateway = RecordingGateway()
    asyncio.run(cancel_token_orders(cast(Any, gateway), {"YES-A", "NO-A"}))
    assert set(gateway.cancelled) == {"YES-A", "NO-A"}
    asyncio.run(cancel_token_orders(cast(Any, gateway), {"YES-B", "NO-B"}))
    assert set(gateway.cancelled[2:]) == {"YES-B", "NO-B"}
    assert "YES-B" not in gateway.cancelled[:2]
    assert "NO-B" not in gateway.cancelled[:2]


def test_fence_timeout_with_final_does_not_write_cleanup(tmp_path: Path) -> None:
    """Unproven orders after Steam final must not write execution_cleanup.json."""
    match_id = "8944931337"
    archive = tmp_path / match_id
    _write_final_meta(archive, match_id, "0xCOND", "YES", "NO")

    async def still_open() -> list[OpenOrder]:
        return [OpenOrder("live", "YES", Side.BUY, 0.4, 5.0)]

    engine = SimpleNamespace(gateway=SimpleNamespace(open_orders=still_open))
    proven = asyncio.run(fence_until(cast(Any, engine), {"YES", "NO"}, 0.2))
    assert proven is False
    assert not (archive / EXECUTION_CLEANUP_FILENAME).exists()
    assert match_has_final(tmp_path, match_id) is True


def test_proven_fence_writes_cleanup(tmp_path: Path) -> None:
    """Cleanup is written only after open_orders shows none of this market's tokens."""

    async def empty() -> list[OpenOrder]:
        return []

    engine = SimpleNamespace(gateway=SimpleNamespace(open_orders=empty))
    proven = asyncio.run(fence_until(cast(Any, engine), {"YES", "NO"}, 0.2))
    assert proven is True
    archive = tmp_path / "8944931337"
    write_execution_cleanup(archive, "8944931337", "0xCOND")
    assert (archive / EXECUTION_CLEANUP_FILENAME).is_file()


def test_fence_uses_open_orders_never_rest_positions() -> None:
    """Fence proves from open_orders; sqlite leftover drop is MatchWorker's job."""

    async def empty_orders() -> list[OpenOrder]:
        return []

    async def boom_positions() -> dict[str, tuple[float, float]]:
        raise AssertionError("fence must not call REST positions")

    engine = SimpleNamespace(
        gateway=SimpleNamespace(open_orders=empty_orders, positions=boom_positions)
    )
    proven = asyncio.run(fence_no_orders(cast(Any, engine), {"YES"}))
    assert proven is True


def test_two_cids_keep_separate_token_sets() -> None:
    """Two markets cancel disjoint token sets."""

    class RecordingGateway:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def cancel_asset(self, asset_id: str) -> bool:
            self.cancelled.append(asset_id)
            return True

    gateway = RecordingGateway()
    asyncio.run(cancel_token_orders(cast(Any, gateway), {"Y0", "N0"}))
    asyncio.run(cancel_token_orders(cast(Any, gateway), {"Y1", "N1"}))
    assert set(gateway.cancelled) == {"Y0", "N0", "Y1", "N1"}


def test_make_live_report_is_gone() -> None:
    """`make live-report` is not a target; the report module is gone."""
    if shutil.which("make") is None:
        pytest.skip("make is not available")
    result = subprocess.run(
        ["make", "-n", "live-report"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert importlib.util.find_spec("trader.report") is None


def test_wallet_host_does_not_patch_fair_value() -> None:
    """WalletHost quotes from cells; compute_fair_value stays the fork's."""
    source = (BASE_DIR / "src" / "trader" / "wallet_host.py").read_text(encoding="utf-8")
    assert "compute_fair_value" not in source


def test_materialize_wallet_config_dir_is_empty_markets(tmp_path: Path) -> None:
    """Process config has one db, one journal_dir, empty markets, and every assigned profile."""
    db_path = tmp_path / "wallet" / "paper.db"
    journal_dir = tmp_path / "wallet" / "engine_journal"
    template = read_template((GAME_PROFILES["dota"], GAME_PROFILES["lol"]))
    materialized = materialize_wallet_config_dir(db_path, journal_dir, template)
    try:
        generated = tomllib.loads(
            (materialized.config_dir / "config.toml").read_text(encoding="utf-8")
        )
        markets = tomllib.loads(
            (materialized.config_dir / "markets.toml").read_text(encoding="utf-8")
        )
        strategy = tomllib.loads(
            (materialized.config_dir / "strategy.toml").read_text(encoding="utf-8")
        )
        assert generated["paths"]["db"] == str(db_path)
        assert generated["paths"]["journal_dir"] == str(journal_dir)
        assert "kalshi" not in generated
        assert markets.get("markets", []) == []
        assert set(strategy["profiles"]) == {
            "dota-map",
            "dota-oddin-map",
            "lol-map",
        }
        assert strategy["profiles"] == template.profiles
        cfg = Config.load(materialized.config_dir, load_env=False)
        cfg.profile_for(MarketEntry(condition_id=CONDITION_ID, profile="lol-map"))
        cfg.profile_for(MarketEntry(condition_id=CONDITION_ID, profile="dota-oddin-map"))
    finally:
        materialized.cleanup()


def test_identity_mismatch_refuses_before_engine_start(tmp_path: Path) -> None:
    """A foreign sqlite must fail before start or cancel_all."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.pin_static(2, 137)
    store.pin_funder("0xOLD")
    started: list[str] = []
    cancelled: list[str] = []

    async def start() -> None:
        started.append("start")

    async def cancel_all() -> None:
        cancelled.append("cancel_all")

    engine = SimpleNamespace(
        state=store,
        gateway=SimpleNamespace(funder="0xNEW", cancel_all=cancel_all),
        cfg=SimpleNamespace(wallet=SimpleNamespace(signature_type=2, chain_id=137)),
        start=start,
    )
    with pytest.raises(RuntimeError, match="wallet identity"):
        pin_engine_identity(cast(Any, engine))
    assert started == []
    assert cancelled == []
    assert store.current_day() is None
    store.close()


def test_start_error_still_runs_teardown() -> None:
    """A raise in engine.start must still cancel_all and shut the Engine down."""
    events: list[str] = []

    class FakeGateway:
        async def cancel_all(self) -> None:
            events.append("cancel_all")

    class FakeEngine:
        def __init__(self) -> None:
            self.gateway = FakeGateway()
            self.metas: dict[str, object] = {}

        async def start(self) -> None:
            events.append("start")
            raise RuntimeError("start failed")

        async def shutdown(self) -> None:
            events.append("shutdown")

    host = object.__new__(WalletHost)
    host.engine = cast(Any, FakeEngine())
    host._games = ()
    host._latch = ShutdownLatch()
    host._matches_by_cid = {}
    host._closed = False

    def close() -> None:
        events.append("close")
        host._closed = True

    host.close = close  # type: ignore[method-assign]

    async def run() -> None:
        with pytest.raises(RuntimeError, match="start failed"):
            await host.run()

    asyncio.run(run())
    assert events == ["start", "cancel_all", "shutdown", "close"]


def _bare_host(engine: object) -> WalletHost:
    """WalletHost with maps only; skips Engine seam install."""
    host = object.__new__(WalletHost)
    host.engine = cast(Any, engine)
    host.store = getattr(engine, "state", None)
    host._mode = "paper"
    host._models = {
        catalog.profile_name: FakeModelServer()
        for catalog in strategy_catalogs(GAME_PROFILES["dota"])
    }
    host.steam_client = SimpleNamespace()
    host._worker_by_cid = {}
    host._worker_by_token = {}
    host._archive_by_token = OrderedDict()
    host._tokens_by_cid = {}
    host._cells = {}
    host.cores = {}
    host._quiet = set()
    host._matches_by_cid = {}
    host._map_ended = _keep_map
    host._games = (GAME_PROFILES["dota"],)
    host._archive_by_game = {"dota": Path("/tmp")}
    host.archive_root = Path("/tmp")
    return host


def _record_for(discovered: object) -> _ManagedMatch:
    """One supervision record with no live task."""
    handoff = cast(Any, discovered)
    return _ManagedMatch(
        match_id=handoff.match_id,
        handoff=handoff,
        identity=_match_bindings(handoff),
        task=None,
        attempts=0,
        next_eligible=0.0,
        completed=False,
        exhausted=False,
        announced=False,
    )


def test_detach_resets_sticky_order_error_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detach zeros this cid and the fork; a sibling cid stays over the cap."""
    risk = RiskManager(RiskConfig(), cast(Any, SimpleNamespace()))
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
    host = _bare_host(SimpleNamespace(risk=risk, metas={}))
    host.readiness = SimpleNamespace()

    async def fake_stop(engine: object, cid: str) -> None:
        del engine, cid

    def fake_detach(engine: object, cid: str, readiness: object) -> None:
        del engine, cid, readiness

    monkeypatch.setattr("trader.wallet_host.stop_quoter", fake_stop)
    monkeypatch.setattr("trader.wallet_host.detach_market", fake_detach)

    async def run() -> None:
        await host.detach(cid_a)

    assert tracker.trip_reason(cid_a) is not None
    assert tracker.trip_reason(cid_b) is not None
    asyncio.run(run())
    assert risk.error_rate == 0.0
    assert tracker.trip_reason(cid_a) is None
    assert cid_a not in tracker._tripped
    assert tracker.trip_reason(cid_b) is not None


def test_fence_unproven_when_matched_outbox_pending(tmp_path: Path) -> None:
    """Open orders empty is not proven while a MATCHED outbox row is still unacked."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("t1", "o1")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.MATCHED, 1.0),
        CONDITION_ID,
    )

    async def empty() -> list[OpenOrder]:
        return []

    engine = SimpleNamespace(gateway=SimpleNamespace(open_orders=empty), state=store)
    proven = asyncio.run(fence_until(cast(Any, engine), {YES_TOKEN, NO_TOKEN}, 0.2))
    assert proven is False
    store.close()


def test_second_attach_of_live_cid_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cid already in engine.metas must not spawn a second worker."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    meta = make_meta()
    engine = SimpleNamespace(metas={meta.condition_id: meta})
    host = _bare_host(engine)
    host.reconcile((build_discovered(),))
    record = host._matches_by_cid[CONDITION_ID]
    assert record.task is None
    assert meta.condition_id in engine.metas


def test_cid_in_use_ignores_the_managed_task_before_attach() -> None:
    """A running match task is not an Engine owner; the first attach must proceed."""
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(build_discovered())
    record.task = cast(Any, SimpleNamespace(done=lambda: False))
    host._matches_by_cid[CONDITION_ID] = record
    assert host.cid_in_use(CONDITION_ID) is False
    host._worker_by_cid[CONDITION_ID] = cast(Any, object())
    assert host.cid_in_use(CONDITION_ID) is True


def test_remake_does_not_cross_terminate_live_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A new Steam match_id on a pinned CID is rejected; the live worker stays."""

    async def run() -> None:
        monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
        alerts: list[str] = []
        monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
        live = build_discovered()
        meta = make_meta()
        engine = SimpleNamespace(metas={meta.condition_id: meta})
        host = _bare_host(engine)
        worker_a = SimpleNamespace(_discovered=live, _yes_is_radiant=live.market.yes_is_radiant)
        host._worker_by_cid[meta.condition_id] = cast(Any, worker_a)
        record_a = _record_for(live)
        record_a.task = asyncio.create_task(asyncio.sleep(3600), name="match-a")
        record_a.announced = True
        host._matches_by_cid[CONDITION_ID] = record_a
        remake = replace(live, match_id="8944931338")
        with caplog.at_level(logging.WARNING, logger="trader.wallet_host"):
            host.reconcile((remake,))
        assert list(host._matches_by_cid) == [CONDITION_ID]
        assert host._matches_by_cid[CONDITION_ID] is record_a
        assert record_a.match_id == live.match_id
        assert host._worker_by_cid[meta.condition_id] is worker_a
        assert record_a.task is not None
        assert not record_a.task.done()
        assert alerts == []
        assert any("reason=pinned_binding" in rec.getMessage() for rec in caplog.records)
        record_a.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await record_a.task

    asyncio.run(run())


def test_pinned_worker_orientation_matches_later_grid_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """After the worker locks GRID sides, rediscovery with those sides is not a rebind."""

    async def run() -> None:
        monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
        launch = build_discovered(yes_is_radiant=True)
        meta = make_meta()
        engine = SimpleNamespace(metas={meta.condition_id: meta})
        host = _bare_host(engine)
        oriented = replace(
            launch,
            sides=TeamSides(radiant="Team Secret", dire="Aurora"),
            market=replace(launch.market, yes_is_radiant=False),
        )
        worker = SimpleNamespace(_discovered=oriented, _yes_is_radiant=False)
        host._worker_by_cid[meta.condition_id] = cast(Any, worker)
        record = _record_for(launch)
        record.task = asyncio.create_task(asyncio.sleep(3600), name="match-a")
        record.announced = True
        host._matches_by_cid[CONDITION_ID] = record
        with caplog.at_level(logging.WARNING, logger="trader.wallet_host"):
            host.reconcile((oriented,))
        assert record.identity.yes_is_radiant is False
        assert record.identity.radiant_name == "Team Secret"
        assert record.handoff.market.yes_is_radiant is False
        assert not any("reason=pinned_binding" in rec.getMessage() for rec in caplog.records)
        record.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await record.task

    asyncio.run(run())


def test_stale_discovery_sides_do_not_pin_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """GRID-flipped worker vs leftover Steam sides is not a pinned_binding skip."""

    async def run() -> None:
        monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
        launch = build_discovered(yes_is_radiant=True)
        meta = make_meta()
        engine = SimpleNamespace(metas={meta.condition_id: meta})
        host = _bare_host(engine)
        oriented = replace(
            launch,
            sides=TeamSides(radiant="Team Secret", dire="Aurora"),
            market=replace(launch.market, yes_is_radiant=False),
        )
        worker = SimpleNamespace(_discovered=oriented, _yes_is_radiant=False)
        host._worker_by_cid[meta.condition_id] = cast(Any, worker)
        record = _record_for(launch)
        record.task = asyncio.create_task(asyncio.sleep(3600), name="match-a")
        record.announced = True
        record.feed_source = FeedSource.GRID
        host._matches_by_cid[CONDITION_ID] = record
        with caplog.at_level(logging.WARNING, logger="trader.wallet_host"):
            host.reconcile((launch,))
        assert record.identity.yes_is_radiant is False
        assert record.task is not None
        assert not record.task.done()
        assert not any("reason=pinned_binding" in rec.getMessage() for rec in caplog.records)
        record.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await record.task

    asyncio.run(run())


def test_remake_of_finalized_match_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A pinned worker whose archive already has final does not warn on rediscovery."""

    async def run() -> None:
        monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
        _write_final_meta(tmp_path / MATCH_ID, MATCH_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN)
        live = build_discovered()
        meta = make_meta()
        engine = SimpleNamespace(metas={meta.condition_id: meta})
        host = _bare_host(engine)
        worker_a = SimpleNamespace(_discovered=live, _yes_is_radiant=live.market.yes_is_radiant)
        host._worker_by_cid[meta.condition_id] = cast(Any, worker_a)
        record_a = _record_for(live)
        record_a.task = asyncio.create_task(asyncio.sleep(3600), name="match-a")
        record_a.announced = True
        host._matches_by_cid[CONDITION_ID] = record_a
        remake = replace(live, match_id="8944931338")
        with caplog.at_level(logging.WARNING, logger="trader.wallet_host"):
            host.reconcile((remake,))
        assert record_a.match_id == live.match_id
        assert host._worker_by_cid[meta.condition_id] is worker_a
        assert not any("reason=pinned_binding" in rec.getMessage() for rec in caplog.records)
        record_a.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await record_a.task

    asyncio.run(run())


def test_quiet_cid_missing_from_discovery_detaches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unproven leftover whose sidecar left discovery is detached."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    risk = RiskManager(RiskConfig(), cast(Any, SimpleNamespace()))
    host = _bare_host(SimpleNamespace(risk=risk, metas={CONDITION_ID: make_meta()}))
    host.readiness = SimpleNamespace()
    host._quiet.add(CONDITION_ID)
    detached: list[str] = []

    async def fake_stop(engine: object, cid: str) -> None:
        del engine
        detached.append(cid)

    def fake_detach(engine: object, cid: str, readiness: object) -> None:
        del engine, cid, readiness

    monkeypatch.setattr("trader.wallet_host.stop_quoter", fake_stop)
    monkeypatch.setattr("trader.wallet_host.detach_market", fake_detach)
    asyncio.run(host._detach_quiet_missing(set()))
    assert detached == [CONDITION_ID]
    assert CONDITION_ID not in host._quiet


def test_quiet_cid_stays_when_still_discovered_or_task_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quiet CID still in discovery, or with a live task, is not detached."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    risk = RiskManager(RiskConfig(), cast(Any, SimpleNamespace()))
    host = _bare_host(SimpleNamespace(risk=risk, metas={CONDITION_ID: make_meta()}))
    host.readiness = SimpleNamespace()
    detached: list[str] = []

    async def fake_stop(engine: object, cid: str) -> None:
        del engine
        detached.append(cid)

    def fake_detach(engine: object, cid: str, readiness: object) -> None:
        del engine, cid, readiness

    monkeypatch.setattr("trader.wallet_host.stop_quoter", fake_stop)
    monkeypatch.setattr("trader.wallet_host.detach_market", fake_detach)

    async def run() -> None:
        host._quiet.add(CONDITION_ID)
        await host._detach_quiet_missing({CONDITION_ID})
        assert detached == []
        assert CONDITION_ID in host._quiet
        record = _record_for(build_discovered())
        record.task = asyncio.create_task(asyncio.sleep(3600), name="quiet-live")
        host._matches_by_cid[CONDITION_ID] = record
        await host._detach_quiet_missing(set())
        assert detached == []
        assert CONDITION_ID in host._quiet
        record.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await record.task

    asyncio.run(run())


def test_unpinned_waiter_adopts_grid_match_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One CID can swap steam numeric id for grid-* before the session is pinned."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    host = _bare_host(SimpleNamespace(metas={}))
    steam = build_discovered()
    record = _record_for(steam)
    record.waiting_for_feed = True
    host._matches_by_cid[CONDITION_ID] = record
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)
        target.task = cast(Any, SimpleNamespace(done=lambda: False))

    host._launch = fake_launch  # type: ignore[method-assign]
    grid = replace(
        steam,
        match_id="grid-2998620-m2",
        steam_match_id=None,
        market=replace(steam.market, grid_series_id="2998620"),
    )
    host.reconcile((grid,))
    assert list(host._matches_by_cid) == [CONDITION_ID]
    assert record.match_id == "grid-2998620-m2"
    assert launched == ["grid-2998620-m2"]


def test_grid_orientation_error_is_an_ordinary_crash_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GridOrientationError from the feed is a crash retry, not a feed wait."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered()
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record

    class _FailedTask:
        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> BaseException:
            return GridOrientationError("grid orientation unresolved")

    record.task = cast(Any, _FailedTask())
    now = 1_000.0
    host._reap_finished_tasks(now)
    assert record.attempts == 1
    assert record.exhausted is False
    assert record.waiting_for_feed is False
    assert record.next_eligible == now + RESTART_BACKOFF_SECONDS[0]
    assert record.task is None


def test_register_worker_refuses_a_second_owner() -> None:
    """Two workers cannot share a cid in the routing maps."""
    engine = SimpleNamespace(metas={}, state=SimpleNamespace(pending_outbox=lambda: []))
    host = _bare_host(engine)
    first = SimpleNamespace(
        _cell=object(), _discovered=SimpleNamespace(match_id=MATCH_ID), core=None
    )
    second = SimpleNamespace(
        _cell=object(), _discovered=SimpleNamespace(match_id=MATCH_ID), core=None
    )
    host.register_worker(CONDITION_ID, {YES_TOKEN, NO_TOKEN}, cast(Any, first))
    with pytest.raises(RuntimeError, match="already has a live worker"):
        host.register_worker(CONDITION_ID, {YES_TOKEN, NO_TOKEN}, cast(Any, second))
    assert host._worker_by_cid[CONDITION_ID] is first


def test_exhaustion_alerts_once_and_retrieves_task_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give-up after MAX_CRASH_RESTARTS pages once; task.exception() is observed."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(build_discovered())
    host._matches_by_cid[CONDITION_ID] = record

    class _FailedTask:
        def __init__(self, exc: BaseException) -> None:
            self._exc = exc
            self.retrieved = False

        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> BaseException:
            self.retrieved = True
            return self._exc

    observed: list[_FailedTask] = []
    for _ in range(MAX_CRASH_RESTARTS + 1):
        failed = _FailedTask(RuntimeError("worker crashed"))
        observed.append(failed)
        record.task = cast(Any, failed)
        host._reap_finished_tasks(0.0)
    assert record.exhausted is True
    assert len(alerts) == 1
    assert MATCH_ID in alerts[0]
    assert "exhausted" in alerts[0]
    assert all(task.retrieved for task in observed)


def test_crash_retry_waits_backoff_then_relaunches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash without Steam-final waits the backoff; reconcile then relaunches."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered()
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)
        target.task = cast(Any, SimpleNamespace(done=lambda: False))

    host._launch = fake_launch  # type: ignore[method-assign]

    class _FailedTask:
        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> BaseException:
            return RuntimeError("worker crashed")

    record.task = cast(Any, _FailedTask())
    now = 1_000.0
    host._reap_finished_tasks(now)
    assert record.attempts == 1
    assert record.exhausted is False
    assert record.next_eligible == now + RESTART_BACKOFF_SECONDS[0]
    assert record.task is None
    host._handle_match(discovered, now + RESTART_BACKOFF_SECONDS[0] - 1.0)
    assert launched == []
    host._handle_match(discovered, now + RESTART_BACKOFF_SECONDS[0])
    assert launched == [MATCH_ID]


def _outbox_host(engine: object, store: WalletStateStore) -> WalletHost:
    """Bare host with real outbox drain/dispatch bound."""
    host = _bare_host(engine)
    host.store = store
    host.drain_outbox = MethodType(WalletHost.drain_outbox, host)
    host._dispatch_fill = MethodType(WalletHost._dispatch_fill, host)
    host._journal_fill = MethodType(WalletHost._journal_fill, host)
    host._ack_outbox_event = MethodType(WalletHost._ack_outbox_event, host)
    return host


class _Markout:
    """Records markout samples."""

    def __init__(self) -> None:
        self.fills: list[tuple[object, float, float]] = []

    def record_fill(self, side: object, fv: float, ts: float) -> None:
        self.fills.append((side, fv, ts))


def test_confirmed_outbox_acked_only_after_side_effect(tmp_path: Path) -> None:
    """CONFIRMED seq stays unacked until journal/markout run, then acks that seq only."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("t1", "o1")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.MATCHED, 1.0),
        CONDITION_ID,
    )
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.CONFIRMED, 2.0),
        CONDITION_ID,
    )
    meta = make_meta()
    markout = _Markout()
    engine = SimpleNamespace(
        _token_cid={YES_TOKEN: meta.condition_id, NO_TOKEN: meta.condition_id},
        metas={meta.condition_id: meta},
        est={meta.condition_id: SimpleNamespace(last_fv=0.55, markout=markout)},
        state=store,
    )
    host = _outbox_host(engine, store)
    fill = store.fill_for_key(key)
    assert fill is not None
    journals: list[Fill] = []
    host._worker_by_token[YES_TOKEN] = cast(Any, SimpleNamespace(note_fill=journals.append))
    host._dispatch_fill(fill)
    assert len(journals) == 1
    assert len(markout.fills) == 1
    remaining = store.pending_outbox()
    assert all(item.event != "confirmed" for item in remaining)
    host._dispatch_fill(fill)
    assert len(journals) == 1
    store.close()


def test_crash_before_ack_replays_fill_journal_once(tmp_path: Path) -> None:
    """An unacked CONFIRMED row replays the fill journal once on drain."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("t1", "o1")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.CONFIRMED, 1.0),
        CONDITION_ID,
    )
    meta = make_meta()
    markout = _Markout()
    engine = SimpleNamespace(
        _token_cid={YES_TOKEN: meta.condition_id, NO_TOKEN: meta.condition_id},
        metas={meta.condition_id: meta},
        est={meta.condition_id: SimpleNamespace(last_fv=0.55, markout=markout)},
        state=store,
    )
    host = _outbox_host(engine, store)
    journals: list[Fill] = []
    host._worker_by_token[YES_TOKEN] = cast(Any, SimpleNamespace(note_fill=journals.append))
    host.drain_outbox()
    assert len(journals) == 1
    host.drain_outbox()
    assert len(journals) == 1
    store.close()


def test_restart_drain_skips_markout_without_last_fv(tmp_path: Path) -> None:
    """Restart drain journals confirmed fills but must not markout without historical FV."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("t1", "o1")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.CONFIRMED, 1.0),
        CONDITION_ID,
    )
    meta = make_meta()
    markout = _Markout()
    engine = SimpleNamespace(
        _token_cid={YES_TOKEN: meta.condition_id, NO_TOKEN: meta.condition_id},
        metas={meta.condition_id: meta},
        est={meta.condition_id: SimpleNamespace(last_fv=None, markout=markout)},
        state=store,
    )
    host = _outbox_host(engine, store)
    journals: list[Fill] = []
    host._worker_by_token[YES_TOKEN] = cast(Any, SimpleNamespace(note_fill=journals.append))
    host.drain_outbox()
    assert len(journals) == 1
    assert markout.fills == []
    store.close()


def test_matched_drain_does_not_journal(tmp_path: Path) -> None:
    """MATCHED outbox stays unacked and is not journaled; fence still sees it."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("t1", "o1")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.MATCHED, 1.0),
        CONDITION_ID,
    )
    engine = SimpleNamespace(state=store, _token_cid={}, metas={}, est={})
    host = _outbox_host(engine, store)
    journals: list[Fill] = []
    host._worker_by_token[YES_TOKEN] = cast(Any, SimpleNamespace(note_fill=journals.append))
    host.drain_outbox()
    assert journals == []
    assert store.has_unacked_matched({YES_TOKEN}) is True
    store.close()


def test_paper_confirmed_journals_and_markout_once(tmp_path: Path) -> None:
    """Paper apply_fill is CONFIRMED: one journal and one markout on that path."""
    store = WalletStateStore(tmp_path / "wallet.db")
    fill = Fill(YES_TOKEN, Side.BUY, 0.4, 10.0, "paper-ns-1:fill", 1.0, is_maker=True)
    assert store.apply_fill(fill) is True
    meta = make_meta()
    markout = _Markout()
    engine = SimpleNamespace(
        _token_cid={YES_TOKEN: meta.condition_id, NO_TOKEN: meta.condition_id},
        metas={meta.condition_id: meta},
        est={meta.condition_id: SimpleNamespace(last_fv=0.55, markout=markout)},
        state=store,
        paper=True,
    )
    host = _outbox_host(engine, store)
    journals: list[Fill] = []
    host._worker_by_token[YES_TOKEN] = cast(Any, SimpleNamespace(note_fill=journals.append))
    host._dispatch_fill(fill)
    assert len(journals) == 1
    assert len(markout.fills) == 1
    host._dispatch_fill(fill)
    assert len(journals) == 1
    assert len(markout.fills) == 1
    store.close()


def test_record_engine_markout_skips_without_last_fv() -> None:
    """Falling back to fill.price after restart would poison toxicity."""
    meta = make_meta()
    markout = _Markout()
    engine = SimpleNamespace(
        _token_cid={YES_TOKEN: meta.condition_id},
        metas={meta.condition_id: meta},
        est={meta.condition_id: SimpleNamespace(last_fv=None, markout=markout)},
    )
    fill = Fill(YES_TOKEN, Side.BUY, 0.4, 10.0, "t1:o1", 1.0, is_maker=True)
    record_engine_markout(cast(Any, engine), fill)
    assert markout.fills == []


def test_clean_no_snapshot_exit_schedules_backoff_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean feed exit with no match.json counts as one failure and waits the backoff."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered()
    record = _record_for(discovered)
    record.feed_source = FeedSource.GRID
    host._matches_by_cid[CONDITION_ID] = record
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)
        target.task = cast(Any, SimpleNamespace(done=lambda: False))

    host._launch = fake_launch  # type: ignore[method-assign]

    class _CleanTask:
        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            return None

    record.task = cast(Any, _CleanTask())
    now = 1_000.0
    host._reap_finished_tasks(now)
    assert record.no_snapshot is True
    assert record.attempts == 1
    assert record.exhausted is False
    assert record.next_eligible == now + RESTART_BACKOFF_SECONDS[0]
    host._handle_match(discovered, now + RESTART_BACKOFF_SECONDS[0] - 1.0)
    assert launched == []
    assert record.task is None
    host._handle_match(discovered, now + RESTART_BACKOFF_SECONDS[0])
    assert launched == [MATCH_ID]


def test_missing_from_discovery_abandons_dead_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-snapshot worker whose match left the live list is marked complete once."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(build_discovered())
    record.no_snapshot = True
    host._matches_by_cid[CONDITION_ID] = record
    host.reconcile(())
    assert record.completed is True
    assert record.attempts == 0
    assert alerts == []
    host.reconcile(())
    assert alerts == []


def test_missing_from_discovery_does_not_complete_feed_waiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A picker skip stays waiting through an empty cycle and launches on rediscovery."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered()
    record = _record_for(discovered)
    record.waiting_for_feed = True
    host._matches_by_cid[CONDITION_ID] = record
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)
        target.task = cast(Any, SimpleNamespace(done=lambda: False))

    host._launch = fake_launch  # type: ignore[method-assign]
    host.reconcile(())
    assert record.completed is False
    assert record.waiting_for_feed is True
    assert record.exhausted is False
    assert record.attempts == 0
    assert alerts == []
    assert launched == []
    host.reconcile((discovered,))
    assert launched == [MATCH_ID]
    assert record.completed is False


def test_no_snapshot_alerts_once_across_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The feed-dead Telegram fires on the first no-snapshot exit, not later retries."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(build_discovered(grid_series_id="2995964"))
    record.feed_source = FeedSource.GRID
    host._matches_by_cid[CONDITION_ID] = record

    class _CleanTask:
        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            return None

    record.task = cast(Any, _CleanTask())
    host._reap_finished_tasks(0.0)
    assert len(alerts) == 1
    assert MATCH_ID in alerts[0]
    assert "2995964" in alerts[0]
    assert "retrying" in alerts[0]
    record.task = cast(Any, _CleanTask())
    host._reap_finished_tasks(60.0)
    assert len(alerts) == 1
    assert record.attempts == 2


def test_no_snapshot_exhaustion_alerts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four no-snapshot exits page feed-dead once, then exhausted once."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(build_discovered())
    record.feed_source = FeedSource.GRID
    host._matches_by_cid[CONDITION_ID] = record

    class _CleanTask:
        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            return None

    for _ in range(MAX_CRASH_RESTARTS + 1):
        record.task = cast(Any, _CleanTask())
        host._reap_finished_tasks(0.0)
    assert record.exhausted is True
    assert record.attempts == MAX_CRASH_RESTARTS + 1
    assert len(alerts) == 2
    assert MATCH_ID in alerts[0]
    assert "retrying" in alerts[0]
    assert "exhausted" in alerts[1]


def test_record_only_no_snapshot_failure_sends_no_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record-only map dying without an archive still retries, just silently."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(replace(build_discovered(), record_only=True))
    record.record_only = True
    record.feed_source = FeedSource.GRID
    host._matches_by_cid[CONDITION_ID] = record

    class _CleanTask:
        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            return None

    record.task = cast(Any, _CleanTask())
    host._reap_finished_tasks(0.0)
    assert record.no_snapshot is True
    assert record.attempts == 1
    assert alerts == []


def test_record_only_exhaustion_sends_no_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record-only retries exhaust silently: no feed-dead and no exhausted page."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(replace(build_discovered(), record_only=True))
    record.record_only = True
    record.feed_source = FeedSource.GRID
    host._matches_by_cid[CONDITION_ID] = record

    class _CleanTask:
        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            return None

    for _ in range(MAX_CRASH_RESTARTS + 1):
        record.task = cast(Any, _CleanTask())
        host._reap_finished_tasks(0.0)
    assert record.exhausted is True
    assert record.attempts == MAX_CRASH_RESTARTS + 1
    assert alerts == []


def test_record_only_launch_logs_without_start_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A record_only handoff launches the worker but pages no session started."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)

    async def fake_select(handoff: object, _should_close: object) -> object:
        return SimpleNamespace(
            feed=SimpleNamespace(source=FeedSource.GRID, stale_seconds=GRID_FEED_STALE_SECONDS),
            handoff=handoff,
        )

    class _IdleWorker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def run(self) -> None:
            return None

    monkeypatch.setattr("trader.wallet_host.select_feed", fake_select)
    monkeypatch.setattr("trader.wallet_host.MatchWorker", _IdleWorker)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = replace(build_discovered(grid_series_id="2995964"), record_only=True)
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        with caplog.at_level(logging.INFO, logger="trader.wallet_host"):
            host._launch(record)
            task = record.task
            assert task is not None
            await task

    asyncio.run(run())
    assert record.announced is True
    assert record.record_only is True
    assert record.feed_source is FeedSource.GRID
    assert alerts == []
    assert any(
        f"record-only alert suppressed match={MATCH_ID}" in item.getMessage()
        for item in caplog.records
    )


def test_lol_book_watch_keeps_only_traded_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Traded map and series markets get a book and a prior-tape slot; record-only ones do not."""
    write_json(
        tmp_path / "metadata/events/lec-event.json",
        {"id": "lec-event", "eventMetadata": {"league": "LEC"}},
    )
    write_json(
        tmp_path / "metadata/events/lck-event.json",
        {"id": "lck-event", "eventMetadata": {"league": "LCK Cup"}},
    )

    def lol_sidecar(condition_id: str, event_id: str) -> dict[str, object]:
        return sidecar_body(
            conditionId=condition_id,
            eventId=event_id,
            eventSlug=f"lol-{event_id}",
            marketSlug=f"lol-{event_id}-g1",
            mapNumber=1,
            outcomes=[
                {"index": 0, "name": "A", "tokenId": f"{condition_id}-0"},
                {"index": 1, "name": "B", "tokenId": f"{condition_id}-1"},
            ],
        )

    write_sidecar(tmp_path, "0xlec", lol_sidecar("0xlec", "lec-event"))
    write_sidecar(tmp_path, "0xlck", lol_sidecar("0xlck", "lck-event"))
    series = lol_sidecar("0xbo1", "lec-event")
    series.update(marketKind="series_winner", mapNumber=None, marketSlug="lol-lec-event")
    write_sidecar(tmp_path, "0xbo1", series)
    retained: list[dict[str, tuple[str, str]]] = []
    applied: list[dict[str, tuple[str, str]]] = []
    monkeypatch.setattr(
        "trader.wallet_host.apply_book_only_markets",
        lambda engine, markets, readiness: applied.append(dict(markets)),
    )
    host = _bare_host(SimpleNamespace(metas={}))
    host._archive_by_game = {"lol": tmp_path}
    host._lol_leagues = None
    host.readiness = SimpleNamespace()
    host.lol_prior = cast(
        Any, SimpleNamespace(retain=lambda markets: retained.append(dict(markets)))
    )

    host._sync_lol_book_watch()

    expected = {"0xlec": ("0xlec-0", "0xlec-1"), "0xbo1": ("0xbo1-0", "0xbo1-1")}
    assert retained == [expected]
    assert applied == [expected]


def _grid_pin_event() -> FeedEvent:
    """One GRID first event so write_match_start can pin feed_source."""
    snapshot = GameSnapshot(
        second=0,
        server_timestamp=0,
        phase=MatchPhase.IN_PROGRESS,
        radiant_nw_adv=0,
        radiant_nw=0,
        dire_nw=0,
        radiant_xp_adv=0,
        deaths_radiant=0,
        deaths_dire=0,
        top=ZERO_TOP,
        paused=False,
    )
    return FeedEvent(snapshot, "2026-08-14T12:00:00Z", FeedSource.GRID, 0, True)


def test_grid_only_handoff_builds_grid_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GRID-only handoff whose scoreboard probe returns delay 8 builds GridLiveFeed."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)

    async def fake_probe(series_id: str, map_number: int) -> int | None:
        assert series_id == "2995964"
        assert map_number == 1
        return 8

    monkeypatch.setattr("trader.feed_selection.probe_grid_delay", fake_probe)
    discovered = build_discovered(grid_series_id="2995964")

    async def run() -> None:
        choice = await select_feed(discovered, _keep_map)
        assert choice is not None
        assert isinstance(choice.feed, GridLiveFeed)
        assert choice.feed.stale_seconds == GRID_FEED_STALE_SECONDS
        assert choice.feed.source is FeedSource.GRID

    asyncio.run(run())


def test_grid_scoreboard_delay_launches_without_waiting_for_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GRID handoff with a scoreboard delay starts the feed; no waiting_for_feed."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)

    async def fake_probe(series_id: str, map_number: int) -> int | None:
        assert series_id == "2995964"
        assert map_number == 1
        return 8

    monkeypatch.setattr("trader.feed_selection.probe_grid_delay", fake_probe)

    class _IdleWorker:
        def __init__(
            self,
            host: object,
            discovered: object,
            model: object,
            mode: object,
            feed: object,
            feed_timeout_seconds: float,
        ) -> None:
            del host, discovered, model, mode
            assert isinstance(feed, GridLiveFeed)
            assert feed_timeout_seconds == GRID_FEED_STALE_SECONDS

        async def run(self) -> None:
            return

    monkeypatch.setattr("trader.wallet_host.MatchWorker", _IdleWorker)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered(grid_series_id="2995964")
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        host._launch(record)
        task = record.task
        assert task is not None
        await task
        assert record.waiting_for_feed is False
        assert record.feed_source is FeedSource.GRID
        assert record.attempts == 0

    asyncio.run(run())


def test_lol_grid_handoff_launches_without_steam_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LoL GRID delay 8 starts the worker when steam_client is None; not waiting_for_feed."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)

    async def fake_probe(series_id: str, map_number: int) -> int | None:
        assert series_id == "2995964"
        assert map_number == 1
        return 8

    monkeypatch.setattr("trader.feed_selection.probe_grid_delay", fake_probe)
    received: list[object] = []

    class _IdleWorker:
        def __init__(
            self,
            host: object,
            discovered: object,
            model: object,
            mode: object,
            feed: object,
            feed_timeout_seconds: float,
        ) -> None:
            del host, discovered, mode
            received.append(model)
            assert isinstance(feed, GridLiveFeed)
            assert feed_timeout_seconds == GRID_FEED_STALE_SECONDS

        async def run(self) -> None:
            return

    monkeypatch.setattr("trader.wallet_host.MatchWorker", _IdleWorker)
    host = _bare_host(SimpleNamespace(metas={}))
    lol_model = FakeModelServer()
    host._models = {
        catalog.profile_name: lol_model for catalog in strategy_catalogs(GAME_PROFILES["lol"])
    }
    host._games = (GAME_PROFILES["lol"],)
    host.steam_client = None
    discovered = replace(build_discovered(grid_series_id="2995964"), game="lol")
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        host._launch(record)
        task = record.task
        assert task is not None
        await task
        assert record.waiting_for_feed is False
        assert record.feed_source is FeedSource.GRID
        assert record.attempts == 0
        assert received == [lol_model]

    asyncio.run(run())


def test_lol_without_grid_series_selects_nothing() -> None:
    """LoL with no GRID series is not a usable source."""
    handoff = replace(build_discovered(), game="lol")

    async def run() -> None:
        assert await select_feed(handoff, _keep_map) is None

    asyncio.run(run())


def test_unknown_game_completes_without_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown game identity completes at launch: no task, no archive dir."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    constructed: list[object] = []

    class _FailWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            constructed.append((args, kwargs))
            raise AssertionError("unknown game must not construct MatchWorker")

        async def run(self) -> None:
            return

    monkeypatch.setattr("trader.wallet_host.MatchWorker", _FailWorker)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = replace(build_discovered(), game="cs2")
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record
    host._launch(record)
    assert record.task is None
    assert record.completed is True
    assert record.waiting_for_feed is False
    assert constructed == []
    assert not (tmp_path / MATCH_ID).exists()


def test_dota_without_grid_or_oddin_selects_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Steam-linked Dota handoff with no GRID series and no Oddin id has no source."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)

    async def run() -> None:
        assert await select_feed(build_discovered(), _keep_map) is None

    asyncio.run(run())


def test_picker_skip_retries_on_next_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No usable feed is not a crash: attempts stay 0 and the next cycle launches again."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered()
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        host._launch(record)
        task = record.task
        assert task is not None
        await task
        assert task.exception() is None
        assert alerts == []
        host._reap_finished_tasks(100.0)
        assert record.attempts == 0
        assert record.exhausted is False
        assert record.waiting_for_feed is True
        assert record.feed_source is None
        assert record.no_snapshot is False
        assert record.task is None
        host._handle_match(discovered, 100.0)
        retry = host._matches_by_cid[CONDITION_ID].task
        assert retry is not None
        await retry

    asyncio.run(run())


def test_picker_skip_does_not_exhaust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Four picker skips in a row do not consume crash restarts or page exhausted."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered()
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        for _ in range(MAX_CRASH_RESTARTS + 1):
            host._launch(record)
            task = record.task
            assert task is not None
            await task
            host._reap_finished_tasks(0.0)
        assert record.attempts == 0
        assert record.exhausted is False
        assert record.waiting_for_feed is True
        assert record.feed_source is None
        assert alerts == []

    asyncio.run(run())


def test_picker_skip_then_start_alerts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skip then a successful pick sends session started once and does not exhaust."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    picks = {"n": 0}

    async def fake_select(handoff: object, _should_close: object) -> object:
        picks["n"] += 1
        if picks["n"] == 1:
            return None
        return SimpleNamespace(
            feed=SimpleNamespace(source=FeedSource.GRID, stale_seconds=GRID_FEED_STALE_SECONDS),
            handoff=handoff,
        )

    class _IdleWorker:
        def __init__(
            self,
            host: object,
            discovered: object,
            model: object,
            mode: object,
            feed: object,
            feed_timeout_seconds: float,
        ) -> None:
            del (
                host,
                discovered,
                model,
                mode,
                feed,
                feed_timeout_seconds,
            )

        async def run(self) -> None:
            return

    monkeypatch.setattr("trader.wallet_host.select_feed", fake_select)
    monkeypatch.setattr("trader.wallet_host.MatchWorker", _IdleWorker)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered(grid_series_id="2995964")
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        host._launch(record)
        first = record.task
        assert first is not None
        await first
        host._reap_finished_tasks(0.0)
        assert record.announced is False
        assert alerts == []
        host._handle_match(discovered, 0.0)
        second = record.task
        assert second is not None
        await second
        assert record.announced is True
        assert record.waiting_for_feed is False
        assert record.feed_source is FeedSource.GRID
        assert record.attempts == 0
        assert record.exhausted is False
        assert len(alerts) == 1
        assert alerts[0].startswith(SESSION_STARTED_PREFIX)

    asyncio.run(run())


def test_corrupt_feed_pin_counts_a_crash_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pinned GRID source whose series id is gone raises: the reaper backs off as usual."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    pinned = build_discovered(grid_series_id="2995964")
    start = pinned.with_model(ModelReference(name="pin", trained_at="2026-08-14T16:52:39Z"))
    write_match_start(start, _grid_pin_event())
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(build_discovered())
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        host._launch(record)
        task = record.task
        assert task is not None
        await asyncio.gather(task, return_exceptions=True)
        assert isinstance(task.exception(), CorruptFeedPin)
        host._reap_finished_tasks(100.0)
        assert record.waiting_for_feed is False
        assert record.attempts == 1
        assert record.next_eligible == 100.0 + RESTART_BACKOFF_SECONDS[0]

    asyncio.run(run())


def test_discovery_refreshes_the_handoff_for_the_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every cycle adopts the new handoff, so the retry picks on the fresh Oddin id."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    seen: list[str | None] = []

    async def fake_select(handoff: Any, _should_close: object) -> object:
        seen.append(cast(str | None, handoff.oddin_match_id))
        return None

    monkeypatch.setattr("trader.wallet_host.select_feed", fake_select)
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(replace(build_discovered(), oddin_match_id="od:match:1"))
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        host._launch(record)
        first = record.task
        assert first is not None
        await first
        host._reap_finished_tasks(0.0)
        host._handle_match(replace(build_discovered(), oddin_match_id="od:match:2"), 0.0)
        retry = record.task
        assert retry is not None
        await retry
        assert seen == ["od:match:1", "od:match:2"]
        assert record.handoff.oddin_match_id == "od:match:2"

    asyncio.run(run())


def test_pinned_grid_skips_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing match.json feed_source=grid forces GRID without a delay probe."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    discovered = build_discovered(grid_series_id="2995964")
    start = discovered.with_model(ModelReference(name="pin", trained_at="2026-08-14T16:52:39Z"))
    write_match_start(start, _grid_pin_event())

    async def boom(series_id: str, map_number: int) -> int | None:
        raise AssertionError("pinned source must not probe GRID")

    monkeypatch.setattr("trader.feed_selection.probe_grid_delay", boom)

    async def run() -> None:
        choice = await select_feed(discovered, _keep_map)
        assert choice is not None
        assert isinstance(choice.feed, GridLiveFeed)
        assert choice.feed.stale_seconds == GRID_FEED_STALE_SECONDS
        assert choice.feed.source is FeedSource.GRID

    asyncio.run(run())


def _oddin_pin_event() -> FeedEvent:
    """One Oddin first event so write_match_start can pin feed_source."""
    snapshot = GameSnapshot(
        second=0,
        server_timestamp=0,
        phase=MatchPhase.IN_PROGRESS,
        radiant_nw_adv=0,
        radiant_nw=0,
        dire_nw=0,
        radiant_xp_adv=0,
        deaths_radiant=0,
        deaths_dire=0,
        top=ZERO_TOP,
        paused=False,
    )
    return FeedEvent(snapshot, "2026-08-14T12:00:00Z", FeedSource.ODDIN, 0, True)


def test_without_grid_oddin_15_selects_oddin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No GRID series leaves Oddin as the only usable source."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)

    async def fake_oddin(match_id: str, map_number: int) -> int | None:
        del match_id, map_number
        return 15

    monkeypatch.setattr("trader.feed_selection.probe_oddin_delay", fake_oddin)
    handoff = replace(build_discovered(), oddin_match_id="od:match:3211324")

    async def run() -> None:
        choice = await select_feed(handoff, _keep_map)
        assert choice is not None
        assert isinstance(choice.feed, OddinLiveFeed)
        assert choice.feed.source is FeedSource.ODDIN
        assert choice.feed.stale_seconds == ODDIN_FEED_STALE_SECONDS
        assert choice.handoff.oddin_delay_s == 15

    asyncio.run(run())


def test_oddin_only_handoff_selects_oddin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No GRID series and no Steam link still picks Oddin when the probe returns."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)

    async def fake_oddin(match_id: str, map_number: int) -> int | None:
        del match_id, map_number
        return 15

    monkeypatch.setattr("trader.feed_selection.probe_oddin_delay", fake_oddin)
    handoff = replace(build_discovered(), steam_match_id=None, oddin_match_id="od:match:3211324")

    async def run() -> None:
        choice = await select_feed(handoff, _keep_map)
        assert choice is not None
        assert choice.feed.source is FeedSource.ODDIN

    asyncio.run(run())


def test_equal_delays_prefer_grid_over_oddin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 30/30 tie picks GRID over Oddin."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)

    async def fake_grid(series_id: str, map_number: int) -> int | None:
        del series_id, map_number
        return 30

    async def fake_oddin(match_id: str, map_number: int) -> int | None:
        del match_id, map_number
        return 30

    monkeypatch.setattr("trader.feed_selection.probe_grid_delay", fake_grid)
    monkeypatch.setattr("trader.feed_selection.probe_oddin_delay", fake_oddin)
    handoff = replace(build_discovered(grid_series_id="2995964"), oddin_match_id="od:match:3211324")

    async def run() -> None:
        choice = await select_feed(handoff, _keep_map)
        assert choice is not None and choice.feed.source is FeedSource.GRID

    asyncio.run(run())


def test_pinned_oddin_skips_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing match.json feed_source=oddin forces Oddin without a delay probe."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    discovered = replace(
        build_discovered(),
        oddin_match_id="od:match:3211324",
        oddin_delay_s=15,
    )
    start = discovered.with_model(ModelReference(name="pin", trained_at="2026-08-14T16:52:39Z"))
    write_match_start(start, _oddin_pin_event())

    async def boom(series_id: str, map_number: int) -> int | None:
        raise AssertionError("pinned oddin must not probe GRID")

    monkeypatch.setattr("trader.feed_selection.probe_grid_delay", boom)

    async def run() -> None:
        choice = await select_feed(discovered, _keep_map)
        assert choice is not None
        assert isinstance(choice.feed, OddinLiveFeed)
        assert choice.feed.source is FeedSource.ODDIN
        assert choice.handoff.oddin_delay_s == 15

    asyncio.run(run())


def test_pinned_oddin_uses_archive_id_when_discovery_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cold Disir catalog still binds the Oddin id already stored in match.json."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    archived = replace(
        build_discovered(),
        oddin_match_id="od:match:3211324",
        oddin_delay_s=15,
    )
    start = archived.with_model(ModelReference(name="pin", trained_at="2026-08-14T16:52:39Z"))
    write_match_start(start, _oddin_pin_event())
    handoff = replace(archived, oddin_match_id=None)

    async def run() -> None:
        choice = await select_feed(handoff, _keep_map)
        assert choice is not None
        assert isinstance(choice.feed, OddinLiveFeed)
        assert choice.feed._oddin_match_id == "od:match:3211324"
        assert choice.handoff.oddin_match_id == "od:match:3211324"

    asyncio.run(run())


def test_dead_oddin_alert_keeps_archive_id_after_discovery_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The feed-dead alert names the archive Oddin id when discovery's handoff has none."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.wallet_host.notify_in_background", lambda message: None)
    archived = replace(
        build_discovered(),
        oddin_match_id="od:match:3211324",
        oddin_delay_s=15,
    )
    start = archived.with_model(ModelReference(name="pin", trained_at="2026-08-14T16:52:39Z"))
    write_match_start(start, _oddin_pin_event())
    handoff = replace(archived, oddin_match_id=None)

    class IdleWorker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def run(self) -> None:
            return None

    monkeypatch.setattr("trader.wallet_host.MatchWorker", IdleWorker)
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(handoff)

    async def run() -> None:
        await host._pick_and_run(record)

    asyncio.run(run())
    assert record.feed_source is FeedSource.ODDIN
    assert _dead_feed_id(record) == "od:match:3211324"


def test_pinned_oddin_without_archive_id_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Oddin pin with a null archive id is corruption even when discovery has an id."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    start = replace(build_discovered(), oddin_match_id=None).with_model(
        ModelReference(name="pin", trained_at="2026-08-14T16:52:39Z")
    )
    write_match_start(start, _oddin_pin_event())
    handoff = replace(build_discovered(), oddin_match_id="od:match:3211324")

    async def run() -> None:
        with pytest.raises(CorruptFeedPin, match="no match id"):
            await select_feed(handoff, _keep_map)

    asyncio.run(run())


def test_waiting_record_rebinds_map_and_market(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A feed wait with no session start adopts a later grid match_id on the same CID."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    host = _bare_host(SimpleNamespace(metas={}))
    first = build_discovered(map_number=1)
    record = _record_for(first)
    record.waiting_for_feed = True
    host._matches_by_cid[CONDITION_ID] = record
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.handoff.market.condition_id)
        target.task = cast(Any, SimpleNamespace(done=lambda: False))

    host._launch = fake_launch  # type: ignore[method-assign]
    second = replace(
        first,
        match_id="grid-2998620-m2",
        steam_match_id=None,
        map_number=2,
        market=replace(first.market, grid_series_id="2998620"),
    )
    host._handle_match(second, 0.0)
    assert launched == [CONDITION_ID]
    assert record.match_id == "grid-2998620-m2"
    assert record.handoff.map_number == 2
    assert record.identity.condition_id == CONDITION_ID
    assert record.identity.map_number == 2
    assert record.identity.grid_series_id == "2998620"


def test_started_session_rejects_binding_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After session start, a different match_id on the same CID is rejected."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    host = _bare_host(SimpleNamespace(metas={}))
    first = build_discovered(map_number=1)
    record = _record_for(first)
    record.announced = True
    archive = tmp_path / MATCH_ID
    archive.mkdir()
    (archive / MATCH_META_FILENAME).write_text("{}", encoding="utf-8")
    host._matches_by_cid[CONDITION_ID] = record
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.handoff.market.condition_id)

    host._launch = fake_launch  # type: ignore[method-assign]
    second = replace(
        first,
        match_id="grid-2998620-m2",
        steam_match_id=None,
        map_number=2,
        market=replace(first.market, grid_series_id="2998620"),
    )
    host._handle_match(second, 0.0)
    assert launched == []
    assert record.match_id == MATCH_ID
    assert record.identity.condition_id == CONDITION_ID
    assert record.handoff.map_number == 1


def test_rebind_during_backoff_adopts_the_binding_without_launching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash backoff still gates the launch; the new match_id is adopted for the retry."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    host = _bare_host(SimpleNamespace(metas={}))
    first = build_discovered(map_number=1)
    record = _record_for(first)
    record.attempts = 1
    record.next_eligible = 500.0
    host._matches_by_cid[CONDITION_ID] = record
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.handoff.market.condition_id)

    host._launch = fake_launch  # type: ignore[method-assign]
    second = replace(
        first,
        match_id="grid-2998620-m2",
        steam_match_id=None,
        map_number=2,
        market=replace(first.market, grid_series_id="2998620"),
    )
    host._handle_match(second, 100.0)
    assert launched == []
    assert record.match_id == "grid-2998620-m2"
    assert record.identity.map_number == 2
    host._handle_match(second, 600.0)
    assert launched == [CONDITION_ID]


def _unfinished_schema3_text() -> str:
    """Valid unfinished schema-3 match.json for MATCH_ID / build_discovered."""
    discovered = build_discovered()
    market = discovered.market
    document = {
        "schema_version": 3,
        "match_id": discovered.match_id,
        "steam_match_id": discovered.steam_match_id,
        "server_steam_id": "9012345678",
        "league_id": discovered.league_id,
        "tournament": discovered.tournament,
        "teams": {"radiant": discovered.sides.radiant, "dire": discovered.sides.dire},
        "map_number": discovered.map_number,
        "joined_at_second": -90,
        "joined_at_utc": "2026-08-14T12:00:00.123456Z",
        "horn_at_utc": "2026-08-14T12:01:30Z",
        "market": {
            "condition_id": market.condition_id,
            "market_slug": market.market_slug,
            "event_slug": market.event_slug,
            "yes_token_id": market.yes_token_id,
            "no_token_id": market.no_token_id,
            "yes_is_radiant": market.yes_is_radiant,
            "outcome_0_name": market.outcome_0_name,
            "outcome_1_name": market.outcome_1_name,
            "tick_size": market.tick_size,
            "min_order_size": market.min_order_size,
            "neg_risk": market.neg_risk,
            "grid_series_id": market.grid_series_id,
        },
        "model": {"name": "test-model", "trained_at": "2026-01-01T00:00:00Z"},
        "feed_source": "grid",
        "steam_delay_s": None,
        "steam_observed_lag_s": None,
        "grid_delay_s": None,
        "final": None,
    }
    return json.dumps(document, indent=2) + "\n"


def test_unfinished_schema_3_start_is_not_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unfinished schema-3 match.json survives a launch untouched."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    archive = tmp_path / MATCH_ID
    archive.mkdir(parents=True)
    original = _unfinished_schema3_text()
    (archive / MATCH_META_FILENAME).write_text(original, encoding="utf-8")

    async def fake_select(handoff: object, _should_close: object) -> object:
        return SimpleNamespace(
            feed=SimpleNamespace(source=FeedSource.GRID, stale_seconds=GRID_FEED_STALE_SECONDS),
            handoff=handoff,
        )

    class _IdleWorker:
        def __init__(
            self,
            host: object,
            discovered: object,
            model: object,
            mode: object,
            feed: object,
            feed_timeout_seconds: float,
        ) -> None:
            del host, discovered, model, mode, feed, feed_timeout_seconds

        async def run(self) -> None:
            return

    monkeypatch.setattr("trader.wallet_host.select_feed", fake_select)
    monkeypatch.setattr("trader.wallet_host.MatchWorker", _IdleWorker)
    host = _bare_host(SimpleNamespace(metas={}))
    record = _record_for(build_discovered())
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        host._launch(record)
        task = record.task
        assert task is not None
        await task

    asyncio.run(run())
    assert (archive / MATCH_META_FILENAME).read_text(encoding="utf-8") == original
    loaded = json.loads((archive / MATCH_META_FILENAME).read_text(encoding="utf-8"))
    assert "kalshi" not in loaded


def test_own_steam_pin_closes_the_record_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An own unfinished Steam archive is skipped once: no probe, no page, no retry."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr("trader.wallet_host.notify_in_background", alerts.append)
    archive = tmp_path / MATCH_ID
    archive.mkdir(parents=True)
    document = json.loads(_unfinished_schema3_text())
    document["feed_source"] = "steam"
    (archive / MATCH_META_FILENAME).write_text(json.dumps(document), encoding="utf-8")
    host = _bare_host(SimpleNamespace(metas={}))
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)

    host._launch = fake_launch  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="trader.wallet_host"):
        host._handle_match(build_discovered(), 0.0)
        host._handle_match(build_discovered(), 600.0)

    record = host._matches_by_cid[CONDITION_ID]
    assert record.completed is True
    assert record.attempts == 0
    assert launched == []
    assert alerts == []
    skips = [rec.getMessage() for rec in caplog.records if "reason=steam_pin" in rec.getMessage()]
    assert len(skips) == 1


def test_run_scans_boot_before_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """The boot scan runs after engine.start and before the first discovery poll."""
    events: list[str] = []

    class FakeGateway:
        async def cancel_all(self) -> None:
            events.append("cancel_all")

    class FakeEngine:
        def __init__(self) -> None:
            self.gateway = FakeGateway()
            self.metas: dict[str, object] = {}

        async def start(self) -> None:
            events.append("start")

        async def shutdown(self) -> None:
            events.append("shutdown")

    host = _bare_host(FakeEngine())
    host._latch = ShutdownLatch()
    host._closed = False

    async def empty_boot() -> None:
        events.append("boot_scan")

    async def empty_poll(discoveries: object) -> Any:
        del discoveries
        events.append("discovery")
        return
        yield

    monkeypatch.setattr(host, "_boot_scan", empty_boot)
    monkeypatch.setattr("trader.wallet_host.cadence.poll_discoveries", empty_poll)
    monkeypatch.setattr("trader.wallet_host.MarketDiscovery", lambda *a, **k: object())
    monkeypatch.setattr("trader.wallet_host.pin_engine_identity", lambda engine: None)
    monkeypatch.setattr("trader.wallet_host.bind_user_fill_address", lambda engine: None)
    _idle_disir_catalog(monkeypatch)

    def close() -> None:
        events.append("close")
        host._closed = True

    host.close = close  # type: ignore[method-assign]
    asyncio.run(host.run())
    assert events.index("start") < events.index("boot_scan")
    assert events.index("boot_scan") < events.index("discovery")


def _run_engine(events: list[str]) -> object:
    """Minimal engine whose start/shutdown bookend a run() that reaches discovery."""

    class FakeGateway:
        async def cancel_all(self) -> None:
            events.append("cancel_all")

    class FakeEngine:
        def __init__(self) -> None:
            self.gateway = FakeGateway()
            self.metas: dict[str, object] = {}

        async def start(self) -> None:
            events.append("start")

        async def shutdown(self) -> None:
            events.append("shutdown")

    return FakeEngine()


def _idle_disir_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep run() from scanning Disir. The task waits until the test cancels it."""

    async def idle(self: object) -> None:
        del self
        await asyncio.Event().wait()

    monkeypatch.setattr("trader.wallet_host.DisirCatalog.run", idle)


def _patch_run_to_discovery(
    monkeypatch: pytest.MonkeyPatch, host: WalletHost, events: list[str]
) -> None:
    """Skip boot-scan seams and make poll_discoveries fire once."""
    _idle_disir_catalog(monkeypatch)

    async def empty_boot() -> None:
        events.append("boot_scan")

    async def empty_poll(discoveries: object) -> Any:
        del discoveries
        events.append("discovery")
        return
        yield

    monkeypatch.setattr(host, "_boot_scan", empty_boot)
    monkeypatch.setattr("trader.wallet_host.cadence.poll_discoveries", empty_poll)
    monkeypatch.setattr("trader.wallet_host.pin_engine_identity", lambda engine: None)
    monkeypatch.setattr("trader.wallet_host.bind_user_fill_address", lambda engine: None)

    def close() -> None:
        events.append("close")
        host._closed = True

    host.close = close  # type: ignore[method-assign]
    host._latch = ShutdownLatch()
    host._closed = False


def test_run_lol_paper_reaches_discovery_without_steam(monkeypatch: pytest.MonkeyPatch) -> None:
    """LoL-paper with steam_client=None still reaches poll_discoveries (US-007 wait is gone)."""
    events: list[str] = []
    host = _bare_host(_run_engine(events))
    host.steam_client = None
    host._games = (GAME_PROFILES["lol"],)
    host._archive_by_game = {"lol": Path("/tmp/lol")}
    host.archive_root = Path("/tmp/lol")
    monkeypatch.setattr("trader.wallet_host.MarketDiscovery", lambda *a, **k: object())
    _patch_run_to_discovery(monkeypatch, host, events)
    asyncio.run(host.run())
    assert "discovery" in events


def test_run_builds_one_discovery_per_assigned_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dota gets the process Steam client and the live blacklist; LoL gets neither."""
    events: list[str] = []
    constructed: list[tuple[str, bool, Path]] = []
    blacklist_by_game: dict[str, tuple[str, ...]] = {}

    class FakeDiscovery:
        def __init__(
            self,
            archive: Path,
            steam: object,
            probe: object,
            list_oddin: object,
            profile: GameProfile,
            title_blacklist: tuple[str, ...],
        ) -> None:
            del probe, list_oddin
            constructed.append((profile.game, steam is None, archive))
            blacklist_by_game[profile.game] = title_blacklist

    steam = SimpleNamespace()
    host = _bare_host(_run_engine(events))
    host._mode = "live"
    host.steam_client = steam  # type: ignore[assignment]
    host._games = (GAME_PROFILES["dota"], GAME_PROFILES["lol"])
    host._archive_by_game = {"dota": Path("/tmp/dota"), "lol": Path("/tmp/lol")}
    host.archive_root = Path("/tmp/dota")
    monkeypatch.setattr("trader.wallet_host.MarketDiscovery", FakeDiscovery)
    _patch_run_to_discovery(monkeypatch, host, events)
    asyncio.run(host.run())
    assert constructed == [
        ("dota", False, Path("/tmp/dota")),
        ("lol", True, Path("/tmp/lol")),
    ]
    assert blacklist_by_game == {"dota": LIVE_TITLE_BLACKLIST, "lol": ()}
    assert "discovery" in events


def _write_owner_meta(archive_dir: Path, match_id: str, condition_id: str, has_final: bool) -> None:
    """Write a schema-3 match.json that inspect_match_archive can attribute."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    final: dict[str, object] | None = None
    if has_final:
        final = {"duration_seconds": 100, "winner": "radiant"}
    document = {
        "schema_version": 3,
        "match_id": match_id,
        "final": final,
        "market": {
            "condition_id": condition_id,
            "yes_token_id": "YES",
            "no_token_id": "NO",
        },
    }
    (archive_dir / MATCH_META_FILENAME).write_text(json.dumps(document), encoding="utf-8")


def _game2_handoff() -> DiscoveredMatch:
    """Game 2 CID on the Steam id of map 1, with a GRID series so the archive is grid-*."""
    steam = build_discovered(grid_series_id="2996001", map_number=2)
    return replace(
        steam,
        match_id="grid-2996001-m2",
        steam_match_id="8977252978",
        market=replace(steam.market, condition_id="0xgame2", grid_series_id="2996001"),
    )


def test_canonical_grid_id_selects_grid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A grid-* archive id delay-picks GRID."""
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)

    async def fake_probe(series_id: str, map_number: int) -> int | None:
        del series_id, map_number
        return 8

    monkeypatch.setattr("trader.feed_selection.probe_grid_delay", fake_probe)
    handoff = replace(
        build_discovered(grid_series_id="2996001", map_number=2),
        match_id="grid-2996001-m2",
    )

    async def run() -> None:
        choice = await select_feed(handoff, _keep_map)
        assert choice is not None
        assert choice.feed.source is FeedSource.GRID

    asyncio.run(run())


def test_game2_on_map1_steam_id_launches_canonical_grid_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Game 2 does not inherit map 1's final; it starts under grid-{series}-m2."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    steam_id = "8977252978"
    _write_owner_meta(tmp_path / steam_id, steam_id, "0xgame1", True)
    g1_before = (tmp_path / steam_id / MATCH_META_FILENAME).read_text(encoding="utf-8")
    host = _bare_host(SimpleNamespace(metas={}))
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)
        target.task = cast(Any, SimpleNamespace(done=lambda: False))

    host._launch = fake_launch  # type: ignore[method-assign]
    host.reconcile((_game2_handoff(),))
    record = host._matches_by_cid["0xgame2"]
    assert record.completed is False
    assert record.match_id == "grid-2996001-m2"
    assert launched == ["grid-2996001-m2"]
    assert not (tmp_path / "grid-2996001-m2" / MATCH_META_FILENAME).exists()
    assert (tmp_path / steam_id / MATCH_META_FILENAME).read_text(encoding="utf-8") == g1_before


def test_occupied_steam_id_without_grid_waits_then_rebinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A foreign Steam archive without GRID id does not pin; a new Steam id rebinds."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    _write_owner_meta(tmp_path / MATCH_ID, MATCH_ID, "0xother", True)
    host = _bare_host(SimpleNamespace(metas={}))
    occupied = replace(
        build_discovered(),
        market=replace(build_discovered().market, condition_id="0xgame2", grid_series_id=None),
    )
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)
        target.task = cast(Any, SimpleNamespace(done=lambda: False))

    host._launch = fake_launch  # type: ignore[method-assign]
    with caplog.at_level(logging.INFO, logger="trader.wallet_host"):
        host._handle_match(occupied, 0.0)
        host._handle_match(occupied, 1.0)
        record = host._matches_by_cid["0xgame2"]
        assert record.completed is False
        assert record.is_pinned(tmp_path) is False
        assert launched == []
        skips = [
            rec.getMessage()
            for rec in caplog.records
            if "reason=occupied_other_cid" in rec.getMessage()
        ]
        assert len(skips) == 1
        fresh = replace(occupied, match_id="8977325814", steam_match_id="8977325814")
        host._handle_match(fresh, 2.0)
    assert record.match_id == "8977325814"
    assert record.handoff.match_id == record.identity.match_id == "8977325814"
    assert launched == ["8977325814"]
    rebind_lines = [
        rec.getMessage() for rec in caplog.records if "trader rebind:" in rec.getMessage()
    ]
    assert rebind_lines
    assert "old_match_id=" in rebind_lines[0]
    assert "new_match_id=" in rebind_lines[0]


def test_legacy_unfinished_resume_keeps_numeric_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Own unfinished Steam archive resumes under the numeric id; grid-* is not created."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    _write_owner_meta(tmp_path / MATCH_ID, MATCH_ID, CONDITION_ID, False)
    host = _bare_host(SimpleNamespace(metas={}))
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)
        target.task = cast(Any, SimpleNamespace(done=lambda: False))

    host._launch = fake_launch  # type: ignore[method-assign]
    discovered = replace(
        build_discovered(grid_series_id="2996001"),
        match_id="grid-2996001-m1",
        steam_match_id=MATCH_ID,
    )
    host._handle_match(discovered, 0.0)
    record = host._matches_by_cid[CONDITION_ID]
    assert record.match_id == MATCH_ID
    assert launched == [MATCH_ID]
    assert not (tmp_path / "grid-2996001-m1").exists()
    assert record.match_id == record.handoff.match_id == record.identity.match_id


def test_duplicate_owned_archives_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Canonical and legacy archives of the same CID do not pick one automatically."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    canonical = "grid-2996001-m1"
    _write_owner_meta(tmp_path / MATCH_ID, MATCH_ID, CONDITION_ID, False)
    _write_owner_meta(tmp_path / canonical, canonical, CONDITION_ID, False)
    host = _bare_host(SimpleNamespace(metas={}))
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)

    host._launch = fake_launch  # type: ignore[method-assign]
    discovered = replace(
        build_discovered(grid_series_id="2996001"),
        match_id=canonical,
        steam_match_id=MATCH_ID,
    )
    with caplog.at_level(logging.INFO, logger="trader.wallet_host"):
        host._handle_match(discovered, 0.0)
    assert launched == []
    assert host._matches_by_cid[CONDITION_ID].completed is False
    assert any("reason=duplicate_owned_archives" in rec.getMessage() for rec in caplog.records)


def test_canonical_id_collision_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A grid-* archive owned by another CID does not start this market."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    canonical = "grid-2996001-m1"
    _write_owner_meta(tmp_path / canonical, canonical, "0xother", False)
    host = _bare_host(SimpleNamespace(metas={}))
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)

    host._launch = fake_launch  # type: ignore[method-assign]
    discovered = replace(
        build_discovered(grid_series_id="2996001"),
        match_id=canonical,
        steam_match_id=MATCH_ID,
    )
    with caplog.at_level(logging.INFO, logger="trader.wallet_host"):
        host._handle_match(discovered, 0.0)
    assert launched == []
    assert any("reason=canonical_id_collision" in rec.getMessage() for rec in caplog.records)
    assert any("archive_cid=0xother" in rec.getMessage() for rec in caplog.records)


def test_unreadable_archive_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A match.json this build cannot read is not overwritten and does not launch."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    archive = tmp_path / MATCH_ID
    archive.mkdir()
    (archive / MATCH_META_FILENAME).write_text("{", encoding="utf-8")
    original = (archive / MATCH_META_FILENAME).read_text(encoding="utf-8")
    host = _bare_host(SimpleNamespace(metas={}))
    launched: list[str] = []

    def fake_launch(target: _ManagedMatch) -> None:
        launched.append(target.match_id)

    host._launch = fake_launch  # type: ignore[method-assign]
    with caplog.at_level(logging.INFO, logger="trader.wallet_host"):
        host._handle_match(build_discovered(), 0.0)
    assert launched == []
    assert any("reason=unreadable_archive" in rec.getMessage() for rec in caplog.records)
    assert (archive / MATCH_META_FILENAME).read_text(encoding="utf-8") == original


def test_reap_ignores_foreign_final_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finished task completes only on this CID's final or execution_cleanup.json."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.archive_paths.TRADER_DIR", tmp_path)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered()
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record

    class _CleanTask:
        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            return None

    _write_owner_meta(tmp_path / MATCH_ID, MATCH_ID, "0xother", True)
    write_execution_cleanup(tmp_path / MATCH_ID, MATCH_ID, "0xother")
    record.task = cast(Any, _CleanTask())
    host._reap_finished_tasks(1_000.0)
    assert record.completed is False
    record.task = cast(Any, _CleanTask())
    record.no_snapshot = True
    _write_owner_meta(tmp_path / MATCH_ID, MATCH_ID, CONDITION_ID, True)
    host._reap_finished_tasks(1_000.0)
    assert record.completed is True
    record.completed = False
    record.task = cast(Any, _CleanTask())
    write_execution_cleanup(tmp_path / MATCH_ID, MATCH_ID, CONDITION_ID)
    _write_owner_meta(tmp_path / MATCH_ID, MATCH_ID, "0xother", True)
    host._reap_finished_tasks(1_000.0)
    assert record.completed is True


def test_decision_logs_include_required_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """launch and feed_selected lines carry the grep fields."""
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    host = _bare_host(SimpleNamespace(metas={}))
    discovered = build_discovered(grid_series_id="2996001")

    async def fake_probe(series_id: str, map_number: int) -> int | None:
        del series_id, map_number
        return 8

    monkeypatch.setattr("trader.feed_selection.probe_grid_delay", fake_probe)

    class _IdleWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def run(self) -> None:
            return

    monkeypatch.setattr("trader.wallet_host.MatchWorker", _IdleWorker)
    record = _record_for(discovered)
    host._matches_by_cid[CONDITION_ID] = record

    async def run() -> None:
        host._launch(record)
        task = record.task
        assert task is not None
        await task

    with caplog.at_level(logging.INFO):
        asyncio.run(run())
    messages = [rec.getMessage() for rec in caplog.records]
    feed_lines = [line for line in messages if "trader feed_selected:" in line]
    launch_lines = [line for line in messages if "trader launch:" in line]
    assert feed_lines
    assert "feed_source=" in feed_lines[0]
    assert "grid_delay_s=8" in feed_lines[0]
    assert "cid=" in feed_lines[0]
    assert launch_lines
    assert "archive_id_kind=" in launch_lines[0]
    assert "cid=" in launch_lines[0]


def _confirmed_fill(store: WalletStateStore) -> tuple[str, Fill]:
    """Insert one CONFIRMED YES BUY and return its key and fill."""
    processor = WalletFillProcessor(store)
    key = fill_key("t1", "o1")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.4, 10.0, key, TradeState.CONFIRMED, 1.0),
        CONDITION_ID,
    )
    fill = store.fill_for_key(key)
    assert fill is not None
    return key, fill


def _closed_archive(tmp_path: Path) -> Path:
    """A finished match archive on disk: match.json plus a session.jsonl ending at session_end."""
    archive = tmp_path / MATCH_ID
    archive.mkdir()
    _write_final_meta(archive, MATCH_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN)
    (archive / "session.jsonl").write_text(
        '{"kind":"session_end","terminal_reason":"finished"}\n', encoding="utf-8"
    )
    return archive


def test_dispatch_fill_without_worker_writes_late_fill(tmp_path: Path) -> None:
    """A CONFIRMED fill with no live worker appends late_fill and acks the outbox."""
    store = WalletStateStore(tmp_path / "wallet.db")
    key, fill = _confirmed_fill(store)
    archive = _closed_archive(tmp_path)
    engine = SimpleNamespace(state=store, _token_cid={}, metas={}, est={})
    host = _outbox_host(engine, store)
    host._archives = ArchivedMarketIndex((tmp_path,))
    host._dispatch_fill(fill)
    assert all(item.event != "confirmed" for item in store.pending_outbox())
    records = [
        json.loads(line)
        for line in (archive / "session.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert records[-1]["kind"] == "late_fill"
    assert records[-1]["fill_key"] == key
    assert records[-1]["source"] == "user_ws"
    store.close()


def test_drain_outbox_without_worker_writes_late_fill(tmp_path: Path) -> None:
    """drain_outbox late-fills a closed archive and acks the confirmed row."""
    store = WalletStateStore(tmp_path / "wallet.db")
    key, _fill = _confirmed_fill(store)
    archive = _closed_archive(tmp_path)
    engine = SimpleNamespace(state=store, _token_cid={}, metas={}, est={})
    host = _outbox_host(engine, store)
    host._archives = ArchivedMarketIndex((tmp_path,))
    host.drain_outbox()
    assert all(item.event != "confirmed" for item in store.pending_outbox())
    records = [
        json.loads(line)
        for line in (archive / "session.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert records[0]["kind"] == "session_end"
    assert records[-1]["kind"] == "late_fill"
    assert records[-1]["fill_key"] == key
    store.close()


@pytest.mark.parametrize("mode", ["live", "paper"])
@pytest.mark.parametrize("contents", [None, "{", '{"leagues":[],"aliases":{}}'])
def test_invalid_lol_whitelist_prevents_engine_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: ExecutionMode, contents: str | None
) -> None:
    events: list[str] = []
    host = _bare_host(_run_engine(events))
    host.mode = mode
    host.steam_client = None
    host._games = (GAME_PROFILES["lol"],)
    host._archive_by_game = {"lol": tmp_path}
    whitelist = tmp_path / "whitelist.json"
    if contents is not None:
        whitelist.write_text(contents)
    monkeypatch.setattr("shared.utils.lol_leagues.LOL_LEAGUE_WHITELIST_PATH", whitelist)
    with pytest.raises(ValueError, match=r"whitelist|leagues"):
        asyncio.run(host.run())
    assert events == []


def test_missing_oddin_token_stops_before_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """No brand token means the process never opens the engine."""
    events: list[str] = []
    host = _bare_host(_run_engine(events))
    host._games = ()

    def no_token(name: str) -> None:
        del name
        return None

    monkeypatch.setattr("trader.oddin_client.env_value", no_token)
    with pytest.raises(OddinFeedError, match="ODDIN_BRAND_TOKEN"):
        asyncio.run(host.run())
    assert events == []


@pytest.mark.parametrize("mode", ["live", "paper"])
def test_lol_start_logs_whitelist_before_engine_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mode: ExecutionMode,
) -> None:
    events: list[str] = []
    host = _bare_host(_run_engine(events))
    host.mode = mode
    host.steam_client = None
    host._games = (GAME_PROFILES["lol"],)
    host._archive_by_game = {"lol": tmp_path}
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text('{"leagues":["LEC"],"aliases":{}}')
    monkeypatch.setattr("shared.utils.lol_leagues.LOL_LEAGUE_WHITELIST_PATH", whitelist)
    caplog.set_level(logging.INFO, logger="shared.utils.lol_leagues")
    _patch_run_to_discovery(monkeypatch, host, events)
    asyncio.run(host.run())
    assert "start" in events and "discovery" in events
    assert str(whitelist) in caplog.text
    assert sha256_file(whitelist) in caplog.text


def _credit(rig: BuyRig, *, qty: float, key: str, confirmed: bool) -> None:
    fill = Fill(YES_TOKEN, Side.BUY, rig.price, qty, key, 1.0, is_maker=True)
    if confirmed:
        rig.store.apply_confirmed_fill(fill, key)
    else:
        rig.store.apply_matched_fill(fill, key)
    rig.core.note_fill(
        fill_key=key,
        venue_id=rig.venue_id,
        qty=qty,
        price=rig.price,
        now_ns=1,
        token_index=0,
        side="BUY",
    )
    rig.core.drain_apply()


def test_durable_cancel_commits_the_row_before_notify(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    real = MatchWorker.note_cancel_result

    def notify(self: MatchWorker, venue_ids: list[str], ok: bool) -> None:
        assert venue_ids == [rig.venue_id] and ok is True
        seen = sqlite3.connect(rig.path)
        try:
            outcome = seen.execute(
                "SELECT outcome FROM core_commands WHERE core_order_id=?",
                (rig.order_id,),
            ).fetchone()
            row = seen.execute(
                "SELECT qty FROM unsettled_buys WHERE venue_id=?",
                (rig.venue_id,),
            ).fetchone()
        finally:
            seen.close()
        assert outcome is not None and outcome[0] == "canceled"
        assert row is not None and row[0] == rig.qty
        real(self, venue_ids, ok)

    rig.worker.note_cancel_result = notify.__get__(rig.worker, MatchWorker)
    run_cancel(rig, [rig.venue_id])
    assert rig.core.reserved_buy_notional() == 0.0
    assert unsettled_buy_notional(rig.store._conn, None) == pytest.approx(rig.qty * rig.price)
    assert _budget(rig).cash_usdc == pytest.approx(1000.0 - rig.qty * rig.price)
    rig.store.close()


def test_durable_cancel_rolls_back_when_the_row_insert_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = open_buy_rig(tmp_path)
    called = False

    def boom(self: MatchWorker, venue_ids: list[str], ok: bool) -> None:
        nonlocal called
        called = True

    rig.worker.note_cancel_result = boom.__get__(rig.worker, MatchWorker)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("row")

    monkeypatch.setattr("trader.wallet_host.insert_unsettled_buy", fail)
    with pytest.raises(RuntimeError, match="row"):
        run_cancel(rig, [rig.venue_id])
    assert called is False
    assert rig.core.state.orders[0].status == "live"
    command = rig.store._conn.execute(
        "SELECT outcome, dispatch_state FROM core_commands WHERE core_order_id=?",
        (rig.order_id,),
    ).fetchone()
    assert command is not None
    assert command["outcome"] == ""
    assert command["dispatch_state"] == "prepared"
    count = rig.store._conn.execute("SELECT COUNT(*) FROM unsettled_buys").fetchone()
    assert count is not None and count[0] == 0
    rig.store.close()


def test_durable_cancel_routes_buy_and_sell(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    add_order(rig, order_id="c-sell", venue_id="venue-sell", side="SELL", qty=5.0, price=0.4)
    run_cancel(rig, [rig.venue_id, "venue-sell"])
    assert get_unsettled_buy(rig.store._conn, rig.venue_id) is not None
    assert get_unsettled_buy(rig.store._conn, "venue-sell") is None
    assert rig.core.state.orders[0].status == "gone"
    assert all(order.order_id != "c-sell" for order in rig.core.state.orders)
    assert rig.venue_id not in rig.store.orders
    rig.store.close()


def test_durable_cancel_false_times_out_without_a_row(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    assert run_cancel(rig, [rig.venue_id], ok=False) is False
    assert get_unsettled_buy(rig.store._conn, rig.venue_id) is None
    assert rig.core.state.orders[0].status == "unknown"
    outcome = rig.store._conn.execute(
        "SELECT outcome FROM core_commands WHERE core_order_id=?",
        (rig.order_id,),
    ).fetchone()
    assert outcome is not None and outcome[0] == "timeout"
    rig.store.close()


def test_durable_cancel_keeps_ws_proof_and_survives_a_detached_core(tmp_path: Path) -> None:
    rig = open_buy_rig(tmp_path)
    key = f"t1:{rig.venue_id}"

    def during(_ids: list[str]) -> None:
        _credit(rig, qty=rig.qty, key=key, confirmed=False)

    run_cancel(rig, [rig.venue_id], during=during)
    row = get_unsettled_buy(rig.store._conn, rig.venue_id)
    assert row is not None
    assert row.qty == rig.qty and row.proven is False and row.resolved is False
    assert unsettled_buy_notional(rig.store._conn, None) == 0.0
    assert all(order.order_id != rig.order_id for order in rig.core.state.orders)
    assert any(record.order_id == rig.order_id for record in rig.core.state.records)
    rig.store.close()

    confirmed_dir = tmp_path / "confirmed"
    confirmed_dir.mkdir()
    confirmed = open_buy_rig(confirmed_dir)

    def during_confirmed(_ids: list[str]) -> None:
        _credit(confirmed, qty=confirmed.qty, key=f"t1:{confirmed.venue_id}", confirmed=True)

    run_cancel(confirmed, [confirmed.venue_id], during=during_confirmed)
    closed = get_unsettled_buy(confirmed.store._conn, confirmed.venue_id)
    assert closed is not None and closed.resolved is True and closed.qty == confirmed.qty
    assert all(order.order_id != confirmed.order_id for order in confirmed.core.state.orders)
    confirmed.store.close()

    detached_dir = tmp_path / "detached"
    detached_dir.mkdir()
    detached = open_buy_rig(detached_dir)
    captured = detached.core

    def during_detach(_ids: list[str]) -> None:
        detached.worker._core = None

    run_cancel(detached, [detached.venue_id], during=during_detach)
    kept = get_unsettled_buy(detached.store._conn, detached.venue_id)
    assert kept is not None and kept.qty == detached.qty and kept.proven is False
    assert captured.state.orders[0].status == "live"
    detached.store.close()
