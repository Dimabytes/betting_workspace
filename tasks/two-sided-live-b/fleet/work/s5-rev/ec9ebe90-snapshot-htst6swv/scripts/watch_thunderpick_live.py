"""Print every DATA.BET scoreboard snapshot for one live Dota 2 Thunderpick match.

Thunderpick embeds the DATA.BET scoreboard widget (`<databet-widget>`). The
match REST API hands out a per-match JWT; the feed is a graphql-transport-ws
subscription on widgets-gql.databet.cloud that pushes a full `Dota2Snapshot`
about once a second. Partial player/score frames are ignored: the next
snapshot already carries them.

Thunderpick sits behind Cloudflare, so REST calls go through a
Chrome-impersonating `curl_cffi` session; the DATA.BET socket is plain
`websockets`.

Invocation:
  make run F=scripts/watch_thunderpick_live.py
  make run F=scripts/watch_thunderpick_live.py ARGS="Synapse"
  make run F=scripts/watch_thunderpick_live.py ARGS="2713235"
  make run F=scripts/watch_thunderpick_live.py ARGS="<thunderpick url>"
"""

import asyncio
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import websockets
from curl_cffi.requests.exceptions import RequestException

from shared.constants.api import OPENDOTA_API
from shared.types.opendota import OpenDotaHero
from shared.utils.json_read import try_int
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_time import format_clock
from trader.databet_client import (
    MAX_CONSECUTIVE_FAILURES,
    RECONNECT_SECONDS,
    databet_token,
    fetch_event_info,
    fetch_match,
    iter_databet_snapshots,
    list_live_matches,
    new_session,
    project_tick,
    resolve_match,
)
from trader.databet_types import (
    TERMINAL_STATUSES,
    DatabetError,
    DatabetPlayer,
    DatabetSnapshot,
    DatabetTeam,
    DatabetTick,
    ThunderpickMatch,
)

logger = get_logger(__name__)

DATABET_FEED_STALE_SECONDS = 30.0


async def fetch_hero_names() -> dict[int, str]:
    """Map hero id to display name from the keyless OpenDota hero list."""
    async with new_session() as session:
        response = await session.get(f"{OPENDOTA_API}/heroes")
    response.raise_for_status()
    heroes = cast(list[OpenDotaHero], json.loads(response.content))
    return {hero["id"]: hero["localized_name"] for hero in heroes}


def log_matches(matches: Sequence[ThunderpickMatch]) -> None:
    """Print the live-match table used to pick a series."""
    logger.info("live dota 2 matches: %s", len(matches))
    for match in matches:
        logger.info(
            "  %s  bo%s %s vs %s  series=%s:%s  databet=%s  %s",
            match.id,
            match.best_of or "?",
            match.home.name,
            match.away.name,
            match.home_score,
            match.away_score,
            "yes" if match.databet_widget_available else "no",
            match.competition,
        )


def format_player(player: DatabetPlayer, hero_names: dict[int, str]) -> str:
    """`Mikey Puck 13527 6/4/7` plus dead/dc flags when they matter."""
    hero_id = try_int(player.hero_id) or 0
    hero = hero_names.get(hero_id, player.hero_id or "?")
    extras: list[str] = []
    if player.stats.status == "DEAD":
        extras.append("dead")
    if player.stats.disconnected:
        extras.append("dc")
    suffix = f" {' '.join(extras)}" if extras else ""
    return (
        f"{player.name} {hero} {player.stats.networth} "
        f"{player.kda.kills}/{player.kda.deaths}/{player.kda.assists}{suffix}"
    )


def format_side_players(team: DatabetTeam, hero_names: dict[int, str]) -> str:
    """Players in scoreboard order."""
    return "  ".join(format_player(player, hero_names) for player in team.players)


def format_tick_lines(
    seq: int, dt: float | None, tick: DatabetTick, hero_names: dict[int, str]
) -> tuple[str, ...]:
    """The four-line terminal block for one received payload."""
    clock = "-" if tick.game_time is None else format_clock(tick.game_time)
    map_number = "-" if tick.map_number is None else str(tick.map_number)
    series_home = "-" if tick.home_score is None else str(tick.home_score)
    series_away = "-" if tick.away_score is None else str(tick.away_score)
    if tick.radiant is None or tick.dire is None:
        kills = "-"
        net_worth = "-"
        lead = "-"
        sides = ("     no scoreboard yet",)
        objectives = "     objectives -"
    else:
        kills = f"R{tick.radiant.statistics.players_killed}:D{tick.dire.statistics.players_killed}"
        net_worth = f"R{tick.radiant.statistics.net_worth}:D{tick.dire.statistics.net_worth}"
        lead_value = tick.net_worth_lead
        lead = "-" if lead_value is None else f"{lead_value:+d}"
        sides = (
            f"     R {tick.radiant.name:<16} {format_side_players(tick.radiant, hero_names)}",
            f"     D {tick.dire.name:<16} {format_side_players(tick.dire, hero_names)}",
        )
        objectives = (
            f"     objectives R towers={tick.radiant.statistics.towers_killed} "
            f"rax={tick.radiant.statistics.barracks_killed} | "
            f"D towers={tick.dire.statistics.towers_killed} "
            f"rax={tick.dire.statistics.barracks_killed}"
        )
    header = f"#{seq} dt={'-' if dt is None else f'{dt:.3f}s'} source=databet"
    summary = (
        f"t={clock} {tick.match_status} map={map_number} "
        f"series={series_home}:{series_away} kills={kills} "
        f"nw={net_worth} lead={lead}"
    )
    return (header, summary, *sides, objectives)


