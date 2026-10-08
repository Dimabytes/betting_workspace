"""Dota 2 game constants."""

# Cumulative XP needed to enter each level, 1..30. Derived from the STRATZ cache: the
# cumulative sum of experienceEvents at each playerUpdateLevelEvents second. Verified
# stable across the whole dataset window (2025-10 .. 2026-08).
LEVEL_XP: tuple[int, ...] = (
    0,
    240,
    640,
    1160,
    1760,
    2440,
    3200,
    4000,
    4900,
    5900,
    7000,
    8200,
    9500,
    10900,
    12400,
    14000,
    15700,
    17500,
    19400,
    21400,
    23600,
    26000,
    28600,
    31400,
    34400,
    38400,
    43400,
    49400,
    56400,
    64400,
)
MAX_LEVEL = len(LEVEL_XP)
