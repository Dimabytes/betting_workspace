"""Shared builders and fakes for the live paper session tests.

Everything here is offline and deterministic: real fork types, a real
`Engine` built in memory (never `Engine.start`, no websocket, no HTTP, no
collector process, no live feed) plus small fakes for the model server, the
market data service and the engine. Every session test module builds its
world from these.
"""

# The session composes private engine seams by design (bind_fill_sink,
# _recompute_locked, _locks, regime proxy, MDS callbacks); pinning them is the
# point of these tests. The production session modules are the only place that
# uses them.
# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from polymaker.catalog.store import CatalogStore
from polymaker.config import Config, RiskConfig, StrategyProfile
from polymaker.domain import Fill, MarketMeta, Quote, Side, TokenMeta
from polymaker.engine import Engine
from polymaker.journal import Journal
from polymaker.marketdata.orderbook import OrderBook
from polymaker.marketdata.parse import TradePrint
from polymaker.risk.manager import RiskManager
from polymaker.state.store import StateStore
from polymaker.strategy.regime import RegimeMachine

from shared.utils.board_features import BoardFeatures
from shared.utils.dota_features import SnapshotHistory
from shared.utils.top_players import ZERO_TOP
from trader import (
    session_journal,
    session_types,
)
from trader.bindings import (
    DiscoveredMatch,
    MarketReference,
    ModelReference,
    TeamSides,
)
from trader.collector_sidecars import MarketKind
from trader.core_trace import CoreTrace
from trader.engine_seams import BookReadiness
from trader.game_profile import GAME_PROFILES
from trader.live_feed import FeedEvent, FeedSource, GameSnapshot, LiveFeed, MatchPhase
from trader.lol_prior import LolPriorTape
from trader.match_worker import MatchWorker
from trader.model_server import ModelPrediction, ModelPredictionError
from trader.paper_gateway import PaperGateway
from trader.session_config import materialize_wallet_config_dir, read_template
from trader.session_engine import StrategyCell


def _test_model_sha(game: str) -> str:
    """Stable fake catalog identity so session tests do not hash live production/."""
    return f"test-model-sha-{game}"


BOTH = (GAME_PROFILES["dota"], GAME_PROFILES["lol"])

CONDITION_ID = "0xCOND"
YES_TOKEN = "TOKEN0"
NO_TOKEN = "TOKEN1"
MATCH_ID = "8944931337"
HORN_UNIX_SECONDS = 1_786_798_842 + 2_000_000_000

# ── fixtures and builders ──────────────────────────────────────────────────


def build_discovered(
    yes_is_radiant: bool = True,
    market_kind: MarketKind = "map_winner",
    map_number: int = 1,
    grid_series_id: str | None = None,
    game: str = "dota",
) -> DiscoveredMatch:
    """One fixed discovered match bound to the two canonical tokens.

    The explicit immutable market kind is carried like discovery does it;
    None models a legacy handoff without a kind.
    """
    return DiscoveredMatch(
        match_id=MATCH_ID,
        game=game,
        steam_match_id=MATCH_ID,
        league_id=19719,
        tournament=None,
        sides=TeamSides(radiant="Aurora", dire="Team Secret"),
        map_number=map_number,
        market=MarketReference(
            condition_id=CONDITION_ID,
            market_slug="dota2-aurora-secret-game1",
            event_slug="dota2-aurora-secret",
            yes_token_id=YES_TOKEN,
            no_token_id=NO_TOKEN,
            yes_is_radiant=yes_is_radiant,
            outcome_0_name="Aurora",
            outcome_1_name="Team Secret",
            tick_size="0.01",
            min_order_size="5",
            neg_risk=False,
            grid_series_id=grid_series_id,
        ),
        market_kind=market_kind,
    )


def build_event(
    second: int,
    *,
    paused: bool = False,
    phase: MatchPhase = MatchPhase.IN_PROGRESS,
    radiant_nw_adv: int = 1_000,
    yes_is_radiant: bool = True,
) -> FeedEvent:
    """One fixed feed event with the given snapshot fields."""
    snapshot = GameSnapshot(
        second=second,
        server_timestamp=2_000_000_000 + second,
        phase=phase,
        radiant_nw_adv=radiant_nw_adv,
        radiant_nw=3_000,
        dire_nw=2_000,
        radiant_xp_adv=2_000,
        deaths_radiant=1,
        deaths_dire=3,
        top=ZERO_TOP,
        paused=paused,
    )
    return FeedEvent(
        snapshot=snapshot,
        received_at_utc="2026-08-14T12:00:00.100000Z",
        source=FeedSource.GRID,
        horn_unix_seconds=HORN_UNIX_SECONDS,
        yes_is_radiant=yes_is_radiant,
    )


