"""Oddin LoL watcher terminal formatting and feed payload extraction."""

import json

from watch_oddin_lol import format_tick_lines, next_scoreboard, subscribe_message


def _side(faction: str, name: str, kills: int, gold: int) -> dict[str, object]:
    return {
        "faction": faction,
        "kills": kills,
        "barons": 1,
        "dragons": 2,
        "turrets": 3,
        "inhibitors": 0,
        "gold": gold,
        "won": False,
        "team": {"name": name},
        "players": [
            {
                "kills": 5,
                "deaths": 2,
                "assists": 4,
                "creepScore": 210,
                "respawnTimer": 12,
                "player": {"nickname": "Faker"},
                "champion": {"name": "Ahri"},
            }
        ],
    }


def _tick() -> dict[str, object]:
    return {
        "id": "x",
        "dataStatus": "VALID_DATA",
        "lastUpdatedAt": "2026-09-20 09:40:12.123456 +0000 UTC",
        "mapPaused": False,
        "matchStatus": "LIVE",
        "homeScore": 0,
        "awayScore": 1,
        "homeTeam": {"name": "Movistar KOI Academy"},
        "awayTeam": {"name": "CFO Academy"},
        "currentMap": {
            "id": "m1",
            "mapOrder": 2,
            "gameTime": 757,
            "homeTeam": _side("BLUE", "Movistar KOI Academy", 12, 35820),
            "awayTeam": _side("RED", "CFO Academy", 9, 34110),
        },
        "previousMaps": [],
    }


def test_format_tick_has_clock_score_gold_and_players() -> None:
    lines = format_tick_lines(7, 0.214, "ws", _tick())
    assert lines[0] == "#7 dt=0.214s source=ws updated=2026-09-20 09:40:12.123456 +0000 UTC"
    assert "t=12:37" in lines[1]
    assert "LIVE" in lines[1]
    assert "map=2" in lines[1]
    assert "series=0:1" in lines[1]
    assert "kills=12:9" in lines[1]
    assert "gold=35820:34110" in lines[1]
    assert "lead=+1710" in lines[1]
    assert "paused=no" in lines[1]
    assert "BLUE Movistar KOI Academy" in lines[2]
    assert "Faker Ahri 5/2/4 cs=210 dead=12" in lines[2]
    assert "RED CFO Academy" in lines[3]
    assert "BLUE turrets=3 inhib=0 drag=2 baron=1" in lines[4]


def test_format_tick_without_current_map() -> None:
    tick = _tick()
    tick["currentMap"] = None
    lines = format_tick_lines(1, None, "snapshot", tick)
    assert lines[0].startswith("#1 dt=- source=snapshot")
    assert "t=-" in lines[1]
    assert "home Movistar KOI Academy" in lines[2]


def test_next_scoreboard_reads_feed_field() -> None:
    payload = {"data": {"onLolScoreboardFeed": _tick()}}
    assert next_scoreboard(payload) == _tick()
    assert next_scoreboard({"data": {"other": 1}}) is None
    assert next_scoreboard(None) is None


def test_subscribe_message_targets_lol_feed() -> None:
    frame = json.loads(subscribe_message("abc"))
    assert frame["type"] == "subscribe"
    assert frame["payload"]["variables"] == {"matchId": "abc"}
    assert "onLolScoreboardFeed" in frame["payload"]["query"]
