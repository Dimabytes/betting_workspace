"""Run the maker backtest: one match, the validation split, or a since-match cohort."""

# pyright: reportMissingTypeStubs=false

import argparse
import asyncio
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass, fields, replace
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd
import prediction_market_extensions as prediction_market_extensions_pkg
from dotenv import load_dotenv
from nautilus_trader.common.component import (
    init_logging,  # pyright: ignore[reportUnknownVariableType]
    log_level_from_str,  # pyright: ignore[reportUnknownVariableType]
    set_backtest_force_stop,  # pyright: ignore[reportUnknownVariableType]
)
from nautilus_trader.core.data import Data  # pyright: ignore[reportUnknownVariableType]
from nautilus_trader.model.data import (
    CustomData,  # pyright: ignore[reportUnknownVariableType]
    DataType,  # pyright: ignore[reportUnknownVariableType]
)
from prediction_market_extensions.backtesting._execution_config import (
    ExecutionModelConfig,
    StaticLatencyConfig,
)
from prediction_market_extensions.backtesting._experiments import (
    _finalize_replay_results,  # pyright: ignore[reportPrivateUsage]
    build_backtest_for_experiment,
    build_replay_experiment,
)
from prediction_market_extensions.backtesting._prediction_market_runner import MarketDataConfig
from prediction_market_extensions.backtesting.data_sources import Book, Polymarket, Telonex

from archive_index.schedule import (
    ADMISSION_RULES_VERSION,
    EXTRACTION_RULES_VERSION,
)
from backtest.context import MarketContext, ReplayLookups
from backtest.extraction_identity import sha256_text
from backtest.feed_schedules import (
    ArchiveJoinInput,
    DotaArchiveJoin,
    FeedPlanResult,
    LolArchiveJoin,
    MatchFeedPlan,
    SchedulePlan,
    entry_stale_seconds,
    grid_exit_age_seconds,
    reject_schedule_flags,
    resolve_feed_plans,
    schedule_archive_dirs,
    schedule_feed_positions,
    schedule_map_sha256,
)
from backtest.lol_inputs import (
    EMPTY_SIGNAL_TAPE_STOP_REASON,
    NAUTILUS_ZERO_FILL_SKIP_IDS,
    NAUTILUS_ZERO_FILL_STOP_REASON,
    NO_REPLAY_STOP_REASONS,
    build_lol_book_weights,
    load_lol_replay_lookups,
    load_lol_selection,
    select_lol_since_match_ids,
)
from backtest.market_terms import MarketFee, load_market_fees
from backtest.marks import EnrichedFill, MidSeries
from backtest.paths import (
    BACKTESTS_DIR,
    FILLS_FILENAME,
    MANIFEST_FILENAME,
    QUOTE_EVENTS_FILENAME,
    RESULTS_FILENAME,
    SUMMARY_FILENAME,
)
from backtest.postprocess import (
    build_summary_payload,
    calculate_drawdowns,
    enrich_fills,
    load_match_mid_series,
    read_fills_checkpoint,
    write_fills_parquet,
)
from backtest.quote_store import (
    QUOTE_EVENT_PARTS_DIRNAME,
    QuoteCompaction,
    assert_quote_event_parts_resumable,
    clear_quote_event_parts,
    compact_quote_events,
    copy_quote_event_parts,
    find_parquet_file_problems,
    find_quote_events_file_problems,
    parts_dir,
    read_quote_telemetry,
    write_quote_event_parts,
)
from backtest.report import format_terminal_report
from backtest.report_types import ReplayInstrumentResult
from backtest.results import (
    MakerMatchResult,
    SignalProvenance,
    assert_manifest_matches,
    build_maker_match_results,
    concat_results_without_overlap,
    engine_fault_match_result,
    read_manifest,
    read_results_checkpoint,
    signal_provenance_map,
    write_manifest,
    write_results_checkpoint,
    write_validation_outputs,
)
from backtest.selection import (
    MarketSources,
    ValidationCoverage,
    build_book_weights,
    build_market_context,
    load_market_sources,
    select_dota_since_match_ids,
    select_validation_matches,
)
from backtest.shared_archive import (
    ARCHIVE_DIRNAME,
    ARCHIVE_DONE_FILENAME,
    SharedArchive,
    empty_shared_archive,
    graft_shared_archive,
    load_shared_archive,
    overlay_archive,
    write_archive_done,
    write_archive_parts,
)
from backtest.signals import (
    GRID_FEED_MAX_AGE_SECONDS,
    LIVE_GRID_TIMING,
    DatasetReadinessError,
    MatchSignals,
    SignalTiming,
    build_match_signals,
    build_schedule_match_signals,
    history_policy_for_model_dir,
    load_game_feature_rows,
    load_usable_signal_rows,
    require_catalog_features,
)
from backtest.strategy import MatchKernelConfig, ObservedClockTape
from backtest.telemetry import MakerRecords, clear_records, take_records
from backtest.telonex_local import create_telonex_source_tree
from backtest.two_sided_strategy import TwoSidedSettings
from shared.constants.dataset import BACKTEST_LAG_SECONDS
from shared.constants.lol import (
    LOL_BACKTEST_AUDIT_PATH,
    LOL_BACKTEST_MARKET_SECONDS_PATH,
    LOL_GAME_FEATURES_PATH,
    LOL_RESEARCH_MODEL_DIR,
    LOL_SOURCE_LAG_SECONDS,
    LOL_SPLIT_PATH,
    LOL_UNIVERSE_PATH,
    LOL_VALIDATION_PATH,
)
from shared.constants.paths import (
    GAME_FEATURES_DATASET_PATH,
    MARKET_TERMS_PATH,
    RAW_TELONEX_POLYMARKET_DIR,
    RESEARCH_MODEL_DIR,
    VALIDATION_DATASET_PATH,
)
from shared.constants.strategy import (
    BACKTEST_MAX_POSITION_LEVELS,
    BUY_CUTOFF_SECOND,
    EXIT_ABS_DELTA,
    EXIT_FEED_STALE_SECONDS,
    GRID_FEED_STALE_SECONDS,
    MIN_ABS_DELTA,
    MIN_ORDER_SIZE,
    ORDER_CANCEL_LATENCY_MS,
    ORDER_INSERT_LATENCY_MS,
    QUOTE_GRID,
)
from shared.types.opendota import OpenDotaPause
from shared.utils.board_features import BOARD_REACTION_SECONDS
from shared.utils.dota_features import (
    DOTA_NOXP_FEATURE_COLUMNS,
    DOTA_XP_FEATURE_COLUMNS,
)
from shared.utils.engine_cadence import read_engine_cadence
from shared.utils.gbm import MODEL_META_FILENAME, model_identity_sha256
from shared.utils.hashing import sha256_file
from shared.utils.log import get_logger, setup_logging
from shared.utils.lol_leagues import (
    LeagueWhitelist,
    load_canonical_league_whitelist,
    select_backtest_event_ids,
)
from shared.utils.match_time import NS_PER_SECOND, datetime_to_ns, get_state_available_ts
from shared.utils.model_registry import read_model_meta
from shared.utils.telonex_book import MAX_BOOK_AGE_SECONDS, PAIR_SUM_TOLERANCE
from strategy.policy import Follow300Policy, follow300_policy
from strategy.two_sided import BAND_HI
from strategy.types import FreshnessLimits, MarketLimits

logger = get_logger(__name__)

EXPERIMENT_NAME = "dota_maker"
FILL_MODEL = "queue"
PLACEMENT = "join"
MIN_BOOK_EVENTS = 25
# One batch replays two books per match. 1 keeps LoL cache-miss parquet decode
# from stacking day-files; measured overhead vs batch 8 was <0.25s.
MAX_MATCHES_PER_BATCH = 1
REPLAY_LOAD_WORKERS = 8
# Fake venue funds, not modelled capital: the report's capital and ROI come from
# wallet_path, which reads the fill tape and never looks at this account. Sizing it
# per match made a batch a shared pot, so one map's loss could starve the next and
# results moved with MAX_MATCHES_PER_BATCH. A flat balance that cannot bind keeps
# every map independent of the batch it lands in.
ENGINE_STARTING_BALANCE = 1_000_000.0
STRATEGY_PATH = "backtest.strategy:DotaMakerStrategy"
STRATEGY_CONFIG_PATH = "backtest.strategy:DotaMakerConfig"

Game = Literal["dota", "lol"]
BACKTEST_LEVEL_USDC: dict[Game, float] = {"dota": 300.0, "lol": 300.0}


def resolve_max_position_levels(game: Game, override: int | None) -> int:
    if override is None:
        return BACKTEST_MAX_POSITION_LEVELS[game]
    return override


# Manifest fingerprint; the tuple is the framework channel name, not a ranking.
LOL_EXECUTION_PRIORITY = ("onchain_fills",)


@dataclass(frozen=True)
class ShardSpec:
    """Zero-based process index and the parallel process count."""

    index: int
    count: int


@dataclass(frozen=True)
class MergedCheckpoints:
    """Parent plus shard result/fill rows after a successful concat."""

    results: tuple[MakerMatchResult, ...]
    fills: tuple[EnrichedFill, ...]
    shard_dirs: tuple[Path, ...]


@dataclass(frozen=True)
class DotaSelection:
    """Dota ids, sources, and report root for one run."""

    selected_ids: tuple[int, ...]
    coverage: ValidationCoverage | None
    capture_root: Path
    report_root: Path
    signal_rows: pd.DataFrame
    sources: MarketSources


@dataclass(frozen=True)
class RunSelection:
    """Game-agnostic ids, paths, and the lookup loader for one backtest run."""

    selected_ids: tuple[int, ...]
    coverage: ValidationCoverage | None
    capture_root: Path
    report_root: Path
    signal_rows: pd.DataFrame
    load_lookups: Callable[[Sequence[int]], ReplayLookups]
    shard_weights: Callable[[Sequence[int]], Mapping[int, int]]
    model_dir: Path
    lag_seconds: int
    archive_join: ArchiveJoinInput


@dataclass(frozen=True)
class ReplayPlan:
    """Which matches to replay, where, and what checkpoint rows to keep."""

    match_ids: tuple[int, ...]
    selected: int
    resumed: tuple[MakerMatchResult, ...]
    resumed_fills: tuple[EnrichedFill, ...]
    skip_keys: frozenset[int]
    write_summary: bool
    work_dir: Path


def build_order_latency() -> tuple[StaticLatencyConfig, int, int]:
    """CLOB round-trip for insert/update plus the strategy-owned cancel delay.

    Live sports orders become LIVE on the user channel at network RTT, not
    Gamma `secondsDelay`. On 28 Dota sessions after 2026-09-24, 107 of 476
    fills arrived under 1s after the place plan. Queue is snapshotted at
    accept. The venue has no amend: a reprice is a cancel plus a new order, so
    update latency matches insert. Cancels stay network-only: DotaMakerStrategy
    owns the delay via deferred cancel (`_schedule_cancel` → alert →
    `cancel_order`) so a racing fill can abort a reprice and leave the
    remainder resting (US-006). Model cancel latency stays 0.
    """
    latency_config = StaticLatencyConfig(
        insert_latency_ms=ORDER_INSERT_LATENCY_MS,
        update_latency_ms=ORDER_INSERT_LATENCY_MS,
        cancel_latency_ms=0.0,
    )
    order_latency_ns = round(ORDER_INSERT_LATENCY_MS * 1_000_000)
    cancel_latency_ns = round(ORDER_CANCEL_LATENCY_MS * 1_000_000)
    return latency_config, order_latency_ns, cancel_latency_ns


