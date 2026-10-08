"""One process, one Engine, one wallet: discovery plus in-process match tasks."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import asyncio
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import polymaker.engine as polymaker_engine
from polymaker.domain import Fill, MarketMeta, OpenOrder, Quote, Side
from polymaker.engine import Engine
from polymaker.execution.gateway import ExecutionGateway
from polymaker.marketdata.parse import TradePrint

from shared.constants.paths import TRADER_DIR
from shared.utils.log import get_logger
from trader import cadence
from trader.archive_paths import (
    match_archive_dir,
    own_execution_cleanup,
    write_execution_cleanup,
)
from trader.archived_markets import ArchivedMarketIndex
from trader.bindings import DiscoveredMatch
from trader.chain_balances import (
    ChainSnapshot,
    fresh_token_balances,
    polygon_rpcs,
    read_chain_snapshot,
)
from trader.clip_rules import ClipTable
from trader.collector_sidecars import MarketKind, scan_sidecars
from trader.core_execution import (
    PreparedPlace,
    paper_order_hash,
    persist_cancel_outcome,
    persist_prepared_cancels,
    prepare_dispatch,
    record_dispatch_results,
    retire_unsent,
    wrap_recompute_retire,
)
from trader.core_persistence import (
    CoreCommand,
    CoreSessionKey,
    get_unsettled_buy,
    insert_unsettled_buy,
    list_prepared_places,
    open_unsettled_buys,
    prove_unsettled_buy,
    resolve_settled_buys,
)
from trader.core_session_io import SessionIdentity, consume_core_outbox, persist_core_snapshot
from trader.discovery import MarketDiscovery, archive_id_kind
from trader.engine_seams import (
    DRAIN_TIMEOUT_S,
    BookReadiness,
    EngineClassRestore,
    ShutdownLatch,
    WalletUserStream,
    apply_book_only_markets,
    bind_user_fill_address,
    cash_only_on_fill,
    detach_market,
    fence_no_orders,
    install_book_readiness,
    install_collateral_snapshot,
    install_heartbeat_boot_grace,
    install_rest_fill_recovery,
    install_rest_snapshot_stamps,
    install_strict_rest,
    mark_inventory_from_books,
    pin_engine_identity,
    record_engine_markout,
    reset_order_error_rate,
    restore_engine_classes,
    stop_quoter,
    wrap_alert_transitions,
    wrap_inventory_place_guard,
    wrap_position_divergence,
    wrap_risk_from_ledger,
)
from trader.feed_selection import select_feed
from trader.fill_parsing import split_fill_key
from trader.game_profile import GAME_PROFILES, GameProfile, strategy_profile_name
from trader.live_feed import FeedSource
from trader.lol_league_filter import LolLeagueFilter
from trader.lol_prior import LolPriorTape
from trader.match_meta import (
    inspect_match_archive,
    read_finalized_match,
)
from trader.match_worker import (
    FENCE_TIMEOUT_S,
    MatchWorker,
)
from trader.model_server import ModelServer
from trader.notify import (
    notify_in_background,
    session_alert_from_match,
    session_exhausted_message,
    session_feed_dead_message,
    session_started_message,
)
from trader.oddin_catalog import DisirCatalog
from trader.oddin_client import require_brand_token
from trader.oddin_close import close_ended_oddin_archives
from trader.paper_gateway import PaperGateway
from trader.paths import (
    EXECUTION_CLEANUP_FILENAME,
    TRADER_WALLET_DIR,
)
from trader.process_lock import FileLock, FileLockHeld, acquire_file_lock
from trader.session_config import MaterializedConfigDir
from trader.session_core import CollateralCache, LiveCore, core_now_ns
from trader.session_engine import StrategyCell, close_engine_resources, sidecar_metadata_noop
from trader.session_journal import append_late_fill
from trader.session_quoting import fill_ts_utc
from trader.session_types import TradingDisabled
from trader.source_picker import probe_grid_scoreboard_cycle
from trader.steam_client import SteamClient
from trader.trading_mode import ExecutionMode
from trader.unsettled_buy_recovery import (
    UNSETTLED_ALERT_AGE_S,
    BuyExecutionProof,
    collect_rest_buy_proofs,
    parse_buy_cancellation,
    parse_terminal_buy_proof,
)
from trader.wallet_store import WalletStateStore

logger = get_logger(__name__)

FENCE_POLL_S = 0.5
MAX_CRASH_RESTARTS = 3
RESTART_BACKOFF_SECONDS: tuple[float, ...] = (60.0, 120.0, 240.0)
LIVE_TITLE_BLACKLIST: tuple[str, ...] = ("Streamers", "Winline")


def select_title_blacklist(mode: ExecutionMode, game: str) -> tuple[str, ...]:
    """Banned title leagues for one profile; the pinned list only applies to live Dota."""
    if mode == "live" and game == "dota":
        return LIVE_TITLE_BLACKLIST
    return ()


@dataclass(frozen=True)
class BootScanTarget:
    """A finalized match whose orders are not yet proven gone."""

    match_id: str
    condition_id: str
    yes_token_id: str
    no_token_id: str


@dataclass(frozen=True)
class _MatchBindings:
    """Static market/match binding.

    A live worker's sides are copied onto both pin identities so a GRID flip
    is not a rebind.
    """

    match_id: str
    steam_match_id: str | None
    league_id: int | None
    tournament: str | None
    radiant_name: str
    dire_name: str
    map_number: int
    condition_id: str
    market_slug: str
    event_slug: str
    yes_token_id: str
    no_token_id: str
    yes_is_radiant: bool
    neg_risk: bool
    grid_series_id: str | None
    market_kind: MarketKind | None


@dataclass(frozen=True)
class _ArchiveChoice:
    """Which archive id this CID may use, or why it must not start."""

    match_id: str
    skip_reason: str | None
    archive_cid: str | None


@dataclass
class _ManagedMatch:
    """In-process supervision record for one condition_id."""

    match_id: str
    handoff: DiscoveredMatch
    identity: _MatchBindings
    task: asyncio.Task[None] | None
    attempts: int
    next_eligible: float
    completed: bool
    exhausted: bool
    announced: bool
    no_snapshot: bool = False
    waiting_for_feed: bool = False
    feed_source: FeedSource | None = None
    oddin_match_id: str | None = None
    logged_skip: str | None = None
    record_only: bool = False

    def is_pinned(self, live_paper_dir: Path) -> bool:
        """True once this match owns a session: its static binding can no longer move."""
        if self.task is not None or self.completed or self.announced:
            return True
        return _own_archive(live_paper_dir, self.match_id, self.handoff.market.condition_id)


def _dead_feed_id(record: _ManagedMatch) -> str | None:
    """Identity the feed-dead alert names: Oddin match, else GRID series."""
    if record.feed_source is FeedSource.ODDIN:
        return record.oddin_match_id
    return record.identity.grid_series_id


def wallet_db_path(mode: ExecutionMode) -> Path:
    """Process-wide sqlite path: paper.db or live.db under the wallet directory."""
    filename = "live.db" if mode == "live" else "paper.db"
    return TRADER_WALLET_DIR / filename


def _match_bindings(discovered: DiscoveredMatch) -> _MatchBindings:
    """Project the static (non-dynamic) fields of one discovered match."""
    market = discovered.market
    return _MatchBindings(
        match_id=discovered.match_id,
        steam_match_id=discovered.steam_match_id,
        league_id=discovered.league_id,
        tournament=discovered.tournament,
        radiant_name=discovered.sides.radiant,
        dire_name=discovered.sides.dire,
        map_number=discovered.map_number,
        condition_id=market.condition_id,
        market_slug=market.market_slug,
        event_slug=market.event_slug,
        yes_token_id=market.yes_token_id,
        no_token_id=market.no_token_id,
        yes_is_radiant=market.yes_is_radiant,
        neg_risk=market.neg_risk,
        grid_series_id=market.grid_series_id,
        market_kind=discovered.market_kind,
    )


def _align_identity_to_worker(identity: _MatchBindings, worker: MatchWorker) -> _MatchBindings:
    """Copy the worker's feed-locked sides onto this identity so they drop out of pin compare."""
    return replace(
        identity,
        radiant_name=worker._discovered.sides.radiant,
        dire_name=worker._discovered.sides.dire,
        yes_is_radiant=worker._yes_is_radiant,
    )


