"""Join LoL livestats to local Telonex books and publish dataset parquets."""

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib import import_module
from multiprocessing import get_context
from pathlib import Path
from typing import Annotated, cast

import typer

from lol.constants import (
    LOL_DATASET_COLUMNS,
    LOL_DATASET_INTEGER_COLUMNS,
    LOL_DETAILS_DIR,
    LOL_LINKS_PATH,
    LOL_MAP_BUILD_CACHE_DIR,
    LOL_PREPARE_END_SECOND,
    LOL_PREPARE_WORKERS,
    LOL_PRIOR_WINDOW_SECONDS,
    LOL_TARGET_HORIZON_SECONDS,
    LOL_VALIDATION_START_TIME,
    LOL_WINDOWS_DIR,
    REASON_ACCEPTED,
    REASON_MISSING_BOOKS,
    REASON_MISSING_PRIOR,
    REASON_ZERO_LABELED_ROWS,
    REASON_ZERO_USABLE_ROWS,
)
from lol.livestats_frames import (
    GridRow,
    LivestatsDrop,
    LivestatsOk,
    prepare_map_livestats_until,
)
from lol.map_build_cache import (
    build_input_stamp,
    map_cache_path,
    read_cached_build,
    write_cached_build,
)
from lol.networth import DEFAULT_ITEM_CATALOG_DIR, ItemCatalog, load_item_catalog
from lol.parquet_io import read_parquet_rows, write_parquet_rows
from lol.replay import (
    audit_reason,
    book_load_window,
    build_market_seconds,
    has_required_channels,
    parse_clob_tokens,
    replay_bounds,
    unix_seconds,
)
from lol.types import (
    LolBacktestAuditRow,
    LolBacktestMarketSecondRow,
    LolDatasetRow,
    LolLinkRow,
    LolPrepareAuditRow,
    LolPrepareAuditSplit,
    LolPrepareSplit,
    LolPrepareSplitRow,
    MapBuild,
)
from shared.constants.lol import LOL_DATASETS_DIR, LOL_RAW_TELONEX_DIR, LOL_SOURCE_LAG_SECONDS
from shared.types.dataset import DotaGameFeatureRow
from shared.utils.dota_features import snapshot_features
from shared.utils.log import print_count
from shared.utils.telonex_book import (
    TokenBook,
    load_token_book,
    lookup_market_p_after,
    lookup_strict_prior,
)

PREPARE_PROGRESS_EVERY = 50

DATASET_INTEGER_COLUMNS = LOL_DATASET_INTEGER_COLUMNS
SPLIT_COLUMNS: list[str] = list(LolPrepareSplitRow.__annotations__)
SPLIT_INTEGER_COLUMNS = ["match_id", "game_number", "start_time", "event_start_time"]
AUDIT_COLUMNS: list[str] = list(LolPrepareAuditRow.__annotations__)
AUDIT_INTEGER_COLUMNS = [
    "game_number",
    "match_id",
    "start_time",
    "invariant_rule",
    "pause_count",
    "frame_count",
    "grid_seconds",
    "dataset_rows",
    "skipped_age_rows",
    "skipped_market_rows",
    "skipped_invariant_rows",
    "skipped_stamp_rows",
]
BACKTEST_MARKET_COLUMNS: list[str] = list(LolBacktestMarketSecondRow.__annotations__)
BACKTEST_MARKET_INTEGER_COLUMNS = ["match_id", "second", "state_ts_us"]
BACKTEST_AUDIT_COLUMNS: list[str] = list(LolBacktestAuditRow.__annotations__)
BACKTEST_AUDIT_INTEGER_COLUMNS = [
    "match_id",
    "radiant_token_index",
    "resolved_outcome_index",
    "replay_start_ts",
    "game_ended_at_ts",
    "replay_end_ts",
    "signal_row_count",
    "market_second_count",
    "source_lag_seconds",
]
GAME_FEATURE_COLUMNS: list[str] = list(DotaGameFeatureRow.__annotations__)
GAME_FEATURE_INTEGER_COLUMNS = [
    "match_id",
    "game_second",
    "radiant_nw_adv",
    "radiant_nw",
    "dire_nw",
    "radiant_xp_adv",
    "deaths_radiant",
    "deaths_dire",
    "top1_nw_adv",
    "top3_nw_adv",
]


