"""Widget/GraphQL parsing, socket frames, and scoreboard projection."""

import asyncio
import json
from collections.abc import Sequence
from typing import cast

import httpx
import pytest

from trader.oddin_client import (
    apply_socket_frame,
    connection_init_message,
    direct_match_id,
    fetch_snapshot_envelope,
    next_envelope,
    parse_socket_frame,
    pong_message,
    subscribe_message,
    widget_config,
)
from trader.oddin_crypto import DEFAULT_KEY_HEX, parse_feed_key, seal_envelope
from trader.oddin_discovery import snapshot_is_playing, unique_oddin_match_id
from trader.oddin_feed import open_tick, project_tick
from trader.oddin_types import VALID_DATA, OddinFeedError, OddinMatch, OddinTick, WidgetConfig


def test_direct_match_id_numeric_prefixed_and_url() -> None:
    assert direct_match_id("3211324") == "od:match:3211324"
    assert direct_match_id("od:match:3211324") == "od:match:3211324"
    assert (
        direct_match_id("https://www.bitsler.com/sports/esports?id=od:match:3211324")
        == "od:match:3211324"
    )
    assert (
        direct_match_id("https://www.bitsler.com/esports/dota-2/od:match:3211324")
        == "od:match:3211324"
    )
    assert direct_match_id("https://www.bitsler.com/match/3211324") == "od:match:3211324"
    assert direct_match_id("Aurora") is None


def test_bitsler_url_without_id_fails() -> None:
    with pytest.raises(OddinFeedError, match="cannot parse match id"):
        direct_match_id("https://www.bitsler.com/sports/esports/dota-2")


def test_widget_config_encodes_match_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODDIN_BRAND_TOKEN", "brand-token")
    config = widget_config("od:match:3232747")
    assert config == WidgetConfig("brand-token", "bWF0Y2gvb2Q6bWF0Y2g6MzIzMjc0Nw==")


def test_widget_config_rejects_bare_id() -> None:
    with pytest.raises(OddinFeedError, match="not an oddin match id"):
        widget_config("3232747")


def _tick(match_status: str, data_status: str, map_order: int | None) -> OddinTick:
    return OddinTick(
        match_status,
        data_status,
        "",
        False,
        "Xtreme Gaming",
        "LGD Gaming",
        0,
        0,
        map_order,
        10,
        None,
        None,
    )


def test_snapshot_is_playing_requires_a_live_map() -> None:
    assert snapshot_is_playing(_tick("LIVE", VALID_DATA, 2))
    assert not snapshot_is_playing(_tick("FINISHED", VALID_DATA, 2))
    assert not snapshot_is_playing(_tick("LIVE", "INVALID_DATA", 2))
    assert not snapshot_is_playing(_tick("LIVE", VALID_DATA, None))


def _xtreme(match_id: str) -> OddinMatch:
    return OddinMatch(
        match_id,
        "Xtreme Gaming",
        "LGD Gaming",
        0,
        0,
        False,
        "",
        "",
        14906,
        "PGL Wallachia Season 9",
    )


def test_two_name_hits_keep_the_playing_card(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    answers = {"od:match:3232747": True, "od:match:3239529": False}

    def fake_probe(match_ids: Sequence[str]) -> dict[str, bool]:
        calls.append(tuple(match_ids))
        return {match_id: answers[match_id] for match_id in match_ids}

    monkeypatch.setattr("trader.oddin_discovery.probe_playing", fake_probe)
    pgl = _xtreme("od:match:3232747")
    blast = _xtreme("od:match:3239529")
    playing: dict[str, bool] = {}
    bound = unique_oddin_match_id("Xtreme Gaming", "LGD Gaming", (pgl, blast), {}, playing)
    assert bound == pgl.id
    assert unique_oddin_match_id("Xtreme Gaming", "LGD Gaming", (pgl, blast), {}, playing) == pgl.id
    assert calls == [(pgl.id, blast.id)]

    answers["od:match:3232747"] = False
    unbound = unique_oddin_match_id("Xtreme Gaming", "LGD Gaming", (pgl, blast), {}, {})
    assert unbound is None


def test_graphql_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "tok"
        return httpx.Response(200, json={"errors": [{"message": "deprecated"}]})

    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_snapshot_envelope(client, WidgetConfig("tok", "abc"))

    with pytest.raises(OddinFeedError, match="deprecated"):
        asyncio.run(run())


def test_graphql_snapshot_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"dota2ScoreboardData": {"id": "x", "data": ".js2cc*abc"}}},
        )

    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_snapshot_envelope(client, WidgetConfig("tok", "abc"))

    assert asyncio.run(run()) == ".js2cc*abc"


