"""Print every Oddin scoreboard snapshot for one Dota 2 match.

A full encrypted snapshot arrives over GraphQL HTTP, then every later tick is
another full snapshot on `graphql-transport-ws`. There is no 1 Hz cap and no
dedup: each `next` prints. `--live` redraws a fixed dashboard instead.

Invocation:
  make run F=scripts/watch_oddin_live.py
  make run F=scripts/watch_oddin_live.py ARGS="Aurora"
  make run F=scripts/watch_oddin_live.py ARGS="Aurora --live"
  make run F=scripts/watch_oddin_live.py ARGS="3211324"
  make run F=scripts/watch_oddin_live.py ARGS="<match url>"
"""

import asyncio
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import websockets
from rich.columns import Columns
from rich.console import Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from shared.utils.log import get_logger, setup_logging
from shared.utils.match_time import format_clock
from trader.oddin_catalog import DisirCatalog, log_matches, select_open_match
from trader.oddin_client import (
    MAX_CONSECUTIVE_FAILURES,
    RECONNECT_SECONDS,
    REQUEST_TIMEOUT_S,
    direct_match_id,
    disir_client,
    fetch_snapshot_envelope,
    iter_scoreboard_payloads,
    require_brand_token,
    websocket_url,
    widget_config,
)
from trader.oddin_crypto import (
    AUTH_FAILED,
    OddinCryptoError,
    load_feed_key,
    open_envelope,
)
from trader.oddin_feed import ODDIN_FEED_STALE_SECONDS, project_tick
from trader.oddin_types import (
    FINISHED_STATUS,
    OddinFeedError,
    OddinTick,
    PlayerTick,
    TeamTick,
    WidgetConfig,
)

logger = get_logger(__name__)


def format_updated(raw: str) -> str:
    """Turn Oddin's `2006-01-02 15:04:05.999 +0000 UTC` into ISO-ish UTC."""
    if " +0000 UTC" not in raw:
        return raw or "-"
    head = raw.replace(" +0000 UTC", "").replace(" ", "T", 1)
    if "." not in head:
        return head + "Z"
    date, frac = head.split(".", 1)
    return f"{date}.{frac[:3]}Z"


def feed_lag_seconds(tick: OddinTick) -> float | None:
    """Seconds since Oddin's `last_updated_at`; None when it can't be parsed."""
    head = tick.last_updated_at.replace(" +0000 UTC", "+00:00")
    try:
        updated = datetime.fromisoformat(head)
    except ValueError:
        return None
    if updated.tzinfo is None:
        return None
    return (datetime.now(UTC) - updated).total_seconds()


def format_lag(lag: float | None) -> str:
    """`0.4s` or `-` when the feed timestamp can't be parsed."""
    return "-" if lag is None else f"{lag:.1f}s"


def player_extras(player: PlayerTick) -> str:
    """`dead=25 aegis` markers, empty for an alive player without Aegis."""
    extras: list[str] = []
    if not player.alive and player.respawn_timer is not None:
        extras.append(f"dead={player.respawn_timer}")
    if player.has_aegis:
        extras.append("aegis")
    return " ".join(extras)


def format_player(player: PlayerTick) -> str:
    """One player row: `Rincyq       Anti-Mage            1529  0/0/0`."""
    extras = player_extras(player)
    suffix = f"  {extras}" if extras else ""
    return (
        f"{player.nickname:<14.14} {player.hero:<20.20} {player.net_worth:>6}  "
        f"{player.kills}/{player.deaths}/{player.assists}{suffix}"
    )


def format_side_lines(tag: str, team: TeamTick) -> tuple[str, ...]:
    """Faction header followed by one line per player."""
    return (
        f"     {tag} {team.name}",
        *(f"       {format_player(player)}" for player in team.players),
    )


def format_tick_lines(seq: int, dt: float | None, source: str, tick: OddinTick) -> tuple[str, ...]:
    """The scrolling terminal block for one received payload."""
    clock = "-" if tick.game_time is None else format_clock(tick.game_time)
    map_order = "-" if tick.map_order is None else str(tick.map_order)
    if tick.radiant is None or tick.dire is None:
        kills = "-"
        net_worth = "-"
        lead = "-"
        paused = "-"
        sides = (
            f"     home {tick.home_name}",
            f"     away {tick.away_name}",
        )
        objectives = "     objectives -"
    else:
        kills = f"R{tick.radiant.kills}:D{tick.dire.kills}"
        net_worth = f"R{tick.radiant.net_worth}:D{tick.dire.net_worth}"
        lead_value = tick.net_worth_lead
        lead = "-" if lead_value is None else f"{lead_value:+d}"
        paused = "yes" if tick.map_paused else "no"
        sides = (
            *format_side_lines("R", tick.radiant),
            *format_side_lines("D", tick.dire),
        )
        objectives = (
            f"     objectives R towers={tick.radiant.towers} rax={tick.radiant.barracks} "
            f"roshan={tick.radiant.roshans} | D towers={tick.dire.towers} "
            f"rax={tick.dire.barracks} roshan={tick.dire.roshans}"
        )
    header = (
        f"#{seq} dt={'-' if dt is None else f'{dt:.3f}s'} source={source} "
        f"updated={format_updated(tick.last_updated_at)} "
        f"lag={format_lag(feed_lag_seconds(tick))}"
    )
    summary = (
        f"t={clock} {tick.match_status} map={map_order} "
        f"series={tick.home_score}:{tick.away_score} kills={kills} "
        f"nw={net_worth} lead={lead} paused={paused}"
    )
    return (header, summary, *sides, objectives)