def rewrite_replay_instrument(instrument: Any, closed_at: Any) -> Any:
    """Copy a binary option with real close and zero engine fee for post-processing rebates.

    Live Gamma feeSchedule would set a non-zero taker_fee and PolymarketFeeModel would
    then credit a LIMIT rebate (~25%, not the archived 15%). US-009 owns fee math outside
    the engine, so every loaded leg must settle as raw price x quantity.
    """
    instrument_type = cast(Any, type(instrument))
    values = cast(dict[str, object], instrument_type.to_dict(instrument))
    values["expiration_ns"] = datetime_to_ns(closed_at)
    values["taker_fee"] = "0"
    info = dict(cast(dict[str, object], values.get("info") or {}))
    info["end_date_iso"] = closed_at.isoformat()
    values["info"] = info
    return instrument_type.from_dict(values)


class ReplayEndBoundary(Data):  # pyright: ignore[reportUntypedBaseClass]
    """Neutral custom data marking the requested end of an otherwise empty replay tail."""

    def __init__(self, instrument_id: Any, timestamp_ns: int) -> None:
        self.instrument_id = instrument_id
        self._timestamp_ns = timestamp_ns

    @property
    def ts_event(self) -> int:
        """Return the replay boundary's event timestamp."""
        return self._timestamp_ns

    @property
    def ts_init(self) -> int:
        """Return the replay boundary's initialization timestamp."""
        return self._timestamp_ns


class InstrumentCustomData(CustomData):  # pyright: ignore[reportUntypedBaseClass]
    """Expose the wrapped event's instrument so Nautilus can route custom data."""

    @property
    def instrument_id(self) -> Any:
        """Return the instrument associated with the wrapped boundary."""
        boundary = cast(ReplayEndBoundary, self.data)  # pyright: ignore[reportUnknownMemberType]
        return boundary.instrument_id


def assert_every_leg_loaded(contexts: Sequence[MarketContext], loaded_sims: Sequence[Any]) -> None:
    """Fail the batch, naming the matches, when a market did not load both of its legs."""
    loaded_instrument_ids = {str(loaded_sim.instrument.id) for loaded_sim in loaded_sims}
    missing = [
        context.match_id
        for context in contexts
        if not all(
            instrument_id in loaded_instrument_ids for instrument_id in context.instrument_ids
        )
    ]
    if missing:
        raise ValueError(f"replay did not load both market legs for matches {missing}")


def install_settlement_compatibility(
    backtest: Any, contexts: Sequence[MarketContext], boundary_ns: int
) -> None:
    """Give every loaded leg its real expiration, zero engine fee, and one clock boundary."""
    load_sims = backtest._load_sims_async
    closed_at_by_instrument_id = {
        instrument_id: context.market_closed_at
        for context in contexts
        for instrument_id in context.instrument_ids
    }

    async def load_sims_with_replay_end() -> list[Any]:
        """Fix loaded instruments and append one global clock boundary."""
        loaded_sims = cast(list[Any], await load_sims())
        assert_every_leg_loaded(contexts, loaded_sims)
        for index, loaded_sim in enumerate(loaded_sims):
            closed_at = closed_at_by_instrument_id[str(loaded_sim.instrument.id)]
            instrument = rewrite_replay_instrument(loaded_sim.instrument, closed_at)
            records = loaded_sim.records
            if index == 0:
                boundary = ReplayEndBoundary(instrument.id, boundary_ns)
                custom_boundary = InstrumentCustomData(DataType(ReplayEndBoundary), boundary)
                records = (*records, custom_boundary)
            loaded_sims[index] = replace(loaded_sim, instrument=instrument, records=records)
        return loaded_sims

    backtest._load_sims_async = load_sims_with_replay_end


def skip_market_artifacts(**_kwargs: object) -> dict[str, object]:
    """Skip the framework's post-run book/Brier tables; we only keep result warnings."""
    return {}


def install_skip_market_artifacts(backtest: Any) -> None:
    """Disable `_build_market_artifacts`; `report=None` still builds them otherwise."""
    backtest._build_market_artifacts = skip_market_artifacts


def install_nautilus_logging(level: str) -> Any:
    """Initialize Nautilus logging once and return the guard the caller must keep alive.

    Nautilus can install its Rust logger only once per process, and every disposed
    engine drops the guard it created itself, so the second batch's engine panics
    unless a guard we own keeps the subsystem initialized.
    """
    return cast(Any, init_logging(level_stdout=log_level_from_str(level)))


def resolve_run_policies(
    *,
    level_usdc: float,
    match_ids: Sequence[int],
    min_abs_delta: float | None,
    exit_abs_delta: float | None,
) -> dict[int, Follow300Policy]:
    """The Follow300 policy each match runs on: repo constants plus overrides."""
    cadence = read_engine_cadence()
    policies: dict[int, Follow300Policy] = {}
    for match_id in match_ids:
        policy = follow300_policy(
            level_usdc=level_usdc,
            debounce_ms=cadence.debounce_ms,
            fallback_timer_s=cadence.quoter_tick_s,
        )
        if min_abs_delta is not None:
            policy = replace(policy, min_abs_delta=min_abs_delta)
        if exit_abs_delta is not None:
            policy = replace(policy, exit_abs_delta=exit_abs_delta)
        policies[int(match_id)] = policy
    return policies


def build_kernels(
    *,
    contexts: Sequence[MarketContext],
    policies: Mapping[int, Follow300Policy],
    plans: Mapping[int, MatchFeedPlan],
    max_position_levels: int,
) -> dict[int, MatchKernelConfig]:
    """Kernel config per match from repo constants plus the catalog orientation."""
    kernels: dict[int, MatchKernelConfig] = {}
    for context in contexts:
        plan = plans[context.match_id]
        entry_stale = (
            entry_stale_seconds(plan.binding)
            if isinstance(plan, SchedulePlan)
            else GRID_FEED_STALE_SECONDS
        )
        kernels[context.match_id] = MatchKernelConfig(
            policy=policies[context.match_id],
            limits=MarketLimits(
                min_order_size=MIN_ORDER_SIZE,
                tick_size=QUOTE_GRID,
                pair_sum_tolerance=PAIR_SUM_TOLERANCE,
                radiant_token_index=context.radiant_token_index,
            ),
            freshness=FreshnessLimits(
                book_stale_s=MAX_BOOK_AGE_SECONDS,
                entry_stale_s=entry_stale,
                exit_stale_s=EXIT_FEED_STALE_SECONDS,
            ),
            max_position_levels=max_position_levels,
        )
    return kernels


def build_strategy_configs(
    *,
    game: Game,
    contexts: Sequence[MarketContext],
    signals: Mapping[int, MatchSignals],
    pauses_by_match: Mapping[int, list[OpenDotaPause]],
    order_latency_ns: int,
    cancel_latency_ns: int,
    kernels: Mapping[int, MatchKernelConfig],
    plans: Mapping[int, MatchFeedPlan],
    two_sided: TwoSidedSettings | None = None,
) -> tuple[dict[str, Any], ...]:
    """One strategy per match, bound to its own pair of instruments and model signals."""
    configs: list[dict[str, Any]] = []
    for context in contexts:
        match_signals = signals[context.match_id]
        feed_timestamps = match_signals.feed_timestamps_ns
        plan = plans[context.match_id]
        if isinstance(plan, SchedulePlan):
            ticks = plan.binding.schedule.ticks
            feed_positions = schedule_feed_positions(
                plan, history_policy_for_model_dir(plan.model_dir)
            )
            feed_ticks = [ticks[index] for index in feed_positions]
            cutoff_second = kernels[context.match_id].policy.buy_cutoff_second
            cutoff_index = next(
                (
                    index
                    for index, tick in enumerate(feed_ticks)
                    if tick.game_second >= cutoff_second
                ),
                len(feed_timestamps) - 1,
            )
            buy_cutoff_ns = feed_timestamps[cutoff_index]
            terminal_ticks = [tick.received_ns for tick in feed_ticks if tick.terminal]
            game_end_ns = (
                terminal_ticks[-1] if terminal_ticks else datetime_to_ns(context.game_ended_at)
            )
            observed_clock = ObservedClockTape(
                game_seconds=tuple(tick.game_second for tick in feed_ticks),
                paused=tuple(tick.paused for tick in feed_ticks),
                terminal=tuple(tick.terminal for tick in feed_ticks),
            )
        else:
            pauses = pauses_by_match[context.match_id]
            buy_cutoff_ns = datetime_to_ns(
                get_state_available_ts(
                    horn=context.horn_at,
                    second=kernels[context.match_id].policy.buy_cutoff_second,
                    pauses=pauses,
                )
            )
            game_end_ns = datetime_to_ns(context.game_ended_at)
            if game == "lol":
                lag_ns = LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND
                buy_cutoff_ns += lag_ns
                game_end_ns += lag_ns
            observed_clock = None
        if two_sided is None:
            payload: dict[str, Any] = {
                # keeps strategy ids and client order ids unique inside a batch
                "order_id_tag": str(context.match_id),
                "match_id": context.match_id,
                "instrument_ids": list(context.instrument_ids),
                "feed_timestamps_ns": feed_timestamps,
                "signal_timestamps_ns": match_signals.timestamps_ns,
                "source_timestamps_ns": match_signals.source_timestamps_ns,
                "predicted_deltas": match_signals.predicted_deltas,
                "dataset_market_ps": match_signals.dataset_market_ps,
                "deaths_radiant": match_signals.deaths_radiant,
                "deaths_dire": match_signals.deaths_dire,
                "kill_gates": match_signals.kill_gates,
                "board_tick_ns": match_signals.board_tick_ns,
                "observed_clock": observed_clock,
                "horn_ns": datetime_to_ns(context.horn_at),
                "buy_cutoff_ns": buy_cutoff_ns,
                "game_end_ns": game_end_ns,
                "order_latency_ns": order_latency_ns,
                "cancel_latency_ns": cancel_latency_ns,
                "kernel": kernels[context.match_id],
            }
            configs.append(
                {
                    "strategy_path": STRATEGY_PATH,
                    "config_path": STRATEGY_CONFIG_PATH,
                    "config": payload,
                }
            )
            continue
        if observed_clock is None:
            raise ValueError(
                f"two-sided match {context.match_id} has no observed clock; "
                "Dota validation schedule replays are required"
            )
        configs.append(
            {
                "strategy_path": "backtest.two_sided_strategy:TwoSidedMakerStrategy",
                "config_path": "backtest.two_sided_strategy:TwoSidedConfig",
                "config": {
                    "order_id_tag": str(context.match_id),
                    "match_id": context.match_id,
                    "instrument_ids": list(context.instrument_ids),
                    "feed_timestamps_ns": feed_timestamps,
                    "signal_timestamps_ns": match_signals.timestamps_ns,
                    "source_timestamps_ns": match_signals.source_timestamps_ns,
                    "predicted_deltas": match_signals.predicted_deltas,
                    "dataset_market_ps": match_signals.dataset_market_ps,
                    "observed_clock": observed_clock,
                    "game_end_ns": game_end_ns,
                    "order_latency_ns": order_latency_ns,
                    "cancel_latency_ns": cancel_latency_ns,
                    "radiant_token_index": context.radiant_token_index,
                    "half_spread_ticks": two_sided.half_spread_ticks,
                    "skew_per_share": two_sided.skew_per_share,
                    "net_max_shares": two_sided.net_max_shares,
                    "size_shares": two_sided.size_shares,
                    "merge_min_shares": two_sided.merge_min_shares,
                    "mid_spike": two_sided.mid_spike,
                    "debounce_ns": two_sided.debounce_ns,
                    "book_stale_s": MAX_BOOK_AGE_SECONDS,
                    "band_hi": BAND_HI,
                },
            }
        )
    return tuple(configs)


