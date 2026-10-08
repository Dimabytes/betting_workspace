from collections.abc import Sequence
from pathlib import Path
from typing import cast

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
import pandas as pd
import pytest

import shared.utils.gbm as train_model_gbm
import train_model.train_model as train_model_module
from shared.constants.dataset import (
    BACKTEST_LAG_SECONDS,
    TRAIN_LAG_SECONDS,
)
from shared.constants.paths import PRODUCTION_NOXP_MODEL_DIR, RESEARCH_NOXP_MODEL_DIR
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.types.dataset import DatasetRow, ValidationDatasetRow, ValidationMetricRow
from shared.types.model import ModelMeta, ModelMetrics
from shared.utils.dota_features import (
    DOTA_HISTORY_COLUMN_NAMES,
    DOTA_HISTORY_FIELDS,
    DOTA_NOXP_FEATURE_COLUMNS,
    DOTA_XP_FEATURE_COLUMNS,
)
from shared.utils.hashing import sha256_file
from shared.utils.market_scenario_report import (
    FULL_WINDOW_SCENARIO_BUCKET,
    ScenarioBucketStats,
)
from shared.utils.model_registry import (
    publish_model_dir,
    read_model_meta,
    write_model_meta,
)
from train_model.market_metrics import evaluate_validation_metrics
from train_model.train_model import (
    TRAINING_COLUMNS,
    VALIDATION_COLUMNS,
    build_price_delta_labels,
    build_validation_dataset_rows,
    lagged_source_features,
    load_training_dataset,
    load_validation_dataset,
    predict_future_prices,
    select_usable_validation_rows,
    select_validation_prediction_frame,
    slice_train_rows,
)


def build_validation_row() -> ValidationDatasetRow:
    """Build one complete validation row for training orchestration tests."""
    return ValidationDatasetRow(
        match_id=42,
        condition_id="condition",
        event_id="event",
        second=60,
        state_ts_us=60_000_000,
        market_status="ok",
        market_p_radiant=0.5,
        signal_market_p_radiant_30s=0.45,
        signal_market_p_radiant_300s=0.50,
        start_time=1_780_563_592,
        radiant_win=True,
        radiant_nw_adv=10,
        radiant_nw=10,
        dire_nw=0,
        radiant_xp_adv=20,
        deaths_radiant=1,
        deaths_dire=2,
        top1_nw_adv=0,
        radiant_top1_nw_ratio=0.0,
        dire_top1_nw_ratio=0.0,
        top3_nw_adv=0,
        radiant_top3_nw_ratio=0.0,
        dire_top3_nw_ratio=0.0,
        market_radiant_prior=0.48,
    )


RESEARCH_TREES = 53


def build_existing_model_meta() -> ModelMeta:
    """Build the old model metadata needed before a training rotation."""
    return ModelMeta(
        name="old-model",
        trained_at="2026-08-13T23:37:31Z",
        train_dataset_sha256="0" * 64,
        validation_dataset_sha256="1" * 64,
        features=list(DOTA_XP_FEATURE_COLUMNS),
        source_lag_seconds=8,
        train_matches=1,
        metrics=ModelMetrics(
            trees=1,
            rows=1,
            future_300_n=1,
            no_move_mae_300_cents=0.0,
            model_mae_300_cents=0.0,
            mae_gain_300_cents=0.0,
            mae_gain_300_ci_low_cents=0.0,
            mae_gain_300_ci_high_cents=0.0,
            model_bias_300_cents=0.0,
            dir_300_cents=0.0,
        ),
        members=["member_00.txt"],
        member_trees=[1],
        ensemble_arm="default_boot",
        ensemble_k=1,
        ensemble_sampling="boot",
    )


