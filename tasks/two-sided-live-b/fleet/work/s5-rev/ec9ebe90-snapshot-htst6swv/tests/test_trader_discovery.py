"""Tests for market discovery: Steam linking, Bo1 gates and log-only misses.

Everything runs offline: sidecars live in a temporary `<root>/metadata/markets`,
mtimes are set with `os.utime`, the clock is a fixed epoch, and Steam responses
come from `httpx.MockTransport`. No network, Docker, collector, poly-maker or
Telegram is involved.
"""

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from trader_discovery_fixtures import (
    NOW_EPOCH,
    FakeSteamApi,
    discovery_logs,
    grid_maps_all,
    league_row,
    make_discovery,
    make_oddin_card,
    make_scoreboard,
    outcome,
    patch_alerts,
    patch_discovery_time,
    prime_happy_api,
    series_winner_body,
    sidecar_body,
    write_sidecar,
)

from shared.utils import team_names
from trader import discovery
from trader.grid_widgets import UPCOMING_STATUS, Scoreboard
from trader.oddin_types import OddinMatch
from trader.source_picker import GridProbeCycle

# --- flags and kind gates -------------------------------------------------------


def _grid_cycle(boards: Mapping[str, Scoreboard]) -> GridProbeCycle:
    """Wrap a board map as a probe cycle with no handshake rejects."""
    return GridProbeCycle(dict(boards), frozenset())


@pytest.mark.parametrize(
    "override",
    [
        {"active": False},
        {"closed": True},
        {"acceptingOrders": False},
        {"acceptingOrders": None},
        {"enableOrderBook": False},
    ],
)
def test_non_tradeable_flags_yield_no_match_and_no_steam_traffic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: dict[str, object]
) -> None:
    """Every false/null trade flag keeps the sidecar out; Steam is never called."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1", sidecar_body(**override))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert api.requests == []
    assert recorder.messages == []


def test_is_league_blacklisted_matches_the_league_not_the_teams() -> None:
    """The needle matches the `(BOx) - league` suffix; a team name is not a hit."""
    blacklist = ("Streamers", "Winline")
    assert discovery.is_league_blacklisted(
        "Dota 2: Daxak Team vs YBN Team (BO3) - BetBoom Streamers Battle Playoffs", blacklist
    )
    assert discovery.is_league_blacklisted(
        "Dota 2: Team A vs Team B (BO3) - winline star series", blacklist
    )
    assert not discovery.is_league_blacklisted(
        "Dota 2: Winline Team vs Team B (BO3) - EPL", blacklist
    )
    assert not discovery.is_league_blacklisted("Winline showmatch", blacklist)
    assert not discovery.is_league_blacklisted(None, blacklist)
    assert not discovery.is_league_blacklisted("Dota 2: A vs B (BO3) - Winline", ())


def test_blacklisted_event_league_never_reaches_steam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A sidecar whose title league is blacklisted drops before any Steam call."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(
        tmp_path,
        "0x1",
        sidecar_body(eventTitle="Dota 2: Team A vs Team B (BO3) - Winline Star Series"),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)
    found = make_discovery(tmp_path, api, title_blacklist=("Winline",))

    with caplog.at_level(logging.INFO):
        assert found.discover() == ()
        assert found.discover() == ()

    assert api.requests == []
    assert recorder.messages == []
    skips = [row for row in caplog.records if "title_blacklist" in row.getMessage()]
    assert len(skips) == 1
    assert skips[0].levelname == "INFO"
    assert "Winline Star Series" in skips[0].getMessage()


def test_map_winner_works_on_non_bo1_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A map_winner sidecar trades on any series when its map number matches."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=3, radiant_wins=1, dire_wins=1)
    write_sidecar(tmp_path, "0x1", sidecar_body(mapNumber=3))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert len(matches) == 1
    assert matches[0].map_number == 3
    assert recorder.messages == []


@pytest.mark.parametrize("series_type", [None, 1, 2, 3])
def test_series_winner_fails_off_the_decider_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    series_type: int | None,
) -> None:
    """A missing type, BO2, or a BO3/BO5 still on map 1 never authorizes Match Winner."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=series_type)
    write_sidecar(tmp_path, "0xmatch", series_winner_body())
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert recorder.messages == []
    logs = discovery_logs(caplog)
    assert any("series_skips=1" in message for message in logs)
    assert all("0xmatch" not in message for message in logs)


def test_series_only_bo1_works_as_implicit_game_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a Bo1 without a Game-1 sidecar, the Match Winner trades as map 1."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=0)
    write_sidecar(tmp_path, "0xmatch", series_winner_body())
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    oddin = make_oddin_card()
    matches = make_discovery(tmp_path, api, list_oddin_matches=lambda: (oddin,)).discover()

    assert len(matches) == 1
    assert matches[0].map_number == 1
    assert matches[0].market.condition_id == "0xmatch"
    assert recorder.messages == []


def test_bo1_game_1_wins_over_match_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a Bo1 with both Game 1 and Match Winner, only Game 1 is emitted."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=0)
    write_sidecar(tmp_path, "0xgame1", sidecar_body(conditionId="0xgame1"))
    write_sidecar(tmp_path, "0xmatch", series_winner_body(condition_id="0xmatch"))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert [match.market.condition_id for match in matches] == ["0xgame1"]
    assert recorder.messages == []


