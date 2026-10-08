"""Print every Oddin LoL scoreboard payload for one match id.

Same widget socket as watch_oddin_live.py, but LoL payloads are plain JSON
(`lolScoreboard` query / `onLolScoreboardFeed` subscription), not the
encrypted `.js2cc*` envelopes Dota 2 uses. Without an argument it lists the
open LoL matches from `tournaments` + `lolTournamentInfo`.

Invocation:
  make run F=scripts/watch_oddin_lol.py
  make run F=scripts/watch_oddin_lol.py ARGS="T1"
  make run F=scripts/watch_oddin_lol.py ARGS="3218074"
  make run F=scripts/watch_oddin_lol.py ARGS="<match url>"
"""

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import httpx
import websockets

from shared.utils.json_read import as_map, read_str, try_int
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_time import format_clock
from trader.oddin_catalog import fetch_open_lol_matches, log_matches, select_open_match
from trader.oddin_client import (
    MAX_CONSECUTIVE_FAILURES,
    ODDIN_ORIGIN,
    ODDIN_QUERY,
    RECONNECT_SECONDS,
    REQUEST_TIMEOUT_S,
    connection_init_message,
    direct_match_id,
    disir_client,
    graphql_headers,
    parse_socket_frame,
    pong_message,
    require_brand_token,
    websocket_url,
    widget_config,
)
from trader.oddin_types import OddinFeedError, WidgetConfig

logger = get_logger(__name__)

FINISHED_STATUS = "FINISHED"
STALE_SECONDS = 15.0

SCOREBOARD_FIELDS = (
    "id dataStatus lastUpdatedAt mapPaused matchStatus homeScore awayScore "
    "homeTeam { name } awayTeam { name } "
    "currentMap { ...Map } previousMaps { ...Map }"
)
MAP_FIELDS = "id mapOrder gameTime homeTeam { ...Side } awayTeam { ...Side }"
SIDE_FIELDS = (
    "faction kills barons dragons turrets inhibitors gold won "
    "team { name } "
    "players { kills deaths assists creepScore respawnTimer "
    "player { nickname } champion { name } }"
)
FRAGMENTS = (
    f"fragment Map on LolMapScoreboard {{ {MAP_FIELDS} }} "
    f"fragment Side on LolTeamScoreboard {{ {SIDE_FIELDS} }}"
)
SNAPSHOT_QUERY = (
    f"query lolScoreboard($matchId: ID!) {{ lolScoreboard(matchId: $matchId) "
    f"{{ {SCOREBOARD_FIELDS} }} }} {FRAGMENTS}"
)
SUBSCRIBE_QUERY = (
    f"subscription onLolScoreboard($matchId: ID!) "
    f"{{ onLolScoreboardFeed(matchId: $matchId) {{ {SCOREBOARD_FIELDS} }} }} {FRAGMENTS}"
)


async def fetch_snapshot(
    client: httpx.AsyncClient, config: WidgetConfig
) -> dict[str, object] | None:
    """One HTTP `lolScoreboard` snapshot, or None when none is published."""
    response = await client.post(
        ODDIN_QUERY,
        headers=graphql_headers(config.brand_token),
        json={
            "operationName": "lolScoreboard",
            "query": SNAPSHOT_QUERY,
            "variables": {"matchId": config.match_id},
        },
    )
    response.raise_for_status()
    body = as_map(response.json()) or {}
    errors = body.get("errors")
    if errors:
        logger.info("snapshot: %s", errors)
        return None
    data = as_map(body.get("data")) or {}
    return as_map(data.get("lolScoreboard"))


def next_scoreboard(payload: object) -> dict[str, object] | None:
    """Plain `onLolScoreboardFeed` object out of a `next` frame payload."""
    mapped = as_map(payload)
    if mapped is None:
        return None
    data = as_map(mapped.get("data"))
    if data is None:
        return None
    return as_map(data.get("onLolScoreboardFeed"))


def subscribe_message(match_id: str) -> str:
    """`subscribe` frame for the LoL scoreboard feed."""
    return json.dumps(
        {
            "id": "scoreboard",
            "type": "subscribe",
            "payload": {
                "operationName": "onLolScoreboard",
                "variables": {"matchId": match_id},
                "query": SUBSCRIBE_QUERY,
            },
        }
    )