def build_training_frame() -> pd.DataFrame:
    """Build a small minute-row frame with current and future prices."""
    return pd.DataFrame(
        [
            {
                "match_id": 1,
                "start_time": 1_700_000_000,
                "second": 0,
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
                "radiant_top3_nw_ratio": 1.5,
                "dire_top3_nw_ratio": 1.2,
                "market_radiant_prior": 0.60,
                "market_p_radiant": 0.60,
                "signal_market_p_radiant_300s": 0.62,
            },
            {
                "match_id": 2,
                "start_time": 1_700_000_060,
                "second": 60,
                "radiant_win": False,
                "radiant_nw_adv": -100,
                "radiant_nw": 900,
                "dire_nw": 1000,
                "radiant_xp_adv": -50,
                "deaths_radiant": 1,
                "deaths_dire": 0,
                "top1_nw_adv": -10,
                "radiant_top1_nw_ratio": 0.3,
                "dire_top1_nw_ratio": 0.6,
                "top3_nw_adv": -30,
                "radiant_top3_nw_ratio": 0.9,
                "dire_top3_nw_ratio": 1.8,
                "market_radiant_prior": 0.40,
                "market_p_radiant": 0.40,
                "signal_market_p_radiant_300s": 0.38,
            },
            {
                "match_id": 3,
                "start_time": 1_700_000_120,
                "second": 120,
                "radiant_win": True,
                "radiant_nw_adv": 200,
                "radiant_nw": 1200,
                "dire_nw": 1000,
                "radiant_xp_adv": 100,
                "deaths_radiant": 0,
                "deaths_dire": 2,
                "top1_nw_adv": 20,
                "radiant_top1_nw_ratio": 0.7,
                "dire_top1_nw_ratio": 0.2,
                "top3_nw_adv": 60,
                "radiant_top3_nw_ratio": 2.1,
                "dire_top3_nw_ratio": 0.6,
                "market_radiant_prior": 0.55,
                "market_p_radiant": 0.55,
                "signal_market_p_radiant_300s": 0.57,
            },
            {
                "match_id": 4,
                "start_time": 1_700_000_180,
                "second": 180,
                "radiant_win": False,
                "radiant_nw_adv": -200,
                "radiant_nw": 800,
                "dire_nw": 1000,
                "radiant_xp_adv": -100,
                "deaths_radiant": 2,
                "deaths_dire": 0,
                "top1_nw_adv": -20,
                "radiant_top1_nw_ratio": 0.2,
                "dire_top1_nw_ratio": 0.8,
                "top3_nw_adv": -60,
                "radiant_top3_nw_ratio": 0.6,
                "dire_top3_nw_ratio": 2.4,
                "market_radiant_prior": 0.45,
                "market_p_radiant": 0.45,
                "signal_market_p_radiant_300s": 0.42,
            },
        ],
        columns=TRAINING_COLUMNS,
    )


def build_validation_frame() -> pd.DataFrame:
    """Build a small exact-second validation frame with all evaluator inputs."""
    rows = [build_validation_row() for _ in range(5)]
    rows[0]["match_id"] = 42
    rows[0]["second"] = 0
    rows[1]["match_id"] = 43
    rows[1]["second"] = 60
    rows[1]["radiant_win"] = False
    rows[1]["signal_market_p_radiant_300s"] = 0.49
    rows[2]["match_id"] = 44
    rows[2]["second"] = 120
    rows[2]["market_status"] = "missing_quote"
    rows[3]["match_id"] = 45
    rows[3]["second"] = 180
    rows[3]["signal_market_p_radiant_300s"] = None
    rows[4]["match_id"] = 46
    rows[4]["second"] = 600
    return pd.DataFrame(rows, columns=VALIDATION_COLUMNS)


def write_dataset_files(training_path: Path, validation_path: Path) -> None:
    """Write the small training and validation fixtures to parquet."""
    build_training_frame().to_parquet(training_path, index=False)
    build_validation_frame().to_parquet(validation_path, index=False)


def write_flat_tape(path: Path, match_ids: Sequence[int]) -> None:
    """Write a one-row-per-match tape: covers attach, resolves no lag pivots."""
    rows: dict[str, list[int] | list[float]] = {
        "match_id": list(match_ids),
        "game_second": [0] * len(match_ids),
        "deaths_radiant": [0] * len(match_ids),
        "deaths_dire": [0] * len(match_ids),
    }
    for field in DOTA_HISTORY_FIELDS:
        rows[field] = [0.0] * len(match_ids)
    pd.DataFrame(rows).to_parquet(path, index=False)