@dataclass(frozen=True)
class TokenPair:
    """Oriented Telonex token ids for one map."""

    radiant: str
    dire: str


@dataclass(frozen=True)
class MapBuildCacheResult:
    """Per-map prepare result and whether it was read from cache."""

    build: MapBuild
    hit: bool


@dataclass(frozen=True)
class MapBuildBatch:
    """Ordered per-map builds plus cache hit count from the process pool."""

    builds: tuple[MapBuild, ...]
    cache_hits: int


def parse_token_pair(link: LolLinkRow) -> TokenPair:
    """Read the two CLOB token ids and orient Radiant to Blue."""
    tokens = cast(list[str], json.loads(link["clob_token_ids_json"]))
    radiant_index = link["radiant_token_index"]
    return TokenPair(radiant=tokens[radiant_index], dire=tokens[1 - radiant_index])


def dropped_map(
    link: LolLinkRow,
    livestats: LivestatsOk | LivestatsDrop | None,
    reason: str,
    invariant_rule: int | None,
    skipped_market_rows: int,
) -> MapBuild:
    """Build an excluded-map audit record."""
    start_time: int | None = None
    pause_count = 0
    pause_seconds = 0.0
    frame_count = 0
    grid_seconds = 0
    skipped_age = 0
    skipped_invariant = 0
    skipped_stamp = 0
    if isinstance(livestats, LivestatsOk):
        start_time = livestats.start_time
        pause_count = livestats.pause_count
        pause_seconds = livestats.pause_seconds
        frame_count = livestats.frame_count
        grid_seconds = len(livestats.grid_rows)
        skipped_age = livestats.skipped_age_rows
        skipped_invariant = livestats.skipped_invariant_rows
        skipped_stamp = livestats.skipped_stamp_rows
    elif isinstance(livestats, LivestatsDrop):
        start_time = livestats.start_time
        pause_count = livestats.pause_count
        pause_seconds = livestats.pause_seconds
        frame_count = livestats.frame_count
        invariant_rule = livestats.invariant_rule if invariant_rule is None else invariant_rule
        skipped_invariant = livestats.skipped_invariant_rows
        skipped_stamp = livestats.skipped_stamp_rows
    return MapBuild(
        link=link,
        match_id=int(str(link["esports_game_id"])),
        start_time=start_time,
        pause_count=pause_count,
        pause_seconds=pause_seconds,
        frame_count=frame_count,
        grid_seconds=grid_seconds,
        rows=(),
        skipped_age_rows=skipped_age,
        skipped_market_rows=skipped_market_rows,
        skipped_invariant_rows=skipped_invariant,
        skipped_stamp_rows=skipped_stamp,
        included=False,
        reason=reason,
        invariant_rule=invariant_rule,
        market_rows=(),
        feature_rows=(),
        backtest_audit=None,
    )


@dataclass(frozen=True)
class MarketJoin:
    """Row-level current/target join plus skipped-market count."""

    rows: tuple[LolDatasetRow, ...]
    skipped_market_rows: int


def finite_mid(value: float | None) -> float | None:
    """Keep a finite midpoint; None otherwise."""
    if value is None or not math.isfinite(value):
        return None
    return value


def build_dataset_row(
    livestats: LivestatsOk,
    link: LolLinkRow,
    slot: GridRow,
    radiant_win: bool,
    prior: float,
    current: float,
    label: float | None,
) -> LolDatasetRow:
    """Assemble one parquet row from grid features and the optional 300s mid."""
    return LolDatasetRow(
        match_id=livestats.match_id,
        start_time=livestats.start_time,
        event_id=str(link["event_id"]),
        second=slot.second,
        state_ts_us=slot.state_wall_us,
        radiant_win=radiant_win,
        **snapshot_features(slot.features),
        market_radiant_prior=prior,
        market_p_radiant=current,
        signal_market_p_radiant_300s=label,
    )


