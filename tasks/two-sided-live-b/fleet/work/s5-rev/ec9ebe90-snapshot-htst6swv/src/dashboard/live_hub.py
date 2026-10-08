import asyncio
import atexit
import functools
import itertools
import threading
import time
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, Self

from dashboard import catalog, health, logs, summarize, wallet
from dashboard.balance import (
    BalanceAccount,
    BalanceRead,
    BalanceTracker,
    load_balance_account,
)
from dashboard.catalog import CatalogSnapshot, MapView
from dashboard.collateral import CollateralReader
from dashboard.health import EMPTY_HEALTH, HealthFacts
from dashboard.hub_types import (
    EMPTY_DAY_STATE,
    EMPTY_NONTRADING,
    EMPTY_SUBSCRIPTION_FACTS,
    DesiredSubs,
    HubSnapshot,
    LogsFacts,
    NontradingFacts,
    SubscriptionFacts,
    classify_map_views,
    desired_subscriptions,
    fail_day_state,
    fail_health,
    fail_nontrading,
    fail_service_logs,
    mark_day_inflight,
    merge_day_state,
    merge_health,
    merge_nontrading,
    merge_service_logs,
    reserve_snapshot,
    run_day_job,
    tail_paths,
    wallet_facts,
)
from dashboard.logs import LogRunner, NontradingResult, SkipNote, latest_skip_reasons
from dashboard.market_books import (
    DEFAULT_WS_URL,
    BooksCallback,
    BookSnapshot,
    DesiredMarket,
    HubBooks,
    ScheduleFn,
)
from dashboard.reserve import ReserveReport
from dashboard.summarize import FetchJson
from dashboard.tails import TailCache, TailView
from dashboard.wallet import OutboxPage, WalletSnapshot
from shared.utils.environment import env_value
from shared.utils.log import get_logger
from trader.collector_sidecars import load_archive_root
from trader.game_profile import GAME_PROFILES

OUTBOX_PAGE_LIMIT = 256
LOG_FAIL_PAUSE_CAP_S = 300.0

_log = get_logger("dashboard.live_hub")


@dataclass(frozen=True)
class HubTiming:
    disk_s: float
    catalog_s: float
    subs_s: float
    day_s: float
    logs_s: float
    drain_timeout_s: float
    balance_min_s: float
    balance_period_s: float


PRODUCTION_TIMING = HubTiming(
    disk_s=0.5,
    catalog_s=catalog.CATALOG_REFRESH_S,
    subs_s=5.0,
    day_s=300.0,
    logs_s=60.0,
    drain_timeout_s=20.0,
    balance_min_s=5.0,
    balance_period_s=60.0,
)


class BooksDriver(Protocol):
    @property
    def applied_at(self) -> float | None: ...
    def run(self) -> Coroutine[Any, Any, None]: ...
    async def apply_subscriptions(self, markets: tuple[DesiredMarket, ...]) -> bool: ...
    async def shutdown(self) -> None: ...


BooksFactory = Callable[[ScheduleFn, BooksCallback], BooksDriver]


@dataclass(frozen=True)
class HubConfig:
    wallet_db: Path
    live_root: Path
    legacy_root: Path
    compose_file: Path
    service: str
    ws_url: str
    proxy: str | None
    account: BalanceAccount | None
    account_error: str | None
    sidecar_roots: Mapping[str, Path]
    sidecar_root_errors: tuple[str, ...]
    timing: HubTiming
    fetch: FetchJson
    log_runner: LogRunner
    read_balance: Callable[[], BalanceRead] | None
    make_books: BooksFactory
    wall: Callable[[], float]
    mono: Callable[[], float]


def _default_books_factory(ws_url: str, proxy: str | None) -> BooksFactory:
    def build(schedule: ScheduleFn, on_books: BooksCallback) -> BooksDriver:
        return HubBooks(ws_url, schedule=schedule, on_books=on_books, proxy=proxy)

    return build


def _sidecar_roots() -> tuple[dict[str, Path], list[str]]:
    roots: dict[str, Path] = {}
    errors: list[str] = []
    for game, profile in GAME_PROFILES.items():
        try:
            roots[game] = load_archive_root(profile)
        except RuntimeError:
            errors.append(f"{game}: {profile.archive_root_env} unset")
    return roots, errors


