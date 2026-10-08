"""Unit tests for bucketed market scenario aggregation and CSV output."""

import csv
from pathlib import Path

import pytest

from shared.types.dataset import ValidationDatasetRow, ValidationMetricRow
from shared.utils.market_scenario_report import (
    CSV_FIELDNAMES,
    SCENARIO_BUCKETS,
    SeriesMetricTotals,
    aggregate_scenario_bucket,
    bootstrap_series_cluster_ci,
    build_scenario_stats,
    sum_series_metric,
    write_scenario_csv,
)


def build_validation_row() -> ValidationDatasetRow:
    """Build one complete validation row for report fixtures."""
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


def build_metric_row(second: int, event_id: str) -> ValidationMetricRow:
    """Build one slim metric row with deterministic report values."""
    validation_row = build_validation_row()
    validation_row["second"] = second
    validation_row["event_id"] = event_id
    return ValidationMetricRow(
        **validation_row,
        model_p_radiant=0.60,
        model_market_difference=0.10,
        directional_side="radiant",
        directional_markout_30s=0.03,
        directional_markout_300s=0.05,
    )


def test_series_totals_cluster_rows_by_event_id() -> None:
    """Rows from one event become one bootstrap sampling unit."""
    first = build_metric_row(0, "series-a")
    second = build_metric_row(30, "series-a")
    third = build_metric_row(0, "series-b")

    totals = sum_series_metric([first, second, third], [1.0, 3.0, 10.0])

    assert totals == (
        SeriesMetricTotals(numerator_sum=4.0, row_count=2),
        SeriesMetricTotals(numerator_sum=10.0, row_count=1),
    )


def test_bootstrap_same_seed_returns_same_ci_bounds() -> None:
    """The fixed bootstrap seed makes repeated confidence intervals stable."""
    series_totals = (
        SeriesMetricTotals(numerator_sum=4.0, row_count=2),
        SeriesMetricTotals(numerator_sum=10.0, row_count=1),
    )
    first = bootstrap_series_cluster_ci(series_totals)
    second = bootstrap_series_cluster_ci(series_totals)

    assert first is not None
    assert second == first


def test_series_rows_enter_or_leave_bootstrap_replicates_together() -> None:
    """Rows that share event_id collapse to one sampling unit before bootstrap."""
    first = build_metric_row(0, "series-a")
    second = build_metric_row(30, "series-a")
    third = build_metric_row(0, "series-b")
    totals = sum_series_metric([first, second, third], [1.0, 3.0, 10.0])

    assert len(totals) == 2
    assert totals[0] == SeriesMetricTotals(numerator_sum=4.0, row_count=2)
    assert bootstrap_series_cluster_ci(totals) is not None


def test_null_metric_values_stay_out_of_series_denominator() -> None:
    """Null numerators do not add rows to the bootstrap denominator."""
    first = build_metric_row(0, "series-a")
    second = build_metric_row(1, "series-a")
    totals = sum_series_metric([first, second], [0.2, None])

    assert len(totals) == 1
    assert totals[0].numerator_sum == pytest.approx(0.2)
    assert totals[0].row_count == 1
    assert bootstrap_series_cluster_ci(()) is None


def test_minute_bucket_includes_every_second_in_half_open_range() -> None:
    """A one-minute bucket includes interior seconds, not only boundaries."""
    first = build_metric_row(0, "series-a")
    second = build_metric_row(30, "series-b")

    stats = next(item for item in build_scenario_stats([first, second]) if item.bucket == "0-59")

    assert stats.bucket == "0-59"
    assert stats.second == 0
    assert stats.rows == 2
    assert stats.future_price_300_count == 2