def join_market_rows(
    livestats: LivestatsOk,
    link: LolLinkRow,
    prior: float,
    radiant_book: TokenBook,
    dire_book: TokenBook,
) -> MarketJoin:
    """Attach current mid plus the 300s label at any second where the future mid is finite.

    Drop the grid row when the current mid fails; the label stays None only when
    the horizon escapes the captured book.
    """
    radiant_win = int(link["resolved_outcome_index"]) == int(link["radiant_token_index"])
    rows: list[LolDatasetRow] = []
    skipped = 0
    for slot in livestats.grid_rows:
        current = finite_mid(
            lookup_market_p_after(
                radiant_book, dire_book, slot.state_wall_us, LOL_SOURCE_LAG_SECONDS
            )
        )
        if current is None:
            skipped += 1
            continue
        label = finite_mid(
            lookup_market_p_after(
                radiant_book,
                dire_book,
                slot.state_wall_us,
                LOL_SOURCE_LAG_SECONDS + LOL_TARGET_HORIZON_SECONDS,
            )
        )
        rows.append(build_dataset_row(livestats, link, slot, radiant_win, prior, current, label))
    return MarketJoin(tuple(rows), skipped)


def build_game_feature_rows(livestats: LivestatsOk, prior: float) -> tuple[DotaGameFeatureRow, ...]:
    """One exact-second model-input row per age-gated grid slot; no market join."""
    rows: list[DotaGameFeatureRow] = []
    for slot in livestats.grid_rows:
        rows.append(
            DotaGameFeatureRow(
                match_id=livestats.match_id,
                game_second=slot.second,
                **snapshot_features(slot.features),
                market_radiant_prior=prior,
            )
        )
    return tuple(rows)


@lru_cache(maxsize=1)
def item_catalog_for_worker(path: str) -> ItemCatalog:
    """Load the pinned item catalog once per worker process."""
    return load_item_catalog(Path(path))


def build_one_map(
    link: LolLinkRow,
    windows_dir: Path,
    details_dir: Path,
    item_catalog_dir: Path,
    telonex_root: Path,
) -> MapBuild:
    """Clock, invariants, prior, and row-level market join for one linked map."""
    livestats = prepare_map_livestats_until(
        link,
        windows_dir,
        details_dir,
        item_catalog_for_worker(str(item_catalog_dir)),
        LOL_PREPARE_END_SECOND,
    )
    if isinstance(livestats, LivestatsDrop):
        return dropped_map(link, livestats, livestats.reason, livestats.invariant_rule, 0)
    tokens = parse_token_pair(link)
    bounds = replay_bounds(livestats)
    window = book_load_window(livestats, bounds, livestats.grid_rows)
    radiant_book = load_token_book(
        token_id=tokens.radiant,
        start_us=window.start_us,
        end_us=window.end_us,
        telonex_root=telonex_root,
    )
    dire_book = load_token_book(
        token_id=tokens.dire,
        start_us=window.start_us,
        end_us=window.end_us,
        telonex_root=telonex_root,
    )
    if radiant_book is None or dire_book is None:
        return dropped_map(link, livestats, REASON_MISSING_BOOKS, None, 0)
    prior = lookup_strict_prior(
        radiant_book, dire_book, livestats.spawn_us, LOL_PRIOR_WINDOW_SECONDS
    )
    if prior is None:
        return dropped_map(link, livestats, REASON_MISSING_PRIOR, None, 0)
    joined = join_market_rows(livestats, link, prior, radiant_book, dire_book)
    if not joined.rows:
        return dropped_map(
            link, livestats, REASON_ZERO_USABLE_ROWS, None, joined.skipped_market_rows
        )
    event_id = str(link["event_id"])
    condition_id = str(link["condition_id"])
    clob = parse_clob_tokens(link)
    has_books = False
    has_onchain_fills = False
    if clob is not None:
        channels = has_required_channels(
            telonex_root, clob, bounds.replay_start_wall, bounds.replay_end_wall
        )
        has_books = channels.has_books
        has_onchain_fills = channels.has_onchain_fills
    market_rows = tuple(
        build_market_seconds(
            livestats.match_id,
            event_id,
            condition_id,
            livestats.spawn_wall_seconds,
            livestats.pauses,
            bounds.last_market_second,
            radiant_book,
            dire_book,
        )
    )
    feature_rows = build_game_feature_rows(livestats, prior)
    reason = audit_reason(has_books, has_onchain_fills, len(joined.rows))
    token_id_0 = "" if clob is None else clob.token_id_0
    token_id_1 = "" if clob is None else clob.token_id_1
    radiant_win = int(link["resolved_outcome_index"]) == int(link["radiant_token_index"])
    backtest_audit: LolBacktestAuditRow = {
        "match_id": livestats.match_id,
        "event_id": event_id,
        "condition_id": condition_id,
        "slug": "",
        "token_id_0": token_id_0,
        "token_id_1": token_id_1,
        "radiant_token_index": int(link["radiant_token_index"]),
        "resolved_outcome": str(link["resolved_outcome"]),
        "resolved_outcome_index": int(link["resolved_outcome_index"]),
        "radiant_win": radiant_win,
        "replay_start_ts": unix_seconds(bounds.replay_start_wall),
        "game_ended_at_ts": unix_seconds(bounds.game_ended_at_wall),
        "replay_end_ts": unix_seconds(bounds.replay_end_wall),
        "has_books": has_books,
        "has_onchain_fills": has_onchain_fills,
        "signal_row_count": len(joined.rows),
        "market_second_count": len(market_rows),
        "source_lag_seconds": LOL_SOURCE_LAG_SECONDS,
        "eligible": reason == REASON_ACCEPTED,
        "reason": reason,
        "ok_quote_fraction": ok_quote_fraction(market_rows),
    }
    return MapBuild(
        link=link,
        match_id=livestats.match_id,
        start_time=livestats.start_time,
        pause_count=livestats.pause_count,
        pause_seconds=livestats.pause_seconds,
        frame_count=livestats.frame_count,
        grid_seconds=len(livestats.grid_rows),
        rows=joined.rows,
        skipped_age_rows=livestats.skipped_age_rows,
        skipped_market_rows=joined.skipped_market_rows,
        skipped_invariant_rows=livestats.skipped_invariant_rows,
        skipped_stamp_rows=livestats.skipped_stamp_rows,
        included=True,
        reason=REASON_ACCEPTED,
        invariant_rule=None,
        market_rows=market_rows,
        feature_rows=feature_rows,
        backtest_audit=backtest_audit,
    )


