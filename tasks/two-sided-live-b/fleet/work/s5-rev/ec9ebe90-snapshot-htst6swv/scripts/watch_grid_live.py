"""Print every tick of one live Dota 2 match from the public GRID widget socket.

This is the feed the Polymarket match page renders. It needs no key: only the
gridSeriesId from the Gamma event and an `Origin: https://polymarket.com`
header. Two services matter. `series_scoreboard_v2` is zero delay and carries
the in-game clock, the kills and the sides. `series_table` runs 8s behind and
carries net worth per player. Both are far ahead of Steam, which applies the
league DotaTV delay (900s in EPL Masters).

Invocation:
  make run F=scripts/watch_grid_live.py                       # list live events
  make run F=scripts/watch_grid_live.py ARGS="<market url>"   # follow one series
  make run F=scripts/watch_grid_live.py ARGS="dota2-ks-lynx-2026-08-24"
  make run F=scripts/watch_grid_live.py ARGS="2995964"        # bare series id
"""

import asyncio
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlparse

import httpx
import websockets

from shared.constants.api import POLYMARKET_GAMMA_API
from shared.constants.strategy import GRID_FEED_STALE_SECONDS
from shared.types.polymarket import GammaLiveEvent
from shared.utils.http import get_json, http_client
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_time import format_clock
from trader.grid_widgets import (
    FINISHED_STATUS,
    GRID_WIDGETS_ORIGIN,
    MAX_CONSECUTIVE_FAILURES,
    RECONNECT_SECONDS,
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    Frame,
    NetWorthSnapshot,
    Scoreboard,
    build_socket_url,
    clock_age_seconds,
    live_clock_seconds,
    parse_frame,
    read_net_worth,
    read_scoreboard,
    same_table_content,
    select_side_players,
)

logger = get_logger(__name__)

DOTA_TAG_SLUG = "dota-2"
GAMMA_EVENT_LIMIT = 100


@dataclass(frozen=True)
class LiveEvent:
    """One live Polymarket esports event and the GRID series behind it."""

    slug: str
    title: str
    series_id: str
    score: str
    period: str


def extract_slug(selector: str) -> str:
    """Return the event slug of a Polymarket URL, or the selector when it is already a slug."""
    if not selector.startswith("http"):
        return selector
    segments = [segment for segment in urlparse(selector).path.split("/") if segment]
    if not segments:
        raise SystemExit(f"no slug in {selector!r}")
    return segments[-1]


def read_event(event: GammaLiveEvent) -> LiveEvent:
    """Project one Gamma event onto the fields this script prints."""
    metadata = event.get("eventMetadata", {})
    return LiveEvent(
        slug=event["slug"],
        title=event["title"],
        series_id=metadata.get("gridSeriesId", ""),
        score=event.get("score", ""),
        period=event.get("period", ""),
    )


def list_live_events(client: httpx.Client, tag_slug: str) -> tuple[LiveEvent, ...]:
    """List the events Gamma currently flags as live for one tag."""
    payload = cast(
        list[GammaLiveEvent],
        get_json(
            client,
            f"{POLYMARKET_GAMMA_API}/events",
            {"tag_slug": tag_slug, "closed": "false", "limit": GAMMA_EVENT_LIMIT},
        ),
    )
    live = [read_event(event) for event in payload if event.get("live")]
    return tuple(live)


def log_events(events: tuple[LiveEvent, ...]) -> None:
    """Print the live-event table used to pick a series."""
    logger.info("live events: %s", len(events))
    for event in events:
        logger.info(
            "  series=%-8s map=%-4s score=%-16s %s",
            event.series_id or "-",
            event.period or "-",
            event.score or "-",
            event.slug,
        )


def resolve_event(client: httpx.Client, selector: str) -> LiveEvent:
    """Resolve a bare series id, a slug or a market URL to one live event."""
    if selector.isdigit():
        return LiveEvent(slug="", title="", series_id=selector, score="", period="")
    slug = extract_slug(selector)
    payload = cast(
        list[GammaLiveEvent],
        get_json(client, f"{POLYMARKET_GAMMA_API}/events", {"slug": slug}),
    )
    if not payload:
        raise SystemExit(f"gamma knows no event with slug {slug!r}")
    event = read_event(payload[0])
    if not event.series_id:
        raise SystemExit(f"{slug} has no eventMetadata.gridSeriesId yet")
    return event


def grid_socket_is_stale(silence_seconds: float) -> bool:
    """True when the widget socket has been silent for the GRID watchdog budget."""
    return silence_seconds >= GRID_FEED_STALE_SECONDS


class GridSocketWatch:
    """Arm a GRID silence timer on every raw widget frame, including repeats."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._generation = 0
        self._handle: asyncio.TimerHandle | None = None
        self._last_raw = time.monotonic()
        self._stale = False

    def note_raw(self) -> None:
        """Reset the silence countdown; log recovery if the previous countdown fired."""
        self._last_raw = time.monotonic()
        if self._stale:
            logger.info("grid socket recovered")
            self._stale = False
        self._generation += 1
        expected = self._generation
        if self._handle is not None:
            self._handle.cancel()
        self._handle = self._loop.call_later(GRID_FEED_STALE_SECONDS, self._fire, expected)

    def disarm(self) -> None:
        """Cancel the pending timer so a reconnect sleep is not a STALE firing."""
        self._generation += 1
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _fire(self, expected: int) -> None:
        """Log STALE only for the current generation."""
        if expected != self._generation:
            return
        self._stale = True
        logger.warning(
            "STALE grid socket silent %.1fs (threshold %.0fs)",
            time.monotonic() - self._last_raw,
            GRID_FEED_STALE_SECONDS,
        )


def format_side_net_worth(snapshot: NetWorthSnapshot, team_id: str) -> str:
    """Six-wide side net worth, or `?` when the table's row shape is off — what the bot sees."""
    players = select_side_players(snapshot, team_id)
    if players is None:
        return "     ?"
    return f"{sum(player.net_worth for player in players):6d}"


