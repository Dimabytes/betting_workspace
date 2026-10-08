"""Print every tick of live Dota 2 games from Steam GetTopLiveGame.

GetTopLiveGame is not delayed by `stream_delay_s` / DotaTV (GetRealtimeStats
and the GetLiveLeagueGames scoreboard are). It updates every 15-45s with
game_time, kills, `radiant_lead` (team gold difference), buildings, and the
ten hero ids. There is no per-player net worth. `delay` on the row is the
advertised spectator delay, not the age of these fields.

OpenDota `/api/live` is the same snapshot plus an extra ~40s cache; this
script calls Steam directly.

Invocation:
  make run F=scripts/watch_top_live.py
  make run F=scripts/watch_top_live.py ARGS="9005981988"
  make run F=scripts/watch_top_live.py ARGS="nemesis"
"""

import logging
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import httpx

from shared.constants.api import OPENDOTA_API
from shared.types.opendota import OpenDotaHero
from shared.utils.environment import env_value
from shared.utils.http import get_json, http_client
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_time import format_clock
from trader.steam_client import STEAM_TOP_LIVE_GAME_API

logger = get_logger(__name__)

PARTNERS = (0, 1, 2, 3)


def as_map(value: object) -> dict[str, object] | None:
    """JSON object, or None."""
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def as_int(value: object) -> int:
    """JSON number or decimal string, else 0. Bool is not a number."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value:
        return int(value)
    return 0


def as_float(value: object) -> float:
    """JSON number, else 0. Bool is not a number."""
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        return float(value)
    return 0.0


POLL_SECONDS = 5.0
PAUSE_HEARTBEAT_SECONDS = 30
MAX_CONSECUTIVE_FAILURES = 5
RADIANT_TEAM = 0
DIRE_TEAM = 1


@dataclass(frozen=True)
class HeroSlot:
    """One GetTopLiveGame player: hero and slot, no gold."""

    hero: str
    team_slot: int


@dataclass(frozen=True)
class Tick:
    """One GetTopLiveGame row, reduced to the numbers we watch."""

    match_id: int
    league_id: int
    game_time: int
    radiant_score: int
    dire_score: int
    radiant_lead: int
    building_state: int
    delay: int
    spectators: int
    radiant_name: str
    dire_name: str
    radiant_heroes: tuple[HeroSlot, ...]
    dire_heroes: tuple[HeroSlot, ...]
    last_update_time: float
    server_steam_id: str


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


def read_int(raw: Mapping[str, object], key: str) -> int:
    """Read a JSON number or decimal string, or 0 when missing."""
    return as_int(raw.get(key))


def read_str(raw: Mapping[str, object], key: str) -> str:
    """Read a JSON string, or empty when missing."""
    value = raw.get(key)
    return str(value) if value else ""


def read_heroes(players: object, team: int, hero_names: dict[int, str]) -> tuple[HeroSlot, ...]:
    """Heroes on one side, ordered by team_slot."""
    if not isinstance(players, list):
        return ()
    slots: list[HeroSlot] = []
    for item in cast(list[object], players):
        row = as_map(item)
        if row is None:
            continue
        if as_int(row.get("team")) != team:
            continue
        hero_id = as_int(row.get("hero_id"))
        slots.append(
            HeroSlot(
                hero=hero_names.get(hero_id, f"hero_{hero_id}"),
                team_slot=as_int(row.get("team_slot")),
            )
        )
    return tuple(sorted(slots, key=lambda slot: slot.team_slot))


def read_tick(raw: Mapping[str, object], hero_names: dict[int, str]) -> Tick:
    """Project one GetTopLiveGame row onto the fields this watcher prints."""
    return Tick(
        match_id=read_int(raw, "match_id"),
        league_id=read_int(raw, "league_id"),
        game_time=read_int(raw, "game_time"),
        radiant_score=read_int(raw, "radiant_score"),
        dire_score=read_int(raw, "dire_score"),
        radiant_lead=read_int(raw, "radiant_lead"),
        building_state=read_int(raw, "building_state"),
        delay=read_int(raw, "delay"),
        spectators=read_int(raw, "spectators"),
        radiant_name=read_str(raw, "team_name_radiant") or "Radiant",
        dire_name=read_str(raw, "team_name_dire") or "Dire",
        radiant_heroes=read_heroes(raw.get("players"), RADIANT_TEAM, hero_names),
        dire_heroes=read_heroes(raw.get("players"), DIRE_TEAM, hero_names),
        last_update_time=as_float(raw.get("last_update_time")),
        server_steam_id=read_str(raw, "server_steam_id"),
    )


def fetch_top_games(client: httpx.Client, key: str, hero_names: dict[int, str]) -> tuple[Tick, ...]:
    """Union of every partner's game_list, first row for a match id wins."""
    by_match: dict[int, Tick] = {}
    for partner in PARTNERS:
        payload = cast(
            dict[str, object],
            get_json(client, STEAM_TOP_LIVE_GAME_API, {"key": key, "partner": partner}),
        )
        rows = payload.get("game_list")
        if not isinstance(rows, list):
            continue
        for item in cast(list[object], rows):
            row = as_map(item)
            if row is None:
                continue
            tick = read_tick(row, hero_names)
            if tick.match_id and tick.match_id not in by_match:
                by_match[tick.match_id] = tick
    return tuple(sorted(by_match.values(), key=lambda tick: -tick.spectators))


