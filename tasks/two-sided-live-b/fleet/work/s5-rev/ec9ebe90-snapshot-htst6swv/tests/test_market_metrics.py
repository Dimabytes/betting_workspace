"""Unit tests for pure per-second market metrics."""

import pytest

from shared.types.dataset import ValidationDatasetRow
from train_model.market_metrics import (
    evaluate_validation_metric_row,
    evaluate_validation_metrics,
)


def build_validation_row() -> ValidationDatasetRow:
    """Build one complete synthetic validation row for evaluator tests."""
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


def test_evaluator_calculates_model_market_difference_and_directional_metrics() -> None:
    """Evaluator preserves rows and computes directional market metrics."""
    row = build_validation_row()
    metric = evaluate_validation_metric_row(row, 0.8)

    assert metric["model_p_radiant"] == 0.8
    assert metric["model_market_difference"] == pytest.approx(0.3)
    assert metric["directional_side"] == "radiant"
    assert metric["directional_markout_30s"] == pytest.approx(-0.05)
    assert metric["directional_markout_300s"] == pytest.approx(0.0)
    assert metric["match_id"] == row["match_id"]
    assert metric["market_radiant_prior"] == row["market_radiant_prior"]
    assert "model_log_loss" not in metric
    assert "trade_status" not in metric


def test_unusable_market_nulls_comparison_and_directional_metrics() -> None:
    """An unusable quote leaves only the model prediction on the metric row."""
    row = build_validation_row()
    row["market_status"] = "stale_quote"
    metric = evaluate_validation_metric_row(row, 0.8)

    assert metric["model_p_radiant"] == 0.8
    assert metric["model_market_difference"] is None
    assert metric["directional_side"] is None
    assert metric["directional_markout_30s"] is None
    assert "market_log_loss" not in metric
    assert "decision_net_edge" not in metric


def test_missing_market_probability_is_unusable() -> None:
    """A null live market probability suppresses directional metrics."""
    row = build_validation_row()
    row["market_p_radiant"] = None
    metric = evaluate_validation_metric_row(row, 0.8)

    assert metric["model_market_difference"] is None
    assert metric["directional_markout_30s"] is None


def test_directional_markout_uses_signal_horizons_and_flips_for_dire() -> None:
    """A model below market chooses Dire and reverses the markout sign."""
    row = build_validation_row()
    row["signal_market_p_radiant_300s"] = None
    metric = evaluate_validation_metric_row(row, 0.30)

    assert metric["directional_side"] == "dire"
    assert metric["directional_markout_30s"] == pytest.approx(0.05)
    assert metric["directional_markout_300s"] is None


def test_batch_evaluator_requires_matching_lengths_and_preserves_rows() -> None:
    """The batch API rejects silent zip truncation and preserves row order."""
    first = build_validation_row()
    second = build_validation_row()
    second["match_id"] = 43

    metrics = evaluate_validation_metrics([first, second], [0.70, 0.30])

    assert len(metrics) == 2
    assert [metric["match_id"] for metric in metrics] == [42, 43]
    assert [metric["model_p_radiant"] for metric in metrics] == [0.70, 0.30]
    with pytest.raises(ValueError, match="equal lengths"):
        evaluate_validation_metrics([first, second], [0.70])
