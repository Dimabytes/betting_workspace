"""Aggregate validation scenarios into one bucket CSV with cluster CIs."""

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from shared.constants.dataset import (
    MODEL_START_SECOND,
    MODEL_TARGET_HORIZON_SECONDS,
)
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.types.dataset import ValidationMetricRow


def select_usable_market_p(row: ValidationMetricRow) -> float | None:
    if row["market_status"] != "ok":
        return None
    return row["market_p_radiant"]


@dataclass(frozen=True)
class ScenarioBucket:
    """One half-open second range included in the scenario report."""

    label: str
    start_second: int
    end_second_exclusive: int


FULL_WINDOW_SCENARIO_BUCKET = ScenarioBucket(
    label=f"{MODEL_START_SECOND}-{BUY_CUTOFF_SECOND - 1}",
    start_second=MODEL_START_SECOND,
    end_second_exclusive=BUY_CUTOFF_SECOND,
)
SCENARIO_BUCKETS: tuple[ScenarioBucket, ...] = (
    *(
        ScenarioBucket(
            label=f"{start_second}-{start_second + 59}",
            start_second=start_second,
            end_second_exclusive=start_second + 60,
        )
        for start_second in range(MODEL_START_SECOND, BUY_CUTOFF_SECOND, 60)
    ),
    ScenarioBucket(
        label=f"{MODEL_START_SECOND}-{MODEL_TARGET_HORIZON_SECONDS - 1}",
        start_second=MODEL_START_SECOND,
        end_second_exclusive=MODEL_TARGET_HORIZON_SECONDS,
    ),
    ScenarioBucket(
        label=f"{MODEL_TARGET_HORIZON_SECONDS}-{BUY_CUTOFF_SECOND - 1}",
        start_second=MODEL_TARGET_HORIZON_SECONDS,
        end_second_exclusive=BUY_CUTOFF_SECOND,
    ),
    FULL_WINDOW_SCENARIO_BUCKET,
)

BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20260810


@dataclass(frozen=True)
class MetricConfidenceInterval:
    """Lower and upper bounds for a metric in probability units."""

    low: float
    high: float


@dataclass(frozen=True)
class SeriesMetricTotals:
    """Numerator and row count for one event series."""

    numerator_sum: float
    row_count: int


@dataclass(frozen=True)
class ScenarioBucketStats:
    """Aggregates for one overlapping scenario bucket."""

    bucket: str
    second: int
    rows: int
    future_price_300_count: int
    no_move_mae_300: float | None
    model_mae_300: float | None
    mae_gain_300: float | None
    mae_gain_300_ci: MetricConfidenceInterval | None
    model_bias_300: float | None
    directional_markout_30s: float | None
    directional_markout_300s: float | None
    directional_markout_300_ci: MetricConfidenceInterval | None


def is_usable_market_row(row: ValidationMetricRow) -> bool:
    """Return True when the row has a usable live market probability."""
    return select_usable_market_p(row) is not None


def mean_of(values: Sequence[float]) -> float | None:
    """Return the arithmetic mean, or None when the sequence is empty."""
    if not values:
        return None
    return sum(values) / len(values)


def mean_of_non_null(values: Sequence[float | None]) -> float | None:
    """Return the arithmetic mean of non-null values, or None when empty."""
    return mean_of([value for value in values if value is not None])


def require_non_null_values(
    values: Sequence[float | None],
    field_name: str,
) -> list[float]:
    """Require every value to be present and return the narrowed values."""
    non_null_values: list[float] = []
    for value in values:
        if value is None:
            raise ValueError(f"usable row has null {field_name}")
        non_null_values.append(value)
    return non_null_values


def sum_series_metric(
    rows: Sequence[ValidationMetricRow],
    values: Sequence[float | None],
) -> tuple[SeriesMetricTotals, ...]:
    """Sum one metric by event series while excluding null row values."""
    if len(rows) != len(values):
        raise ValueError(
            f"rows and metric values must have equal lengths: {len(rows)} != {len(values)}"
        )

    totals_by_event: dict[str, SeriesMetricTotals] = {}
    for row, value in zip(rows, values, strict=True):
        if value is None:
            continue
        event_id = row["event_id"]
        previous = totals_by_event.get(event_id)
        if previous is None:
            totals_by_event[event_id] = SeriesMetricTotals(
                numerator_sum=value,
                row_count=1,
            )
            continue
        totals_by_event[event_id] = SeriesMetricTotals(
            numerator_sum=previous.numerator_sum + value,
            row_count=previous.row_count + 1,
        )
    return tuple(totals_by_event.values())


def bootstrap_series_cluster_ci(
    series_totals: Sequence[SeriesMetricTotals],
) -> MetricConfidenceInterval | None:
    """Bootstrap row-weighted metric bounds by resampling event series."""
    if any(total.row_count < 0 for total in series_totals):
        raise ValueError("series row counts must be non-negative")

    positive_series = [total for total in series_totals if total.row_count > 0]
    if not positive_series:
        return None

    numerators = np.asarray(
        [total.numerator_sum for total in positive_series],
        dtype=np.float64,
    )
    counts = np.asarray(
        [total.row_count for total in positive_series],
        dtype=np.float64,
    )
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    sampled_indices = rng.integers(
        0,
        len(positive_series),
        size=(BOOTSTRAP_REPLICATES, len(positive_series)),
    )
    replicate_numerators = numerators[sampled_indices].sum(axis=1)
    replicate_counts = counts[sampled_indices].sum(axis=1)
    replicate_estimates = replicate_numerators / replicate_counts
    percentiles = np.asarray(
        np.percentile(replicate_estimates, [2.5, 97.5]),
        dtype=np.float64,
    )
    return MetricConfidenceInterval(
        low=float(percentiles[0]),
        high=float(percentiles[1]),
    )


