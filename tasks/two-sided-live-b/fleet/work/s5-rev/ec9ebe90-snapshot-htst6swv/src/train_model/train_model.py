# pyright: reportMissingTypeStubs=false

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from shutil import copy2
from typing import cast

import numpy as np
import pandas as pd

from shared.constants.dataset import (
    MODEL_START_SECOND,
    TRAIN_LAG_SECONDS,
)
from shared.constants.paths import (
    GAME_DEATHS_PATH,
    GAME_FEATURES_DATASET_PATH,
    GAME_HISTORY_MINUTES_PATH,
    PRODUCTION_ARCHIVE_DIR,
    PRODUCTION_DATASET_SPLIT_PATH,
    PRODUCTION_MODEL_DIR,
    PRODUCTION_NOXP_ARCHIVE_DIR,
    PRODUCTION_NOXP_MODEL_DIR,
    PRODUCTION_TRAINING_DATASET_PATH,
    RESEARCH_ARCHIVE_DIR,
    RESEARCH_MODEL_DIR,
    RESEARCH_MODEL_SPLIT_PATH,
    RESEARCH_NOXP_ARCHIVE_DIR,
    RESEARCH_NOXP_MODEL_DIR,
    TRAINING_DATASET_PATH,
    VALIDATION_DATASET_PATH,
)
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.types.dataset import ValidationDatasetRow
from shared.utils.dota_features import (
    DOTA_NOXP_FEATURE_COLUMNS,
    DOTA_XP_FEATURE_COLUMNS,
    attach_catalog_features,
    catalog_has_board,
    history_policy_for_columns,
)
from shared.utils.gbm import (
    ENSEMBLE_ARM,
    ENSEMBLE_K,
    LGB_PARAMS,
    MAX_BOOST_ROUNDS,
    TRAINING_COLUMNS,
    GbmPredictor,
    HoldoutWindowStats,
    build_ensemble_model_meta,
    build_model_metrics,
    build_price_delta_labels,
    fit_production_members,
    fit_research_members,
    load_training_dataset,
    predict_future_prices,
    write_ensemble_members,
)
from shared.utils.hashing import sha256_file
from shared.utils.log import get_logger, setup_logging
from shared.utils.market_scenario_report import (
    build_scenario_stats,
    full_window_scenario_stats,
    write_scenario_csv,
)
from shared.utils.model_registry import (
    publish_model_dir,
    staging_model_dir,
    write_model_meta,
)
from shared.utils.parquet_io import replace_nulls_with_none
from train_model.market_metrics import evaluate_validation_metrics

# LGB_PARAMS and TRAINING_COLUMNS are re-exported for tests / trader.
__all__ = ["LGB_PARAMS", "TRAINING_COLUMNS"]

logger = get_logger(__name__)


def lagged_source_features(
    frame: pd.DataFrame, lag_seconds: int, features: Sequence[str]
) -> pd.DataFrame:
    """Copy `features` with second set to the snapshot clock (T - lag)."""
    return frame[list(features)].assign(second=frame["second"] - lag_seconds)


# Parquet contracts stay tied to the TypedDicts so new fields cannot drift out of
# the loaders. LightGBM still receives only the DOTA catalog feature lists below.
VALIDATION_COLUMNS: list[str] = list(ValidationDatasetRow.__annotations__)


def load_validation_dataset(path: Path) -> pd.DataFrame:
    """Read exact-second validation rows for early stopping and metrics."""
    return pd.read_parquet(path, columns=VALIDATION_COLUMNS)


def build_validation_dataset_rows(frame: pd.DataFrame) -> list[ValidationDatasetRow]:
    """Convert validation rows to typed records while mapping pandas nulls to None."""
    records = replace_nulls_with_none(frame).to_dict(orient="records")
    return [cast(ValidationDatasetRow, record) for record in records]


def select_usable_validation_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep full-map rows that can feed canonical model inference."""
    in_window = frame["second"] >= MODEL_START_SECOND
    return frame.loc[in_window & (frame["market_status"] == "ok")]


def select_validation_prediction_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep usable rows where we BUY: model metrics stop at the BUY cutoff."""
    usable = select_usable_validation_rows(frame)
    return usable.loc[usable["second"] < BUY_CUTOFF_SECOND]