class StubModel:
    """Return fixed price deltas for prediction helper tests."""

    def __init__(self, deltas: npt.NDArray[np.float64]) -> None:
        self.deltas = deltas

    def predict(
        self,
        features: pd.DataFrame,
    ) -> npt.NDArray[np.float64]:
        """Return fixed deltas for the supplied feature rows."""
        assert len(features) == len(self.deltas)
        return self.deltas


def test_prediction_frame_stops_at_buy_cutoff() -> None:
    """Seconds at or past BUY_CUTOFF_SECOND stay out of restored-price evaluation."""
    inside = build_validation_row()
    inside["second"] = BUY_CUTOFF_SECOND - 1
    outside = build_validation_row()
    outside["second"] = BUY_CUTOFF_SECOND
    frame = pd.DataFrame([inside, outside], columns=VALIDATION_COLUMNS)

    selected = select_validation_prediction_frame(frame)

    assert list(selected["second"]) == [BUY_CUTOFF_SECOND - 1]


def test_lagged_source_features_shifts_second_only() -> None:
    """Feature second is parquet second minus BACKTEST_LAG; other columns stay."""
    frame = pd.DataFrame(
        {
            "second": [BACKTEST_LAG_SECONDS, BACKTEST_LAG_SECONDS + 10],
            "radiant_nw_adv": [1, 2],
            "radiant_nw": [100, 200],
            "dire_nw": [99, 198],
            "radiant_xp_adv": [3, 4],
            "deaths_radiant": [0, 1],
            "deaths_dire": [1, 0],
            "top1_nw_adv": [10, -10],
            "radiant_top1_nw_ratio": [0.5, 0.3],
            "dire_top1_nw_ratio": [0.4, 0.6],
            "top3_nw_adv": [30, -30],
            "radiant_top3_nw_ratio": [1.5, 0.9],
            "dire_top3_nw_ratio": [1.2, 1.8],
            "market_radiant_prior": [0.5, 0.5],
            "market_p_radiant": [0.4, 0.6],
        }
    )

    features = lagged_source_features(frame, BACKTEST_LAG_SECONDS, list(frame.columns))

    assert list(features.columns) == list(frame.columns)
    assert list(features["second"]) == [0, 10]
    assert list(features["market_p_radiant"]) == [0.4, 0.6]
    assert list(frame["second"]) == [BACKTEST_LAG_SECONDS, BACKTEST_LAG_SECONDS + 10]


def test_column_contracts_track_typed_dicts() -> None:
    """Keep parquet loaders aligned with the DatasetRow contracts."""
    assert list(DatasetRow.__annotations__) == TRAINING_COLUMNS
    assert list(ValidationDatasetRow.__annotations__) == VALIDATION_COLUMNS
    assert "market_p_radiant" in TRAINING_COLUMNS
    assert "market_p_radiant" in VALIDATION_COLUMNS
    derived = set(DOTA_HISTORY_COLUMN_NAMES) | {
        "logit_market_p_radiant",
        "market_vs_prior",
    }
    xp_history = {
        column for column in DOTA_HISTORY_COLUMN_NAMES if column.endswith("_radiant_xp_adv")
    }
    assert set(DOTA_XP_FEATURE_COLUMNS) - set(TRAINING_COLUMNS) == derived | {
        "pending_deaths_radiant",
        "pending_deaths_dire",
        "board_death_age_radiant_s",
        "board_death_age_dire_s",
    }
    assert set(DOTA_NOXP_FEATURE_COLUMNS) - set(TRAINING_COLUMNS) == derived - xp_history
    assert "signal_market_p_radiant_300s" not in DOTA_XP_FEATURE_COLUMNS
    assert "signal_market_p_radiant_300s" not in DOTA_NOXP_FEATURE_COLUMNS