async def iter_scoreboard_payloads(
    config: WidgetConfig, stale_seconds: float
) -> AsyncIterator[dict[str, object]]:
    """Connect, init, subscribe, answer pings, yield each plain `next` payload.

    Returns on `complete`; raises OddinFeedError on an error frame or a
    non-JSON frame; TimeoutError when no frame for stale_seconds.
    """
    async with websockets.connect(
        websocket_url(config.brand_token),
        origin=cast(websockets.Origin, ODDIN_ORIGIN),
        subprotocols=[cast(websockets.Subprotocol, "graphql-transport-ws")],
        max_size=None,
        ping_interval=10,
        ping_timeout=10,
    ) as socket:
        await socket.send(connection_init_message(config.brand_token))
        while True:
            raw = await asyncio.wait_for(socket.recv(), timeout=stale_seconds)
            frame = parse_socket_frame(str(raw))
            if frame is None:
                raise OddinFeedError("oddin socket non-JSON frame")
            if frame.type == "connection_ack":
                await socket.send(subscribe_message(config.match_id))
                continue
            if frame.type == "ping":
                await socket.send(pong_message(frame.payload))
                continue
            if frame.type == "next":
                scoreboard = next_scoreboard(frame.payload)
                if scoreboard is not None:
                    yield scoreboard
                continue
            if frame.type == "error":
                raise OddinFeedError(f"oddin socket error frame: {frame.payload}")
            if frame.type == "complete":
                return


def format_player(raw: object) -> str:
    """`Faker Ahri 5/2/4 cs=210 dead=12`."""
    fields = as_map(raw) or {}
    player = as_map(fields.get("player")) or {}
    champion = as_map(fields.get("champion")) or {}
    kills = try_int(fields.get("kills")) or 0
    deaths = try_int(fields.get("deaths")) or 0
    assists = try_int(fields.get("assists")) or 0
    cs = try_int(fields.get("creepScore"))
    respawn = try_int(fields.get("respawnTimer"))
    suffix = f" dead={respawn}" if respawn else ""
    cs_text = f" cs={cs}" if cs is not None else ""
    return (
        f"{read_str(player, 'nickname') or '?'} {read_str(champion, 'name') or '?'} "
        f"{kills}/{deaths}/{assists}{cs_text}{suffix}"
    )


def format_side(raw: object, fallback: str) -> tuple[str, str]:
    """(player line, objectives) for one map side."""
    fields = as_map(raw) or {}
    team = as_map(fields.get("team")) or {}
    faction = read_str(fields, "faction") or fallback
    name = read_str(team, "name") or fallback
    players_raw = fields.get("players")
    players = ""
    if isinstance(players_raw, list):
        players = "  ".join(format_player(item) for item in cast(list[object], players_raw))
    objectives = (
        f"{faction} turrets={try_int(fields.get('turrets')) or 0} "
        f"inhib={try_int(fields.get('inhibitors')) or 0} "
        f"drag={try_int(fields.get('dragons')) or 0} "
        f"baron={try_int(fields.get('barons')) or 0}"
    )
    return f"     {faction} {name:<20} {players}", objectives


def format_tick_lines(
    seq: int, dt: float | None, source: str, tick: Mapping[str, object]
) -> tuple[str, ...]:
    """The terminal block for one received payload."""
    current = as_map(tick.get("currentMap"))
    game_time = try_int(current.get("gameTime")) if current else None
    map_order = try_int(current.get("mapOrder")) if current else None
    clock = "-" if game_time is None else format_clock(game_time)
    home_score = try_int(tick.get("homeScore")) or 0
    away_score = try_int(tick.get("awayScore")) or 0
    paused = "yes" if tick.get("mapPaused") is True else "no"
    home = as_map(tick.get("homeTeam")) or {}
    away = as_map(tick.get("awayTeam")) or {}
    if current is None:
        kills = "-"
        gold = "-"
        lead = "-"
        sides = (
            f"     home {read_str(home, 'name') or 'Home'}",
            f"     away {read_str(away, 'name') or 'Away'}",
        )
        objectives = "     objectives -"
    else:
        home_side = as_map(current.get("homeTeam")) or {}
        away_side = as_map(current.get("awayTeam")) or {}
        home_kills = try_int(home_side.get("kills")) or 0
        away_kills = try_int(away_side.get("kills")) or 0
        home_gold = try_int(home_side.get("gold"))
        away_gold = try_int(away_side.get("gold"))
        kills = f"{home_kills}:{away_kills}"
        gold = f"{home_gold}:{away_gold}" if home_gold is not None else "-"
        lead = "-" if home_gold is None or away_gold is None else f"{home_gold - away_gold:+d}"
        home_line, home_obj = format_side(current.get("homeTeam"), "home")
        away_line, away_obj = format_side(current.get("awayTeam"), "away")
        sides = (home_line, away_line)
        objectives = f"     objectives {home_obj} | {away_obj}"
    header = (
        f"#{seq} dt={'-' if dt is None else f'{dt:.3f}s'} source={source} "
        f"updated={read_str(tick, 'lastUpdatedAt') or '-'}"
    )
    summary = (
        f"t={clock} {read_str(tick, 'matchStatus') or '?'} map={map_order or '-'} "
        f"series={home_score}:{away_score} kills={kills} "
        f"gold={gold} lead={lead} paused={paused} "
        f"data={read_str(tick, 'dataStatus') or '?'}"
    )
    return (header, summary, *sides, objectives)