class FakeLiveFeed:
    """Scripted LiveFeed: yields the given events, then optionally hangs or raises."""

    source: FeedSource = FeedSource.GRID
    stale_seconds: float = 3.0

    def __init__(
        self,
        events: tuple[FeedEvent, ...] = (),
        hang: bool = False,
        error: BaseException | None = None,
    ) -> None:
        """Bind the scripted ticks and the optional hang/error after they end."""
        self._events = events
        self._hang = hang
        self._error = error

    async def ticks(self) -> AsyncIterator[FeedEvent]:
        """Yield each scripted event once, then hang or raise if asked."""
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error
        if self._hang:
            await asyncio.Future()


def make_meta(
    tick_size: float = 0.01,
    min_order_size: float = 5.0,
    condition_id: str = CONDITION_ID,
    question: str | None = "Dota 2: Aurora vs Team Secret - Game 1 Winner",
) -> MarketMeta:
    """One full fork MarketMeta matching the fixed discovered binding."""
    return MarketMeta(
        condition_id=condition_id,
        question=question or "dota2-aurora-secret-game1",
        slug="dota2-aurora-secret-game1",
        tokens=(TokenMeta(YES_TOKEN, "Aurora"), TokenMeta(NO_TOKEN, "Team Secret")),
        tick_size=tick_size,
        neg_risk=False,
        min_order_size=min_order_size,
        rewards_min_size=0.0,
        rewards_max_spread=0.0,
        rewards_daily_rate=0.0,
        maker_fee_bps=0,
        taker_fee_bps=0,
        fees_enabled=False,
        end_date_iso=None,
        event_id="808454",
    )


def sidecar_body(**overrides: object) -> dict[str, object]:
    """One valid collector-v1 sidecar for the fixed discovered binding."""
    body: dict[str, object] = {
        "schemaVersion": 1,
        "eventId": "808454",
        "eventSlug": "dota2-aurora-secret",
        "conditionId": CONDITION_ID,
        "marketSlug": "dota2-aurora-secret-game1",
        "question": "Dota 2: Aurora vs Team Secret - Game 1 Winner",
        "marketKind": "map_winner",
        "mapNumber": 1,
        "outcomes": [
            {"index": 0, "name": "Aurora", "tokenId": YES_TOKEN},
            {"index": 1, "name": "Team Secret", "tokenId": NO_TOKEN},
        ],
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "tickSize": "0.01",
        "minOrderSize": "5",
        "negRisk": False,
        "gridSeriesId": None,
    }
    body.update(overrides)
    return body


def write_sidecar(root: Path, body: dict[str, object]) -> Path:
    """Write one sidecar file into the collector archive layout."""
    markets = root / "metadata" / "markets"
    markets.mkdir(parents=True, exist_ok=True)
    condition_id = cast(str, body["conditionId"])
    path = markets / f"{condition_id}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


BookLevels = tuple[list[tuple[float, float]], list[tuple[float, float]]]


def make_binding(
    market_kind: MarketKind = "map_winner",
    map_number: int | None = 1,
    grid_series_id: str | None = None,
) -> session_types.SidecarBinding:
    """One immutable sidecar binding matching the default sidecar body."""
    return session_types.SidecarBinding(
        condition_id=CONDITION_ID,
        market_slug="dota2-aurora-secret-game1",
        event_slug="dota2-aurora-secret",
        event_id="808454",
        market_kind=market_kind,
        map_number=map_number,
        outcome_0_name="Aurora",
        outcome_0_token=YES_TOKEN,
        outcome_1_name="Team Secret",
        outcome_1_token=NO_TOKEN,
        neg_risk=False,
        grid_series_id=grid_series_id,
    )


def valid_levels() -> tuple[BookLevels, BookLevels]:
    """A standard valid YES/NO pair: mids 0.485 and 0.505."""
    yes_levels: BookLevels = ([(0.47, 10.0)], [(0.50, 10.0)])
    no_levels: BookLevels = ([(0.49, 10.0)], [(0.52, 10.0)])
    return yes_levels, no_levels


def seed_real_engine(engine: Engine, meta: MarketMeta) -> None:
    """Seed the per-market state a real (never-started) engine needs to recompute."""
    condition_id = meta.condition_id
    engine.metas[condition_id] = meta
    engine.profiles[condition_id] = StrategyProfile()
    engine.est[condition_id] = engine._make_estimators(engine.profiles[condition_id])
    engine.regime_m[condition_id] = RegimeMachine()
    engine._dirty[condition_id] = asyncio.Event()
    engine._locks[condition_id] = asyncio.Lock()
    engine._sweep[condition_id] = False
    engine._token_cid[meta.yes.token_id] = condition_id
    engine._token_cid[meta.no.token_id] = condition_id
    engine.md.set_markets([(condition_id, [meta.yes.token_id, meta.no.token_id])])


