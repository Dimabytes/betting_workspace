from itertools import pairwise
from typing import cast

import pytest

from shared.constants.dota import LEVEL_XP
from shared.types.stratz import StratzPlayer
from shared.utils.dota_levels import (
    experience_at_level,
    is_level_timeline_monotonic,
    level_at_second,
    radiant_xp_advantage,
    radiant_xp_advantage_at_second,
)


def build_player(level_seconds: list[int], level: int, radiant: bool) -> StratzPlayer:
    """Build the level fields used by level conversion tests."""
    return cast(
        StratzPlayer,
        {
            "level": level,
            "isRadiant": radiant,
            "stats": {"level": level_seconds},
        },
    )


def test_experience_at_level_uses_level_boundaries() -> None:
    """Level zero and one are zero XP; level thirty uses the final threshold."""
    assert experience_at_level(0) == 0
    assert experience_at_level(1) == 0
    assert experience_at_level(30) == 64400


def test_experience_at_level_rejects_out_of_range_levels() -> None:
    """Reject levels outside the Dota 2 table, including negatives."""
    with pytest.raises(ValueError, match="between 0 and"):
        experience_at_level(-1)
    with pytest.raises(ValueError, match="between 0 and"):
        experience_at_level(31)


def test_level_xp_table_is_complete_and_strictly_increasing() -> None:
    """Keep one threshold for every level and preserve its order."""
    assert len(LEVEL_XP) == 30
    assert all(left < right for left, right in pairwise(LEVEL_XP))


def test_level_at_second_counts_level_ups_through_second() -> None:
    """Count a level-up at its exact second, including a pre-horn level."""
    level_seconds = [-89, 96, 170]
    assert [level_at_second(level_seconds, second) for second in (-100, -89, 95, 96, 170)] == [
        0,
        1,
        1,
        2,
        3,
    ]


def test_radiant_xp_advantage_sums_team_thresholds() -> None:
    """Subtract five level-four heroes from five level-five heroes."""
    assert radiant_xp_advantage([5] * 5, [4] * 5) == 3000


def test_radiant_xp_advantage_treats_level_zero_as_zero_xp() -> None:
    """Steam draft players are level 0; that is 0 XP, not an error."""
    assert radiant_xp_advantage([0] * 5, [0] * 5) == 0
    assert radiant_xp_advantage([2] * 5, [0] * 5) == 5 * experience_at_level(2)


def test_radiant_xp_advantage_at_second_uses_player_timelines() -> None:
    """Convert each side's level timelines at one game second."""
    players = [
        build_player([-1, 60], 2, True),
        build_player([-1], 1, False),
    ]
    assert radiant_xp_advantage_at_second(players, 60) == 240


def test_level_timeline_accepts_repeated_seconds() -> None:
    """Accept multiple level-ups recorded in one second."""
    assert is_level_timeline_monotonic(build_player([-1, 60, 60], 3, True)) is True


def test_level_timeline_rejects_invalid_order() -> None:
    """Reject a level-up recorded before the previous level-up."""
    assert is_level_timeline_monotonic(build_player([-1, 10, 9], 3, True)) is False


def test_level_timeline_allows_a_short_timeline() -> None:
    """A missing late level-up does not reject the match."""
    assert is_level_timeline_monotonic(build_player([-1, 10, 20], 4, True)) is True
