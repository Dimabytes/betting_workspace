"""Holdout MAE, bias, directional markout, and event-cluster bootstrap for LoL."""

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lol.constants import LOL_GRID_START_SECOND
from lol.types import LolValidationMetricsCsvRow
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.utils.market_scenario_report import (
    MetricConfidenceInterval,
    SeriesMetricTotals,
    bootstrap_series_cluster_ci,
    mean_of,
    probability_to_cents,
)

METRICS_CSV_FIELDNAMES: tuple[str, ...] = (
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
    "dir_300_cents",
    "dir_300_ci_low_cents",
    "dir_300_ci_high_cents",
)


@dataclass(frozen=True)
class LolHoldoutPoint:
    """One validation second with current, 300s future, and restored model price."""

    event_id: str
    second: int
    current: float
    future: float
    model_p: float


@dataclass(frozen=True)
class LolBucketAggregate:
    """MAE, bias, directional markout, and CIs for one second bucket."""

    bucket: str
    second: int
    rows: int
    future_300_n: int
    no_move_mae: float | None
    model_mae: float | None
    mae_gain: float | None
    mae_gain_ci: MetricConfidenceInterval | None
    bias: float | None
    dir_300: float | None
    dir_300_ci: MetricConfidenceInterval | None


def directional_markout(current: float, future: float, model_p: float) -> float:
    """+1 radiant (tie included) or -1 dire, times the 300s midpoint move."""
    direction = 1.0 if model_p >= current else -1.0
    return direction * (future - current)


def cluster_metric_totals(
    event_ids: Sequence[str],
    values: Sequence[float],
) -> tuple[SeriesMetricTotals, ...]:
    """Sum one metric by PM event_id for cluster bootstrap."""
    totals_by_event: dict[str, SeriesMetricTotals] = {}
    for event_id, value in zip(event_ids, values, strict=True):
        previous = totals_by_event.get(event_id)
        if previous is None:
            totals_by_event[event_id] = SeriesMetricTotals(numerator_sum=value, row_count=1)
            continue
        totals_by_event[event_id] = SeriesMetricTotals(
            numerator_sum=previous.numerator_sum + value,
            row_count=previous.row_count + 1,
        )
    return tuple(totals_by_event.values())


def minute_bucket_label(second: int) -> str:
    """Map a grid second to a 60-second CSV bucket label."""
    start = (second // 60) * 60
    return f"{start}-{start + 59}"


def aggregate_points(
    bucket: str, second: int, points: Sequence[LolHoldoutPoint]
) -> LolBucketAggregate:
    """Compute MAE, gain, bias, directional markout, and event-cluster CIs."""
    no_move_errors = [abs(point.future - point.current) for point in points]
    model_errors = [abs(point.future - point.model_p) for point in points]
    biases = [point.model_p - point.future for point in points]
    mae_gains = [
        no_move - model_err for no_move, model_err in zip(no_move_errors, model_errors, strict=True)
    ]
    directionals = [
        directional_markout(point.current, point.future, point.model_p) for point in points
    ]
    event_ids = [point.event_id for point in points]
    no_move_mae = mean_of(no_move_errors)
    model_mae = mean_of(model_errors)
    mae_gain = None if no_move_mae is None or model_mae is None else no_move_mae - model_mae
    return LolBucketAggregate(
        bucket=bucket,
        second=second,
        rows=len(points),
        future_300_n=len(points),
        no_move_mae=no_move_mae,
        model_mae=model_mae,
        mae_gain=mae_gain,
        mae_gain_ci=bootstrap_series_cluster_ci(cluster_metric_totals(event_ids, mae_gains)),
        bias=mean_of(biases),
        dir_300=mean_of(directionals),
        dir_300_ci=bootstrap_series_cluster_ci(cluster_metric_totals(event_ids, directionals)),
    )


def build_holdout_aggregates(points: Sequence[LolHoldoutPoint]) -> list[LolBucketAggregate]:
    """Per-minute buckets that have rows, then the full 0..540 window aggregate."""
    by_bucket: dict[str, list[LolHoldoutPoint]] = {}
    for point in points:
        label = minute_bucket_label(point.second)
        by_bucket.setdefault(label, []).append(point)
    aggregates = [
        aggregate_points(label, (int(label.split("-")[0])), by_bucket[label])
        for label in sorted(by_bucket, key=lambda item: int(item.split("-")[0]))
    ]
    window = f"{LOL_GRID_START_SECOND}-{BUY_CUTOFF_SECOND - 1}"
    aggregates.append(aggregate_points(window, LOL_GRID_START_SECOND, points))
    return aggregates


def csv_row_from_aggregate(stats: LolBucketAggregate) -> LolValidationMetricsCsvRow:
    """Flatten one bucket into the research validation_metrics.csv contract."""
    mae_ci = stats.mae_gain_ci
    dir_ci = stats.dir_300_ci
    return {
        "bucket": stats.bucket,
        "second": stats.second,
        "rows": stats.rows,
        "future_300_n": stats.future_300_n,
        "no_move_mae_300_cents": probability_to_cents(stats.no_move_mae),
        "model_mae_300_cents": probability_to_cents(stats.model_mae),
        "mae_gain_300_cents": probability_to_cents(stats.mae_gain),
        "mae_gain_300_ci_low_cents": probability_to_cents(None if mae_ci is None else mae_ci.low),
        "mae_gain_300_ci_high_cents": probability_to_cents(None if mae_ci is None else mae_ci.high),
        "model_bias_300_cents": probability_to_cents(stats.bias),
        "dir_300_cents": probability_to_cents(stats.dir_300),
        "dir_300_ci_low_cents": probability_to_cents(None if dir_ci is None else dir_ci.low),
        "dir_300_ci_high_cents": probability_to_cents(None if dir_ci is None else dir_ci.high),
    }


def write_validation_metrics_csv(stats: Sequence[LolBucketAggregate], path: Path) -> None:
    """Write per-minute and full-window holdout metrics."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=METRICS_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(csv_row_from_aggregate(bucket) for bucket in stats)
