"""Train a LoL-only LightGBM research or production ensemble from Stage 05 parquets."""

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from shutil import copy2

import numpy as np
import numpy.typing as npt
import pandas as pd
import typer

from lol.constants import (
    LOL_GRID_START_SECOND,
    LOL_PRODUCTION_ARCHIVE_DIR,
    LOL_PRODUCTION_TRAINING_PATH,
    LOL_RESEARCH_ARCHIVE_DIR,
    LOL_TRAIN_END_SECOND,
    LOL_TRAINING_PATH,
)
from lol.lol_validation_metrics import (
    LolHoldoutPoint,
    build_holdout_aggregates,
    write_validation_metrics_csv,
)
from lol.types import LolModelMeta
from shared.constants.lol import (
    LOL_GAME_FEATURES_PATH,
    LOL_PRODUCTION_MODEL_DIR,
    LOL_RESEARCH_MODEL_DIR,
    LOL_SOURCE_LAG_SECONDS,
    LOL_SPLIT_PATH,
    LOL_VALIDATION_PATH,
)
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.types.model import ModelMetrics
from shared.utils.dota_features import (
    DOTA_XP_FEATURE_COLUMNS,
    GRID_HISTORY_POLICY,
    attach_catalog_features,
)
from shared.utils.gbm import (
    ENSEMBLE_ARM,
    ENSEMBLE_K,
    MAX_BOOST_ROUNDS,
    GbmPredictor,
    HoldoutWindowStats,
    build_ensemble_model_meta,
    build_model_metrics,
    build_price_delta_labels,
    fit_production_members,
    fit_research_members,
    predict_future_prices,
    write_ensemble_members,
)
from shared.utils.hashing import sha256_file
from shared.utils.json_io import write_json
from shared.utils.log import get_logger, setup_logging
from shared.utils.model_registry import publish_model_dir, staging_model_dir

logger = get_logger(__name__)

LOL_XP_SOURCE = "level"
LOL_STATE_SOURCE = "lolesports_window_details_grid_networth"


def load_event_ids(split_path: Path) -> dict[int, str]:
    """Map Stage 05 match_id to PM event_id from split.parquet."""
    frame = pd.read_parquet(split_path, columns=["match_id", "event_id"])
    mapping: dict[int, str] = {}
    for match_id, event_id in zip(frame["match_id"], frame["event_id"], strict=True):
        mapping[int(match_id)] = str(event_id)
    return mapping


def unique_match_count(frame: pd.DataFrame) -> int:
    """Count distinct maps in a DatasetRow parquet."""
    return int(frame["match_id"].nunique())