def split_into_batches(
    contexts: Sequence[MarketContext],
) -> list[tuple[MarketContext, ...]]:
    """Group markets by venue delay, then cut each group into replayable batches."""
    by_seconds_delay: dict[int, list[MarketContext]] = {}
    for context in contexts:
        by_seconds_delay.setdefault(context.seconds_delay, []).append(context)

    batches: list[tuple[MarketContext, ...]] = []
    for seconds_delay in sorted(by_seconds_delay):
        group = by_seconds_delay[seconds_delay]
        for start in range(0, len(group), MAX_MATCHES_PER_BATCH):
            batches.append(tuple(group[start : start + MAX_MATCHES_PER_BATCH]))
    return batches


def run_batch(
    *,
    game: Game,
    contexts: Sequence[MarketContext],
    signals: Mapping[int, MatchSignals],
    pauses_by_match: Mapping[int, list[OpenDotaPause]],
    source_root: Path,
    kernels: Mapping[int, MatchKernelConfig],
    plans: Mapping[int, MatchFeedPlan],
    two_sided: TwoSidedSettings | None = None,
    queue_position: bool = True,
) -> tuple[list[ReplayInstrumentResult], MakerRecords]:
    """Replay one batch of same-delay markets and return results plus harvested telemetry."""
    seconds_delays = {context.seconds_delay for context in contexts}
    if len(seconds_delays) != 1:
        raise ValueError(f"a batch must share one venue delay, got {sorted(seconds_delays)}")
    latency_config, order_latency_ns, cancel_latency_ns = build_order_latency()

    experiment = build_replay_experiment(
        name=EXPERIMENT_NAME,
        description="Dota maker join/queue on paired Telonex L2 books",
        data=MarketDataConfig(
            platform=Polymarket,
            data_type=Book,
            vendor=Telonex,
            sources=(f"local:{source_root}",),
        ),
        replays=tuple(replay for context in contexts for replay in context.as_book_replays()),
        strategy_configs=build_strategy_configs(
            game=game,
            contexts=contexts,
            signals=signals,
            pauses_by_match=pauses_by_match,
            order_latency_ns=order_latency_ns,
            cancel_latency_ns=cancel_latency_ns,
            kernels=kernels,
            plans=plans,
            two_sided=two_sided,
        ),
        initial_cash=ENGINE_STARTING_BALANCE,
        min_book_events=MIN_BOOK_EVENTS,
        execution=ExecutionModelConfig(queue_position=queue_position, latency_model=latency_config),
        report=None,
        return_summary_series=False,
        empty_message="No Telonex book windows met the Dota maker backtest requirements.",
    )
    backtest = build_backtest_for_experiment(experiment)
    boundary_ns = datetime_to_ns(max(context.clock_end for context in contexts))
    install_settlement_compatibility(backtest, contexts, boundary_ns)
    install_skip_market_artifacts(backtest)
    # Module global survives engine dispose; clear so a prior batch cannot poison this one.
    set_backtest_force_stop(False)
    clear_records()
    raw_results = backtest.run()
    records = take_records()
    results = cast(list[ReplayInstrumentResult], _finalize_replay_results(experiment, raw_results))
    log_framework_result_warnings(results)
    return results, records


def reject_warm_cache_strategy(instrument_id: Any) -> Any:
    """Warm-cache never constructs a strategy; the engine path must not run."""
    raise RuntimeError(f"warm-cache does not run the engine for {instrument_id}")


def warm_replay_cache(
    *,
    contexts: Sequence[MarketContext],
    capture_root: Path,
    schedule_archives: Mapping[int, Path],
) -> None:
    """Load Telonex books so Nautilus deltas/trade caches exist; skip the engine."""
    with create_telonex_source_tree(contexts, capture_root, schedule_archives) as source_root:
        batches = split_into_batches(contexts)
        for index, batch in enumerate(batches, start=1):
            logger.info(
                "warm-cache batch %s/%s: %s matches",
                index,
                len(batches),
                len(batch),
            )
            experiment = build_replay_experiment(
                name=EXPERIMENT_NAME,
                description="warm Telonex materialized cache",
                data=MarketDataConfig(
                    platform=Polymarket,
                    data_type=Book,
                    vendor=Telonex,
                    sources=(f"local:{source_root}",),
                ),
                replays=tuple(replay for context in batch for replay in context.as_book_replays()),
                strategy_factory=reject_warm_cache_strategy,
                initial_cash=ENGINE_STARTING_BALANCE,
                min_book_events=MIN_BOOK_EVENTS,
                report=None,
                return_summary_series=False,
                empty_message="No Telonex book windows met the Dota maker backtest requirements.",
            )
            backtest = build_backtest_for_experiment(experiment)
            loaded = asyncio.run(backtest._load_sims_async())  # pyright: ignore[reportPrivateUsage]
            assert_every_leg_loaded(batch, loaded)


def log_framework_result_warnings(results: Sequence[ReplayInstrumentResult]) -> None:
    """Surface early terminations and per-instrument warnings the report path would print."""
    for result in results:
        instrument_id = result["instrument_id"]
        if result.get("terminated_early"):
            logger.warning(
                "%s terminated early (%s)",
                instrument_id,
                result.get("stop_reason") or "unknown",
            )
        for warning in result.get("warnings") or ():
            logger.warning("%s: %s", instrument_id, warning)


def log_coverage(coverage: ValidationCoverage) -> None:
    """Report how many validation matches are replayable and why the rest are not."""
    logger.info(
        "validation matches: %s | no map market: %s | no signal rows: %s | no local Telonex: %s | archive excluded: %s | eligible: %s",
        coverage.validation_matches,
        coverage.without_map_market,
        coverage.without_signal_rows,
        coverage.without_local_telonex,
        coverage.archive_excluded,
        coverage.eligible,
    )


def read_framework_commit() -> str:
    """Return the prediction-market-backtesting HEAD commit used for this run."""
    repo = Path(prediction_market_extensions_pkg.__file__).resolve().parents[1]
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def build_whitelist_manifest_keys(
    league_whitelist: LeagueWhitelist | None, selected_ids: Sequence[int]
) -> dict[str, Any]:
    """Fingerprint LoL's mandatory filter and selected map set; Dota has no filter."""
    if league_whitelist is None:
        return {}
    ids = ",".join(str(match_id) for match_id in sorted(selected_ids))
    return {
        "league_whitelist_path": str(league_whitelist.path),
        "league_whitelist_sha256": league_whitelist.sha256,
        "selected_matches": len(selected_ids),
        "selected_matches_sha256": sha256_text(ids),
    }


def build_run_manifest(
    *,
    model_dir: Path,
    game: Game,
    signal_cadence_seed: int,
    run_policy: Follow300Policy,
    max_position_levels: int,
    selected_ids: Sequence[int],
    whitelist_keys: Mapping[str, Any],
    plans: Mapping[int, MatchFeedPlan],
    exclusions: Mapping[int, str],
    since_match: int | None,
    validation_dataset_path: Path = VALIDATION_DATASET_PATH,
    backtest_lag_seconds: int = BACKTEST_LAG_SECONDS,
    cadence_mean_interval: int | None = None,
    archives_only: bool,
) -> dict[str, Any]:
    """Fingerprint canonical inputs, the feed map, and each model dir for resume safety."""
    model_meta = read_model_meta(model_dir / MODEL_META_FILENAME)
    match_ids_text = ",".join(str(match_id) for match_id in sorted(selected_ids))
    shared = {
        "experiment": EXPERIMENT_NAME,
        "model_name": model_meta["name"],
        "framework_commit": read_framework_commit(),
        "buy_ladder_policy": run_policy.version,
        "backtest_policy": "current",
        "signal_source": "auto",
        "layers": run_policy.level_count,
        "layer_step_ticks": run_policy.step_ticks,
        "layer_usdc": run_policy.level_usdc,
        "max_position_levels": max_position_levels,
        "engine_starting_balance": ENGINE_STARTING_BALANCE,
        "insert_latency_ms": ORDER_INSERT_LATENCY_MS,
        "cancel_latency_ms": ORDER_CANCEL_LATENCY_MS,
        "buy_cutoff_second": run_policy.buy_cutoff_second,
        "min_abs_delta": run_policy.min_abs_delta,
        "exit_abs_delta": run_policy.exit_abs_delta,
        "min_entry_price": run_policy.min_entry_price,
        "max_entry_price": run_policy.max_entry_price,
        "exit_settle_seconds": run_policy.exit_settle_s,
        "sell_min_life_seconds": run_policy.sell_min_life_s,
        "hold_unconfirmed_sell": run_policy.hold_unconfirmed_sell,
        "debounce_ms": run_policy.debounce_ms,
        "quoter_tick_s": run_policy.fallback_timer_s,
        "fill_model": FILL_MODEL,
        "model_path": str(model_dir),
        "model_sha256": model_identity_sha256(model_dir),
        **({} if archives_only else {"signal_cadence_seed": signal_cadence_seed}),
        "signal_contract": "received-table-board-v1",
        "board_reaction_seconds": BOARD_REACTION_SECONDS,
        "max_signal_age_seconds": GRID_FEED_MAX_AGE_SECONDS,
        "max_exit_age_seconds": grid_exit_age_seconds(GRID_FEED_MAX_AGE_SECONDS),
        "schedule_map_sha256": schedule_map_sha256(plans),
        "feed_schedule_rules_version": EXTRACTION_RULES_VERSION,
        "admission_rules_version": ADMISSION_RULES_VERSION,
        "archive_exclusions": {
            str(match_id): exclusions[match_id] for match_id in sorted(exclusions)
        },
        "model_sha256_by_dir": {
            str(plan_model_dir): model_identity_sha256(plan_model_dir)
            for plan_model_dir in sorted({plan.model_dir for plan in plans.values()}, key=str)
        },
    }
    if since_match is not None:
        shared["since_match"] = since_match
    if archives_only:
        shared["archives_only"] = True
    has_schedule = any(isinstance(plan, SchedulePlan) for plan in plans.values())
    if game == "dota":
        payload = {
            **shared,
            "validation_dataset_path": str(validation_dataset_path),
            "validation_dataset_sha256": sha256_file(validation_dataset_path),
            "selected_matches": len(selected_ids),
            "selected_matches_sha256": sha256_text(match_ids_text),
            "train_lag_seconds": int(model_meta["source_lag_seconds"]),
            "backtest_lag_seconds": backtest_lag_seconds,
        }
        if has_schedule:
            payload["game_features_sha256"] = sha256_file(GAME_FEATURES_DATASET_PATH)
        if cadence_mean_interval is not None:
            payload["cadence_mean_interval"] = cadence_mean_interval
        return payload
    payload = {
        **shared,
        **whitelist_keys,
        "game": "lol",
        "source_lag_seconds": int(model_meta["source_lag_seconds"]),
        "split_sha256": sha256_file(LOL_SPLIT_PATH),
        "signals_sha256": sha256_file(LOL_VALIDATION_PATH),
        "market_seconds_sha256": sha256_file(LOL_BACKTEST_MARKET_SECONDS_PATH),
        "audit_sha256": sha256_file(LOL_BACKTEST_AUDIT_PATH),
        "execution_priority": list(LOL_EXECUTION_PRIORITY),
    }
    if has_schedule:
        payload["game_features_sha256"] = sha256_file(LOL_GAME_FEATURES_PATH)
    return payload


