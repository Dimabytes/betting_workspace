# pyright: reportMissingTypeStubs=false

"""Shared LightGBM fit helpers used by Dota and LoL trainers."""

import hashlib
import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Protocol, cast

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
import pandas as pd

from shared.types.dataset import DatasetRow
from shared.types.model import ModelMeta, ModelMetrics
from shared.utils.hashing import sha256_file
from shared.utils.market_scenario_report import MetricConfidenceInterval
from shared.utils.model_registry import MODEL_META_FILENAME, read_model_meta

MAX_BOOST_ROUNDS = 3000
LGB_PARAMS = {
    "objective": "regression_l1",
    "metric": "l1",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 200,
    "verbosity": -1,
}

TRAINING_COLUMNS: list[str] = list(DatasetRow.__annotations__)

ENSEMBLE_IDENTITY_VERSION = b"ensemble-identity-v1"
EARLY_STOP_PATIENCE = 100
MEMBER_THREADS = 1
ENSEMBLE_K = 10
# One process per member, MEMBER_THREADS inside it: the reduction order stays fixed,
# so a refit reproduces the same bytes, and the K fits still use every core.
MEMBER_FIT_WORKERS = min(ENSEMBLE_K, os.process_cpu_count() or 1)
# Quality-fit RNG from docs/experiments/ensemble: QUALITY_SEED=1, default_sub90
# is ARMS[3], outer_index=0. Keep this tuple so retraining reproduces the
# sub90-20260912 catalogs member for member.
ENSEMBLE_SEED = 1
ENSEMBLE_ARM_INDEX = 3
ENSEMBLE_OUTER_INDEX = 0
ENSEMBLE_ARM = "default_sub90"
ENSEMBLE_SAMPLING = "sub90"
ENSEMBLE_SUBSAMPLE_FRACTION = 0.90


class ModelCatalogError(ValueError):
    """A model directory cannot be loaded as an ensemble catalog."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DeltaModel(Protocol):
    """Anything that turns feature rows into 300-second price-delta predictions."""

    def predict(self, data: Any) -> Any: ...

    def num_trees(self) -> int: ...


@dataclass(frozen=True)
class GbmPredictor:
    """Loaded catalog: mean of the listed LightGBM members."""

    model_dir: Path
    feature_names: tuple[str, ...]
    member_names: tuple[str, ...]
    member_trees: tuple[int, ...]
    _boosters: tuple[lgb.Booster, ...]

    def predict(self, data: Any) -> npt.NDArray[np.float64]:
        """Return the mean member delta for every row."""
        stacked = np.stack(
            [
                np.asarray(booster.predict(data), dtype=np.float64)  # pyright: ignore[reportUnknownMemberType]
                for booster in self._boosters
            ]
        )
        return stacked.mean(axis=0)

    def predict_one_thread(self, data: Any) -> npt.NDArray[np.float64]:
        """Return the mean member delta for every row on one LightGBM thread."""
        stacked = np.stack(
            [
                np.asarray(
                    booster.predict(data, num_threads=1),  # pyright: ignore[reportUnknownMemberType]
                    dtype=np.float64,
                )
                for booster in self._boosters
            ]
        )
        return stacked.mean(axis=0)

    def num_trees(self) -> int:
        """Round of the mean member tree count; production uses this as boost_rounds."""
        return round(sum(self.member_trees) / len(self.member_trees))


@dataclass(frozen=True)
class HoldoutWindowStats:
    """300s holdout fields used to populate ModelMetrics cents."""

    rows: int
    future_price_300_count: int
    no_move_mae_300: float | None
    model_mae_300: float | None
    mae_gain_300: float | None
    mae_gain_300_ci: MetricConfidenceInterval | None
    model_bias_300: float | None
    directional_markout_300s: float | None


def load_training_dataset(path: Path) -> pd.DataFrame:
    """Read DatasetRow columns from a Dota training parquet."""
    frame = pd.read_parquet(path)
    missing = [column for column in TRAINING_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"training dataset missing columns: {', '.join(missing)}")
    return frame[TRAINING_COLUMNS]


def build_price_delta_labels(frame: pd.DataFrame) -> npt.NDArray[np.float64]:
    """Return the canonical 300-second future-price delta target."""
    future = frame["signal_market_p_radiant_300s"].to_numpy(dtype=np.float64)
    current = frame["market_p_radiant"].to_numpy(dtype=np.float64)
    return future - current


def predict_future_prices(
    model: DeltaModel,
    features: pd.DataFrame,
    current_market_prices: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Predict price deltas and restore clipped future market prices."""
    deltas = np.asarray(model.predict(features), dtype=np.float64)
    return np.clip(current_market_prices + deltas, 0.0, 1.0)


