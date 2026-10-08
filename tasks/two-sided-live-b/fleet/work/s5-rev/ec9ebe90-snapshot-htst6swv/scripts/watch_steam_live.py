"""Print every tick of one live Dota 2 match from the Steam realtime API.

GetRealtimeStats is fresh on every request, so the log resolution is the poll
interval (1s). The floor on freshness is the league's own DotaTV delay, printed
as `stream_delay` when the match is selected.

Invocation:
  make run F=scripts/watch_steam_live.py                     # list live league games
  make run F=scripts/watch_steam_live.py ARGS="8944742846"   # follow one match id
  make run F=scripts/watch_steam_live.py ARGS="falcons"      # follow by team name
"""

import logging
import sys
import time
from dataclasses import dataclass
from typing import cast

import httpx

from shared.constants.api import OPENDOTA_API
from shared.types.opendota import OpenDotaHero
from shared.utils.environment import env_value
from shared.utils.http import get_json, http_client
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_time import format_clock
from trader.steam_client import (
    STEAM_LIVE_LEAGUE_GAMES_API,
    STEAM_REALTIME_STATS_API,
    STEAM_TOP_LIVE_GAME_API,
)
from trader.steam_types import (
    OpenDotaLiveGame,
    SteamLiveLeagueGamesResponse,
    SteamRealtimeStats,
    SteamRealtimeTeam,
    SteamTopLiveGameResponse,
)

logger = get_logger(__name__)

# The server rebuilds the snapshot once per second; a faster poll returns the same bytes.
POLL_SECONDS = 1.0
PAUSE_HEARTBEAT_SECONDS = 10
VELOCITY_WINDOW_SECONDS = 30
TICK_HISTORY = 600
MAX_CONSECUTIVE_FAILURES = 5
RADIANT_TEAM_NUMBER = 2
TOWER_BUILDING_TYPE = 0
TOWERS_PER_SIDE = 11
POST_GAME_STATE = 6
DISCONNECT_STATE = 7
GAME_STATE_NAMES = {
    1: "init",
    2: "wait_players",
    3: "draft",
    4: "strategy",
    5: "in_progress",
    6: "post_game",
    7: "disconnect",
    8: "team_showcase",
}


@dataclass(frozen=True)
class LiveGame:
    """One live league game: ids, team names and the league's DotaTV delay."""

    match_id: int
    league_id: int
    server_steam_id: str
    radiant_name: str
    dire_name: str
    stream_delay_seconds: int
    clock_seconds: float


@dataclass(frozen=True)
class PlayerNetWorth:
    """One player's hero name and net worth."""

    hero: str
    net_worth: int


@dataclass(frozen=True)
class Tick:
    """One GetRealtimeStats snapshot, reduced to the numbers we watch."""

    game_time: int
    server_timestamp: int
    game_state: int
    radiant_score: int
    dire_score: int
    radiant_net_worth: int
    dire_net_worth: int
    radiant_towers_lost: int
    dire_towers_lost: int
    gold_graph_points: int
    radiant_players: tuple[PlayerNetWorth, ...]
    dire_players: tuple[PlayerNetWorth, ...]

    @property
    def net_worth_lead(self) -> int:
        """Radiant net worth minus Dire net worth."""
        return self.radiant_net_worth - self.dire_net_worth


def steam_key() -> str:
    """Return the first key of STEAM_KEYS from env or repo .env, or exit with a hint."""
    keys = env_value("STEAM_KEYS")
    if not keys:
        raise SystemExit("STEAM_KEYS not set (env or .env)")
    return keys.split(",")[0].strip()


def fetch_hero_names(client: httpx.Client) -> dict[int, str]:
    """Map hero id to display name from the keyless OpenDota hero list."""
    heroes = cast(list[OpenDotaHero], get_json(client, f"{OPENDOTA_API}/heroes"))
    return {hero["id"]: hero["localized_name"] for hero in heroes}


def fetch_server_ids(client: httpx.Client, key: str) -> dict[int, str]:
    """Map match id to server_steam_id from GetTopLiveGame, then OpenDota /live."""
    server_ids: dict[int, str] = {}
    top_payload = cast(
        SteamTopLiveGameResponse,
        get_json(client, STEAM_TOP_LIVE_GAME_API, {"key": key, "partner": 0}),
    )
    for game in top_payload.get("game_list", []):
        server_ids[int(game["match_id"])] = game["server_steam_id"]
    opendota_payload = cast(list[OpenDotaLiveGame], get_json(client, f"{OPENDOTA_API}/live"))
    for row in opendota_payload:
        server_ids.setdefault(int(row["match_id"]), row["server_steam_id"])
    return server_ids


