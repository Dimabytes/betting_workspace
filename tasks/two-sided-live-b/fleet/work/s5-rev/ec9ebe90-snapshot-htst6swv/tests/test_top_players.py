from dataclasses import fields

import pytest

from shared.utils.top_players import (
    ZERO_TOP,
    build_top_player_features,
    build_top_player_features_over_total,
    top_player_feature_values,
)


def test_top1_is_the_richest_player_not_the_first() -> None:
    """Top-1 is the richest player; the ratio divides it by the rest of the side."""
    features = build_top_player_features([500, 1200, 800], [300, 900, 400])

    assert features.top1_nw_adv == 300
    assert features.radiant_top1_nw_ratio == 1200 / (500 + 800)
    assert features.dire_top1_nw_ratio == 900 / (300 + 400)


def test_equal_networth_needs_no_tie_break() -> None:
    """Tied top net worths give the same features whatever the input order."""
    features = build_top_player_features([100, 100], [50, 50])
    reversed_features = build_top_player_features([100, 100], [50, 50][::-1])

    assert features == reversed_features
    assert features.top1_nw_adv == 50
    assert features.radiant_top1_nw_ratio == 1.0
    assert features.dire_top1_nw_ratio == 1.0


def test_advantage_sign_follows_top1_gap() -> None:
    """Positive when Radiant top-1 is richer; negative when Dire top-1 is."""
    radiant_lead = build_top_player_features([200], [50])
    dire_lead = build_top_player_features([50], [200])

    assert radiant_lead.top1_nw_adv == 150
    assert dire_lead.top1_nw_adv == -150


def test_short_side_falls_back_to_zero() -> None:
    """Missing ranks and a zero rest-sum ratio return 0 so 1-2 player fixtures work."""
    features = build_top_player_features([1000], [])

    assert features.top1_nw_adv == 1000
    assert features.radiant_top1_nw_ratio == 0.0
    assert features.dire_top1_nw_ratio == 0.0


def test_top_player_feature_values_follow_dataclass_fields() -> None:
    """Parquet/live flatten uses TopPlayerFeatures field names in dataclass order."""
    values = top_player_feature_values(ZERO_TOP)
    assert list(values) == [item.name for item in fields(ZERO_TOP)]
    assert values["top1_nw_adv"] == 0
    assert values["radiant_top1_nw_ratio"] == 0.0


def test_equal_spawn_gold_is_quarter_of_the_rest() -> None:
    """Five equal 500s: Dota rest formula is 500 / 2000."""
    features = build_top_player_features([500] * 5, [500] * 5)
    assert features.radiant_top1_nw_ratio == 0.25
    assert features.dire_top1_nw_ratio == 0.25
    assert features.top1_nw_adv == 0


def test_equal_spawn_gold_over_total_is_one_fifth() -> None:
    """Five equal 500s: LoL over-total formula is 500 / 2500."""
    features = build_top_player_features_over_total([500] * 5, [500] * 5)
    assert features.radiant_top1_nw_ratio == 0.20
    assert features.dire_top1_nw_ratio == 0.20
    assert features.top1_nw_adv == 0


def test_over_total_divides_by_the_side_sum() -> None:
    """LoL ratio is top1 / team total; Dota rest stays top1 / (total - top1)."""
    features = build_top_player_features_over_total([500, 1200, 800], [300, 900, 400])
    assert features.radiant_top1_nw_ratio == 1200 / 2500
    assert features.dire_top1_nw_ratio == 900 / 1600
    assert features.top1_nw_adv == 300


def test_top3_sums_the_three_richest_and_ratios_the_rest() -> None:
    """[100..500] has top3 1200 and ratio 1200/300; advantage is Radiant minus Dire."""
    features = build_top_player_features([100, 200, 300, 400, 500], [100, 100, 100, 50, 50])

    assert features.top3_nw_adv == 1200 - 300
    assert features.radiant_top3_nw_ratio == pytest.approx(1200 / 300)
    assert features.dire_top3_nw_ratio == pytest.approx(300 / 100)


def test_top3_zero_remainder_yields_zero_ratio() -> None:
    """A side where the top-3 hold all the net worth has a zero ratio, not inf."""
    features = build_top_player_features([100, 200, 300], [50])

    assert features.radiant_top3_nw_ratio == 0.0
    assert features.top3_nw_adv == 600 - 50
    assert features.dire_top3_nw_ratio == 0.0


def test_top3_ignores_input_order() -> None:
    """Sorting happens inside: reversed nets give identical features."""
    features = build_top_player_features([500, 100, 400, 200, 300], [100, 200])
    reversed_features = build_top_player_features([300, 500, 200, 100, 400], [200, 100])
    assert features == reversed_features


def test_top3_values_share_the_top_player_flatten() -> None:
    """Parquet/live flatten emits the top-3 fields from the same dataclass."""
    values = top_player_feature_values(ZERO_TOP)
    assert list(values) == [item.name for item in fields(ZERO_TOP)]
    assert values["top3_nw_adv"] == 0
    assert values["radiant_top3_nw_ratio"] == 0.0