def parse_shard(raw: str) -> ShardSpec:
    """Parse --shard i/n into a zero-based index and a count of at least 2."""
    parts = raw.split("/")
    if len(parts) != 2:
        raise ValueError(f"--shard must be i/n, got {raw!r}")
    index = int(parts[0])
    count = int(parts[1])
    if count < 2:
        raise ValueError(f"--shard count must be >= 2, got {count}")
    if index < 0 or index >= count:
        raise ValueError(f"--shard index must be in 0..{count - 1}, got {index}")
    return ShardSpec(index=index, count=count)


def shard_subdir(parent_dir: Path, shard: ShardSpec) -> Path:
    """Checkpoint directory for one parallel worker under the canonical run dir."""
    return parent_dir / f"shard_{shard.index}of{shard.count}"


def format_policy_frac(value: float) -> str:
    """Encode 0.01 as 01, 0.02 as 02, 0.015 as 015, 0.35 as 35."""
    text = f"{value:.10f}".rstrip("0")
    if not text.startswith("0."):
        raise ValueError(f"run-dir frac token expects (0, 1), got {value}")
    return text[2:]


def format_validation_run_name(*, name: str, run_policy: Follow300Policy) -> str:
    """Join placement plus the effective cut/price/delta tokens, then --name."""
    delta = format_policy_frac(run_policy.min_abs_delta)
    xdelta = (
        ""
        if run_policy.exit_abs_delta == run_policy.min_abs_delta
        else f"_x{format_policy_frac(run_policy.exit_abs_delta)}"
    )
    price = format_policy_frac(run_policy.min_entry_price)
    return (
        f"validation_{PLACEMENT}_delta{delta}{xdelta}"
        f"_cut{run_policy.buy_cutoff_second}_p{price}_{name}"
    )


def build_report_dir(
    *,
    report_root: Path,
    match_id: int | None,
    limit: int | None,
    name: str | None,
    signal_cadence_seed: int,
    run_policy: Follow300Policy,
    archives_only: bool,
) -> Path:
    """Build the canonical run directory and isolate validation cadence seeds."""
    if match_id is not None:
        suffix = f"_{name}" if name else ""
        return report_root / f"match_{int(match_id)}{suffix}"
    suffix = f"_{name}" if name else ""
    if limit is not None:
        run_root = report_root / f"validation_limit_{int(limit)}{suffix}"
    else:
        run_root = report_root / format_validation_run_name(name=str(name), run_policy=run_policy)
    if archives_only:
        return run_root / ARCHIVE_DIRNAME
    return run_root / f"seed{signal_cadence_seed}"


def assign_shard(
    match_ids: Sequence[int], shard: ShardSpec, weights: Mapping[int, int]
) -> tuple[int, ...]:
    """Greedy split by weight: each id lands on the lightest shard, heavy first.

    Every worker computes the same partition from the same weights, so no
    coordination is needed. Uniform weights degrade to round-robin order.
    """
    totals = [0] * shard.count
    buckets: list[list[int]] = [[] for _ in range(shard.count)]
    for match_id in sorted(match_ids, key=lambda m: (-weights[m], m)):
        lightest = min(range(shard.count), key=lambda i: (totals[i], i))
        totals[lightest] += weights[match_id]
        buckets[lightest].append(match_id)
    return tuple(buckets[shard.index])


def clear_run_artifacts(report_dir: Path) -> None:
    """Delete checkpoint artifacts so a fresh run cannot adopt a foreign resume state."""
    for name in (
        RESULTS_FILENAME,
        FILLS_FILENAME,
        QUOTE_EVENTS_FILENAME,
        SUMMARY_FILENAME,
        MANIFEST_FILENAME,
    ):
        path = report_dir / name
        if path.exists():
            path.unlink()
    clear_quote_event_parts(report_dir)


def match_ids_missing_results(
    selected_ids: Sequence[int],
    completed: Set[int],
) -> tuple[int, ...]:
    """Match ids from the selection that have no result row yet."""
    return tuple(match_id for match_id in selected_ids if match_id not in completed)


def replay_matches(
    *,
    contexts: Sequence[MarketContext],
    context_by_match: Mapping[int, MarketContext],
    signals: Mapping[int, MatchSignals],
    pauses_by_match: Mapping[int, list[OpenDotaPause]],
    mids: Mapping[int, MidSeries],
    report_dir: Path,
    resumed: Sequence[MakerMatchResult],
    resumed_fills: Sequence[EnrichedFill],
    coverage: ValidationCoverage | None,
    selected: int,
    write_summary: bool,
    skip_keys: frozenset[int],
    manifest: Mapping[str, Any],
    capture_root: Path,
    kernels: Mapping[int, MatchKernelConfig],
    schedule_archives: Mapping[int, Path],
    provenance: Mapping[int, SignalProvenance],
    plans: Mapping[int, MatchFeedPlan],
    game: Game,
    archives_only: bool,
    two_sided: TwoSidedSettings | None = None,
    market_fees: Mapping[int, MarketFee] | None = None,
    queue_position: bool = True,
) -> None:
    """Load Telonex once, replay pending matches, and write maker artifacts."""
    started_at = time.perf_counter()
    results: list[MakerMatchResult] = list(resumed)
    all_fills: list[EnrichedFill] = list(resumed_fills)
    completed = set(skip_keys)
    completed.update(result.match_id for result in resumed)
    report_dir.mkdir(parents=True, exist_ok=True)
    # Persist fingerprint up front so a mid-run crash can --resume.
    write_manifest(report_dir, manifest)
    pending = [context for context in contexts if context.match_id not in completed]
    logger.info(
        "replay %s matches (%s already done)",
        len(pending),
        len(contexts) - len(pending),
    )

    if pending:
        with create_telonex_source_tree(pending, capture_root, schedule_archives) as source_root:
            batches = split_into_batches(pending)
            for index, batch in enumerate(batches, start=1):
                logger.info(
                    "batch %s/%s: %s matches, secondsDelay=%s",
                    index,
                    len(batches),
                    len(batch),
                    batch[0].seconds_delay,
                )
                framework_results, records = run_batch(
                    game=game,
                    contexts=batch,
                    signals=signals,
                    pauses_by_match=pauses_by_match,
                    source_root=source_root,
                    kernels=kernels,
                    plans=plans,
                    two_sided=two_sided,
                    queue_position=queue_position,
                )
                batch_results = build_maker_match_results(
                    batch,
                    framework_results,
                    records.fills,
                    records.quote_events,
                    placement=PLACEMENT,
                    fill_model=FILL_MODEL,
                    uptimes={
                        uptime.match_id: uptime.live_order_seconds for uptime in records.uptimes
                    },
                    provenance=provenance,
                )
                results.extend(batch_results)
                all_fills.extend(
                    enrich_fills(
                        records.fills,
                        mids,
                        batch,
                        fill_model=FILL_MODEL,
                        fees=market_fees,
                        board_ticks={
                            match_id: signals[match_id].board_tick_ns
                            for match_id in (context.match_id for context in batch)
                            if match_id in signals
                        },
                    )
                )
                for result in batch_results:
                    completed.add(result.match_id)
                # The tape lands before the ledger: results.parquet is what resume skips,
                # so a kill between the two writes must not drop a map's quote events.
                write_quote_event_parts(
                    report_dir=report_dir,
                    events=records.quote_events,
                    match_ids=[context.match_id for context in batch],
                )
                write_results_checkpoint(report_dir=report_dir, results=results)
                write_fills_parquet(report_dir=report_dir, fills=all_fills)

    logger.info("saved: %s", report_dir)
    replayed_ids = [result.match_id for result in results]
    if not write_summary:
        compact_quote_events(report_dir=report_dir, match_ids=replayed_ids)
        return
    write_finished_summary(
        results=results,
        fills=all_fills,
        context_by_match=context_by_match,
        mids=mids,
        coverage=coverage,
        selected=selected,
        wall_seconds=time.perf_counter() - started_at,
        manifest=manifest,
        report_dir=report_dir,
        archives_only=archives_only,
    )


def write_finished_summary(
    *,
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    context_by_match: Mapping[int, MarketContext],
    mids: Mapping[int, MidSeries],
    coverage: ValidationCoverage | None,
    selected: int,
    wall_seconds: float,
    manifest: Mapping[str, Any],
    report_dir: Path,
    archives_only: bool,
) -> tuple[str, ...]:
    """Compact the quote-event parts, write summary.json, then drop verified intermediates."""
    replayed_ids = [
        result.match_id for result in results if result.stop_reason not in NO_REPLAY_STOP_REASONS
    ]
    compacted = compact_quote_events(report_dir=report_dir, match_ids=replayed_ids)
    telemetry = read_quote_telemetry(report_dir)
    summary = build_summary_payload(
        results=results,
        fills=fills,
        drawdowns=calculate_drawdowns(results, fills, mids, context_by_match),
        coverage=coverage,
        selected=selected,
        wall_seconds=wall_seconds,
        manifest=manifest,
        telemetry=telemetry,
        mids=mids,
        contexts=context_by_match,
    )
    write_validation_outputs(report_dir=report_dir, summary=summary)
    print(format_terminal_report(summary), flush=True)
    logger.info("saved: %s", report_dir)
    problems = find_finished_run_problems(
        report_dir=report_dir,
        results=results,
        fills=fills,
        compacted=compacted,
    )
    problems = drop_finished_parts(report_dir, problems)
    if archives_only and not problems:
        write_archive_done(report_dir)
    return problems


def find_finished_run_problems(
    *,
    report_dir: Path,
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    compacted: QuoteCompaction,
) -> tuple[str, ...]:
    """Which final artifacts fail to match what the run wrote; empty means verified."""
    problems: list[str] = []
    if not (report_dir / SUMMARY_FILENAME).is_file():
        problems.append(f"{SUMMARY_FILENAME} was not written")
    problems += find_parquet_file_problems(
        report_dir / RESULTS_FILENAME,
        len(results),
        [field.name for field in fields(MakerMatchResult)],
    )
    problems += find_parquet_file_problems(
        report_dir / FILLS_FILENAME,
        len(fills),
        [field.name for field in fields(EnrichedFill)],
    )
    problems += find_quote_events_file_problems(report_dir, compacted)
    problems += [f"missing quote part for match {match_id}" for match_id in compacted.missing]
    problems += [
        f"unaccounted entry {name} in {QUOTE_EVENT_PARTS_DIRNAME}" for name in compacted.unaccounted
    ]
    return tuple(problems)


def drop_finished_parts(report_dir: Path, problems: tuple[str, ...]) -> tuple[str, ...]:
    """Delete quote_event_parts once the run verified; keep everything on any problem."""
    if problems:
        logger.warning("keeping intermediates under %s: %s", report_dir, "; ".join(problems))
        return problems
    target = parts_dir(report_dir)
    if not target.is_dir():
        return problems
    try:
        shutil.rmtree(target)
    except OSError as exc:
        logger.warning("could not remove %s: %s", target, exc)
        return (*problems, f"could not remove {target}: {exc}")
    logger.info("removed %s", target)
    return problems


