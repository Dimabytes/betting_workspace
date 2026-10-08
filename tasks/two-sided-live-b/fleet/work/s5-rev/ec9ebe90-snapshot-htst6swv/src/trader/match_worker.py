"""One match on a shared wallet Engine: archive, quote, match final, then fence."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import asyncio
import math
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from polymaker.config import MarketEntry
from polymaker.domain import Fill, OpenOrder, Quote, Side

from shared.constants.paths import TRADER_DIR
from shared.constants.strategy import EXIT_FEED_STALE_SECONDS, LIVE_MAX_POSITION_LEVELS
from shared.utils.board_features import BOARD_REACTION_SECONDS, BoardHistory
from shared.utils.dota_features import SnapshotHistory, snapshot_history_levels
from shared.utils.engine_cadence import read_engine_cadence
from shared.utils.log import get_logger
from shared.utils.match_time import HORN_OFFSET_SECONDS, NS_PER_SECOND
from shared.utils.telonex_book import MAX_BOOK_AGE_SECONDS, PAIR_SUM_TOLERANCE
from shared.utils.trading import drop_share_residue, maker_rebate_usdc
from strategy.policy import follow300_policy
from strategy.signals import core_book_p
from strategy.types import (
    FreshnessLimits,
    KillGate,
    KillGateUpdate,
    KillWait,
    LimitsUpdate,
    MarketLimits,
    RawDeltaSignal,
    SignalUpdate,
)
from trader.archive_paths import (
    archive_has_prior_session,
    match_archive_dir,
    write_execution_cleanup,
)
from trader.bindings import DiscoveredMatch, MatchStart, SessionPnl, TeamSides
from trader.clip_rules import ClipChoice, choose_clip
from trader.collector_sidecars import FreshSidecar, SidecarScan
from trader.core_persistence import CoreSchemaError, CoreSessionKey
from trader.core_recovery import RecoveryCoordinator, prove_token_balances
from trader.core_session_io import (
    SessionIdentity,
    apply_and_persist,
    load_core_snapshot,
    persist_core_snapshot,
)
from trader.core_trace import CoreTrace, header_for_market, start_market_trace, try_open_trace
from trader.dust_sweep import DustSweeper
from trader.engine_seams import attach_market, sell_drop_reason, stop_quoter
from trader.fill_parsing import split_fill_key
from trader.game_profile import strategy_catalog, strategy_profile_name
from trader.live_feed import (
    FeedEvent,
    GameSnapshot,
    KillTick,
    LiveFeed,
    MatchPhase,
    horn_is_pinnable,
)
from trader.market_prior import fetch_market_prior
from trader.match_meta import finalize_match, pin_horn_from_event, write_match_start
from trader.model_server import ModelPredictionError, ModelServer, yes_fair_from_model
from trader.notify import (
    notify_in_background,
    notify_session_finished,
    session_alert_from_match,
)
from trader.sell_drop_watch import SellDropWatch
from trader.session_binding import (
    build_sidecar_meta,
    parse_min_order_decimal,
    parse_tick_decimal,
    provenance_sidecar_preview,
    select_current_sidecar,
    sidecar_binding,
)
from trader.session_core import (
    LiveCore,
    core_books,
    core_now_ns,
    entry_block_from_reason,
    exit_state_label,
    feed_core,
    queue_resume_recovery,
)
from trader.session_engine import FreshnessWatchdog, StrategyCell
from trader.session_journal import (
    TERMINAL_REASON_FINISHED,
    FaultReporter,
    SessionJournal,
    open_session_journal,
)
from trader.session_quoting import (
    fill_ts_utc,
    pin_place_tick,
    read_raw_pair,
    window_reason,
)
from trader.session_types import (
    PHASE_DECISION,
    PHASE_SETUP,
    PHASE_SIDECAR_REFRESH,
    EntryBlock,
    ModelFair,
    RawBookPair,
    SessionEndSnapshot,
    SidecarBinding,
    SidecarUnavailable,
    SignalDecision,
    SignalReason,
    TradingDisabled,
)
from trader.trading_mode import ExecutionMode
from trader.wallet_store import WalletStateStore

if TYPE_CHECKING:
    from trader.wallet_host import WalletHost

logger = get_logger(__name__)

FENCE_TIMEOUT_S = 20.0


def _wallet_store(store: object) -> WalletStateStore | None:
    if isinstance(store, WalletStateStore):
        return store
    return None


def _outbox_head(store: WalletStateStore) -> int:
    row = store._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM fill_outbox").fetchone()
    return 0 if row is None else int(row[0])


@dataclass(frozen=True)
class _GatedPair:
    """YES/NO books plus the first gate reason, if any."""

    raw: RawBookPair
    market_p_radiant: float | None
    reason: SignalReason | None


@dataclass(frozen=True)
class _BoardSource:
    """Latest fresh table event a delayed board quote may reuse."""

    event: FeedEvent
    received_ns: int


@dataclass(frozen=True)
class _LatchedFair:
    """Model fair from the latched prior, plus the first prior/model reason."""

    model_fair: ModelFair | None
    reason: SignalReason | None


RECOVERY_PROOF_MIN_INTERVAL_S = 5.0


class MatchWorker:
    """One match: archive-first, attach to the shared Engine, fence after post-game."""

    def __init__(
        self,
        host: "WalletHost",
        discovered: DiscoveredMatch,
        model: ModelServer,
        mode: ExecutionMode,
        feed: LiveFeed,
        feed_timeout_seconds: float,
        exit_timeout_seconds: float | None = None,
    ) -> None:
        """Bind host, discovery, model, mode, feed, and freshness timeouts."""
        self._host = host
        self._discovered = discovered
        self._model = model
        self._mode: ExecutionMode = mode
        self._feed = feed
        self._cell = StrategyCell()
        self._watchdog = FreshnessWatchdog(
            self._on_entry_feed_timeout,
            feed_timeout_seconds,
            self._on_exit_feed_timeout,
            EXIT_FEED_STALE_SECONDS if exit_timeout_seconds is None else exit_timeout_seconds,
        )
        history_policy = strategy_catalog(game=discovered.game, feed_source=feed.source).history
        self._history = SnapshotHistory(history_policy)
        self._board = BoardHistory()
        self._board_source: _BoardSource | None = None
        self._board_handles: list[asyncio.TimerHandle] = []
        self._last_second = 0
        self._last_raw_delta = 0.0
        self._core: LiveCore | None = None
        self._last_buy_unix: float | None = None
        self._maker_rebate_usdc = 0.0
        self._journal: SessionJournal | None = None
        self._reporter: FaultReporter | None = None
        self._attached = False
        self._quoting = False
        self._quiesced = False
        self._cid = discovered.market.condition_id
        self._yes = discovered.market.yes_token_id
        self._no = discovered.market.no_token_id
        self._yes_is_radiant = discovered.market.yes_is_radiant
        self._binding: SidecarBinding | None = None
        self._last_tick_str = ""
        self._observed_tick = 0.0
        self._sidecar_usable = True
        self._scheduled_cancels: list[asyncio.Task[None]] = []
        self._prior: float | None = None
        self._prior_task: asyncio.Task[None] | None = None
        self._lol_prior_done = False
        self._recovery_task: asyncio.Task[None] | None = None
        self._recovery_proof_at = 0.0
        self._sell_drops = SellDropWatch()
        self._horn_pinned = False
        self._dust = DustSweeper(host, self._cid, self._yes, self._no, mode)

    @property
    def core(self) -> LiveCore | None:
        return self._core

    async def run(self) -> None:
        """Archive every feed event; trade only when mode, model and sidecar pin."""
        start = self._discovered.with_model(self._model.model_reference)
        archive_dir = match_archive_dir(TRADER_DIR, self._discovered.match_id)
        prior = archive_has_prior_session(archive_dir)
        journal: SessionJournal | None = None
        try:
            first = None
            async for event in self._feed.ticks():
                if isinstance(event, KillTick):
                    self._on_kill_tick(event)
                    continue
                self._board.record_table(
                    core_now_ns(), event.snapshot.deaths_radiant, event.snapshot.deaths_dire
                )
                self._record_history(event.snapshot)
                if first is None:
                    first = event
                    start = self._pin_feed_orientation(start, event)
                    write_match_start(start, event)
                    self._maybe_pin_horn(event)
                    journal = self._open_journal(archive_dir, start, prior)
                    self._journal = journal
                    if event.snapshot.finished:
                        await self._finish_terminal()
                        return
                    await self._try_attach(archive_dir, journal)
                    continue
                self._maybe_pin_horn(event)
                if self._quoting:
                    await self.handle_event(event)
                    self._dust.schedule()
                if not event.snapshot.finished:
                    continue
                await self._finish_terminal()
                return
        finally:
            with suppress(asyncio.CancelledError):
                await asyncio.shield(self._quiesce(False))
            self._stop_feed_timers()
            for task in self._scheduled_cancels:
                task.cancel()
            if self._prior_task is not None:
                self._prior_task.cancel()
            if self._recovery_task is not None:
                self._recovery_task.cancel()
            self._dust.cancel()

    def _stop_feed_timers(self) -> None:
        self._watchdog.disarm()
        for handle in self._board_handles:
            handle.cancel()
        self._board_handles.clear()

    def _record_history(self, snapshot: GameSnapshot) -> None:
        """Tape non-terminal snapshot levels for the shared history features."""
        if not snapshot.finished:
            self._history.record(snapshot.second, snapshot_history_levels(snapshot))

    def _pin_feed_orientation(self, start: MatchStart, event: FeedEvent) -> MatchStart:
        """Lock YES↔Radiant from the first tick onto the worker, discovery, and MatchStart."""
        yes_is_radiant = event.yes_is_radiant
        self._yes_is_radiant = yes_is_radiant
        market = start.market
        outcome_0, outcome_1 = market.outcome_0_name, market.outcome_1_name
        sides = (
            TeamSides(radiant=outcome_0, dire=outcome_1)
            if yes_is_radiant
            else TeamSides(radiant=outcome_1, dire=outcome_0)
        )
        market = replace(market, yes_is_radiant=yes_is_radiant)
        self._discovered = replace(self._discovered, sides=sides, market=market)
        return replace(start, sides=sides, market=market)

    def _open_journal(
        self, archive_dir: Path, start: MatchStart, prior: bool
    ) -> SessionJournal | None:
        """Pin or read provenance. Archive-only on record_only, mode/model mismatch, or corrupt start."""
        if self._discovered.record_only:
            return None
        preview = provenance_sidecar_preview(
            self._discovered, self._host.archive_for(self._discovered.game)
        )
        journal = open_session_journal(
            archive_dir,
            start,
            preview,
            prior,
            self._host.git_commit,
            self._mode,
            clip=self._clip_choice(preview),
        )
        if journal is None:
            return None
        try:
            pinned = journal.first_start(start.match_id, start.market.condition_id)
        except TradingDisabled:
            journal.close()
            return None
        if pinned.execution_mode != self._mode:
            logger.warning("trader archive-only: pinned execution_mode does not match boot")
            journal.close()
            return None
        if (
            pinned.model_name != start.model.name
            or pinned.model_trained_at != start.model.trained_at
        ):
            logger.warning("trader archive-only: pinned model does not match production/")
            journal.close()
            return None
        return journal

    def _clip_choice(self, preview: FreshSidecar | None) -> ClipChoice:
        """Title-suffix clip of this feed's table: clips.<game> primary, clips.<game>-<feed> satellite."""
        profile_name = strategy_profile_name(
            game=self._discovered.game, feed_source=self._feed.source
        )
        title = None if preview is None else preview.event_title
        return choose_clip(self._host.clip_tables[profile_name], title)

    def _maybe_pin_horn(self, event: FeedEvent) -> None:
        """Write horn_at_utc from the first tick whose clock is past 0."""
        if self._horn_pinned:
            return
        pin_horn_from_event(self._discovered.match_id, event)
        if horn_is_pinnable(event):
            self._horn_pinned = True

    async def _try_attach(self, archive_dir: Path, journal: SessionJournal | None) -> None:
        """Attach this market on the shared Engine when provenance and sidecar allow it."""
        if journal is None:
            return
        reporter = FaultReporter(journal)
        self._reporter = reporter
        try:
            pinned = journal.first_start(self._discovered.match_id, self._cid)
            sidecar_meta = build_sidecar_meta(
                self._discovered,
                self._host.archive_for(self._discovered.game),
                pinned.binding,
            )
            profile = self._host.engine.cfg.profile_for(
                MarketEntry(
                    condition_id=self._cid,
                    profile=strategy_profile_name(
                        game=self._discovered.game, feed_source=self._feed.source
                    ),
                )
            )
        except Exception as exc:
            reporter.report(PHASE_SETUP, type(exc).__name__)
            return
        self._binding = sidecar_meta.binding
        self._last_tick_str = sidecar_meta.tick_size_str
        self._observed_tick = sidecar_meta.meta.tick_size
        self._sidecar_usable = True
        self._host.engine.catalog.upsert_market(sidecar_meta.meta)
        preview = provenance_sidecar_preview(
            self._discovered, self._host.archive_for(self._discovered.game)
        )
        clip = pinned.clip or self._clip_choice(preview)
        logger.info(
            "trader clip match=%s clip=%s reason=%s",
            self._discovered.match_id,
            clip.clip_usdc,
            clip.reason,
        )
        if clip.reason == "default":
            title = None if preview is None else preview.event_title
            notify_in_background(
                "trader default clip "
                f"match={self._discovered.match_id} game={self._discovered.game} "
                f"title={title} clip={clip.clip_usdc}"
            )
        if self._host.cid_in_use(self._cid):
            logger.warning(
                "trader attach refused: condition already live match=%s",
                self._discovered.match_id,
            )
            return
        attach_market(
            self._host.engine, sidecar_meta.meta, profile, self._cell, self._host.readiness
        )
        self.open_core(
            level_usdc=clip.clip_usdc,
            min_order_size=sidecar_meta.meta.min_order_size,
            tick_size=sidecar_meta.meta.tick_size,
            trace=try_open_trace(archive_dir),
        )
        self._attached = True
        self._restore_open_round(journal)
        self._host.register_worker(self._cid, {self._yes, self._no}, self)
        self._quoting = True

    async def handle_event(self, event: FeedEvent) -> None:
        """One feed event through the join-bid cell; a fault never kills the feed."""
        try:
            await self._on_event(event)
        except Exception as exc:
            if self._reporter is not None:
                self._reporter.report(PHASE_DECISION, type(exc).__name__)
            self._cell.clear()

    async def _on_event(self, event: FeedEvent) -> None:
        """Publish the model cell and wake the shared quoter."""
        self._maybe_start_prior(event)
        self._maybe_verify_recovery()
        self._board_source = None
        received_ns = core_now_ns()
        if not event.snapshot.finished and not self._history.check_required_lags(
            event.snapshot.second
        ):
            if self._journal is not None:
                self._journal.write_history_gap(event.snapshot.second)
            return
        arrived_at = time.time()
        fresh = self._watchdog.consume()
        if fresh:
            self._board_source = _BoardSource(event=event, received_ns=received_ns)
        self._quote_pm(event, arrived_at, fresh, received_ns)

    def _quote_pm(self, event: FeedEvent, arrived_at: float, fresh: bool, source_ns: int) -> None:
        """Publish the PM cell and wake the quoter. Returns early if journal write fails."""
        gated = self._gate_pair(window_reason(event.snapshot, fresh))
        latched = self._latch_fair(event.snapshot, gated.market_p_radiant, gated.reason)
        reason = latched.reason if latched.reason is not None else SignalReason.MODEL
        model_fair = latched.model_fair
        self._last_second = event.snapshot.second
        self._cell.publish(model_fair.yes if model_fair is not None else None)
        self._enqueue_core_signal(event, reason, gated.market_p_radiant, core_now_ns(), source_ns)
        decision = self._compute_decision(event, arrived_at, gated, latched, reason)
        if self._journal is not None:
            try:
                self._journal.write_signal(decision)
            except Exception as exc:
                if self._reporter is not None:
                    self._reporter.report(PHASE_DECISION, type(exc).__name__)
                self._cell.clear()
                return
        self._host.engine._wake_cid(self._cid)

    def _gate_pair(self, reason: SignalReason | None) -> _GatedPair:
        """Raw books for the journal. Model input and the anchor use core_books.

        A raw book that fails its own check keeps that reason. After a good raw
        read, core_books is None only when stripping our orders empties a side:
        that is OWN_LIQUIDITY_ONLY, not a missing book.
        """
        raw = RawBookPair(None, None, None, None, None, None)
        if reason is not None:
            return _GatedPair(raw, None, reason)
        if not self._sidecar_usable:
            return _GatedPair(raw, None, SignalReason.SIDECAR_FAULT)
        if not self._books_ready():
            return _GatedPair(raw, None, SignalReason.MISSING_BOOK)
        yes_book = self._host.engine.md.book(self._yes)
        no_book = self._host.engine.md.book(self._no)
        pair_read = read_raw_pair(yes_book, no_book)
        raw = pair_read.raw
        if pair_read.reason is not None:
            return _GatedPair(raw, None, pair_read.reason)
        core = self._core
        assert core is not None
        books = core_books(
            core=core,
            yes=yes_book,
            no=no_book,
            now_ns=core_now_ns(),
            live=self._mode == "live",
        )
        if books is None:
            return _GatedPair(raw, None, SignalReason.OWN_LIQUIDITY_ONLY)
        market_p_radiant = core_book_p(books=books, limits=core.state.limits)
        if market_p_radiant is None:
            return _GatedPair(raw, None, SignalReason.PAIR_BROKEN)
        return _GatedPair(raw, market_p_radiant, None)

    def _latch_fair(
        self,
        snapshot: GameSnapshot,
        market_p_radiant: float | None,
        reason: SignalReason | None,
    ) -> _LatchedFair:
        prior = self._prior
        if prior is None:
            if reason is None:
                reason = SignalReason.MISSING_PRIOR
            return _LatchedFair(None, reason)
        if reason is not None or market_p_radiant is None:
            return _LatchedFair(None, reason)
        try:
            board = self._board.derive(core_now_ns(), snapshot.deaths_radiant, snapshot.deaths_dire)
            prediction = self._model.predict_fair(
                snapshot, market_p_radiant, prior, self._history, board
            )
            self._last_raw_delta = prediction.raw_delta
            yes_fair = yes_fair_from_model(prediction.fair, self._yes_is_radiant)
            return _LatchedFair(ModelFair(prediction.fair, yes_fair, prediction.raw_delta), reason)
        except ModelPredictionError:
            return _LatchedFair(None, SignalReason.MODEL_ERROR)

    def _on_kill_tick(self, tick: KillTick) -> None:
        """Apply the kill gate immediately and recompute from the received table after reaction."""
        now_ns = core_now_ns()
        self._board.record_board(now_ns, tick.radiant.awaited_deaths, tick.dire.awaited_deaths)
        if self._core is None:
            return
        self._core.enqueue(
            KillGateUpdate(
                now_ns=now_ns,
                gate=KillGate(
                    radiant=KillWait(
                        awaited_deaths=tick.radiant.awaited_deaths,
                        until_ns=now_ns + round(tick.radiant.seconds_left * NS_PER_SECOND),
                    ),
                    dire=KillWait(
                        awaited_deaths=tick.dire.awaited_deaths,
                        until_ns=now_ns + round(tick.dire.seconds_left * NS_PER_SECOND),
                    ),
                ),
            )
        )
        self._host.engine._wake_cid(self._cid)
        self._board_handles = [
            handle
            for handle in self._board_handles
            if not handle.cancelled() and handle.when() > asyncio.get_running_loop().time()
        ]
        handle = asyncio.get_running_loop().call_later(BOARD_REACTION_SECONDS, self._quote_board)
        self._board_handles.append(handle)

    def _quote_board(self) -> None:
        source = self._board_source
        if source is None or not self._quoting or self._quiesced:
            return
        age_seconds = (core_now_ns() - source.received_ns) / NS_PER_SECOND
        if source.event.snapshot.finished or age_seconds > self._watchdog.entry_timeout_seconds:
            return
        self._quote_pm(source.event, time.time(), True, source.received_ns)

    def _enqueue_core_signal(
        self,
        event: FeedEvent,
        reason: SignalReason,
        market_p_radiant: float | None,
        now_ns: int,
        source_ns: int,
    ) -> None:
        """Queue clock and signal for the next construct_quotes cycle."""
        if self._core is None:
            return
        signal = None
        if reason is SignalReason.MODEL and market_p_radiant is not None:
            signal = RawDeltaSignal(
                predicted_delta=self._last_raw_delta,
                source_received_ns=source_ns,
                received_ns=now_ns,
                anchor_p=market_p_radiant,
                deaths_radiant=event.snapshot.deaths_radiant,
                deaths_dire=event.snapshot.deaths_dire,
            )
        feed_core(
            self._core,
            now_ns=now_ns,
            second=event.snapshot.second,
            paused=event.snapshot.paused,
            finished=event.snapshot.finished,
            signal=signal,
        )
        self._alert_stale_cancels()

    def _alert_stale_cancels(self) -> None:
        """Push one line per order whose cancel the venue never proved."""
        if self._core is None:
            return
        for order_id in self._core.take_stale_cancels():
            notify_in_background(
                f"trader cancel unproven: match {self._discovered.match_id} order {order_id}"
            )

    def _compute_decision(
        self,
        event: FeedEvent,
        arrived_at: float,
        gated: _GatedPair,
        latched: _LatchedFair,
        reason: SignalReason,
    ) -> SignalDecision:
        """Assemble the journaled decision; call after enqueue so entry_block previews it."""
        model_fair = latched.model_fair
        model_evaluated = model_fair is not None
        raw_delta = model_fair.raw_delta if model_fair is not None else None
        token_id: str | None = None
        price = 0.0
        block = EntryBlock.NONE
        exit_state = "none"
        pos_yes = 0.0
        pos_no = 0.0
        if self._core is not None:
            core_state = self._core.state
            block = entry_block_from_reason(self._core.preview_block_reason())
            exit_state = exit_state_label(core_state)
            pos_yes = core_state.inventory[0].qty
            pos_no = core_state.inventory[1].qty
            index = core_state.episode_token_index
            if index is not None:
                token_id = self._core.token_id(index)
                if core_state.rungs:
                    price = core_state.rungs[0].price
        return SignalDecision(
            snapshot=event.snapshot,
            arrived_at=arrived_at,
            raw=gated.raw,
            market_p_radiant=gated.market_p_radiant,
            market_radiant_prior=self._prior,
            radiant_fair=model_fair.radiant if model_fair is not None else None,
            yes_fair=model_fair.yes if model_fair is not None else None,
            reason=reason,
            entry_block=block,
            entry_token_id=token_id,
            entry_price=price,
            exit_state=exit_state,
            pos_yes=pos_yes,
            pos_no=pos_no,
            feed_source=event.source,
            feed_received_at_utc=event.received_at_utc,
            model_evaluated=model_evaluated,
            raw_delta=raw_delta,
        )

    def _maybe_start_prior(self, event: FeedEvent) -> None:
        """Latch the map prior once: LoL from the book tape, Dota from prices-history."""
        if self._prior is not None or self._prior_task is not None or self._lol_prior_done:
            return
        if event.snapshot.phase not in {MatchPhase.PRE_HORN, MatchPhase.IN_PROGRESS}:
            return
        if self._discovered.game == "lol":
            self._latch_lol_book_prior(event)
            return
        anchor_ts = event.horn_unix_seconds - HORN_OFFSET_SECONDS
        self._prior_task = asyncio.get_running_loop().create_task(self._load_prior(anchor_ts))

    def _latch_lol_book_prior(self, event: FeedEvent) -> None:
        """Freeze the last two-sided mid in [horn - 61s, horn). No prices-history fallback."""
        horn = event.horn_unix_seconds
        now = time.time()
        if now < horn:
            return
        prior = self._host.lol_prior.prior(
            self._yes, self._no, horn, yes_is_radiant=self._yes_is_radiant
        )
        # A print still in flight can carry an exchange time inside the window.
        if prior is None and now < horn + 2:
            return
        self._prior = prior
        self._lol_prior_done = True

    async def _load_prior(self, anchor_ts: int) -> None:
        """Fetch the map-load prior off the event loop; HTTP failure clears the task so it retries."""
        market = self._discovered.market
        try:
            prior = await asyncio.to_thread(
                fetch_market_prior,
                market.yes_token_id,
                market.no_token_id,
                anchor_ts,
                yes_is_radiant=self._yes_is_radiant,
            )
        except Exception as exc:
            logger.warning("trader market prior failed: %s", type(exc).__name__)
            self._prior_task = None
            return
        self._prior = prior

    def _books_ready(self) -> bool:
        """True when both YES and NO have a book for this attach generation."""
        return self._token_book_ready(self._yes) and self._token_book_ready(self._no)

    def _token_book_ready(self, token_id: str) -> bool:
        """True when this token has a post-attach snapshot or a book ts after attach."""
        book = self._host.engine.md.book(token_id)
        last_update_ts = 0.0 if book is None else book.last_update_ts
        return self._host.readiness.ready(token_id, last_update_ts)

    def _write_off_sell_residue(self, token_id: str) -> None:
        """Zero sqlite when a SELL leaves less than one CLOB share tick: float residue.

        A remainder of whole share ticks under the order minimum is real inventory
        the chain still holds. It cannot be sold, it settles at redemption, and
        zeroing it strands the shares, so only sub-tick residue is written off.
        """
        size = self._host.store.position(token_id).size
        if size > 0.0 and drop_share_residue(size) == 0.0:
            self._host.store.zero_token_sizes({token_id})

    def _identity(self) -> SessionIdentity:
        return SessionIdentity(
            session_id=self._cid,
            key=CoreSessionKey(
                condition_id=self._cid,
                game=self._discovered.game,
                yes_token=self._yes,
                no_token=self._no,
                yes_is_radiant=self._yes_is_radiant,
            ),
        )

    def _coordinator(self) -> RecoveryCoordinator | None:
        store = _wallet_store(self._host.store)
        if store is None:
            return None
        return RecoveryCoordinator(
            store=store,
            session_id=self._cid,
            yes_token=self._yes,
            no_token=self._no,
            min_order_size=self._host.engine.metas[self._cid].min_order_size,
        )

    def _restore_open_round(self, journal: SessionJournal) -> None:
        """Lock a restored map only while it holds inventory or an outstanding BUY."""
        min_size = self._host.engine.metas[self._cid].min_order_size
        self._write_off_sell_residue(self._yes)
        self._write_off_sell_residue(self._no)
        yes_size = self._host.store.position(self._yes).size
        no_size = self._host.store.position(self._no).size
        self._cell.resume_exit = yes_size >= min_size or no_size >= min_size
        last_buy_unix = None
        try:
            last_buy_unix = journal.scan_open_round(self._yes, self._no, min_size).last_buy_unix
        except TradingDisabled:
            last_buy_unix = None
        self._last_buy_unix = last_buy_unix
        has_orders = bool(self._core.state.orders) if self._core is not None else False
        store = _wallet_store(self._host.store)
        if store is None:
            kind = "recovery" if yes_size > 0 or no_size > 0 or has_orders else "fresh"
        else:
            kind = (
                RecoveryCoordinator(
                    store=store,
                    session_id=self._cid,
                    yes_token=self._yes,
                    no_token=self._no,
                    min_order_size=min_size,
                )
                .classify(
                    key=self._identity().key,
                    has_orders=has_orders,
                )
                .kind
            )
        if kind != "recovery":
            return
        self._begin_recovery(last_buy_unix=last_buy_unix)

    def _begin_recovery(self, *, last_buy_unix: float | None) -> None:
        if self._core is None:
            return
        self._last_buy_unix = last_buy_unix
        queue_resume_recovery(
            self._core,
            yes_size=self._host.store.position(self._yes).size,
            no_size=self._host.store.position(self._no).size,
            min_size=self._host.engine.metas[self._cid].min_order_size,
            now_ns=core_now_ns(),
        )
        self._core.drain_apply()
        self.persist_core_checkpoint()
        logger.warning("trader recovery: blocking buys session=%s", self._cid[:12])

    def continue_recovery(self, *, rest_yes: float, rest_no: float) -> bool:
        if self._core is None or not self._core.state.recovery_pending:
            return False
        coordinator = self._coordinator()
        if coordinator is None:
            return False
        accepted = coordinator.accept_if_proven(
            core=self._core,
            now_ns=core_now_ns(),
            now_wall_s=time.time(),
            rest_yes=rest_yes,
            rest_no=rest_no,
            last_buy_unix=self._last_buy_unix,
        )
        if accepted:
            self.persist_core_checkpoint()
            logger.warning(
                "trader recovery verified session=%s sell_only=%s",
                self._cid[:12],
                self._core.state.sell_only,
            )
        return accepted

    def _maybe_verify_recovery(self) -> None:
        if (
            self._core is None
            or not self._core.state.recovery_pending
            or self._recovery_task is not None
        ):
            return
        self._recovery_task = asyncio.get_running_loop().create_task(self._verify_recovery())

    async def _verify_recovery(self) -> None:
        try:
            await self.continue_recovery_async()
        except Exception as exc:
            logger.warning("trader recovery proof failed: %s", type(exc).__name__)
        finally:
            self._recovery_task = None

    async def continue_recovery_async(self) -> bool:
        store = _wallet_store(self._host.store)
        if store is None or self._core is None or not self._core.state.recovery_pending:
            return False
        now = time.monotonic()
        if now - self._recovery_proof_at < RECOVERY_PROOF_MIN_INTERVAL_S:
            return False
        self._recovery_proof_at = now
        proven = await prove_token_balances(
            read_balances=self._host.read_fresh_balances,
            yes_token=self._yes,
            no_token=self._no,
            store=store,
            revision_of=lambda: _outbox_head(store),
        )
        if proven is None:
            return False
        return self.continue_recovery(rest_yes=proven[0], rest_no=proven[1])

    def persist_core_checkpoint(self) -> None:
        store = _wallet_store(self._host.store)
        if self._core is None or store is None:
            return
        persist_core_snapshot(store=store, core=self._core, identity=self._identity())

    def note_fill(self, fill: Fill) -> None:
        """Journal one durable fill routed here by token and accumulate maker rebate."""
        if self._journal is None:
            return
        position_after = self._host.engine.state.position(fill.token_id).size
        store = self._host.store
        self._journal.write_fill(
            fill,
            position_after,
            store.ledger_net_cash_for_tokens({self._yes, self._no}),
            self._last_second,
            fill_ts_utc(fill.ts),
            fill.trade_id,
        )
        logger.info(
            "fill match=%s second=%d side=%s price=%s size=%s position_after=%s token=%s",
            self._discovered.match_id,
            self._last_second,
            fill.side.value,
            fill.price,
            fill.size,
            position_after,
            fill.token_id[:12],
        )
        if fill.is_maker:
            self._maker_rebate_usdc += maker_rebate_usdc(price=fill.price, size=fill.size)
        if fill.side is Side.SELL:
            self._write_off_sell_residue(fill.token_id)
            self._dust.schedule()

    def note_place_result(self, placed: list[OpenOrder]) -> None:
        """Map venue ids onto the last planned batch and queue accept/timeout."""
        if self._core is None:
            return
        now_ns = core_now_ns()
        self._apply_core(lambda: self._finish_place(placed, now_ns))
        self._host.engine._wake_cid(self._cid)

    def _finish_place(self, placed: list[OpenOrder], now_ns: int) -> None:
        if self._core is None:
            return
        self._core.note_placed(placed, now_ns)
        self._core.drain_apply()

    def note_cancel_result(self, order_ids: list[str], ok: bool) -> None:
        """Apply cancel outcomes, persist, then retry recovery proof."""
        if self._core is None:
            return
        now_ns = core_now_ns()
        self._apply_core(lambda: self._finish_cancel(order_ids, ok, now_ns))
        self._host.engine._wake_cid(self._cid)

    def _finish_cancel(self, order_ids: list[str], ok: bool, now_ns: int) -> None:
        if self._core is None:
            return
        self._core.note_cancel(order_ids, ok, now_ns)
        self._core.drain_apply()

    def note_core_fill(self, fill: Fill) -> None:
        """Apply a credited ledger fill and persist the new cursor with it."""
        if self._core is None:
            return
        now_ns = core_now_ns()
        self._apply_core(lambda: self._finish_core_fill(fill, now_ns))

    def _finish_core_fill(self, fill: Fill, now_ns: int) -> None:
        if self._core is None:
            return
        _clob, venue_id = split_fill_key(fill.trade_id)
        token_index = self._core.token_index(fill.token_id)
        if token_index is None:
            return
        self._core.note_fill(
            fill_key=fill.trade_id,
            venue_id=venue_id,
            qty=fill.size,
            price=fill.price,
            now_ns=now_ns,
            token_index=token_index,
            side="BUY" if fill.side is Side.BUY else "SELL",
        )
        self._core.drain_apply()

    def note_failed_fill(self, fill: Fill) -> None:
        """Re-enter recovery after sqlite rebuilt the token from remaining fills."""
        del fill
        if self._core is None:
            return
        self._apply_core(lambda: self._finish_failed())

    def _finish_failed(self) -> None:
        if self._core is None:
            return
        self._core.note_recovery(now_ns=core_now_ns())
        self._core.drain_apply()

    def _apply_core(self, apply: Callable[[], None]) -> None:
        store = _wallet_store(self._host.store)
        if self._core is None:
            return
        if store is None:
            apply()
            return
        apply_and_persist(store=store, core=self._core, identity=self._identity(), apply=apply)

    def write_quote(self, placed: list[OpenOrder], canceled: list[str]) -> None:
        """Append one session quote record. Journal I/O is swallowed like write_signal."""
        if self._journal is None or (not placed and not canceled):
            return
        decision: Literal["normal", "reduce_only"] = (
            "reduce_only" if self._cell.forced else "normal"
        )
        try:
            self._journal.write_quote(decision, "model", placed, canceled, self._last_second)
        except Exception as exc:
            if self._reporter is not None:
                self._reporter.report(PHASE_DECISION, type(exc).__name__)
            self._cell.clear()

    def _on_entry_feed_timeout(self) -> None:
        """Wake the quoter and pull resting BUYs; the core keeps the SELL to `exit_stale_s`.

        The core blocks new entries on its own once the signal passes
        `entry_stale_s`, so this handler never touches the signal. Clearing it
        here would also expire the v5 exit gate and drop the resting SELL early.
        """
        self._host.engine._wake_cid(self._cid)
        self._schedule_entry_cancel()

    def _on_exit_feed_timeout(self) -> None:
        """Feed silent long enough: drop fair so quoting pulls the SELL."""
        if self._core is not None:
            self._core.enqueue(SignalUpdate(now_ns=core_now_ns(), signal=None))
        self._cell.clear()
        self._host.engine._wake_cid(self._cid)

    async def cancel_entry_buys(self) -> None:
        """Cancel only BUY orders on this market's tokens. SELLs stay."""
        if not self._attached:
            return
        lock = self._host.engine._locks.get(self._cid)
        if lock is None:
            return
        async with lock:
            buy_ids = [
                order.order_id
                for token_id in (self._yes, self._no)
                for order in self._host.engine.state.orders_for(token_id)
                if order.side is Side.BUY
            ]
            await self._cancel_order_ids(buy_ids)

    def _schedule_entry_cancel(self) -> None:
        """Schedule the scoped BUY cancellation on the running loop, best-effort."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._scheduled_cancels = [task for task in self._scheduled_cancels if not task.done()]
        self._scheduled_cancels.append(loop.create_task(self._safe_entry_cancel()))

    async def _safe_entry_cancel(self) -> None:
        """Run the scoped cancel; a failure is a decision-phase fault, never a raise."""
        try:
            await self.cancel_entry_buys()
        except Exception as exc:
            if self._reporter is not None:
                self._reporter.report(PHASE_DECISION, type(exc).__name__)

    async def _enter_permanent_safe_mode(self) -> None:
        """Close entries for this CID and never retry sidecar refresh in this attach."""
        self._set_sidecar_usable(False)
        self._cell.clear()
        self._host.engine._wake_cid(self._cid)
        try:
            await self.cancel_entry_buys()
        except Exception as cancel_exc:
            logger.warning(
                "trader entry cancel failed during sidecar safe mode: %s",
                type(cancel_exc).__name__,
            )

    async def refresh_sidecar(self, scan: SidecarScan) -> None:
        """Apply one host-level sidecar scan to this attached CID."""
        if self._quiesced or not self._sidecar_usable or not self._attached:
            return
        try:
            await self._refresh_sidecar_once(scan)
        except SidecarUnavailable as exc:
            if self._reporter is not None:
                self._reporter.report(PHASE_SIDECAR_REFRESH, type(exc).__name__)
            await self._enter_permanent_safe_mode()
        except Exception as exc:
            if self._reporter is not None:
                self._reporter.report(PHASE_SIDECAR_REFRESH, type(exc).__name__)
            self._cell.clear()
            self._host.engine._wake_cid(self._cid)
            try:
                await self.cancel_entry_buys()
            except Exception as cancel_exc:
                logger.warning(
                    "trader entry cancel failed during sidecar refresh: %s",
                    type(cancel_exc).__name__,
                )

    async def _refresh_sidecar_once(self, scan: SidecarScan) -> None:
        """Rescan this CID's sidecar and apply tick/min changes under the market lock."""
        lock = self._host.engine._locks.get(self._cid)
        if lock is None:
            return
        async with lock:
            sidecar = select_current_sidecar(scan, self._discovered)
            if sidecar is None or not sidecar.is_tradeable():
                self._set_sidecar_usable(False)
                raise SidecarUnavailable("collector sidecar missing, changed or not tradeable")
            if self._binding is None or sidecar_binding(sidecar) != self._binding:
                self._set_sidecar_usable(False)
                raise SidecarUnavailable("collector sidecar changed its immutable binding")
            new_tick = parse_tick_decimal(sidecar.tick_size)
            new_min = parse_min_order_decimal(sidecar.min_order_size)
            if new_tick is None or new_min is None:
                self._set_sidecar_usable(False)
                raise SidecarUnavailable("collector sidecar has no usable tick or min order size")
            await self._apply_sidecar_update(sidecar, new_tick, new_min)

    def _drop_sell(self, quote: Quote) -> bool:
        """Drop a SELL the wallet cannot cover, and time how long that keeps happening."""
        reason = sell_drop_reason(self._host.store, quote)
        if quote.side is Side.SELL:
            self._sell_drops.note(
                match_id=self._discovered.match_id,
                token_id=quote.token_id,
                quote_size=quote.size,
                held=self._host.store.position(quote.token_id).size,
                reason=reason,
                now=time.monotonic(),
            )
        return reason is not None

    def open_core(
        self,
        *,
        level_usdc: float,
        min_order_size: float,
        tick_size: float,
        trace: CoreTrace | None,
    ) -> None:
        """Build this market's Follow300 core. Attach and tests call this."""
        cadence = read_engine_cadence()
        policy = follow300_policy(
            level_usdc=level_usdc,
            debounce_ms=cadence.debounce_ms,
            fallback_timer_s=cadence.quoter_tick_s,
        )
        limits = MarketLimits(
            min_order_size=min_order_size,
            tick_size=tick_size,
            pair_sum_tolerance=PAIR_SUM_TOLERANCE,
            radiant_token_index=0 if self._yes_is_radiant else 1,
        )
        freshness = FreshnessLimits(
            book_stale_s=MAX_BOOK_AGE_SECONDS,
            entry_stale_s=self._watchdog.entry_timeout_seconds,
            exit_stale_s=EXIT_FEED_STALE_SECONDS,
        )
        header = (
            None
            if trace is None
            else header_for_market(
                session_id=self._cid,
                match_id=self._discovered.match_id,
                game=self._discovered.game,
                execution_mode=self._mode,
                git_commit=self._host.git_commit,
                policy=policy,
                limits=limits,
                freshness=freshness,
                model_name=self._model.model_reference.name,
                model_trained_at=self._model.model_reference.trained_at,
                yes_token=self._yes,
                no_token=self._no,
            )
        )
        trace = start_market_trace(trace, header)
        self._core = LiveCore(
            policy=policy,
            limits=limits,
            freshness=freshness,
            yes_token=self._yes,
            no_token=self._no,
            drop_sell=self._drop_sell,
            trace=trace,
            max_position_levels=LIVE_MAX_POSITION_LEVELS[self._discovered.game],
        )
        self._core.session_id = self._cid
        self._dust.attach_core(self._core)
        store = _wallet_store(self._host.store)
        if store is not None:
            with suppress(CoreSchemaError):
                load_core_snapshot(store=store, core=self._core, session_id=self._cid)
        self._cell.core = self._core

    def _set_sidecar_usable(self, usable: bool) -> None:
        self._sidecar_usable = usable
        if self._core is not None:
            self._core.sidecar_usable = usable

    def _apply_place_ticks(self, place_tick: float) -> None:
        """Push the CLOB place tick onto both token books that already exist."""
        for token_id in (self._yes, self._no):
            book = self._host.engine.md.book(token_id)
            if book is not None:
                book.set_tick_size(place_tick)

    async def _apply_sidecar_update(
        self, sidecar: FreshSidecar, new_tick: float, new_min: float
    ) -> None:
        """Apply min always; apply tick to meta/books only when it stays on the CLOB grid."""
        meta = self._host.engine.metas[self._cid]
        place_tick = pin_place_tick(new_tick, meta.tick_size)
        self._observed_tick = new_tick
        if place_tick == new_tick:
            self._apply_place_ticks(place_tick)
        tick_changed = place_tick != meta.tick_size
        min_changed = new_min != meta.min_order_size
        observed_changed = sidecar.tick_size != self._last_tick_str
        if tick_changed or min_changed:
            updated = replace(meta, tick_size=place_tick, min_order_size=new_min)
            self._host.engine.metas[self._cid] = updated
            self._host.engine.catalog.upsert_market(updated)
        if tick_changed:
            await self._cancel_off_grid_orders(place_tick)
        if observed_changed:
            assert sidecar.tick_size is not None
            old_tick_str = self._last_tick_str
            self._last_tick_str = sidecar.tick_size
            try:
                if self._journal is not None:
                    self._journal.write_tick_change(old_tick_str, sidecar.tick_size)
            except Exception as exc:
                if self._reporter is not None:
                    self._reporter.report(PHASE_SIDECAR_REFRESH, type(exc).__name__)
        if tick_changed or min_changed or observed_changed:
            if self._core is not None:
                self._core.sidecar_usable = self._sidecar_usable
                self._core.enqueue(
                    LimitsUpdate(
                        now_ns=core_now_ns(),
                        limits=MarketLimits(
                            min_order_size=new_min,
                            tick_size=place_tick,
                            pair_sum_tolerance=PAIR_SUM_TOLERANCE,
                            radiant_token_index=self._core.state.limits.radiant_token_index,
                        ),
                    )
                )
            self._host.engine._wake_cid(self._cid)

    async def _cancel_order_ids(self, order_ids: list[str]) -> None:
        """Cancel one selected batch and drop the orders the gateway accepted."""
        if not order_ids:
            return
        ok = await self._host.engine.gateway.cancel(order_ids)
        if not ok:
            return
        for order_id in order_ids:
            self._host.engine.state.remove_order(order_id)

    async def _cancel_off_grid_orders(self, new_tick: float) -> None:
        """Cancel only orders off the new Decimal tick grid, if any exist."""
        tick = Decimal(str(new_tick))
        off_grid = [
            order.order_id
            for token_id in (self._yes, self._no)
            for order in self._host.engine.state.orders_for(token_id)
            if Decimal(str(order.price)) % tick != 0
        ]
        await self._cancel_order_ids(off_grid)

    def end_snapshot(self) -> SessionEndSnapshot:
        """Leftover sizes plus this market's ledger cash and book IMV."""
        leftover_yes = self._host.engine.state.position(self._yes).size
        leftover_no = self._host.engine.state.position(self._no).size
        positions = {self._yes: leftover_yes, self._no: leftover_no}
        net_cash = self._host.store.ledger_net_cash_for_tokens({self._yes, self._no})
        yes_mark = self._host.engine.risk._marks.get(self._yes)
        no_mark = self._host.engine.risk._marks.get(self._no)
        inventory = leftover_yes * (yes_mark if yes_mark is not None else 0.0)
        inventory += leftover_no * (no_mark if no_mark is not None else 0.0)
        equity = net_cash + inventory
        if not (math.isfinite(net_cash) and math.isfinite(inventory) and math.isfinite(equity)):
            return SessionEndSnapshot(positions, leftover_yes, leftover_no, None, None, None)
        return SessionEndSnapshot(positions, leftover_yes, leftover_no, net_cash, inventory, equity)

    async def _finish_terminal(self) -> None:
        """Match final first; then the shared quiesce path."""
        snapshot = self.end_snapshot() if self._attached else None
        pnl: SessionPnl | None = None
        if (
            snapshot is not None
            and snapshot.net_cash is not None
            and snapshot.inventory_value is not None
        ):
            pnl = SessionPnl(snapshot.net_cash, snapshot.inventory_value)
        finalize_match(self._discovered.match_id, pnl)
        await self._quiesce(True)

    async def _finish_final(self) -> None:
        """Record match-final cleanup, zero sqlite, and detach."""
        snapshot: SessionEndSnapshot | None = None
        if self._attached:
            snapshot = self.end_snapshot()
        if self._journal is not None:
            try:
                self._journal.write_end(TERMINAL_REASON_FINISHED, snapshot)
            except Exception as exc:
                logger.warning("trader session_end recording failed: %s", type(exc).__name__)
        write_execution_cleanup(
            match_archive_dir(TRADER_DIR, self._discovered.match_id),
            self._discovered.match_id,
            self._cid,
        )
        self._emit_finished(snapshot)
        if self._attached:
            self._host.store.zero_token_sizes({self._yes, self._no})
        self._host.unregister_worker(self._cid)
        self._close_journal()
        if self._attached:
            await self._host.detach(self._cid)

    async def _quiesce(self, final: bool) -> None:
        """Stop quoting, cancel, fence, and drop routing. A second call is a no-op.

        Proven match final zeros sqlite YES/NO and detaches. Unproven fence is
        the only keep_quiet path. Abnormal proven fence detaches and keeps sqlite.
        """
        if self._quiesced:
            return
        self._quiesced = True
        self._quoting = False
        self._cell.clear()
        self._dust.mark_quiesced()
        await self._dust.wait_inflight()
        proven = True
        if self._attached:
            self._host.engine._wake_cid(self._cid)
            await stop_quoter(self._host.engine, self._cid)
            try:
                await self.cancel_entry_buys()
            except Exception as exc:
                logger.warning("trader entry cancel failed during quiesce: %s", type(exc).__name__)
            try:
                await self._host.cancel_market(self._cid)
            except Exception as exc:
                logger.warning("trader market cancel failed during quiesce: %s", type(exc).__name__)
            await self._dust.sweep(force=True)
            proven = await self._host.fence_market({self._yes, self._no}, FENCE_TIMEOUT_S)
        if not proven:
            logger.warning(
                "trader fence unproven match=%s: execution_cleanup not written",
                self._discovered.match_id,
            )
            self._host.keep_quiet(self._cid)
            notify_in_background(f"trader fence unproven: match {self._discovered.match_id}")
            self._host.unregister_worker(self._cid)
            self._close_journal()
            return
        if final:
            await self._finish_final()
            return
        self._host.unregister_worker(self._cid)
        self._close_journal()
        if self._attached:
            await self._host.detach(self._cid)

    def _close_journal(self) -> None:
        """Drop the journal pointer first so a late fill cannot write a closed file."""
        journal = self._journal
        self._journal = None
        if journal is not None:
            journal.close()
        if self._core is not None:
            self._core.detach_trace()

    def _emit_finished(self, snapshot: SessionEndSnapshot | None) -> None:
        """Send the finished Telegram line from leftover/PnL fields, never blocking the loop."""
        if self._discovered.record_only:
            return
        leftover_yes = 0.0
        leftover_no = 0.0
        realized_pnl_usdc: float | None = None
        inventory_value_usdc: float | None = None
        if snapshot is not None:
            leftover_yes = snapshot.leftover_yes
            leftover_no = snapshot.leftover_no
            realized_pnl_usdc = snapshot.net_cash
            inventory_value_usdc = snapshot.inventory_value
        notify_session_finished(
            session_alert_from_match(self._discovered, self._mode),
            realized_pnl_usdc,
            inventory_value_usdc,
            self._maker_rebate_usdc,
            leftover_yes,
            leftover_no,
        )