def test_socket_frames_ack_ping_next_error_complete() -> None:
    ack = apply_socket_frame(parse_socket_frame('{"type":"connection_ack"}'))
    assert ack.send_subscribe is True
    ping = apply_socket_frame(parse_socket_frame('{"type":"ping","payload":{"x":1}}'))
    assert ping.send_pong is True
    assert pong_message(ping.pong_payload) == '{"type": "pong", "payload": {"x": 1}}'
    nxt = apply_socket_frame(
        parse_socket_frame(
            json.dumps(
                {
                    "id": "scoreboard",
                    "type": "next",
                    "payload": {
                        "data": {"onDota2ScoreboardFeedData": {"id": "x", "data": ".js2cc*z"}}
                    },
                }
            )
        )
    )
    assert nxt.envelope == ".js2cc*z"
    err = apply_socket_frame(parse_socket_frame('{"type":"error","payload":[{"message":"no"}]}'))
    assert err.reconnect is True
    done = apply_socket_frame(parse_socket_frame('{"id":"scoreboard","type":"complete"}'))
    assert done.stream_complete is True


def test_invalid_json_frame() -> None:
    assert parse_socket_frame("{") is None
    assert apply_socket_frame(None).reconnect is True


def test_protocol_helpers() -> None:
    assert "connection_init" in connection_init_message("tok")
    assert "OnDota2ScoreboardFeedEncrypted" in subscribe_message("abc")


def test_next_envelope_requires_field() -> None:
    with pytest.raises(OddinFeedError):
        next_envelope({"data": {}})


def _player(
    nickname: str, hero: str, net_worth: int, kda: tuple[int, int, int]
) -> dict[str, object]:
    return {
        "player": {"nickname": nickname},
        "hero": {"name": hero},
        "netWorth": net_worth,
        "kills": kda[0],
        "deaths": kda[1],
        "assists": kda[2],
        "alive": True,
        "hasAegis": False,
    }


