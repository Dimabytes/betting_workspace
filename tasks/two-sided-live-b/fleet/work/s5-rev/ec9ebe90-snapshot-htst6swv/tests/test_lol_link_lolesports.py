"""Synthetic lolesports fixtures for the LoL link stage. No network."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import httpx
import pandas as pd
import pytest

import lol.lolesports_match as lol_match
from lol.constants import (
    LOL_MAX_CONCURRENCY,
    LOL_SERIES_START_WINDOW_SECONDS,
    REASON_ACCEPTED,
    REASON_BO_MISMATCH,
    REASON_GAME_WINNER,
    REASON_LEAGUE_MISMATCH,
    REASON_MATCH_WINNER_DECIDER,
    REASON_MISSING_SIDE,
    REASON_MULTIPLE_ELIGIBLE_SERIES,
    REASON_NO_CANDIDATES_IN_WINDOW,
    REASON_ORIENTATION_AMBIGUOUS,
    REASON_SERIES_CLAIM_TIE,
    REASON_SERIES_CLAIMED_BY_RICHER_EVENT,
    REASON_UNRESOLVED_MARKET,
    REASON_UNSUPPORTED_FALLBACK,
)
from lol.lolesports_match import score_lol_team_name
from lol.types import LolContractKind, LolGameRow, LolLinkAuditRow, LolLinkRow, LolUniverseMarketRow
from shared.constants.lol import LOL_TEAM_ALIASES
from shared.utils.team_names import normalize_team_name

BASE_TS = int(datetime(2026, 3, 1, 12, 0, tzinfo=UTC).timestamp())
BASE_TIME = "2026-03-01T12:00:00Z"
TEAM_A_ID = "esports-t1"
TEAM_B_ID = "esports-geng"


class LinkResultView(Protocol):
    """Return value of `link_events`."""

    links: Sequence[LolLinkRow]
    audit: Sequence[LolLinkAuditRow]


class LinkModule(Protocol):
    """Importable behavior from the numbered LoL link stage."""

    def link_events(
        self, universe_rows: Sequence[LolUniverseMarketRow], game_rows: Sequence[LolGameRow]
    ) -> LinkResultView:
        """Match Polymarket events to completed lolesports maps."""
        ...

    def build_game_row(self, event_details: object, window: object) -> LolGameRow | None:
        """Build one games.parquet row from getEventDetails plus a first window."""
        ...

    def write_gzip_json(self, path: Path, payload: object) -> None:
        """Write one gzip JSON cache file with stable metadata."""
        ...

    def link_lolesports(
        self,
        universe_path: Path,
        raw_dir: Path,
        games_path: Path,
        links_dir: Path,
        fetch: bool,
    ) -> None:
        """Optionally fetch lolesports, then write games/links/audit parquets."""
        ...


def load_link() -> LinkModule:
    """Import the numbered LoL link module through its typed test surface."""
    return cast(LinkModule, cast(object, import_module("lol.03_link_lolesports")))


def encode_list(values: list[str]) -> str:
    """Stable JSON list for parquet string columns."""
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def make_universe_row(
    event_id: str,
    market_id: str,
    condition_id: str,
    team_a: str | None,
    team_b: str | None,
    best_of: int | None,
    league: str | None,
    scheduled_ts: int | None,
    game_number: int | None,
    contract_kind: LolContractKind,
    included: bool,
    reason: str,
    outcomes: list[str],
    resolved_outcome: str | None,
    resolved_outcome_index: int | None,
) -> LolUniverseMarketRow:
    """Build one universe parquet row for matching tests."""
    return {
        "event_id": event_id,
        "event_slug": f"event-{event_id}",
        "market_id": market_id,
        "condition_id": condition_id,
        "question": f"LoL: {team_a} vs {team_b}",
        "group_item_title": "Game",
        "sports_market_type": "child_moneyline",
        "outcomes_json": encode_list(outcomes),
        "clob_token_ids_json": encode_list([f"tok-{market_id}-a", f"tok-{market_id}-b"]),
        "team_a": team_a,
        "team_b": team_b,
        "game_number": game_number,
        "best_of": best_of,
        "league": league,
        "scheduled_time": BASE_TIME,
        "scheduled_ts": scheduled_ts,
        "market_start_time": BASE_TIME,
        "market_end_time": BASE_TIME,
        "resolved_outcome": resolved_outcome,
        "resolved_outcome_index": resolved_outcome_index,
        "contract_kind": contract_kind,
        "included": included,
        "reason": reason,
    }


def included_game_n(
    event_id: str,
    game_number: int,
    team_a: str,
    team_b: str,
    best_of: int | None,
    league: str | None,
    scheduled_ts: int | None,
) -> LolUniverseMarketRow:
    """Resolved included Game N Winner for the default T1/Gen.G pair."""
    return make_universe_row(
        event_id,
        f"{event_id}-g{game_number}",
        f"cid-{event_id}-g{game_number}",
        team_a,
        team_b,
        best_of,
        league,
        scheduled_ts,
        game_number,
        "game_winner",
        True,
        REASON_GAME_WINNER,
        [team_a, team_b],
        team_a,
        0,
    )


def included_match_winner(
    event_id: str,
    team_a: str,
    team_b: str,
    best_of: int | None,
    league: str | None,
    scheduled_ts: int | None,
    game_number: int | None,
) -> LolUniverseMarketRow:
    """Resolved included Match Winner decider row."""
    return make_universe_row(
        event_id,
        f"{event_id}-mw",
        f"cid-{event_id}-mw",
        team_a,
        team_b,
        best_of,
        league,
        scheduled_ts,
        game_number,
        "match_winner",
        True,
        REASON_MATCH_WINNER_DECIDER,
        [team_a, team_b],
        team_a,
        0,
    )


def make_game(
    esports_game_id: str,
    esports_match_id: str,
    game_number: int,
    team_a_name: str,
    team_b_name: str,
    blue_id: str,
    red_id: str,
    start_ts: int | None,
    best_of: int | None,
    league_slug: str | None,
    team_a_wins: int | None,
    team_b_wins: int | None,
) -> LolGameRow:
    """Build one completed lolesports map row."""
    start_time = BASE_TIME if start_ts is not None else None
    return {
        "esports_game_id": esports_game_id,
        "esports_match_id": esports_match_id,
        "league_slug": league_slug,
        "league_name": "LCK" if league_slug == "lck" else league_slug,
        "start_time": start_time,
        "start_ts": start_ts,
        "best_of": best_of,
        "game_number": game_number,
        "team_a_id": TEAM_A_ID,
        "team_a_name": team_a_name,
        "team_a_code": team_a_name[:3].upper() if team_a_name else None,
        "team_b_id": TEAM_B_ID,
        "team_b_name": team_b_name,
        "team_b_code": team_b_name[:3].upper() if team_b_name else None,
        "team_a_game_wins": team_a_wins,
        "team_b_game_wins": team_b_wins,
        "blue_esports_team_id": blue_id,
        "red_esports_team_id": red_id,
        "loading_anchor": "2026-03-01T12:05:00Z",
        "loading_anchor_ts": BASE_TS + 300,
        "patch_version": "14.5",
    }


def default_game(match_id: str, game_number: int, start_ts: int, best_of: int) -> LolGameRow:
    """T1 vs Gen.G map with T1 on blue."""
    return make_game(
        f"{match_id}-g{game_number}",
        match_id,
        game_number,
        "T1",
        "Gen.G",
        TEAM_A_ID,
        TEAM_B_ID,
        start_ts,
        best_of,
        "lck",
        1,
        0,
    )


def event_audit(result: LinkResultView, event_id: str) -> LolLinkAuditRow:
    """Return the unique event-scope audit row."""
    rows = [row for row in result.audit if row["event_id"] == event_id and row["scope"] == "event"]
    assert len(rows) == 1, event_id
    return rows[0]


def map_audit(result: LinkResultView, event_id: str, game_number: int) -> LolLinkAuditRow:
    """Return the unique map-scope audit row for one game number."""
    rows = [
        row
        for row in result.audit
        if row["event_id"] == event_id
        and row["scope"] == "map"
        and row["game_number"] == game_number
    ]
    assert len(rows) == 1, (event_id, game_number)
    return rows[0]


def make_window(esports_game_id: str) -> dict[str, object]:
    """First livestats window with two frames."""
    return {
        "esportsGameId": esports_game_id,
        "frames": [
            {"rfc460Timestamp": "2026-03-01T12:05:00Z"},
            {"rfc460Timestamp": "2026-03-01T12:05:10Z"},
        ],
        "gameMetadata": {
            "patchVersion": "14.5",
            "blueTeamMetadata": {"esportsTeamId": TEAM_A_ID},
            "redTeamMetadata": {"esportsTeamId": TEAM_B_ID},
        },
    }


def make_event_details(
    match_id: str,
    game_id: str,
    team_a_name: str,
    team_b_name: str,
    blue_id: str,
    red_id: str,
) -> dict[str, object]:
    """getEventDetails body for a completed BO1."""
    return {
        "data": {
            "event": {
                "id": match_id,
                "startTime": BASE_TIME,
                "league": {"slug": "lck", "name": "LCK"},
                "match": {
                    "id": match_id,
                    "strategy": {"count": 1},
                    "teams": [
                        {
                            "id": TEAM_A_ID,
                            "name": team_a_name,
                            "code": "T1",
                            "result": {"gameWins": 1},
                        },
                        {
                            "id": TEAM_B_ID,
                            "name": team_b_name,
                            "code": "GEN",
                            "result": {"gameWins": 0},
                        },
                    ],
                    "games": [
                        {
                            "id": game_id,
                            "number": 1,
                            "state": "completed",
                            "teams": [
                                {"side": "blue", "esportsTeamId": blue_id},
                                {"side": "red", "esportsTeamId": red_id},
                            ],
                        }
                    ],
                },
            }
        }
    }


def make_schedule_body(match_id: str) -> dict[str, object]:
    """getSchedule body with one completed match."""
    return {
        "data": {
            "schedule": {
                "pages": {"older": None, "newer": None},
                "events": [
                    {
                        "type": "match",
                        "state": "completed",
                        "startTime": BASE_TIME,
                        "match": {"id": match_id},
                        "league": {"slug": "lck", "name": "LCK"},
                    }
                ],
            }
        }
    }


def write_universe_parquet(path: Path, rows: list[LolUniverseMarketRow]) -> None:
    """Write a tiny markets.parquet for cache/CLI tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_swap_teams_still_matches() -> None:
    """PM A/B reversed vs lolesports still matches; Blue follows the map, not PM order."""
    universe = [
        included_game_n("e-swap", 1, "Gen.G", "T1", 1, "LCK", BASE_TS),
    ]
    games = [
        make_game(
            "g-swap",
            "m-swap",
            1,
            "T1",
            "Gen.G",
            TEAM_A_ID,
            TEAM_B_ID,
            BASE_TS,
            1,
            "lck",
            1,
            0,
        )
    ]
    result = load_link().link_events(universe, games)
    assert len(result.links) == 1
    assert result.links[0]["radiant_token_index"] == 1
    assert result.links[0]["assignment"] == "game_winner"
    assert event_audit(result, "e-swap")["reason"] == REASON_ACCEPTED