def build_one_map_cached(
    link: LolLinkRow,
    windows_dir: Path,
    details_dir: Path,
    item_catalog_dir: Path,
    telonex_root: Path,
    cache_dir: Path,
) -> MapBuildCacheResult:
    """Return a cached MapBuild when the input stamp matches, otherwise compute and store one."""
    stamp = build_input_stamp(link, windows_dir, details_dir, telonex_root, item_catalog_dir)
    path = map_cache_path(cache_dir, str(link["esports_game_id"]))
    cached = read_cached_build(path, stamp)
    if cached is not None:
        return MapBuildCacheResult(build=cached, hit=True)
    build = build_one_map(link, windows_dir, details_dir, item_catalog_dir, telonex_root)
    write_cached_build(path, stamp, build)
    return MapBuildCacheResult(build=build, hit=False)


def event_start_times(builds: Sequence[MapBuild]) -> dict[str, int]:
    """Min spawn start_time per PM event, including maps later dropped for market."""
    starts: dict[str, int] = {}
    for build in builds:
        if build.start_time is None:
            continue
        event_id = str(build.link["event_id"])
        current = starts.get(event_id)
        if current is None or build.start_time < current:
            starts[event_id] = build.start_time
    return starts


def split_for_event(event_start: int) -> LolPrepareSplit:
    """Assign the whole PM event to train or validation."""
    if event_start < LOL_VALIDATION_START_TIME:
        return "train"
    return "validation"


def ok_quote_fraction(market_rows: Sequence[LolBacktestMarketSecondRow]) -> float:
    """Share of market seconds whose quote status is ok."""
    if not market_rows:
        return 0.0
    ok = 0
    for row in market_rows:
        if row["market_status"] == "ok":
            ok += 1
    return ok / len(market_rows)