def find_finished_shard_problems(shard_dir: Path) -> tuple[str, ...]:
    """Why a merged shard dir is unsafe to delete; empty means verified finished."""
    expected_names = {
        MANIFEST_FILENAME,
        RESULTS_FILENAME,
        FILLS_FILENAME,
        QUOTE_EVENTS_FILENAME,
        QUOTE_EVENT_PARTS_DIRNAME,
    }
    problems = [
        f"{shard_dir.name}: unexpected entry {entry.name}"
        for entry in sorted(shard_dir.iterdir())
        if entry.name not in expected_names
    ]
    if (shard_dir / RESULTS_FILENAME).exists() and not (
        shard_dir / QUOTE_EVENTS_FILENAME
    ).is_file():
        problems.append(
            f"{shard_dir.name}: replayed checkpoint without its own {QUOTE_EVENTS_FILENAME}"
        )
    return tuple(problems)


def merge_shard_checkpoints(
    *,
    parent_dir: Path,
    shard_count: int,
    manifest: Mapping[str, Any],
    archive: SharedArchive,
) -> MergedCheckpoints:
    """Concat parent + shard parquets; archive ids replace any copy of those maps."""
    parent_results = read_results_checkpoint(parent_dir)
    parent_fills = read_fills_checkpoint(parent_dir)
    parent_manifest = read_manifest(parent_dir)
    if parent_manifest or parent_results or parent_fills:
        assert_manifest_matches(parent_manifest, manifest)
    combined_results: list[MakerMatchResult] = list(parent_results)
    combined_fills: list[EnrichedFill] = list(parent_fills)
    shard_dirs: list[Path] = []
    for index in range(shard_count):
        shard_dir = shard_subdir(parent_dir, ShardSpec(index=index, count=shard_count))
        if not shard_dir.is_dir():
            raise ValueError(f"missing shard directory {shard_dir}")
        assert_manifest_matches(read_manifest(shard_dir), manifest)
        combined_results.extend(read_results_checkpoint(shard_dir))
        combined_fills.extend(read_fills_checkpoint(shard_dir))
        shard_dirs.append(shard_dir)
    overlaid = overlay_archive(combined_results, combined_fills, archive)
    merged_results = concat_results_without_overlap((overlaid.results,))
    for shard_dir in shard_dirs:
        copy_quote_event_parts(source_dir=shard_dir, target_dir=parent_dir)
    write_archive_parts(parent_dir, archive)
    write_results_checkpoint(report_dir=parent_dir, results=merged_results)
    write_fills_parquet(report_dir=parent_dir, fills=overlaid.fills)
    # A shards-only run never replayed through the parent, so nothing wrote its manifest.
    # Without it a reader cannot tell which cutoff, delta, or model produced these rows.
    write_manifest(parent_dir, manifest)
    return MergedCheckpoints(
        results=tuple(merged_results),
        fills=tuple(overlaid.fills),
        shard_dirs=tuple(shard_dirs),
    )


def load_replay_lookups(match_ids: Sequence[int], sources: MarketSources) -> ReplayLookups:
    """Load pauses, market contexts, and mid series for the given match ids."""
    return ReplayLookups(
        pauses_by_match={match_id: sources.catalog[match_id].pauses for match_id in match_ids},
        context_by_match={
            match_id: build_market_context(sources, match_id) for match_id in match_ids
        },
        mids=load_match_mid_series(tuple(match_ids)),
    )


def load_dota_selection(
    match_id: int | None,
    limit: int | None,
    *,
    match_ids: tuple[int, ...] | None = None,
    validation_dataset: Path = VALIDATION_DATASET_PATH,
) -> DotaSelection:
    """Load Dota validation rows and pick replayable matches."""
    signal_rows = load_usable_signal_rows(validation_dataset)
    sources = load_market_sources(signal_rows)
    capture_root = RAW_TELONEX_POLYMARKET_DIR
    report_root = BACKTESTS_DIR / EXPERIMENT_NAME
    if match_ids is not None:
        return DotaSelection(
            selected_ids=tuple(int(mid) for mid in match_ids),
            coverage=None,
            capture_root=capture_root,
            report_root=report_root,
            signal_rows=signal_rows,
            sources=sources,
        )
    if match_id is not None:
        return DotaSelection(
            selected_ids=(int(match_id),),
            coverage=None,
            capture_root=capture_root,
            report_root=report_root,
            signal_rows=signal_rows,
            sources=sources,
        )
    coverage, eligible = select_validation_matches(sources)
    selected_ids = eligible if limit is None else eligible[: int(limit)]
    return DotaSelection(
        selected_ids=selected_ids,
        coverage=coverage,
        capture_root=capture_root,
        report_root=report_root,
        signal_rows=signal_rows,
        sources=sources,
    )


def finalize_from_checkpoint(
    *,
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    lookups: ReplayLookups,
    coverage: ValidationCoverage | None,
    selected: int,
    manifest: Mapping[str, Any],
    report_dir: Path,
    wall_seconds: float,
    archives_only: bool,
) -> tuple[str, ...]:
    """Write summary.json from checkpoint rows without replaying."""
    return write_finished_summary(
        results=results,
        fills=fills,
        context_by_match=lookups.context_by_match,
        mids=lookups.mids,
        coverage=coverage,
        selected=selected,
        wall_seconds=wall_seconds,
        manifest=manifest,
        report_dir=report_dir,
        archives_only=archives_only,
    )


def _pin_catalog(parser: argparse.ArgumentParser, path: Path | None, flag: str) -> Path | None:
    """Resolve one model catalog and reject a directory without model.json."""
    if path is None:
        return None
    pinned = path.expanduser()
    if not (pinned / MODEL_META_FILENAME).is_file():
        parser.error(f"{flag} has no {MODEL_META_FILENAME}: {pinned}")
    return pinned.resolve()