def test_alias_map_matches_skt_to_t1() -> None:
    """PM SKT matches lolesports T1 through LOL_TEAM_ALIASES."""
    assert "t1" in LOL_TEAM_ALIASES["skt"]
    universe = [included_game_n("e-alias", 1, "SKT", "Gen.G", 1, "LCK", BASE_TS)]
    games = [default_game("m-alias", 1, BASE_TS, 1)]
    result = load_link().link_events(universe, games)
    assert len(result.links) == 1
    assert result.links[0]["esports_match_id"] == "m-alias"
    assert event_audit(result, "e-alias")["reason"] == REASON_ACCEPTED


def test_average_score_below_0_82_is_unmatched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-side 0.80/0.80 (average 0.80) is unmatched."""
    module = load_link()

    def fake_score(pm_name: str, series_name: str, series_code: str | None) -> float:
        observed = {normalize_team_name(series_name), normalize_team_name(series_code or "")}
        if normalize_team_name(pm_name) in observed:
            return 0.80
        return 0.10

    monkeypatch.setattr(lol_match, "score_lol_team_name", fake_score)
    universe = [included_game_n("e-avg", 1, "Alpha", "Bravo", 1, "LCK", BASE_TS)]
    games = [
        make_game(
            "g-avg",
            "m-avg",
            1,
            "Alpha",
            "Bravo",
            TEAM_A_ID,
            TEAM_B_ID,
            BASE_TS,
            1,
            "lck",
            1,
            0,
        )
    ]
    result = module.link_events(universe, games)
    assert result.links == ()
    assert event_audit(result, "e-avg")["reason"] == REASON_NO_CANDIDATES_IN_WINDOW


def test_per_team_score_below_0_72_is_unmatched(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.99/0.70 is unmatched even though the pair average is above 0.82."""
    module = load_link()

    def fake_score(pm_name: str, series_name: str, series_code: str | None) -> float:
        observed = {normalize_team_name(series_name), normalize_team_name(series_code or "")}
        expected = normalize_team_name(pm_name)
        if expected not in observed:
            return 0.10
        if expected == "alpha":
            return 0.99
        return 0.70

    monkeypatch.setattr(lol_match, "score_lol_team_name", fake_score)
    universe = [included_game_n("e-side", 1, "Alpha", "Bravo", 1, "LCK", BASE_TS)]
    games = [
        make_game(
            "g-side",
            "m-side",
            1,
            "Alpha",
            "Bravo",
            TEAM_A_ID,
            TEAM_B_ID,
            BASE_TS,
            1,
            "lck",
            1,
            0,
        )
    ]
    result = module.link_events(universe, games)
    assert result.links == ()
    assert event_audit(result, "e-side")["reason"] == REASON_NO_CANDIDATES_IN_WINDOW