def aggregate_scenario_bucket(
    bucket: ScenarioBucket,
    rows: Sequence[ValidationMetricRow],
) -> ScenarioBucketStats:
    """Aggregate every candidate row in one half-open scenario bucket."""
    usable_rows = [row for row in rows if is_usable_market_row(row)]
    future_rows = [row for row in usable_rows if row["signal_market_p_radiant_300s"] is not None]
    current_prices = require_non_null_values(
        [select_usable_market_p(row) for row in future_rows],
        "market_p_radiant",
    )
    future_prices = require_non_null_values(
        [row["signal_market_p_radiant_300s"] for row in future_rows],
        "signal_market_p_radiant_300s",
    )
    model_prices = [row["model_p_radiant"] for row in future_rows]
    no_move_errors = [
        abs(future_price - current_price)
        for current_price, future_price in zip(current_prices, future_prices, strict=True)
    ]
    model_errors = [
        abs(future_price - model_price)
        for future_price, model_price in zip(future_prices, model_prices, strict=True)
    ]
    model_biases = [
        model_price - future_price
        for future_price, model_price in zip(future_prices, model_prices, strict=True)
    ]
    mae_gain_numerators = [
        no_move_error - model_error
        for no_move_error, model_error in zip(no_move_errors, model_errors, strict=True)
    ]
    mae_gain_300_ci = bootstrap_series_cluster_ci(
        sum_series_metric(future_rows, mae_gain_numerators)
    )

    directional_rows = [row for row in usable_rows if row["directional_markout_300s"] is not None]
    directional_values = require_non_null_values(
        [row["directional_markout_300s"] for row in directional_rows],
        "directional_markout_300s",
    )
    directional_markout_300_ci = bootstrap_series_cluster_ci(
        sum_series_metric(directional_rows, directional_values)
    )

    no_move_mae_300 = mean_of(no_move_errors)
    model_mae_300 = mean_of(model_errors)
    mae_gain_300 = (
        None
        if no_move_mae_300 is None or model_mae_300 is None
        else no_move_mae_300 - model_mae_300
    )
    return ScenarioBucketStats(
        bucket=bucket.label,
        second=bucket.start_second,
        rows=len(rows),
        future_price_300_count=len(future_rows),
        no_move_mae_300=no_move_mae_300,
        model_mae_300=model_mae_300,
        mae_gain_300=mae_gain_300,
        mae_gain_300_ci=mae_gain_300_ci,
        model_bias_300=mean_of(model_biases),
        directional_markout_30s=mean_of_non_null(
            [row["directional_markout_30s"] for row in usable_rows]
        ),
        directional_markout_300s=mean_of_non_null(
            [row["directional_markout_300s"] for row in usable_rows]
        ),
        directional_markout_300_ci=directional_markout_300_ci,
    )


def build_scenario_stats(
    rows: Sequence[ValidationMetricRow],
) -> list[ScenarioBucketStats]:
    """Aggregate all thirteen overlapping scenario buckets."""
    return [
        aggregate_scenario_bucket(
            bucket,
            [
                row
                for row in rows
                if bucket.start_second <= row["second"] < bucket.end_second_exclusive
            ],
        )
        for bucket in SCENARIO_BUCKETS
    ]


def full_window_scenario_stats(stats: Sequence[ScenarioBucketStats]) -> ScenarioBucketStats:
    """Return the full model-window aggregate. It is always the last bucket."""
    return stats[-1]


def probability_to_cents(value: float | None) -> float | None:
    """Convert a probability-point markout to cents per contract."""
    if value is None:
        return None
    return value * 100.0


CSV_FIELDNAMES: tuple[str, ...] = (
    "bucket",
    "second",
    "rows",
    "future_300_n",
    "no_move_mae_300_cents",
    "model_mae_300_cents",
    "mae_gain_300_cents",
    "mae_gain_300_ci_low_cents",
    "mae_gain_300_ci_high_cents",
    "model_bias_300_cents",
    "dir_30_cents",
    "dir_300_cents",
    "dir_300_ci_low_cents",
    "dir_300_ci_high_cents",
)


def build_scenario_csv_row(
    stats: ScenarioBucketStats,
) -> dict[str, str | float | int | None]:
    """Flatten one bucket aggregate into a CSV-friendly record."""
    mae_ci = stats.mae_gain_300_ci
    directional_ci = stats.directional_markout_300_ci
    return {
        "bucket": stats.bucket,
        "second": stats.second,
        "rows": stats.rows,
        "future_300_n": stats.future_price_300_count,
        "no_move_mae_300_cents": probability_to_cents(stats.no_move_mae_300),
        "model_mae_300_cents": probability_to_cents(stats.model_mae_300),
        "mae_gain_300_cents": probability_to_cents(stats.mae_gain_300),
        "mae_gain_300_ci_low_cents": probability_to_cents(None if mae_ci is None else mae_ci.low),
        "mae_gain_300_ci_high_cents": probability_to_cents(None if mae_ci is None else mae_ci.high),
        "model_bias_300_cents": probability_to_cents(stats.model_bias_300),
        "dir_30_cents": probability_to_cents(stats.directional_markout_30s),
        "dir_300_cents": probability_to_cents(stats.directional_markout_300s),
        "dir_300_ci_low_cents": probability_to_cents(
            None if directional_ci is None else directional_ci.low
        ),
        "dir_300_ci_high_cents": probability_to_cents(
            None if directional_ci is None else directional_ci.high
        ),
    }


def write_scenario_csv(
    stats: Sequence[ScenarioBucketStats],
    path: Path,
) -> None:
    """Write all thirteen overlapping scenario aggregates to one CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(build_scenario_csv_row(bucket_stats) for bucket_stats in stats)