def ensemble_member_params() -> dict[str, object]:
    """Live LightGBM knobs with one thread so member fits stay reproducible."""
    params: dict[str, object] = dict(LGB_PARAMS)
    params["num_threads"] = MEMBER_THREADS
    return params


def fit_ensemble_member_booster(
    train_X: pd.DataFrame,
    train_y: npt.NDArray[np.float64],
    valid_X: pd.DataFrame,
    valid_y: npt.NDArray[np.float64],
) -> lgb.Booster:
    """Train one ensemble member with early stopping on the holdout set."""
    train_data = lgb.Dataset(train_X, train_y)
    validation_data = lgb.Dataset(valid_X, valid_y)
    return lgb.train(  # pyright: ignore[reportUnknownMemberType]
        ensemble_member_params(),
        train_data,
        num_boost_round=MAX_BOOST_ROUNDS,
        valid_sets=[validation_data],
        valid_names=["validation"],
        callbacks=[lgb.early_stopping(EARLY_STOP_PATIENCE, verbose=False)],
    )


def fit_ensemble_member_fixed_trees(
    train_X: pd.DataFrame,
    train_y: npt.NDArray[np.float64],
    boost_rounds: int,
) -> lgb.Booster:
    """Train one production member with exactly `boost_rounds` trees, one thread, no holdout."""
    train_data = lgb.Dataset(train_X, train_y)
    return lgb.train(  # pyright: ignore[reportUnknownMemberType]
        ensemble_member_params(),
        train_data,
        num_boost_round=boost_rounds,
    )


def _ensemble_member_rng(member_index: int) -> np.random.Generator:
    """Independent Generator for one default_sub90 quality member."""
    return np.random.default_rng(
        np.random.SeedSequence(
            (ENSEMBLE_SEED, ENSEMBLE_ARM_INDEX, ENSEMBLE_OUTER_INDEX, member_index)
        )
    )


def _member_match_ids(
    unique_ids: npt.NDArray[np.int64], member_index: int
) -> npt.NDArray[np.int64]:
    """Draw this member's 90% of the matches, without replacement."""
    rng = _ensemble_member_rng(member_index)
    n_keep = round(len(unique_ids) * ENSEMBLE_SUBSAMPLE_FRACTION)
    indices = rng.choice(len(unique_ids), size=n_keep, replace=False)
    return unique_ids[indices]


def group_rows_by_match_id(
    frame: pd.DataFrame,
) -> tuple[dict[int, pd.DataFrame], npt.NDArray[np.int64]]:
    """Split a training frame by match_id, preserving first-seen match order."""
    grouped = {
        int(cast(int, match_id)): group for match_id, group in frame.groupby("match_id", sort=False)
    }
    unique_ids = frame["match_id"].drop_duplicates().to_numpy(dtype=np.int64)
    return grouped, unique_ids


def member_train_frame(
    grouped: dict[int, pd.DataFrame],
    unique_ids: npt.NDArray[np.int64],
    member_index: int,
) -> pd.DataFrame:
    """Rows for one default_sub90 member: a random 90% of the matches."""
    match_ids = _member_match_ids(unique_ids, member_index)
    pieces = [grouped[int(match_id)] for match_id in match_ids]
    return pd.concat(pieces, ignore_index=True)


@dataclass(frozen=True, eq=False)
class ResearchMemberFit:
    """Research member fit: early stopping on the holdout picks each member's tree count."""

    valid_X: pd.DataFrame
    valid_y: npt.NDArray[np.float64]
    features: tuple[str, ...]

    def __call__(self, train: pd.DataFrame) -> lgb.Booster:
        """Fit one research member on its subsampled rows."""
        return fit_ensemble_member_booster(
            train[list(self.features)],
            build_price_delta_labels(train),
            self.valid_X,
            self.valid_y,
        )


@dataclass(frozen=True, eq=False)
class ProductionMemberFit:
    """Production member fit: every member gets the same fixed tree count and no holdout."""

    boost_rounds: int
    features: tuple[str, ...]

    def __call__(self, train: pd.DataFrame) -> lgb.Booster:
        """Fit one production member on its subsampled rows."""
        return fit_ensemble_member_fixed_trees(
            train[list(self.features)],
            build_price_delta_labels(train),
            self.boost_rounds,
        )


EnsembleMemberFit = ResearchMemberFit | ProductionMemberFit

_worker_rows: tuple[dict[int, pd.DataFrame], npt.NDArray[np.int64]] | None = None
_worker_fit: EnsembleMemberFit | None = None


def init_member_fit_worker(train_frame: pd.DataFrame, fit_member: EnsembleMemberFit) -> None:
    """Group the training rows once per worker so a member job only carries its index."""
    global _worker_rows, _worker_fit
    _worker_rows = group_rows_by_match_id(train_frame)
    _worker_fit = fit_member


