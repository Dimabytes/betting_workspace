import argparse
import sys
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import pandas as pd

from market_data.build_market_data import market_seconds_cache_path
from prepare_dataset.stratz_seconds import (
    ExactSecondState,
    MatchDataError,
    build_exact_second_states,
    build_minute_states,
)
from shared.constants.dataset import (
    MAX_TRAIN_HARD_MISSES,
    MAX_VALIDATION_HARD_MISSES,
    MODEL_START_SECOND,
    MODEL_TARGET_HORIZON_SECONDS,
    TRAIN_LAG_SECONDS,
    VALIDATION_START_TIME,
)
from shared.constants.paths import (
    GAME_DEATHS_PATH,
    GAME_FEATURES_DATASET_PATH,
    GAME_HISTORY_MINUTES_PATH,
    MATCH_CATALOG_PATH,
    PRODUCTION_DATASET_SPLIT_PATH,
    PRODUCTION_TRAINING_DATASET_PATH,
    RESEARCH_MODEL_SPLIT_PATH,
    TRAINING_DATASET_PATH,
    VALIDATION_DATASET_PATH,
)
from shared.types.dataset import (
    DatasetRow,
    DotaGameFeatureRow,
    GameDeathRow,
    GameHistoryMinuteRow,
    MarketSecondRow,
    SplitRow,
    ValidationDatasetRow,
)
from shared.types.stratz import UsableStratzMatch
from shared.utils.dota_features import snapshot_features
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_catalog import CatalogEntry, MatchCatalog, load_match_catalog
from shared.utils.parquet_io import replace_nulls_with_none, write_parquet
from shared.utils.stratz import death_times, get_stratz_match_reach_data
from shared.utils.telonex_book import is_ok_market_second

PROGRESS_EVERY = 500

assert MODEL_TARGET_HORIZON_SECONDS == 300

logger = get_logger(__name__)

CacheSplit = Literal["train", "validation"]


@dataclass(frozen=True)
class MatchIdSplit:
    train: tuple[int, ...]
    validation: tuple[int, ...]


@dataclass(frozen=True)
class MatchInputs:
    match: UsableStratzMatch
    market_rows: list[MarketSecondRow]
    entry: CatalogEntry


def select_matches(catalog: MatchCatalog, cutoff: int) -> MatchIdSplit:
    entries = list(catalog.values())
    train_ids = exclude_missing_market_caches(
        tuple(entry.match_id for entry in entries if entry.start_time < cutoff),
        "train",
    )

    validation_entries = [entry for entry in entries if entry.start_time >= cutoff]
    missing_playback = [
        entry.match_id for entry in validation_entries if not entry.playback_available
    ]
    if missing_playback:
        ids = ", ".join(str(match_id) for match_id in missing_playback)
        raise SystemExit(f"validation candidates missing playback: {ids}")
    logger.info("validation candidates: %s", len(validation_entries))
    validation_ids = exclude_missing_market_caches(
        tuple(entry.match_id for entry in validation_entries),
        "validation",
    )
    return MatchIdSplit(train=train_ids, validation=validation_ids)


def exclude_missing_market_caches(match_ids: Sequence[int], split: CacheSplit) -> tuple[int, ...]:
    missing = [
        match_id for match_id in match_ids if not market_seconds_cache_path(match_id).exists()
    ]
    cap = MAX_TRAIN_HARD_MISSES if split == "train" else MAX_VALIDATION_HARD_MISSES
    report = f"prepare missing {split} market caches ({len(missing)}): {missing}"
    if len(missing) > cap:
        logger.error(report)
        raise SystemExit(report)
    if missing:
        logger.warning(report)
    skipped = set(missing)
    return tuple(match_id for match_id in match_ids if match_id not in skipped)


def match_minute_states(inputs: MatchInputs) -> tuple[ExactSecondState, ...]:
    """Minute-boundary states from -60 through the match's own last minute."""
    match = inputs.match
    try:
        return build_minute_states(match, match["durationSeconds"] + 1)
    except MatchDataError as error:
        logger.warning("skip match %s: %s", match["id"], error.reason)
        return ()


