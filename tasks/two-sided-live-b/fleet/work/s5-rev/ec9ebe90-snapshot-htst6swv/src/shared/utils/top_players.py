"""Top-net-worth player features shared by train (npm) and validation (playback)."""

from collections.abc import Sequence
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class TopPlayerFeatures:
    """Top-net-worth features for one game second: richest player and top three."""

    top1_nw_adv: int
    radiant_top1_nw_ratio: float
    dire_top1_nw_ratio: float
    top3_nw_adv: int
    radiant_top3_nw_ratio: float
    dire_top3_nw_ratio: float


ZERO_TOP = TopPlayerFeatures(
    top1_nw_adv=0,
    radiant_top1_nw_ratio=0.0,
    dire_top1_nw_ratio=0.0,
    top3_nw_adv=0,
    radiant_top3_nw_ratio=0.0,
    dire_top3_nw_ratio=0.0,
)

TOP_PLAYER_FIELD_NAMES = tuple(field.name for field in fields(TopPlayerFeatures))


@dataclass(frozen=True)
class _SideSums:
    """Top-1 and top-3 sums plus the side total for ratio denominators."""

    top1: int
    top3: int
    total: int


def _side_sums(networths: Sequence[int]) -> _SideSums:
    """Richest player, three richest summed, and the side total."""
    if not networths:
        return _SideSums(0, 0, 0)
    ordered = sorted(networths, reverse=True)
    return _SideSums(top1=ordered[0], top3=sum(ordered[:3]), total=sum(ordered))


def _ratio(part: int, denominator: int) -> float:
    # ponytail: 0.0 on a zero denominator; unreachable on 5v5 data
    return part / denominator if denominator else 0.0


def build_top_player_features(
    radiant: Sequence[int],
    dire: Sequence[int],
) -> TopPlayerFeatures:
    """Top-1/top-3 features; ratios are top-n divided by the rest of the side."""
    radiant_sums = _side_sums(radiant)
    dire_sums = _side_sums(dire)
    return TopPlayerFeatures(
        top1_nw_adv=radiant_sums.top1 - dire_sums.top1,
        radiant_top1_nw_ratio=_ratio(radiant_sums.top1, radiant_sums.total - radiant_sums.top1),
        dire_top1_nw_ratio=_ratio(dire_sums.top1, dire_sums.total - dire_sums.top1),
        top3_nw_adv=radiant_sums.top3 - dire_sums.top3,
        radiant_top3_nw_ratio=_ratio(radiant_sums.top3, radiant_sums.total - radiant_sums.top3),
        dire_top3_nw_ratio=_ratio(dire_sums.top3, dire_sums.total - dire_sums.top3),
    )


def build_top_player_features_over_total(
    radiant: Sequence[int],
    dire: Sequence[int],
) -> TopPlayerFeatures:
    """Top-1/top-3 features; ratios are top-n divided by the team total (LoL train)."""
    radiant_sums = _side_sums(radiant)
    dire_sums = _side_sums(dire)
    return TopPlayerFeatures(
        top1_nw_adv=radiant_sums.top1 - dire_sums.top1,
        radiant_top1_nw_ratio=_ratio(radiant_sums.top1, radiant_sums.total),
        dire_top1_nw_ratio=_ratio(dire_sums.top1, dire_sums.total),
        top3_nw_adv=radiant_sums.top3 - dire_sums.top3,
        radiant_top3_nw_ratio=_ratio(radiant_sums.top3, radiant_sums.total),
        dire_top3_nw_ratio=_ratio(dire_sums.top3, dire_sums.total),
    )


def top_player_feature_values(top: TopPlayerFeatures) -> dict[str, int | float]:
    """Flat catalog names for parquet rows and the live booster mapping."""
    return {item.name: getattr(top, item.name) for item in fields(TopPlayerFeatures)}