def fit_member_job(member_index: int) -> lgb.Booster:
    """Draw this member's rows inside the worker and fit it there."""
    if _worker_rows is None or _worker_fit is None:
        raise RuntimeError("member fit worker was not initialised")
    grouped, unique_ids = _worker_rows
    return _worker_fit(member_train_frame(grouped, unique_ids, member_index))


def _fit_ensemble_members(
    train_frame: pd.DataFrame,
    fit_member: EnsembleMemberFit,
) -> list[lgb.Booster]:
    """Fit K subsampled members, one process each; `map` returns them in member order."""
    with ProcessPoolExecutor(
        max_workers=MEMBER_FIT_WORKERS,
        mp_context=get_context("spawn"),
        initializer=init_member_fit_worker,
        initargs=(train_frame, fit_member),
    ) as pool:
        return list(pool.map(fit_member_job, range(ENSEMBLE_K)))


def fit_research_members(
    train_frame: pd.DataFrame,
    valid_X: pd.DataFrame,
    valid_y: npt.NDArray[np.float64],
    features: Sequence[str],
) -> list[lgb.Booster]:
    """Fit K default_sub90 members with early stopping on the holdout features."""
    return _fit_ensemble_members(train_frame, ResearchMemberFit(valid_X, valid_y, tuple(features)))


def fit_production_members(
    train_frame: pd.DataFrame,
    boost_rounds: int,
    features: Sequence[str],
) -> list[lgb.Booster]:
    """Fit K default_sub90 members with a fixed tree count and no holdout."""
    return _fit_ensemble_members(train_frame, ProductionMemberFit(boost_rounds, tuple(features)))


def build_ensemble_model_meta(
    train_matches: int,
    train_dataset_sha256: str,
    *,
    features: Sequence[str],
    validation_dataset_sha256: str | None,
    metrics: ModelMetrics | None,
    members: Sequence[str],
    member_trees: Sequence[int],
    source_lag_seconds: int,
) -> ModelMeta:
    """Build catalog metadata: members, trees, and the subsampling training rule."""
    now = datetime.now(UTC)
    return ModelMeta(
        name=now.strftime("%Y%m%dT%H%M%SZ"),
        trained_at=now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        train_dataset_sha256=train_dataset_sha256,
        validation_dataset_sha256=validation_dataset_sha256,
        features=list(features),
        source_lag_seconds=source_lag_seconds,
        train_matches=train_matches,
        metrics=metrics,
        members=list(members),
        member_trees=list(member_trees),
        ensemble_arm=ENSEMBLE_ARM,
        ensemble_k=len(members),
        ensemble_sampling=ENSEMBLE_SAMPLING,
    )


def require_metric(value: float | None, field_name: str) -> float:
    """Require a populated validation metric for model metadata."""
    if value is None:
        raise ValueError(f"missing validation metric: {field_name}")
    return value


def build_model_metrics(
    model: DeltaModel,
    full_window_stats: HoldoutWindowStats,
) -> ModelMetrics:
    """Build holdout metrics from the full-window 300s validation stats."""
    mae_ci = full_window_stats.mae_gain_300_ci
    if mae_ci is None:
        raise ValueError("missing validation confidence interval: mae_gain_300")
    return ModelMetrics(
        trees=model.num_trees(),
        rows=full_window_stats.rows,
        future_300_n=full_window_stats.future_price_300_count,
        no_move_mae_300_cents=require_metric(full_window_stats.no_move_mae_300, "no_move_mae_300")
        * 100,
        model_mae_300_cents=require_metric(full_window_stats.model_mae_300, "model_mae_300") * 100,
        mae_gain_300_cents=require_metric(full_window_stats.mae_gain_300, "mae_gain_300") * 100,
        mae_gain_300_ci_low_cents=mae_ci.low * 100,
        mae_gain_300_ci_high_cents=mae_ci.high * 100,
        model_bias_300_cents=require_metric(full_window_stats.model_bias_300, "model_bias_300")
        * 100,
        dir_300_cents=require_metric(
            full_window_stats.directional_markout_300s,
            "directional_markout_300s",
        )
        * 100,
    )


def member_filename(index: int) -> str:
    """Return the catalog filename for ensemble member `index`."""
    return f"member_{index:02d}.txt"


