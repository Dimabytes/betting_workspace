"""Print livestats ticks for a live LoL map that is on Polymarket.

Official Riot window feed (`getLive` + `livestats/v1/window`), not the GRID
widget. GRID-less Challengers cards still list. `t=` is spawn-relative game
clock from the first gold frame (the no-startingTime window is often still
zeros, so the watcher walks 10s windows from that stamp); `frame_age` is wall
minus `rfc460Timestamp` (~55-75s). Pauses are not subtracted.

Invocation:
  make run F=scripts/watch_lol_livestats.py
  make run F=scripts/watch_lol_livestats.py ARGS="<market url>"
  make run F=scripts/watch_lol_livestats.py ARGS="lol-nsea-dnsc-2026-08-31"
  make run F=scripts/watch_lol_livestats.py ARGS="115548681803406198"
"""

import sys
import time
from datetime import UTC, datetime

import httpx
from watch_grid_live import extract_slug

from lol.live_schedule import (
    WINDOW_BACK_SECONDS,
    LiveLolGame,
    LivePmEvent,
    LivestatsTick,
    aligned_starting_time,
    fetch_gamma_event,
    fetch_gamma_live_lol,
    fetch_live_lol_games,
    fetch_window,
    games_for_pm_event,
    match_live_pairs,
    project_tick,
    seek_spawn_wall,
)
from shared.utils.http import http_client
from shared.utils.log import RepeatingWarning, get_logger, setup_logging
from shared.utils.match_time import format_clock

logger = get_logger(__name__)

POLL_SECONDS = 1.0
MAX_CONSECUTIVE_FAILURES = 5


def log_pm_events(events: tuple[LivePmEvent, ...]) -> None:
    """Print live Polymarket LoL events, GRID id optional."""
    logger.info("live Polymarket LoL events: %s", len(events))
    for event in events:
        logger.info(
            "  grid=%-10s map=%-4s score=%-16s %s",
            event.grid_series_id or "-",
            event.period or "-",
            event.score or "-",
            event.slug,
        )


def log_live_games(games: tuple[LiveLolGame, ...]) -> None:
    """Print in-progress lolesports maps from getLive."""
    logger.info("live lolesports games: %s", len(games))
    for game in games:
        map_text = "-" if game.game_number is None else str(game.game_number)
        logger.info(
            "  game=%s map=%-2s %-16s %s vs %s",
            game.esports_game_id,
            map_text,
            game.league or "-",
            game.team_a_code or game.team_a_name,
            game.team_b_code or game.team_b_name,
        )


def log_pairs(events: tuple[LivePmEvent, ...], games: tuple[LiveLolGame, ...]) -> None:
    """Print unique PM↔getLive matches and events that did not bind."""
    pairs = match_live_pairs(events, games)
    logger.info("matched: %s", len(pairs))
    for pair in pairs:
        logger.info(
            "  %s -> %s (%s vs %s)",
            pair.event.slug,
            pair.game.esports_game_id,
            pair.game.team_a_code or pair.game.team_a_name,
            pair.game.team_b_code or pair.game.team_b_name,
        )
    matched_slugs = {pair.event.slug for pair in pairs}
    unmatched = [event for event in events if event.slug not in matched_slugs]
    if not unmatched:
        return
    logger.info("unmatched Polymarket events: %s", len(unmatched))
    for event in unmatched:
        logger.info("  %s (%s vs %s)", event.slug, event.team_a or "?", event.team_b or "?")


def format_side(tick: LivestatsTick, side: str) -> str:
    """Render one side as `nick L11 8234 ...`, richest first."""
    return "  ".join(
        f"{player.label} L{player.level} {player.gold}"
        for player in tick.players
        if player.side == side
    )