def seed_books(engine: Engine, meta: MarketMeta) -> None:
    """Seed the two in-memory books with the standard valid levels."""
    yes_levels, no_levels = valid_levels()
    for token_id, (bids, asks) in ((meta.yes.token_id, yes_levels), (meta.no.token_id, no_levels)):
        book = engine.md.book(token_id)
        assert book is not None
        book.set_tick_size(meta.tick_size)
        book.apply_snapshot(bids, asks, 100.0, "hash")


class FakeModelServer:
    """A model stand-in: fixed Radiant fair, optional first-N prediction failures."""

    def __init__(self, radiant_fair: float = 0.5, raise_first: int = 0) -> None:
        self.model_reference = ModelReference(name="fake-model", trained_at="2026-01-01T00:00:00Z")
        self._fair = radiant_fair
        self._raise_first = raise_first
        self.calls = 0
        self.board_args: list[BoardFeatures] = []
        self.fair_args: list[tuple[float, float]] = []

    def predict_fair(
        self,
        snapshot: GameSnapshot,
        market_p_radiant: float,
        market_radiant_prior: float,
        history: SnapshotHistory,
        board: BoardFeatures,
    ) -> ModelPrediction:
        """Count every call; the first `raise_first` calls fail like the real server."""
        del snapshot, history
        self.board_args.append(board)
        self.fair_args.append((market_p_radiant, market_radiant_prior))
        self.calls += 1
        if self.calls <= self._raise_first:
            raise ModelPredictionError("fake model error")
        return ModelPrediction(raw_delta=self._fair - market_p_radiant, fair=self._fair)


def _ignore_dirty(_condition_id: str, _token_id: str) -> None:
    """Default MDS dirty callback stand-in."""


def _ignore_trade(_trade: TradePrint) -> None:
    """Default MDS trade callback stand-in."""


class FakeMd:
    """Market-data stand-in: in-memory books plus assignable callbacks."""

    def __init__(self, raise_book_call: int = 0) -> None:
        self.books: dict[str, OrderBook] = {}
        self._on_dirty: Callable[[str, str], None] = _ignore_dirty
        self._on_trade: Callable[[TradePrint], None] = _ignore_trade
        self._raise_book_call = raise_book_call
        self.book_calls = 0

    def book(self, token_id: str) -> OrderBook | None:
        """Return one book; an injected call index raises once (trading fault)."""
        self.book_calls += 1
        if self._raise_book_call and self.book_calls == self._raise_book_call:
            self._raise_book_call = 0
            raise RuntimeError("simulated book read fault")
        return self.books.get(token_id)