def test_series_winner_bo3_decider_trades_without_game_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a BO3 at 1-1 with no Game 3 sidecar, Match Winner trades as map 3."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=1, radiant_wins=1, dire_wins=1)
    write_sidecar(tmp_path, "0xmatch", series_winner_body())
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    oddin = make_oddin_card()
    matches = make_discovery(tmp_path, api, list_oddin_matches=lambda: (oddin,)).discover()

    assert len(matches) == 1
    assert matches[0].map_number == 3
    assert matches[0].market.condition_id == "0xmatch"
    assert recorder.messages == []


def test_steam_zero_zero_with_grid_map_three_emits_series_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Steam 0-0 plus a GRID map-3 probe trades Match Winner, not Game 1."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=1, radiant_wins=0, dire_wins=0)
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1", mapNumber=1))
    write_sidecar(
        tmp_path,
        "0x2",
        sidecar_body(conditionId="0x2", mapNumber=2, marketSlug="dota2-aurora-secret-game2"),
    )
    write_sidecar(tmp_path, "0xmatch", series_winner_body())
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    matches = make_discovery(tmp_path, api, grid_maps_all(3)).discover()

    assert len(matches) == 1
    assert matches[0].map_number == 3
    assert matches[0].market.condition_id == "0xmatch"


def test_conflicting_grid_maps_keep_the_steam_map_and_count_the_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Two series that disagree on one Steam game fall back to Steam and count a conflict."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=1, radiant_wins=0, dire_wins=0)
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1", mapNumber=1))
    write_sidecar(
        tmp_path,
        "0x3",
        sidecar_body(
            conditionId="0x3",
            mapNumber=3,
            marketSlug="dota2-aurora-secret-game3",
            gridSeriesId="2974459",
        ),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        boards = {"2974458": make_scoreboard(1), "2974459": make_scoreboard(3)}
        return _grid_cycle({series_id: boards[series_id] for series_id in series_ids})

    with caplog.at_level(logging.DEBUG):
        matches = make_discovery(tmp_path, api, probe).discover()

    assert [match.market.condition_id for match in matches] == ["0x1", "0x3"]
    assert matches[0].match_id == "grid-2974458-m1"
    assert matches[0].steam_match_id == "100"
    assert matches[0].map_number == 1
    assert matches[0].game == "dota"
    assert matches[1].match_id == "grid-2974459-m3"
    assert matches[1].map_number == 3
    assert matches[1].game == "dota"
    logs = discovery_logs(caplog)
    assert any("conflicting grid maps for steam match 100" in line for line in logs)
    assert any("grid_map_conflicts=1" in line for line in logs)


def test_steam_zero_zero_without_grid_map_still_emits_game_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed GRID probe keeps Steam's 0-0 map and still binds Game 1."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=1, radiant_wins=0, dire_wins=0)
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1", mapNumber=1))
    write_sidecar(tmp_path, "0xmatch", series_winner_body())
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    matches = make_discovery(tmp_path, api).discover()

    assert len(matches) == 1
    assert matches[0].map_number == 1
    assert matches[0].market.condition_id == "0x1"


def test_series_winner_bo3_at_one_nil_is_not_a_decider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Match Winner at BO3 1-0 is P(series), not P(map 2)."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=1, radiant_wins=1, dire_wins=0)
    write_sidecar(tmp_path, "0xmatch", series_winner_body())
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    matches = make_discovery(tmp_path, api).discover()

    assert matches == ()


def test_bo3_game_3_wins_over_match_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a BO3 decider with both Game 3 and Match Winner, only Game 3 is emitted."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=1, radiant_wins=1, dire_wins=1)
    write_sidecar(tmp_path, "0xgame3", sidecar_body(conditionId="0xgame3", mapNumber=3))
    write_sidecar(tmp_path, "0xmatch", series_winner_body(condition_id="0xmatch"))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert [match.market.condition_id for match in matches] == ["0xgame3"]
    assert recorder.messages == []


def test_series_winner_bo5_decider_trades_without_game_5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a BO5 at 2-2 with no Game 5 sidecar, Match Winner trades as map 5."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=2, radiant_wins=2, dire_wins=2)
    write_sidecar(tmp_path, "0xmatch", series_winner_body())
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    oddin = make_oddin_card()
    matches = make_discovery(tmp_path, api, list_oddin_matches=lambda: (oddin,)).discover()

    assert len(matches) == 1
    assert matches[0].map_number == 5
    assert matches[0].market.condition_id == "0xmatch"


def test_map_mismatch_returns_none_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A sidecar map number that does not match the Steam game is logged, never traded."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=0)
    write_sidecar(tmp_path, "0x1", sidecar_body(mapNumber=2))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert recorder.messages == []
    logs = discovery_logs(caplog)
    assert any("map_mismatches=1" in message and "emitted=0" in message for message in logs)
    assert not any("map mismatch: condition" in message for message in logs)


# --- Steam fetch ----------------------------------------------------------------


def test_requests_only_the_live_list_with_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exactly one GetLiveLeagueGames runs, with the current key."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert len(matches) == 1
    paths = [request.url.path for request in api.requests]
    assert paths == ["/IDOTA2Match_570/GetLiveLeagueGames/v1/"]
    assert all(request.url.params["key"] == "test-key" for request in api.requests)


def test_steam_only_link_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Steam link without gridSeriesId and without an Oddin match emits nothing."""
    api = FakeSteamApi()
    api.set_league_games(league_row(100))
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1", gridSeriesId=None))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert recorder.messages == []
    assert any(
        "no grid_series_id or oddin match for steam match 100" in message
        for message in discovery_logs(caplog)
    )


def test_steam_link_with_grid_series_emits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Steam-linked sidecar with gridSeriesId is emitted."""
    api = FakeSteamApi()
    api.set_league_games(league_row(100))
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert len(matches) == 1
    assert matches[0].market.grid_series_id == "2974458"


def league_status_500(api: FakeSteamApi) -> None:
    """Serve the live list with an HTTP 500."""
    api.league_status = 500


def league_transport_error(api: FakeSteamApi) -> None:
    """Make the live-list request raise a transport error."""
    api.league_error = httpx.ConnectError("boom")


def league_invalid_utf8(api: FakeSteamApi) -> None:
    """Serve the live list as invalid UTF-8 bytes."""
    api.league_content = b"\xff\xfe\xfa"


def league_json_null(api: FakeSteamApi) -> None:
    """Serve a JSON-null live list."""
    api.league_content = b"null"


def league_empty_object(api: FakeSteamApi) -> None:
    """Serve an empty object as the live list."""
    api.league_content = b"{}"


def league_missing_games(api: FakeSteamApi) -> None:
    """Serve a live list whose result has no games list."""
    api.league_content = b'{"result": {}}'


def league_games_not_a_list(api: FakeSteamApi) -> None:
    """Serve a live list whose games field is not a list."""
    api.league_content = b'{"result": {"games": "nope"}}'


@pytest.mark.parametrize(
    "case_id,configure",
    [
        ("http-500", league_status_500),
        ("transport-error", league_transport_error),
        ("invalid-utf8", league_invalid_utf8),
        ("json-null", league_json_null),
        ("empty-object", league_empty_object),
        ("missing-games", league_missing_games),
        ("games-not-a-list", league_games_not_a_list),
    ],
)
def test_failed_or_malformed_live_list_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    configure: Callable[[FakeSteamApi], None],
) -> None:
    """A failed/malformed live list yields nothing for the cycle and never pages."""
    api = FakeSteamApi()
    prime_happy_api(api)
    configure(api)
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert matches == (), case_id
    assert recorder.messages == []


