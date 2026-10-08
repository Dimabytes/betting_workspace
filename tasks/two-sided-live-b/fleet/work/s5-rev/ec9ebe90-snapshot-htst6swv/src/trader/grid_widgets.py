"""Parse GRID widget socket frames: scoreboard clock, sides, and series_table net worth."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from shared.constants.strategy import PLAYERS_PER_SIDE
from trader.grid_widget_types import ScoreboardPayload, SeriesTablePayload, TableRow, WidgetFrame

# GRID widget live socket. This is the feed the Polymarket match page renders,
# and it needs no key: only the `Origin` header below and the gridSeriesId from
# the Gamma event (`eventMetadata.gridSeriesId`). Frames are
# {"service", "delay", "scope", "data": [{"data": "<json string>"}]}.
# `series_scoreboard_v2` is zero delay (game clock, kills, sides);
# `series_table` is 8s behind and carries per-player net worth.
# Our GRID_GG_TOKEN is not entitled to live-data-feed, so this is the live path.
GRID_WIDGETS_WS = "wss://api.grid.gg/widgets-v2/live"
GRID_WIDGETS_ORIGIN = "https://polymarket.com"
SCOREBOARD_SERVICE = "series_scoreboard_v2"
TABLE_SERVICE = "series_table"
WIDGET_EVENTS = f"{SCOREBOARD_SERVICE},{TABLE_SERVICE}"
SERVICE_PREFIX = "integrity_safe_"
FINISHED_STATUS = "finished"
LIVE_STATUS = "live"
UPCOMING_STATUS = "upcoming"
GAME_STATE_GROUP = "Game"
PLAYER_ENTITY_GROUP = "Player"
RADIANT_SIDE = "RADIANT"
DIRE_SIDE = "DIRE"
RECONNECT_SECONDS = 3.0
MAX_CONSECUTIVE_FAILURES = 5


@dataclass(frozen=True)
class TeamSide:
    """One side of a map: GRID team id, name, side, kills, and map-won flag."""

    team_id: str
    name: str
    side: str
    kills: int
    maps_won: int
    won: bool


@dataclass(frozen=True)
class Scoreboard:
    """The clock and score of one map, plus which map GRID currently calls active."""

    series_status: str
    series_format: str
    tournament: str
    game_label: str
    game_number: int
    active_game_number: int
    game_status: str
    clock_seconds: int
    clock_ticking: bool
    occurred_at: str
    publish_delay: int
    teams: tuple[TeamSide, ...]


@dataclass(frozen=True)
class PlayerNetWorth:
    """One player's hero, portrait flag, level, net worth and K/D/A on one map."""

    team_id: str
    nick: str
    hero: str  # "" without a portrait and on LoL uuid portraits — has_portrait tells apart
    has_portrait: bool
    level: int
    net_worth: int
    kills: int
    deaths: int
    assists: int


@dataclass(frozen=True)
class NetWorthSnapshot:
    """The last `series_table` payload, with the map it describes and its lag."""

    game_number: int
    feed_delay: int
    players: tuple[PlayerNetWorth, ...]


@dataclass(frozen=True)
class Frame:
    """One socket frame, with the `integrity_safe_` prefix stripped off the service."""

    service: str
    delay: int
    payload: str


def build_socket_url(series_id: str) -> str:
    """Build the widget socket URL: zero delay, plain JSON, the two services we read."""
    return (
        f"{GRID_WIDGETS_WS}/{series_id}"
        f"?delay=zero&events={WIDGET_EVENTS}&isCompressionEnabled=false"
    )


def parse_frame(raw: str) -> Frame:
    """Decode one socket frame; `payload` is empty when the service sent no state."""
    frame = cast(WidgetFrame, json.loads(raw))
    service = frame["service"].removeprefix(SERVICE_PREFIX)
    items = frame["data"]
    payload = items[0]["data"] if items else ""
    return Frame(service=service, delay=frame["delay"], payload=payload)