def test_time_window_four_hours() -> None:
    """A 4h delta matches; 4h+1s does not."""
    module = load_link()
    at_bound = module.link_events(
        [included_game_n("e-4h", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)],
        [default_game("m-4h", 1, BASE_TS + LOL_SERIES_START_WINDOW_SECONDS, 1)],
    )
    assert len(at_bound.links) == 1
    over = module.link_events(
        [included_game_n("e-4h1", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)],
        [default_game("m-4h1", 1, BASE_TS + LOL_SERIES_START_WINDOW_SECONDS + 1, 1)],
    )
    assert over.links == ()
    assert event_audit(over, "e-4h1")["reason"] == REASON_NO_CANDIDATES_IN_WINDOW


def test_bo_guard_only_when_both_known() -> None:
    """BO matches when both known; mismatches; null PM BO still eligible."""
    module = load_link()
    matched = module.link_events(
        [included_game_n("e-bo3", 1, "T1", "Gen.G", 3, "LCK", BASE_TS)],
        [default_game("m-bo3", 1, BASE_TS, 3)],
    )
    assert len(matched.links) == 1
    mismatch = module.link_events(
        [included_game_n("e-bo35", 1, "T1", "Gen.G", 3, "LCK", BASE_TS)],
        [default_game("m-bo35", 1, BASE_TS, 5)],
    )
    assert mismatch.links == ()
    assert event_audit(mismatch, "e-bo35")["reason"] == REASON_BO_MISMATCH
    null_bo = module.link_events(
        [included_game_n("e-bonull", 1, "T1", "Gen.G", None, "LCK", BASE_TS)],
        [default_game("m-bonull", 1, BASE_TS, 3)],
    )
    assert len(null_bo.links) == 1


