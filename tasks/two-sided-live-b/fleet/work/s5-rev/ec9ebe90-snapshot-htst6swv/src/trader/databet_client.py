"""Thunderpick match API, DATA.BET GraphQL HTTP, and graphql-transport-ws feed.

Thunderpick sits behind Cloudflare: plain httpx/curl get a 403 on TLS
fingerprint, so every REST call goes through a Chrome-impersonating
`curl_cffi` session. The DATA.BET endpoints are not fingerprinted and accept
any client.
"""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Sequence
from typing import cast
from urllib.parse import urlparse

import websockets
from curl_cffi.requests import AsyncSession

from shared.utils.json_read import as_map, read_str, try_int
from trader.databet_types import (
    DOTA_GAME_ID,
    DatabetError,
    DatabetEventInfo,
    DatabetPlayer,
    DatabetPlayerKda,
    DatabetPlayerStats,
    DatabetSnapshot,
    DatabetTeam,
    DatabetTeamScore,
    DatabetTeamStatistics,
    DatabetTick,
    ThunderpickJwt,
    ThunderpickMatch,
    ThunderpickTeam,
)

THUNDERPICK_ORIGIN = "https://thunderpick.io"
THUNDERPICK_API = f"{THUNDERPICK_ORIGIN}/api"
DATABET_GRAPHQL_HTTP = "https://widgets-gql.databet.cloud/graphql"
DATABET_GRAPHQL_WS = "wss://widgets-gql.databet.cloud/graphql"
RECONNECT_SECONDS = 3.0
MAX_CONSECUTIVE_FAILURES = 5

GET_EVENT_INFO_QUERY = """
query getEventInfo {
  GetEventInfo {
    id
    sport
    widgets {
      type
      features {
        ... on WidgetScoreboardFeatures {
          scopes {
            weapons playerNetWorth items playerLevel playerHP teamBarracksKilled
          }
        }
      }
    }
  }
}
"""

DOTA2_SNAPSHOT_SUBSCRIPTION = """
subscription dota2SnapshotUpdated($id: String!, $locale: String!) {
  dota2SnapshotUpdated(id: $id, locale: $locale) {
    ... on Dota2Snapshot {
      __typename status startTime bestOf mapNumber mapTimeMs
      scores { total { teamId score } maps { mapNumber teamScores { teamId score } } }
      teams {
        id name side
        players {
          id name heroId
          kda { kills deaths assists }
          stats { level networth healthMax health status disconnected }
          items { id quantity }
        }
        statistics { playersKilled towersKilled barracksKilled netWorth }
      }
    }
    ... on Dota2PlayerKDAUpdated { __typename teamId playerId kda { kills deaths assists } }
    ... on Dota2PlayerStatsUpdated {
      __typename teamId playerId
      stats { level networth healthMax health status disconnected }
    }
    ... on Dota2PlayerItemsUpdated { __typename teamId playerId items { id quantity } }
    ... on Dota2PlayerHeroUpdated { __typename teamId playerId heroId }
    ... on Dota2ScoresUpdated {
      __typename scores { total { teamId score } maps { mapNumber teamScores { teamId score } } }
    }
    ... on Dota2MapTimeUpdated { __typename mapTimeMs }
    ... on Error { __typename message incidentId }
  }
}
"""


def new_session() -> AsyncSession:
    """Fresh Chrome-impersonating session. Thunderpick's Cloudflare sporadically
    403s requests that reuse a session, so every REST call opens its own."""
    return AsyncSession(
        impersonate="chrome",
        timeout=30.0,
        headers={
            "Accept": "application/json",
            "Origin": THUNDERPICK_ORIGIN,
            "Referer": f"{THUNDERPICK_ORIGIN}/",
        },
    )


def _envelope(body: object, label: str) -> dict[str, object]:
    """Unwrap Thunderpick's `{statusCode, ok, data}` envelope."""
    mapped = as_map(body)
    if mapped is None or mapped.get("ok") is not True:
        raise DatabetError(f"{label} failed")
    data = as_map(mapped.get("data"))
    if data is None:
        raise DatabetError(f"{label} has no data")
    return data


def _read_team(raw: object) -> ThunderpickTeam:
    fields = as_map(raw) or {}
    return ThunderpickTeam(id=try_int(fields.get("id")) or 0, name=read_str(fields, "name") or "?")


