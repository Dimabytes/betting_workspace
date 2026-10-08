"""Tournament clip from the Polymarket (BOx) title suffix."""

from trader.clip_rules import ClipTable, ClipTier, choose_clip

TABLE = ClipTable(
    default_usdc=5.0,
    tiers=(
        ClipTier(75.0, ("EPL World Series",)),
        ClipTier(300.0, ("BLAST Slam",)),
    ),
)


def test_group_stage_suffix_matches_the_name() -> None:
    choice = choose_clip(TABLE, "Dota 2: Heroic vs OG (BO1) - BLAST Slam Group Stage")
    assert choice.clip_usdc == 300.0
    assert choice.reason == "BLAST Slam"


def test_team_name_is_not_the_tournament() -> None:
    choice = choose_clip(TABLE, "Dota 2: BLAST Slam vs OG (BO1) - European Pro League")
    assert choice.clip_usdc == 5.0
    assert choice.reason == "default"


def test_missing_title_is_the_default() -> None:
    choice = choose_clip(TABLE, None)
    assert choice.clip_usdc == 5.0
    assert choice.reason == "default"


def test_two_hits_take_the_smaller_clip() -> None:
    table = ClipTable(
        default_usdc=5.0,
        tiers=(ClipTier(300.0, ("Slam",)), ClipTier(75.0, ("BLAST",))),
    )
    choice = choose_clip(table, "Dota 2: A vs B (BO1) - BLAST Slam")
    assert choice.clip_usdc == 75.0
    assert choice.reason == "BLAST"


def test_qualifier_containing_the_name_uses_that_clip() -> None:
    choice = choose_clip(
        TABLE,
        "Dota 2: A vs B (BO3) - BLAST Slam Southeast Asia Closed Qualifier Playoffs",
    )
    assert choice.clip_usdc == 300.0
    assert choice.reason == "BLAST Slam"