def _own_archive(live_paper_dir: Path, match_id: str, condition_id: str) -> bool:
    """True when match.json exists and its stored CID is this condition."""
    owner = inspect_match_archive(live_paper_dir, match_id).owner
    return owner is not None and owner.condition_id == condition_id


def _own_durable_final(live_paper_dir: Path, match_id: str, condition_id: str) -> bool:
    """True when this CID's match.json already has a non-null final."""
    owner = inspect_match_archive(live_paper_dir, match_id).owner
    return owner is not None and owner.condition_id == condition_id and owner.has_final


def _own_steam_pin(live_paper_dir: Path, match_id: str, condition_id: str) -> bool:
    """True when this CID's match.json pins Steam, which no live feed can resume."""
    owner = inspect_match_archive(live_paper_dir, match_id).owner
    return owner is not None and owner.condition_id == condition_id and owner.pins_steam


def _session_archive_decision(discovered: DiscoveredMatch) -> _ArchiveChoice:
    """Pick the archive id for this CID, or a fail-closed skip that must not start."""
    cid = discovered.market.condition_id
    canonical_id = discovered.match_id
    legacy_id = discovered.steam_match_id
    canonical = inspect_match_archive(TRADER_DIR, canonical_id)
    if canonical.unreadable:
        return _ArchiveChoice(canonical_id, "unreadable_archive", None)
    if canonical.owner is not None:
        return _canonical_present_choice(canonical_id, legacy_id, cid, canonical.owner.condition_id)
    if legacy_id is None or legacy_id == canonical_id:
        return _ArchiveChoice(canonical_id, None, None)
    return _legacy_absent_canonical_choice(canonical_id, legacy_id, cid, discovered)


def _canonical_present_choice(
    canonical_id: str, legacy_id: str | None, cid: str, archive_cid: str
) -> _ArchiveChoice:
    """Resolve a readable canonical archive: own it, collide, or duplicate with legacy."""
    if archive_cid != cid:
        if archive_id_kind(canonical_id) == "grid":
            return _ArchiveChoice(canonical_id, "canonical_id_collision", archive_cid)
        return _ArchiveChoice(canonical_id, "occupied_other_cid", archive_cid)
    if legacy_id is None or legacy_id == canonical_id:
        return _ArchiveChoice(canonical_id, None, None)
    legacy = inspect_match_archive(TRADER_DIR, legacy_id)
    if legacy.unreadable:
        return _ArchiveChoice(canonical_id, None, None)
    if legacy.owner is not None and legacy.owner.condition_id == cid:
        return _ArchiveChoice(canonical_id, "duplicate_owned_archives", cid)
    return _ArchiveChoice(canonical_id, None, None)


def _legacy_absent_canonical_choice(
    canonical_id: str, legacy_id: str, cid: str, discovered: DiscoveredMatch
) -> _ArchiveChoice:
    """Canonical dir is empty: resume own legacy, start grid-*, or wait on a foreign Steam dir."""
    legacy = inspect_match_archive(TRADER_DIR, legacy_id)
    if legacy.unreadable:
        return _ArchiveChoice(legacy_id, "unreadable_archive", None)
    if legacy.owner is None:
        return _ArchiveChoice(canonical_id, None, None)
    if legacy.owner.condition_id == cid:
        return _ArchiveChoice(legacy_id, None, None)
    if discovered.market.grid_series_id is not None:
        return _ArchiveChoice(canonical_id, None, None)
    return _ArchiveChoice(canonical_id, "occupied_other_cid", legacy.owner.condition_id)


def _accept_handoff(record: _ManagedMatch, discovered: DiscoveredMatch) -> None:
    """Adopt a new handoff as match_id, handoff, and identity together."""
    record.match_id = discovered.match_id
    record.handoff = discovered
    record.identity = _match_bindings(discovered)
    record.no_snapshot = False
    record.logged_skip = None


def _worker_for_order_ids(
    engine: Engine,
    workers_by_token: dict[str, MatchWorker],
    workers_by_cid: dict[str, MatchWorker],
    order_ids: list[str],
) -> MatchWorker | None:
    """The worker that owns the first order id, including one already off the book."""
    for order_id in order_ids:
        order = engine.state.orders.get(order_id)
        if order is None:
            continue
        worker = workers_by_token.get(order.token_id)
        if worker is not None:
            return worker
        cid = engine._token_cid.get(order.token_id)
        if cid is None:
            continue
        mapped = workers_by_cid.get(cid)
        if mapped is not None:
            return mapped
    wanted = set(order_ids)
    for worker in workers_by_cid.values():
        core = worker.core
        if core is None:
            continue
        if wanted.intersection(core.venue_ids()):
            return worker
    return None


def _identity(worker: MatchWorker) -> SessionIdentity:
    return SessionIdentity(
        session_id=worker._cid,
        key=CoreSessionKey(
            condition_id=worker._cid,
            game=worker._discovered.game,
            yes_token=worker._yes,
            no_token=worker._no,
            yes_is_radiant=worker._yes_is_radiant,
        ),
    )


def _dispatch_places(
    store: WalletStateStore, worker: MatchWorker, quotes: list[Quote]
) -> tuple[tuple[PreparedPlace, ...], tuple[CoreCommand, ...]]:
    prepared = _prepared_from_batch(worker, quotes)
    if not prepared or worker.core is None:
        return (), ()
    commands = prepare_dispatch(
        store._conn,
        session_id=worker._cid,
        revision=worker.core.state.next_order_seq,
        batch_id=str(worker.core._cycle),
        places=prepared,
    )
    return prepared, commands


def _dispatch_cancels(
    store: WalletStateStore, worker: MatchWorker, order_ids: list[str]
) -> tuple[CoreCommand, ...]:
    if worker.core is None:
        return ()
    core_ids = tuple(
        worker.core._venue_to_core[order_id]
        for order_id in order_ids
        if order_id in worker.core._venue_to_core
    )
    if len(core_ids) != len(order_ids):
        return ()
    commands = persist_prepared_cancels(
        store._conn,
        session_id=worker._cid,
        revision=worker.core.state.next_order_seq,
        batch_id=f"cancel-{worker.core._cycle}",
        venue_ids=order_ids,
        core_ids=core_ids,
    )
    store._conn.commit()
    return commands