def log_tick(seq: int, dt: float | None, source: str, tick: OddinTick) -> None:
    """Print one received payload immediately."""
    for line in format_tick_lines(seq, dt, source, tick):
        logger.info("%s", line)


def live_side_table(tag: str, team: TeamTick, style: str) -> Table:
    """One faction's player table for the live dashboard."""
    table = Table(title=f"{tag} {team.name}", title_style=style, box=None, pad_edge=False)
    table.add_column("player")
    table.add_column("hero", style="cyan")
    table.add_column("nw", justify="right")
    table.add_column("k/d/a")
    table.add_column("")
    for player in team.players:
        table.add_row(
            player.nickname,
            player.hero,
            f"{player.net_worth:,}",
            f"{player.kills}/{player.deaths}/{player.assists}",
            player_extras(player),
            style="" if player.alive else "dim",
        )
    return table


def team_side(tick: OddinTick) -> str | None:
    """`R` when home is radiant, `D` when home is dire, None when unknown."""
    if tick.radiant is not None and tick.radiant.name == tick.home_name:
        return "R"
    if tick.dire is not None and tick.dire.name == tick.home_name:
        return "D"
    return None


def lag_style(lag: float | None) -> str:
    """Dim under normal freshness; loud once the feed falls behind."""
    if lag is None:
        return "dim"
    if lag >= ODDIN_FEED_STALE_SECONDS:
        return "bold red"
    if lag >= 5:
        return "yellow"
    return "dim"


def build_live_view(seq: int, dt: float | None, source: str, tick: OddinTick, label: str) -> Group:
    """The whole dashboard redrawn for one received payload."""
    clock = "-" if tick.game_time is None else format_clock(tick.game_time)
    map_order = "-" if tick.map_order is None else str(tick.map_order)
    dt_text = "-" if dt is None else f"{dt:.2f}s"
    lag = feed_lag_seconds(tick)
    header = Text.assemble(
        (
            f"{label}  ·  #{seq} {source} dt={dt_text} upd={format_updated(tick.last_updated_at)}",
            "dim",
        ),
        (f"  lag={format_lag(lag)}", lag_style(lag)),
    )
    home_side = team_side(tick)
    if home_side == "R":
        home_style, away_style, away_side = "bold green", "bold red", "D"
    elif home_side == "D":
        home_style, away_style, away_side = "bold red", "bold green", "R"
    else:
        home_style, away_style, away_side = "bold", "bold", None
    score = Text()
    score.append(tick.home_name, style=home_style)
    if home_side is not None:
        score.append(f" ({home_side})", style=home_style)
    score.append(f"  {tick.home_score}:{tick.away_score}  ", style="bold")
    score.append(tick.away_name, style=away_style)
    if away_side is not None:
        score.append(f" ({away_side})", style=away_style)
    status = Text(f"map {map_order}  ·  {clock}  ·  {tick.match_status}")
    if tick.map_paused:
        status.append("  ·  PAUSED", style="bold yellow")
    if tick.radiant is None or tick.dire is None:
        return Group(header, score, status, Text("waiting for map", style="dim"))
    lead = tick.net_worth_lead or 0
    stats = Text.assemble(
        (f"{tick.radiant.kills}", "bold green"),
        ":",
        (f"{tick.dire.kills}", "bold red"),
        " kills   nw ",
        (f"{tick.radiant.net_worth:,}", "green"),
        ":",
        (f"{tick.dire.net_worth:,}", "red"),
        (f" ({lead:+,})", "green" if lead >= 0 else "red"),
        "   twr ",
        (f"{tick.radiant.towers}", "green"),
        ":",
        (f"{tick.dire.towers}", "red"),
        "  rax ",
        (f"{tick.radiant.barracks}", "green"),
        ":",
        (f"{tick.dire.barracks}", "red"),
        "  rosh ",
        (f"{tick.radiant.roshans}", "green"),
        ":",
        (f"{tick.dire.roshans}", "red"),
    )
    sides = Columns(
        (
            live_side_table("R", tick.radiant, "bold green"),
            live_side_table("D", tick.dire, "bold red"),
        ),
        equal=True,
        expand=True,
    )
    return Group(header, score, status, stats, Text(""), sides)


DisplayTick = Callable[[int, float | None, str, OddinTick], None]


@dataclass(frozen=True)
class WatchState:
    """Sequence and timing carried across snapshot and websocket payloads."""

    seq: int
    last_at: float | None