def test_no_opendota_request_occurs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery never calls OpenDota."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)

    make_discovery(tmp_path, api).discover()

    assert all("opendota" not in request.url.host for request in api.requests)


# --- name matching ---------------------------------------------------------------


def test_aurora_aliases_normalize_and_forward_orientation_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aurora vs Aurora Gaming normalize together; forward orientation sets yes_is_radiant."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(
        tmp_path,
        "0x1",
        sidecar_body(
            outcomes=[
                outcome(0, "Aurora Gaming", "TOKEN0"),
                outcome(1, "Team Secret", "TOKEN1"),
            ]
        ),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert len(matches) == 1
    assert matches[0].sides.radiant == "Aurora Gaming"
    assert matches[0].sides.dire == "Team Secret"
    assert matches[0].market.yes_is_radiant is True
    assert recorder.messages == []


def test_reversed_sides_yield_yes_is_radiant_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When outcome 0 is the Dire side, the reverse orientation wins."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(
        tmp_path,
        "0x1",
        sidecar_body(
            outcomes=[
                outcome(0, "Team Secret", "TOKEN0"),
                outcome(1, "Aurora", "TOKEN1"),
            ]
        ),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert len(matches) == 1
    assert matches[0].market.yes_is_radiant is False
    assert recorder.messages == []


def make_fixed_scorer(
    values: list[float],
) -> Callable[[str, frozenset[str] | set[str], object], float]:
    """Build a scorer that returns scripted values in call order."""

    def scorer(expected: str, observed: frozenset[str] | set[str], aliases: object) -> float:
        del expected, observed, aliases
        return values.pop(0)

    return scorer