def _scoreboard_at_index(board: ScoreboardPayload, game_index: int) -> Scoreboard | None:
    """Reduce one scoreboard payload to the map at `game_index`, or None if missing."""
    games = board["games"]
    if not games or game_index < 0 or game_index >= len(games):
        return None
    game = games[game_index]
    series = board["series"]
    maps_won = {team["id"]: team["score"] for team in series["teams"]}
    names = {team["id"]: team["name"] for team in series["teams"]}
    teams = tuple(
        TeamSide(
            team_id=team["id"],
            name=names.get(team["id"], team["id"]),
            side=team["infoText"]["text"],
            kills=team["score"],
            maps_won=maps_won.get(team["id"], 0),
            won=team["won"],
        )
        for team in game["teams"]
    )
    clock = game["gameClock"]
    return Scoreboard(
        series_status=series["status"],
        series_format=series["format"],
        tournament=board["tournament"]["name"],
        game_label=game["centeredInfoText"],
        game_number=game_index + 1,
        active_game_number=board["activeGameIndex"] + 1,
        game_status=game["status"],
        clock_seconds=clock["currentSeconds"],
        clock_ticking=clock["isTicking"],
        occurred_at=clock["occurredAt"],
        publish_delay=clock["publishDelay"],
        teams=teams,
    )


def read_scoreboard(payload: str) -> Scoreboard | None:
    """Reduce a `series_scoreboard_v2` payload to the active map's clock and score."""
    board = cast(ScoreboardPayload, json.loads(payload))
    return _scoreboard_at_index(board, board["activeGameIndex"])


def read_current_scoreboard(payload: str) -> Scoreboard | None:
    """Reduce a scoreboard payload to the series' current map, or None.

    A live or upcoming active game is that game. A finished active game while
    the series is still live is `sum(maps_won) + 1`. A missing slot is None.
    """
    board = cast(ScoreboardPayload, json.loads(payload))
    if board["series"]["status"] != LIVE_STATUS:
        return None
    games = board["games"]
    active_index = board["activeGameIndex"]
    if not games or active_index < 0 or active_index >= len(games):
        return None
    game_status = games[active_index]["status"]
    if game_status in (LIVE_STATUS, UPCOMING_STATUS):
        return _scoreboard_at_index(board, active_index)
    if game_status != FINISHED_STATUS:
        return None
    maps_won = sum(team["score"] for team in board["series"]["teams"])
    return _scoreboard_at_index(board, maps_won)


def read_map_scoreboard(payload: str, map_number: int) -> Scoreboard | None:
    """Reduce a `series_scoreboard_v2` payload to one 1-based map, even if it is not active."""
    board = cast(ScoreboardPayload, json.loads(payload))
    return _scoreboard_at_index(board, map_number - 1)


def side_team(board: Scoreboard, side: str) -> TeamSide | None:
    """The single team standing on `side`, or None when that side is absent or duplicated."""
    teams = [team for team in board.teams if team.side == side]
    if len(teams) != 1:
        return None
    return teams[0]


def map_number_from_scoreboard(board: Scoreboard) -> int | None:
    """Return the live 1-based map of the series, or None when no map is live.

    A live active game is that game's number. A finished active game is
    `sum(maps_won) + 1`, because GRID keeps the index on the old map for a
    while after it ends. A series that is not `live` has no current map.
    """
    if board.series_status != LIVE_STATUS:
        return None
    if board.game_status == LIVE_STATUS:
        return board.active_game_number
    if board.game_status == FINISHED_STATUS:
        return sum(team.maps_won for team in board.teams) + 1
    return None


def hero_from_icon(icon_url: str) -> str:
    """Read the hero name out of a GRID character portrait URL.

    LoL portraits are a GRID character uuid, not a slug; those return "" so the
    watcher can print the player nick instead of hex.
    """
    name = icon_url.rsplit("/", 1)[-1].removesuffix(".png")
    compact = name.replace("-", "").replace("_", "").replace(" ", "")
    if len(compact) == 32 and all(char in "0123456789abcdefABCDEF" for char in compact):
        return ""
    return name.replace("-", " ").replace("_", " ")