def labeled_row_count(build: MapBuild) -> int:
    """Count rows that carry a 300s midpoint label at any game second."""
    count = 0
    for row in build.rows:
        if row["signal_market_p_radiant_300s"] is None:
            continue
        count += 1
    return count


def eligibility_drop_reason(build: MapBuild, split: LolPrepareSplit) -> str | None:
    """Drop reason after the event split, or None to keep the map."""
    if split == "train":
        if labeled_row_count(build) < 1:
            return REASON_ZERO_LABELED_ROWS
        return None
    audit = build.backtest_audit
    assert audit is not None
    if audit["reason"] != REASON_ACCEPTED:
        return audit["reason"]
    return None


def drop_build(build: MapBuild, reason: str) -> MapBuild:
    """Excluded map with empty dataset rows; keeps backtest_audit."""
    return replace(build, included=False, reason=reason, rows=(), market_rows=(), feature_rows=())


def apply_split_eligibility(
    builds: Sequence[MapBuild], event_starts: Mapping[str, int]
) -> list[MapBuild]:
    """Keep train maps with a label and validation maps that pass backtest gates."""
    updated: list[MapBuild] = []
    for build in builds:
        if not build.included or build.start_time is None:
            updated.append(build)
            continue
        split = split_for_event(event_starts[str(build.link["event_id"])])
        reason = eligibility_drop_reason(build, split)
        if reason is None:
            updated.append(build)
            continue
        updated.append(drop_build(build, reason))
    return updated


def audit_split_label(build: MapBuild, event_starts: Mapping[str, int]) -> LolPrepareAuditSplit:
    """train/validation for accepted maps; none otherwise."""
    if not build.included or build.start_time is None:
        return "none"
    event_id = str(build.link["event_id"])
    event_start = event_starts.get(event_id)
    if event_start is None:
        return "none"
    return split_for_event(event_start)


def build_split_rows(
    builds: Sequence[MapBuild], event_starts: Mapping[str, int]
) -> list[LolPrepareSplitRow]:
    """One split row per accepted map."""
    rows: list[LolPrepareSplitRow] = []
    for build in builds:
        if not build.included or build.start_time is None:
            continue
        event_id = str(build.link["event_id"])
        event_start = event_starts[event_id]
        split = split_for_event(event_start)
        rows.append(
            {
                "match_id": build.match_id,
                "event_id": event_id,
                "esports_game_id": str(build.link["esports_game_id"]),
                "game_number": int(build.link["game_number"]),
                "start_time": build.start_time,
                "event_start_time": event_start,
                "split": split,
            }
        )
    return rows


def build_audit_rows(
    builds: Sequence[MapBuild], event_starts: Mapping[str, int]
) -> list[LolPrepareAuditRow]:
    """One audit row per linked map."""
    rows: list[LolPrepareAuditRow] = []
    for build in builds:
        rows.append(
            {
                "event_id": str(build.link["event_id"]),
                "esports_game_id": str(build.link["esports_game_id"]),
                "game_number": int(build.link["game_number"]),
                "match_id": build.match_id,
                "start_time": build.start_time,
                "split": audit_split_label(build, event_starts),
                "included": build.included,
                "reason": build.reason,
                "invariant_rule": build.invariant_rule,
                "pause_count": build.pause_count,
                "pause_seconds": build.pause_seconds,
                "frame_count": build.frame_count,
                "grid_seconds": build.grid_seconds,
                "dataset_rows": len(build.rows),
                "skipped_age_rows": build.skipped_age_rows,
                "skipped_market_rows": build.skipped_market_rows,
                "skipped_invariant_rows": build.skipped_invariant_rows,
                "skipped_stamp_rows": build.skipped_stamp_rows,
            }
        )
    return rows


@dataclass(frozen=True)
class PartitionedRows:
    """Accepted dataset rows split into train, validation, and production."""

    train: list[LolDatasetRow]
    validation: list[LolDatasetRow]
    production: list[LolDatasetRow]