def catalog_member_names(meta: ModelMeta) -> tuple[str, ...]:
    """Resolve member filenames; an empty, missing, or invalid list is a load error."""
    raw = meta.get("members")
    if type(raw) is not list or not raw:
        raise ModelCatalogError("members list is empty")
    if any(type(item) is not str for item in raw):
        raise ModelCatalogError("members list is empty")
    names = tuple(raw)
    if any(not _is_plain_filename(name) for name in names):
        raise ModelCatalogError("member name is not a plain filename")
    if len(set(names)) != len(names):
        raise ModelCatalogError("members list repeats a file")
    return names


def catalog_member_trees(meta: ModelMeta) -> tuple[int, ...]:
    """Resolve recorded tree counts; missing, null, or a non-int list is a load error."""
    raw = meta.get("member_trees")
    if type(raw) is not list or not raw:
        raise ModelCatalogError("member_trees list is empty")
    if any(type(item) is not int for item in raw):
        raise ModelCatalogError("member_trees list is empty")
    return tuple(raw)


def _is_plain_filename(name: str) -> bool:
    """True when `name` is a single path component, not empty, `.`, or `..`."""
    return name != "" and Path(name).name == name and name not in {".", ".."}


def require_regular_file(path: Path, reason: str) -> None:
    """Require `path` to be a regular file, or raise a catalog error."""
    try:
        is_file = path.is_file()
    except OSError as exc:
        raise ModelCatalogError("model artifacts are unreadable") from exc
    if not is_file:
        raise ModelCatalogError(reason)


def load_booster(path: Path) -> lgb.Booster:
    """Construct one LightGBM booster from a member file."""
    try:
        return lgb.Booster(model_file=str(path))
    except (lgb.basic.LightGBMError, ValueError, RuntimeError) as exc:
        raise ModelCatalogError("ensemble member cannot be parsed by LightGBM") from exc
    except OSError as exc:
        raise ModelCatalogError("ensemble member cannot be read") from exc


def load_predictor_from_meta(model_dir: Path, meta: ModelMeta) -> GbmPredictor:
    """Load every listed member and refuse a partial catalog."""
    features = list(meta["features"])
    if not features:
        raise ModelCatalogError("model.json features empty")
    names = catalog_member_names(meta)
    recorded_trees = catalog_member_trees(meta)
    if len(recorded_trees) != len(names):
        raise ModelCatalogError("member_trees do not match members")
    boosters: list[lgb.Booster] = []
    trees: list[int] = []
    for name in names:
        path = model_dir / name
        require_regular_file(path, "ensemble member is missing or not a regular file")
        booster = load_booster(path)
        try:
            booster_features = list(booster.feature_name())
        except (lgb.basic.LightGBMError, OSError, ValueError, RuntimeError) as exc:
            raise ModelCatalogError("ensemble member features cannot be read") from exc
        if booster_features != features:
            raise ModelCatalogError("ensemble member features do not match model.json")
        boosters.append(booster)
        trees.append(booster.num_trees())
    if recorded_trees != tuple(trees):
        raise ModelCatalogError("member_trees do not match the loaded boosters")
    return GbmPredictor(
        model_dir=model_dir,
        feature_names=tuple(features),
        member_names=names,
        member_trees=tuple(trees),
        _boosters=tuple(boosters),
    )


def load_predictor(model_dir: Path) -> GbmPredictor:
    """Load an ensemble catalog from `model_dir` / model.json."""
    meta_path = model_dir / MODEL_META_FILENAME
    require_regular_file(meta_path, "model.json is missing or not a regular file")
    try:
        meta = read_model_meta(meta_path)
    except (OSError, ValueError) as exc:
        raise ModelCatalogError("model.json cannot be read or parsed") from exc
    return load_predictor_from_meta(model_dir, meta)


def model_identity_sha256(model_dir: Path) -> str:
    """Identity of a catalog: ordered members, their SHAs, and predict metadata."""
    meta = read_model_meta(model_dir / MODEL_META_FILENAME)
    names = catalog_member_names(meta)
    hasher = hashlib.sha256()
    hasher.update(ENSEMBLE_IDENTITY_VERSION)
    hasher.update(b"\n")
    for name in names:
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\n")
        hasher.update(sha256_file(model_dir / name).encode("ascii"))
        hasher.update(b"\n")
    hasher.update(b"features\n")
    for feature in meta["features"]:
        hasher.update(feature.encode("utf-8"))
        hasher.update(b"\n")
    hasher.update(b"source_lag_seconds\n")
    hasher.update(str(int(meta["source_lag_seconds"])).encode("ascii"))
    return hasher.hexdigest()


def write_ensemble_members(staging_dir: Path, boosters: Sequence[lgb.Booster]) -> tuple[str, ...]:
    """Write `member_00.txt` … into staging and return those filenames."""
    names = tuple(member_filename(index) for index in range(len(boosters)))
    for name, booster in zip(names, boosters, strict=True):
        booster.save_model(staging_dir / name)
    return names