def test_league_guard_only_when_both_canonical() -> None:
    """Known slugs must match; an unknown PM league skips the guard."""
    module = load_link()
    matched = module.link_events(
        [included_game_n("e-lck", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)],
        [default_game("m-lck", 1, BASE_TS, 1)],
    )
    assert len(matched.links) == 1
    mismatch = module.link_events(
        [included_game_n("e-lec", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)],
        [
            make_game(
                "g-lec",
                "m-lec",
                1,
                "T1",
                "Gen.G",
                TEAM_A_ID,
                TEAM_B_ID,
                BASE_TS,
                1,
                "lec",
                1,
                0,
            )
        ],
    )
    assert mismatch.links == ()
    assert event_audit(mismatch, "e-lec")["reason"] == REASON_LEAGUE_MISMATCH
    unknown = module.link_events(
        [included_game_n("e-unk", 1, "T1", "Gen.G", 1, "Some Cup", BASE_TS)],
        [default_game("m-unk", 1, BASE_TS, 1)],
    )
    assert len(unknown.links) == 1


def test_multiple_eligible_series_are_ambiguous() -> None:
    """Two series that pass every guard stay ambiguous; nearest time is not used."""
    universe = [included_game_n("e-amb", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    games = [
        default_game("m-a", 1, BASE_TS, 1),
        default_game("m-b", 1, BASE_TS + 60, 1),
    ]
    result = load_link().link_events(universe, games)
    assert result.links == ()
    audit = event_audit(result, "e-amb")
    assert audit["reason"] == REASON_MULTIPLE_ELIGIBLE_SERIES
    assert audit["candidate_count"] == 2


def test_duplicate_pm_events_richer_game_n_wins() -> None:
    """The event with more explicit Game N markets keeps a shared series."""
    richer = [
        included_game_n("e-rich", 1, "T1", "Gen.G", 3, "LCK", BASE_TS),
        included_game_n("e-rich", 2, "T1", "Gen.G", 3, "LCK", BASE_TS),
    ]
    poorer = [included_game_n("e-poor", 1, "T1", "Gen.G", 3, "LCK", BASE_TS)]
    games = [
        make_game("g1", "m-share", 1, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 0),
        make_game("g2", "m-share", 2, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 0),
    ]
    result = load_link().link_events(richer + poorer, games)
    assert {row["event_id"] for row in result.links} == {"e-rich"}
    assert event_audit(result, "e-rich")["reason"] == REASON_ACCEPTED
    poor = event_audit(result, "e-poor")
    assert poor["reason"] == REASON_SERIES_CLAIMED_BY_RICHER_EVENT
    assert poor["duplicate_of"] == "e-rich"


def test_duplicate_pm_events_tie_stays_ambiguous() -> None:
    """Equal explicit Game N counts leave every claimant ambiguous."""
    left = [included_game_n("e-left", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    right = [included_game_n("e-right", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    games = [default_game("m-tie", 1, BASE_TS, 1)]
    result = load_link().link_events(left + right, games)
    assert result.links == ()
    assert event_audit(result, "e-left")["reason"] == REASON_SERIES_CLAIM_TIE
    assert event_audit(result, "e-right")["reason"] == REASON_SERIES_CLAIM_TIE


def test_game_n_preferred_over_match_winner() -> None:
    """An explicit Game 1 Winner beats Match Winner on a BO1."""
    universe = [
        included_game_n("e-pref", 1, "T1", "Gen.G", 1, "LCK", BASE_TS),
        included_match_winner("e-pref", "T1", "Gen.G", 1, "LCK", BASE_TS, 1),
    ]
    result = load_link().link_events(universe, [default_game("m-pref", 1, BASE_TS, 1)])
    assert len(result.links) == 1
    assert result.links[0]["assignment"] == "game_winner"
    assert result.links[0]["condition_id"] == "cid-e-pref-g1"


def test_bo3_decider_fallback_on_tied_series() -> None:
    """Game 3 of a 2-1 BO3 uses included Match Winner when Game 3 Winner is absent."""
    universe = [
        included_game_n("e-dec", 1, "T1", "Gen.G", 3, "LCK", BASE_TS),
        included_game_n("e-dec", 2, "T1", "Gen.G", 3, "LCK", BASE_TS),
        included_match_winner("e-dec", "T1", "Gen.G", 3, "LCK", BASE_TS, 3),
    ]
    games = [
        make_game("g1", "m-dec", 1, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 1),
        make_game("g2", "m-dec", 2, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 1),
        make_game("g3", "m-dec", 3, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 1),
    ]
    result = load_link().link_events(universe, games)
    by_game = {row["game_number"]: row for row in result.links}
    assert by_game[1]["assignment"] == "game_winner"
    assert by_game[2]["assignment"] == "game_winner"
    assert by_game[3]["assignment"] == "match_winner_decider"
    assert by_game[3]["condition_id"] == "cid-e-dec-mw"


def test_bo3_2_0_does_not_assign_decider() -> None:
    """A 2-0 BO3 does not get a Game 3 Match Winner fallback."""
    universe = [
        included_game_n("e-20", 1, "T1", "Gen.G", 3, "LCK", BASE_TS),
        included_game_n("e-20", 2, "T1", "Gen.G", 3, "LCK", BASE_TS),
        included_match_winner("e-20", "T1", "Gen.G", 3, "LCK", BASE_TS, 3),
    ]
    played = [
        make_game("g1", "m-20", 1, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 0),
        make_game("g2", "m-20", 2, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 0),
    ]
    without_g3 = load_link().link_events(universe, played)
    assert {row["game_number"] for row in without_g3.links} == {1, 2}
    with_g3 = load_link().link_events(
        universe,
        [
            *played,
            make_game(
                "g3", "m-20", 3, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 0
            ),
        ],
    )
    assert {row["game_number"] for row in with_g3.links} == {1, 2}
    assert map_audit(with_g3, "e-20", 3)["reason"] == REASON_UNSUPPORTED_FALLBACK


def test_unresolved_game_n_does_not_fall_through() -> None:
    """An excluded Game 2 Winner does not hand the map to Match Winner."""
    universe = [
        included_game_n("e-unres", 1, "T1", "Gen.G", 3, "LCK", BASE_TS),
        make_universe_row(
            "e-unres",
            "e-unres-g2",
            "cid-e-unres-g2",
            "T1",
            "Gen.G",
            3,
            "LCK",
            BASE_TS,
            2,
            "game_winner",
            False,
            REASON_UNRESOLVED_MARKET,
            ["T1", "Gen.G"],
            None,
            None,
        ),
        included_match_winner("e-unres", "T1", "Gen.G", 3, "LCK", BASE_TS, 3),
    ]
    games = [
        make_game("g1", "m-unres", 1, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 1),
        make_game("g2", "m-unres", 2, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 1),
        make_game("g3", "m-unres", 3, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID, BASE_TS, 3, "lck", 2, 1),
    ]
    result = load_link().link_events(universe, games)
    assert {row["game_number"] for row in result.links} == {1, 3}
    assert map_audit(result, "e-unres", 2)["reason"] == REASON_UNRESOLVED_MARKET


def test_orientation_outcome_to_side() -> None:
    """Outcome 0 T1 on red yields radiant_token_index 1."""
    universe = [included_game_n("e-or", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    games = [
        make_game(
            "g-or",
            "m-or",
            1,
            "T1",
            "Gen.G",
            TEAM_B_ID,
            TEAM_A_ID,
            BASE_TS,
            1,
            "lck",
            1,
            0,
        )
    ]
    result = load_link().link_events(universe, games)
    assert len(result.links) == 1
    assert result.links[0]["radiant_token_index"] == 1


def test_orientation_tie_excludes_map() -> None:
    """Tied outcome names make orientation ambiguous and drop the map, not the series."""
    universe = [
        make_universe_row(
            "e-tie",
            "e-tie-g1",
            "cid-e-tie-g1",
            "T1",
            "Gen.G",
            1,
            "LCK",
            BASE_TS,
            1,
            "game_winner",
            True,
            REASON_GAME_WINNER,
            ["T1", "T1"],
            "T1",
            0,
        )
    ]
    games = [default_game("m-tie", 1, BASE_TS, 1)]
    result = load_link().link_events(universe, games)
    assert result.links == ()
    assert event_audit(result, "e-tie")["reason"] == REASON_ACCEPTED
    assert map_audit(result, "e-tie", 1)["reason"] == REASON_ORIENTATION_AMBIGUOUS


def test_missing_blue_red_side_excludes_map() -> None:
    """Both sides pointing at the same esportsTeamId is missing_side."""
    universe = [included_game_n("e-side", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    games = [
        make_game(
            "g-side",
            "m-side",
            1,
            "T1",
            "Gen.G",
            TEAM_A_ID,
            TEAM_A_ID,
            BASE_TS,
            1,
            "lck",
            1,
            0,
        )
    ]
    result = load_link().link_events(universe, games)
    assert result.links == ()
    assert map_audit(result, "e-side", 1)["reason"] == REASON_MISSING_SIDE


def test_http_403_aborts_without_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 403 on getSchedule raises immediately and writes no schedule page."""
    module = load_link()
    universe_path = tmp_path / "markets.parquet"
    write_universe_parquet(
        universe_path, [included_game_n("e-403", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    )
    raw_dir = tmp_path / "lolesports"

    def fake_get(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        return httpx.Response(403, request=httpx.Request("GET", url))

    monkeypatch.setattr(module, "http_get", fake_get)
    with pytest.raises(RuntimeError, match="403"):
        module.link_lolesports(
            universe_path, raw_dir, tmp_path / "games.parquet", tmp_path / "links", True
        )
    schedule_dir = raw_dir / "schedule"
    assert not schedule_dir.exists() or not any(schedule_dir.glob("*.json.gz"))


@pytest.mark.parametrize(
    ("broken", "match"),
    [
        ("schedule", "getSchedule"),
        ("event", "getEventDetails"),
        ("window", "window"),
    ],
)
def test_incompatible_schema_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str, match: str
) -> None:
    """An empty or null top-level body aborts and is not cached."""
    module = load_link()
    universe_path = tmp_path / "markets.parquet"
    write_universe_parquet(
        universe_path, [included_game_n("e-schema", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    )
    raw_dir = tmp_path / "lolesports"
    match_id = "m-schema"
    game_id = "g-schema"
    valid_schedule = make_schedule_body(match_id)
    valid_event = make_event_details(match_id, game_id, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID)
    valid_window = make_window(game_id)

    def fake_get(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        if "getSchedule" in url:
            payload: object = {} if broken == "schedule" else valid_schedule
        elif "getEventDetails" in url:
            payload = {} if broken == "event" else valid_event
        else:
            payload = {} if broken == "window" else valid_window
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(module, "http_get", fake_get)
    with pytest.raises(RuntimeError, match=match):
        module.link_lolesports(
            universe_path, raw_dir, tmp_path / "games.parquet", tmp_path / "links", True
        )
    if broken == "schedule":
        assert not (raw_dir / "schedule").exists() or not any((raw_dir / "schedule").glob("*"))
    if broken == "event":
        assert not (raw_dir / "events" / f"{match_id}.json.gz").exists()
    if broken == "window":
        assert not (raw_dir / "anchors" / f"{game_id}.json").exists()


def test_build_from_cache_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeded schedule/event/anchor caches rebuild parquets and never HTTP."""
    module = load_link()

    def fail_get(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        raise AssertionError(f"http_get should not be called: {url}")

    monkeypatch.setattr(module, "http_get", fail_get)
    match_id = "m-cache"
    game_id = "g-cache"
    universe_path = tmp_path / "markets.parquet"
    write_universe_parquet(
        universe_path, [included_game_n("e-cache", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    )
    raw_dir = tmp_path / "lolesports"
    schedule_body = make_schedule_body(match_id)
    envelope: dict[str, object] = {
        "endpoint": "getSchedule",
        "page_token": None,
        "pages": {"older": None, "newer": None},
        "fetched_at": "2026-08-27T00:00:00+00:00",
        "body": schedule_body,
    }
    module.write_gzip_json(raw_dir / "schedule" / "page_00000.json.gz", envelope)
    module.write_gzip_json(
        raw_dir / "events" / f"{match_id}.json.gz",
        make_event_details(match_id, game_id, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID),
    )
    anchor_path = raw_dir / "anchors" / f"{game_id}.json"
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    anchor_path.write_text(json.dumps(make_window(game_id)), encoding="utf-8")
    games_path = tmp_path / "games.parquet"
    links_dir = tmp_path / "links"
    module.link_lolesports(universe_path, raw_dir, games_path, links_dir, False)
    games = pd.read_parquet(games_path)
    links = pd.read_parquet(links_dir / "links.parquet")
    audit = pd.read_parquet(links_dir / "audit.parquet")
    assert len(games) == 1
    assert str(games.iloc[0]["esports_game_id"]) == game_id
    assert len(links) == 1
    assert str(links.iloc[0]["event_id"]) == "e-cache"
    assert len(audit) >= 2
    assert not games_path.with_suffix(games_path.suffix + ".tmp").exists()


def test_live_event_details_fill_start_from_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live getEventDetails omits startTime; schedule startTime fills games.start_ts."""
    module = load_link()

    def fail_get(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        raise AssertionError(f"http_get should not be called: {url}")

    monkeypatch.setattr(module, "http_get", fail_get)
    match_id = "m-live"
    game_id = "g-live"
    universe_path = tmp_path / "markets.parquet"
    write_universe_parquet(
        universe_path, [included_game_n("e-live", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    )
    raw_dir = tmp_path / "lolesports"
    envelope: dict[str, object] = {
        "endpoint": "getSchedule",
        "page_token": None,
        "pages": {"older": None, "newer": None},
        "fetched_at": "2026-08-27T00:00:00+00:00",
        "body": make_schedule_body(match_id),
    }
    module.write_gzip_json(raw_dir / "schedule" / "page_00000.json.gz", envelope)
    details = make_event_details(match_id, game_id, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID)
    data = cast(dict[str, object], details["data"])
    event = cast(dict[str, object], data["event"])
    match = cast(dict[str, object], event["match"])
    del match["id"]
    del event["startTime"]
    games_raw = cast(list[object], match["games"])
    game = cast(dict[str, object], games_raw[0])
    for raw in cast(list[object], game["teams"]):
        team = cast(dict[str, object], raw)
        team["id"] = team.pop("esportsTeamId")
    module.write_gzip_json(raw_dir / "events" / f"{match_id}.json.gz", details)
    anchor_path = raw_dir / "anchors" / f"{game_id}.json"
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    anchor_path.write_text(json.dumps(make_window(game_id)), encoding="utf-8")
    games_path = tmp_path / "games.parquet"
    links_dir = tmp_path / "links"
    module.link_lolesports(universe_path, raw_dir, games_path, links_dir, False)
    games = pd.read_parquet(games_path)
    links = pd.read_parquet(links_dir / "links.parquet")
    assert len(games) == 1
    assert int(games.iloc[0]["start_ts"]) == BASE_TS
    assert len(links) == 1


def iso_from_ts(ts: int) -> str:
    """UTC Z stamp from unix seconds."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_schedule_page(
    match_id: str, start_time: str, older: str | None, newer: str | None, state: str
) -> dict[str, object]:
    """getSchedule body with one event and explicit page tokens."""
    return {
        "data": {
            "schedule": {
                "pages": {"older": older, "newer": newer},
                "events": [
                    {
                        "type": "match",
                        "state": state,
                        "startTime": start_time,
                        "match": {"id": match_id},
                        "league": {"slug": "lck", "name": "LCK"},
                    }
                ],
            }
        }
    }


def test_getschedule_pages_to_gamma_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Follow older/newer once; cache the out-of-window page; do not fetch the next token."""
    module = load_link()
    universe_path = tmp_path / "markets.parquet"
    write_universe_parquet(
        universe_path, [included_game_n("e-page", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    )
    raw_dir = tmp_path / "lolesports"
    older_time = iso_from_ts(BASE_TS - LOL_SERIES_START_WINDOW_SECONDS - 1)
    newer_time = iso_from_ts(BASE_TS + LOL_SERIES_START_WINDOW_SECONDS + 1)
    first_page = make_schedule_page("m-now", BASE_TIME, "older-token", "newer-token", "inProgress")
    older_page = make_schedule_page("m-old", older_time, "even-older", None, "inProgress")
    newer_page = make_schedule_page("m-new", newer_time, None, "even-newer", "inProgress")
    tokens: list[str | None] = []

    def fake_get(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        if "getSchedule" not in url:
            raise AssertionError(f"unexpected URL {url}")
        token = params.get("pageToken")
        tokens.append(token)
        if token is None:
            payload: object = first_page
        elif token == "older-token":
            payload = older_page
        elif token == "newer-token":
            payload = newer_page
        else:
            raise AssertionError(f"unexpected pageToken {token}")
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(module, "http_get", fake_get)
    module.link_lolesports(
        universe_path, raw_dir, tmp_path / "games.parquet", tmp_path / "links", True
    )
    assert tokens == [None, "older-token", "newer-token"]
    names = sorted(path.name for path in (raw_dir / "schedule").glob("page_*.json.gz"))
    assert names == ["page_00000.json.gz", "page_00001.json.gz", "page_00002.json.gz"]


def test_event_id_used_when_match_id_absent() -> None:
    """Live getEventDetails puts the id on event.id, not match.id."""
    details = make_event_details("m1", "g1", "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID)
    data = cast(dict[str, object], details["data"])
    event = cast(dict[str, object], data["event"])
    match = cast(dict[str, object], event["match"])
    del match["id"]
    row = load_link().build_game_row(details, make_window("g1"))
    assert row is not None
    assert row["esports_match_id"] == "m1"


def test_missing_event_and_match_id_aborts() -> None:
    """Neither event.id nor match.id is an incompatible schema, not an invented id."""
    details = make_event_details("m1", "g1", "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID)
    data = cast(dict[str, object], details["data"])
    event = cast(dict[str, object], data["event"])
    match = cast(dict[str, object], event["match"])
    del event["id"]
    del match["id"]
    with pytest.raises(RuntimeError, match="getEventDetails"):
        load_link().build_game_row(details, make_window("g1"))


def test_game_team_id_field_is_side_id() -> None:
    """Live games[].teams use id, not esportsTeamId."""
    details = make_event_details("m1", "g1", "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID)
    data = cast(dict[str, object], details["data"])
    event = cast(dict[str, object], data["event"])
    match = cast(dict[str, object], event["match"])
    games = cast(list[object], match["games"])
    game = cast(dict[str, object], games[0])
    teams = cast(list[object], game["teams"])
    for raw in teams:
        team = cast(dict[str, object], raw)
        team["id"] = team.pop("esportsTeamId")
    row = load_link().build_game_row(details, make_window("g1"))
    assert row is not None
    assert row["blue_esports_team_id"] == TEAM_A_ID
    assert row["red_esports_team_id"] == TEAM_B_ID


def test_build_game_row_from_payloads() -> None:
    """getEventDetails plus a window become one games.parquet row."""
    details = make_event_details("m1", "g1", "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID)
    window = make_window("g1")
    row = load_link().build_game_row(details, window)
    assert row is not None
    assert row["esports_game_id"] == "g1"
    assert row["esports_match_id"] == "m1"
    assert row["blue_esports_team_id"] == TEAM_A_ID
    assert row["loading_anchor"] == "2026-03-01T12:05:00Z"
    assert row["patch_version"] == "14.5"


def test_game_row_uses_window_sides_when_details_swap() -> None:
    """Livestats window sides win over getEventDetails games[].teams."""
    details = make_event_details("m1", "g1", "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID)
    window = make_window("g1")
    metadata = cast(dict[str, object], window["gameMetadata"])
    metadata["blueTeamMetadata"] = {"esportsTeamId": TEAM_B_ID}
    metadata["redTeamMetadata"] = {"esportsTeamId": TEAM_A_ID}
    row = load_link().build_game_row(details, window)
    assert row is not None
    assert row["blue_esports_team_id"] == TEAM_B_ID
    assert row["red_esports_team_id"] == TEAM_A_ID


def test_game_row_skips_when_window_sides_missing() -> None:
    """A window without team ids is dropped even when getEventDetails has sides."""
    details = make_event_details("m1", "g1", "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID)
    window = make_window("g1")
    window["gameMetadata"] = {"patchVersion": "14.5"}
    assert load_link().build_game_row(details, window) is None


def test_score_lol_team_name_uses_alias_map() -> None:
    """The matcher expands LOL_TEAM_ALIASES."""
    assert score_lol_team_name("SKT", "T1", "T1") == 1.0


def test_empty_first_window_body_skips_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP 200 empty first-window body is skipped, not incompatible schema."""
    module = load_link()
    universe_path = tmp_path / "markets.parquet"
    write_universe_parquet(
        universe_path, [included_game_n("e-empty", 1, "T1", "Gen.G", 1, "LCK", BASE_TS)]
    )
    match_id = "m-empty"
    game_id = "g-empty"

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], _params: dict[str, str]
    ) -> httpx.Response:
        if "getSchedule" in url:
            payload: object = make_schedule_body(match_id)
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))
        if "getEventDetails" in url:
            payload = make_event_details(match_id, game_id, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID)
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))
        return httpx.Response(200, content=b"", request=httpx.Request("GET", url))

    monkeypatch.setattr(module, "http_get", fake_get)
    module.link_lolesports(
        universe_path, tmp_path / "lolesports", tmp_path / "games.parquet", tmp_path / "links", True
    )
    games = pd.read_parquet(tmp_path / "games.parquet")
    assert len(games) == 0
    assert not (tmp_path / "lolesports" / "anchors" / f"{game_id}.json").exists()


def test_lol_max_concurrency_is_one_hundred_twenty_eight() -> None:
    """Stage 04 pool size: 128, with an explicit httpx connection limit to match."""
    assert LOL_MAX_CONCURRENCY == 128


def test_fetch_two_matches_writes_both_event_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--fetch` downloads event details and first windows for every completed match."""
    module = load_link()
    universe_path = tmp_path / "markets.parquet"
    write_universe_parquet(
        universe_path,
        [
            included_game_n("e-a", 1, "T1", "Gen.G", 1, "LCK", BASE_TS),
            included_game_n("e-b", 1, "HLE", "KT", 1, "LCK", BASE_TS),
        ],
    )
    match_a = "m-a"
    match_b = "m-b"
    game_a = "g-a"
    game_b = "g-b"
    schedule = make_schedule_body(match_a)
    data = cast(dict[str, object], schedule["data"])
    sched = cast(dict[str, object], data["schedule"])
    events = cast(list[object], sched["events"])
    events.append(
        {
            "type": "match",
            "state": "completed",
            "startTime": BASE_TIME,
            "match": {"id": match_b},
            "league": {"slug": "lck", "name": "LCK"},
        }
    )
    payloads = {
        match_a: make_event_details(match_a, game_a, "T1", "Gen.G", TEAM_A_ID, TEAM_B_ID),
        match_b: make_event_details(match_b, game_b, "HLE", "KT", "esports-hle", "esports-kt"),
    }

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        if "getSchedule" in url:
            return httpx.Response(200, json=schedule, request=httpx.Request("GET", url))
        if "getEventDetails" in url:
            return httpx.Response(
                200, json=payloads[params["id"]], request=httpx.Request("GET", url)
            )
        game_id = url.rsplit("/", 1)[-1]
        return httpx.Response(200, json=make_window(game_id), request=httpx.Request("GET", url))

    monkeypatch.setattr(module, "http_get", fake_get)
    raw_dir = tmp_path / "lolesports"
    module.link_lolesports(
        universe_path, raw_dir, tmp_path / "games.parquet", tmp_path / "links", True
    )
    games = pd.read_parquet(tmp_path / "games.parquet")
    assert (raw_dir / "events" / f"{match_a}.json.gz").exists()
    assert (raw_dir / "events" / f"{match_b}.json.gz").exists()
    assert set(games["esports_game_id"]) == {game_a, game_b}