class FakeEngine:
    """No-network stand-in for the exact Engine surface the session composes.

    StateStore, CatalogStore, RiskManager and PaperGateway are all real fork
    types on a temp SQLite file; the market data service is the FakeMd above,
    and start/shutdown are local. `trigger_bridge_on_start` replays one book
    update through the installed MDS bridge (used to inject gateway faults),
    `fail_bridge_once` makes the gateway's book processor raise once, and
    `fail_cancel_once` makes the gateway's cancel raise once (installed
    before the session seams so the journaling wrapper captures it).
    """

    def __init__(
        self,
        cfg: Config,
        meta: MarketMeta,
        *,
        raise_book_call: int = 0,
        trigger_bridge_on_start: bool = False,
        fail_bridge_once: bool = False,
        fail_cancel_once: bool = False,
        seed_entry_buy: bool = False,
    ) -> None:
        self.cfg = cfg
        self.state = StateStore(cfg.paths.db)
        self.catalog = CatalogStore(cfg.paths.db)
        self.risk = RiskManager(RiskConfig(), self.state)
        self.md = FakeMd(raise_book_call=raise_book_call)
        self.journal = Journal(cfg.paths.journal_dir, enabled=False)
        self.gateway = PaperGateway(cfg, self.journal, paper=True)
        self.metas = {meta.condition_id: meta}
        self.profiles = {meta.condition_id: StrategyProfile()}
        self.est: dict[str, object] = {}
        self.regime_m = {meta.condition_id: RegimeMachine()}
        self._dirty = {meta.condition_id: asyncio.Event()}
        self._locks = {meta.condition_id: asyncio.Lock()}
        self._token_cid = {
            meta.yes.token_id: meta.condition_id,
            meta.no.token_id: meta.condition_id,
        }
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._task_specs: dict[str, object] = {}
        self._running = False
        self._sweep: dict[str, bool] = {}
        self._last_quote_fv: dict[str, float] = {}
        self._merging: set[str] = set()
        self._halted: set[str] = set()
        self._aux_tasks: list[asyncio.Task[Any]] = []
        self.condition_id = meta.condition_id
        self.yes_token = meta.yes.token_id
        self.no_token = meta.no.token_id
        self._raise_book_call = raise_book_call
        self._trigger_bridge_on_start = trigger_bridge_on_start
        self._fail_bridge_once = fail_bridge_once
        self._cancel_fault_armed = fail_cancel_once
        self._seed_entry_buy = seed_entry_buy
        self.wake_calls = 0
        self.shutdown_calls = 0
        if fail_cancel_once:
            original_cancel = self.gateway.cancel

            async def failing_cancel(order_ids: list[str]) -> bool:
                if self._cancel_fault_armed:
                    self._cancel_fault_armed = False
                    raise RuntimeError("simulated cancel fault")
                return await original_cancel(order_ids)

            self.gateway.cancel = failing_cancel
        yes_book = OrderBook(tick_size=meta.tick_size)
        yes_book.apply_snapshot([(0.47, 10.0)], [(0.50, 10.0)], 100.0, "hash")
        no_book = OrderBook(tick_size=meta.tick_size)
        no_book.apply_snapshot([(0.49, 10.0)], [(0.52, 10.0)], 100.0, "hash")
        self.md.books[meta.yes.token_id] = yes_book
        self.md.books[meta.no.token_id] = no_book

    def _on_fill(self, fill: Fill) -> None:
        """The engine fill callback seam the session binds."""
        self.risk.note_fill(fill)

    def _wake_cid(self, condition_id: str) -> None:
        """Wake like the real engine; counted for the exactly-once tests."""
        self.wake_calls += 1
        self._dirty[condition_id].set()

    async def start(self) -> None:
        """Local start; optionally replay one book update through the bridge."""
        if self._fail_bridge_once:
            self._fail_bridge_once = False
            original = self.gateway.process_book_update

            def failing_book(token_id: str, book: OrderBook) -> list[Fill]:
                raise RuntimeError("simulated gateway fault")

            self.gateway.process_book_update = failing_book
            del original
        if self._trigger_bridge_on_start:
            self.md._on_dirty(self.condition_id, self.yes_token)
        if self._seed_entry_buy:
            placed = await self.gateway.place(
                [Quote(self.yes_token, Side.BUY, 0.45, 5.0)], self.metas[self.condition_id]
            )
            for order in placed:
                self.state.upsert_order(order)

    async def shutdown(self) -> None:
        """Local shutdown."""
        self.shutdown_calls += 1


class FakeWalletHost:
    """Minimal host surface MatchWorker sidecar/watchdog/lifecycle tests drive."""

    def __init__(self, engine: FakeEngine, archive_root: Path, git_commit: str) -> None:
        self.engine = engine
        self.lol_prior = LolPriorTape()
        self.archive_root = archive_root
        self.git_commit = git_commit
        self.clip_tables = read_template(BOTH).clips
        self.store = engine.state
        self.readiness = BookReadiness()
        self.steam_client = object()
        self._worker_by_cid: dict[str, MatchWorker] = {}
        self._worker_by_token: dict[str, MatchWorker] = {}
        self._tokens_by_cid: dict[str, set[str]] = {}
        self._cells: dict[str, StrategyCell] = {}
        self.cores: dict[str, object] = {}
        self._quiet: set[str] = set()
        self.cancelled_cids: list[str] = []
        self.detached_cids: list[str] = []
        self.keep_quiet_calls: list[str] = []
        self.drain_calls = 0
        self.fence_result = True

    def archive_for(self, game: str) -> Path:
        """MatchWorker provenance uses this; FakeWalletHost has one archive."""
        del game
        return self.archive_root

    def cid_in_use(self, cid: str) -> bool:
        """True when a worker or Engine market already owns this condition id."""
        return cid in self._worker_by_cid or cid in self.engine.metas

    def register_worker(self, cid: str, tokens: set[str], worker: MatchWorker) -> None:
        """Route fills and replay pending outbox rows after attach."""
        current = self._worker_by_cid.get(cid)
        if current is not None and current is not worker:
            raise RuntimeError("condition already has a live worker")
        self._worker_by_cid[cid] = worker
        self._tokens_by_cid[cid] = set(tokens)
        self._cells[cid] = worker._cell
        if worker.core is not None:
            self.cores[cid] = worker.core
        for token_id in tokens:
            self._worker_by_token[token_id] = worker
        self.drain_outbox()

    def unregister_worker(self, cid: str) -> None:
        """Drop the live worker. Quiet leftover keeps the cell."""
        worker = self._worker_by_cid.pop(cid, None)
        tokens = self._tokens_by_cid.get(cid, set())
        for token_id in tokens:
            mapped = self._worker_by_token.get(token_id)
            if mapped is worker:
                self._worker_by_token.pop(token_id, None)
        if cid not in self._quiet:
            self._cells.pop(cid, None)
            self._tokens_by_cid.pop(cid, None)

    def keep_quiet(self, cid: str) -> None:
        """Hold an attached market without a quoter when the exit fence is unproven."""
        self._quiet.add(cid)
        self.keep_quiet_calls.append(cid)
        cell = self._cells.get(cid)
        if cell is not None:
            cell.clear()

    def drain_outbox(self) -> None:
        """Count drain so attach-before-replay tests can see the order."""
        self.drain_calls += 1

    async def cancel_market(self, cid: str) -> None:
        """Record a scoped market cancel."""
        self.cancelled_cids.append(cid)

    async def fence_market(self, token_ids: set[str], timeout_s: float) -> bool:
        """Return the scripted fence result. Token set is unused in the fake."""
        del token_ids, timeout_s
        return self.fence_result

    async def detach(self, cid: str) -> None:
        """Drop the market from the fake Engine metas."""
        self.engine.metas.pop(cid, None)
        self._quiet.discard(cid)
        self._cells.pop(cid, None)
        self._tokens_by_cid.pop(cid, None)
        self.detached_cids.append(cid)


