"""Tests for team name normalization, aliases, and fuzzy scoring."""

from shared.constants.lol import LOL_TEAM_ALIASES
from shared.utils.team_names import (
    PAIR_SCORE_MIN,
    SIDE_SCORE_MIN,
    STOPWORDS,
    TEAM_ALIASES,
    build_name_probes,
    normalize_team_name,
    orient_outcomes,
    pick_pair_orientation,
    score_team_name,
    sort_name_tokens,
)


def test_constants() -> None:
    """Verify threshold constants and structure of aliases/stopwords."""
    assert PAIR_SCORE_MIN == 0.82
    assert SIDE_SCORE_MIN == 0.72
    assert "team" in STOPWORDS
    assert "gaming" in STOPWORDS
    assert "navi" in TEAM_ALIASES
    assert "natus vincere" in TEAM_ALIASES["navi"]


def test_normalize_team_name() -> None:
    """Normalize team names by stripping punctuation, casing, and stopwords."""
    assert normalize_team_name("Team Spirit") == "spirit"
    assert normalize_team_name("Natus Vincere!") == "natus vincere"
    assert normalize_team_name("G2 x iG") == "g2 x ig"
    assert normalize_team_name("") == ""
    assert normalize_team_name(None) == ""
    # Fallback to original tokens when all words are stopwords
    assert normalize_team_name("The Team") == "the team"
    assert normalize_team_name("ЯЧЁ123") == "яче123"
    assert normalize_team_name("яче123") == "яче123"
    assert normalize_team_name("yache123") == "yache123"
    assert normalize_team_name("123") == "123"


def test_build_name_probes() -> None:
    """Return normalized probe and its known alias variations without duplicates."""
    probes = build_name_probes("NaVi", TEAM_ALIASES)
    assert "navi" in probes
    assert "natus vincere" in probes
    assert len(probes) == len(set(probes))

    # Unknown team returns single normalized probe
    assert build_name_probes("Unknown Squad", TEAM_ALIASES) == ("unknown squad",)


def test_sort_name_tokens() -> None:
    """Sort tokens alphabetically."""
    assert sort_name_tokens("beta alpha gamma") == "alpha beta gamma"
    assert sort_name_tokens("spirit") == "spirit"


def test_score_team_name_exact_and_fuzzy() -> None:
    """Score team names with exact, alias, and token-order tolerance."""
    assert score_team_name("Team Liquid", {"liquid"}, TEAM_ALIASES) == 1.0
    assert score_team_name("NaVi", {"natus vincere"}, TEAM_ALIASES) == 1.0

    # Token reordering
    assert score_team_name("Boys Boom", {"boom boys"}, TEAM_ALIASES) == 1.0

    # Unrelated teams score low
    assert score_team_name("Team Liquid", {"betboom"}, TEAM_ALIASES) < 0.5

    # Empty observed set gives 0.0
    assert score_team_name("Team Liquid", set(), TEAM_ALIASES) == 0.0


def test_score_team_name_aliases_are_symmetric() -> None:
    """Alias table keys and values score in both directions, including Inner Circle."""
    inner = normalize_team_name("Inner Circle")
    long_name = normalize_team_name("Inner Circle x Insanity")
    assert score_team_name("Inner Circle x Insanity", {inner}, TEAM_ALIASES) == 1.0
    assert score_team_name("Inner Circle", {long_name}, TEAM_ALIASES) == 1.0
    assert score_team_name("NaVi", {normalize_team_name("Natus Vincere")}, TEAM_ALIASES) == 1.0
    assert score_team_name("Natus Vincere", {normalize_team_name("NaVi")}, TEAM_ALIASES) == 1.0
    assert score_team_name("yache123", {normalize_team_name("ЯЧЁ123")}, TEAM_ALIASES) == 1.0
    assert score_team_name("ЯЧЁ123", {normalize_team_name("yache123")}, TEAM_ALIASES) == 1.0
    assert score_team_name("yache123", {normalize_team_name("123")}, TEAM_ALIASES) != 1.0
    assert score_team_name("123", {normalize_team_name("yache123")}, TEAM_ALIASES) != 1.0
    assert (
        score_team_name("Yakutou Brothers", {normalize_team_name("Yakult Brothers")}, TEAM_ALIASES)
        == 1.0
    )
    assert (
        score_team_name("Tearlaments", {normalize_team_name("YB.Tearlaments")}, TEAM_ALIASES) == 1.0
    )
    assert (
        score_team_name("Yakult Brothers", {normalize_team_name("Tearlaments")}, TEAM_ALIASES)
        != 1.0
    )
    assert (
        score_team_name("Yakutou Brothers", {normalize_team_name("YB.Tearlaments")}, TEAM_ALIASES)
        != 1.0
    )
    assert score_team_name("LGD Gaming", {normalize_team_name("LGD.Pinghu")}, TEAM_ALIASES) == 1.0
    assert score_team_name("G-Time", {normalize_team_name("Ec1ipse")}, TEAM_ALIASES) == 1.0
    assert score_team_name("Team Syntax", {normalize_team_name("Synapse")}, TEAM_ALIASES) != 1.0


def test_pick_pair_orientation_tie_thresholds_and_winner() -> None:
    """Exact ties and failed thresholds reject; otherwise the higher total wins."""
    assert pick_pair_orientation(0.9, 0.9, 0.9, 0.9) is None
    assert pick_pair_orientation(0.99, 0.5, 0.4, 0.4) is None
    assert pick_pair_orientation(0.95, 0.95, 0.5, 0.5) is True
    assert pick_pair_orientation(0.5, 0.5, 0.95, 0.95) is False


def test_lol_aliases_orient_ruddy_sack_and_skt() -> None:
    """LoL live aliases bind Ruddy Sack and SKT/T1; the Dota table does not."""
    assert (
        orient_outcomes(
            "Deer Gaming",
            "The Ruddy Sack",
            "Deer Gaming",
            "Ruddy Corporation",
            LOL_TEAM_ALIASES,
        )
        is True
    )
    assert (
        orient_outcomes(
            "Deer Gaming",
            "The Ruddy Sack",
            "Deer Gaming",
            "Ruddy Corporation",
            TEAM_ALIASES,
        )
        is None
    )
    assert orient_outcomes("SKT", "Gen.G", "T1", "Gen.G", LOL_TEAM_ALIASES) is True
    assert orient_outcomes("SKT", "Gen.G", "T1", "Gen.G", TEAM_ALIASES) is None
