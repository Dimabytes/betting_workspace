"""Pure per-second validation market metrics."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from shared.types.dataset import TradeSide, ValidationDatasetRow, ValidationMetricRow
from shared.utils.telonex_book import is_ok_market_second


@dataclass(frozen=True)
class MarketComparison:
    """Model-versus-market metrics for one second with a usable live quote."""

    model_market_difference: float
    directional_side: TradeSide
    directional_markout_30s: float | None
    directional_markout_300s: float | None


NULL_MARKET_COMPARISON_FIELDS: dict[str, None] = {
    "model_market_difference": None,
    "directional_side": None,
    "directional_markout_30s": None,
    "directional_markout_300s": None,
}


def select_usable_market_p(row: ValidationDatasetRow) -> float | None:
    """Return the live market probability only when the row's quote is usable."""
    if not is_ok_market_second(row):
        return None
    return row["market_p_radiant"]


def calculate_directional_markout(
    *,
    direction: float,
    current_market_p: float,
    future_market_p: float | None,
) -> float | None:
    """Calculate one signal-anchored markout, or null for a missing future."""
    if future_market_p is None:
        return None
    return direction * (future_market_p - current_market_p)


def build_market_comparison(
    *,
    row: ValidationDatasetRow,
    model_p_radiant: float,
) -> MarketComparison | None:
    """Compare model and market for one second, or None without a usable quote."""
    market_p_radiant = select_usable_market_p(row)
    if market_p_radiant is None:
        return None

    directional_side: TradeSide = "radiant" if model_p_radiant >= market_p_radiant else "dire"
    direction = 1.0 if directional_side == "radiant" else -1.0
    return MarketComparison(
        model_market_difference=model_p_radiant - market_p_radiant,
        directional_side=directional_side,
        directional_markout_30s=calculate_directional_markout(
            direction=direction,
            current_market_p=market_p_radiant,
            future_market_p=row["signal_market_p_radiant_30s"],
        ),
        directional_markout_300s=calculate_directional_markout(
            direction=direction,
            current_market_p=market_p_radiant,
            future_market_p=row["signal_market_p_radiant_300s"],
        ),
    )


def evaluate_validation_metric_row(
    row: ValidationDatasetRow,
    model_p_radiant: float,
) -> ValidationMetricRow:
    """Calculate market and directional metrics for one validation second."""
    comparison = build_market_comparison(
        row=row,
        model_p_radiant=model_p_radiant,
    )
    comparison_fields = NULL_MARKET_COMPARISON_FIELDS if comparison is None else asdict(comparison)
    return ValidationMetricRow(
        **row,
        model_p_radiant=model_p_radiant,
        **comparison_fields,
    )


def evaluate_validation_metrics(
    rows: Sequence[ValidationDatasetRow],
    model_probabilities: Sequence[float],
) -> list[ValidationMetricRow]:
    """Evaluate every validation second independently; lengths must match."""
    if len(rows) != len(model_probabilities):
        raise ValueError(
            "validation rows and model probabilities must have equal lengths: "
            f"rows={len(rows)} probabilities={len(model_probabilities)}"
        )
    return [
        evaluate_validation_metric_row(row, model_probability)
        for row, model_probability in zip(rows, model_probabilities, strict=True)
    ]