def close_fake_engine(fake: FakeEngine) -> None:
    """Close the real fork stores a FakeEngine opened."""
    fake.state.close()
    fake.catalog.close()
    fake.journal.close()
    fake.gateway.close()


def build_attached_worker(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    *,
    archive_root: Path | None = None,
    meta: MarketMeta | None = None,
    tick_str: str = "0.01",
    model: object | None = None,
    git_commit: str = "test-commit",
    prior: float | None = 0.5,
    prior_ready: bool = True,
    feed: LiveFeed | None = None,
    feed_timeout_seconds: float = 3.0,
    exit_timeout_seconds: float | None = None,
    trace: CoreTrace | None = None,
    level_usdc: float = 65.0,
    game: str = "dota",
) -> tuple[Any, FakeEngine]:
    """One MatchWorker attached to a FakeEngine, ready for sidecar/watchdog tests."""
    patcher = patch("trader.core_trace.model_file_sha", _test_model_sha)
    patcher.start()
    request.addfinalizer(patcher.stop)
    meta = meta or make_meta()
    archive_root = archive_root or tmp_path / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_dir = tmp_path / "match_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    owner = materialize_wallet_config_dir(
        archive_dir / "paper_state.db", archive_dir / "engine_journal", read_template(BOTH)
    )
    request.addfinalizer(owner.cleanup)
    cfg = Config.load(owner.config_dir, load_env=False)
    fake = FakeEngine(cfg, meta)
    request.addfinalizer(lambda: close_fake_engine(fake))
    host = FakeWalletHost(fake, archive_root, git_commit)
    worker = MatchWorker(
        cast(Any, host),
        build_discovered(game=game),
        cast(Any, model or FakeModelServer()),
        "paper",
        feed if feed is not None else FakeLiveFeed(),
        feed_timeout_seconds,
        exit_timeout_seconds=exit_timeout_seconds,
    )
    journal = session_journal.SessionJournal(tmp_path / "journal")
    request.addfinalizer(journal.close)
    worker._journal = journal
    worker._reporter = session_journal.FaultReporter(journal)
    worker._binding = make_binding()
    worker._last_tick_str = tick_str
    worker._observed_tick = meta.tick_size
    worker._sidecar_usable = True
    worker.open_core(
        level_usdc=level_usdc,
        min_order_size=meta.min_order_size,
        tick_size=meta.tick_size,
        trace=trace,
    )
    worker._attached = True
    worker._quoting = True
    worker._prior = prior
    if prior_ready:
        worker._prior_task = None
    host.register_worker(worker._cid, {worker._yes, worker._no}, worker)
    return worker, fake


def discard_session_alert(message: str) -> None:
    """Default notify_in_background stand-in for helper-built trading objects."""
    del message


class AlertRecorder:
    """Synchronous stand-in for notify_in_background."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        self.messages.append(message)


def read_session_records(journal_dir: Path) -> list[dict[str, object]]:
    """Parse session.jsonl into records."""
    path = journal_dir / "session.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
