"""Hawk watcher helpers: Inertia parse, Pusher frames, cadence, kill marker."""

import html
import json
import logging

import pytest
from watch_hawk_live import (
    HawkWatch,
    apply_frame,
    extract_path,
    format_buildings,
    format_picks,
    is_echo_snapshot,
    median_gap,
    parse_pusher_frame,
    props_of,
    read_current_match,
    read_inertia_payload,
    read_series_page,
    read_tick,
    strip_event_prefix,
    tick_from_state_event,
)


def _series_html(page: dict[str, object]) -> str:
    blob = html.escape(json.dumps(page), quote=True)
    return f'<div id="app" data-page="{blob}"></div>'


def _page() -> dict[str, object]:
    return {
        "component": "SeriesPage",
        "props": {
            "wsConfig": {
                "host": "ws.hawk.live",
                "port": "8443",
                "key": "hawk-test",
                "shouldForceTls": True,
            },
            "seriesPageData": {
                "id": 100487,
                "slug": "1win-team-vs-team-nemesis",
                "championship": {
                    "id": 2403,
                    "name": "PGL Wallachia Season 9: Group Stage",
                    "slug": "pgl-wallachia-season-9-group-stage",
                },
                "team1": {"id": 1, "name": "1win Team"},
                "team2": {"id": 2, "name": "Team Nemesis"},
                "bestOf": 3,
                "startAt": "2026-09-19T07:00:00.000000Z",
                "streams": [{"name": "PGL_Dota2"}],
                "matches": [
                    {
                        "id": 213609,
                        "number": 1,
                        "isTeam1Radiant": False,
                        "isRadiantWinner": None,
                        "picks": [
                            {
                                "isRadiant": False,
                                "hero": {"name": "Tusk"},
                                "player": {"name": "Ari"},
                            },
                            {
                                "isRadiant": True,
                                "hero": {"name": "Luna"},
                                "player": {"name": "JACKBOYS"},
                            },
                        ],
                        "states": [
                            {
                                "id": 1,
                                "radiantScore": 22,
                                "direScore": 31,
                                "radiantNetWorthAdvantage": -10635,
                                "buildingState": {
                                    "radiant": {
                                        "top": "00111",
                                        "mid": "00011",
                                        "bot": "00111",
                                        "t4": "11",
                                    },
                                    "dire": {
                                        "top": "00111",
                                        "mid": "01111",
                                        "bot": "01111",
                                        "t4": "11",
                                    },
                                },
                                "gameTime": 2185,
                            }
                        ],
                    }
                ],
            },
        },
    }


def test_extract_path_from_url_or_bare_slug() -> None:
    url = (
        "https://hawk.live/dota-2/matches/"
        "pgl-wallachia-season-9-group-stage/1win-team-vs-team-nemesis"
    )
    assert extract_path(url).endswith("/1win-team-vs-team-nemesis")
    assert extract_path("1win-team-vs-team-nemesis") == "1win-team-vs-team-nemesis"


def test_read_series_page_last_state_and_sides() -> None:
    parsed = read_series_page(props_of(read_inertia_payload(_series_html(_page()))))
    assert parsed.series.team1 == "1win Team"
    assert parsed.is_team1_radiant is False
    assert parsed.last_tick is not None
    assert parsed.last_tick.game_time == 2185
    assert parsed.last_tick.radiant_score == 22
    assert parsed.last_tick.dire_score == 31
    assert parsed.last_tick.radiant_net_worth_advantage == -10635
    assert "netWorth" not in parsed.last_tick.keys
    assert format_picks(parsed.picks, True) == "Luna JACKBOYS"
    assert format_picks(parsed.picks, False) == "Tusk Ari"
    assert "t=00111" in format_buildings(parsed.last_tick)


def test_read_current_match_prefers_unfinished_map() -> None:
    matches: list[dict[str, object]] = [
        {"id": 1, "number": 1, "isRadiantWinner": False, "states": [1]},
        {"id": 2, "number": 2, "isRadiantWinner": None, "states": []},
    ]
    current = read_current_match(matches)
    assert current is not None
    assert current["id"] == 2


def test_median_gap_ignores_clock_resets() -> None:
    assert median_gap((31, 60, 90, 10, 40)) == 30.0
    assert median_gap((10,)) is None


def test_plus_two_duplicate_is_echo_snapshot() -> None:
    raw: dict[str, object] = {
        "id": 1,
        "radiantScore": 7,
        "direScore": 14,
        "radiantNetWorthAdvantage": -6000,
        "buildingState": {
            "radiant": {"top": "11111", "mid": "01111", "bot": "01111", "t4": "11"},
            "dire": {"top": "01111", "mid": "11111", "bot": "11111", "t4": "11"},
        },
        "gameTime": 946,
    }
    first = read_tick(raw, 213809)
    echo = read_tick({**raw, "id": 2, "gameTime": 948}, 213809)
    later = read_tick({**raw, "id": 3, "gameTime": 1006, "radiantScore": 8}, 213809)
    assert is_echo_snapshot(first, echo) is True
    assert is_echo_snapshot(first, later) is False


def test_parse_pusher_match_state_and_kill(caplog: pytest.LogCaptureFixture) -> None:
    raw = json.dumps(
        {
            "event": "App\\Events\\Match\\MatchStateCreated",
            "channel": "series.100487",
            "data": json.dumps(
                {
                    "matchId": 213609,
                    "matchState": {
                        "id": 2,
                        "radiantScore": 23,
                        "direScore": 31,
                        "radiantNetWorthAdvantage": -11000,
                        "buildingState": {
                            "radiant": {
                                "top": "00111",
                                "mid": "00011",
                                "bot": "00111",
                                "t4": "11",
                            },
                            "dire": {
                                "top": "00111",
                                "mid": "01111",
                                "bot": "01111",
                                "t4": "11",
                            },
                        },
                        "gameTime": 2210,
                    },
                }
            ),
        }
    )
    frame = parse_pusher_frame(raw)
    assert frame.event == "Match\\MatchStateCreated"
    tick = tick_from_state_event(frame.payload)
    assert tick is not None
    assert tick.radiant_score == 23
    page = read_series_page(props_of(read_inertia_payload(_series_html(_page()))))
    watch = HawkWatch(page)
    with caplog.at_level(logging.INFO):
        apply_frame(watch, frame)
    assert ">>> score 22:31 -> 23:31 in t=36:25..36:50" in caplog.text
    assert strip_event_prefix("App\\Events\\MatchFinished") == "MatchFinished"
    hello = parse_pusher_frame(
        json.dumps({"event": "pusher:connection_established", "data": '{"socket_id":"1.2"}'})
    )
    assert hello.event == "pusher:connection_established"