def log_tick(seq: int, dt: float | None, tick: DatabetTick, hero_names: dict[int, str]) -> None:
    """Print one received payload immediately."""
    for line in format_tick_lines(seq, dt, tick, hero_names):
        logger.info("%s", line)


@dataclass(frozen=True)
class WatchState:
    """Sequence and timing carried across snapshot payloads."""

    seq: int
    last_at: float | None


def next_watch_state(state: WatchState, now: float) -> tuple[WatchState, int, float | None]:
    """Advance the payload counter and return (state, seq, dt)."""
    dt = None if state.last_at is None else now - state.last_at
    seq = state.seq
    return WatchState(seq + 1, now), seq, dt


def emit_snapshot(
    snapshot: DatabetSnapshot,
    match: ThunderpickMatch,
    hero_names: dict[int, str],
    state: WatchState,
    now: float,
) -> tuple[WatchState, DatabetTick]:
    """Project, print, and advance the watch state."""
    tick = project_tick(snapshot, match)
    state, seq, dt = next_watch_state(state, now)
    log_tick(seq, dt, tick, hero_names)
    return state, tick


async def load_hero_names() -> dict[int, str]:
    """Hero names for display; empty map when OpenDota is unreachable."""
    try:
        return await fetch_hero_names()
    except (RequestException, OSError, ValueError) as exc:
        logger.warning("hero names unavailable: %s", exc)
        return {}


async def follow_match(match_id: int, hero_names: dict[int, str]) -> int:
    """Print every websocket snapshot, reconnecting and refreshing JWT as needed."""
    match = await fetch_match(match_id)
    jwt = databet_token(match)
    logger.info(
        "following %s %s vs %s event=%s stale_after=%.0fs",
        match.id,
        match.home.name,
        match.away.name,
        jwt.event_id,
        DATABET_FEED_STALE_SECONDS,
    )
    try:
        info = await fetch_event_info(jwt.token)
        logger.info(
            "event info sport=%s widgets=%s scopes=%s", info.sport, info.widgets, info.scopes
        )
    except (DatabetError, RequestException) as exc:
        logger.warning("GetEventInfo failed: %s", exc)
    state = WatchState(1, None)
    failures = 0
    while failures < MAX_CONSECUTIVE_FAILURES:
        try:
            if jwt.expires_at and time.time() >= jwt.expires_at - 60:
                logger.info("JWT near expiry, refreshing")
                match = await fetch_match(match_id)
                jwt = databet_token(match)
            async for snapshot in iter_databet_snapshots(
                jwt.token, jwt.event_id, DATABET_FEED_STALE_SECONDS
            ):
                state, tick = emit_snapshot(snapshot, match, hero_names, state, time.monotonic())
                if tick.match_status in TERMINAL_STATUSES:
                    return 0
            match = await fetch_match(match_id)
            if not match.is_live:
                return 0
            jwt = databet_token(match)
            logger.info("stream complete, match still live, reconnecting")
            await asyncio.sleep(RECONNECT_SECONDS)
            continue
        except (
            DatabetError,
            TimeoutError,
            RequestException,
            websockets.WebSocketException,
            OSError,
        ) as exc:
            failures += 1
            logger.warning("%s (%s/%s)", exc, failures, MAX_CONSECUTIVE_FAILURES)
        await asyncio.sleep(RECONNECT_SECONDS)
    logger.error("stopping: %s errors in a row", MAX_CONSECUTIVE_FAILURES)
    return 1


async def amain(argv: Sequence[str]) -> int:
    """List live Dota 2 matches, or follow the one named by the first argument."""
    if len(argv) < 2:
        live = await list_live_matches()
        log_matches(live)
        logger.info("pass a team name, match id, or Thunderpick URL to follow")
        return 0
    try:
        match = await resolve_match(argv[1])
        hero_names = await load_hero_names()
        logger.info("match %s %s", match.id, match.name)
        return await follow_match(match.id, hero_names)
    except DatabetError as exc:
        raise SystemExit(str(exc)) from exc


def main() -> int:
    """CLI entry: list or follow one Thunderpick/DATA.BET match."""
    setup_logging()
    try:
        return asyncio.run(amain(sys.argv))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