def _map_team(name: str, faction: str, kills: int, net_worth: int) -> dict[str, object]:
    return {
        "team": {"name": name},
        "faction": faction,
        "kills": kills,
        "netWorth": 0,
        "netWorthNullable": net_worth,
        "towers": 4 if faction == "RADIANT" else 3,
        "barracks": 0,
        "barracksNullable": 0,
        "roshans": 1 if faction == "RADIANT" else 0,
        "players": [_player(name[:4], "Kez", net_worth // 5, (1, 0, 0))],
    }


def test_home_can_be_dire_and_away_radiant() -> None:
    tick = project_tick(
        {
            "matchStatus": "LIVE",
            "dataStatus": "VALID_DATA",
            "lastUpdatedAt": "2026-09-19 16:44:12.123 +0000 UTC",
            "mapPaused": False,
            "homeScore": 0,
            "awayScore": 1,
            "homeTeam": {"name": "Aurora Gaming"},
            "awayTeam": {"name": "Pipsqueak+4"},
            "currentMap": {
                "mapOrder": 2,
                "gameTime": 757,
                "homeTeam": _map_team("Aurora Gaming", "DIRE", 9, 34110),
                "awayTeam": _map_team("Pipsqueak+4", "RADIANT", 12, 35820),
            },
        }
    )
    assert tick.radiant is not None and tick.dire is not None
    assert tick.radiant.name == "Pipsqueak+4"
    assert tick.dire.name == "Aurora Gaming"
    assert tick.radiant.kills == 12
    assert tick.dire.kills == 9
    assert tick.radiant.net_worth == 35820
    assert tick.dire.net_worth == 34110
    assert tick.net_worth_lead == 1710
    assert tick.radiant.players[0].nickname == "Pips"
    assert tick.radiant.players[0].hero == "Kez"


def test_player_missing_net_worth_or_deaths_is_omitted() -> None:
    missing_nw = _map_team("A", "RADIANT", 0, 100)
    nw_players = cast(list[dict[str, object]], missing_nw["players"])
    del nw_players[0]["netWorth"]
    missing_deaths = _map_team("B", "DIRE", 0, 100)
    death_players = cast(list[dict[str, object]], missing_deaths["players"])
    del death_players[0]["deaths"]
    tick = project_tick(
        {
            "matchStatus": "LIVE",
            "dataStatus": "VALID_DATA",
            "lastUpdatedAt": "now",
            "homeTeam": {"name": "A"},
            "awayTeam": {"name": "B"},
            "homeScore": 0,
            "awayScore": 0,
            "currentMap": {
                "mapOrder": 1,
                "gameTime": 10,
                "homeTeam": missing_nw,
                "awayTeam": missing_deaths,
            },
        }
    )
    assert tick.radiant is not None and tick.dire is not None
    assert tick.radiant.players == ()
    assert tick.dire.players == ()


def test_current_map_missing() -> None:
    tick = project_tick(
        {
            "matchStatus": "LIVE",
            "dataStatus": "VALID_DATA",
            "lastUpdatedAt": "now",
            "homeTeam": {"name": "A"},
            "awayTeam": {"name": "B"},
            "homeScore": 0,
            "awayScore": 0,
        }
    )
    assert tick.radiant is None
    assert tick.dire is None
    assert tick.map_order is None
    assert tick.game_time is None


def test_pre_horn_negative_game_time() -> None:
    tick = project_tick(
        {
            "matchStatus": "LIVE",
            "dataStatus": "VALID_DATA",
            "lastUpdatedAt": "now",
            "homeTeam": {"name": "A"},
            "awayTeam": {"name": "B"},
            "homeScore": 0,
            "awayScore": 0,
            "currentMap": {
                "mapOrder": 1,
                "gameTime": -45,
                "homeTeam": _map_team("A", "RADIANT", 0, 0),
                "awayTeam": _map_team("B", "DIRE", 0, 0),
            },
        }
    )
    assert tick.game_time == -45


def test_paused_and_finished() -> None:
    paused = project_tick(
        {
            "matchStatus": "LIVE",
            "dataStatus": "VALID_DATA",
            "lastUpdatedAt": "now",
            "mapPaused": True,
            "homeTeam": {"name": "A"},
            "awayTeam": {"name": "B"},
            "homeScore": 1,
            "awayScore": 1,
            "currentMap": {
                "mapOrder": 2,
                "gameTime": 10,
                "homeTeam": _map_team("A", "RADIANT", 0, 1),
                "awayTeam": _map_team("B", "DIRE", 0, 1),
            },
        }
    )
    assert paused.map_paused is True
    finished = project_tick(
        {
            "matchStatus": "FINISHED",
            "dataStatus": "VALID_DATA",
            "lastUpdatedAt": "now",
            "homeTeam": {"name": "A"},
            "awayTeam": {"name": "B"},
            "homeScore": 2,
            "awayScore": 1,
        }
    )
    assert finished.match_status == "FINISHED"


def test_open_tick_through_envelope() -> None:
    key = parse_feed_key(DEFAULT_KEY_HEX)
    envelope = seal_envelope(
        {
            "matchStatus": "LIVE",
            "dataStatus": "VALID_DATA",
            "lastUpdatedAt": "now",
            "homeTeam": {"name": "A"},
            "awayTeam": {"name": "B"},
            "homeScore": 0,
            "awayScore": 0,
        },
        key,
        bytes(range(24)),
    )
    tick = open_tick(envelope, key)
    assert tick.home_name == "A"
    assert tick.away_name == "B"
