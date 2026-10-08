"""Convert Dota 2 level-up timelines to cumulative XP advantages."""

from bisect import bisect_right
from itertools import pairwise

from shared.constants.dota import LEVEL_XP
from shared.types.stratz import StratzPlayer
from shared.utils.level_xp import cumulative_xp, xp_advantage


def experience_at_level(level: int) -> int:
    """Return cumulative XP at a Dota 2 level. Steam draft players are level 0 (0 XP)."""
    return cumulative_xp(LEVEL_XP, level)


def level_at_second(level_seconds: list[int] | tuple[int, ...], second: int) -> int:
    """Return the level reached by a player at one game second."""
    return bisect_right(level_seconds, second)


def radiant_xp_advantage(radiant_levels: list[int], dire_levels: list[int]) -> int:
    """Return cumulative Radiant XP minus cumulative Dire XP."""
    return xp_advantage(LEVEL_XP, radiant_levels, dire_levels)


def radiant_xp_advantage_at_second(players: list[StratzPlayer], second: int) -> int:
    """Return the level-based Radiant XP advantage at one game second."""
    radiant_levels: list[int] = []
    dire_levels: list[int] = []
    for player in players:
        level = level_at_second(player["stats"]["level"], second)
        if player["isRadiant"]:
            radiant_levels.append(level)
        else:
            dire_levels.append(level)
    return radiant_xp_advantage(radiant_levels, dire_levels)


def is_level_timeline_monotonic(player: StratzPlayer) -> bool:
    """Report whether a player's level-up seconds never go backwards."""
    level_seconds = player["stats"]["level"]
    return all(left <= right for left, right in pairwise(level_seconds))