def test_dataset_loaders_keep_training_narrow_and_validation_full(tmp_path: Path) -> None:
    """Read training columns narrowly and all validation metric inputs once."""
    training_path = tmp_path / "training_dataset.parquet"
    validation_path = tmp_path / "validation_dataset.parquet"
    write_dataset_files(training_path, validation_path)

    training_frame = load_training_dataset(training_path)
    validation_frame = load_validation_dataset(validation_path)

    assert list(training_frame.columns) == TRAINING_COLUMNS
    assert list(validation_frame.columns) == VALIDATION_COLUMNS
    assert "market_p_radiant" in training_frame.columns
    assert "market_p_radiant" in validation_frame.columns


def test_build_validation_dataset_rows_maps_nan_to_none_keeps_real_values() -> None:
    """Map parquet nulls to None without rewriting real zeros or False labels."""
    frame = build_validation_frame()
    frame.loc[0, "market_p_radiant"] = np.nan
    frame.loc[0, "signal_market_p_radiant_300s"] = np.nan
    frame.loc[1, "market_p_radiant"] = 0.0
    frame.loc[1, "radiant_win"] = False
    frame.loc[1, "deaths_radiant"] = 0

    rows = build_validation_dataset_rows(frame)

    assert rows[0]["market_p_radiant"] is None
    assert rows[0]["signal_market_p_radiant_300s"] is None
    assert rows[1]["market_p_radiant"] == 0.0
    assert rows[1]["radiant_win"] is False
    assert rows[1]["deaths_radiant"] == 0


def test_predict_future_prices_adds_current_market_price() -> None:
    """Restore future prices by adding each predicted delta to the current price."""
    model = StubModel(np.array([0.10, -0.20], dtype=np.float64))
    features = pd.DataFrame({"second": [0, 60]})
    current_prices = np.array([0.40, 0.60], dtype=np.float64)

    future_prices = predict_future_prices(
        cast(lgb.Booster, model),
        features,
        current_prices,
    )

    assert np.allclose(future_prices, [0.50, 0.40])


def test_predict_future_prices_clips_to_unit_interval() -> None:
    """Clip restored future prices at both probability boundaries."""
    model = StubModel(np.array([-1.00, 1.00], dtype=np.float64))
    features = pd.DataFrame({"second": [0, 60]})
    current_prices = np.array([0.20, 0.80], dtype=np.float64)

    future_prices = predict_future_prices(
        cast(lgb.Booster, model),
        features,
        current_prices,
    )

    assert np.array_equal(future_prices, [0.0, 1.0])


