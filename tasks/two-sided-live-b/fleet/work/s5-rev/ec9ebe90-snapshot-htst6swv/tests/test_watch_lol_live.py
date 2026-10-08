"""Watcher-only LoL script: Gamma tag and slug extract via the shared GRID helper."""

from watch_grid_live import extract_slug
from watch_lol_live import LOL_TAG_SLUG


def test_lol_listing_uses_the_league_of_legends_gamma_tag() -> None:
    assert LOL_TAG_SLUG == "league-of-legends"


def test_extract_slug_from_lol_url_or_bare_slug() -> None:
    url = "https://polymarket.com/esports/league-of-legends/north-american-challengers-league/lol-cpd-mvu-2026-08-28"
    assert extract_slug(url) == "lol-cpd-mvu-2026-08-28"
    assert extract_slug("lol-cpd-mvu-2026-08-28") == "lol-cpd-mvu-2026-08-28"