def fetch_live_games(client: httpx.Client, key: str) -> tuple[LiveGame, ...]:
    """List every ticketed league game Valve advertises, with its server id."""
    payload = cast(
        SteamLiveLeagueGamesResponse,
        get_json(client, STEAM_LIVE_LEAGUE_GAMES_API, {"key": key}),
    )
    server_ids = fetch_server_ids(client, key)
    games: list[LiveGame] = []
    for game in payload["result"]["games"]:
        match_id = int(game["match_id"])
        radiant = game.get("radiant_team")
        dire = game.get("dire_team")
        scoreboard = game.get("scoreboard")
        games.append(
            LiveGame(
                match_id=match_id,
                league_id=int(game["league_id"]),
                server_steam_id=server_ids.get(match_id, ""),
                radiant_name=radiant["team_name"] if radiant else "?",
                dire_name=dire["team_name"] if dire else "?",
                stream_delay_seconds=int(game["stream_delay_s"]),
                clock_seconds=scoreboard["duration"] if scoreboard else 0.0,
            )
        )
    return tuple(games)


def log_games(games: tuple[LiveGame, ...]) -> None:
    """Print the live-game table used to pick a match."""
    logger.info("live league games: %s", len(games))
    for game in games:
        logger.info(
            "  %s league=%s clock=%s delay=%3ss server=%s  %s vs %s",
            game.match_id,
            game.league_id,
            format_clock(game.clock_seconds),
            game.stream_delay_seconds,
            game.server_steam_id or "-",
            game.radiant_name,
            game.dire_name,
        )


def select_game(games: tuple[LiveGame, ...], selector: str) -> LiveGame:
    """Pick the game whose match id equals, or whose team name contains, the selector."""
    needle = selector.lower()
    for game in games:
        if str(game.match_id) == selector:
            return game
    for game in games:
        if needle in game.radiant_name.lower() or needle in game.dire_name.lower():
            return game
    log_games(games)
    raise SystemExit(f"no live league game matches {selector!r}")


def count_towers_lost(stats: SteamRealtimeStats, team_number: int) -> int:
    """Towers one side lost, counted as the survivors missing from the full set.

    A destroyed building is not flagged in place: Valve replaces it with an
    anonymous zeroed stub (`team` 0, `tier` 0, `destroyed` true), so the only way
    to name the loss is to count who is still standing.
    """
    buildings = stats.get("buildings", [])
    if not buildings:
        return 0
    standing = sum(
        1
        for building in buildings
        if building["team"] == team_number and building["type"] == TOWER_BUILDING_TYPE
    )
    return TOWERS_PER_SIDE - standing


def player_net_worths(
    team: SteamRealtimeTeam, hero_names: dict[int, str]
) -> tuple[PlayerNetWorth, ...]:
    """Net worth per player of one side, richest first, with hero ids resolved to names."""
    players = [
        PlayerNetWorth(
            hero=hero_names.get(player["heroid"], f"hero_{player['heroid']}"),
            net_worth=player["net_worth"],
        )
        for player in team["players"]
    ]
    return tuple(sorted(players, key=lambda player: -player.net_worth))


def build_tick(stats: SteamRealtimeStats, hero_names: dict[int, str]) -> Tick:
    """Reduce one GetRealtimeStats payload to the numbers we print."""
    teams = {team["team_number"]: team for team in stats["teams"]}
    radiant = teams[RADIANT_TEAM_NUMBER]
    dire = next(team for number, team in teams.items() if number != RADIANT_TEAM_NUMBER)
    graph = stats.get("graph_data")
    return Tick(
        game_time=stats["match"]["game_time"],
        server_timestamp=stats["match"]["timestamp"],
        game_state=stats["match"]["game_state"],
        radiant_score=radiant["score"],
        dire_score=dire["score"],
        radiant_net_worth=radiant["net_worth"],
        dire_net_worth=dire["net_worth"],
        radiant_towers_lost=count_towers_lost(stats, RADIANT_TEAM_NUMBER),
        dire_towers_lost=count_towers_lost(stats, dire["team_number"]),
        gold_graph_points=len(graph["graph_gold"]) if graph else 0,
        radiant_players=player_net_worths(radiant, hero_names),
        dire_players=player_net_worths(dire, hero_names),
    )


def net_worth_velocity(history: list[Tick], current: Tick) -> int | None:
    """Lead change over the last VELOCITY_WINDOW_SECONDS, or None before the window fills."""
    cutoff = current.game_time - VELOCITY_WINDOW_SECONDS
    older = [tick for tick in history if tick.game_time <= cutoff]
    if not older:
        return None
    return current.net_worth_lead - older[-1].net_worth_lead


