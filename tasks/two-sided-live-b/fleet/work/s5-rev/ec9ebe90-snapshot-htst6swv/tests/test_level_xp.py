"""Level-XP table and shared arithmetic. No V5, no network."""

import json
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest

from shared.constants.lol import LOL_LEVEL_XP
from shared.utils.dota_levels import experience_at_level
from shared.utils.level_xp import cumulative_xp, xp_advantage

BRACKETS_PATH = Path(__file__).resolve().parent / "fixtures" / "lol" / "level_xp_brackets.json"


def test_lol_level_xp_length_and_order() -> None:
    """LoL table has 20 strictly increasing cumulative thresholds."""
    assert len(LOL_LEVEL_XP) == 20
    assert LOL_LEVEL_XP[18] == 20340
    assert LOL_LEVEL_XP[19] == 22420
    assert all(left < right for left, right in pairwise(LOL_LEVEL_XP))


def test_lol_level_xp_falls_inside_committed_brackets() -> None:
    """Each table value sits in the offline (max at L-1, min at L] bracket."""
    raw = json.loads(BRACKETS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    brackets = cast(list[object], raw)
    assert len(brackets) == 20
    for index, item in enumerate(brackets):
        assert isinstance(item, dict)
        row = cast(dict[str, object], item)
        level_raw = row["level"]
        min_raw = row["min_xp_at_level"]
        max_prev = row["max_xp_at_prev"]
        assert isinstance(level_raw, int)
        assert isinstance(min_raw, int)
        assert level_raw == index + 1
        value = LOL_LEVEL_XP[index]
        if max_prev is None:
            assert value == min_raw
            continue
        assert isinstance(max_prev, int)
        assert max_prev < value <= min_raw


def test_cumulative_xp_rejects_above_lol_table() -> None:
    """Level 21 is past the LoL table and must raise."""
    assert cumulative_xp(LOL_LEVEL_XP, 20) == 22420
    with pytest.raises(ValueError, match="between 0 and 20"):
        cumulative_xp(LOL_LEVEL_XP, 21)


def test_xp_advantage_on_known_five_v_five() -> None:
    """Five level-3 blues minus five level-2 reds is 1900 XP."""
    assert xp_advantage(LOL_LEVEL_XP, [3] * 5, [2] * 5) == 5 * (660 - 280)


def test_dota_experience_at_level_thirty_unchanged() -> None:
    """Dota public API still returns 64400 XP at level 30 after the extract."""
    assert experience_at_level(30) == 64400
    assert experience_at_level(0) == 0
    assert experience_at_level(1) == 0