def format_players(snapshot: NetWorthSnapshot, team_id: str) -> str:
    """Render one side as `life stealer L23 30784 ...`, richest first.

    Rows print raw, including a roster substitute the bot drops — the `nw`
    sums beside them are the selected five. LoL portraits are uuids, so
    `hero` is empty and the player nick is printed.
    """
    side = [player for player in snapshot.players if player.team_id == team_id]
    side.sort(key=lambda player: -player.net_worth)
    return "  ".join(
        f"{player.hero or player.nick} L{player.level} {player.net_worth}" for player in side
    )


def log_tick(
    board: Scoreboard,
    snapshot: NetWorthSnapshot | None,
    age: float,
    gold_age: float | None,
) -> None:
    """Print one tick line plus one hero net worth line per side."""
    scores = "-".join(str(team.kills) for team in board.teams)
    # A table from the previous map is worse than no table, so drop it.
    current = None if snapshot is None or snapshot.game_number != board.game_number else snapshot
    net_worths = "-"
    net_worth_lag = "-"
    if current is not None:
        net_worths = " ".join(
            f"{team.side[:1]}={format_side_net_worth(current, team.team_id)}"
            for team in board.teams
        )
        net_worth_lag = f"{current.feed_delay}s"
    gold_age_text = "-" if gold_age is None else f"{gold_age:.1f}s"
    logger.info(
        "t=%6s %-8s %-8s kills=%-7s nw %s nw_lag=%-3s clock_lag=%.1fs gold_age=%s",
        format_clock(live_clock_seconds(board, age)),
        board.game_status if board.clock_ticking else f"{board.game_status}/paused",
        f"map {board.game_number}",
        scores,
        net_worths,
        net_worth_lag,
        age,
        gold_age_text,
    )
    for team in board.teams:
        heroes = "-" if current is None else format_players(current, team.team_id)
        logger.info("     %-7s %-18s %s", team.side, team.name, heroes)


@dataclass(frozen=True)
class LiveState:
    """Latest scoreboard, table, and when the table last changed."""

    board: Scoreboard | None
    snapshot: NetWorthSnapshot | None
    gold_at: float | None


def apply_live_frame(frame: Frame, state: LiveState) -> LiveState | None:
    """Apply one widget frame. None when the frame is empty or repeats the last state."""
    if not frame.payload:
        return None
    if frame.service == TABLE_SERVICE:
        fresh_snapshot = read_net_worth(frame.payload, frame.delay)
        if fresh_snapshot is None:
            return None
        if state.snapshot is not None and same_table_content(fresh_snapshot, state.snapshot):
            return None
        return LiveState(state.board, fresh_snapshot, time.monotonic())
    if frame.service != SCOREBOARD_SERVICE:
        return None
    fresh_board = read_scoreboard(frame.payload)
    if fresh_board is None or fresh_board == state.board:
        return None
    return LiveState(fresh_board, state.snapshot, state.gold_at)


async def follow_series(series_id: str) -> int:
    """Stream the widget socket, printing one block per frame that changes something."""
    url = build_socket_url(series_id)
    logger.info("following series %s stale_after=%.0fs", series_id, GRID_FEED_STALE_SECONDS)
    state = LiveState(board=None, snapshot=None, gold_at=None)
    failures = 0
    while failures < MAX_CONSECUTIVE_FAILURES:
        try:
            async with websockets.connect(
                url, origin=cast(websockets.Origin, GRID_WIDGETS_ORIGIN), max_size=None
            ) as socket:
                failures = 0
                watch = GridSocketWatch(asyncio.get_running_loop())
                try:
                    async for raw in socket:
                        watch.note_raw()
                        fresh = apply_live_frame(parse_frame(str(raw)), state)
                        if fresh is None:
                            continue
                        state = fresh
                        if state.board is None:
                            continue
                        age = clock_age_seconds(state.board.occurred_at, datetime.now(UTC))
                        gold_age = (
                            None if state.gold_at is None else time.monotonic() - state.gold_at
                        )
                        log_tick(state.board, state.snapshot, age, gold_age)
                        if state.board.series_status == FINISHED_STATUS:
                            logger.info("series finished")
                            return 0
                finally:
                    watch.disarm()
        except websockets.ConnectionClosed:
            failures += 1
            logger.warning("socket closed (%s/%s)", failures, MAX_CONSECUTIVE_FAILURES)
            await asyncio.sleep(RECONNECT_SECONDS)
    logger.error("stopping: %s closed sockets in a row", MAX_CONSECUTIVE_FAILURES)
    return 1


def main() -> int:
    """List the live Dota 2 events, or follow the one named by the first argument."""
    setup_logging()
    with http_client() as client:
        if len(sys.argv) < 2:
            log_events(list_live_events(client, DOTA_TAG_SLUG))
            logger.info("pass a market url, a slug or a grid series id to follow one series")
            return 0
        event = resolve_event(client, sys.argv[1])
    if event.title:
        logger.info("%s (%s)", event.title, event.slug)
    return asyncio.run(follow_series(event.series_id))


if __name__ == "__main__":
    raise SystemExit(main())