def format_players(players: tuple[PlayerNetWorth, ...]) -> str:
    """Render one side's hero net worths as `Luna 1046  Puck 945 ...`."""
    return "  ".join(f"{player.hero} {player.net_worth}" for player in players)


def log_tick(game: LiveGame, tick: Tick, velocity: int | None, latency: float) -> None:
    """Print one tick line plus one hero net worth line per side."""
    logger.info(
        "t=%5s %-12s %2d:%-2d nw R=%6d D=%6d lead=%+7d d30=%s towers R=%d D=%d "
        "gold_pts=%3d req=%.2fs",
        format_clock(tick.game_time),
        GAME_STATE_NAMES.get(tick.game_state, str(tick.game_state)),
        tick.radiant_score,
        tick.dire_score,
        tick.radiant_net_worth,
        tick.dire_net_worth,
        tick.net_worth_lead,
        f"{velocity:+d}" if velocity is not None else "-",
        tick.radiant_towers_lost,
        tick.dire_towers_lost,
        tick.gold_graph_points,
        latency,
    )
    logger.info("     R %-18s %s", game.radiant_name, format_players(tick.radiant_players))
    logger.info("     D %-18s %s", game.dire_name, format_players(tick.dire_players))


def log_kill(tick: Tick, previous: Tick | None) -> None:
    """Mark the wall-clock second a kill lands, to time the feed against a stream."""
    if previous is None:
        return
    if (tick.radiant_score, tick.dire_score) == (previous.radiant_score, previous.dire_score):
        return
    logger.info(
        ">>> KILL %d:%d at t=%s",
        tick.radiant_score,
        tick.dire_score,
        format_clock(tick.game_time),
    )


def follow_game(client: httpx.Client, key: str, game: LiveGame, hero_names: dict[int, str]) -> int:
    """Poll GetRealtimeStats until the game ends, printing every new game_time."""
    logger.info(
        "following %s: %s vs %s, league=%s, stream_delay=%ss, poll=%.1fs",
        game.match_id,
        game.radiant_name,
        game.dire_name,
        game.league_id,
        game.stream_delay_seconds,
        POLL_SECONDS,
    )
    history: list[Tick] = []
    failures = 0
    while failures < MAX_CONSECUTIVE_FAILURES:
        started = time.monotonic()
        payload = cast(
            SteamRealtimeStats,
            get_json(
                client,
                STEAM_REALTIME_STATS_API,
                {"key": key, "server_steam_id": game.server_steam_id},
            ),
        )
        latency = time.monotonic() - started
        if "match" not in payload or not payload.get("teams"):
            failures += 1
            logger.warning("empty payload (%s/%s)", failures, MAX_CONSECUTIVE_FAILURES)
            time.sleep(POLL_SECONDS)
            continue
        failures = 0
        tick = build_tick(payload, hero_names)
        previous = history[-1] if history else None
        # A pause freezes game_time, so the heartbeat keeps the log alive.
        paused_heartbeat = previous is not None and (
            tick.server_timestamp - previous.server_timestamp >= PAUSE_HEARTBEAT_SECONDS
        )
        if previous is None or tick.game_time != previous.game_time or paused_heartbeat:
            log_tick(game, tick, net_worth_velocity(history, tick), latency)
            log_kill(tick, previous)
            history.append(tick)
            history[:] = history[-TICK_HISTORY:]
        if tick.game_state == POST_GAME_STATE or tick.game_state == DISCONNECT_STATE:
            logger.info("game over, state=%s", tick.game_state)
            return 0
        # Subtract the request time, else a 0.3s request turns the cycle into 1.3s
        # and every third game second is missed.
        time.sleep(max(0.0, POLL_SECONDS - latency))
    logger.error("stopping: %s empty payloads in a row", MAX_CONSECUTIVE_FAILURES)
    return 1


def main() -> int:
    """List live league games, or follow the one named by the first argument."""
    setup_logging()
    # httpx logs the full URL, and the URL carries STEAM_KEY. Keep it out of the log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    key = steam_key()
    with http_client() as client:
        games = fetch_live_games(client, key)
        if len(sys.argv) < 2:
            log_games(games)
            logger.info("pass a match id or a team name to follow one game")
            return 0
        game = select_game(games, sys.argv[1])
        if not game.server_steam_id:
            raise SystemExit(
                f"{game.match_id} has no server_steam_id yet (not in GetTopLiveGame "
                "or OpenDota /live); retry in a minute"
            )
        return follow_game(client, key, game, fetch_hero_names(client))


if __name__ == "__main__":
    raise SystemExit(main())