def _warn_orphaned(kind: str, ids: list[str]) -> None:
    """No worker owns this venue result, so no core will ever see its ack.

    The core then keeps the order in `canceling` or `pending`, `sell_occupied`
    holds the only exit slot, and neither `core_trace.jsonl` nor `session.jsonl`
    records the miss: both hang off the same lookup. One line here names the
    seam that swallowed the result.
    """
    logger.warning("trader order result orphaned kind=%s ids=%s", kind, ",".join(ids))


async def _durable_place(
    host: "WalletHost",
    original_place: Callable[[list[Quote], MarketMeta], Awaitable[list[OpenOrder]]],
    quotes: list[Quote],
    meta: MarketMeta,
) -> list[OpenOrder]:
    worker = host._worker_by_cid.get(meta.condition_id)
    prepared: tuple[PreparedPlace, ...] = ()
    commands: tuple[CoreCommand, ...] = ()
    if worker is not None:
        prepared, commands = _dispatch_places(host.store, worker, quotes)
    orders = await original_place(quotes, meta)
    if prepared:
        record_dispatch_results(host.store._conn, commands=commands, places=prepared, placed=orders)
    if worker is None and orders:
        worker = host._worker_by_token.get(orders[0].token_id)
    if worker is None:
        _warn_orphaned("place", [order.order_id[:12] for order in orders])
    if worker is not None:
        worker.note_place_result(orders)
        if orders:
            worker.write_quote(orders, [])
    return orders


@dataclass(frozen=True)
class _OwnedBuyCancel:
    venue_id: str
    session_id: str
    token_id: str
    price: float
    qty: float


def _owned_buy_cancels(worker: MatchWorker, order_ids: list[str]) -> tuple[_OwnedBuyCancel, ...]:
    """Submitted BUY metadata captured before the venue await."""
    core = worker.core
    if core is None:
        return ()
    owned: list[_OwnedBuyCancel] = []
    for venue_id in order_ids:
        order = core.find_owned_order(venue_id)
        if order is None or order.side != "BUY":
            continue
        owned.append(
            _OwnedBuyCancel(
                venue_id=venue_id,
                session_id=worker._cid,
                token_id=core.token_id(order.token_index),
                price=order.price,
                qty=order.submitted_qty,
            )
        )
    return tuple(owned)


def _commit_cancel_reserves(
    conn: sqlite3.Connection,
    *,
    commands: tuple[CoreCommand, ...],
    buys: tuple[_OwnedBuyCancel, ...],
    ok: bool,
) -> bool:
    """One transaction for cancel outcomes and successful BUY rows. True when rows were written."""
    insert = ok and bool(buys)
    if not commands and not insert:
        return False
    with conn:
        for command in commands:
            persist_cancel_outcome(conn, command=command, ok=ok)
        if insert:
            for buy in buys:
                insert_unsettled_buy(
                    conn,
                    venue_id=buy.venue_id,
                    session_id=buy.session_id,
                    token_id=buy.token_id,
                    price=buy.price,
                    qty=buy.qty,
                )
    return insert


async def _durable_cancel(
    host: "WalletHost",
    original_cancel: Callable[[list[str]], Awaitable[bool]],
    order_ids: list[str],
) -> bool:
    worker = _worker_for_order_ids(
        host.engine, host._worker_by_token, host._worker_by_cid, order_ids
    )
    if worker is None:
        _warn_orphaned("cancel", [order_id[:12] for order_id in order_ids])
    commands: tuple[CoreCommand, ...] = ()
    buys: tuple[_OwnedBuyCancel, ...] = ()
    if worker is not None:
        commands = _dispatch_cancels(host.store, worker, order_ids)
        buys = _owned_buy_cancels(worker, order_ids)
    ok = await original_cancel(order_ids)
    inserted = _commit_cancel_reserves(host.store._conn, commands=commands, buys=buys, ok=ok)
    if worker is not None:
        worker.note_cancel_result(order_ids, ok)
        if ok and order_ids:
            worker.write_quote([], order_ids)
    if inserted:
        host._apply_unsettled_proofs(())
    return ok


def _prepared_from_batch(
    worker: MatchWorker | None, quotes: list[Quote]
) -> tuple[PreparedPlace, ...]:
    if worker is None or worker.core is None or worker.core._last_batch is None:
        return ()
    wanted = {(quote.token_id, quote.side, quote.price, quote.size) for quote in quotes}
    return tuple(
        PreparedPlace(
            core_order_id=item.order_id,
            quote=item.quote,
            order_hash=paper_order_hash(quote=item.quote, core_order_id=item.order_id),
            signed=None,
        )
        for item in worker.core._last_batch.to_place
        if (item.quote.token_id, item.quote.side, item.quote.price, item.quote.size) in wanted
    )