def build_minute_rows(
    inputs: MatchInputs,
    states: tuple[ExactSecondState, ...],
    lag_seconds: int,
) -> list[DatasetRow]:
    market_by_second = {int(row["second"]): row for row in inputs.market_rows}
    rows: list[DatasetRow] = []
    for state in states:
        market_row = market_by_second.get(state.second + lag_seconds)
        if market_row is None or not is_ok_market_second(market_row):
            continue
        signal_market_p_radiant_300s = market_row["signal_market_p_radiant_300s"]
        if signal_market_p_radiant_300s is None:
            continue
        rows.append(
            DatasetRow(
                match_id=state.match_id,
                start_time=inputs.entry.start_time,
                second=state.second,
                radiant_win=state.radiant_win,
                **snapshot_features(state),
                market_radiant_prior=inputs.entry.radiant_prior,
                market_p_radiant=market_row["market_p_radiant"],
                signal_market_p_radiant_300s=signal_market_p_radiant_300s,
            )
        )
    return rows


def build_game_history_minute_rows(
    match_id: int,
    states: Sequence[ExactSecondState],
) -> list[GameHistoryMinuteRow]:
    """Minute game levels through map end; market-free tape for trainer history."""
    return [
        GameHistoryMinuteRow(
            match_id=match_id,
            game_second=state.second,
            **snapshot_features(state),
        )
        for state in states
    ]


def build_game_death_rows(match_id: int, match: UsableStratzMatch) -> list[GameDeathRow]:
    """Death counts at the model start and each STRATZ death second."""
    radiant = death_times(match, radiant=True)
    dire = death_times(match, radiant=False)
    seconds = sorted({MODEL_START_SECOND, *radiant, *dire})
    return [
        GameDeathRow(
            match_id=match_id,
            game_second=second,
            deaths_radiant=bisect_right(radiant, second),
            deaths_dire=bisect_right(dire, second),
        )
        for second in seconds
    ]


def build_game_feature_rows(
    match_id: int,
    states: Sequence[ExactSecondState],
    market_radiant_prior: float,
) -> list[DotaGameFeatureRow]:
    """Exact-second model inputs keyed by game second for archive-schedule replay."""
    return [
        DotaGameFeatureRow(**row, market_radiant_prior=market_radiant_prior)
        for row in build_game_history_minute_rows(match_id, states)
    ]


def load_market_second_rows(path: Path) -> list[MarketSecondRow]:
    frame = pd.read_parquet(path)
    records = replace_nulls_with_none(frame).to_dict(orient="records")
    return [cast(MarketSecondRow, record) for record in records]


def join_validation_rows(
    states: tuple[ExactSecondState, ...],
    inputs: MatchInputs,
    lag_seconds: int,
) -> list[ValidationDatasetRow]:
    states_by_second = {state.second: state for state in states}
    rows: list[ValidationDatasetRow] = []
    for market_row in inputs.market_rows:
        market_match_id = int(market_row["match_id"])
        market_second = int(market_row["second"])
        lagged_second = market_second - lag_seconds
        if lagged_second < MODEL_START_SECOND:
            continue
        state = states_by_second.get(lagged_second)
        if state is None:
            raise ValueError(
                "validation join missing lagged state: "
                f"market=({market_match_id}, {market_second}) "
                f"lagged_second={lagged_second}"
            )
        rows.append(
            ValidationDatasetRow(
                **market_row,
                start_time=inputs.entry.start_time,
                radiant_win=state.radiant_win,
                **snapshot_features(state),
                market_radiant_prior=inputs.entry.radiant_prior,
            )
        )
    return rows


def write_rows(
    rows: (
        Sequence[DatasetRow]
        | Sequence[ValidationDatasetRow]
        | Sequence[DotaGameFeatureRow]
        | Sequence[GameHistoryMinuteRow]
        | Sequence[GameDeathRow]
        | Sequence[SplitRow]
    ),
    path: Path,
) -> None:
    write_parquet(pd.DataFrame(rows), path)
    logger.info("%s rows -> %s", len(rows), path)


def split_rows(
    rows: Sequence[DatasetRow] | Sequence[ValidationDatasetRow],
    split: Literal["train", "validation"],
) -> list[SplitRow]:
    match_times = {int(row["match_id"]): int(row["start_time"]) for row in rows}
    result = [
        SplitRow(
            match_id=match_id,
            start_time=start_time,
            split=split,
        )
        for match_id, start_time in match_times.items()
    ]
    result.sort(key=lambda row: (int(row["start_time"]), int(row["match_id"])))
    return result