def test_mae_uses_shared_future_price_denominator() -> None:
    """MAE gain and bias use only usable rows with a 300s future price."""
    first = build_metric_row(0, "series-a")
    first["market_p_radiant"] = 0.580
    first["model_p_radiant"] = 0.496115
    first["signal_market_p_radiant_300s"] = 0.555

    second = build_metric_row(30, "series-b")
    second["market_p_radiant"] = 0.400
    second["model_p_radiant"] = 0.450
    second["signal_market_p_radiant_300s"] = 0.500

    missing_future = build_metric_row(45, "series-c")
    missing_future["signal_market_p_radiant_300s"] = None

    zero_minute = next(bucket for bucket in SCENARIO_BUCKETS if bucket.label == "0-59")
    stats = aggregate_scenario_bucket(zero_minute, [first, second, missing_future])

    assert stats.rows == 3
    assert stats.future_price_300_count == 2
    assert stats.no_move_mae_300 == pytest.approx((0.025 + 0.100) / 2)
    assert stats.model_mae_300 == pytest.approx((0.058885 + 0.050) / 2)
    assert stats.no_move_mae_300 is not None
    assert stats.model_mae_300 is not None
    assert stats.mae_gain_300 == pytest.approx(stats.no_move_mae_300 - stats.model_mae_300)
    assert stats.model_bias_300 == pytest.approx((-0.058885 - 0.050) / 2)


def test_csv_has_twelve_overlapping_buckets_and_ci_fields(tmp_path: Path) -> None:
    """The writer emits the 12-row, 24-column scenario contract including pre-horn."""
    rows = [
        build_metric_row(0, "series-a"),
        build_metric_row(30, "series-b"),
        build_metric_row(60, "series-c"),
        build_metric_row(299, "series-d"),
        build_metric_row(300, "series-e"),
        build_metric_row(479, "series-f"),
    ]
    path = tmp_path / "validation_scenarios.csv"

    write_scenario_csv(build_scenario_stats(rows), path)

    with path.open(newline="") as handle:
        report_rows = list(csv.DictReader(handle))
    assert len(report_rows) == 12
    assert list(report_rows[0]) == list(CSV_FIELDNAMES)
    assert len(CSV_FIELDNAMES) == 14
    assert [row["bucket"] for row in report_rows] == [
        "-60--1",
        "0-59",
        "60-119",
        "120-179",
        "180-239",
        "240-299",
        "300-359",
        "360-419",
        "420-479",
        "-60-299",
        "300-479",
        "-60-479",
    ]
    assert "mae_gain_300_ci_low_cents" in report_rows[0]
    assert "mae_gain_300_ci_high_cents" in report_rows[0]
    assert "dir_300_ci_low_cents" in report_rows[0]
    assert "dir_300_ci_high_cents" in report_rows[0]


def test_csv_has_no_settlement_coverage_or_log_loss_columns(tmp_path: Path) -> None:
    """The one report contains only price and directional metrics."""
    path = tmp_path / "validation_scenarios.csv"
    write_scenario_csv(build_scenario_stats([build_metric_row(0, "series-a")]), path)

    with path.open(newline="") as handle:
        columns = list(csv.DictReader(handle).fieldnames or [])

    forbidden = {
        "model_log_loss",
        "market_log_loss",
        "delta_log_loss",
        "trade_status",
        "trade_side",
        "usable",
        "q_cov",
    }
    assert forbidden.isdisjoint(columns)


def test_empty_bucket_writes_empty_metric_and_ci_cells(tmp_path: Path) -> None:
    """A bucket without observations keeps metric and CI cells empty."""
    path = tmp_path / "validation_scenarios.csv"
    write_scenario_csv(build_scenario_stats([build_metric_row(0, "series-a")]), path)

    with path.open(newline="") as handle:
        report_rows = list(csv.DictReader(handle))
    empty_bucket = next(row for row in report_rows if row["bucket"] == "300-479")

    assert empty_bucket["rows"] == "0"
    assert empty_bucket["future_300_n"] == "0"
    assert empty_bucket["no_move_mae_300_cents"] == ""
    assert empty_bucket["mae_gain_300_ci_low_cents"] == ""
    assert empty_bucket["mae_gain_300_ci_high_cents"] == ""
    assert empty_bucket["dir_300_cents"] == ""
    assert empty_bucket["dir_300_ci_low_cents"] == ""
    assert empty_bucket["dir_300_ci_high_cents"] == ""