def list_boot_scan_targets(match_root: Path) -> list[BootScanTarget]:
    """Finalized matches this build owns that still lack execution_cleanup.json."""
    if not match_root.is_dir():
        return []
    targets: list[BootScanTarget] = []
    for child in sorted(match_root.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.name == TRADER_WALLET_DIR.name:
            continue
        if (child / EXECUTION_CLEANUP_FILENAME).exists():
            continue
        finalized = read_finalized_match(match_root, child.name)
        if finalized is None:
            continue
        targets.append(
            BootScanTarget(
                child.name,
                finalized.condition_id,
                finalized.yes_token_id,
                finalized.no_token_id,
            )
        )
    return targets


async def cancel_token_orders(gateway: ExecutionGateway, token_ids: set[str]) -> None:
    """Cancel live orders on these tokens only. Other markets are left alone."""
    for token_id in token_ids:
        await gateway.cancel_asset(token_id)


async def fence_until(engine: Engine, token_ids: set[str], timeout_s: float) -> bool:
    """Poll until REST shows no orders and MATCHED outbox is quiet, or the timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while True:
        if await fence_no_orders(engine, token_ids) and not _unacked_matched(engine, token_ids):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(FENCE_POLL_S)


def _unacked_matched(engine: Engine, token_ids: set[str]) -> bool:
    """True when a durable unacked MATCHED row still points at these tokens."""
    store = getattr(engine, "state", None)
    if not isinstance(store, WalletStateStore):
        return False
    return store.has_unacked_matched(token_ids)


async def refresh_attached_sidecars(archive_root: Path, workers: tuple[MatchWorker, ...]) -> None:
    """Reread collector sidecars once and apply them to every live match."""
    scan = scan_sidecars(archive_root, time.time())
    for worker in workers:
        await worker.refresh_sidecar(scan)


async def refresh_sidecars_by_game(
    archive_by_game: dict[str, Path], workers: tuple[MatchWorker, ...]
) -> None:
    """Scan each game's archive once and refresh only that game's workers."""
    grouped: dict[str, list[MatchWorker]] = {}
    for worker in workers:
        grouped.setdefault(worker._discovered.game, []).append(worker)
    for game, game_workers in grouped.items():
        await refresh_attached_sidecars(archive_by_game[game], tuple(game_workers))


async def _empty_resolve_markets() -> None:
    """Wallet Engine starts with no markets; MatchWorker attaches them later."""


def _keep_map_open(_oddin_match_id: str, _map_number: int) -> bool:
    return False


def _log_task_failure(task: asyncio.Task[None]) -> None:
    """Surface a crashed background task; teardown cancels stay quiet."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("background task %s died: %r", task.get_name(), exc, exc_info=exc)


class WalletHost:
    """One Engine, one sqlite wallet, many in-process match tasks."""

    def __init__(
        self,
        steam_client: SteamClient | None,
        archive_by_game: dict[str, Path],
        engine: Engine,
        mode: ExecutionMode,
        lock: FileLock,
        config_dir: MaterializedConfigDir,
        models: dict[str, ModelServer],
        class_restore: EngineClassRestore,
        latch: ShutdownLatch,
        readiness: BookReadiness,
        cells: dict[str, StrategyCell],
        cores: dict[str, LiveCore],
        budget_cache: CollateralCache,
        original_gateway: type[ExecutionGateway],
        original_quotes: object,
        original_reconcile: object,
        git_commit: str,
        games: tuple[GameProfile, ...],
        clip_tables: dict[str, ClipTable],
    ) -> None:
        if not isinstance(engine.state, WalletStateStore):
            raise TypeError("wallet host requires WalletStateStore")
        self.steam_client = steam_client
        self._games = games
        self._archive_by_game = archive_by_game
        # First-game alias for tests and leftover callers. Sidecar refresh
        # groups workers by game and scans that game's archive.
        self.archive_root = archive_by_game[games[0].game]
        self.engine = engine
        self.lol_prior = LolPriorTape()
        self._lol_leagues: LolLeagueFilter | None = None
        self.store = engine.state
        self._mode: ExecutionMode = mode
        self._lock = lock
        self._config_dir = config_dir
        self._models = models
        self._class_restore = class_restore
        self._latch = latch
        self.readiness = readiness
        self._cells = cells
        self.cores = cores
        self._budget_cache = budget_cache
        self._original_gateway = original_gateway
        self._original_quotes = original_quotes
        self._original_reconcile = original_reconcile
        self.git_commit = git_commit
        self.clip_tables = clip_tables
        self._worker_by_cid: dict[str, MatchWorker] = {}
        self._worker_by_token: dict[str, MatchWorker] = {}
        self._archives = ArchivedMarketIndex((TRADER_DIR,))
        self._tokens_by_cid: dict[str, set[str]] = {}
        self._quiet: set[str] = set()
        self._matches_by_cid: dict[str, _ManagedMatch] = {}
        self._map_ended: Callable[[str, int], bool] = _keep_map_open
        self._closed = False
        self._install_runtime_seams()

    def archive_for(self, game: str) -> Path:
        """Return the collector archive mounted for `game`; unknown identity fails closed."""
        try:
            return self._archive_by_game[game]
        except KeyError as exc:
            raise TradingDisabled(f"unknown game identity {game!r}") from exc

    async def read_chain_snapshot_for(self, token_ids: list[str]) -> ChainSnapshot | None:
        """One chain read. Paper mode does not call an RPC."""
        if self.engine.paper:
            return None
        configured = self.engine.cfg.secrets.polygon_rpc or self.engine.cfg.wallet.polygon_rpc
        floor_ts = max(
            (self.store.chain_read_floor(token_id) for token_id in token_ids),
            default=0.0,
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await read_chain_snapshot(
                client,
                polygon_rpcs(configured),
                self.engine.gateway.funder,
                token_ids,
                floor_ts=floor_ts,
            )

    async def read_fresh_balances(self, token_ids: list[str]) -> dict[str, float] | None:
        """Balances only when the block is fresh for every token. No cached balance."""
        snapshot = await self.read_chain_snapshot_for(token_ids)
        if snapshot is None:
            return None
        return fresh_token_balances(
            snapshot,
            token_ids,
            self.store.chain_read_floor,
            time.time(),
        )

    def _install_runtime_seams(self) -> None:
        """No-Gamma resolution, ledger cash, latch, book readiness, paper MDS, no FV patch."""
        engine = self.engine
        engine._resolve_markets = _empty_resolve_markets
        engine.refresh_market_metadata = sidecar_metadata_noop
        cash = cash_only_on_fill(engine)

        def routed_fill(fill: Fill) -> None:
            cash(fill)
            self._dispatch_fill(fill)

        def halt_oversized(token_id: str) -> None:
            cid = engine._token_cid.get(token_id)
            if cid is None:
                return
            engine._halted.add(cid)
            engine._wake_cid(cid)

        engine._on_fill = routed_fill
        engine.user_proc._on_fill = routed_fill
        self.store.set_oversized_sell_handler(halt_oversized)
        self.store.set_fill_credited_handler(self._on_fill_credited)
        self.store.set_fill_failed_handler(self._on_fill_failed)
        wrap_risk_from_ledger(engine, self.store)
        wrap_alert_transitions(engine)
        wrap_position_divergence(engine, self.read_chain_snapshot_for)
        install_rest_snapshot_stamps(engine)
        install_rest_fill_recovery(
            engine, self.store, self.drain_outbox, self._reconcile_unsettled_buys
        )
        install_collateral_snapshot(engine, self._budget_cache)
        install_strict_rest(engine)
        install_heartbeat_boot_grace(engine)
        self._latch.wrap_gateway(engine.gateway)
        wrap_inventory_place_guard(engine, self.cores)
        self._wrap_quote_journal()
        self._wrap_unsent_places()
        install_book_readiness(engine, self.readiness)
        if engine.paper:
            self._install_paper_mds_bridge()
        self._install_lol_prior_tap()

    def _install_paper_mds_bridge(self) -> None:
        """Feed applied books and trade prints into the paper fill simulation."""
        gateway = self.engine.gateway
        if not isinstance(gateway, PaperGateway):
            return
        gateway.bind_fill_sink(self.engine.state, self.engine._on_fill)
        md = self.engine.md
        original_dirty = cast(Callable[[str, str], None], md._on_dirty)
        original_trade = cast(Callable[[TradePrint], None], md._on_trade)

        def bridged_dirty(condition_id: str, token_id: str) -> None:
            book = md.book(token_id)
            if book is not None:
                gateway.process_book_update(token_id, book)
            original_dirty(condition_id, token_id)

        def bridged_trade(trade: TradePrint) -> None:
            gateway.process_trade_print(trade)
            original_trade(trade)

        md._on_dirty = cast(Any, bridged_dirty)
        md._on_trade = cast(Any, bridged_trade)

    def _install_lol_prior_tap(self) -> None:
        """Record two-sided books for the LoL prior tape. No quotes."""
        md = self.engine.md
        original_dirty = cast(Callable[[str, str], None], md._on_dirty)
        tape = self.lol_prior

        def tapped_dirty(condition_id: str, token_id: str) -> None:
            book = md.book(token_id)
            if book is not None:
                bid = book.best_bid()
                ask = book.best_ask()
                tape.note(
                    token_id,
                    book.last_update_ts,
                    None if bid is None else bid.price,
                    None if ask is None else ask.price,
                )
            original_dirty(condition_id, token_id)

        md._on_dirty = cast(Any, tapped_dirty)

    def _sync_lol_book_watch(self) -> None:
        """Subscribe tradeable LoL map and series books before GRID, and keep their tape."""
        if "lol" not in self._archive_by_game:
            return
        root = self._archive_by_game["lol"]
        if self._lol_leagues is None:
            self._lol_leagues = LolLeagueFilter(root)
        scan = scan_sidecars(root, time.time())
        selected = self._lol_leagues.select_sidecars(scan.sidecars).traded
        retained: dict[str, tuple[str, str]] = {}
        book_only: dict[str, tuple[str, str]] = {}
        for sidecar in selected:
            pair = (sidecar.outcome_0_token, sidecar.outcome_1_token)
            retained[sidecar.condition_id] = pair
            if sidecar.is_tradeable() and sidecar.condition_id not in self.engine.metas:
                book_only[sidecar.condition_id] = pair
        for worker in self._worker_by_cid.values():
            if worker._discovered.game != "lol":
                continue
            retained.setdefault(worker._cid, (worker._yes, worker._no))
        self.lol_prior.retain(retained)
        apply_book_only_markets(self.engine, book_only, self.readiness)

    def _wrap_unsent_places(self) -> None:
        original = self.engine._recompute_locked

        def load_prepared(cid: str) -> tuple[CoreCommand, ...]:
            return list_prepared_places(self.store._conn, cid)

        def retire(cid: str, commands: tuple[CoreCommand, ...]) -> None:
            retired = retire_unsent(self.store._conn, commands)
            self.store._conn.commit()
            worker = self._worker_by_cid.get(cid)
            if worker is None or worker.core is None or not retired:
                return
            worker.core.note_unsent(retired, core_now_ns())
            worker.core.drain_apply()
            persist_core_snapshot(store=self.store, core=worker.core, identity=_identity(worker))

        self.engine._recompute_locked = wrap_recompute_retire(
            original=original, load_prepared=load_prepared, retire=retire
        )

    def _wrap_quote_journal(self) -> None:
        """Persist place/cancel intent, then journal the venue result."""
        gateway = self.engine.gateway
        original_place = gateway.place
        original_cancel = gateway.cancel
        host = self

        async def place(quotes: list[Quote], meta: MarketMeta) -> list[OpenOrder]:
            return await _durable_place(host, original_place, quotes, meta)

        async def cancel(order_ids: list[str]) -> bool:
            return await _durable_cancel(host, original_cancel, order_ids)

        gateway.place = place
        gateway.cancel = cancel

    def _bind_order_terminal(self) -> None:
        """Attach the raw order hook once Engine.start has built the user stream."""
        user = getattr(self.engine, "user", None)
        if isinstance(user, WalletUserStream):
            user.on_order_terminal = self._on_order_terminal

    def _on_order_terminal(self, msg: object) -> None:
        """Prove an open BUY row, or open one when the venue cancels a core BUY."""
        if type(msg) is not dict:
            return
        raw = cast(Mapping[str, object], msg)
        proof = parse_terminal_buy_proof(raw)
        cancellation = parse_buy_cancellation(raw)
        venue_id = proof.venue_id if proof is not None else None
        if venue_id is None and cancellation is not None:
            venue_id = cancellation.venue_id
        if venue_id is None:
            return
        row = get_unsettled_buy(self.store._conn, venue_id)
        if row is not None and row.resolved:
            return
        if row is None:
            if cancellation is None:
                return
            self._open_cancelled_buy(venue_id, proof)
            return
        if proof is not None:
            self._apply_unsettled_proofs((proof,))
        if cancellation is not None:
            self._deliver_cancelled_buy(venue_id)

    def _open_cancelled_buy(self, venue_id: str, proof: BuyExecutionProof | None) -> None:
        """Insert core metadata and any proof, then mark the BUY gone."""
        worker = _worker_for_order_ids(
            self.engine, self._worker_by_token, self._worker_by_cid, [venue_id]
        )
        if worker is None or worker.core is None:
            return
        owner = worker.core.find_owned_order(venue_id)
        if owner is None or owner.side != "BUY":
            return
        conn = self.store._conn
        with conn:
            insert_unsettled_buy(
                conn,
                venue_id=venue_id,
                session_id=worker._cid,
                token_id=worker.core.token_id(owner.token_index),
                price=owner.price,
                qty=owner.submitted_qty,
            )
            stored = get_unsettled_buy(conn, venue_id)
            if (
                proof is not None
                and proof.venue_id == venue_id
                and stored is not None
                and not stored.proven
                and not stored.resolved
            ):
                prove_unsettled_buy(conn, venue_id, proof.matched_qty)
        worker.note_cancel_result([venue_id], True)
        self._apply_unsettled_proofs(())

    def _deliver_cancelled_buy(self, venue_id: str) -> None:
        """Re-apply a venue cancel when the row already exists and the BUY is still owned."""
        worker = _worker_for_order_ids(
            self.engine, self._worker_by_token, self._worker_by_cid, [venue_id]
        )
        if worker is None or worker.core is None:
            return
        owner = worker.core.find_owned_order(venue_id)
        if owner is None or owner.side != "BUY":
            return
        worker.note_cancel_result([venue_id], True)

    def _resolve_confirmed_buy(self, fill: Fill) -> None:
        """Close a matching open row once the ledger itself is CONFIRMED."""
        if fill.side is not Side.BUY:
            return
        if self.store.ledger_status(fill.trade_id) != "CONFIRMED":
            return
        _clob_trade_id, maker_order_id = split_fill_key(fill.trade_id)
        if not any(row.venue_id == maker_order_id for row in open_unsettled_buys(self.store._conn)):
            return
        self._apply_unsettled_proofs(())

    def _apply_unsettled_proofs(self, proofs: tuple[BuyExecutionProof, ...]) -> None:
        """Prove, resolve, commit, then wake sessions that moved."""
        conn = self.store._conn
        with conn:
            current = open_unsettled_buys(conn)
            by_id = {row.venue_id: row for row in current}
            progressed: list[str] = []
            applied: set[str] = set()
            for proof in proofs:
                row = by_id.get(proof.venue_id)
                if row is None or row.proven or proof.venue_id in applied:
                    continue
                prove_unsettled_buy(conn, proof.venue_id, proof.matched_qty)
                applied.add(proof.venue_id)
                progressed.append(row.session_id)
            resolved_ids = resolve_settled_buys(conn)
        resolved_sessions = tuple(
            by_id[venue_id].session_id for venue_id in resolved_ids if venue_id in by_id
        )
        self._wake_unsettled_sessions((*progressed, *resolved_sessions))

    def _wake_unsettled_sessions(self, session_ids: tuple[str, ...]) -> None:
        """Wake each live worker once. A missing worker still leaves the row settled."""
        woken: set[str] = set()
        for session_id in session_ids:
            if session_id in woken or session_id not in self._worker_by_cid:
                continue
            woken.add(session_id)
            self.engine._wake_cid(session_id)

    async def _reconcile_unsettled_buys(self) -> None:
        """Prove aged rows from trades, resolve from the ledger, then alert the rest."""
        now_s = time.time()
        rows = open_unsettled_buys(self.store._conn)
        proofs = await collect_rest_buy_proofs(
            self.engine.gateway,
            rows,
            now_s,
            self.store,
            self.engine._other_token,
        )
        self._apply_unsettled_proofs(proofs)
        self._alert_stale_unsettled_buys(time.time())

    def _alert_stale_unsettled_buys(self, now_s: float) -> None:
        """One stable alert per old open row. Alerting does not prove or release it."""
        for row in open_unsettled_buys(self.store._conn):
            if now_s - row.created_at <= UNSETTLED_ALERT_AGE_S:
                continue
            self.engine.alerter.alert(
                f"unsettled_buy:{row.venue_id[:8]}",
                f"venue {row.venue_id} session {row.session_id} reserve remains held",
            )

    def _dispatch_fill(self, fill: Fill) -> None:
        """Journal, rebate, and markout one CONFIRMED fill, then ack that seq."""
        recorded = self.store.fill_for_key(fill.trade_id)
        target = recorded if recorded is not None else fill
        self._resolve_confirmed_buy(target)
        seq = self.store.unacked_seq(target.trade_id, "confirmed")
        if seq is None:
            return
        self._journal_fill(target)
        self.store.ack_outbox(seq)
        self._ack_outbox_event(target.trade_id, "matched")

    def _on_fill_credited(self, fill: Fill) -> None:
        """Wake the core consumer. Ledger already credited once."""
        self._consume_core_outbox(fill.token_id)
        cid = self.engine._token_cid.get(fill.token_id)
        if cid is not None:
            self.engine._wake_cid(cid)

    def _consume_core_outbox(self, token_id: str) -> None:
        worker = self._worker_by_token.get(token_id)
        if worker is None or worker.core is None:
            return
        tokens = frozenset(self._tokens_by_cid.get(worker._cid, {token_id}))
        consume_core_outbox(
            store=self.store,
            core=worker.core,
            identity=_identity(worker),
            tokens=tokens,
        )

    def _on_fill_failed(self, fill: Fill) -> None:
        """Queue Recovery after a FAILED trade rebuilds sqlite."""
        worker = self._worker_by_token.get(fill.token_id)
        if worker is None:
            return
        worker.note_failed_fill(fill)
        cid = self.engine._token_cid.get(fill.token_id)
        if cid is not None:
            self.engine._wake_cid(cid)

    def _ack_outbox_event(self, key: str, event: str) -> None:
        """Ack the unacked outbox row for this key and event, if any."""
        seq = self.store.unacked_seq(key, event)
        if seq is not None:
            self.store.ack_outbox(seq)

    def _journal_fill(self, fill: Fill) -> None:
        """Write the fill to the live worker, or a late_fill into the closed archive."""
        worker = self._worker_by_token.get(fill.token_id)
        if worker is not None:
            worker.note_fill(fill)
            record_engine_markout(self.engine, fill)
            return
        market = self._archives.find(fill.token_id)
        if market is None:
            logger.error(
                "trader late fill dropped: no worker and no archive token=%s key=%s",
                fill.token_id[:12],
                fill.trade_id,
            )
            return
        append_late_fill(
            market.archive_dir,
            fill,
            self.store.position(fill.token_id).size,
            self.store.ledger_net_cash_for_tokens(set(market.tokens)),
            fill_ts_utc(fill.ts),
            fill.trade_id,
            "user_ws",
        )

    def drain_outbox(self) -> None:
        """Replay unacked CONFIRMED journal rows. MATCHED stays unacked until CONFIRMED/FAILED."""
        for item in self.store.pending_outbox():
            if item.event == "matched":
                continue
            fill = self.store.fill_for_key(item.fill_key)
            if fill is None:
                self.store.ack_outbox(item.seq)
                continue
            if item.event == "failed":
                self.store.ack_outbox(item.seq)
                continue
            if item.event != "confirmed":
                continue
            self._journal_fill(fill)
            self.store.ack_outbox(item.seq)
            matched_seq = self.store.unacked_seq(item.fill_key, "matched")
            if matched_seq is not None:
                self.store.ack_outbox(matched_seq)

    def register_worker(self, cid: str, tokens: set[str], worker: MatchWorker) -> None:
        """Route fills for these tokens to the worker and replay pending outbox rows.

        Call after attach_market so replay cannot hit a market that is not live.
        """
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
        for token_id in tokens:
            self._consume_core_outbox(token_id)

    def unregister_worker(self, cid: str) -> None:
        """Drop the live worker. Quiet leftover keeps the cell so the idle quoter stays empty."""
        worker = self._worker_by_cid.pop(cid, None)
        tokens = self._tokens_by_cid.get(cid, set())
        for token_id in tokens:
            mapped = self._worker_by_token.get(token_id)
            if mapped is worker:
                self._worker_by_token.pop(token_id, None)
        if cid not in self._quiet:
            self._cells.pop(cid, None)
            self.cores.pop(cid, None)
            self._tokens_by_cid.pop(cid, None)

    def keep_quiet(self, cid: str) -> None:
        """Hold an attached market without a quoter when the exit fence is unproven."""
        self._quiet.add(cid)
        cell = self._cells.get(cid)
        if cell is not None:
            cell.clear()
        mark_inventory_from_books(self.engine)

    def cid_in_use(self, cid: str) -> bool:
        """True when a live worker or Engine market already owns this condition.

        The managed task is not a gate: `_launch` sets it before attach, so
        counting it here would make the first worker refuse its own market.
        """
        return cid in self._worker_by_cid or cid in self.engine.metas

    def _live_owner_match_id(self, cid: str) -> str | None:
        """The match_id of the running task that holds this condition, if one is mapped."""
        record = self._matches_by_cid.get(cid)
        if record is None or record.task is None:
            return None
        return record.match_id

    async def cancel_market(self, cid: str) -> None:
        """Cancel orders on this market's tokens only."""
        tokens = self._tokens_for_cid(cid)
        await cancel_token_orders(self.engine.gateway, tokens)

    async def fence_market(self, token_ids: set[str], timeout_s: float) -> bool:
        """True when these tokens have no live orders before the timeout."""
        return await fence_until(self.engine, token_ids, timeout_s)

    async def detach(self, cid: str) -> None:
        """Stop the idle quoter stub and drop the market from the live Engine."""
        await stop_quoter(self.engine, cid)
        detach_market(self.engine, cid, self.readiness)
        reset_order_error_rate(self.engine.risk, cid)
        self._quiet.discard(cid)
        self._cells.pop(cid, None)
        self._tokens_by_cid.pop(cid, None)

    def _tokens_for_cid(self, cid: str) -> set[str]:
        meta = self.engine.metas.get(cid)
        if meta is not None:
            return {meta.yes.token_id, meta.no.token_id}
        return set(self._tokens_by_cid.get(cid, set()))

    async def run(self) -> None:
        """Start the Engine, fence leftover finals, then attach matches from discovery."""
        catalog = DisirCatalog(
            require_brand_token(), now=lambda: datetime.now(UTC), sleep=asyncio.sleep
        )
        discoveries = tuple(
            MarketDiscovery(
                self._archive_by_game[profile.game],
                self.steam_client if profile.uses_steam else None,
                probe_grid_scoreboard_cycle,
                catalog.open_matches,
                profile,
                select_title_blacklist(self._mode, profile.game),
            )
            for profile in self._games
        )
        catalog_task: asyncio.Task[None] | None = None
        refresh_task: asyncio.Task[None] | None = None
        try:
            await self.engine.start()
            pin_engine_identity(self.engine)
            bind_user_fill_address(self.engine)
            self._bind_order_terminal()
            await catalog.refresh_now()
            close_ended_oddin_archives(
                TRADER_DIR,
                catalog.map_ended,
                self._position_size,
                self._net_cash_for_tokens,
                self._mode,
            )
            await self._boot_scan()
            self._map_ended = catalog.map_ended
            catalog_task = asyncio.create_task(catalog.run(), name="disir-catalog")
            refresh_task = asyncio.create_task(self._sidecar_refresh_loop(), name="sidecar-refresh")
            for task in (catalog_task, refresh_task):
                task.add_done_callback(_log_task_failure)
            async for discovered in cadence.poll_discoveries(discoveries):
                live_cids = self.reconcile(discovered)
                await self._detach_quiet_missing(live_cids)
        finally:
            background = [task for task in (catalog_task, refresh_task) if task is not None]
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await self.teardown()

    def _position_size(self, token_id: str) -> float:
        return float(self.engine.state.position(token_id).size)

    def _net_cash_for_tokens(self, tokens: set[str]) -> float:
        return self.store.ledger_net_cash_for_tokens(tokens)

    async def _boot_scan(self) -> None:
        """Fence markets whose final is on disk but execution_cleanup is not."""
        for target in list_boot_scan_targets(TRADER_DIR):
            tokens = {target.yes_token_id, target.no_token_id}
            await cancel_token_orders(self.engine.gateway, tokens)
            proven = await fence_until(self.engine, tokens, FENCE_TIMEOUT_S)
            if not proven:
                logger.warning(
                    "trader boot scan unproven match=%s: execution_cleanup not written",
                    target.match_id,
                )
                continue
            write_execution_cleanup(
                match_archive_dir(TRADER_DIR, target.match_id),
                target.match_id,
                target.condition_id,
            )

    async def _sidecar_refresh_loop(self) -> None:
        """Rescan each game's collector sidecars on the discovery cadence."""
        while True:
            await refresh_sidecars_by_game(
                self._archive_by_game, tuple(self._worker_by_cid.values())
            )
            self._sync_lol_book_watch()
            await asyncio.sleep(cadence.DISCOVERY_POLL_INTERVAL_SECONDS)

    def reconcile(self, discovered: tuple[DiscoveredMatch, ...]) -> set[str]:
        """Spawn or skip in-process workers. Returns the CIDs still in this discovery tuple."""
        now = time.monotonic()
        self._reap_finished_tasks(now)
        by_cid: dict[str, list[DiscoveredMatch]] = {}
        by_match: dict[str, list[DiscoveredMatch]] = {}
        for match in discovered:
            by_cid.setdefault(match.market.condition_id, []).append(match)
            by_match.setdefault(match.match_id, []).append(match)
        skip_cids: set[str] = set()
        for match_id, group in by_match.items():
            conditions = {item.market.condition_id for item in group}
            if len(conditions) > 1:
                logger.warning(
                    "discovery yielded the match id %s twice: rejecting the tuple", match_id
                )
                skip_cids.update(conditions)
        live_cids: set[str] = set()
        for cid in sorted(by_cid):
            group = by_cid[cid]
            if len(group) > 1:
                logger.warning("discovery yielded the condition %s twice: rejecting the tuple", cid)
                continue
            if cid in skip_cids:
                continue
            live_cids.add(cid)
            self._handle_match(group[0], now)
        self._abandon_missing(live_cids)
        return live_cids

    def _reap_finished_tasks(self, now: float) -> None:
        """Resolve tasks that have exited against own cleanup or the durable final.

        A picker skip (`waiting_for_feed`) is not a failure: it waits for the next discovery.
        """
        for record in self._matches_by_cid.values():
            task = record.task
            if task is None or not task.done():
                continue
            record.task = None
            cancelled = task.cancelled()
            self._observe_task_result(record.match_id, task)
            cid = record.handoff.market.condition_id
            if own_execution_cleanup(record.match_id, cid):
                record.completed = True
                continue
            if _own_durable_final(TRADER_DIR, record.match_id, cid):
                record.completed = True
                continue
            if cancelled:
                continue
            if task.exception() is not None:
                self._register_unresolved_failure(record, now)
                continue
            if record.waiting_for_feed:
                continue
            if not _own_archive(TRADER_DIR, record.match_id, cid) and not record.no_snapshot:
                record.no_snapshot = True
                self._notify_match(
                    record,
                    session_feed_dead_message(
                        session_alert_from_match(record.handoff, self._mode),
                        str(record.feed_source),
                        _dead_feed_id(record),
                    ),
                )
            self._register_unresolved_failure(record, now)

    def _observe_task_result(self, match_id: str, task: asyncio.Task[None]) -> None:
        """Retrieve a finished worker's exception so asyncio cannot drop it."""
        if task.cancelled():
            logger.warning("trader match task cancelled match=%s", match_id)
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "trader match task failed match=%s: %r",
                match_id,
                exc,
            )

    def _handle_match(self, discovered: DiscoveredMatch, now: float) -> None:
        """Reconcile one discovered match against its in-process record."""
        cid = discovered.market.condition_id
        choice = _session_archive_decision(discovered)
        effective = replace(discovered, match_id=choice.match_id)
        identity = _match_bindings(effective)
        record = self._matches_by_cid.get(cid)
        if record is None:
            record = _ManagedMatch(
                match_id=effective.match_id,
                handoff=effective,
                identity=identity,
                task=None,
                attempts=0,
                next_eligible=0.0,
                completed=False,
                exhausted=False,
                announced=False,
            )
            self._matches_by_cid[cid] = record
            self._continue_after_handoff(record, choice, now)
            return
        worker = self._worker_by_cid.get(cid)
        if worker is not None:
            record.identity = _align_identity_to_worker(record.identity, worker)
            identity = _align_identity_to_worker(identity, worker)
        if record.identity != identity:
            if record.completed or _own_durable_final(TRADER_DIR, record.match_id, cid):
                return
            if record.is_pinned(TRADER_DIR):
                self._log_skip(record, "pinned_binding", None, warning=True)
                return
            self._log_rebind(record, effective)
            _accept_handoff(record, effective)
        else:
            record.handoff = effective
        self._continue_after_handoff(record, choice, now)

    def _continue_after_handoff(
        self, record: _ManagedMatch, choice: _ArchiveChoice, now: float
    ) -> None:
        """Skip or launch after the record holds its bindings."""
        if choice.skip_reason is not None:
            self._log_skip(record, choice.skip_reason, choice.archive_cid, False)
            return
        self._start_or_defer(record, now)

    def _start_or_defer(self, record: _ManagedMatch, now: float) -> None:
        """Launch this record's worker, or leave it for a later discovery cycle.

        A durable final, own cleanup, or an own Steam pin closes the record. `_launch` owns the last
        gate: a condition id still held by a live worker or an Engine market waits
        for that market to detach.
        """
        if record.task is not None or record.completed:
            return
        cid = record.handoff.market.condition_id
        if _own_durable_final(TRADER_DIR, record.match_id, cid):
            record.completed = True
            self._log_skip(record, "own_final", cid, False)
            return
        if own_execution_cleanup(record.match_id, cid):
            record.completed = True
            self._log_skip(record, "own_cleanup", cid, False)
            return
        if _own_steam_pin(TRADER_DIR, record.match_id, cid):
            record.completed = True
            self._log_skip(record, "steam_pin", cid, warning=True)
            return
        if record.exhausted or now < record.next_eligible:
            return
        self._launch(record)

    async def _detach_quiet_missing(self, live_cids: set[str]) -> None:
        """Detach unproven leftovers whose sidecar left discovery. Skip a still-running task."""
        for cid in tuple(self._quiet):
            if cid in live_cids:
                continue
            record = self._matches_by_cid.get(cid)
            if record is not None and record.task is not None:
                continue
            await self.detach(cid)

    def _abandon_missing(self, live_cids: set[str]) -> None:
        """Close a no-snapshot worker whose condition left the live list.

        A picker skip (`waiting_for_feed`) is not done: one empty cycle must
        not pin the record. The next emit retries the picker.
        """
        for cid, record in self._matches_by_cid.items():
            if cid in live_cids:
                continue
            if record.completed or record.task is not None:
                continue
            if record.no_snapshot:
                record.completed = True
                logger.warning("trader session abandoned: match %s feed dead", record.match_id)

    def _log_skip(
        self,
        record: _ManagedMatch,
        reason: str,
        archive_cid: str | None,
        warning: bool,
    ) -> None:
        """Log one skip decision; the same reason+ids on a later tick stay quiet."""
        cid = record.handoff.market.condition_id
        key = f"{reason}:{record.match_id}:{cid}:{archive_cid}"
        if record.logged_skip == key:
            return
        record.logged_skip = key
        line = (
            "trader skip: reason=%s match_id=%s cid=%s record_cid=%s archive_cid=%s "
            "steam_match_id=%s grid_series_id=%s"
        )
        args = (
            reason,
            record.match_id,
            cid,
            cid,
            archive_cid,
            record.handoff.steam_match_id,
            record.handoff.market.grid_series_id,
        )
        if warning:
            logger.warning(line, *args)
            return
        logger.info(line, *args)

    def _log_rebind(self, record: _ManagedMatch, discovered: DiscoveredMatch) -> None:
        """Log one binding change from the record's current id to the new handoff."""
        logger.info(
            "trader rebind: old_match_id=%s new_match_id=%s cid=%s map=%s "
            "steam_match_id=%s grid_series_id=%s",
            record.match_id,
            discovered.match_id,
            discovered.market.condition_id,
            discovered.map_number,
            discovered.steam_match_id,
            discovered.market.grid_series_id,
        )

    def _launch(self, record: _ManagedMatch) -> None:
        """Create the match archive dir and spawn the picker plus worker.

        Unknown game identity completes the record without a task.
        """
        cid = record.handoff.market.condition_id
        if self.cid_in_use(cid):
            self._log_skip(record, "cid_in_use", self._live_owner_match_id(cid), False)
            return
        game = record.handoff.game
        if (
            game not in GAME_PROFILES
            or GAME_PROFILES[game].primary.profile_name not in self._models
        ):
            logger.warning(
                "trader reject unknown game identity match=%s game=%s",
                record.match_id,
                game,
            )
            record.completed = True
            return
        archive_dir = match_archive_dir(TRADER_DIR, record.match_id)
        archive_dir.mkdir(parents=True, exist_ok=True)
        record.waiting_for_feed = False
        logger.info(
            "trader launch: match_id=%s cid=%s archive_id_kind=%s steam_match_id=%s "
            "grid_series_id=%s map=%s",
            record.match_id,
            cid,
            archive_id_kind(record.match_id),
            record.handoff.steam_match_id,
            record.handoff.market.grid_series_id,
            record.handoff.map_number,
        )
        record.record_only = record.handoff.record_only
        record.task = asyncio.create_task(
            self._pick_and_run(record), name=f"match:{record.match_id}"
        )

    async def _pick_and_run(self, record: _ManagedMatch) -> None:
        """Pick a feed, announce once, and run MatchWorker until it returns.

        No usable source yet is not a crash: mark the wait so the next discovery retries.
        """
        game = record.handoff.game
        choice = await select_feed(record.handoff, self._map_ended)
        if choice is None:
            record.waiting_for_feed = True
            self._log_skip(record, "no_usable_feed", None, False)
            return
        feed = choice.feed
        record.feed_source = feed.source
        record.oddin_match_id = choice.handoff.oddin_match_id
        if not record.announced:
            record.announced = True
            started = session_started_message(session_alert_from_match(record.handoff, self._mode))
            self._notify_match(record, started)
        profile_name = strategy_profile_name(game=game, feed_source=feed.source)
        worker = MatchWorker(
            self,
            replace(choice.handoff, record_only=record.record_only),
            self._models[profile_name],
            self._mode,
            feed,
            feed.stale_seconds,
        )
        await worker.run()

    def _notify_match(self, record: _ManagedMatch, message: str) -> None:
        """Log one session alert and page it; a record-only launch only logs the suppressed page."""
        if record.record_only:
            logger.info("record-only alert suppressed match=%s: %s", record.match_id, message)
            return
        logger.info("%s", message)
        notify_in_background(message)

    def _register_unresolved_failure(self, record: _ManagedMatch, now: float) -> None:
        """Count one unresolved failure and schedule the standard restart."""
        if record.exhausted:
            return
        record.attempts += 1
        if record.attempts > MAX_CRASH_RESTARTS:
            record.exhausted = True
            logger.warning(
                "session for match %s failed %d times without a durable final: no more restarts",
                record.match_id,
                record.attempts,
            )
            self._notify_match(
                record,
                session_exhausted_message(session_alert_from_match(record.handoff, self._mode)),
            )
            return
        backoff = RESTART_BACKOFF_SECONDS[record.attempts - 1]
        record.next_eligible = now + backoff

    async def teardown(self) -> None:
        """Latch, stop match tasks, drain, cancel, then Engine.shutdown."""
        self._latch.close()
        for record in self._matches_by_cid.values():
            task = record.task
            if task is not None and not task.done():
                task.cancel()
        tasks = [record.task for record in self._matches_by_cid.values() if record.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for cid in list(self.engine.metas):
            await stop_quoter(self.engine, cid)
        await self._latch.drain(DRAIN_TIMEOUT_S)
        with suppress(Exception):
            await self.engine.gateway.cancel_all()
        try:
            await self.engine.shutdown()
        except Exception as exc:
            logger.warning("trader engine shutdown failed: %s", type(exc).__name__)
        self.close()

    def close(self) -> None:
        """Restore patched fork globals, drop the temp config dir, release the flock."""
        if self._closed:
            return
        self._closed = True
        close_engine_resources(self.engine)
        restore_engine_classes(self._class_restore)
        polymaker_engine.ExecutionGateway = self._original_gateway
        polymaker_engine.construct_quotes = cast(Any, self._original_quotes)
        polymaker_engine.reconcile = cast(Any, self._original_reconcile)
        self._config_dir.cleanup()
        self._lock.close()


def lock_or_exit(mode: ExecutionMode) -> FileLock:
    """Take the wallet flock or exit 1 when another process already holds it."""
    try:
        return acquire_file_lock(wallet_db_path(mode))
    except FileLockHeld:
        logger.error("trader wallet lock is held; another process is running")
        sys.exit(1)