def slice_model_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep labeled LoL rows at every second from the grid start through 540."""
    labeled = frame["signal_market_p_radiant_300s"].notna()
    in_window = (frame["second"] >= LOL_GRID_START_SECOND) & (
        frame["second"] <= LOL_TRAIN_END_SECOND
    )
    return frame.loc[in_window & labeled]


def build_lol_model_meta(
    train_matches: int,
    train_dataset_sha256: str,
    *,
    features: Sequence[str],
    validation_dataset_sha256: str | None,
    validation_matches: int | None,
    metrics: ModelMetrics | None,
    members: Sequence[str],
    member_trees: Sequence[int],
) -> LolModelMeta:
    """Build LoL model.json; extra keys stay off the Dota ModelMeta contract."""
    return LolModelMeta(
        **build_ensemble_model_meta(
            train_matches,
            train_dataset_sha256,
            features=features,
            validation_dataset_sha256=validation_dataset_sha256,
            metrics=metrics,
            members=members,
            member_trees=member_trees,
            source_lag_seconds=LOL_SOURCE_LAG_SECONDS,
        ),
        xp_source=LOL_XP_SOURCE,
        state_source=LOL_STATE_SOURCE,
        validation_matches=validation_matches,
    )


def publish_lol_model_dir(staging_dir: Path, live_dir: Path, archive_dir: Path) -> str | None:
    """Rename staging into live; archive a predecessor only when model.json exists."""
    # ponytail: first live dir has no predecessor; do not change model_registry.
    if not (live_dir / "model.json").is_file():
        if live_dir.exists():
            shutil.rmtree(live_dir)
        live_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir.rename(live_dir)
        return None
    return publish_model_dir(staging_dir, live_dir, archive_dir)


def load_lol_dataset(path: Path, tape: pd.DataFrame) -> pd.DataFrame:
    """Read one labeled LoL parquet slice and enrich it from the game_features tape.

    `tape` is the Stage 05 per-second tape artifact; key seconds are the row's
    own game second and pivots start at the GRID start second. The tape is
    complete per-second, so pivots must hit the exact lag second. The scoreboard features
    use the same exact death-event tape.
    """
    rows = slice_model_rows(pd.read_parquet(path))
    return attach_catalog_features(
        rows,
        tape,
        key_seconds=rows["second"],
        start_second=GRID_HISTORY_POLICY.start_second,
        board_tape=tape,
    )


def holdout_points(
    validation: pd.DataFrame,
    model_prices: npt.NDArray[np.float64],
    event_ids: Mapping[int, str],
) -> list[LolHoldoutPoint]:
    """Pair each validation row with its restored model price and PM event_id."""
    points: list[LolHoldoutPoint] = []
    currents = validation["market_p_radiant"].to_numpy(dtype=np.float64)
    futures = validation["signal_market_p_radiant_300s"].to_numpy(dtype=np.float64)
    seconds = validation["second"].to_numpy(dtype=np.int64)
    match_ids = validation["match_id"].to_numpy(dtype=np.int64)
    for index in range(len(validation)):
        points.append(
            LolHoldoutPoint(
                event_id=event_ids[int(match_ids[index])],
                second=int(seconds[index]),
                current=float(currents[index]),
                future=float(futures[index]),
                model_p=float(model_prices[index]),
            )
        )
    return points


def train_research(
    training_path: Path,
    validation_path: Path,
    split_path: Path,
    tape: pd.DataFrame,
    research_dir: Path,
    research_archive_dir: Path,
) -> int:
    """Fit and publish the research default_sub90 ensemble; return mean member trees.

    Early stopping sees labeled validation rows through LOL_TRAIN_END_SECOND;
    holdout metrics stop at BUY_CUTOFF_SECOND — the split is intentional:
    tree count uses a bit past the window where we buy, reported quality
    stays inside it.
    """
    features = list(DOTA_XP_FEATURE_COLUMNS)
    train_dataset = load_lol_dataset(training_path, tape)
    validation_dataset = load_lol_dataset(validation_path, tape)
    event_ids = load_event_ids(split_path)
    valid_features = validation_dataset[features]
    boosters = fit_research_members(
        train_dataset,
        valid_features,
        build_price_delta_labels(validation_dataset),
        features,
    )
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
    metric_frame = validation_dataset.loc[validation_dataset["second"] < BUY_CUTOFF_SECOND]
    metric_features = metric_frame[features]
    current_prices = metric_frame["market_p_radiant"].to_numpy(dtype=np.float64)
    model_prices = predict_future_prices(predictor, metric_features, current_prices)
    points = holdout_points(metric_frame, model_prices, event_ids)
    aggregates = build_holdout_aggregates(points)
    full_window = aggregates[-1]
    train_matches = unique_match_count(train_dataset)
    validation_matches = unique_match_count(validation_dataset)
    logger.info(
        "ensemble %s k=%s trees: %s-%s mean=%s (of %s) | train matches: %s | "
        "validation matches: %s | train rows: %s | validation rows: %s",
        ENSEMBLE_ARM,
        ENSEMBLE_K,
        min(member_trees),
        max(member_trees),
        predictor.num_trees(),
        MAX_BOOST_ROUNDS,
        train_matches,
        validation_matches,
        len(train_dataset),
        len(validation_dataset),
    )

    write_json(
        staging_dir / "model.json",
        build_lol_model_meta(
            train_matches,
            sha256_file(training_path),
            features=features,
            validation_dataset_sha256=sha256_file(validation_path),
            validation_matches=validation_matches,
            metrics=build_model_metrics(
                predictor,
                HoldoutWindowStats(
                    rows=full_window.rows,
                    future_price_300_count=full_window.future_300_n,
                    no_move_mae_300=full_window.no_move_mae,
                    model_mae_300=full_window.model_mae,
                    mae_gain_300=full_window.mae_gain,
                    mae_gain_300_ci=full_window.mae_gain_ci,
                    model_bias_300=full_window.bias,
                    directional_markout_300s=full_window.dir_300,
                ),
            ),
            members=member_names,
            member_trees=member_trees,
        ),
    )
    copy2(split_path, staging_dir / "split.parquet")
    write_validation_metrics_csv(aggregates, staging_dir / "validation_metrics.csv")
    archived_name = publish_lol_model_dir(staging_dir, research_dir, research_archive_dir)
    logger.info(
        "published: %s | archived previous model: %s | note: early stopping ran on %d "
        "labeled rows through second %d, reported metrics on second < %d",
        research_dir,
        archived_name,
        len(validation_dataset),
        LOL_TRAIN_END_SECOND,
        BUY_CUTOFF_SECOND,
    )
    return predictor.num_trees()


def train_production_model(
    production_training_path: Path,
    split_path: Path,
    tape: pd.DataFrame,
    production_dir: Path,
    production_archive_dir: Path,
    boost_rounds: int,
) -> None:
    """Fit K production members at the research tree count and publish the catalog."""
    train_dataset = load_lol_dataset(production_training_path, tape)
    boosters = fit_production_members(train_dataset, boost_rounds, DOTA_XP_FEATURE_COLUMNS)
    member_trees = tuple(booster.num_trees() for booster in boosters)
    train_matches = unique_match_count(train_dataset)
    logger.info(
        "production train: publishing %s | ensemble %s k=%s trees: %s-%s mean=%s | "
        "train matches: %s | train rows: %s",
        production_dir,
        ENSEMBLE_ARM,
        ENSEMBLE_K,
        min(member_trees),
        max(member_trees),
        round(sum(member_trees) / len(member_trees)),
        train_matches,
        len(train_dataset),
    )
    staging_dir = staging_model_dir(production_dir)
    member_names = write_ensemble_members(staging_dir, boosters)
    write_json(
        staging_dir / "model.json",
        build_lol_model_meta(
            train_matches,
            sha256_file(production_training_path),
            features=DOTA_XP_FEATURE_COLUMNS,
            validation_dataset_sha256=None,
            validation_matches=None,
            metrics=None,
            members=member_names,
            member_trees=member_trees,
        ),
    )
    copy2(split_path, staging_dir / "split.parquet")
    archived_name = publish_lol_model_dir(staging_dir, production_dir, production_archive_dir)
    logger.info("published: %s | archived previous model: %s", production_dir, archived_name)


def main(model_dir: Path | None = None) -> None:
    """Publish the canonical scoreboard model, or train it into an experiment directory."""
    setup_logging()
    tape = pd.read_parquet(LOL_GAME_FEATURES_PATH)
    if model_dir is not None:
        target = model_dir.expanduser().resolve()
        blocked = {
            LOL_RESEARCH_MODEL_DIR.resolve(),
            LOL_PRODUCTION_MODEL_DIR.resolve(),
        }
        if target in blocked:
            raise typer.BadParameter("--model-dir cannot be a live research or production catalog")
        train_research(
            LOL_TRAINING_PATH,
            LOL_VALIDATION_PATH,
            LOL_SPLIT_PATH,
            tape,
            target,
            target.parent / "archive",
        )
        return
    boost_rounds = train_research(
        LOL_TRAINING_PATH,
        LOL_VALIDATION_PATH,
        LOL_SPLIT_PATH,
        tape,
        LOL_RESEARCH_MODEL_DIR,
        LOL_RESEARCH_ARCHIVE_DIR,
    )
    train_production_model(
        LOL_PRODUCTION_TRAINING_PATH,
        LOL_SPLIT_PATH,
        tape,
        LOL_PRODUCTION_MODEL_DIR,
        LOL_PRODUCTION_ARCHIVE_DIR,
        boost_rounds,
    )


if __name__ == "__main__":
    typer.run(main)