def write_datasets(
    training_rows: list[DatasetRow],
    validation_minute_rows: list[DatasetRow],
    validation_rows: list[ValidationDatasetRow],
    game_feature_rows: list[DotaGameFeatureRow],
    history_minute_rows: list[GameHistoryMinuteRow],
    death_rows: list[GameDeathRow],
    output_dir: Path | None,
) -> None:
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_rows(training_rows, output_dir / "training_dataset.parquet")
        write_rows(validation_rows, output_dir / "validation_dataset.parquet")
        write_rows(game_feature_rows, output_dir / "game_features.parquet")
        write_rows(history_minute_rows, output_dir / "game_history_minutes.parquet")
        write_rows(death_rows, output_dir / "game_deaths.parquet")
        for name, rows in (("train", training_rows), ("validation", validation_rows)):
            match_count = len({int(row["match_id"]) for row in rows})
            logger.info("%s: %s matches | %s rows", name, match_count, len(rows))
        return

    production_rows = [*training_rows, *validation_minute_rows]
    research_split_rows = split_rows(training_rows, "train") + split_rows(
        validation_rows, "validation"
    )
    production_split_rows = split_rows(production_rows, "train")

    write_rows(training_rows, TRAINING_DATASET_PATH)
    write_rows(validation_rows, VALIDATION_DATASET_PATH)
    write_rows(game_feature_rows, GAME_FEATURES_DATASET_PATH)
    write_rows(history_minute_rows, GAME_HISTORY_MINUTES_PATH)
    write_rows(death_rows, GAME_DEATHS_PATH)
    write_rows(research_split_rows, RESEARCH_MODEL_SPLIT_PATH)
    write_rows(production_rows, PRODUCTION_TRAINING_DATASET_PATH)
    write_rows(production_split_rows, PRODUCTION_DATASET_SPLIT_PATH)
    for name, rows in (
        ("train", training_rows),
        ("validation", validation_rows),
        ("production", production_rows),
    ):
        match_count = len({int(row["match_id"]) for row in rows})
        logger.info("%s: %s matches | %s rows", name, match_count, len(rows))


def load_match_inputs(match_id: int, catalog: MatchCatalog) -> MatchInputs:
    return MatchInputs(
        match=get_stratz_match_reach_data(match_id),
        market_rows=load_market_second_rows(market_seconds_cache_path(match_id)),
        entry=catalog[match_id],
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lag-seconds", type=int, default=TRAIN_LAG_SECONDS)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.lag_seconds < 0:
        parser.error("--lag-seconds must be >= 0")
    if args.lag_seconds != TRAIN_LAG_SECONDS and args.output_dir is None:
        parser.error("--lag-seconds other than 10 requires --output-dir")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    setup_logging()
    catalog = load_match_catalog(MATCH_CATALOG_PATH)
    split = select_matches(catalog, VALIDATION_START_TIME)
    lag_seconds = args.lag_seconds
    output_dir = args.output_dir

    training_rows: list[DatasetRow] = []
    history_minute_rows: list[GameHistoryMinuteRow] = []
    death_rows: list[GameDeathRow] = []
    for i, match_id in enumerate(split.train, start=1):
        inputs = load_match_inputs(match_id, catalog)
        minute_states = match_minute_states(inputs)
        training_rows.extend(build_minute_rows(inputs, minute_states, lag_seconds))
        history_minute_rows.extend(build_game_history_minute_rows(match_id, minute_states))
        death_rows.extend(build_game_death_rows(match_id, inputs.match))
        if i % PROGRESS_EVERY == 0:
            logger.info("processed train %s / %s matches", i, len(split.train))

    validation_minute_rows: list[DatasetRow] = []
    validation_rows: list[ValidationDatasetRow] = []
    game_feature_rows: list[DotaGameFeatureRow] = []
    for i, match_id in enumerate(split.validation, start=1):
        inputs = load_match_inputs(match_id, catalog)
        minute_states = match_minute_states(inputs)
        if output_dir is None:
            validation_minute_rows.extend(build_minute_rows(inputs, minute_states, lag_seconds))
        history_minute_rows.extend(build_game_history_minute_rows(match_id, minute_states))
        death_rows.extend(build_game_death_rows(match_id, inputs.match))
        states = build_exact_second_states(inputs.match)
        validation_rows.extend(join_validation_rows(states, inputs, lag_seconds))
        game_feature_rows.extend(
            build_game_feature_rows(match_id, states, inputs.entry.radiant_prior)
        )
        if i % PROGRESS_EVERY == 0:
            logger.info("processed validation %s / %s matches", i, len(split.validation))

    write_datasets(
        training_rows,
        validation_minute_rows,
        validation_rows,
        game_feature_rows,
        history_minute_rows,
        death_rows,
        output_dir,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