def default_hub_config() -> HubConfig:
    root = summarize.ESPORTS_TRADER
    trading_toml = root / "config" / "trading.toml"
    try:
        account = load_balance_account(trading_toml)
        account_error = (
            None if account is not None else "PK/BROWSER_ADDRESS credentials unavailable"
        )
    except Exception as exc:
        account = None
        account_error = f"invalid wallet config: {type(exc).__name__}"
    reader = None if account is None else CollateralReader(account).read
    ws_url = DEFAULT_WS_URL
    proxy = env_value("ALL_PROXY") or env_value("HTTPS_PROXY")
    sidecar_roots, sidecar_errors = _sidecar_roots()
    return HubConfig(
        wallet_db=root / "data" / "trader_live" / "wallet" / "live.db",
        live_root=root / "data" / "trader_live",
        legacy_root=root / "data" / "live_paper",
        compose_file=root / "compose.yaml",
        service="live",
        ws_url=ws_url,
        proxy=proxy,
        account=account,
        account_error=account_error,
        sidecar_roots=sidecar_roots,
        sidecar_root_errors=tuple(sidecar_errors),
        timing=PRODUCTION_TIMING,
        fetch=summarize.fetch_json,
        log_runner=logs.docker_runner,
        read_balance=reader,
        make_books=_default_books_factory(ws_url, proxy),
        wall=time.time,
        mono=time.monotonic,
    )


@dataclass(frozen=True)
class _TailRead:
    path: Path
    view: TailView | None


@dataclass(frozen=True)
class _DiskResult:
    wallet: WalletSnapshot
    page: OutboxPage | None
    tails: tuple[_TailRead, ...]


class _DiskLane:
    def __init__(self, wallet_db: Path) -> None:
        self._wallet_db = wallet_db
        self._checkpoints = wallet.CheckpointCache()
        self._tails = TailCache()

    def __call__(
        self,
        *,
        after_seq: int | None,
        tail_paths: tuple[Path, ...],
        now_wall: float,
    ) -> _DiskResult:
        snap = wallet.read_wallet_snapshot(
            self._wallet_db, checkpoints=self._checkpoints, now=now_wall
        )
        page = (
            wallet.read_outbox_page(self._wallet_db, after_seq=after_seq, limit=OUTBOX_PAGE_LIMIT)
            if after_seq is not None
            else None
        )
        tails = tuple(_TailRead(p, self._tails.read(p)) for p in tail_paths)
        return _DiskResult(wallet=snap, page=page, tails=tails)


@dataclass(frozen=True)
class _CatalogResult:
    live: CatalogSnapshot
    legacy: CatalogSnapshot
    nontrading: NontradingResult


class _CatalogLane:
    def __init__(
        self,
        live_root: Path,
        legacy_root: Path,
        sidecar_roots: Mapping[str, Path],
        refresh_s: float = catalog.CATALOG_REFRESH_S,
    ) -> None:
        self._live = catalog.MatchCatalog(live_root, "live", refresh_s=refresh_s)
        self._legacy = catalog.MatchCatalog(legacy_root, "legacy", refresh_s=refresh_s)
        self._sidecar_roots = sidecar_roots

    def __call__(
        self, *, now_mono: float, now_wall: float, skips: Mapping[str, SkipNote]
    ) -> _CatalogResult:
        live = self._live.snapshot(now_mono=now_mono, now_wall=now_wall)
        legacy = self._legacy.snapshot(now_mono=now_mono, now_wall=now_wall)
        traded = frozenset(
            entry.condition_id
            for entry in itertools.chain(live.entries, legacy.entries)
            if entry.condition_id is not None
        )
        nontrading = logs.list_nontrading(
            roots=self._sidecar_roots,
            traded_cids=traded,
            skips=skips,
            now_epoch=now_wall,
        )
        return _CatalogResult(live=live, legacy=legacy, nontrading=nontrading)


@dataclass(frozen=True)
class _LogsResult:
    service: logs.ServiceLogs
    health: HealthFacts


def _run_logs_job(compose_file: Path, service: str, runner: LogRunner) -> _LogsResult:
    return _LogsResult(
        service=logs.read_service_logs(compose_file=compose_file, service=service, runner=runner),
        health=health.read_health(compose_file=compose_file, service=service, runner=runner),
    )


_GENERATIONS = itertools.count(1)