def test_main_evaluates_restored_future_prices_and_writes_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fit deltas, evaluate restored prices, then save the model after the CSV."""
    training_path = tmp_path / "training_dataset.parquet"
    validation_path = tmp_path / "validation_dataset.parquet"
    model_dir = tmp_path / "models"
    research_dir = model_dir / "research"
    archive_dir = model_dir / "archive" / "research"
    research_dir.mkdir(parents=True)
    write_model_meta(build_existing_model_meta(), research_dir / "model.json")
    (research_dir / "model.txt").write_text("previous-model")
    (research_dir / "split.parquet").write_bytes(b"split")
    scenarios_path = research_dir / "validation_scenarios.csv"
    model_path = research_dir / "model.txt"
    write_dataset_files(training_path, validation_path)
    monkeypatch.setattr(train_model_module, "MAX_BOOST_ROUNDS", 2)

    class RecordingDataset:
        """Capture the labels one member fit received."""

        def __init__(self, label: npt.NDArray[np.float64]) -> None:
            self.label = label

    seen_rows: list[ValidationDatasetRow] = []
    seen_probabilities: list[float] = []
    report_calls: list[Sequence[ScenarioBucketStats]] = []
    events: list[str] = []
    datasets: list[RecordingDataset] = []

    def record_metrics(
        rows: list[ValidationDatasetRow],
        future_prices: list[float],
    ) -> list[ValidationMetricRow]:
        """Record evaluator inputs and delegate to the real batch evaluator."""
        seen_rows.extend(rows)
        seen_probabilities.extend(future_prices)
        return evaluate_validation_metrics(rows, future_prices)

    monkeypatch.setattr(train_model_module, "evaluate_validation_metrics", record_metrics)

    def record_write_scenarios(rows: list[ScenarioBucketStats], path: Path) -> None:
        """Record that the single scenario report was written."""
        events.append("report")
        report_calls.append(rows)
        original_write_scenarios(rows, path)

    original_write_scenarios = train_model_module.write_scenario_csv
    monkeypatch.setattr(train_model_module, "write_scenario_csv", record_write_scenarios)

    class RecordingModel:
        """Return fixed deltas and record the final model save."""

        def predict(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
            """Return one delta for every usable prediction row."""
            assert len(features) == 3
            return np.array([0.10, 0.10, -0.20], dtype=np.float64)

        def num_trees(self) -> int:
            """Return a deterministic tree count for the orchestration log."""
            return 2

        def save_model(self, path: Path) -> None:
            """Record model persistence after the scenario report."""
            events.append("save")
            path.write_text("model")

    def record_research_members(
        train_frame: pd.DataFrame,
        _valid_X: pd.DataFrame,
        valid_y: npt.NDArray[np.float64],
        _features: Sequence[str],
    ) -> list[lgb.Booster]:
        """Capture the member fit inputs instead of running the LightGBM pool."""
        datasets.append(RecordingDataset(build_price_delta_labels(train_frame)))
        datasets.append(RecordingDataset(valid_y))
        return [cast(lgb.Booster, RecordingModel())]

    monkeypatch.setattr(train_model_module, "fit_research_members", record_research_members)

    write_flat_tape(tmp_path / "minutes.parquet", [1, 2, 3, 4])
    write_flat_tape(tmp_path / "seconds.parquet", [42, 43, 44, 45, 46])
    write_flat_tape(tmp_path / "deaths.parquet", [1, 2, 3, 4])
    train_model_module.train_research(
        training_path,
        validation_path,
        research_dir / "split.parquet",
        research_dir,
        archive_dir,
        TRAIN_LAG_SECONDS,
        DOTA_XP_FEATURE_COLUMNS,
        minute_tape_path=tmp_path / "minutes.parquet",
        second_tape_path=tmp_path / "seconds.parquet",
        death_tape_path=tmp_path / "deaths.parquet",
    )

    scenarios = pd.read_csv(scenarios_path)
    assert len(seen_rows) == 3
    assert [row["match_id"] for row in seen_rows] == [42, 43, 45]
    assert seen_probabilities == pytest.approx([0.60, 0.60, 0.30])
    assert len(report_calls) == 1
    assert len(report_calls[0]) == 12
    assert report_calls[0][-1].bucket == FULL_WINDOW_SCENARIO_BUCKET.label
    assert events == ["save", "report"]
    assert all(0.0 <= price <= 1.0 for price in seen_probabilities)
    assert len(datasets) == 2
    assert np.allclose(datasets[0].label, [0.02, -0.02, 0.02, -0.03])
    assert np.allclose(datasets[1].label, [0.0, -0.01, 0.0])
    assert len(scenarios) == 12
    assert len(scenarios.columns) == 14
    assert {
        "bucket",
        "second",
        "mae_gain_300_cents",
        "mae_gain_300_ci_low_cents",
        "mae_gain_300_ci_high_cents",
        "dir_300_ci_low_cents",
        "dir_300_ci_high_cents",
    }.issubset(scenarios.columns)
    assert "model_log_loss" not in scenarios.columns
    assert "trade_status" not in scenarios.columns
    assert "usable" not in scenarios.columns
    assert "q_cov" not in scenarios.columns
    assert not (tmp_path / "validation_market_metrics.parquet").exists()
    assert not model_path.exists()
    assert (research_dir / "member_00.txt").read_text() == "model"
    assert (research_dir / "split.parquet").read_bytes() == b"split"
    assert not (model_dir / ".next-research").exists()
    archived_dir = archive_dir / build_existing_model_meta()["name"]
    assert (archived_dir / "model.txt").read_text() == "previous-model"
    assert read_model_meta(archived_dir / "model.json")["name"] == archived_dir.name
    model_meta = read_model_meta(research_dir / "model.json")
    assert model_meta["source_lag_seconds"] == TRAIN_LAG_SECONDS
    assert model_meta["features"] == DOTA_XP_FEATURE_COLUMNS
    assert model_meta.get("members") == ["member_00.txt"]
    assert model_meta.get("member_trees") == [2]
    assert model_meta.get("ensemble_arm") == "default_sub90"
    assert model_meta.get("ensemble_k") == 1
    assert "poll_interval_seconds" not in model_meta
    assert model_meta["train_dataset_sha256"] == sha256_file(training_path)
    assert model_meta["validation_dataset_sha256"] == sha256_file(validation_path)
    metrics = model_meta["metrics"]
    assert metrics is not None
    assert metrics["trees"] == 2
    assert not hasattr(train_model_module, "predict_restored_probabilities")


def test_main_trains_research_then_production_then_noxp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One train invocation publishes the XP pair then the no-XP pair."""
    with pytest.raises(SystemExit):
        train_model_module.main(["--production"])
    with pytest.raises(SystemExit):
        train_model_module.main(["--training-path", "x"])
    with pytest.raises(SystemExit):
        train_model_module.main(["--no-xp"])

    calls: list[tuple[str, int, int]] = []

    def research(*args: object, **_kwargs: object) -> int:
        """Record the catalog and feature count, return tree count by feature width."""
        features = cast(Sequence[str], args[-1])
        live_dir = cast(Path, args[3])
        calls.append(("research", len(features), 0))
        del live_dir
        return 11 if len(features) == 70 else 37

    def production(*args: object, boost_rounds: int, **_kwargs: object) -> None:
        """Record the tree count and feature width received by the production refit."""
        features = cast(Sequence[str], args[-1])
        calls.append(("production", len(features), boost_rounds))

    monkeypatch.setattr(train_model_module, "train_research", research)
    monkeypatch.setattr(train_model_module, "train_production", production)

    train_model_module.main([])

    assert calls == [
        ("research", 81, 0),
        ("production", 81, 37),
        ("research", 70, 0),
        ("production", 70, 11),
    ]