@pytest.mark.parametrize(
    "case_id,values,expect_match",
    [
        ("accepted-forward", [0.9, 0.9, 0.5, 0.5], True),
        ("pair-below-threshold", [0.8, 0.8, 0.5, 0.5], False),
        ("side-below-threshold", [0.95, 0.7, 0.5, 0.5], False),
        ("orientation-tie", [0.9, 0.9, 0.9, 0.9], False),
    ],
)
def test_scorer_thresholds_gate_matching_before_map_and_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    values: list[float],
    expect_match: bool,
) -> None:
    """Pair/side thresholds and exact ties reject before map and server gates run."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1")
    monkeypatch.setattr(team_names, "score_team_name", make_fixed_scorer(values))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    if expect_match:
        assert len(matches) == 1, case_id
        assert matches[0].market.yes_is_radiant is True
        assert recorder.messages == []
    else:
        assert matches == (), case_id
        assert recorder.messages == []


# --- determinism and safety -------------------------------------------------------


def test_shuffled_files_and_live_rows_give_identical_ordered_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Results are sorted by numeric match id then condition id regardless of order."""
    api = FakeSteamApi()
    write_sidecar(
        tmp_path,
        "0xccc",
        sidecar_body(
            conditionId="0xccc",
            eventSlug="e30",
            marketSlug="m30",
            gridSeriesId=None,
            outcomes=[outcome(0, "Tundra", "T30"), outcome(1, "Xtreme Gaming", "X30")],
        ),
    )
    write_sidecar(
        tmp_path,
        "0xaaa",
        sidecar_body(
            conditionId="0xaaa",
            eventSlug="e10",
            marketSlug="m10",
            gridSeriesId=None,
            outcomes=[outcome(0, "Nigma Galaxy", "T10"), outcome(1, "Team Spirit", "X10")],
        ),
    )
    write_sidecar(
        tmp_path,
        "0xbbb",
        sidecar_body(
            conditionId="0xbbb",
            eventSlug="e20",
            marketSlug="m20",
            gridSeriesId=None,
            outcomes=[outcome(0, "BetBoom", "T20"), outcome(1, "Gaimin Gladiators", "X20")],
        ),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)
    cards = (
        make_oddin_card("od:match:30", "Tundra", "Xtreme Gaming"),
        make_oddin_card("od:match:10", "Nigma Galaxy", "Team Spirit"),
        make_oddin_card("od:match:20", "BetBoom", "Gaimin Gladiators"),
    )
    market_discovery = make_discovery(tmp_path, api, list_oddin_matches=lambda: cards)

    api.set_league_games(
        league_row(30, radiant="Tundra", dire="Xtreme Gaming"),
        league_row(10, radiant="Nigma Galaxy", dire="Team Spirit"),
        league_row(20, radiant="BetBoom", dire="Gaimin Gladiators"),
    )
    first = market_discovery.discover()
    api.set_league_games(
        league_row(20, radiant="BetBoom", dire="Gaimin Gladiators"),
        league_row(30, radiant="Tundra", dire="Xtreme Gaming"),
        league_row(10, radiant="Nigma Galaxy", dire="Team Spirit"),
    )
    second = market_discovery.discover()

    assert first == second
    assert [(match.match_id, match.market.condition_id) for match in first] == [
        ("10", "0xaaa"),
        ("20", "0xbbb"),
        ("30", "0xccc"),
    ]


def test_sidecar_matching_two_games_emits_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One sidecar linking two Steam games is ambiguous and emits nothing."""
    api = FakeSteamApi()
    api.set_league_games(league_row(100), league_row(200))
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert recorder.messages == []
    assert any("ambiguous sidecar 0x1" in message for message in discovery_logs(caplog))


def test_multiple_surviving_markets_for_one_match_emit_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Two surviving markets for one Steam match are ambiguous and emit nothing."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1"))
    write_sidecar(
        tmp_path,
        "0x2",
        sidecar_body(conditionId="0x2", marketSlug="dota2-aurora-secret-game1-alt"),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert recorder.messages == []
    logs = discovery_logs(caplog)
    assert any(
        "ambiguous match grid-2974458-m1" in message and "0x1" in message for message in logs
    )


def test_discovery_touches_no_archive_session_or_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full cycle only reads the external archive; nothing else is created."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)
    names_before = sorted(path.name for path in (tmp_path / "metadata" / "markets").iterdir())

    matches = make_discovery(tmp_path, api).discover()

    assert len(matches) == 1
    assert (
        sorted(path.name for path in (tmp_path / "metadata" / "markets").iterdir()) == names_before
    )
    assert not (tmp_path / "live_paper").exists()
    assert "subprocess" not in vars(discovery)


# --- alerts -----------------------------------------------------------------------


def test_first_name_miss_logs_and_does_not_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A real name miss counts on the cycle line and does not send Telegram."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(
        tmp_path,
        "0x1",
        sidecar_body(
            outcomes=[outcome(0, "Jenz", "TOKEN0"), outcome(1, "Nemiga Gaming", "TOKEN1")]
        ),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert recorder.messages == []
    logs = discovery_logs(caplog)
    assert any("name_misses=1" in line and "emitted=0" in line for line in logs)
    assert not any("no team name link" in line for line in logs)


def test_no_live_games_send_no_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty usable Steam list never pages, even with an eligible sidecar."""
    api = FakeSteamApi()
    api.set_league_games()
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert recorder.messages == []


def test_steam_failure_sends_no_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport failure on the live list never pages."""
    api = FakeSteamApi()
    api.set_league_games(league_row(100))
    api.league_error = httpx.ConnectError("boom")
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert recorder.messages == []


def test_malformed_sidecar_sends_no_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed sidecars never page and never trigger Steam traffic."""
    api = FakeSteamApi()
    prime_happy_api(api)
    markets = tmp_path / "metadata" / "markets"
    markets.mkdir(parents=True)
    (markets / "0xbad.json").write_text("{broken", encoding="utf-8")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert api.requests == []
    assert recorder.messages == []


