"""Pick the linked matches a backtest can replay and describe each one."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest.context import (
    MarketContext,
    calculate_availability_window,
    calculate_clock_end,
    calculate_replay_window,
)
from backtest.telonex_local import book_rows_in_window, has_local_telonex_days
from shared.constants.paths import (
    MATCH_CATALOG_PATH,
    RAW_TELONEX_POLYMARKET_DIR,
    RESEARCH_MODEL_SPLIT_PATH,
)
from shared.utils.match_catalog import CatalogEntry, MatchCatalog, load_match_catalog


@dataclass(frozen=True)
class MarketSources:
    """Every table the market selection reads, loaded once per run."""

    validation_match_ids: tuple[int, ...]
    catalog: MatchCatalog
    usable_signal_match_ids: frozenset[int]


@dataclass(frozen=True)
class ValidationCoverage:
    """Why each validation match is or is not replayable, counted over all of them."""

    validation_matches: int
    without_map_market: int
    without_signal_rows: int
    without_local_telonex: int
    archive_excluded: int
    eligible: int


def load_validation_match_ids() -> tuple[int, ...]:
    """Load chronological validation match ids from the current research split."""
    split = pd.read_parquet(RESEARCH_MODEL_SPLIT_PATH)
    validation = split[split["split"] == "validation"].sort_values(
        ["start_time", "match_id"], ignore_index=True
    )
    return tuple(int(match_id) for match_id in validation["match_id"])


def load_market_sources(signal_rows: pd.DataFrame) -> MarketSources:
    """Load the catalog and split the selection and market contexts read."""
    usable_signal_match_ids = frozenset(int(match_id) for match_id in signal_rows["match_id"])
    return MarketSources(
        validation_match_ids=load_validation_match_ids(),
        catalog=load_match_catalog(MATCH_CATALOG_PATH),
        usable_signal_match_ids=usable_signal_match_ids,
    )


def _has_replay_days(row: CatalogEntry) -> bool:
    """Local Telonex day files cover this catalog row's availability window."""
    availability = calculate_availability_window(
        map_load_at=row.anchor_at, game_ended_at=row.ended_at
    )
    return has_local_telonex_days(row.gamma.token_ids, availability, RAW_TELONEX_POLYMARKET_DIR)


def _dota_replayable(sources: MarketSources, match_id: int, row: CatalogEntry) -> bool:
    """The signal-rows and local-day gates for one catalog match."""
    return match_id in sources.usable_signal_match_ids and _has_replay_days(row)


def select_validation_matches(
    sources: MarketSources,
) -> tuple[ValidationCoverage, tuple[int, ...]]:
    """Chronological validation matches that can be replayed, plus why the rest cannot."""
    without_map_market = 0
    without_signal_rows = 0
    without_local_telonex = 0
    eligible: list[int] = []

    for match_id in sources.validation_match_ids:
        row = sources.catalog.get(match_id)
        if row is None:
            without_map_market += 1
            continue
        if match_id not in sources.usable_signal_match_ids:
            without_signal_rows += 1
            continue
        if not _has_replay_days(row):
            without_local_telonex += 1
            continue
        eligible.append(match_id)

    coverage = ValidationCoverage(
        validation_matches=len(sources.validation_match_ids),
        without_map_market=without_map_market,
        without_signal_rows=without_signal_rows,
        without_local_telonex=without_local_telonex,
        archive_excluded=0,
        eligible=len(eligible),
    )
    return coverage, tuple(eligible)


def build_book_weights(
    match_ids: Sequence[int], catalog: MatchCatalog, capture_root: Path
) -> dict[int, int]:
    """Book day-file rows per match: the shard weight approximating replay cost."""
    weights: dict[int, int] = {}
    for match_id in match_ids:
        row = catalog[match_id]
        window = calculate_replay_window(horn_at=row.horn_at, game_ended_at=row.ended_at)
        weights[match_id] = book_rows_in_window(row.gamma.token_ids, window, capture_root)
    return weights


def select_dota_since_match_ids(sources: MarketSources, since_match_id: int) -> tuple[int, ...]:
    """Replayable catalog matches timed at/after the anchor match's start_time.

    The cohort comes out in (start_time, match_id) order, same as the LoL
    selector. The anchor itself does not need to pass the replayability gates.
    """
    anchor = sources.catalog.get(since_match_id)
    if anchor is None:
        raise ValueError(f"--since-match {since_match_id} is not in the match catalog")
    cohort = [
        match_id
        for match_id, row in sources.catalog.items()
        if row.start_time >= anchor.start_time and _dota_replayable(sources, match_id, row)
    ]
    cohort.sort(key=lambda match_id: (sources.catalog[match_id].start_time, match_id))
    if not cohort:
        raise ValueError(f"no replayable matches at/after --since-match {since_match_id}")
    return tuple(cohort)


def build_market_context(
    sources: MarketSources,
    match_id: int,
) -> MarketContext:
    """Describe one match/market pair; horn, pauses and winner come from the catalog."""
    row = sources.catalog[match_id]
    gamma = row.gamma
    replay = calculate_replay_window(horn_at=row.horn_at, game_ended_at=row.ended_at)
    return MarketContext(
        match_id=match_id,
        condition_id=row.condition_id,
        event_id=row.event_id,
        market_slug=gamma.slug,
        token_ids=gamma.token_ids,
        radiant_token_index=row.radiant_token_index,
        radiant_win=row.radiant_win,
        seconds_delay=gamma.seconds_delay,
        horn_at=row.horn_at,
        game_ended_at=row.ended_at,
        market_closed_at=gamma.closed_at,
        replay_start=replay.start,
        replay_end=replay.end,
        clock_end=calculate_clock_end(game_ended_at=row.ended_at, market_closed_at=gamma.closed_at),
    )