def test_main_experiment_flags_fit_research_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The four experiment flags write one catalog and skip production."""
    training = tmp_path / "training_dataset.parquet"
    validation = tmp_path / "validation_dataset.parquet"
    training.write_bytes(b"train")
    validation.write_bytes(b"val")
    catalog = tmp_path / "catalog"
    calls: list[tuple[str, int | None]] = []

    def research(*args: object, **_kwargs: object) -> int:
        """Record the lag passed into the experiment research fit."""
        calls.append(("research", int(cast(int, args[-2]))))
        return 11

    def production(*_args: object, boost_rounds: int, **_kwargs: object) -> None:
        """Must not run on the experiment path."""
        calls.append(("production", boost_rounds))

    monkeypatch.setattr(train_model_module, "train_research", research)
    monkeypatch.setattr(train_model_module, "train_production", production)

    train_model_module.main(
        [
            "--lag-seconds",
            "25",
            "--training-dataset",
            str(training),
            "--validation-dataset",
            str(validation),
            "--model-dir",
            str(catalog),
        ]
    )

    assert calls == [("research", 25)]


def test_main_experiment_no_xp_fits_seventy_features(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--no-xp on the experiment path trains the 70-column catalog and skips production."""
    training = tmp_path / "training_dataset.parquet"
    validation = tmp_path / "validation_dataset.parquet"
    training.write_bytes(b"train")
    validation.write_bytes(b"val")
    catalog = tmp_path / "catalog"
    seen: list[int] = []

    def research(*args: object, **_kwargs: object) -> int:
        """Record the feature-list width passed into the experiment research fit."""
        seen.append(len(cast(Sequence[str], args[-1])))
        return 9

    def production(*_args: object, boost_rounds: int, **_kwargs: object) -> None:
        """Must not run on the experiment path."""
        seen.append(boost_rounds)

    monkeypatch.setattr(train_model_module, "train_research", research)
    monkeypatch.setattr(train_model_module, "train_production", production)

    train_model_module.main(
        [
            "--lag-seconds",
            "10",
            "--training-dataset",
            str(training),
            "--validation-dataset",
            str(validation),
            "--model-dir",
            str(catalog),
            "--no-xp",
        ]
    )

    assert seen == [70]