class LiveHub:
    def __init__(self, config: HubConfig) -> None:
        self._cfg = config
        self._pub = threading.Lock()
        self._ctl = threading.Lock()
        self._closed = False
        self._closing = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_flag = threading.Event()
        self._stop_async: asyncio.Event | None = None
        self._balance_wake: asyncio.Event | None = None
        self._adapter: BooksDriver | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._inflight: set[Future[Any]] = set()
        self._pool_disk = ThreadPoolExecutor(1, thread_name_prefix="hub-disk")
        self._pool_slow = ThreadPoolExecutor(1, thread_name_prefix="hub-slow")
        self._pool_balance = ThreadPoolExecutor(1, thread_name_prefix="hub-balance")
        self._pool_day = ThreadPoolExecutor(1, thread_name_prefix="hub-day")
        self._pool_logs = ThreadPoolExecutor(1, thread_name_prefix="hub-logs")
        self._pools = [
            self._pool_disk,
            self._pool_slow,
            self._pool_balance,
            self._pool_day,
            self._pool_logs,
        ]
        self._stopping = False
        self._exit_error: str | None = None
        self._source_fatal: str | None = None
        self._generation = next(_GENERATIONS)
        self._tracker = BalanceTracker(
            min_interval_s=config.timing.balance_min_s,
            period_s=config.timing.balance_period_s,
        )
        self._disk = _DiskLane(config.wallet_db)
        self._catalogs = _CatalogLane(
            config.live_root,
            config.legacy_root,
            config.sidecar_roots,
            refresh_s=config.timing.catalog_s,
        )
        self._books: tuple[BookSnapshot, ...] = ()
        self._disk_error: str | None = None
        self._wallet_snap: WalletSnapshot | None = None
        self._wallet_fail: str | None = None
        self._wallet = wallet_facts(None, read_ok=False, error=None)
        self._reserve: ReserveReport | None = None
        self._catalog_live: CatalogSnapshot | None = None
        self._catalog_legacy: CatalogSnapshot | None = None
        self._catalog_error: str | None = None
        self._map_views: tuple[MapView, ...] = ()
        self._legacy_views: tuple[MapView, ...] = ()
        self._nontrading: NontradingFacts = EMPTY_NONTRADING
        self._health: HealthFacts = EMPTY_HEALTH
        self._tail_views: dict[Path, TailView] = {}
        self._subs_error: str | None = None
        self._subs = EMPTY_SUBSCRIPTION_FACTS
        self._day_state = EMPTY_DAY_STATE
        self._log = LogsFacts(
            service=config.service,
            ok=False,
            error=None,
            attempt_at=None,
            read_at=None,
            text="",
            truncated=False,
        )
        self._snapshot = self._build_snapshot()

    @property
    def closed(self) -> bool:
        with self._ctl:
            return self._closed

    @property
    def generation(self) -> int:
        return self._generation

    def get_snapshot(self) -> HubSnapshot:
        with self._pub:
            return self._snapshot

    def start(self) -> Self:
        with self._ctl:
            if self._closed or self._closing:
                raise RuntimeError("live hub closed")
            if self._thread is not None:
                return self
            self._thread = threading.Thread(target=self._run, name="live-hub", daemon=True)
            self._thread.start()
            return self

    def close(self, timeout_s: float | None = None) -> bool:
        with self._ctl:
            self._closing = True
            thread = self._thread
        self._stop_flag.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._request_stop_in_loop)
        if thread is None:
            with self._ctl:
                self._closed = True
            return True
        bound = timeout_s if timeout_s is not None else self._cfg.timing.drain_timeout_s + 10.0
        thread.join(bound)
        alive = thread.is_alive()
        with self._ctl:
            self._closed = not alive
        return not alive

    def _request_stop_in_loop(self) -> None:
        if self._stop_async is not None:
            self._stop_async.set()

    def _schedule_cb(self, fn: Callable[[], None]) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(fn)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        error: str | None = None
        try:
            loop.run_until_complete(self._main())
        except Exception as exc:
            error = type(exc).__name__
            self._cleanup_after_error(loop)
        finally:
            for pool in self._pools:
                pool.shutdown(wait=False, cancel_futures=True)
            loop.close()
            self._exit_error = error
            self._stopping = True
            self._publish()
            with self._ctl:
                self._closed = True

    def _cleanup_after_error(self, loop: asyncio.AbstractEventLoop) -> None:
        async def cleanup() -> None:
            if self._adapter is not None:
                with suppress(Exception):
                    await self._adapter.shutdown()
            for task in self._tasks:
                task.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)

        with suppress(Exception):
            loop.run_until_complete(cleanup())

    async def _main(self) -> None:
        self._stop_async = asyncio.Event()
        self._balance_wake = asyncio.Event()
        if self._stop_flag.is_set():
            self._stop_async.set()
        self._adapter = self._cfg.make_books(self._schedule_cb, self._on_books)
        self._tasks = [
            self._spawn(self._adapter.run(), "hub-books"),
            self._spawn(self._disk_loop(), "hub-disk"),
            self._spawn(self._catalog_loop(), "hub-catalog"),
            self._spawn(self._subs_loop(), "hub-subs"),
            self._spawn(self._balance_loop(), "hub-balance"),
            self._spawn(self._day_loop(), "hub-day"),
            self._spawn(self._logs_loop(), "hub-logs"),
        ]
        self._publish()
        try:
            await self._stop_async.wait()
        finally:
            await self._shutdown()

    def _spawn(self, coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[None]) -> None:
        if self._stopping or task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._source_fatal = f"{task.get_name()}:{type(exc).__name__}"
            self._publish()

    async def _shutdown(self) -> None:
        self._stopping = True
        if self._adapter is not None:
            with suppress(Exception):
                await self._adapter.shutdown()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        pending = [fut for fut in self._inflight if not fut.done()]
        if pending:
            loop = asyncio.get_running_loop()
            _, left = await loop.run_in_executor(
                None,
                lambda: wait(pending, timeout=self._cfg.timing.drain_timeout_s),
            )
            if left:
                self._exit_error = f"worker drain timeout ({len(left)} running)"

    async def _submit[T](self, pool: ThreadPoolExecutor, fn: Callable[[], T]) -> T:
        fut: Future[T] = pool.submit(fn)
        self._inflight.add(fut)
        fut.add_done_callback(self._inflight.discard)
        return await asyncio.wrap_future(fut)

    def _on_books(self, books: tuple[BookSnapshot, ...]) -> None:
        self._books = books
        self._publish()

    def _wake_balance(self) -> None:
        if self._balance_wake is not None:
            self._balance_wake.set()

    async def _wait_wake(self, delay: float | None) -> None:
        wake = self._balance_wake
        if wake is None:
            return
        wake.clear()
        with suppress(TimeoutError):
            await asyncio.wait_for(wake.wait(), delay)

    async def _disk_loop(self) -> None:
        lane = self._disk
        interval = self._cfg.timing.disk_s
        while not self._stopping:
            job = functools.partial(
                lane,
                after_seq=self._tracker.cursor,
                tail_paths=tail_paths(self._catalog_live),
                now_wall=self._wall(),
            )
            try:
                result = await self._submit(self._pool_disk, job)
                self._apply_disk(result)
            except Exception as exc:
                self._disk_error = type(exc).__name__
                self._store_wallet_facts()
                self._publish()
                await asyncio.sleep(interval)
                continue
            self._publish()
            if result.page is not None and result.page.has_more:
                await asyncio.sleep(0.0)
            else:
                await asyncio.sleep(interval)

    async def _catalog_loop(self) -> None:
        lane = self._catalogs
        interval = self._cfg.timing.catalog_s
        while not self._stopping:
            job = functools.partial(
                lane,
                now_mono=self._mono(),
                now_wall=self._wall(),
                skips=latest_skip_reasons(self._log.text),
            )
            try:
                result = await self._submit(self._pool_slow, job)
                self._apply_catalog(result)
            except Exception as exc:
                self._catalog_error = type(exc).__name__
                self._update_subs_error()
                self._nontrading = fail_nontrading(
                    self._nontrading,
                    self._catalog_error,
                    attempt_at=self._wall(),
                    root_errors=self._cfg.sidecar_root_errors,
                )
            self._publish()
            await asyncio.sleep(interval)

    async def _subs_loop(self) -> None:
        interval = self._cfg.timing.subs_s
        while not self._stopping:
            adapter = self._adapter
            applied_at = self._subs.applied_at
            try:
                desired = desired_subscriptions(
                    self._map_views,
                    () if self._wallet_snap is None else self._wallet_snap.token_cids,
                )
            except Exception as exc:
                desired = DesiredSubs(markets=(), missing=(), conflicts=())
                self._subs_error = type(exc).__name__
            else:
                if adapter is not None:
                    try:
                        changed = await adapter.apply_subscriptions(desired.markets)
                        self._subs_error = None
                    except Exception as exc:
                        changed = False
                        self._subs_error = type(exc).__name__
                    if changed:
                        applied_at = adapter.applied_at
            self._subs = SubscriptionFacts(
                catalog_built_at=(
                    self._catalog_live.built_at if self._catalog_live is not None else None
                ),
                applied_at=applied_at,
                markets=desired.markets,
                missing=desired.missing,
                conflicts=desired.conflicts,
                error=self._subs_error or self._catalog_error,
            )
            self._publish()
            await asyncio.sleep(interval)

    async def _balance_loop(self) -> None:
        reader = self._cfg.read_balance
        while not self._stopping:
            if reader is None:
                await self._wait_wake(self._cfg.timing.day_s)
                continue
            now = self._mono()
            delay = self._tracker.next_delay(now)
            if delay is None or delay > 0.0:
                await self._wait_wake(delay)
                continue
            self._tracker.begin_request(now_mono=now, now_wall=self._wall())
            self._publish()
            try:
                result = await self._submit(self._pool_balance, reader)
            except Exception as exc:
                result = BalanceRead(
                    ok=False,
                    collateral_usdc=None,
                    error=type(exc).__name__,
                    status_code=None,
                    retry_after_s=None,
                )
            try:
                self._tracker.finish_request(result, now_mono=self._mono(), now_wall=self._wall())
            except Exception as exc:
                _log.exception("balance reduce failed")
                self._tracker.abort_request(
                    type(exc).__name__, now_mono=self._mono(), now_wall=self._wall()
                )
            self._publish()

    async def _day_loop(self) -> None:
        interval = self._cfg.timing.day_s
        while not self._stopping:
            funder = self._current_funder()
            if funder is None:
                if self._day_state.snapshot.error is None:
                    self._day_state = fail_day_state(
                        self._day_state, "no funder configured", attempt_at=None
                    )
                    self._publish()
            else:
                job = functools.partial(
                    run_day_job,
                    funder=funder,
                    generation=self._generation,
                    trees=(
                        ("live", self._cfg.live_root),
                        ("legacy", self._cfg.legacy_root),
                    ),
                    fetch=self._cfg.fetch,
                    wall=self._wall,
                )
                self._day_state = mark_day_inflight(self._day_state, started_at=self._wall())
                self._publish()
                try:
                    result = await self._submit(self._pool_day, job)
                    self._day_state = merge_day_state(
                        self._day_state,
                        result,
                        generation=self._generation,
                        funder=funder,
                    )
                except Exception as exc:
                    self._day_state = fail_day_state(
                        self._day_state, type(exc).__name__, attempt_at=self._wall()
                    )
                self._publish()
            await asyncio.sleep(interval)

    async def _logs_loop(self) -> None:
        interval = self._cfg.timing.logs_s
        pause = 0.0
        while not self._stopping:
            if pause > 0.0:
                await asyncio.sleep(pause)
            job = functools.partial(
                _run_logs_job,
                self._cfg.compose_file,
                self._cfg.service,
                self._cfg.log_runner,
            )
            try:
                result = await self._submit(self._pool_logs, job)
                self._log = merge_service_logs(self._log, result.service, attempt_at=self._wall())
                self._health = merge_health(result.health, attempt_at=self._wall())
            except Exception as exc:
                self._log = fail_service_logs(
                    self._log, type(exc).__name__, attempt_at=self._wall()
                )
                self._health = fail_health(
                    self._health, type(exc).__name__, attempt_at=self._wall()
                )
            pause = (
                interval if self._log.ok else min(max(pause, interval) * 2.0, LOG_FAIL_PAUSE_CAP_S)
            )
            self._publish()

    def _apply_disk(self, result: _DiskResult) -> None:
        self._disk_error = None
        snap = result.wallet
        if snap.ok:
            self._wallet_snap = snap
            self._wallet_fail = None
            if not self._tracker.seeded() and snap.fill_boundary_ok:
                self._tracker.seed(snap.matched_fill_keys, snap.max_outbox_seq)
                self._wake_balance()
        else:
            self._wallet_fail = snap.error or "wallet read failed"
        if result.page is not None and self._tracker.apply_page(result.page):
            self._wake_balance()
        self._tail_views = {item.path: item.view for item in result.tails if item.view is not None}
        started_at = self._health.started_at
        self._map_views = classify_map_views(
            self._catalog_live,
            self._tail_views,
            now_wall=self._wall(),
            run_started_at=started_at,
        )
        self._store_wallet_facts()
        self._reserve = reserve_snapshot(
            self._wallet_snap,
            self._map_views,
            None if self._funder_mismatch() else self._tracker.collateral,
            started_at,
        )

    def _apply_catalog(self, result: _CatalogResult) -> None:
        self._catalog_error = None
        self._catalog_live = result.live
        self._catalog_legacy = result.legacy
        started_at = self._health.started_at
        self._map_views = classify_map_views(
            result.live, self._tail_views, now_wall=self._wall(), run_started_at=started_at
        )
        self._legacy_views = classify_map_views(
            result.legacy, {}, now_wall=self._wall(), run_started_at=started_at
        )
        self._nontrading = merge_nontrading(
            result.nontrading,
            attempt_at=self._wall(),
            root_errors=self._cfg.sidecar_root_errors,
        )
        self._subs = replace(
            self._subs, catalog_built_at=result.live.built_at, error=self._subs_error
        )

    def _store_wallet_facts(self) -> None:
        self._wallet = wallet_facts(
            self._wallet_snap,
            read_ok=self._wallet_fail is None,
            error=self._wallet_fail or self._disk_error,
        )

    def _update_subs_error(self) -> None:
        error = self._subs_error or self._catalog_error
        if self._subs.error != error:
            self._subs = replace(self._subs, error=error)

    def _current_funder(self) -> str | None:
        if self._wallet_snap is not None and self._wallet_snap.funder:
            return self._wallet_snap.funder
        if self._cfg.account is not None:
            return self._cfg.account.funder
        return None

    def _funder_mismatch(self) -> bool:
        account = self._cfg.account
        if account is None:
            return False
        wallet_funder = self._wallet_snap.funder if self._wallet_snap else None
        return wallet_funder is None or wallet_funder.lower() != account.funder.lower()

    def _wall(self) -> float:
        return self._cfg.wall()

    def _mono(self) -> float:
        return self._cfg.mono()

    def _build_snapshot(self) -> HubSnapshot:
        account = self._cfg.account
        running = self._thread is not None and self._thread.is_alive()
        return HubSnapshot(
            generation=self._generation,
            published_at=self._wall(),
            running=running and not self._stopping,
            error=self._exit_error or self._source_fatal,
            books=self._books,
            balance=self._tracker.snapshot(
                now_mono=self._mono(),
                funder=account.funder if account is not None else None,
                funder_mismatch=self._funder_mismatch(),
                source_error=self._cfg.account_error,
            ),
            wallet=self._wallet,
            reserve=self._reserve,
            day=self._day_state.snapshot,
            subscriptions=self._subs,
            maps=self._map_views,
            legacy_maps=self._legacy_views,
            nontrading=self._nontrading,
            health=self._health,
            service_logs=self._log,
        )

    def _publish(self) -> None:
        snap = self._build_snapshot()
        with self._pub:
            self._snapshot = snap


_registry_lock = threading.Lock()
_singleton: LiveHub | None = None
_atexit_registered = False


def _acquire_hub(factory: Callable[[], LiveHub]) -> LiveHub:
    global _singleton, _atexit_registered
    with _registry_lock:
        if _singleton is not None and not _singleton.closed:
            return _singleton
        hub = factory()
        hub.start()
        _singleton = hub
        if not _atexit_registered:
            atexit.register(close_live_hub)
            _atexit_registered = True
        return hub


def _swap_hub(factory: Callable[[], LiveHub]) -> LiveHub:
    global _singleton
    with _registry_lock:
        old = _singleton
        new = factory()
        if old is not None and not old.close():
            _log.warning("previous live hub failed to stop cleanly")
        new.start()
        _singleton = new
        return new


def get_live_hub() -> LiveHub:
    return _acquire_hub(lambda: LiveHub(default_hub_config()))


def replace_live_hub() -> LiveHub:
    return _swap_hub(lambda: LiveHub(default_hub_config()))


def close_live_hub() -> None:
    global _singleton
    with _registry_lock:
        old = _singleton
        _singleton = None
    if old is not None:
        old.close()
