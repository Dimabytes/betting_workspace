"""Shared cumulative XP from a per-level threshold table."""


def cumulative_xp(level_xp: tuple[int, ...], level: int) -> int:
    """Cumulative XP at `level`. Level 0 is 0 XP (Dota draft)."""
    max_level = len(level_xp)
    if level < 0 or level > max_level:
        raise ValueError(f"level must be between 0 and {max_level}: {level}")
    if level == 0:
        return 0
    return level_xp[level - 1]


def xp_advantage(
    level_xp: tuple[int, ...],
    first_levels: list[int],
    second_levels: list[int],
) -> int:
    """Sum of cumulative XP on `first_levels` minus `second_levels`."""
    first_xp = sum(cumulative_xp(level_xp, level) for level in first_levels)
    second_xp = sum(cumulative_xp(level_xp, level) for level in second_levels)
    return first_xp - second_xp