def next_watch_state(state: WatchState, now: float) -> tuple[WatchState, int, float | None]:
    """Advance the payload counter and return (state, seq, dt)."""
    dt = None if state.last_at is None else now - state.last_at
    seq = state.seq
    return WatchState(seq + 1, now), seq, dt


def emit_payload(
    payload: dict[str, object],
    source: str,
    state: WatchState,
    now: float,
    display: DisplayTick,
) -> tuple[WatchState, OddinTick]:
    """Project, render, and advance the watch state."""
    tick = project_tick(payload)
    state, seq, dt = next_watch_state(state, now)
    display(seq, dt, source, tick)
    return state, tick


def start_live_view(live_mode: bool) -> Live | None:
    """Alternate-screen dashboard, or None when scrolling or not a TTY."""
    if not live_mode:
        return None
    if not sys.stdout.isatty():
        logger.warning("--live needs a terminal; falling back to scrolling output")
        return None
    view = Live(screen=True, auto_refresh=False)
    view.start()
    return view


async def stream_scoreboard(
    client: httpx.AsyncClient,
    config: WidgetConfig,
    key: bytes,
    state: WatchState,
    display: DisplayTick,
) -> tuple[WatchState, bool, bool]:
    """One snapshot plus its websocket stream: (state, saw_tick, finished)."""
    snapshot = await fetch_snapshot_envelope(client, config)
    state, tick = emit_payload(
        open_envelope(snapshot, key), "snapshot", state, time.monotonic(), display
    )
    if tick.match_status == FINISHED_STATUS:
        return state, True, True
    saw_tick = True
    async for payload in iter_scoreboard_payloads(config, key, ODDIN_FEED_STALE_SECONDS):
        state, tick = emit_payload(payload, "ws", state, time.monotonic(), display)
        saw_tick = True
        if tick.match_status == FINISHED_STATUS:
            return state, True, True
    return state, saw_tick, False


async def follow_match(
    client: httpx.AsyncClient, match_id: str, home: str, away: str, live_mode: bool
) -> int:
    """Print the HTTP snapshot, then every websocket `next`, reconnecting as needed."""
    key = load_feed_key()
    config = widget_config(match_id)
    logger.info(
        "following %s %s vs %s socket=%s stale_after=%.0fs",
        match_id,
        home,
        away,
        websocket_url(config.brand_token).split("?", 1)[0],
        ODDIN_FEED_STALE_SECONDS,
    )
    view = start_live_view(live_mode)
    label = f"{match_id} {home} vs {away}"

    def display(seq: int, dt: float | None, source: str, tick: OddinTick) -> None:
        if view is None:
            log_tick(seq, dt, source, tick)
        else:
            view.update(build_live_view(seq, dt, source, tick, label), refresh=True)

    state = WatchState(1, None)
    failures = 0
    try:
        while failures < MAX_CONSECUTIVE_FAILURES:
            try:
                state, saw_tick, finished = await stream_scoreboard(
                    client, config, key, state, display
                )
                if finished:
                    return 0
                if saw_tick:
                    failures = 0
                    logger.info("scoreboard complete, reconnecting")
                else:
                    failures += 1
                    logger.warning(
                        "scoreboard complete without ticks (%s/%s)",
                        failures,
                        MAX_CONSECUTIVE_FAILURES,
                    )
            except OddinCryptoError as exc:
                failures += 1
                logger.warning("%s (%s/%s)", exc, failures, MAX_CONSECUTIVE_FAILURES)
                if AUTH_FAILED in str(exc):
                    logger.error("%s", AUTH_FAILED)
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
    finally:
        if view is not None:
            view.stop()
    logger.error("stopping: %s errors in a row", MAX_CONSECUTIVE_FAILURES)
    return 1


async def amain(argv: Sequence[str]) -> int:
    """List open Dota 2 matches, or follow a catalog name or a direct Oddin id."""
    token = require_brand_token()
    live_mode = "--live" in argv[1:]
    selector = " ".join(arg for arg in argv[1:] if not arg.startswith("-")) or None
    async with disir_client(REQUEST_TIMEOUT_S) as client:
        try:
            if selector is not None and (match_id := direct_match_id(selector)) is not None:
                logger.info("match %s", match_id)
                return await follow_match(client, match_id, match_id, match_id, live_mode)
            catalog = DisirCatalog(token, now=lambda: datetime.now(UTC), sleep=asyncio.sleep)
            if not await catalog.refresh(client):
                raise SystemExit("Disir catalog refresh failed")
            matches = catalog.open_matches()
            if selector is None:
                log_matches(matches, "dota 2")
                logger.info("pass a team name, match id, or match URL to follow")
                return 0
            match = select_open_match(matches, selector)
        except OddinFeedError as exc:
            raise SystemExit(str(exc)) from exc
        logger.info("match %s %s vs %s", match.id, match.home_name, match.away_name)
        return await follow_match(client, match.id, match.home_name, match.away_name, live_mode)


def main() -> int:
    """CLI entry: list or follow one Oddin match."""
    setup_logging()
    try:
        return asyncio.run(amain(sys.argv))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