def select_validation_fit_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep usable rows through map end with a 300-second future midpoint."""
    usable = select_usable_validation_rows(frame)
    return usable.loc[usable["signal_market_p_radiant_300s"].notna()]


def slice_train_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep canonical Dota minute rows with a usable 300-second label."""
    in_window = frame["second"] >= MODEL_START_SECOND
    return frame.loc[in_window & frame["signal_market_p_radiant_300s"].notna()]


def log_dataset_span(label: str, match_times: pd.DataFrame) -> None:
    """Log how many matches a dataset holds and the first/last match start."""
    logger.info(
        "%s: %s matches | %s .. %s",
        label,
        len(match_times),
        pd.to_datetime(float(match_times["start_time"].min()), unit="s", utc=True),
        pd.to_datetime(float(match_times["start_time"].max()), unit="s", utc=True),
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Empty argv publishes both live catalogs; the four experiment flags must travel together."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lag-seconds", type=int, default=None)
    parser.add_argument("--training-dataset", type=Path, default=None)
    parser.add_argument("--validation-dataset", type=Path, default=None)
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--no-xp", action="store_true")
    args = parser.parse_args(argv)
    flags = (args.lag_seconds, args.training_dataset, args.validation_dataset, args.model_dir)
    if any(value is not None for value in flags) and any(value is None for value in flags):
        parser.error(
            "--lag-seconds, --training-dataset, --validation-dataset, and --model-dir "
            "must be passed together"
        )
    if args.no_xp and args.model_dir is None:
        parser.error("--no-xp requires --model-dir")
    if args.lag_seconds is not None and args.lag_seconds < 0:
        parser.error("--lag-seconds must be >= 0")
    if args.model_dir is not None:
        _validate_experiment_args(parser, args)
    return args


