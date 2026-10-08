"""Synthetic LoL Stage 06 trainer tests. No network, no data/lol."""

import json
from collections.abc import Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
import pandas as pd
import pytest
import typer
from typer.testing import CliRunner

from lol.constants import LOL_DATASET_COLUMNS, LOL_TRAIN_END_SECOND
from lol.lol_validation_metrics import (
    LolHoldoutPoint,
    build_holdout_aggregates,
    cluster_metric_totals,
    directional_markout,
)
from lol.types import LolDatasetRow, LolPrepareSplitRow
from shared.constants.lol import LOL_SOURCE_LAG_SECONDS
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.types.dataset import DotaGameFeatureRow
from shared.utils.dota_features import (
    DOTA_HISTORY_COLUMN_NAMES,
    DOTA_XP_FEATURE_COLUMNS,
    GRID_HISTORY_POLICY,
    attach_catalog_features,
)
from shared.utils.hashing import sha256_file
from shared.utils.market_scenario_report import bootstrap_series_cluster_ci

RESEARCH_TREES = 53


class TrainModule(Protocol):
    """Importable Stage 06 surface."""

    LOL_RESEARCH_MODEL_DIR: Path

    def slice_model_rows(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Keep labeled LoL rows at every second from 0 through map end."""
        ...

    def load_lol_dataset(self, path: Path, tape: pd.DataFrame) -> pd.DataFrame:
        """Read one labeled slice and enrich it from the explicit tape."""
        ...

    def train_research(
        self,
        training_path: Path,
        validation_path: Path,
        split_path: Path,
        tape: pd.DataFrame,
        research_dir: Path,
        research_archive_dir: Path,
    ) -> int:
        """Fit research GBM and publish LoL research artifacts."""
        ...

    def train_production_model(
        self,
        production_training_path: Path,
        split_path: Path,
        tape: pd.DataFrame,
        production_dir: Path,
        production_archive_dir: Path,
        boost_rounds: int,
    ) -> None:
        """Fit production GBM from the research tree count."""
        ...

    def holdout_points(
        self,
        validation: pd.DataFrame,
        model_prices: npt.NDArray[np.float64],
        event_ids: Mapping[int, str],
    ) -> list[LolHoldoutPoint]:
        """Pair each validation row with its restored model price and PM event_id."""
        ...

    def main(self, model_dir: Path | None = None) -> None:
        """Publish research then production, or one experiment catalog."""
        ...


def load_train() -> TrainModule:
    """Import Stage 06 through its typed test surface."""
    return cast(TrainModule, cast(object, import_module("lol.06_train_model")))


def train_research_base(
    training_path: Path,
    validation_path: Path,
    split_path: Path,
    tape: pd.DataFrame,
    research_dir: Path,
    archive_dir: Path,
) -> int:
    """Call Stage 06 research with canonical paths and the explicit tape."""
    return load_train().train_research(
        training_path,
        validation_path,
        split_path,
        tape,
        research_dir,
        archive_dir,
    )


def dataset_row(match_id: int, second: int, current: float, future: float) -> LolDatasetRow:
    """One Stage 05 row with the 300s target."""
    return {
        "match_id": match_id,
        "start_time": 1_700_000_000 + match_id,
        "event_id": f"e{match_id}",
        "second": second,
        "state_ts_us": (1_700_000_000 + second) * 1_000_000,
        "radiant_win": True,
        "radiant_nw_adv": 100,
        "radiant_nw": 1100,
        "dire_nw": 1000,
        "radiant_xp_adv": 50,
        "deaths_radiant": 0,
        "deaths_dire": 1,
        "top1_nw_adv": 10,
        "radiant_top1_nw_ratio": 0.5,
        "dire_top1_nw_ratio": 0.4,
        "top3_nw_adv": 30,
        "radiant_top3_nw_ratio": 0.6,
        "dire_top3_nw_ratio": 0.5,
        "market_radiant_prior": 0.55,
        "market_p_radiant": current,
        "signal_market_p_radiant_300s": future,
    }


def split_row(match_id: int, event_id: str, split: str) -> LolPrepareSplitRow:
    """One Stage 05 split.parquet row."""
    return {
        "match_id": match_id,
        "event_id": event_id,
        "esports_game_id": str(match_id),
        "game_number": 1,
        "start_time": 1_700_000_000 + match_id,
        "event_start_time": 1_700_000_000,
        "split": "train" if split == "train" else "validation",
    }


def write_dataset(path: Path, rows: Sequence[LolDatasetRow]) -> None:
    """Write the wide LoL experiment parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(list(rows), columns=LOL_DATASET_COLUMNS).to_parquet(path, index=False)


def write_game_features(dataset_dir: Path, rows: Sequence[LolDatasetRow]) -> None:
    """Write the sibling game_features tape covering every dataset row's map-second."""
    tape_rows: list[DotaGameFeatureRow] = [
        DotaGameFeatureRow(
            match_id=row["match_id"],
            game_second=row["second"],
            radiant_nw_adv=row["radiant_nw_adv"],
            radiant_nw=row["radiant_nw"],
            dire_nw=row["dire_nw"],
            radiant_xp_adv=row["radiant_xp_adv"],
            deaths_radiant=row["deaths_radiant"],
            deaths_dire=row["deaths_dire"],
            top1_nw_adv=row["top1_nw_adv"],
            radiant_top1_nw_ratio=row["radiant_top1_nw_ratio"],
            dire_top1_nw_ratio=row["dire_top1_nw_ratio"],
            top3_nw_adv=row["top3_nw_adv"],
            radiant_top3_nw_ratio=row["radiant_top3_nw_ratio"],
            dire_top3_nw_ratio=row["dire_top3_nw_ratio"],
            market_radiant_prior=row["market_radiant_prior"],
        )
        for row in rows
    ]
    pd.DataFrame(tape_rows).to_parquet(dataset_dir / "game_features.parquet", index=False)


def read_tape(dataset_dir: Path) -> pd.DataFrame:
    """Read back the game_features tape written next to the dataset parquets."""
    return pd.read_parquet(dataset_dir / "game_features.parquet")


def write_split(path: Path, rows: Sequence[LolPrepareSplitRow]) -> None:
    """Write a tiny split.parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(list(rows)).to_parquet(path, index=False)


def standard_rows() -> tuple[list[LolDatasetRow], list[LolDatasetRow], list[LolPrepareSplitRow]]:
    """Two train maps and two validation seconds on distinct events."""
    train = [
        dataset_row(1, 0, 0.60, 0.62),
        dataset_row(2, 60, 0.40, 0.38),
    ]
    valid = [
        dataset_row(3, 0, 0.50, 0.55),
        dataset_row(4, 60, 0.50, 0.45),
    ]
    split = [
        split_row(1, "e-train-a", "train"),
        split_row(2, "e-train-b", "train"),
        split_row(3, "e-valid-a", "validation"),
        split_row(4, "e-valid-b", "validation"),
    ]
    return train, valid, split


class RecordingModel:
    """Deterministic booster for LoL orchestration tests."""

    def __init__(self, trees: int, deltas: npt.NDArray[np.float64]) -> None:
        """Store the tree count and per-row predicted deltas."""
        self.trees = trees
        self.deltas = deltas

    def predict(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
        """Return one delta per feature row."""
        assert len(features) == len(self.deltas)
        return self.deltas

    def num_trees(self) -> int:
        """Return the recorded tree count."""
        return self.trees

    def save_model(self, path: Path) -> None:
        """Write a marker instead of a real booster."""
        path.write_text("lol-model")


def patch_research_fit(
    monkeypatch: pytest.MonkeyPatch,
    deltas: npt.NDArray[np.float64],
    trees: int = 2,
) -> list[pd.DataFrame]:
    """Stub the research member fit and capture its train then validation frames."""
    captured: list[pd.DataFrame] = []

    def record_research_members(
        train_frame: pd.DataFrame,
        valid_X: pd.DataFrame,
        _valid_y: npt.NDArray[np.float64],
        _features: Sequence[str],
    ) -> list[lgb.Booster]:
        """Keep both feature frames and return one recording booster."""
        captured.append(train_frame)
        captured.append(valid_X)
        return [cast(lgb.Booster, RecordingModel(trees, deltas))]

    monkeypatch.setattr(load_train(), "fit_research_members", record_research_members)
    return captured


def test_load_lol_dataset_returns_the_81_column_catalog(tmp_path: Path) -> None:
    """The loader slice carries every DOTA_XP catalog column after enrichment."""
    path = tmp_path / "training.parquet"
    rows = [dataset_row(1, 0, 0.5, 0.6)]
    write_dataset(path, rows)
    write_game_features(tmp_path, rows)
    frame = load_train().load_lol_dataset(path, read_tape(tmp_path))
    assert set(DOTA_XP_FEATURE_COLUMNS) <= set(frame.columns)
    assert len(DOTA_XP_FEATURE_COLUMNS) == 81


def test_attach_catalog_features_adds_history_and_market_columns(tmp_path: Path) -> None:
    """The 60 tape-derived columns resolve as-of; logit and vs-prior are exact."""
    rows: list[LolDatasetRow] = []
    for second in range(361):
        row = dataset_row(1, second, 0.50, 0.60)
        row["radiant_nw_adv"] = second * 10
        rows.append(row)
    path = tmp_path / "training.parquet"
    write_dataset(path, rows)
    write_game_features(tmp_path, rows)
    frame_rows = pd.read_parquet(path)
    frame = attach_catalog_features(
        frame_rows,
        read_tape(tmp_path),
        board_tape=None,
        key_seconds=frame_rows["second"],
        start_second=GRID_HISTORY_POLICY.start_second,
    )
    assert set(DOTA_HISTORY_COLUMN_NAMES) <= set(frame.columns)
    row360 = frame.loc[frame["second"] == 360].iloc[0]
    assert row360["game_total_5m_radiant_nw_adv"] == pytest.approx(3000)
    assert row360["game_change_1m_radiant_nw_adv"] == pytest.approx(600)
    assert row360["logit_market_p_radiant"] == pytest.approx(0.0)
    assert row360["market_vs_prior"] == pytest.approx(0.50 - 0.55)
    row0 = frame.loc[frame["second"] == 0].iloc[0]
    assert bool(np.isnan(row0["game_change_1m_radiant_nw_adv"]))


def test_research_smoke_writes_four_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Research publish writes member_00.txt/json, split, and validation_metrics.csv."""
    train_rows, valid_rows, split_rows = standard_rows()
    training_path = tmp_path / "training.parquet"
    validation_path = tmp_path / "validation.parquet"
    split_path = tmp_path / "split.parquet"
    research_dir = tmp_path / "research"
    archive_dir = tmp_path / "archive" / "research"
    write_dataset(training_path, train_rows)
    write_dataset(validation_path, valid_rows)
    write_game_features(tmp_path, train_rows + valid_rows)
    write_split(split_path, split_rows)
    captured = patch_research_fit(monkeypatch, np.array([0.10, -0.10], dtype=np.float64))

    train_research_base(
        training_path, validation_path, split_path, read_tape(tmp_path), research_dir, archive_dir
    )

    assert list(captured[1].columns) == DOTA_XP_FEATURE_COLUMNS
    assert (research_dir / "member_00.txt").read_text() == "lol-model"
    assert (research_dir / "split.parquet").exists()
    assert (research_dir / "validation_metrics.csv").exists()
    assert not (tmp_path / ".next-research").exists()
    payload = (research_dir / "model.json").read_text()
    assert f'"source_lag_seconds": {LOL_SOURCE_LAG_SECONDS}' in payload
    assert '"xp_source": "level"' in payload
    assert '"state_source": "lolesports_window_details_grid_networth"' in payload
    assert sha256_file(training_path) in payload
    assert sha256_file(validation_path) in payload
    assert '"trees": 2' in payload
    assert '"train_matches": 2' in payload
    assert '"validation_matches": 2' in payload
    meta = json.loads((research_dir / "model.json").read_text())
    assert meta["features"] == DOTA_XP_FEATURE_COLUMNS
    assert meta["members"] == ["member_00.txt"]
    assert meta["member_trees"] == [2]
    assert meta["ensemble_k"] == 1
    metrics = meta["metrics"]
    assert metrics["no_move_mae_300_cents"] == pytest.approx(5.0)
    assert metrics["model_mae_300_cents"] == pytest.approx(5.0)
    assert metrics["mae_gain_300_cents"] == pytest.approx(0.0)
    assert metrics["mae_gain_300_ci_low_cents"] == pytest.approx(0.0)
    assert metrics["mae_gain_300_ci_high_cents"] == pytest.approx(0.0)
    assert metrics["model_bias_300_cents"] == pytest.approx(0.0)
    assert metrics["dir_300_cents"] == pytest.approx(5.0)
    csv_frame = pd.read_csv(research_dir / "validation_metrics.csv")
    full_csv = csv_frame.iloc[-1]
    assert full_csv["bucket"] == "0-479"
    assert full_csv["no_move_mae_300_cents"] == pytest.approx(5.0)
    assert full_csv["model_mae_300_cents"] == pytest.approx(5.0)
    assert full_csv["mae_gain_300_cents"] == pytest.approx(0.0)
    assert full_csv["mae_gain_300_ci_low_cents"] == pytest.approx(0.0)
    assert full_csv["mae_gain_300_ci_high_cents"] == pytest.approx(0.0)
    assert full_csv["model_bias_300_cents"] == pytest.approx(0.0)
    assert full_csv["dir_300_cents"] == pytest.approx(5.0)
    assert full_csv["dir_300_ci_low_cents"] == pytest.approx(5.0)
    assert full_csv["dir_300_ci_high_cents"] == pytest.approx(5.0)


def test_direct_1hz_features_use_parquet_second(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Early-stopping validation features keep parquet second, not second-10."""
    train_rows, valid_rows, split_rows = standard_rows()
    training_path = tmp_path / "training.parquet"
    validation_path = tmp_path / "validation.parquet"
    split_path = tmp_path / "split.parquet"
    write_dataset(training_path, train_rows)
    write_dataset(validation_path, valid_rows)
    write_game_features(tmp_path, train_rows + valid_rows)
    write_split(split_path, split_rows)
    captured = patch_research_fit(monkeypatch, np.array([0.10, -0.10], dtype=np.float64))

    train_research_base(
        training_path,
        validation_path,
        split_path,
        read_tape(tmp_path),
        tmp_path / "research",
        tmp_path / "archive" / "research",
    )

    assert list(captured[1]["second"]) == [0, 60]


def test_rows_past_train_end_reach_neither_fit_nor_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-540 labeled row is dropped before early stopping and metrics."""
    train_rows, valid_rows, split_rows = standard_rows()
    valid_rows.append(dataset_row(4, 600, 0.50, 0.55))
    training_path = tmp_path / "training.parquet"
    validation_path = tmp_path / "validation.parquet"
    split_path = tmp_path / "split.parquet"
    write_dataset(training_path, train_rows)
    write_dataset(validation_path, valid_rows)
    write_game_features(tmp_path, train_rows + valid_rows)
    write_split(split_path, split_rows)
    module = load_train()
    seen_seconds: list[int] = []
    real_holdout = module.holdout_points

    def spy_holdout(
        validation: pd.DataFrame,
        model_prices: npt.NDArray[np.float64],
        event_ids: Mapping[int, str],
    ) -> list[LolHoldoutPoint]:
        seen_seconds.extend(int(value) for value in validation["second"])
        return real_holdout(validation, model_prices, event_ids)

    monkeypatch.setattr(module, "holdout_points", spy_holdout)
    captured = patch_research_fit(monkeypatch, np.array([0.10, -0.10], dtype=np.float64))

    train_research_base(
        training_path,
        validation_path,
        split_path,
        read_tape(tmp_path),
        tmp_path / "research",
        tmp_path / "archive" / "research",
    )

    assert list(captured[1]["second"]) == [0, 60]
    assert max(seen_seconds) < BUY_CUTOFF_SECOND


def test_research_trains_the_81_column_catalog_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Research always trains the scoreboard catalog with pending deaths and death ages."""
    train_rows, valid_rows, split_rows = standard_rows()
    training_path = tmp_path / "training.parquet"
    validation_path = tmp_path / "validation.parquet"
    split_path = tmp_path / "split.parquet"
    research_dir = tmp_path / "research"
    archive_dir = tmp_path / "archive" / "research"
    write_dataset(training_path, train_rows)
    write_dataset(validation_path, valid_rows)
    write_game_features(tmp_path, train_rows + valid_rows)
    write_split(split_path, split_rows)
    captured = patch_research_fit(monkeypatch, np.array([0.10, -0.10], dtype=np.float64))

    load_train().train_research(
        training_path,
        validation_path,
        split_path,
        read_tape(tmp_path),
        research_dir,
        archive_dir,
    )

    assert list(captured[1].columns) == DOTA_XP_FEATURE_COLUMNS
    meta = json.loads((research_dir / "model.json").read_text())
    assert meta["features"] == DOTA_XP_FEATURE_COLUMNS


def test_metrics_formulas_include_tie_as_radiant() -> None:
    """no-move MAE, model MAE, gain, bias, and dir_300 match the spec, tie → radiant."""
    tie = directional_markout(0.50, 0.60, 0.50)
    below = directional_markout(0.50, 0.40, 0.40)
    assert tie == pytest.approx(0.10)
    assert below == pytest.approx(0.10)
    points = [
        LolHoldoutPoint("e1", 0, 0.50, 0.60, 0.50),
        LolHoldoutPoint("e1", 0, 0.50, 0.40, 0.40),
    ]
    full = build_holdout_aggregates(points)[-1]
    assert full.no_move_mae == pytest.approx(0.10)
    assert full.model_mae == pytest.approx(0.05)
    assert full.mae_gain == pytest.approx(0.05)
    assert full.bias == pytest.approx(-0.05)
    assert full.dir_300 == pytest.approx(0.10)
    assert full.mae_gain_ci is not None
    assert full.dir_300_ci is not None
    assert full.mae_gain_ci.low == pytest.approx(full.mae_gain)
    assert full.mae_gain_ci.high == pytest.approx(full.mae_gain)
    assert full.dir_300_ci.low == pytest.approx(full.dir_300)
    assert full.dir_300_ci.high == pytest.approx(full.dir_300)


def test_bootstrap_seed_is_deterministic_and_clusters_by_event() -> None:
    """One event is a point-mass CI equal to the metric; two maps share that cluster."""
    points = [
        LolHoldoutPoint("shared", 0, 0.50, 0.70, 0.60),
        LolHoldoutPoint("shared", 60, 0.40, 0.30, 0.35),
    ]
    first = build_holdout_aggregates(points)[-1]
    second = build_holdout_aggregates(points)[-1]
    assert first.mae_gain == pytest.approx(0.075)
    assert first.dir_300 == pytest.approx(0.15)
    assert first.mae_gain_ci is not None
    assert first.dir_300_ci is not None
    assert second.mae_gain_ci is not None
    assert first.mae_gain_ci.low == pytest.approx(first.mae_gain)
    assert first.mae_gain_ci.high == pytest.approx(first.mae_gain)
    assert first.dir_300_ci.low == pytest.approx(first.dir_300)
    assert first.dir_300_ci.high == pytest.approx(first.dir_300)
    assert first.mae_gain_ci.low == second.mae_gain_ci.low
    assert first.mae_gain_ci.high == second.mae_gain_ci.high
    totals = cluster_metric_totals(["shared", "shared"], [0.1, -0.05])
    assert len(totals) == 1
    assert totals[0].row_count == 2
    cluster_ci = bootstrap_series_cluster_ci(totals)
    assert cluster_ci is not None
    assert cluster_ci.low == pytest.approx(totals[0].numerator_sum / totals[0].row_count)
    assert cluster_ci.high == pytest.approx(cluster_ci.low)


def test_per_minute_and_full_window_csv_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seconds 0 and 60 land in 0-59 and 60-119; full-window last; no dir_10_cents."""
    train_rows, _, split_rows = standard_rows()
    valid = [dataset_row(3, 0, 0.50, 0.55), dataset_row(4, 60, 0.50, 0.45)]
    training_path = tmp_path / "training.parquet"
    validation_path = tmp_path / "validation.parquet"
    split_path = tmp_path / "split.parquet"
    research_dir = tmp_path / "research"
    write_dataset(training_path, train_rows)
    write_dataset(validation_path, valid)
    write_game_features(tmp_path, train_rows + valid)
    write_split(split_path, split_rows)
    patch_research_fit(monkeypatch, np.array([0.10, -0.10], dtype=np.float64))

    train_research_base(
        training_path,
        validation_path,
        split_path,
        read_tape(tmp_path),
        research_dir,
        tmp_path / "archive",
    )

    frame = pd.read_csv(research_dir / "validation_metrics.csv")
    assert "dir_10_cents" not in frame.columns
    assert "dir_120_cents" not in frame.columns
    assert "no_move_mae_420_cents" not in frame.columns
    assert list(frame["bucket"])[-1] == "0-479"
    assert "0-59" in set(frame["bucket"])
    assert "60-119" in set(frame["bucket"])


def test_production_happy_reuses_trees_and_leaves_research(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production copies research trees, publishes production/, leaves research/."""
    train_rows, valid_rows, split_rows = standard_rows()
    production_rows = train_rows + valid_rows
    production_path = tmp_path / "production_training.parquet"
    split_path = tmp_path / "split.parquet"
    research_dir = tmp_path / "research"
    production_dir = tmp_path / "production"
    archive_dir = tmp_path / "archive" / "production"
    write_dataset(production_path, production_rows)
    write_game_features(tmp_path, production_rows)
    write_split(split_path, split_rows)
    research_dir.mkdir()
    (research_dir / "model.txt").write_text("research-model")
    seen_boost_rounds: list[int] = []

    def record_production_members(
        _train_frame: pd.DataFrame,
        boost_rounds: int,
        _features: Sequence[str],
    ) -> list[lgb.Booster]:
        """Capture the requested tree count and return one recording booster."""
        seen_boost_rounds.append(boost_rounds)
        return [cast(lgb.Booster, RecordingModel(RESEARCH_TREES, np.array([], dtype=np.float64)))]

    monkeypatch.setattr(load_train(), "fit_production_members", record_production_members)

    load_train().train_production_model(
        production_path,
        split_path,
        read_tape(tmp_path),
        production_dir,
        archive_dir,
        RESEARCH_TREES,
    )

    assert seen_boost_rounds == [RESEARCH_TREES]
    assert (research_dir / "model.txt").read_text() == "research-model"
    assert (production_dir / "member_00.txt").read_text() == "lol-model"
    meta = json.loads((production_dir / "model.json").read_text())
    assert meta["metrics"] is None
    assert meta["members"] == ["member_00.txt"]
    assert meta["member_trees"] == [RESEARCH_TREES]
    assert sha256_file(production_path) in (production_dir / "model.json").read_text()
    assert meta["validation_matches"] is None
    assert not (production_dir / "validation_metrics.csv").exists()


def test_first_publish_has_empty_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No predecessor live dir: research appears and archive stays empty."""
    train_rows, valid_rows, split_rows = standard_rows()
    training_path = tmp_path / "training.parquet"
    validation_path = tmp_path / "validation.parquet"
    split_path = tmp_path / "split.parquet"
    research_dir = tmp_path / "research"
    archive_dir = tmp_path / "archive" / "research"
    write_dataset(training_path, train_rows)
    write_dataset(validation_path, valid_rows)
    write_game_features(tmp_path, train_rows + valid_rows)
    write_split(split_path, split_rows)
    patch_research_fit(monkeypatch, np.array([0.10, -0.10], dtype=np.float64))

    train_research_base(
        training_path, validation_path, split_path, read_tape(tmp_path), research_dir, archive_dir
    )

    assert (research_dir / "model.json").is_file()
    assert not archive_dir.exists() or not any(archive_dir.iterdir())


def test_second_publish_archives_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second research publish moves the previous pair under archive/research/<name>/."""
    train_rows, valid_rows, split_rows = standard_rows()
    training_path = tmp_path / "training.parquet"
    validation_path = tmp_path / "validation.parquet"
    split_path = tmp_path / "split.parquet"
    research_dir = tmp_path / "research"
    archive_dir = tmp_path / "archive" / "research"
    write_dataset(training_path, train_rows)
    write_dataset(validation_path, valid_rows)
    write_game_features(tmp_path, train_rows + valid_rows)
    write_split(split_path, split_rows)
    patch_research_fit(monkeypatch, np.array([0.10, -0.10], dtype=np.float64))
    train_research_base(
        training_path, validation_path, split_path, read_tape(tmp_path), research_dir, archive_dir
    )
    first_payload = json.loads((research_dir / "model.json").read_text())
    first_name = str(first_payload["name"])
    (research_dir / "member_00.txt").write_text("first-model")
    patch_research_fit(monkeypatch, np.array([0.10, -0.10], dtype=np.float64))
    train_research_base(
        training_path, validation_path, split_path, read_tape(tmp_path), research_dir, archive_dir
    )
    assert (archive_dir / first_name / "member_00.txt").read_text() == "first-model"
    assert (research_dir / "member_00.txt").read_text() == "lol-model"


def test_slice_model_rows_keeps_labeled_rows_through_train_end() -> None:
    """LoL fit retains labeled rows inside the training window only."""
    frame = pd.DataFrame(
        {
            "second": [-1, 0, 1, BUY_CUTOFF_SECOND, LOL_TRAIN_END_SECOND + 1],
            "signal_market_p_radiant_300s": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )

    assert list(load_train().slice_model_rows(frame)["second"]) == [
        0,
        1,
        BUY_CUTOFF_SECOND,
    ]


def test_main_trains_research_then_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """One train invocation reads the tape once and feeds research then production."""
    calls: list[tuple[str, int | None]] = []
    tape_reads: list[Path] = []

    def research(*_args: object) -> int:
        """Record research and return its selected tree count."""
        calls.append(("research", None))
        return 37

    def production(
        _production_training_path: object,
        _split_path: object,
        _tape: object,
        _production_dir: object,
        _production_archive_dir: object,
        boost_rounds: int,
    ) -> None:
        """Record the tree count received by the production refit."""
        calls.append(("production", boost_rounds))

    def read_tape_once(path: Path, **_kwargs: object) -> pd.DataFrame:
        """Return an empty stand-in tape and record the read."""
        tape_reads.append(path)
        return pd.DataFrame()

    module = import_module("lol.06_train_model")
    monkeypatch.setattr(module, "train_research", research)
    monkeypatch.setattr(module, "train_production_model", production)
    monkeypatch.setattr(module.pd, "read_parquet", read_tape_once)

    module.main()

    assert calls == [("research", None), ("production", 37)]
    assert len(tape_reads) == 1


def test_main_model_dir_publishes_research_only_and_refuses_live_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--model-dir trains only the research ensemble into <dir>; the live dirs reject."""
    module = load_train()
    calls: list[tuple[str, Path, Path]] = []

    def research(
        _training_path: Path,
        _validation_path: Path,
        _split_path: Path,
        _tape: pd.DataFrame,
        research_dir: Path,
        research_archive_dir: Path,
    ) -> int:
        """Record the publish targets and stop."""
        calls.append(("research", research_dir, research_archive_dir))
        return 1

    def production(*_args: object) -> None:
        calls.append(("production", tmp_path, tmp_path))

    def read_tape(*_args: object, **_kwargs: object) -> pd.DataFrame:
        """Stand in for the Stage 05 tape read."""
        return pd.DataFrame()

    monkeypatch.setattr(module, "train_research", research)
    monkeypatch.setattr(module, "train_production_model", production)
    monkeypatch.setattr(pd, "read_parquet", read_tape)

    target = tmp_path / "experiment"
    module.main(target)

    assert calls == [("research", target, tmp_path / "archive")]

    app = typer.Typer()
    app.command()(module.main)
    result = CliRunner().invoke(app, ["--model-dir", str(module.LOL_RESEARCH_MODEL_DIR)])
    assert result.exit_code != 0
    assert calls == [("research", target, tmp_path / "archive")]


def test_train_cli_has_no_modes_or_paths() -> None:
    """Stage 06 exposes one canonical research-then-production command."""
    app = typer.Typer()
    app.command()(load_train().main)
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for removed in (
        "--production",
        "--training-path",
        "--target-horizon-seconds",
        "--train-second-min",
        "--train-second-max",
        "--row-stride",
        "--league-whitelist",
        "--models-dir",
        "--research-only",
    ):
        assert removed not in result.output