@dataclass(frozen=True)
class WatchState:
    """Sequence and timing carried across snapshot and websocket payloads."""

    seq: int
    last_at: float | None


def emit_payload(
    payload: Mapping[str, object], source: str, state: WatchState, now: float
) -> WatchState:
    """Print one received payload and advance the watch state."""
    dt = None if state.last_at is None else now - state.last_at
    for line in format_tick_lines(state.seq, dt, source, payload):
        logger.info("%s", line)
    return WatchState(state.seq + 1, now)


async def follow_match(client: httpx.AsyncClient, match_id: str) -> int:
    """Print the HTTP snapshot, then every websocket `next`, reconnecting."""
    config = widget_config(match_id)
    logger.info(
        "following %s socket=%s stale_after=%.0fs",
        match_id,
        websocket_url(config.brand_token).split("?", 1)[0],
        STALE_SECONDS,
    )
    state = WatchState(1, None)
    failures = 0
    while failures < MAX_CONSECUTIVE_FAILURES:
        saw_data = False
        try:
            snapshot = await fetch_snapshot(client, config)
            if snapshot is not None:
                state = emit_payload(snapshot, "snapshot", state, time.monotonic())
                saw_data = True
                if read_str(snapshot, "matchStatus") == FINISHED_STATUS:
                    return 0
            async for payload in iter_scoreboard_payloads(config, STALE_SECONDS):
                state = emit_payload(payload, "ws", state, time.monotonic())
                saw_data = True
                if read_str(payload, "matchStatus") == FINISHED_STATUS:
                    return 0
            if saw_data:
                failures = 0
                logger.info("scoreboard complete, reconnecting")
            else:
                failures += 1
                logger.warning(
                    "scoreboard complete without data (%s/%s)",
                    failures,
                    MAX_CONSECUTIVE_FAILURES,
                )
        except (
            OddinFeedError,
            TimeoutError,
            websockets.WebSocketException,
            httpx.HTTPError,
            OSError,
        ) as exc:
            failures += 1
            logger.warning("%s (%s/%s)", exc, failures, MAX_CONSECUTIVE_FAILURES)
        await asyncio.sleep(RECONNECT_SECONDS)
    logger.error("stopping: %s errors in a row", MAX_CONSECUTIVE_FAILURES)
    return 1


async def amain(argv: Sequence[str]) -> int:
    """List open LoL matches, or follow a listed name or a direct Oddin id."""
    token = require_brand_token()
    async with disir_client(REQUEST_TIMEOUT_S) as client:
        try:
            if len(argv) >= 2 and (match_id := direct_match_id(argv[1])) is not None:
                logger.info("match %s", match_id)
                return await follow_match(client, match_id)
            matches = await fetch_open_lol_matches(client, token, datetime.now(UTC))
            if len(argv) < 2:
                log_matches(matches, "lol")
                logger.info("pass a team name, match id, or match URL to follow")
                return 0
            match = select_open_match(matches, argv[1])
        except OddinFeedError as exc:
            raise SystemExit(str(exc)) from exc
        logger.info("match %s %s vs %s", match.id, match.home_name, match.away_name)
        return await follow_match(client, match.id)


def main() -> int:
    """CLI entry: list or follow one Oddin LoL match."""
    setup_logging()
    try:
        return asyncio.run(amain(sys.argv))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