def _validate_experiment_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Path resolution and live-catalog guard for the --model-dir experiment path."""
    model_dir = args.model_dir.expanduser().resolve()
    blocked = {
        RESEARCH_MODEL_DIR.resolve(),
        PRODUCTION_MODEL_DIR.resolve(),
        RESEARCH_NOXP_MODEL_DIR.resolve(),
        PRODUCTION_NOXP_MODEL_DIR.resolve(),
    }
    if model_dir in blocked:
        parser.error("--model-dir cannot be a live research or production catalog")
    args.model_dir = model_dir
    training_path = args.training_dataset.expanduser()
    validation_path = args.validation_dataset.expanduser()
    if not training_path.is_file():
        parser.error(f"--training-dataset not found: {training_path}")
    if not validation_path.is_file():
        parser.error(f"--validation-dataset not found: {validation_path}")
    args.training_dataset = training_path.resolve()
    args.validation_dataset = validation_path.resolve()


def train_research(
    training_path: Path,
    validation_path: Path,
    split_path: Path,
    research_dir: Path,
    archive_dir: Path,
    lag_seconds: int,
    features: Sequence[str],
    *,
    minute_tape_path: Path,
    second_tape_path: Path,
    death_tape_path: Path,
) -> int:
    """Fit and publish the research default_sub90 ensemble; return mean member trees."""
    train_dataset_sha256 = sha256_file(training_path)
    validation_dataset_sha256 = sha256_file(validation_path)
    train_dataset = slice_train_rows(load_training_dataset(training_path))
    start_second = history_policy_for_columns(features).start_second
    board = catalog_has_board(features)
    death_tape = pd.read_parquet(death_tape_path) if board else None
    train_dataset = attach_catalog_features(
        train_dataset,
        pd.read_parquet(minute_tape_path),
        key_seconds=train_dataset["second"],
        start_second=start_second,
        board_tape=death_tape,
    )
    validation_dataset = load_validation_dataset(validation_path)
    second_tape = pd.read_parquet(second_tape_path)
    validation_dataset = attach_catalog_features(
        validation_dataset,
        second_tape,
        key_seconds=validation_dataset["second"] - lag_seconds,
        start_second=start_second,
        board_tape=second_tape if board else None,
    )
    validation_fit = select_validation_fit_frame(validation_dataset)
    validation_prediction = select_validation_prediction_frame(validation_dataset)
    validation_features = lagged_source_features(validation_fit, lag_seconds, features)
    validation_target = build_price_delta_labels(validation_fit)
    boosters = fit_research_members(train_dataset, validation_features, validation_target, features)

    member_trees = tuple(booster.num_trees() for booster in boosters)
    staging_dir = staging_model_dir(research_dir)
    member_names = write_ensemble_members(staging_dir, boosters)
    predictor = GbmPredictor(
        model_dir=staging_dir,
        feature_names=tuple(features),
        member_names=member_names,
        member_trees=member_trees,
        _boosters=tuple(boosters),
    )

    prediction_features = lagged_source_features(validation_prediction, lag_seconds, features)
    current_prices = validation_prediction["market_p_radiant"].to_numpy(dtype=np.float64)
    future_prices = predict_future_prices(predictor, prediction_features, current_prices)
    validation_rows = build_validation_dataset_rows(validation_prediction)
    metric_rows = evaluate_validation_metrics(validation_rows, future_prices.tolist())
    scenario_stats = build_scenario_stats(metric_rows)

    train_matches = train_dataset[["match_id", "start_time"]].drop_duplicates("match_id")
    validation_matches = validation_prediction[["match_id", "start_time"]].drop_duplicates(
        "match_id"
    )
    logger.info(
        "ensemble %s k=%s trees: %s-%s mean=%s (of %s) | train matches: %s | "
        "validation matches: %s | train rows: %s | validation rows: %s",
        ENSEMBLE_ARM,
        ENSEMBLE_K,
        min(member_trees),
        max(member_trees),
        predictor.num_trees(),
        MAX_BOOST_ROUNDS,
        len(train_matches),
        len(validation_matches),
        len(train_dataset),
        len(validation_prediction),
    )
    log_dataset_span("train", train_matches)
    log_dataset_span("validation", validation_matches)

    full_window = full_window_scenario_stats(scenario_stats)
    metrics = build_model_metrics(
        predictor,
        HoldoutWindowStats(
            rows=full_window.rows,
            future_price_300_count=full_window.future_price_300_count,
            no_move_mae_300=full_window.no_move_mae_300,
            model_mae_300=full_window.model_mae_300,
            mae_gain_300=full_window.mae_gain_300,
            mae_gain_300_ci=full_window.mae_gain_300_ci,
            model_bias_300=full_window.model_bias_300,
            directional_markout_300s=full_window.directional_markout_300s,
        ),
    )
    write_model_meta(
        build_ensemble_model_meta(
            len(train_matches),
            train_dataset_sha256,
            features=features,
            validation_dataset_sha256=validation_dataset_sha256,
            metrics=metrics,
            members=member_names,
            member_trees=member_trees,
            source_lag_seconds=lag_seconds,
        ),
        staging_dir / "model.json",
    )
    write_scenario_csv(scenario_stats, staging_dir / "validation_scenarios.csv")
    copy2(split_path, staging_dir / "split.parquet")
    archived_name = publish_model_dir(staging_dir, research_dir, archive_dir)
    logger.info("published: %s | archived previous model: %s", research_dir, archived_name)
    return predictor.num_trees()


def train_production(
    training_dataset_path: Path,
    split_source_path: Path,
    live_dir: Path,
    archive_dir: Path,
    features: Sequence[str],
    *,
    minute_tape_path: Path,
    death_tape_path: Path,
    boost_rounds: int,
) -> None:
    """Fit K production members at `boost_rounds` trees and publish the catalog."""
    train_dataset_sha256 = sha256_file(training_dataset_path)
    train_dataset = load_training_dataset(training_dataset_path)
    board = catalog_has_board(features)
    train_dataset = attach_catalog_features(
        train_dataset,
        pd.read_parquet(minute_tape_path),
        key_seconds=train_dataset["second"],
        start_second=history_policy_for_columns(features).start_second,
        board_tape=pd.read_parquet(death_tape_path) if board else None,
    )
    boosters = fit_production_members(train_dataset, boost_rounds, features)
    member_trees = tuple(booster.num_trees() for booster in boosters)
    train_match_times = train_dataset[["match_id", "start_time"]].drop_duplicates("match_id")
    logger.info(
        "production train: publishing %s | no holdout metrics, use the research model for quality",
        live_dir,
    )
    logger.info(
        "ensemble %s k=%s trees: %s-%s mean=%s | train matches: %s | train rows: %s",
        ENSEMBLE_ARM,
        ENSEMBLE_K,
        min(member_trees),
        max(member_trees),
        round(sum(member_trees) / len(member_trees)),
        len(train_match_times),
        len(train_dataset),
    )
    log_dataset_span("train", train_match_times)

    staging_dir = staging_model_dir(live_dir)
    member_names = write_ensemble_members(staging_dir, boosters)
    write_model_meta(
        build_ensemble_model_meta(
            len(train_match_times),
            train_dataset_sha256,
            features=features,
            validation_dataset_sha256=None,
            metrics=None,
            members=member_names,
            member_trees=member_trees,
            source_lag_seconds=TRAIN_LAG_SECONDS,
        ),
        staging_dir / "model.json",
    )
    copy2(split_source_path, staging_dir / "split.parquet")
    archived_name = publish_model_dir(staging_dir, live_dir, archive_dir)
    logger.info("published: %s | archived previous model: %s", live_dir, archived_name)


@dataclass(frozen=True)
class CatalogPair:
    """The research and production catalogs of one feature set."""

    features: tuple[str, ...]
    research_dir: Path
    research_archive_dir: Path
    production_dir: Path
    production_archive_dir: Path


LIVE_CATALOG_PAIRS = (
    CatalogPair(
        tuple(DOTA_XP_FEATURE_COLUMNS),
        RESEARCH_MODEL_DIR,
        RESEARCH_ARCHIVE_DIR,
        PRODUCTION_MODEL_DIR,
        PRODUCTION_ARCHIVE_DIR,
    ),
    CatalogPair(
        tuple(DOTA_NOXP_FEATURE_COLUMNS),
        RESEARCH_NOXP_MODEL_DIR,
        RESEARCH_NOXP_ARCHIVE_DIR,
        PRODUCTION_NOXP_MODEL_DIR,
        PRODUCTION_NOXP_ARCHIVE_DIR,
    ),
)


def main(argv: Sequence[str] = ()) -> None:
    """Publish live research then production for each catalog pair, or one experiment catalog."""
    args = parse_args(argv)
    setup_logging()
    if args.model_dir is not None:
        features = DOTA_NOXP_FEATURE_COLUMNS if args.no_xp else DOTA_XP_FEATURE_COLUMNS
        train_research(
            args.training_dataset,
            args.validation_dataset,
            RESEARCH_MODEL_SPLIT_PATH,
            args.model_dir,
            args.model_dir.parent / "archive",
            args.lag_seconds,
            features,
            minute_tape_path=args.training_dataset.with_name(GAME_HISTORY_MINUTES_PATH.name),
            second_tape_path=args.validation_dataset.with_name(GAME_FEATURES_DATASET_PATH.name),
            death_tape_path=args.training_dataset.with_name(GAME_DEATHS_PATH.name),
        )
        return
    for pair in LIVE_CATALOG_PAIRS:
        boost_rounds = train_research(
            TRAINING_DATASET_PATH,
            VALIDATION_DATASET_PATH,
            RESEARCH_MODEL_SPLIT_PATH,
            pair.research_dir,
            pair.research_archive_dir,
            TRAIN_LAG_SECONDS,
            pair.features,
            minute_tape_path=GAME_HISTORY_MINUTES_PATH,
            second_tape_path=GAME_FEATURES_DATASET_PATH,
            death_tape_path=GAME_DEATHS_PATH,
        )
        train_production(
            PRODUCTION_TRAINING_DATASET_PATH,
            PRODUCTION_DATASET_SPLIT_PATH,
            pair.production_dir,
            pair.production_archive_dir,
            pair.features,
            minute_tape_path=GAME_HISTORY_MINUTES_PATH,
            death_tape_path=GAME_DEATHS_PATH,
            boost_rounds=boost_rounds,
        )


if __name__ == "__main__":
    main(sys.argv[1:])
