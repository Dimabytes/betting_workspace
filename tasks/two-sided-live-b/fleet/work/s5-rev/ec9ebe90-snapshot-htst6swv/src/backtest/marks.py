"""Enriched fills, mid series, and the token valuation primitives built on them.

Leaf module: postprocessing and the wallet path both mark inventory, so the shapes
and the mark functions live below both instead of one reaching into the other.
"""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Literal

from backtest.telemetry import FillRecord

ReferenceSource = Literal["mid", "settlement"]


@dataclass(frozen=True)
class EnrichedFill(FillRecord):
    """A recorded fill plus post-processed fee, rebate, and markout columns.

    board_age_seconds is the fill's age behind the most recent board tick in
    the same match, NaN when no board tick preceded it — the board fill slice
    filters on it.
    """

    fill_model: str
    fee_base: float
    maker_rebate: float
    taker_fee: float
    reference_30s: float
    reference_300s: float
    markout_30s: float
    markout_300s: float
    reference_source_30s: ReferenceSource
    reference_source_300s: ReferenceSource
    board_age_seconds: float


@dataclass(frozen=True)
class MidSeries:
    """Pause-adjusted wall-clock mids for one match, only market_status == ok rows."""

    timestamps_ns: tuple[int, ...]
    market_ps: tuple[float, ...]


def lookup_reference_mid(series: MidSeries, target_ns: int) -> float | None:
    """Latest ok mid at or before target_ns; None when the series is empty or all later."""
    if not series.timestamps_ns:
        return None
    index = bisect_right(series.timestamps_ns, target_ns) - 1
    if index < 0:
        return None
    return series.market_ps[index]


def settlement_value_for_token(
    token_index: int, radiant_token_index: int, radiant_win: bool
) -> float:
    """Binary settlement of one token: 1 if that side won, else 0."""
    token_is_radiant = token_index == radiant_token_index
    if token_is_radiant:
        return 1.0 if radiant_win else 0.0
    return 0.0 if radiant_win else 1.0


def token_mid(market_p_radiant: float, token_index: int, radiant_token_index: int) -> float:
    """Convert a radiant paired mid into the mid of the filled token."""
    if token_index == radiant_token_index:
        return market_p_radiant
    return 1.0 - market_p_radiant


def cvar_tail_count(n: int) -> int:
    """How many matches enter CVaR 5%: ceil(5% of n), or 0 when n is 0."""
    if n == 0:
        return 0
    return max(1, ceil(0.05 * n))


def calculate_cvar_5(pnls: Sequence[float]) -> float:
    """Mean of the worst ceil(5% of n) match PnLs; 0 when the list is empty."""
    if not pnls:
        return 0.0
    worst = sorted(pnls)[: cvar_tail_count(len(pnls))]
    return sum(worst) / len(worst)