def test_parse_args_blocks_board_flag_misuse(tmp_path: Path) -> None:
    """--board and --board-age need --model-dir and cannot pair with --no-xp."""
    training = tmp_path / "training_dataset.parquet"
    validation = tmp_path / "validation_dataset.parquet"
    training.write_bytes(b"train")
    validation.write_bytes(b"val")

    with pytest.raises(SystemExit):
        train_model_module.parse_args(["--board"])
    with pytest.raises(SystemExit):
        train_model_module.parse_args(["--board-age"])
    with pytest.raises(SystemExit):
        train_model_module.parse_args(
            [
                "--lag-seconds",
                "10",
                "--training-dataset",
                str(training),
                "--validation-dataset",
                str(validation),
                "--model-dir",
                str(tmp_path / "catalog"),
                "--board",
                "--no-xp",
            ]
        )
    with pytest.raises(SystemExit):
        train_model_module.parse_args(
            [
                "--lag-seconds",
                "10",
                "--training-dataset",
                str(training),
                "--validation-dataset",
                str(validation),
                "--model-dir",
                str(tmp_path / "catalog"),
                "--board-age",
                "--no-xp",
            ]
        )


def test_parse_args_blocks_live_noxp_catalogs(tmp_path: Path) -> None:
    """Live research-noxp and production-noxp cannot be experiment --model-dir targets."""
    training = tmp_path / "training_dataset.parquet"
    validation = tmp_path / "validation_dataset.parquet"
    training.write_bytes(b"train")
    validation.write_bytes(b"val")

    for live_dir in (RESEARCH_NOXP_MODEL_DIR, PRODUCTION_NOXP_MODEL_DIR):
        with pytest.raises(SystemExit):
            train_model_module.parse_args(
                [
                    "--lag-seconds",
                    "10",
                    "--training-dataset",
                    str(training),
                    "--validation-dataset",
                    str(validation),
                    "--model-dir",
                    str(live_dir),
                    "--no-xp",
                ]
            )


def test_slice_train_rows_keeps_only_canonical_rows() -> None:
    """Dota fit uses the 300s label from model second -60 through map end."""
    frame = pd.DataFrame(
        {
            "second": [-120, -60, 0, 540, 600],
            "signal_market_p_radiant_300s": [0.1, 0.2, None, 0.4, 0.5],
        }
    )

    assert list(slice_train_rows(frame)["second"]) == [-60, 540, 600]


def test_validation_selection_separates_fit_from_full_map_inference() -> None:
    """Research stops before 600 while backtest inference retains later usable rows."""
    frame = build_validation_frame()

    research = select_validation_prediction_frame(frame)
    inference = select_usable_validation_rows(frame)

    assert 600 not in set(research["second"])
    assert 600 in set(inference["second"])


def test_publish_model_dir_creates_first_experiment_dir(tmp_path: Path) -> None:
    """First publish into a new experiment dir does not require a predecessor."""
    staging_dir = tmp_path / ".next-h240"
    staging_dir.mkdir()
    (staging_dir / "model.txt").write_text("model")
    research_dir = tmp_path / "h240"

    archived = publish_model_dir(staging_dir, research_dir, tmp_path / "archive")

    assert archived is None
    assert (research_dir / "model.txt").read_text() == "model"