def partition_rows(builds: Sequence[MapBuild], event_starts: Mapping[str, int]) -> PartitionedRows:
    """Split accepted rows into train, validation, and production (all)."""
    train: list[LolDatasetRow] = []
    valid: list[LolDatasetRow] = []
    production: list[LolDatasetRow] = []
    for build in builds:
        if not build.included or build.start_time is None:
            continue
        production.extend(build.rows)
        if split_for_event(event_starts[str(build.link["event_id"])]) == "train":
            train.extend(build.rows)
        else:
            valid.extend(build.rows)
    return PartitionedRows(train, valid, production)


@dataclass(frozen=True)
class ValidationReplay:
    """Market seconds and eligibility rows for accepted validation maps."""

    market_rows: tuple[LolBacktestMarketSecondRow, ...]
    audit_rows: tuple[LolBacktestAuditRow, ...]


def collect_validation_replay(
    builds: Sequence[MapBuild], event_starts: Mapping[str, int]
) -> ValidationReplay:
    """Keep replay artifacts only for accepted maps in the validation event split."""
    market_rows: list[LolBacktestMarketSecondRow] = []
    audit_rows: list[LolBacktestAuditRow] = []
    for build in builds:
        if not build.included or build.start_time is None or build.backtest_audit is None:
            continue
        if split_for_event(event_starts[str(build.link["event_id"])]) != "validation":
            continue
        market_rows.extend(build.market_rows)
        audit_rows.append(build.backtest_audit)
    return ValidationReplay(tuple(market_rows), tuple(audit_rows))


def collect_game_feature_rows(builds: Sequence[MapBuild]) -> tuple[DotaGameFeatureRow, ...]:
    """Exact-second model inputs for every accepted map; no market join needed."""
    rows: list[DotaGameFeatureRow] = []
    for build in builds:
        if not build.included or build.start_time is None:
            continue
        rows.extend(build.feature_rows)
    return tuple(rows)


def print_prepare_progress(done: int, total: int, finished: Sequence[MapBuild]) -> None:
    """Print running map counts so a long prepare is observable."""
    accepted = sum(1 for build in finished if build.included)
    print(f"prepared: {done}/{total} accepted={accepted}", flush=True)


def build_maps_parallel(
    links: Sequence[LolLinkRow],
    windows_dir: Path,
    details_dir: Path,
    item_catalog_dir: Path,
    telonex_root: Path,
    cache_dir: Path,
    workers: int,
) -> MapBuildBatch:
    """Build one MapBuild per link on a process pool; preserve input order."""
    slots: list[MapBuild | None] = [None] * len(links)
    total = len(links)
    if total == 0:
        return MapBuildBatch((), 0)
    done = 0
    cache_hits = 0
    workers = min(total, workers)
    # Import by package name so spawn pickle works when this file is __main__.
    worker = import_module("lol.05_prepare_dataset").build_one_map_cached
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"))
    try:
        futures = {
            pool.submit(
                worker,
                link,
                windows_dir,
                details_dir,
                item_catalog_dir,
                telonex_root,
                cache_dir,
            ): index
            for index, link in enumerate(links)
        }
        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            slots[index] = result.build
            if result.hit:
                cache_hits += 1
            done += 1
            if done == total or done % PREPARE_PROGRESS_EVERY == 0:
                finished = [build for build in slots if build is not None]
                print_prepare_progress(done, total, finished)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return MapBuildBatch(tuple(cast(list[MapBuild], slots)), cache_hits)


