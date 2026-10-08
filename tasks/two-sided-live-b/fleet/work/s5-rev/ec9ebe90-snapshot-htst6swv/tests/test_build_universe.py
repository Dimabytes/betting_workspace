"""Contract classification and token columns of the Dota universe builder."""

from typing import Any

import pandas as pd
import pytest
from polymarket.models.gamma.event import Event
from polymarket.models.gamma.market import Market

from collect.s01_build_universe import build_frames, classify_contract, classify_inventory

CONDITION_ID = "0x" + "1" * 64


def build_market(
    question: str,
    sports_market_type: str | None,
    condition_id: str,
    token_ids: list[str] | None,
    gamma_complete: bool,
) -> Market:
    """One raw Gamma market with only the fields the universe builder reads."""
    payload: dict[str, Any] = {
        "id": condition_id,
        "question": question,
        "conditionId": condition_id,
        "outcomes": ["Yes", "No"],
        "gameStartTime": "2026-01-01T00:00:00Z",
    }
    if sports_market_type is not None:
        payload["sportsMarketType"] = sports_market_type
    if token_ids is not None:
        payload["clobTokenIds"] = token_ids
    if gamma_complete:
        payload["slug"] = f"slug-{condition_id}"
        payload["closedTime"] = "2026-01-01T01:00:00Z"
        payload["secondsDelay"] = 3
    return Market.model_validate(payload)


def build_event(title: str, markets: list[Market]) -> Event:
    """One raw Gamma event holding already-parsed markets."""
    event = Event.model_validate({"id": "e1", "title": title, "slug": "e1", "markets": []})
    return event.model_copy(update={"markets": tuple(markets)})


def classify_question(question: str, sports_market_type: str | None, has_teams: bool) -> str:
    """Contract kind for one question, ignoring the game number."""
    market = build_market(question, sports_market_type, CONDITION_ID, ["t0", "t1"], False)
    return classify_contract(market, has_teams).contract_kind


def test_child_moneyline_without_a_game_number_is_a_map_winner() -> None:
    """The sports type alone decides the kind; the number stays unknown."""
    market = build_market("Will Team A win?", "child_moneyline", CONDITION_ID, ["t0", "t1"], False)

    classification = classify_contract(market, True)

    assert classification.contract_kind == "map_winner"
    assert classification.game_number is None


def test_game_winner_question_carries_its_game_number() -> None:
    """A Game N Winner question is a map winner even without a sports type."""
    market = build_market("Dota 2: A vs B - Game 3 Winner", None, CONDITION_ID, ["t0", "t1"], False)

    classification = classify_contract(market, True)

    assert classification.contract_kind == "map_winner"
    assert classification.game_number == 3


def test_scoreline_beats_the_moneyline_rule() -> None:
    """A 2-0 scoreline market is excluded even when it looks like a moneyline."""
    assert classify_question("Dota 2: A vs B 2-0", "moneyline", True) == "other"


@pytest.mark.parametrize(
    ("question", "sports_market_type", "has_teams", "expected"),
    [
        ("Anything", "moneyline", True, "series_winner"),
        ("Dota 2: A vs B", None, True, "series_winner"),
        ("Anything", "moneyline", False, "other"),
        ("Dota 2: A vs B", None, False, "other"),
        ("Who wins the tournament?", None, True, "other"),
    ],
)
def test_series_winner_needs_teams_and_a_moneyline_or_match_question(
    question: str, sports_market_type: str | None, has_teams: bool, expected: str
) -> None:
    """Series winners come from a moneyline type or a matchup question, teams required."""
    assert classify_question(question, sports_market_type, has_teams) == expected


@pytest.mark.parametrize(
    ("kind", "best_of", "expected"),
    [
        ("map_winner", 3, "candidate"),
        ("map_winner", None, "candidate"),
        ("series_winner", 1, "candidate"),
        ("series_winner", 3, "series_linking_required"),
        ("series_winner", None, "series_linking_required"),
        ("other", 3, "excluded"),
    ],
)
def test_inventory_status_follows_kind_and_best_of(
    kind: str, best_of: int | None, expected: str
) -> None:
    """BO1 series winners are the only series markets usable without linking."""
    assert classify_inventory(kind, best_of) == expected  # pyright: ignore[reportArgumentType]


def build_universe_frame(markets: list[Market]) -> pd.DataFrame:
    """Universe rows for one BO3 matchup event."""
    return build_frames([build_event("Dota 2: A vs B (BO3)", markets)])


def test_token_columns_are_filled_without_the_other_gamma_fields() -> None:
    """A market that fails the Gamma parse still publishes its token pair."""
    market = build_market("Game 1 Winner", "child_moneyline", CONDITION_ID, ["yes", "no"], False)

    row = build_universe_frame([market]).iloc[0]

    assert row["token_id_0"] == "yes"
    assert row["token_id_1"] == "no"
    assert row["market_slug"] is None
    assert pd.isna(row["seconds_delay"])
    assert row["market_closed_at"] is None


def test_a_half_missing_pair_publishes_no_token() -> None:
    """One token alone is not a tradable pair, so both columns stay empty."""
    market = build_market("Game 1 Winner", "child_moneyline", CONDITION_ID, ["yes"], False)

    row = build_universe_frame([market]).iloc[0]

    assert row["token_id_0"] is None
    assert row["token_id_1"] is None


def test_complete_gamma_market_publishes_tokens_and_gamma_fields() -> None:
    """The parsed path keeps the same token order as the raw outcomes."""
    market = build_market("Game 1 Winner", "child_moneyline", CONDITION_ID, ["yes", "no"], True)

    row = build_universe_frame([market]).iloc[0]

    assert (row["token_id_0"], row["token_id_1"]) == ("yes", "no")
    assert row["market_slug"] == f"slug-{CONDITION_ID}"
    assert row["seconds_delay"] == 3
    assert row["market_closed_at"] == "2026-01-01T01:00:00Z"