def read_match_card(raw: object) -> ThunderpickMatch | None:
    """Parse one match card from the detail or live-list payload."""
    fields = as_map(raw)
    if fields is None:
        return None
    match_id = try_int(fields.get("id"))
    if match_id is None:
        return None
    teams = as_map(fields.get("teams")) or {}
    score_box = as_map(fields.get("score")) or {}
    score = as_map(score_box.get("score")) or {}
    competition = as_map(fields.get("competition")) or {}
    jwt = fields.get("databetWidgetJwt")
    return ThunderpickMatch(
        id=match_id,
        game_id=try_int(fields.get("gameId")) or 0,
        name=read_str(fields, "name"),
        is_live=fields.get("isLive") is True,
        home=_read_team(teams.get("home")),
        away=_read_team(teams.get("away")),
        competition=read_str(competition, "name"),
        home_score=try_int(score.get("homeScore")) or 0,
        away_score=try_int(score.get("awayScore")) or 0,
        best_of=try_int(fields.get("bestOf")) or try_int(fields.get("bestOfMaps")) or 0,
        databet_widget_available=fields.get("databetWidgetAvailable") is True,
        databet_widget_jwt=jwt if isinstance(jwt, str) else "",
    )


async def _api_json(
    method: str,
    url: str,
    json_body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> object:
    """GET/POST decoded JSON on a fresh session; retried on Cloudflare 403s."""
    for _ in range(3):
        async with new_session() as session:
            if method == "GET":
                response = await session.get(url, headers=headers)
            else:
                response = await session.post(url, json=json_body, headers=headers)
        if response.status_code == 403:
            continue
        response.raise_for_status()
        return cast(object, json.loads(response.content))
    raise DatabetError(f"{method} {url} failed")


async def fetch_match(match_id: int) -> ThunderpickMatch:
    """Load one Thunderpick match card, including the DATA.BET widget JWT."""
    body = await _api_json("GET", f"{THUNDERPICK_API}/matches/{match_id}")
    card = read_match_card(_envelope(body, f"match {match_id}"))
    if card is None:
        raise DatabetError(f"match {match_id} card is empty")
    return card


async def list_live_matches() -> tuple[ThunderpickMatch, ...]:
    """Return live Dota 2 match cards from POST /api/matches."""
    body = await _api_json(
        "POST", f"{THUNDERPICK_API}/matches", json_body={"gameIds": [DOTA_GAME_ID]}
    )
    data = _envelope(body, "live matches")
    live = data.get("live")
    cards: list[ThunderpickMatch] = []
    if isinstance(live, list):
        for item in cast(list[object], live):
            card = read_match_card(item)
            if card is not None and card.is_live and card.game_id == DOTA_GAME_ID:
                cards.append(card)
    return tuple(cards)


def direct_match_id(selector: str) -> int | None:
    """Match id from a numeric selector or a Thunderpick URL's last path segment."""
    stripped = selector.strip()
    if stripped.startswith("http://") or stripped.startswith("https://"):
        tail = urlparse(stripped).path.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            return int(tail)
        raise DatabetError(f"cannot parse match id from URL {selector!r}")
    if stripped.isdigit():
        return int(stripped)
    return None


def match_selector_hits(match: ThunderpickMatch, needle: str) -> bool:
    """True when `needle` is in a team name or the full match name."""
    return (
        needle in match.home.name.lower()
        or needle in match.away.name.lower()
        or needle in match.name.lower()
    )


def select_live_match(matches: Sequence[ThunderpickMatch], selector: str) -> ThunderpickMatch:
    """Pick the live match whose teams or name contain `selector`."""
    needle = selector.lower()
    hits = [match for match in matches if match_selector_hits(match, needle)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        names = ", ".join(f"{match.id} {match.name}" for match in hits)
        raise DatabetError(f"ambiguous selector {selector!r}: {names}")
    raise DatabetError(f"no live Dota 2 match matches {selector!r}")


async def resolve_match(selector: str) -> ThunderpickMatch:
    """Resolve a match id, Thunderpick URL, or team/name selector."""
    match_id = direct_match_id(selector)
    if match_id is not None:
        match = await fetch_match(match_id)
        if match.game_id != DOTA_GAME_ID:
            raise DatabetError(f"{match.id} is not a Dota 2 match")
        return match
    live = await list_live_matches()
    if not live:
        raise DatabetError("no live Dota 2 matches")
    return select_live_match(live, selector)


def decode_jwt_claims(token: str) -> dict[str, object]:
    """Decode JWT payload without verification."""
    parts = token.split(".")
    if len(parts) != 3:
        raise DatabetError("invalid JWT format")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded)
    except (ValueError, TypeError) as exc:
        raise DatabetError(f"JWT decode failed: {exc}") from exc
    if not isinstance(claims, dict):
        raise DatabetError("JWT payload is not an object")
    return cast(dict[str, object], claims)


def databet_token(match: ThunderpickMatch) -> ThunderpickJwt:
    """DATA.BET widget token from a match card's `databetWidgetJwt`."""
    if not match.databet_widget_available or not match.databet_widget_jwt:
        raise DatabetError(f"match {match.id} has no DATA.BET widget")
    claims = decode_jwt_claims(match.databet_widget_jwt)
    event_id = claims.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise DatabetError("JWT missing event_id claim")
    exp = try_int(claims.get("exp")) or 0
    return ThunderpickJwt(token=match.databet_widget_jwt, event_id=event_id, expires_at=exp)


async def fetch_event_info(token: str) -> DatabetEventInfo:
    """GetEventInfo: sport, widget list, and scoreboard feature scopes."""
    body = as_map(
        await _api_json(
            "POST",
            DATABET_GRAPHQL_HTTP,
            json_body={"query": GET_EVENT_INFO_QUERY},
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    if body is None:
        raise DatabetError("GetEventInfo returned non-object")
    errors = body.get("errors")
    if errors:
        raise DatabetError(f"GetEventInfo errors: {errors}")
    data = as_map(body.get("data"))
    info = as_map(data.get("GetEventInfo")) if data is not None else None
    if info is None:
        raise DatabetError("GetEventInfo has no data")
    widgets: list[str] = []
    scopes: dict[str, bool] = {}
    raw_widgets = info.get("widgets")
    if isinstance(raw_widgets, list):
        for item in cast(list[object], raw_widgets):
            widget = as_map(item)
            if widget is None:
                continue
            widget_type = read_str(widget, "type")
            if widget_type:
                widgets.append(widget_type)
            if widget_type != "SCOREBOARD":
                continue
            features = as_map(widget.get("features")) or {}
            raw_scopes = as_map(features.get("scopes")) or {}
            scopes = {key: value is True for key, value in raw_scopes.items()}
    return DatabetEventInfo(sport=read_str(info, "sport"), widgets=tuple(widgets), scopes=scopes)


def _read_player_kda(raw: object) -> DatabetPlayerKda:
    fields = as_map(raw) or {}
    return DatabetPlayerKda(
        kills=try_int(fields.get("kills")) or 0,
        deaths=try_int(fields.get("deaths")) or 0,
        assists=try_int(fields.get("assists")) or 0,
    )


def _read_player_stats(raw: object) -> DatabetPlayerStats:
    fields = as_map(raw) or {}
    return DatabetPlayerStats(
        level=try_int(fields.get("level")) or 0,
        networth=try_int(fields.get("networth")) or 0,
        health_max=try_int(fields.get("healthMax")) or 0,
        health=try_int(fields.get("health")) or 0,
        status=read_str(fields, "status"),
        disconnected=fields.get("disconnected") is True,
    )


def _read_player(raw: object) -> DatabetPlayer | None:
    fields = as_map(raw)
    if fields is None:
        return None
    return DatabetPlayer(
        id=read_str(fields, "id"),
        name=read_str(fields, "name") or "?",
        hero_id=read_str(fields, "heroId"),
        kda=_read_player_kda(fields.get("kda")),
        stats=_read_player_stats(fields.get("stats")),
    )


def _read_team_statistics(raw: object) -> DatabetTeamStatistics:
    fields = as_map(raw) or {}
    return DatabetTeamStatistics(
        players_killed=try_int(fields.get("playersKilled")) or 0,
        towers_killed=try_int(fields.get("towersKilled")) or 0,
        barracks_killed=try_int(fields.get("barracksKilled")) or 0,
        net_worth=try_int(fields.get("netWorth")) or 0,
    )


def _read_feed_team(raw: object) -> DatabetTeam | None:
    fields = as_map(raw)
    if fields is None:
        return None
    players: list[DatabetPlayer] = []
    raw_players = fields.get("players")
    if isinstance(raw_players, list):
        for item in cast(list[object], raw_players):
            player = _read_player(item)
            if player is not None:
                players.append(player)
    return DatabetTeam(
        id=read_str(fields, "id"),
        name=read_str(fields, "name") or "?",
        side=read_str(fields, "side").lower(),
        players=tuple(players),
        statistics=_read_team_statistics(fields.get("statistics")),
    )


def parse_snapshot(payload: dict[str, object]) -> DatabetSnapshot:
    """Parse a full Dota2Snapshot payload."""
    teams: list[DatabetTeam] = []
    raw_teams = payload.get("teams")
    if isinstance(raw_teams, list):
        for item in cast(list[object], raw_teams):
            team = _read_feed_team(item)
            if team is not None:
                teams.append(team)
    scores: list[DatabetTeamScore] = []
    raw_scores = as_map(payload.get("scores")) or {}
    total = raw_scores.get("total")
    if isinstance(total, list):
        for item in cast(list[object], total):
            entry = as_map(item)
            if entry is None:
                continue
            team_id = read_str(entry, "teamId")
            score = try_int(entry.get("score"))
            if team_id and score is not None:
                scores.append(DatabetTeamScore(team_id=team_id, score=score))
    return DatabetSnapshot(
        status=read_str(payload, "status"),
        start_time=read_str(payload, "startTime"),
        best_of=try_int(payload.get("bestOf")) or 0,
        map_number=try_int(payload.get("mapNumber")) or 0,
        map_time_ms=try_int(payload.get("mapTimeMs")) or 0,
        scores=tuple(scores),
        teams=tuple(teams),
    )


def _series_score(snapshot: DatabetSnapshot, team_name: str) -> int | None:
    """Maps won by the feed team whose name matches the card's home/away name."""
    score_by_team = {entry.team_id: entry.score for entry in snapshot.scores}
    for team in snapshot.teams:
        if team.name.lower() == team_name.lower():
            return score_by_team.get(team.id)
    return None


def project_tick(snapshot: DatabetSnapshot, match: ThunderpickMatch) -> DatabetTick:
    """Project a snapshot onto Radiant/Dire plus the card's home/away series score."""
    radiant: DatabetTeam | None = None
    dire: DatabetTeam | None = None
    for team in snapshot.teams:
        if team.side == "radiant":
            radiant = team
        elif team.side == "dire":
            dire = team
    return DatabetTick(
        match_status=snapshot.status,
        map_number=snapshot.map_number if snapshot.map_number > 0 else None,
        game_time=snapshot.map_time_ms // 1000 if snapshot.map_time_ms > 0 else None,
        home_score=_series_score(snapshot, match.home.name),
        away_score=_series_score(snapshot, match.away.name),
        radiant=radiant,
        dire=dire,
    )


def connection_init_message(token: str) -> str:
    """`connection_init` with the DATA.BET widget JWT."""
    return json.dumps({"type": "connection_init", "payload": {"Authorization": f"Bearer {token}"}})


def subscribe_message(event_id: str) -> str:
    """Subscription frame sent after `connection_ack`."""
    return json.dumps(
        {
            "id": "dota2",
            "type": "subscribe",
            "payload": {
                "query": DOTA2_SNAPSHOT_SUBSCRIPTION,
                "variables": {"id": event_id, "locale": "en"},
            },
        }
    )


def pong_message(payload: object) -> str:
    """Reply to a graphql-transport-ws ping, echoing its payload."""
    message: dict[str, object] = {"type": "pong"}
    if payload is not None:
        message["payload"] = payload
    return json.dumps(message)


def parse_ws_frame(raw: str) -> dict[str, object] | None:
    """Parse one websocket text frame, or None when it is not a JSON object."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return as_map(parsed)


def next_snapshot(payload: object) -> DatabetSnapshot | None:
    """Snapshot from a `next` payload; None for partial-update typenames.

    Raises DatabetError when the feed answers an inline Error object.
    """
    body = as_map(payload)
    data = as_map(body.get("data")) if body is not None else None
    update = as_map(data.get("dota2SnapshotUpdated")) if data is not None else None
    if update is None:
        return None
    typename = update.get("__typename")
    if typename == "Dota2Snapshot":
        return parse_snapshot(update)
    if typename == "Error":
        message = read_str(update, "message")
        incident = read_str(update, "incidentId")
        raise DatabetError(f"feed error: {message} (incident {incident})")
    return None


async def iter_databet_snapshots(
    token: str, event_id: str, stale_seconds: float
) -> AsyncIterator[DatabetSnapshot]:
    """Connect, init, subscribe, answer pings, and yield each full snapshot.

    The feed pushes a full Dota2Snapshot about once a second; partial
    player/score updates are skipped because the next snapshot carries them.
    Returns on `complete`; raises DatabetError on an error frame, TimeoutError
    when no frame arrives for stale_seconds.
    """
    async with websockets.connect(
        DATABET_GRAPHQL_WS,
        subprotocols=[cast(websockets.Subprotocol, "graphql-transport-ws")],
        max_size=None,
        ping_interval=None,
    ) as socket:
        await socket.send(connection_init_message(token))
        while True:
            raw = await asyncio.wait_for(socket.recv(), timeout=stale_seconds)
            frame = parse_ws_frame(str(raw))
            if frame is None:
                continue
            frame_type = frame.get("type")
            if frame_type == "connection_ack":
                await socket.send(subscribe_message(event_id))
                continue
            if frame_type == "ping":
                await socket.send(pong_message(frame.get("payload")))
                continue
            if frame_type == "next":
                snapshot = next_snapshot(frame.get("payload"))
                if snapshot is not None:
                    yield snapshot
                continue
            if frame_type == "error":
                raise DatabetError(f"websocket error frame: {frame}")
            if frame_type == "complete":
                return