def publish_datasets(
    output_dir: Path,
    train: Sequence[LolDatasetRow],
    valid: Sequence[LolDatasetRow],
    production: Sequence[LolDatasetRow],
    split_rows: Sequence[LolPrepareSplitRow],
    audit_rows: Sequence[LolPrepareAuditRow],
    replay: ValidationReplay,
    game_features: Sequence[DotaGameFeatureRow],
    columns: list[str],
) -> None:
    """Replace dataset parquets plus validation market seconds and backtest audit."""
    output_dir.mkdir(parents=True, exist_ok=True)
    write_parquet_rows(
        cast(Sequence[Mapping[str, object]], train),
        output_dir / "training.parquet",
        columns,
        DATASET_INTEGER_COLUMNS,
        ["match_id", "second"],
    )
    write_parquet_rows(
        cast(Sequence[Mapping[str, object]], valid),
        output_dir / "validation.parquet",
        columns,
        DATASET_INTEGER_COLUMNS,
        ["match_id", "second"],
    )
    write_parquet_rows(
        cast(Sequence[Mapping[str, object]], production),
        output_dir / "production_training.parquet",
        columns,
        DATASET_INTEGER_COLUMNS,
        ["match_id", "second"],
    )
    write_parquet_rows(
        cast(Sequence[Mapping[str, object]], split_rows),
        output_dir / "split.parquet",
        SPLIT_COLUMNS,
        SPLIT_INTEGER_COLUMNS,
        ["event_id", "game_number", "esports_game_id"],
    )
    write_parquet_rows(
        cast(Sequence[Mapping[str, object]], audit_rows),
        output_dir / "audit.parquet",
        AUDIT_COLUMNS,
        AUDIT_INTEGER_COLUMNS,
        ["event_id", "game_number", "esports_game_id"],
    )
    write_parquet_rows(
        cast(Sequence[Mapping[str, object]], replay.market_rows),
        output_dir / "market_seconds.parquet",
        BACKTEST_MARKET_COLUMNS,
        BACKTEST_MARKET_INTEGER_COLUMNS,
        ["match_id", "second"],
    )
    write_parquet_rows(
        cast(Sequence[Mapping[str, object]], replay.audit_rows),
        output_dir / "backtest_audit.parquet",
        BACKTEST_AUDIT_COLUMNS,
        BACKTEST_AUDIT_INTEGER_COLUMNS,
        ["event_id", "match_id"],
    )
    write_parquet_rows(
        cast(Sequence[Mapping[str, object]], game_features),
        output_dir / "game_features.parquet",
        GAME_FEATURE_COLUMNS,
        GAME_FEATURE_INTEGER_COLUMNS,
        ["match_id", "game_second"],
    )


def print_prepare_totals(
    builds: Sequence[MapBuild], split_rows: Sequence[LolPrepareSplitRow]
) -> None:
    """Print per-reason map counts plus train/validation accepted maps."""
    for reason, count in Counter(build.reason for build in builds).items():
        print_count(reason, count)
    print_count("train", sum(1 for row in split_rows if row["split"] == "train"))
    print_count("validation", sum(1 for row in split_rows if row["split"] == "validation"))


def prepare_dataset(
    links_path: Path,
    windows_dir: Path,
    details_dir: Path,
    item_catalog_dir: Path,
    telonex_root: Path,
    output_dir: Path,
    workers: int,
    cache_dir: Path,
) -> None:
    """Build training/validation/production datasets from local livestats and books."""
    links = cast(list[LolLinkRow], read_parquet_rows(links_path))
    batch = build_maps_parallel(
        links,
        windows_dir,
        details_dir,
        item_catalog_dir,
        telonex_root,
        cache_dir,
        workers,
    )
    builds = batch.builds
    event_starts = event_start_times(builds)
    builds = apply_split_eligibility(builds, event_starts)
    partitioned = partition_rows(builds, event_starts)
    split_rows = build_split_rows(builds, event_starts)
    audit_rows = build_audit_rows(builds, event_starts)
    replay = collect_validation_replay(builds, event_starts)
    feature_rows = collect_game_feature_rows(builds)
    publish_datasets(
        output_dir,
        partitioned.train,
        partitioned.validation,
        partitioned.production,
        split_rows,
        audit_rows,
        replay,
        feature_rows,
        LOL_DATASET_COLUMNS,
    )
    print_prepare_totals(builds, split_rows)
    print_count("map_build_cache_hit", batch.cache_hits)
    print_count("map_build_cache_miss", len(batch.builds) - batch.cache_hits)


def main(
    workers: Annotated[int, typer.Option("--workers")] = LOL_PREPARE_WORKERS,
) -> None:
    """Publish canonical LoL datasets."""
    prepare_dataset(
        LOL_LINKS_PATH,
        LOL_WINDOWS_DIR,
        LOL_DETAILS_DIR,
        DEFAULT_ITEM_CATALOG_DIR,
        LOL_RAW_TELONEX_DIR,
        LOL_DATASETS_DIR,
        workers,
        LOL_MAP_BUILD_CACHE_DIR,
    )


if __name__ == "__main__":
    typer.run(main)