def log_tick(tick: LivestatsTick) -> None:
    """Print one tick line plus one gold line per side."""
    clock = "-" if tick.game_seconds is None else format_clock(tick.game_seconds)
    logger.info(
        "t=%6s frame_age=%5.1fs  B=%6d R=%6d  kills=%s-%s  stamp=%s",
        clock,
        tick.frame_age_seconds,
        tick.blue_gold,
        tick.red_gold,
        tick.blue_kills,
        tick.red_kills,
        tick.stamp,
    )
    logger.info("     %-7s %s", "BLUE", format_side(tick, "BLUE") or "-")
    logger.info("     %-7s %s", "RED", format_side(tick, "RED") or "-")


def resolve_game_id(selector: str) -> str:
    """Resolve a bare game id, slug, or Polymarket URL to one esportsGameId."""
    if selector.isdigit():
        return selector
    slug = extract_slug(selector)
    with http_client() as client:
        event = fetch_gamma_event(client, slug)
        games = fetch_live_lol_games(client)
    hits = games_for_pm_event(event, games)
    if len(hits) == 1:
        game = hits[0]
        logger.info(
            "%s -> %s (%s vs %s)",
            event.slug,
            game.esports_game_id,
            game.team_a_code or game.team_a_name,
            game.team_b_code or game.team_b_name,
        )
        return game.esports_game_id
    log_live_games(games)
    raise SystemExit(
        f"{len(hits)} of {len(games)} live games match {slug!r}: pass the esportsGameId"
    )


def follow_game(esports_game_id: str) -> int:
    """Poll livestats until 404, Ctrl-C, or too many transport failures."""
    logger.info(
        "following livestats %s back=%ss",
        esports_game_id,
        WINDOW_BACK_SECONDS,
    )
    last_stamp = ""
    failures = 0
    http_warn = RepeatingWarning()
    with http_client() as client:
        spawn_wall_seconds = None
        try:
            spawn_wall_seconds = seek_spawn_wall(client, esports_game_id)
        except httpx.HTTPError as error:
            logger.warning("spawn seek failed: %s", error)
        if spawn_wall_seconds is None:
            logger.info("spawn unknown; t= will stay blank")
        else:
            logger.info(
                "spawn t=0 at %s",
                datetime.fromtimestamp(spawn_wall_seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        while failures < MAX_CONSECUTIVE_FAILURES:
            starting_time = aligned_starting_time(datetime.now(UTC), WINDOW_BACK_SECONDS)
            try:
                fetched = fetch_window(client, esports_game_id, starting_time)
            except httpx.HTTPError as error:
                failures += 1
                logger.warning(
                    "livestats request failed (%s/%s): %s",
                    failures,
                    MAX_CONSECUTIVE_FAILURES,
                    error,
                )
                time.sleep(POLL_SECONDS)
                continue
            failures = 0
            if fetched.status_code == 404:
                logger.info("livestats 404: map over")
                return 0
            if fetched.status_code == 200 and fetched.payload is not None:
                tick = project_tick(fetched.payload, time.time(), spawn_wall_seconds)
                if tick is not None and tick.stamp != last_stamp:
                    log_tick(tick)
                    last_stamp = tick.stamp
            elif fetched.status_code == 204:
                http_warn.emit(logger, "livestats 204: no frame yet", time.monotonic())
            else:
                http_warn.emit(
                    logger,
                    f"livestats HTTP {fetched.status_code} for {starting_time}",
                    time.monotonic(),
                )
            time.sleep(POLL_SECONDS)
    logger.error("stopping: %s failed requests in a row", MAX_CONSECUTIVE_FAILURES)
    return 1


def main() -> int:
    """List live PM and getLive maps, or follow the one named by the first argument."""
    setup_logging()
    if len(sys.argv) < 2:
        with http_client() as client:
            events = fetch_gamma_live_lol(client)
            games = fetch_live_lol_games(client)
        log_pm_events(events)
        log_live_games(games)
        log_pairs(events, games)
        logger.info("pass a market url, a slug or an esportsGameId to follow one map")
        return 0
    return follow_game(resolve_game_id(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