def format_heroes(heroes: tuple[HeroSlot, ...]) -> str:
    """`Hoodwink  Mirana  ...` in slot order."""
    if not heroes:
        return "-"
    return "  ".join(slot.hero for slot in heroes)


def log_games(games: tuple[Tick, ...]) -> None:
    """Print the live-game table used to pick a match."""
    logger.info("GetTopLiveGame: %s matches (no per-player net worth)", len(games))
    for game in games:
        logger.info(
            "  %s league=%s t=%s %2d:%-2d lead=%+7d tv_delay=%ss spec=%s  %s vs %s",
            game.match_id,
            game.league_id,
            format_clock(game.game_time),
            game.radiant_score,
            game.dire_score,
            game.radiant_lead,
            game.delay,
            game.spectators,
            game.radiant_name,
            game.dire_name,
        )


def select_game(games: tuple[Tick, ...], selector: str) -> Tick | None:
    """Pick the game whose match id equals, or whose team name contains, the selector."""
    needle = selector.lower()
    for game in games:
        if str(game.match_id) == selector:
            return game
    for game in games:
        if needle in game.radiant_name.lower() or needle in game.dire_name.lower():
            return game
    return None


def log_tick(tick: Tick, wall_dt: float | None, clock_dt: int | None) -> None:
    """Print one tick line plus heroes per side. Team gold is a lead, not two totals."""
    logger.info(
        "t=%5s %2d:%-2d lead=%+7d buildings=%s tv_delay=%ss spec=%s dt=%s dclock=%s",
        format_clock(tick.game_time),
        tick.radiant_score,
        tick.dire_score,
        tick.radiant_lead,
        tick.building_state,
        tick.delay,
        tick.spectators,
        f"{wall_dt:.1f}s" if wall_dt is not None else "-",
        f"{clock_dt:+d}" if clock_dt is not None else "-",
    )
    logger.info("     R %-18s %s", tick.radiant_name, format_heroes(tick.radiant_heroes))
    logger.info("     D %-18s %s", tick.dire_name, format_heroes(tick.dire_heroes))


def log_kill(tick: Tick, previous: Tick | None) -> None:
    """Score changed since the last snapshot; that is a window, not a kill timestamp."""
    if previous is None:
        return
    if (tick.radiant_score, tick.dire_score) == (previous.radiant_score, previous.dire_score):
        return
    logger.info(
        ">>> score %d:%d -> %d:%d in t=%s..%s (%+ds window, not kill time)",
        previous.radiant_score,
        previous.dire_score,
        tick.radiant_score,
        tick.dire_score,
        format_clock(previous.game_time),
        format_clock(tick.game_time),
        tick.game_time - previous.game_time,
    )


def board_key(tick: Tick) -> tuple[int, int, int, int, int]:
    """Fields that count as a new snapshot. Spectators flicker every poll."""
    return (
        tick.game_time,
        tick.radiant_score,
        tick.dire_score,
        tick.radiant_lead,
        tick.building_state,
    )


def follow_game(client: httpx.Client, key: str, selector: str, hero_names: dict[int, str]) -> int:
    """Poll GetTopLiveGame until the match drops out or too many fetches fail."""
    logger.info(
        "following %s poll=%.1fs (team gold = radiant_lead only, no per-player NW)",
        selector,
        POLL_SECONDS,
    )
    previous: Tick | None = None
    printed_at = time.monotonic()
    failures = 0
    waiting_logged = False
    while failures < MAX_CONSECUTIVE_FAILURES:
        started = time.monotonic()
        try:
            games = fetch_top_games(client, key, hero_names)
        except httpx.HTTPError as exc:
            failures += 1
            logger.warning("%s (%s/%s)", exc, failures, MAX_CONSECUTIVE_FAILURES)
            time.sleep(POLL_SECONDS)
            continue
        failures = 0
        tick = select_game(games, selector)
        if tick is None:
            if previous is not None:
                logger.info("match left GetTopLiveGame")
                return 0
            if not waiting_logged:
                logger.info("waiting: %s not in GetTopLiveGame yet", selector)
                waiting_logged = True
            time.sleep(POLL_SECONDS)
            continue
        waiting_logged = False
        now = time.monotonic()
        silence = now - printed_at
        changed = previous is None or board_key(tick) != board_key(previous)
        if changed or silence >= PAUSE_HEARTBEAT_SECONDS:
            if previous is None:
                logger.info(
                    "match=%s league=%s server=%s tv_delay=%ss is advertised DotaTV, "
                    "not applied to this feed",
                    tick.match_id,
                    tick.league_id,
                    tick.server_steam_id or "-",
                    tick.delay,
                )
            clock_dt = None if previous is None else tick.game_time - previous.game_time
            wall_dt = None if previous is None else now - printed_at
            log_tick(tick, wall_dt, clock_dt)
            log_kill(tick, previous)
            previous = tick
            printed_at = now
        time.sleep(max(0.0, POLL_SECONDS - (time.monotonic() - started)))
    logger.error("stopping: %s failed fetches in a row", MAX_CONSECUTIVE_FAILURES)
    return 1


def main() -> int:
    """List GetTopLiveGame matches, or follow the one named by the first argument."""
    setup_logging()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    key = steam_key()
    with http_client() as client:
        hero_names = fetch_hero_names(client)
        if len(sys.argv) < 2:
            log_games(fetch_top_games(client, key, hero_names))
            logger.info("pass a match id or a team name to follow one game")
            return 0
        return follow_game(client, key, sys.argv[1], hero_names)


if __name__ == "__main__":
    raise SystemExit(main())