def test_conflicting_live_list_rows_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Two different GetLiveLeagueGames rows for one match_id emit nothing."""
    api = FakeSteamApi()
    api.set_league_games(
        league_row(100, radiant="Aurora Gaming", dire="Team Secret"),
        league_row(100, radiant="Other Team", dire="Someone Else"),
    )
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api).discover()

    assert matches == ()
    assert recorder.messages == []
    assert any("conflicting rows for match 100" in message for message in discovery_logs(caplog))


def test_grid_native_emits_without_steam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A GRID scoreboard with no Steam game still mints a grid-<series>-m<map> match."""
    api = FakeSteamApi()
    api.set_league_games()
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        board = make_scoreboard(1, tournament="ESL One")
        return _grid_cycle({series_id: board for series_id in series_ids})

    matches = make_discovery(tmp_path, api, probe).discover()

    assert recorder.messages == []
    assert len(matches) == 1
    match = matches[0]
    assert match.match_id == "grid-2974458-m1"
    assert match.steam_match_id is None
    assert match.league_id is None
    assert match.tournament == "ESL One"
    assert match.map_number == 1
    assert match.sides.radiant == "Aurora Gaming"
    assert match.sides.dire == "Team Secret"


def test_grid_native_waits_until_upcoming_map_has_sides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upcoming empty map-2 board is probed, but GRID-native emit waits for sides."""
    api = FakeSteamApi()
    api.set_league_games()
    write_sidecar(
        tmp_path,
        "0x1",
        sidecar_body(conditionId="0x1", mapNumber=2, marketSlug="dota2-aurora-secret-game2"),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)
    boards: dict[str, Scoreboard] = {
        "2974458": replace(make_scoreboard(2), game_status=UPCOMING_STATUS, teams=()),
    }

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        return _grid_cycle({series_id: boards[series_id] for series_id in series_ids})

    discovery = make_discovery(tmp_path, api, probe)
    assert discovery.discover() == ()
    boards["2974458"] = make_scoreboard(2)
    matches = discovery.discover()

    assert recorder.messages == []
    assert len(matches) == 1
    assert matches[0].match_id == "grid-2974458-m2"
    assert matches[0].map_number == 2
    assert matches[0].sides.radiant == "Aurora Gaming"
    assert matches[0].sides.dire == "Team Secret"


def test_grid_native_radiant_dire_swap_sets_yes_is_radiant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Map 2 with swapped GRID sides orients the same market names the other way."""
    api = FakeSteamApi()
    api.set_league_games()
    write_sidecar(
        tmp_path,
        "0x1",
        sidecar_body(conditionId="0x1", mapNumber=2, marketSlug="dota2-aurora-secret-game2"),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        board = make_scoreboard(2, radiant="Team Secret", dire="Aurora Gaming")
        return _grid_cycle({series_id: board for series_id in series_ids})

    matches = make_discovery(tmp_path, api, probe).discover()

    assert len(matches) == 1
    assert matches[0].match_id == "grid-2974458-m2"
    assert matches[0].sides.radiant == "Team Secret"
    assert matches[0].sides.dire == "Aurora Gaming"
    assert matches[0].market.yes_is_radiant is False


def test_steam_leftover_map1_sides_do_not_stamp_grid_map2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Steam still listing map-1 Radiant/Dire cannot orient map 2; GRID sides win."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=1, radiant_wins=0, dire_wins=1)
    write_sidecar(
        tmp_path,
        "0x2",
        sidecar_body(conditionId="0x2", mapNumber=2, marketSlug="dota2-aurora-secret-game2"),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        board = make_scoreboard(2, radiant="Team Secret", dire="Aurora Gaming")
        return _grid_cycle({series_id: board for series_id in series_ids})

    matches = make_discovery(tmp_path, api, probe).discover()
    assert len(matches) == 1
    assert matches[0].map_number == 2
    assert matches[0].sides.radiant == "Team Secret"
    assert matches[0].sides.dire == "Aurora Gaming"
    assert matches[0].market.yes_is_radiant is False


def test_incomplete_grid_board_blocks_steam_sides_for_that_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GRID map 2 with empty sides waits; Steam's leftover Radiant/Dire are not used."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=1, radiant_wins=0, dire_wins=1)
    write_sidecar(
        tmp_path,
        "0x2",
        sidecar_body(conditionId="0x2", mapNumber=2, marketSlug="dota2-aurora-secret-game2"),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        board = replace(make_scoreboard(2), game_status=UPCOMING_STATUS, teams=())
        return _grid_cycle({series_id: board for series_id in series_ids})

    assert make_discovery(tmp_path, api, probe).discover() == ()


def test_match_winner_inherits_unique_game_n_series_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BO3 map 3 without Steam: Match Winner inherits the unique Game-N series id."""
    api = FakeSteamApi()
    api.set_league_games()
    write_sidecar(tmp_path, "0x1")
    write_sidecar(tmp_path, "0xmatch", series_winner_body())
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    recorder = patch_alerts(monkeypatch)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        board = make_scoreboard(3, series_format="best-of-3")
        return _grid_cycle({series_id: board for series_id in series_ids})

    matches = make_discovery(tmp_path, api, probe).discover()

    assert recorder.messages == []
    assert len(matches) == 1
    match = matches[0]
    assert match.market.condition_id == "0xmatch"
    assert match.match_id == "grid-2974458-m3"
    assert match.map_number == 3
    assert match.market.grid_series_id == "2974458"


def test_match_winner_inherits_nothing_when_game_n_series_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Two Game-N series ids on one event: Match Winner inherits none; Game-N still emit."""
    api = FakeSteamApi()
    api.set_league_games()
    write_sidecar(tmp_path, "0x1")
    write_sidecar(
        tmp_path,
        "0x2",
        sidecar_body(
            conditionId="0x2",
            mapNumber=2,
            marketSlug="dota2-aurora-secret-game2",
            gridSeriesId="2995969",
        ),
    )
    write_sidecar(tmp_path, "0xmatch", series_winner_body())
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        boards = {
            "2974458": make_scoreboard(1),
            "2995969": make_scoreboard(2),
        }
        return _grid_cycle({series_id: boards[series_id] for series_id in series_ids})

    with caplog.at_level(logging.WARNING, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api, probe).discover()

    assert [match.market.condition_id for match in matches] == ["0x1", "0x2"]
    assert matches[0].match_id == "grid-2974458-m1"
    assert matches[1].match_id == "grid-2995969-m2"
    assert all(match.market.condition_id != "0xmatch" for match in matches)
    assert any(
        "conflicting grid series for event 808454" in rec.getMessage() for rec in caplog.records
    )


def test_grid_native_series_winner_reads_best_of_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GRID `best-of-3` on map 3 authorizes Match Winner the same way Steam series_type 1 does."""
    api = FakeSteamApi()
    api.set_league_games()
    body = series_winner_body()
    body["gridSeriesId"] = "2974458"
    write_sidecar(tmp_path, "0xmatch", body)
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        board = make_scoreboard(3, series_format="best-of-3")
        return _grid_cycle({series_id: board for series_id in series_ids})

    matches = make_discovery(tmp_path, api, probe).discover()

    assert len(matches) == 1
    assert matches[0].match_id == "grid-2974458-m3"
    assert matches[0].map_number == 3
    assert matches[0].market.condition_id == "0xmatch"


def test_grid_native_unparseable_format_skips_series_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unknown GRID series.format never authorizes Match Winner."""
    api = FakeSteamApi()
    api.set_league_games()
    body = series_winner_body()
    body["gridSeriesId"] = "2974458"
    write_sidecar(tmp_path, "0xmatch", body)
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        board = make_scoreboard(3, series_format="unknown-format")
        return _grid_cycle({series_id: board for series_id in series_ids})

    with caplog.at_level(logging.DEBUG, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api, probe).discover()

    assert matches == ()
    assert any("series_skips=1" in message for message in discovery_logs(caplog))


def test_cycle_sorts_steam_and_grid_ids_as_strings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mixed Steam/GRID cycle sorts by string match_id; GRID ids are not int()ed."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1")
    write_sidecar(
        tmp_path,
        "0x2",
        sidecar_body(
            conditionId="0x2",
            eventId="808455",
            marketSlug="dota2-fts-inner-game1",
            gridSeriesId="2995969",
            outcomes=[
                {"index": 0, "name": "Inner Circle", "tokenId": "TOKEN-A"},
                {"index": 1, "name": "FTS", "tokenId": "TOKEN-B"},
            ],
        ),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        boards = {
            "2974458": make_scoreboard(1),
            "2995969": make_scoreboard(1, radiant="Inner Circle", dire="FTS"),
        }
        return _grid_cycle({series_id: boards[series_id] for series_id in series_ids})

    matches = make_discovery(tmp_path, api, probe).discover()

    ids = [match.match_id for match in matches]
    assert ids == ["grid-2974458-m1", "grid-2995969-m1"]
    assert matches[0].steam_match_id == "100"
    assert matches[1].steam_match_id is None


def test_steam_first_with_grid_series_uses_canonical_archive_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Steam-first Dota keeps the numeric Steam id in steam_match_id, not match_id."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)

    with caplog.at_level(logging.INFO, logger="trader.discovery"):
        matches = make_discovery(tmp_path, api).discover()

    assert len(matches) == 1
    match = matches[0]
    assert match.match_id == "grid-2974458-m1"
    assert match.steam_match_id == "100"
    assert match.market.grid_series_id == "2974458"
    logs = discovery_logs(caplog)
    emit = next(line for line in logs if line.startswith("discovery emit:"))
    assert "archive_id_kind=grid" in emit
    assert "cid=0x1" in emit
    assert "match_id=grid-2974458-m1" in emit
    assert "steam_match_id=100" in emit
    assert all("discovery cycle:" not in line for line in logs)


def test_grid_only_handoff_uses_grid_match_id_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GRID-only emit uses the same grid-{series}-m{map} helper as Steam-first."""
    api = FakeSteamApi()
    api.set_league_games()
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        return _grid_cycle({series_id: make_scoreboard(1) for series_id in series_ids})

    matches = make_discovery(tmp_path, api, probe).discover()

    assert len(matches) == 1
    assert matches[0].match_id == discovery.grid_match_id("2974458", 1)
    assert matches[0].steam_match_id is None


GRID_SERIES = "2974458"


class _SequencedProbe:
    """Scripted GridProbeCycle returns; empty asks get an empty cycle."""

    def __init__(self, cycles: list[GridProbeCycle]) -> None:
        self.asked: list[tuple[str, ...]] = []
        self._cycles = cycles
        self._index = 0

    def __call__(self, series_ids: Iterable[str]) -> GridProbeCycle:
        asked = tuple(sorted(series_ids))
        self.asked.append(asked)
        if not asked:
            return GridProbeCycle({}, frozenset())
        cycle = self._cycles[self._index]
        self._index += 1
        return cycle


def _board_with_wins(series_format: str, maps_won_0: int, maps_won_1: int) -> Scoreboard:
    """Live scoreboard with a series format and per-team maps_won."""
    board = make_scoreboard(1, series_format=series_format)
    return replace(
        board,
        teams=(
            replace(board.teams[0], maps_won=maps_won_0),
            replace(board.teams[1], maps_won=maps_won_1),
        ),
    )


def _prime_grid_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSteamApi:
    """One tradeable map_winner sidecar and an empty Steam live list."""
    api = FakeSteamApi()
    api.set_league_games()
    write_sidecar(tmp_path, "0x1")
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    return api


def test_decided_bo3_then_404_skips_next_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live BO3 2-0 board plus a later 404 skips that series on the next cycle."""
    api = _prime_grid_sidecar(tmp_path, monkeypatch)
    live = GridProbeCycle({GRID_SERIES: _board_with_wins("best-of-3", 2, 0)}, frozenset())
    gone = GridProbeCycle({}, frozenset({GRID_SERIES}))
    probe = _SequencedProbe([live, gone])
    target = make_discovery(tmp_path, api, probe)
    target.discover()
    target.discover()
    target.discover()
    assert probe.asked[0] == (GRID_SERIES,)
    assert probe.asked[1] == (GRID_SERIES,)
    assert probe.asked[2] == ()


def test_bo5_break_404_keeps_probing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A live BO5 2-1 board plus a 404 is a break, not a series end."""
    api = _prime_grid_sidecar(tmp_path, monkeypatch)
    live = GridProbeCycle({GRID_SERIES: _board_with_wins("best-of-5", 2, 1)}, frozenset())
    gone = GridProbeCycle({}, frozenset({GRID_SERIES}))
    probe = _SequencedProbe([live, gone, gone])
    target = make_discovery(tmp_path, api, probe)
    target.discover()
    target.discover()
    target.discover()
    assert probe.asked == [(GRID_SERIES,), (GRID_SERIES,), (GRID_SERIES,)]


def test_404_without_prior_live_board_keeps_probing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart 404 with no last live board does not skip the next cycle."""
    api = _prime_grid_sidecar(tmp_path, monkeypatch)
    gone = GridProbeCycle({}, frozenset({GRID_SERIES}))
    probe = _SequencedProbe([gone, gone])
    target = make_discovery(tmp_path, api, probe)
    target.discover()
    target.discover()
    assert probe.asked == [(GRID_SERIES,), (GRID_SERIES,)]


def test_decided_skip_expires_after_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After the skip window, discovery probes the decided series again."""
    api = _prime_grid_sidecar(tmp_path, monkeypatch)
    clock = {"t": 100.0}
    monkeypatch.setattr("trader.discovery.monotonic_clock", lambda: clock["t"])
    live = GridProbeCycle({GRID_SERIES: _board_with_wins("best-of-3", 2, 0)}, frozenset())
    gone = GridProbeCycle({}, frozenset({GRID_SERIES}))
    probe = _SequencedProbe([live, gone, gone])
    target = make_discovery(tmp_path, api, probe)
    target.discover()
    target.discover()
    clock["t"] = 100.0 + discovery.SERIES_ENDED_SKIP_SECONDS
    target.discover()
    assert probe.asked[0] == (GRID_SERIES,)
    assert probe.asked[1] == (GRID_SERIES,)
    assert probe.asked[2] == (GRID_SERIES,)


def test_unpublished_info_logs_once_per_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Two 404 cycles on a never-live series write one InvalidStatus INFO line."""
    api = _prime_grid_sidecar(tmp_path, monkeypatch)
    gone = GridProbeCycle({}, frozenset({GRID_SERIES}))
    probe = _SequencedProbe([gone, gone])
    target = make_discovery(tmp_path, api, probe)
    with caplog.at_level(logging.INFO, logger="trader.discovery"):
        target.discover()
        target.discover()
    messages = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.INFO and "InvalidStatus" in rec.getMessage()
    ]
    assert len(messages) == 1
    assert GRID_SERIES in messages[0]


def test_oddin_emits_on_steam_link_without_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oddin binds onto a Steam-linked identity when the market has no GRID series."""
    api = FakeSteamApi()
    api.set_league_games(league_row(100))
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1", gridSeriesId=None))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)

    matches = make_discovery(
        tmp_path, api, list_oddin_matches=lambda: (make_oddin_card(),)
    ).discover()

    assert len(matches) == 1
    match = matches[0]
    assert match.oddin_match_id == "od:match:3211324"
    assert match.map_number == 1


def test_oddin_does_not_create_steam_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Bitsler card without a Steam/GRID binding is not a market."""
    api = FakeSteamApi()
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1", gridSeriesId=None))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)
    discovery = make_discovery(tmp_path, api, list_oddin_matches=lambda: (make_oddin_card(),))
    assert discovery.discover() == ()
    api.set_league_games(league_row(100))
    matches = discovery.discover()
    assert len(matches) == 1
    assert matches[0].oddin_match_id == "od:match:3211324"


def test_oddin_1win_alias_orients_against_steam_1w(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bitsler 1w binds onto Steam 1win through the existing alias table."""
    api = FakeSteamApi()
    api.set_league_games(league_row(100, radiant="1win", dire="Team Nemesis"))
    write_sidecar(
        tmp_path,
        "0x1",
        sidecar_body(
            conditionId="0x1",
            gridSeriesId=None,
            outcomes=[
                outcome(0, "1win", "TOKEN0"),
                outcome(1, "Team Nemesis", "TOKEN1"),
            ],
        ),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)
    card = make_oddin_card(home="1w", away="Team Nemesis")

    matches = make_discovery(tmp_path, api, list_oddin_matches=lambda: (card,)).discover()

    assert len(matches) == 1
    assert matches[0].oddin_match_id == card.id
    assert matches[0].market.yes_is_radiant is True


def test_oddin_swapped_home_away_still_binds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Home/away inverted vs Radiant/Dire still binds; faction is a feed concern."""
    api = FakeSteamApi()
    api.set_league_games(league_row(100))
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1", gridSeriesId=None))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)
    card = make_oddin_card(home="Team Secret", away="Aurora Gaming")

    matches = make_discovery(tmp_path, api, list_oddin_matches=lambda: (card,)).discover()

    assert len(matches) == 1
    assert matches[0].oddin_match_id == card.id


def test_oddin_ambiguous_two_cards_does_not_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two name hits whose snapshots are not in progress leave Oddin unbound: no market."""
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1", gridSeriesId=None))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)

    def neither_playing(match_ids: Sequence[str]) -> dict[str, bool]:
        return {match_id: False for match_id in match_ids}

    monkeypatch.setattr("trader.oddin_discovery.probe_playing", neither_playing)
    cards = (
        make_oddin_card("od:match:1"),
        make_oddin_card("od:match:2"),
    )

    matches = make_discovery(tmp_path, api, list_oddin_matches=lambda: cards).discover()

    assert matches == ()


def test_oddin_stamps_map_from_steam_series_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Map number comes from Steam series score; Oddin is only a feed id."""
    api = FakeSteamApi()
    api.set_league_games(league_row(200, series_type=1, radiant_wins=1, dire_wins=0))
    write_sidecar(
        tmp_path,
        "0x2",
        sidecar_body(
            conditionId="0x2",
            mapNumber=2,
            gridSeriesId=None,
            marketSlug="dota2-aurora-secret-game2",
        ),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)

    matches = make_discovery(
        tmp_path, api, list_oddin_matches=lambda: (make_oddin_card(),)
    ).discover()

    assert len(matches) == 1
    assert matches[0].map_number == 2
    assert matches[0].oddin_match_id == "od:match:3211324"


def test_oddin_emits_while_grid_sides_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oddin may emit on Steam orientation while GRID still has no Radiant/Dire."""
    api = FakeSteamApi()
    prime_happy_api(api, series_type=1, radiant_wins=0, dire_wins=1)
    write_sidecar(
        tmp_path,
        "0x2",
        sidecar_body(conditionId="0x2", mapNumber=2, marketSlug="dota2-aurora-secret-game2"),
    )
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        board = replace(make_scoreboard(2), game_status=UPCOMING_STATUS, teams=())
        return _grid_cycle({series_id: board for series_id in series_ids})

    matches = make_discovery(
        tmp_path, api, probe, list_oddin_matches=lambda: (make_oddin_card(),)
    ).discover()
    assert len(matches) == 1
    assert matches[0].oddin_match_id == "od:match:3211324"
    assert matches[0].map_number == 2


def test_oddin_list_runs_without_live_league_games(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eligible sidecars still list Bitsler; Oddin-only does not emit."""
    api = FakeSteamApi()
    write_sidecar(tmp_path, "0x1", sidecar_body(conditionId="0x1", gridSeriesId=None))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)
    calls: list[int] = []

    def list_oddin() -> tuple[OddinMatch, ...]:
        calls.append(1)
        return (make_oddin_card(),)

    assert make_discovery(tmp_path, api, list_oddin_matches=list_oddin).discover() == ()
    assert calls == [1]


def test_oddin_list_skipped_without_eligible_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Dota sidecar means no Bitsler list."""
    api = FakeSteamApi()
    api.set_league_games(league_row(100))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    calls: list[int] = []

    def list_oddin() -> tuple[OddinMatch, ...]:
        calls.append(1)
        return (make_oddin_card(),)

    assert make_discovery(tmp_path, api, list_oddin_matches=list_oddin).discover() == ()
    assert calls == []