def _pin_cli_model_dir(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Dota --model-dir requires --model-dir-noxp; each catalog must match its feature list."""
    if args.game == "lol" and args.model_dir_noxp is not None:
        parser.error("--model-dir-noxp is dota-only")
    if args.game == "dota" and (args.model_dir is None) != (args.model_dir_noxp is None):
        parser.error("--model-dir and --model-dir-noxp must be passed together")
    args.model_dir = _pin_catalog(parser, args.model_dir, "--model-dir")
    args.model_dir_noxp = _pin_catalog(parser, args.model_dir_noxp, "--model-dir-noxp")
    noxp = args.model_dir_noxp
    catalog_checks: list[tuple[Path, Sequence[str], str]] = []
    if args.game == "dota" and args.model_dir is not None and noxp is not None:
        catalog_checks = [
            (args.model_dir, DOTA_XP_FEATURE_COLUMNS, "--model-dir"),
            (noxp, DOTA_NOXP_FEATURE_COLUMNS, "--model-dir-noxp"),
        ]
    elif args.game == "lol" and args.model_dir is not None:
        catalog_checks = [(args.model_dir, DOTA_XP_FEATURE_COLUMNS, "--model-dir")]
    for catalog_dir, columns, flag in catalog_checks:
        try:
            require_catalog_features(catalog_dir, columns, flag)
        except ValueError as exc:
            parser.error(str(exc))


def _reject_negative(parser: argparse.ArgumentParser, value: float | None, flag: str) -> None:
    if value is not None and value < 0:
        parser.error(f"{flag} must be >= 0")


def _pin_dota_experiment_flags(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Reject LoL use of Dota experiment flags and resolve --validation-dataset."""
    if args.game == "lol" and (
        args.lag_seconds is not None
        or args.validation_dataset is not None
        or args.cadence_mean_interval is not None
    ):
        parser.error(
            "--lag-seconds, --validation-dataset, and --cadence-mean-interval are dota-only"
        )
    if args.lag_seconds is not None and args.lag_seconds < 0:
        parser.error("--lag-seconds must be >= 0")
    if args.cadence_mean_interval is not None and args.cadence_mean_interval < 1:
        parser.error("--cadence-mean-interval must be >= 1")
    if args.validation_dataset is not None:
        validation_dataset = args.validation_dataset.expanduser()
        if not validation_dataset.is_file():
            parser.error(f"--validation-dataset not found: {validation_dataset}")
        args.validation_dataset = validation_dataset.resolve()


def build_parser() -> argparse.ArgumentParser:
    """The backtest CLI parser; main() reuses it for post-selection flag errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--match-id", type=int, help="replay one linked match")
    target.add_argument(
        "--validation", action="store_true", help="replay every eligible validation market"
    )
    target.add_argument(
        "--since-match",
        type=int,
        metavar="MATCH_ID",
        help="replay the replayable cohort whose catalog match time is "
        "at/after this match (chronological)",
    )
    target.add_argument(
        "--match-ids-file",
        type=Path,
        help="replay these match ids, one integer per line",
    )
    parser.add_argument("--game", choices=("dota", "lol"), default="dota")
    parser.add_argument("--limit", type=int, help="keep only the first N eligible cohort matches")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip match rows the run's results.parquet already holds",
    )
    parser.add_argument(
        "--shard",
        type=parse_shard,
        help="i/n slice of remaining validation matches; writes shard_<i>of<n> under the run dir",
    )
    parser.add_argument(
        "--merge-shards",
        type=int,
        metavar="N",
        help="concat shard_0ofN..shard_{N-1}ofN into this run dir and write summary",
    )
    parser.add_argument(
        "--warm-cache",
        action="store_true",
        help="load Telonex books to fill the Nautilus cache; skip the engine and run dir",
    )
    parser.add_argument(
        "--name",
        help="required suffix for --validation/--since-match so a new run does not reuse an old dir",
    )
    parser.add_argument(
        "--signal-cadence-seed",
        type=int,
        default=LIVE_GRID_TIMING.seed,
        help="grid-v1 sampling seed (default: 0)",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        help="model catalog (model.json + member_*.txt); default: research. "
        "Dota also requires --model-dir-noxp",
    )
    parser.add_argument(
        "--model-dir-noxp",
        type=Path,
        help="Dota no-XP catalog for Oddin archives; required with --model-dir",
    )
    parser.add_argument(
        "--level-usdc",
        type=float,
        default=None,
        help="BUY rung notional in USDC (default: BACKTEST_LEVEL_USDC for --game)",
    )
    parser.add_argument(
        "--max-position-levels",
        type=int,
        default=None,
        help="map cap in rungs: held cost plus standing BUYs stay under this times "
        "--level-usdc (default: BACKTEST_MAX_POSITION_LEVELS for --game)",
    )
    parser.add_argument(
        "--min-abs-delta",
        type=float,
        default=None,
        help=f"override |predicted_delta| entry floor (default: from the policy, {MIN_ABS_DELTA})",
    )
    parser.add_argument(
        "--exit-abs-delta",
        type=float,
        default=None,
        help="override |predicted_delta| exit floor for the Schmitt gate "
        f"(default: from the policy, {EXIT_ABS_DELTA})",
    )
    parser.add_argument(
        "--lag-seconds",
        type=int,
        default=None,
        help="execution lag for Dota signals (default: 10)",
    )
    parser.add_argument(
        "--validation-dataset",
        type=Path,
        default=None,
        help="validation parquet for Dota signals (default: live dataset)",
    )
    parser.add_argument(
        "--cadence-mean-interval",
        type=int,
        default=None,
        help="fixed GRID mean interval; 1 keeps every second (default: grid-v1 bands)",
    )
    parser.add_argument(
        "--archives-only",
        action="store_true",
        help="replay admitted archive schedules only; drop grid-v1 maps",
    )
    parser.add_argument(
        "--strategy",
        choices=("follow300", "two-sided"),
        default="follow300",
        help="quote policy (default: follow300)",
    )
    parser.add_argument("--half-spread-ticks", type=int, default=None)
    parser.add_argument("--size-shares", type=float, default=None)
    parser.add_argument("--skew-per-share", type=float, default=None)
    parser.add_argument("--net-max-shares", type=float, default=None)
    parser.add_argument("--merge-min-shares", type=float, default=None)
    parser.add_argument("--mid-spike", action="store_true")
    parser.add_argument("--no-queue-position", action="store_true")
    return parser


def parse_args() -> argparse.Namespace:
    """Parse the mutually exclusive single-match, split, and cohort selectors."""
    parser = build_parser()
    args = parser.parse_args()
    if (
        (args.validation or args.since_match is not None)
        and not args.warm_cache
        and args.name is None
    ):
        parser.error("--name is required for --validation and --since-match")
    _check_match_ids_file(parser, args)
    if args.name is not None and (args.name == "" or "/" in args.name or ".." in args.name):
        parser.error("--name must be a non-empty single path segment")
    if args.signal_cadence_seed < 0:
        parser.error("--signal-cadence-seed must be >= 0")
    if args.match_id is not None and (
        args.limit is not None
        or args.resume
        or args.shard is not None
        or args.merge_shards is not None
    ):
        parser.error("--limit, --resume, --shard, and --merge-shards apply to cohort runs")
    if args.shard is not None and args.merge_shards is not None:
        parser.error("--shard and --merge-shards cannot be combined")
    if args.merge_shards is not None and args.merge_shards < 2:
        parser.error("--merge-shards must be >= 2")
    if args.warm_cache and args.merge_shards is not None:
        parser.error("--warm-cache and --merge-shards cannot be combined")
    if args.warm_cache and args.resume:
        parser.error("--warm-cache and --resume cannot be combined")
    _pin_dota_experiment_flags(parser, args)
    _pin_cli_model_dir(parser, args)
    _check_gate_flag_values(parser, args)
    _check_two_sided_flags(parser, args)
    return args


def _check_match_ids_file(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.match_ids_file is None:
        return
    if args.game == "lol":
        parser.error("--match-ids-file is dota-only")
    if not args.warm_cache and args.name is None:
        parser.error("--name is required for --match-ids-file")
    if (
        args.limit is not None
        or args.resume
        or args.shard is not None
        or args.merge_shards is not None
    ):
        parser.error("--match-ids-file replays that file; drop --limit, --resume, and --shard")


def _read_match_ids_file(path: Path) -> tuple[int, ...]:
    ids: list[int] = []
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        ids.append(int(text))
    if not ids:
        raise ValueError(f"no match ids in {path}")
    return tuple(ids)


def _two_sided_settings(args: argparse.Namespace) -> TwoSidedSettings | None:
    """Defaults are the pilot cell (h=3, 20 shares, merge at 20); flags override one knob."""
    if args.strategy != "two-sided":
        return None
    cadence = read_engine_cadence()
    return TwoSidedSettings(
        half_spread_ticks=3 if args.half_spread_ticks is None else args.half_spread_ticks,
        skew_per_share=2e-4 if args.skew_per_share is None else args.skew_per_share,
        net_max_shares=100.0 if args.net_max_shares is None else args.net_max_shares,
        size_shares=20.0 if args.size_shares is None else args.size_shares,
        merge_min_shares=20.0 if args.merge_min_shares is None else args.merge_min_shares,
        mid_spike=args.mid_spike,
        debounce_ns=cadence.debounce_ms * 1_000_000,
    )


def _two_sided_fees(args: argparse.Namespace) -> dict[int, MarketFee] | None:
    if args.strategy != "two-sided":
        return None
    if not MARKET_TERMS_PATH.is_file():
        raise ValueError(f"two-sided fees require {MARKET_TERMS_PATH}")
    fees = load_market_fees(MARKET_TERMS_PATH)
    by_type: dict[str, int] = {}
    for terms in fees.values():
        by_type[terms.fee_type] = by_type.get(terms.fee_type, 0) + 1
    logger.info("loaded market terms %s: %s", MARKET_TERMS_PATH, by_type)
    return fees


def _stamp_strategy(manifest: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    settings = _two_sided_settings(args)
    if settings is None:
        return manifest
    return {
        **manifest,
        "strategy": "two-sided",
        "half_spread_ticks": settings.half_spread_ticks,
        "size_shares": settings.size_shares,
        "skew_per_share": settings.skew_per_share,
        "net_max_shares": settings.net_max_shares,
        "merge_min_shares": settings.merge_min_shares,
        "mid_spike": settings.mid_spike,
        "band_hi": BAND_HI,
        "queue_position": not args.no_queue_position,
    }


def _check_two_sided_flags(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    two_sided_flags = {
        "--half-spread-ticks": args.half_spread_ticks,
        "--size-shares": args.size_shares,
        "--skew-per-share": args.skew_per_share,
        "--net-max-shares": args.net_max_shares,
        "--merge-min-shares": args.merge_min_shares,
        "--mid-spike": args.mid_spike,
        "--no-queue-position": args.no_queue_position,
    }
    used = [name for name, value in two_sided_flags.items() if value not in (None, False)]
    if args.strategy == "follow300":
        if used:
            parser.error(f"{', '.join(used)} require --strategy two-sided")
        return
    if args.game != "dota":
        parser.error("--strategy two-sided is a Dota run")
    if args.half_spread_ticks is not None and args.half_spread_ticks < 1:
        parser.error("--half-spread-ticks must be >= 1")
    if args.size_shares is not None and args.size_shares < MIN_ORDER_SIZE:
        parser.error(f"--size-shares must be >= {MIN_ORDER_SIZE}")
    if args.net_max_shares is not None and args.net_max_shares <= 0:
        parser.error("--net-max-shares must be > 0")
    if args.merge_min_shares is not None and args.merge_min_shares <= 0:
        parser.error("--merge-min-shares must be > 0")
    if args.skew_per_share is not None and args.skew_per_share < 0:
        parser.error("--skew-per-share must be >= 0")


def _check_gate_flag_values(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    _reject_negative(parser, args.min_abs_delta, "--min-abs-delta")
    _reject_negative(parser, args.exit_abs_delta, "--exit-abs-delta")
    if args.level_usdc is not None and args.level_usdc <= 0:
        parser.error("--level-usdc must be > 0")
    if args.max_position_levels is not None and args.max_position_levels < 1:
        parser.error("--max-position-levels must be >= 1")
    entry_floor = MIN_ABS_DELTA if args.min_abs_delta is None else args.min_abs_delta
    exit_floor = EXIT_ABS_DELTA if args.exit_abs_delta is None else args.exit_abs_delta
    if exit_floor > entry_floor:
        parser.error("--exit-abs-delta must not exceed the entry floor")


def load_run_selection(
    *,
    game: Game,
    match_id: int | None,
    since_match: int | None,
    limit: int | None,
    allowed_event_ids: frozenset[str] | None,
    validation_dataset: Path = VALIDATION_DATASET_PATH,
    lag_seconds: int = BACKTEST_LAG_SECONDS,
    match_ids: tuple[int, ...] | None = None,
) -> RunSelection:
    """Load LoL or Dota ids, paths, and the lookup loader for this CLI run."""
    if game == "lol":
        if since_match is not None:
            # Resolve ids against a full probe load, then pin selection to the cohort.
            probe = load_lol_selection(None, None, LOL_RESEARCH_MODEL_DIR, allowed_event_ids)
            since_ids = select_lol_since_match_ids(probe.audit, probe.signal_rows, since_match)
            if limit is not None:
                since_ids = since_ids[:limit]
            lol = replace(probe, selected_ids=since_ids, coverage=None)
        else:
            lol = load_lol_selection(match_id, limit, LOL_RESEARCH_MODEL_DIR, allowed_event_ids)
        return RunSelection(
            selected_ids=lol.selected_ids,
            coverage=lol.coverage,
            capture_root=lol.capture_root,
            report_root=lol.report_root,
            signal_rows=lol.signal_rows,
            load_lookups=partial(
                load_lol_replay_lookups,
                audit=lol.audit,
                market_seconds=lol.market_seconds,
                gamma_markets=lol.gamma_markets,
                buy_cutoff_second=BUY_CUTOFF_SECOND,
                signal_rows=lol.signal_rows,
            ),
            shard_weights=partial(
                build_lol_book_weights, audit=lol.audit, capture_root=lol.capture_root
            ),
            model_dir=LOL_RESEARCH_MODEL_DIR,
            lag_seconds=0,
            archive_join=LolArchiveJoin(lol.audit),
        )
    if match_ids is not None:
        dota = load_dota_selection(
            None, None, match_ids=match_ids, validation_dataset=validation_dataset
        )
    elif since_match is not None:
        # Resolve ids against a full probe load, then pin selection to the cohort.
        probe = load_dota_selection(None, None, validation_dataset=validation_dataset)
        since_ids = select_dota_since_match_ids(probe.sources, since_match)
        if limit is not None:
            since_ids = since_ids[:limit]
        dota = replace(probe, selected_ids=since_ids, coverage=None)
    else:
        dota = load_dota_selection(match_id, limit, validation_dataset=validation_dataset)
    return RunSelection(
        selected_ids=dota.selected_ids,
        coverage=dota.coverage,
        capture_root=dota.capture_root,
        report_root=dota.report_root,
        signal_rows=dota.signal_rows,
        load_lookups=partial(load_replay_lookups, sources=dota.sources),
        shard_weights=partial(
            build_book_weights,
            catalog=dota.sources.catalog,
            capture_root=dota.capture_root,
        ),
        model_dir=RESEARCH_MODEL_DIR,
        lag_seconds=lag_seconds,
        archive_join=DotaArchiveJoin(dota.sources.catalog),
    )


def merge_shard_run(
    report_dir: Path,
    shard_count: int,
    manifest: Mapping[str, Any],
    selected_ids: Sequence[int],
    coverage: ValidationCoverage | None,
    load_lookups: Callable[[Sequence[int]], ReplayLookups],
    archive: SharedArchive,
    archives_only: bool,
) -> None:
    """Concat shard checkpoints into the parent run dir and write the summary."""
    merged = merge_shard_checkpoints(
        parent_dir=report_dir,
        shard_count=shard_count,
        manifest=manifest,
        archive=archive,
    )
    still_missing = match_ids_missing_results(
        selected_ids,
        {result.match_id for result in merged.results},
    )
    if still_missing:
        raise ValueError(f"merge still missing {len(still_missing)} matches; shards incomplete")
    known_ids = tuple(
        dict.fromkeys((*selected_ids, *(result.match_id for result in merged.results)))
    )
    problems = list(
        finalize_from_checkpoint(
            results=merged.results,
            fills=merged.fills,
            lookups=load_lookups(known_ids),
            coverage=coverage,
            selected=len(selected_ids),
            manifest=manifest,
            report_dir=report_dir,
            wall_seconds=0.0,
            archives_only=archives_only,
        )
    )
    for shard_dir in merged.shard_dirs:
        problems += find_finished_shard_problems(shard_dir)
    if problems:
        logger.warning("keeping shard dirs under %s: %s", report_dir, "; ".join(problems))
        return
    for shard_dir in merged.shard_dirs:
        try:
            shutil.rmtree(shard_dir)
        except OSError as exc:
            logger.warning("could not remove %s: %s", shard_dir, exc)
    logger.info("removed %s merged shard dirs under %s", len(merged.shard_dirs), report_dir)


def plan_shard_replay(
    report_dir: Path,
    selected_ids: Sequence[int],
    shard: ShardSpec,
    resume: bool,
    manifest: Mapping[str, Any],
    shard_weights: Callable[[Sequence[int]], Mapping[int, int]],
    archive_ids: frozenset[int],
) -> ReplayPlan:
    """Assign leftover parent ids to this shard and optionally resume its dir."""
    parent_results = read_results_checkpoint(report_dir)
    if parent_results:
        assert_manifest_matches(read_manifest(report_dir), manifest)
    work_dir = shard_subdir(report_dir, shard)
    if "league_whitelist_sha256" in manifest:
        assert_existing_lol_run_matches(work_dir, manifest)
    work_dir.mkdir(parents=True, exist_ok=True)
    if resume:
        assert_manifest_matches(read_manifest(work_dir), manifest)
        assert_quote_event_parts_resumable(work_dir)
        resumed = tuple(read_results_checkpoint(work_dir))
        resumed_fills = tuple(read_fills_checkpoint(work_dir))
    else:
        clear_run_artifacts(work_dir)
        resumed = ()
        resumed_fills = ()
    skip_keys = frozenset(result.match_id for result in parent_results) | archive_ids
    remaining_parent = match_ids_missing_results(selected_ids, skip_keys)
    assigned = assign_shard(remaining_parent, shard, shard_weights(remaining_parent))
    match_ids = match_ids_missing_results(assigned, {result.match_id for result in resumed})
    logger.info(
        "shard %s/%s | parent remaining %s | assigned %s | still to replay %s",
        shard.index,
        shard.count,
        len(remaining_parent),
        len(assigned),
        len(match_ids),
    )
    return ReplayPlan(
        match_ids=match_ids,
        selected=len(selected_ids),
        resumed=resumed,
        resumed_fills=resumed_fills,
        skip_keys=skip_keys,
        write_summary=False,
        work_dir=work_dir,
    )


def plan_resume_replay(
    report_dir: Path,
    selected_ids: Sequence[int],
    manifest: Mapping[str, Any],
) -> ReplayPlan:
    """Skip match rows the parent results.parquet already holds."""
    assert_manifest_matches(read_manifest(report_dir), manifest)
    assert_quote_event_parts_resumable(report_dir)
    resumed = tuple(read_results_checkpoint(report_dir))
    match_ids = match_ids_missing_results(selected_ids, {result.match_id for result in resumed})
    logger.info(
        "selected: %s | resumed rows: %s | matches still missing: %s",
        len(selected_ids),
        len(resumed),
        len(match_ids),
    )
    return ReplayPlan(
        match_ids=match_ids,
        selected=len(selected_ids),
        resumed=resumed,
        resumed_fills=tuple(read_fills_checkpoint(report_dir)),
        skip_keys=frozenset(),
        write_summary=True,
        work_dir=report_dir,
    )


def finalize_empty_plan(
    plan: ReplayPlan,
    selected_ids: Sequence[int],
    coverage: ValidationCoverage | None,
    load_lookups: Callable[[Sequence[int]], ReplayLookups],
    manifest: Mapping[str, Any],
    archives_only: bool,
) -> None:
    """Write a summary or shard note when the planner found nothing to replay."""
    plan.work_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(plan.work_dir, manifest)
    if plan.write_summary:
        known_ids = tuple(
            dict.fromkeys((*selected_ids, *(result.match_id for result in plan.resumed)))
        )
        finalize_from_checkpoint(
            results=plan.resumed,
            fills=plan.resumed_fills,
            lookups=load_lookups(known_ids),
            coverage=coverage,
            selected=plan.selected,
            manifest=manifest,
            report_dir=plan.work_dir,
            wall_seconds=0.0,
            archives_only=archives_only,
        )
        return
    logger.info("shard has nothing to replay: %s", plan.work_dir)


def assert_existing_lol_run_matches(report_dir: Path, manifest: Mapping[str, Any]) -> None:
    """Reject reusing an incompatible LoL directory before deleting any artifacts."""
    artifacts = (
        MANIFEST_FILENAME,
        RESULTS_FILENAME,
        FILLS_FILENAME,
        QUOTE_EVENTS_FILENAME,
        SUMMARY_FILENAME,
    )
    if any((report_dir / name).exists() for name in artifacts):
        assert_manifest_matches(read_manifest(report_dir), manifest)


@dataclass(frozen=True)
class ScheduleCohort:
    """Admitted archive maps, in selection order, with their feed plans."""

    selected_ids: tuple[int, ...]
    plans: dict[int, MatchFeedPlan]


def require_schedule_cohort(
    selected_ids: Sequence[int], plans: Mapping[int, MatchFeedPlan]
) -> ScheduleCohort:
    """Schedule maps for an archives-only run. An empty cohort is an error."""
    cohort = select_schedule_maps(selected_ids, plans)
    if not cohort.selected_ids:
        raise ValueError("--archives-only: no admitted archive in the selection")
    logger.info("archives-only: %s schedule maps", len(cohort.selected_ids))
    return cohort


def warm_selected_cache(
    selection: RunSelection,
    shard: ShardSpec | None,
    schedule_archives: Mapping[int, Path],
) -> None:
    """Load and warm the books this process would replay."""
    match_ids = selection.selected_ids
    if shard is not None:
        match_ids = assign_shard(
            selection.selected_ids,
            shard,
            selection.shard_weights(selection.selected_ids),
        )
        logger.info(
            "warm-cache shard %s/%s | %s matches",
            shard.index,
            shard.count,
            len(match_ids),
        )
    else:
        logger.info("warm-cache | %s matches", len(match_ids))
    if not match_ids:
        return
    lookups = selection.load_lookups(match_ids)
    warm_replay_cache(
        contexts=[lookups.context_by_match[match_id] for match_id in match_ids],
        capture_root=selection.capture_root,
        schedule_archives=schedule_archives,
    )


def select_schedule_maps(
    selected_ids: Sequence[int], plans: Mapping[int, MatchFeedPlan]
) -> ScheduleCohort:
    """Keep admitted archive schedules. Grid-v1 maps stay out."""
    schedule_plans: dict[int, MatchFeedPlan] = {
        match_id: plan for match_id, plan in plans.items() if isinstance(plan, SchedulePlan)
    }
    kept = tuple(match_id for match_id in selected_ids if match_id in schedule_plans)
    return ScheduleCohort(selected_ids=kept, plans=schedule_plans)


def manifest_for_selection(
    selected_ids: Sequence[int],
    feed: FeedPlanResult,
    *,
    model_dir: Path,
    game: Game,
    signal_cadence_seed: int,
    run_policy: Follow300Policy,
    max_position_levels: int,
    league_whitelist: LeagueWhitelist | None,
    since_match: int | None,
    validation_dataset_path: Path,
    backtest_lag_seconds: int,
    cadence_mean_interval: int | None,
    archives_only: bool,
) -> dict[str, Any]:
    """One fingerprint builder. archives_only keeps schedule maps and drops the cadence seed."""
    ids = selected_ids
    plans = feed.plans
    if archives_only:
        cohort = select_schedule_maps(selected_ids, feed.plans)
        ids = cohort.selected_ids
        plans = cohort.plans
    return build_run_manifest(
        model_dir=model_dir,
        game=game,
        signal_cadence_seed=signal_cadence_seed,
        run_policy=run_policy,
        max_position_levels=max_position_levels,
        selected_ids=ids,
        whitelist_keys=build_whitelist_manifest_keys(league_whitelist, ids),
        plans=plans,
        exclusions=feed.exclusions,
        since_match=since_match,
        validation_dataset_path=validation_dataset_path,
        backtest_lag_seconds=backtest_lag_seconds,
        cadence_mean_interval=cadence_mean_interval,
        archives_only=archives_only,
    )


def load_seed_archive(
    report_dir: Path,
    *,
    archives_only: bool,
    match_id: int | None,
    validation: bool,
    limit: int | None,
    expected_manifest: Callable[[], Mapping[str, Any]],
) -> SharedArchive:
    """Read `_archive` for a validation or --limit seed. Skip the hash unless DONE exists."""
    if archives_only or match_id is not None or not (validation or limit is not None):
        return empty_shared_archive()
    archive_dir = report_dir.parent / ARCHIVE_DIRNAME
    if not (archive_dir / ARCHIVE_DONE_FILENAME).is_file():
        return empty_shared_archive()
    return load_shared_archive(archive_dir, expected_manifest())


def plan_unsharded_replay(
    report_dir: Path,
    selected_ids: Sequence[int],
    resume: bool,
    manifest: Mapping[str, Any],
    archive: SharedArchive,
) -> ReplayPlan:
    """Fresh or resume path. Graft is a no-op when the archive is empty."""
    if resume:
        assert_manifest_matches(read_manifest(report_dir), manifest)
    else:
        clear_run_artifacts(report_dir)
    graft_shared_archive(report_dir, archive, manifest)
    if resume or archive.ids:
        return plan_resume_replay(report_dir, selected_ids, manifest)
    logger.info(
        "selected: %s | matches still missing: %s",
        len(selected_ids),
        len(selected_ids),
    )
    return ReplayPlan(
        match_ids=tuple(selected_ids),
        selected=len(selected_ids),
        resumed=(),
        resumed_fills=(),
        skip_keys=frozenset(),
        write_summary=True,
        work_dir=report_dir,
    )


def plan_replay_ids(
    args: argparse.Namespace,
    selected_ids: Sequence[int],
    coverage: ValidationCoverage | None,
    report_dir: Path,
    manifest: Mapping[str, Any],
    load_lookups: Callable[[Sequence[int]], ReplayLookups],
    shard_weights: Callable[[Sequence[int]], Mapping[int, int]],
    archive: SharedArchive,
    archives_only: bool,
) -> ReplayPlan | None:
    """Pick match ids for this process. None means merge or empty work already finished."""
    if archives_only and (report_dir / ARCHIVE_DONE_FILENAME).is_file():
        try:
            assert_manifest_matches(read_manifest(report_dir), manifest)
        except ValueError as exc:
            raise ValueError(f"delete `{ARCHIVE_DIRNAME}` to rebuild") from exc
        logger.info("archive already finished")
        return None
    if "league_whitelist_sha256" in manifest:
        assert_existing_lol_run_matches(report_dir, manifest)
    report_dir.mkdir(parents=True, exist_ok=True)
    if args.match_id is not None:
        clear_run_artifacts(report_dir)
        return ReplayPlan(
            match_ids=tuple(selected_ids),
            selected=1,
            resumed=(),
            resumed_fills=(),
            skip_keys=frozenset(),
            write_summary=True,
            work_dir=report_dir,
        )
    if args.merge_shards is not None:
        merge_shard_run(
            report_dir,
            args.merge_shards,
            manifest,
            selected_ids,
            coverage,
            load_lookups,
            archive,
            archives_only,
        )
        return None
    if args.shard is not None:
        plan = plan_shard_replay(
            report_dir,
            selected_ids,
            args.shard,
            args.resume,
            manifest,
            shard_weights,
            archive.ids,
        )
    else:
        plan = plan_unsharded_replay(report_dir, selected_ids, args.resume, manifest, archive)
    if not plan.match_ids:
        finalize_empty_plan(plan, selected_ids, coverage, load_lookups, manifest, archives_only)
        return None
    return plan


def resolve_run_signals(
    *,
    signal_cadence_seed: int,
    cadence_mean_interval: int | None,
    game: Game,
    plan_match_ids: Sequence[int],
    signal_rows: pd.DataFrame,
    model_dir: Path,
    selection_lag_seconds: int,
    plans: Mapping[int, MatchFeedPlan],
    mids: Mapping[int, MidSeries],
) -> dict[int, MatchSignals]:
    """Per-plan signals: archive schedule ticks for bound matches, grid-v1 else."""
    fallback_ids: list[int] = []
    schedule_plans: dict[int, SchedulePlan] = {}
    for match_id in plan_match_ids:
        plan = plans[match_id]
        if isinstance(plan, SchedulePlan):
            schedule_plans[match_id] = plan
        else:
            fallback_ids.append(match_id)
    signals: dict[int, MatchSignals] = {}
    if fallback_ids:
        timing = SignalTiming(
            seed=signal_cadence_seed,
            max_age_seconds=GRID_FEED_MAX_AGE_SECONDS,
            mean_interval_seconds=cadence_mean_interval,
        )
        signals.update(
            build_match_signals(
                fallback_ids,
                signal_rows,
                model_dir,
                selection_lag_seconds,
                game,
                timing,
                mids,
            )
        )
    if schedule_plans:
        feature_rows = load_game_feature_rows(game, list(schedule_plans))
        try:
            signals.update(build_schedule_match_signals(schedule_plans, feature_rows, mids))
        except DatasetReadinessError as exc:
            raise ValueError(f"dataset not ready for archive replay: {exc}") from exc
    logger.info(
        "signals: %s archive-schedule + %s grid-v1 matches",
        len(schedule_plans),
        len(fallback_ids),
    )
    return signals


def _apply_feed_exclusions(selection: RunSelection, feed: FeedPlanResult) -> RunSelection:
    """Drop excluded matches from the selection; never fall back to grid."""
    excluded = {
        match_id: feed.exclusions[match_id]
        for match_id in selection.selected_ids
        if match_id in feed.exclusions
    }
    selected_ids = tuple(
        match_id for match_id in selection.selected_ids if match_id not in excluded
    )
    if not selected_ids:
        detail = ", ".join(f"{match_id}: {reason}" for match_id, reason in sorted(excluded.items()))
        raise ValueError(f"archive feed excluded every selected match ({detail})")
    coverage = selection.coverage
    if coverage is not None:
        coverage = replace(coverage, archive_excluded=len(feed.exclusions))
    return replace(selection, selected_ids=selected_ids, coverage=coverage)


def main() -> None:
    """CLI entrypoint for the Dota maker backtest."""
    setup_logging()
    args = parse_args()
    load_dotenv()
    # Keep the run offline and reproducible: a day the local Telonex capture has
    # no trades for would otherwise be fetched from Polymarket's public trades API.
    os.environ.setdefault("TELONEX_DISABLE_POLYMARKET_TRADE_FALLBACK", "1")
    # Books come off local disk, so the loader's 32 default workers only add
    # concurrent Gamma metadata requests, and those start failing outright.
    os.environ.setdefault("BACKTEST_REPLAY_LOAD_WORKERS", str(REPLAY_LOAD_WORKERS))

    # assigned only to keep the log guard alive for the whole run
    default_level = "INFO" if args.match_id is not None else "WARNING"
    _log_guard = install_nautilus_logging(os.environ.get("BACKTEST_LOG_LEVEL", default_level))

    game: Game = args.game
    league_whitelist = load_canonical_league_whitelist() if game == "lol" else None
    allowed_event_ids = (
        None
        if league_whitelist is None
        else select_backtest_event_ids(league_whitelist, LOL_UNIVERSE_PATH)
    )
    selection = load_run_selection(
        game=game,
        match_id=args.match_id,
        since_match=args.since_match,
        limit=args.limit,
        allowed_event_ids=allowed_event_ids,
        validation_dataset=(
            VALIDATION_DATASET_PATH if args.validation_dataset is None else args.validation_dataset
        ),
        lag_seconds=(BACKTEST_LAG_SECONDS if args.lag_seconds is None else args.lag_seconds),
        match_ids=(
            None if args.match_ids_file is None else _read_match_ids_file(args.match_ids_file)
        ),
    )
    feed = resolve_feed_plans(
        selection.archive_join,
        selection.selected_ids,
        args.model_dir,
        args.match_id,
        model_override_noxp=args.model_dir_noxp,
    )
    selection = _apply_feed_exclusions(selection, feed)
    if args.archives_only:
        cohort = require_schedule_cohort(selection.selected_ids, feed.plans)
        selection = replace(selection, selected_ids=cohort.selected_ids)
        feed = replace(feed, plans=cohort.plans)
    reject_schedule_flags(
        feed.plans,
        lag_seconds=args.lag_seconds,
        cadence_mean_interval=args.cadence_mean_interval,
        validation_dataset=args.validation_dataset,
    )
    schedule_archives = schedule_archive_dirs(feed.plans)
    level_usdc = BACKTEST_LEVEL_USDC[game] if args.level_usdc is None else args.level_usdc
    max_position_levels = resolve_max_position_levels(game, args.max_position_levels)
    run_policies = resolve_run_policies(
        level_usdc=level_usdc,
        match_ids=selection.selected_ids,
        min_abs_delta=args.min_abs_delta,
        exit_abs_delta=args.exit_abs_delta,
    )
    if args.model_dir is not None:
        selection = replace(selection, model_dir=args.model_dir)
    if league_whitelist is not None:
        logger.info(
            "league whitelist %s: %s maps selected",
            league_whitelist.path,
            len(selection.selected_ids),
        )
    model_dir = selection.model_dir
    if selection.coverage is not None:
        log_coverage(selection.coverage)

    if args.warm_cache:
        warm_selected_cache(selection, args.shard, schedule_archives)
        return

    # The run name carries the effective entry floor, so the policies come first.
    first_id = selection.selected_ids[0]
    run_policy = run_policies[first_id]
    report_dir = build_report_dir(
        report_root=selection.report_root,
        match_id=args.match_id,
        limit=args.limit,
        name=args.name,
        signal_cadence_seed=args.signal_cadence_seed,
        run_policy=run_policy,
        archives_only=args.archives_only,
    )
    validation_dataset_path = (
        VALIDATION_DATASET_PATH if args.validation_dataset is None else args.validation_dataset
    )

    selected_ids = selection.selected_ids
    lag_seconds = selection.lag_seconds

    def build_manifest_for(archives_only: bool) -> dict[str, Any]:
        return manifest_for_selection(
            selected_ids,
            feed,
            model_dir=model_dir,
            game=game,
            signal_cadence_seed=args.signal_cadence_seed,
            run_policy=run_policy,
            max_position_levels=max_position_levels,
            league_whitelist=league_whitelist,
            since_match=args.since_match,
            validation_dataset_path=validation_dataset_path,
            backtest_lag_seconds=lag_seconds,
            cadence_mean_interval=args.cadence_mean_interval,
            archives_only=archives_only,
        )

    manifest = _stamp_strategy(build_manifest_for(args.archives_only), args)
    archive = load_seed_archive(
        report_dir,
        archives_only=args.archives_only,
        match_id=args.match_id,
        validation=args.validation,
        limit=args.limit,
        expected_manifest=lambda: _stamp_strategy(build_manifest_for(True), args),
    )
    plan = plan_replay_ids(
        args,
        selection.selected_ids,
        selection.coverage,
        report_dir,
        manifest,
        selection.load_lookups,
        selection.shard_weights,
        archive,
        args.archives_only,
    )
    if plan is None:
        return

    if plan.write_summary:
        known_ids = tuple(
            dict.fromkeys((*selection.selected_ids, *(result.match_id for result in plan.resumed)))
        )
    else:
        known_ids = tuple(
            dict.fromkeys((*plan.match_ids, *(result.match_id for result in plan.resumed)))
        )
    lookups = selection.load_lookups(known_ids)
    engine_skip_ids = frozenset[int]() if args.match_id is not None else NAUTILUS_ZERO_FILL_SKIP_IDS
    provenance = signal_provenance_map(feed.plans, plan.match_ids)
    fault_results = tuple(
        engine_fault_match_result(
            lookups.context_by_match[match_id],
            placement=PLACEMENT,
            fill_model=FILL_MODEL,
            stop_reason=NAUTILUS_ZERO_FILL_STOP_REASON,
            provenance=provenance[match_id],
        )
        for match_id in plan.match_ids
        if match_id in engine_skip_ids
    )
    contexts = [lookups.context_by_match[match_id] for match_id in plan.match_ids]
    kernels = build_kernels(
        contexts=contexts,
        policies=run_policies,
        plans=feed.plans,
        max_position_levels=max_position_levels,
    )
    signals = resolve_run_signals(
        signal_cadence_seed=args.signal_cadence_seed,
        cadence_mean_interval=args.cadence_mean_interval,
        game=game,
        plan_match_ids=plan.match_ids,
        signal_rows=selection.signal_rows,
        model_dir=model_dir,
        selection_lag_seconds=selection.lag_seconds,
        plans=feed.plans,
        mids=lookups.mids,
    )
    empty_signal_ids = (
        []
        if args.match_id is not None
        else [
            match_id
            for match_id in plan.match_ids
            if match_id not in engine_skip_ids and not signals[match_id].timestamps_ns
        ]
    )
    if empty_signal_ids:
        logger.warning(
            "no priced signal ticks for matches %s; recording terminated_early",
            sorted(empty_signal_ids),
        )
        fault_results = (
            *fault_results,
            *(
                engine_fault_match_result(
                    lookups.context_by_match[match_id],
                    placement=PLACEMENT,
                    fill_model=FILL_MODEL,
                    stop_reason=EMPTY_SIGNAL_TAPE_STOP_REASON,
                    provenance=provenance[match_id],
                )
                for match_id in empty_signal_ids
            ),
        )
    coverage = selection.coverage
    capture_root = selection.capture_root
    # The prepare frames feed lookups and signals, then sit unused for the whole replay.
    # LoL keeps a gigabyte of read-only pandas per worker that way, and twelve workers
    # of that swap a 32 GiB machine. Drop them before the first map.
    del selection

    replay_matches(
        contexts=contexts,
        context_by_match=lookups.context_by_match,
        signals=signals,
        pauses_by_match=lookups.pauses_by_match,
        mids=lookups.mids,
        report_dir=plan.work_dir,
        resumed=(*plan.resumed, *fault_results),
        resumed_fills=plan.resumed_fills,
        coverage=coverage,
        selected=plan.selected,
        write_summary=plan.write_summary,
        skip_keys=plan.skip_keys | {result.match_id for result in fault_results},
        manifest=manifest,
        capture_root=capture_root,
        kernels=kernels,
        schedule_archives=schedule_archives,
        provenance=provenance,
        plans=feed.plans,
        game=game,
        archives_only=args.archives_only,
        two_sided=_two_sided_settings(args),
        market_fees=_two_sided_fees(args),
        queue_position=not args.no_queue_position,
    )


if __name__ == "__main__":
    main()
