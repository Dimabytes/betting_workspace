from typing import cast

from shared.types.stratz import StratzMatch
from shared.utils.stratz import is_usable_stratz_match, stratz_match_unusable_reason


def test_usable_match_with_moving_leads() -> None:
    """A real match changes its leads within the first minutes."""
    match = cast(
        StratzMatch,
        {
            "radiantNetworthLeads": [-150, -150, 88, 402, 900],
            "radiantExperienceLeads": [0, 0, 120, 60, -300],
        },
    )

    assert stratz_match_unusable_reason(match) is None
    assert is_usable_stratz_match(match) is True


def test_flat_filled_prefix_is_unusable() -> None:
    """A partial STRATZ parse backfills the prefix with one repeated value."""
    match = cast(
        StratzMatch,
        {
            "radiantNetworthLeads": [-29841] * 60 + [-32669, -36511],
            "radiantExperienceLeads": [0] * 60 + [-218, 915],
        },
    )

    assert stratz_match_unusable_reason(match) == "flat_edge"
    assert is_usable_stratz_match(match) is False


def test_zero_padded_xp_tail_is_unusable() -> None:
    """A truncated XP series is padded with zeros while networth keeps running."""
    match = cast(
        StratzMatch,
        {
            "radiantNetworthLeads": [0, 412, -298, 2735, -9724, 16452, 30027, -21616],
            "radiantExperienceLeads": [0, 100, -253, 0, 0, 0, 0, 0],
        },
    )

    assert stratz_match_unusable_reason(match) == "flat_edge"
    assert is_usable_stratz_match(match) is False


def test_reset_tail_is_unusable() -> None:
    """Finish rolled back to exact XP 0 after a live stretch rejects the match."""
    match = cast(
        StratzMatch,
        {
            "radiantNetworthLeads": [0, 412, -298, 2735, -9724, 16452, 30027],
            "radiantExperienceLeads": [0, 100, -253, 500, 2000, 3000, 0],
        },
    )

    assert stratz_match_unusable_reason(match) == "reset_tail"
    assert is_usable_stratz_match(match) is False


def test_missing_leads_are_unusable() -> None:
    """Absent or too-short lead arrays stay unusable as before."""
    short = cast(StratzMatch, {"radiantNetworthLeads": [0], "radiantExperienceLeads": [0]})
    assert stratz_match_unusable_reason(short) == "missing_leads"
    assert stratz_match_unusable_reason(None) == "missing_leads"


def test_backwards_level_timeline_is_unusable() -> None:
    """A player whose level-up seconds go backwards rejects the match in collect."""
    match = cast(
        StratzMatch,
        {
            "radiantNetworthLeads": [-150, -150, 88, 402, 900],
            "radiantExperienceLeads": [0, 0, 120, 60, -300],
            "players": [{"stats": {"level": [120, 60]}}],
        },
    )

    assert stratz_match_unusable_reason(match) == "level_timeline"
    assert is_usable_stratz_match(match) is False