def _player_from_row(row: TableRow) -> PlayerNetWorth | None:
    """Project one table row to a player, or None when the nick is empty."""
    entity = row["entity"]
    nick = entity["value"]
    if not nick:
        return None
    icon_url = entity.get("iconUrl")
    hero = hero_from_icon(icon_url) if icon_url else ""
    # GRID drops the whole increaseLevel column until the map's first level-up.
    level_ups_cell = row.get("increaseLevel")
    level_ups = (level_ups_cell["value"] or 0) if level_ups_cell is not None else 0
    return PlayerNetWorth(
        team_id=entity["teamColor"].rsplit(".", 1)[-1],
        nick=nick,
        hero=hero,
        has_portrait=bool(icon_url),
        # Every hero spawns at level 1, so the level-up count is one short.
        level=level_ups + 1,
        net_worth=row["NetWorth"]["value"] or 0,
        kills=row["Kills"]["value"] or 0,
        deaths=row["Deaths"]["value"] or 0,
        assists=row["KillAssistsGiven"]["value"] or 0,
    )


def read_net_worth(payload: str, feed_delay: int) -> NetWorthSnapshot | None:
    """Reduce a `series_table` payload to the per-player net worth of the last map.

    Empty-nick rows are not players and drop. A table that keeps no player
    returns None, so neither the feed nor the delay probe reads it as a frame.
    """
    table = cast(SeriesTablePayload, json.loads(payload))
    groups = [group for group in table["stateGroups"] if group["name"] == GAME_STATE_GROUP]
    if not groups or not groups[0]["states"]:
        return None
    state = groups[0]["states"][-1]
    player_groups = [
        group for group in state["entityGroups"] if group["name"] == PLAYER_ENTITY_GROUP
    ]
    if not player_groups or not player_groups[0]["tables"]:
        return None
    players = tuple(
        player
        for player in (_player_from_row(row) for row in player_groups[0]["tables"][0]["tableRows"])
        if player is not None
    )
    if not players:
        return None
    return NetWorthSnapshot(
        game_number=state["sequenceNumber"], feed_delay=feed_delay, players=players
    )


def same_table_content(left: NetWorthSnapshot, right: NetWorthSnapshot) -> bool:
    """True when two tables describe the same map and players; `feed_delay` is ignored."""
    return left.game_number == right.game_number and left.players == right.players


def clock_age_seconds(occurred_at: str, now: datetime) -> float:
    """Seconds between the GRID clock stamp and now, i.e. the feed lag we observe."""
    stamped = datetime.fromisoformat(occurred_at)
    return (now - stamped).total_seconds()


def clock_stamp_unix_seconds(occurred_at: str) -> int:
    """Unix second of the GRID clock stamp `occurredAt`."""
    return int(datetime.fromisoformat(occurred_at).timestamp())


def live_clock_seconds(board: Scoreboard, age: float) -> int:
    """Advance the last published clock by its own age, the way the page does.

    The scoreboard service pushes only on change, so the raw `currentSeconds`
    can sit ten seconds behind while net worth keeps ticking. `clock_lag` in the
    log discloses how much of the printed clock is this extrapolation.
    """
    if not board.clock_ticking:
        return board.clock_seconds
    return board.clock_seconds + max(0, round(age))


def select_side_players(
    snapshot: NetWorthSnapshot, team_id: str
) -> tuple[PlayerNetWorth, ...] | None:
    """The five players of one side, or None when the table does not hold exactly five.

    GRID lists a roster substitute as an extra row with no hero portrait.
    Portraits decide only when a side has more than five rows.
    """
    players = tuple(player for player in snapshot.players if player.team_id == team_id)
    if len(players) > PLAYERS_PER_SIDE:
        players = tuple(player for player in players if player.has_portrait)
    if len(players) != PLAYERS_PER_SIDE:
        return None
    return players
