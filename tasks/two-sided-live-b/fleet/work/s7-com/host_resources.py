"""Open WalletHost with only the credentials the assigned games need."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import polymaker.engine as polymaker_engine
from polymaker.config import Config
from polymaker.domain import TargetQuotes
from polymaker.engine import Engine
from polymaker.strategy.quoting import QuoteInputs

from shared.utils.http import http_client
from shared.utils.log import get_logger
from shared.utils.telonex_book import PAIR_SUM_TOLERANCE
from strategy.types import MarketLimits
from trader.clob_transport import use_http1_clob_transport
from trader.collector_sidecars import load_archive_root
from trader.engine_seams import (
    BookReadiness,
    EngineClassRestore,
    ShutdownLatch,
    install_core_quoter_wake,
    patch_engine_classes,
    pin_engine_identity,
    restore_engine_classes,
)
from trader.game_profile import GameProfile, strategy_catalogs
from trader.model_server import ModelServer, load_model
from trader.paper_gateway import PaperGateway
from trader.process_lock import acquire_file_lock
from trader.session_config import (
    MaterializedConfigDir,
    materialize_wallet_config_dir,
    read_template,
)
from trader.session_core import (
    CollateralCache,
    LiveCore,
    core_books,
    core_now_ns,
    make_esports_reconcile,
    quote_cycle,
)
from trader.session_engine import StrategyCell, close_engine_resources
from trader.session_types import TradingDisabled
from trader.steam_client import SteamClient
from trader.trading_mode import (
    DotaStrategy,
    ExecutionMode,
    assigned_games,
    read_dota_strategy,
    reject_legacy_env,
    require_live_wallet,
    require_two_sided_start,
    steam_required,
)
from trader.wallet_host import WalletHost, wallet_db_path
from trader.wallet_store import WalletStateStore

logger = get_logger(__name__)


def _quote_fault(error_type: str) -> None:
    """Log a construct_quotes fault by type. The match journal is owned by the worker."""
    logger.warning("trader construct_quotes fault: %s", error_type)


def _bind_quotes_adapter(
    engine: Engine,
    cores: dict[str, LiveCore],
    cache: CollateralCache,
    store: WalletStateStore,
    mode: ExecutionMode,
    account_cap_usdc: float,
) -> Callable[[QuoteInputs], TargetQuotes]:
    """Wire construct_quotes to the per-market LiveCore."""
    live = mode == "live"

    def quotes_adapter(inp: QuoteInputs) -> TargetQuotes:
        empty = TargetQuotes(inp.meta.condition_id, inp.regime, ())
        core = cores.get(inp.meta.condition_id)
        if core is None:
            return empty
        try:
            now_ns = core_now_ns()
            yes = inp.meta.yes.token_id
            no = inp.meta.no.token_id
            return quote_cycle(
                core,
                inp,
                now_ns=now_ns,
                books=core_books(
                    core=core,
                    yes=engine.md.book(yes),
                    no=engine.md.book(no),
                    now_ns=now_ns,
                    live=live,
                ),
                clock=core.latest_clock,
                limits=MarketLimits(
                    min_order_size=inp.meta.min_order_size,
                    tick_size=inp.meta.tick_size,
                    pair_sum_tolerance=PAIR_SUM_TOLERANCE,
                    radiant_token_index=core.state.limits.radiant_token_index,
                ),
                store=store,
                cache=cache,
                cores=cores.values(),
                account_cap_usdc=account_cap_usdc,
            )
        except Exception as exc:
            _quote_fault(type(exc).__name__)
            return empty

    return quotes_adapter


def _load_strategy_models(games: tuple[GameProfile, ...]) -> dict[str, ModelServer]:
    """Load one catalog per strategy-profile name."""
    return {
        catalog.profile_name: load_model(
            catalog.model_dir, catalog.features, catalog.source_lag_seconds
        )
        for profile in games
        for catalog in strategy_catalogs(profile)
    }


def open_wallet_host(
    steam_client: SteamClient | None,
    archive_by_game: dict[str, Path],
    git_commit: str,
    mode: ExecutionMode,
    games: tuple[GameProfile, ...],
    strategy: DotaStrategy,
) -> WalletHost:
    """Acquire the wallet flock, load catalogs by strategy-profile name, and construct the host."""
    if not games:
        raise TradingDisabled("empty assignment cannot open a wallet host")
    if mode == "live":
        require_live_wallet()
    template = read_template(games)
    db_path = wallet_db_path(mode)
    lock = acquire_file_lock(db_path)
    restore: EngineClassRestore | None = None
    config_dir: MaterializedConfigDir | None = None
    engine: Engine | None = None
    original_gateway = polymaker_engine.ExecutionGateway
    original_quotes = polymaker_engine.construct_quotes
    original_reconcile = polymaker_engine.reconcile
    try:
        restore = patch_engine_classes()
        cells: dict[str, StrategyCell] = {}
        cores: dict[str, LiveCore] = {}
        budget_cache = CollateralCache()
        if mode == "paper":
            polymaker_engine.ExecutionGateway = PaperGateway
        config_dir = materialize_wallet_config_dir(
            db_path,
            db_path.parent / "engine_journal",
            template,
        )
        cfg = Config.load(config_dir.config_dir, load_env=False)
        logger.info("trader config loaded [engine] [risk] [profiles] [wallet]")
        if cfg.engine.journal is not True:
            raise RuntimeError("engine journal is disabled in the config template")
        if strategy == "two_sided":
            require_two_sided_start(
                mode=mode,
                games=games,
                signature_type=cfg.wallet.signature_type,
                has_builder_creds=cfg.secrets.has_builder_creds,
            )
        logger.info(
            "trader wallet: strategy=%s signature_type=%d funder=%s",
            strategy,
            cfg.wallet.signature_type,
            cfg.secrets.browser_address,
        )
        engine = Engine(cfg, paper=(mode == "paper"))
        state = cast(WalletStateStore, engine.state)
        polymaker_engine.construct_quotes = _bind_quotes_adapter(
            engine,
            cores,
            budget_cache,
            state,
            mode,
            template.account_cap_usdc,
        )
        polymaker_engine.reconcile = make_esports_reconcile(cores)
        install_core_quoter_wake(engine, cores)
        pin_engine_identity(engine)
        models = _load_strategy_models(games)
        return WalletHost(
            steam_client,
            archive_by_game,
            engine,
            mode,
            lock,
            config_dir,
            models,
            restore,
            ShutdownLatch(),
            BookReadiness(),
            cells,
            cores,
            budget_cache,
            original_gateway,
            original_quotes,
            original_reconcile,
            git_commit,
            games,
            template.clips,
            strategy,
        )
    except BaseException:
        if engine is not None:
            close_engine_resources(engine)
        if restore is not None:
            restore_engine_classes(restore)
        polymaker_engine.ExecutionGateway = original_gateway
        polymaker_engine.construct_quotes = cast(Any, original_quotes)
        polymaker_engine.reconcile = original_reconcile
        if config_dir is not None:
            config_dir.cleanup()
        lock.close()
        raise


async def run_wallet_daemon(git_commit: str, mode: ExecutionMode) -> None:
    """Validate env, idle when no games, otherwise open assigned resources and run."""
    reject_legacy_env()
    strategy = read_dota_strategy()
    use_http1_clob_transport()
    games = assigned_games(mode)
    if not games:
        logger.info("trader idle: mode=%s assigned=()", mode)
        await asyncio.Event().wait()
        return
    logger.info(
        "trader assigned: mode=%s games=%s",
        mode,
        ",".join(profile.game for profile in games),
    )
    archive_by_game = {profile.game: load_archive_root(profile) for profile in games}
    client = http_client() if steam_required(games) else None
    steam = SteamClient.from_environment(client) if client is not None else None
    host: WalletHost | None = None
    try:
        host = open_wallet_host(steam, archive_by_game, git_commit, mode, games, strategy)
        await host.run()
    finally:
        if host is not None and not host._closed:
            host.close()
        if client is not None:
            client.close()