def test_train_production_publishes_production_and_leaves_research_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production train publishes production/, archives the replaced pair, skips research/."""
    research_dir = tmp_path / "models" / "research"
    research_dir.mkdir(parents=True)
    (research_dir / "model.txt").write_text("research-model")
    (research_dir / "split.parquet").write_bytes(b"research-split")

    production_dataset = tmp_path / "dataset" / "production"
    production_model = tmp_path / "models" / "production"
    production_archive = tmp_path / "models" / "archive" / "production"
    production_dataset.mkdir(parents=True)
    production_model.mkdir(parents=True)
    write_model_meta(build_existing_model_meta(), production_model / "model.json")
    (production_model / "model.txt").write_text("previous-production")
    training_path = production_dataset / "training_dataset.parquet"
    split_source = production_dataset / "split.parquet"
    build_training_frame().to_parquet(training_path, index=False)
    split_source.write_bytes(b"prod-split")
    model_path = production_model / "model.txt"
    meta_path = production_model / "model.json"
    split_dest = production_model / "split.parquet"

    seen_boost_rounds: list[int] = []

    class ProductionModel:
        """Deterministic booster for the production-path orchestration test."""

        def num_trees(self) -> int:
            """Return a deterministic tree count."""
            return RESEARCH_TREES

        def save_model(self, path: Path) -> None:
            """Write a marker instead of a real booster."""
            path.write_text("production-model")

    def record_production_members(
        _train_frame: pd.DataFrame,
        boost_rounds: int,
        _features: Sequence[str],
    ) -> list[lgb.Booster]:
        """Capture the requested tree count instead of running the LightGBM pool."""
        seen_boost_rounds.append(boost_rounds)
        return [cast(lgb.Booster, ProductionModel())]

    monkeypatch.setattr(train_model_module, "fit_production_members", record_production_members)

    write_flat_tape(tmp_path / "minutes.parquet", [1, 2, 3, 4])
    write_flat_tape(tmp_path / "deaths.parquet", [1, 2, 3, 4])
    train_model_module.train_production(
        training_path,
        split_source,
        production_model,
        production_archive,
        DOTA_XP_FEATURE_COLUMNS,
        minute_tape_path=tmp_path / "minutes.parquet",
        death_tape_path=tmp_path / "deaths.parquet",
        boost_rounds=RESEARCH_TREES,
    )

    assert seen_boost_rounds == [RESEARCH_TREES]
    assert (research_dir / "model.txt").read_text() == "research-model"
    assert (research_dir / "split.parquet").read_bytes() == b"research-split"
    assert not model_path.exists()
    assert (production_model / "member_00.txt").read_text() == "production-model"
    assert split_dest.read_bytes() == b"prod-split"
    assert not (production_model / "validation_scenarios.csv").exists()
    assert not (tmp_path / "models" / ".next-production").exists()
    archived_dir = production_archive / build_existing_model_meta()["name"]
    assert (archived_dir / "model.txt").read_text() == "previous-production"
    model_meta = read_model_meta(meta_path)
    assert model_meta["metrics"] is None
    assert model_meta["train_matches"] == 4
    assert model_meta["members"] == ["member_00.txt"]
    assert model_meta["member_trees"] == [RESEARCH_TREES]
    assert model_meta["ensemble_k"] == 1
    assert model_meta["features"] == DOTA_XP_FEATURE_COLUMNS
    assert model_meta["train_dataset_sha256"] == sha256_file(training_path)
    assert model_meta["validation_dataset_sha256"] is None


def test_research_ensemble_member_rng_matches_experiment_quality_seed() -> None:
    """Member 0 of train_research draws the same 90% as ensemble run.py quality default_sub90."""
    ids = np.arange(10, dtype=np.int64)
    experiment = np.random.default_rng(np.random.SeedSequence((1, 3, 0, 0)))
    expected = ids[experiment.choice(len(ids), size=9, replace=False)]
    grouped = {int(match_id): pd.DataFrame({"match_id": [int(match_id)]}) for match_id in ids}
    sampled = train_model_gbm.member_train_frame(grouped, ids, 0)["match_id"].to_numpy()
    assert np.array_equal(sampled, expected)
