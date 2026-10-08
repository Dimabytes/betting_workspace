"""Print every Hawk live tick for one Dota 2 series.

Hawk embeds Inertia JSON in the page (`#app[data-page]`) and pushes the same
match states on a public Pusher socket: channel `series.{id}`, event
`App\\Events\\Match\\MatchStateCreated`. The live feed is a snapshot every
~60 game seconds (often a duplicate +2s later), not a 1s clock. Score changes
are first *seen* on that snapshot; they did not necessarily happen at that
`gameTime`. There is no `occurred_at`. Per-player net worth is not in this feed.

Invocation:
  make run F=scripts/watch_hawk_live.py
  make run F=scripts/watch_hawk_live.py ARGS="1win"
  make run F=scripts/watch_hawk_live.py ARGS="100487"
  make run F=scripts/watch_hawk_live.py ARGS="https://hawk.live/dota-2/matches/pgl-wallachia-season-9-group-stage/1win-team-vs-team-nemesis"
"""

import asyncio
import html
import json
import re
import statistics
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import cast
from urllib.parse import urlencode, urlparse

import httpx
import websockets

from shared.utils.http import http_client
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_time import format_clock

logger = get_logger(__name__)

HAWK_ORIGIN = "https://hawk.live"


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


def as_str(value: object) -> str:
    """JSON string, or empty."""
    return str(value) if value is not None else ""


HAWK_HOME = f"{HAWK_ORIGIN}/"
PUSHER_PROTOCOL = "7"
PING_SECONDS = 20.0
STALE_SECONDS = 90.0
RECONNECT_SECONDS = 3.0
MAX_CONSECUTIVE_FAILURES = 5
ECHO_CLOCK_SECONDS = 2
DATA_PAGE_RE = re.compile(r'data-page="([^"]+)"')
STATE_EVENT = "Match\\MatchStateCreated"
PICKS_EVENT = "MatchPicksUpdated"


@dataclass(frozen=True)
class WsConfig:
    """Pusher endpoint taken from the page's `wsConfig`."""

    host: str
    port: str
    key: str
    force_tls: bool


@dataclass(frozen=True)
class LiveSeries:
    """One row of the Hawk homepage `seriesList`."""

    series_id: int
    slug: str
    championship_slug: str
    championship_name: str
    team1: str
    team2: str
    best_of: int
    start_at: str
    team1_score: int
    team2_score: int
    current_match: int

    @property
    def page_url(self) -> str:
        """Series page Hawk serves the Inertia payload from."""
        return f"{HAWK_ORIGIN}/dota-2/matches/{self.championship_slug}/{self.slug}"


@dataclass(frozen=True)
class Pick:
    """One drafted hero and the player who took it."""

    is_radiant: bool
    hero: str
    player: str


@dataclass(frozen=True)
class Buildings:
    """Per-lane standing bits: T1 T2 T3 melee rax ranged rax, plus the two T4s."""

    radiant_top: str
    radiant_mid: str
    radiant_bot: str
    radiant_t4: str
    dire_top: str
    dire_mid: str
    dire_bot: str
    dire_t4: str


@dataclass(frozen=True)
class Tick:
    """One `matchState` snapshot: clock, kills, team gold lead, buildings."""

    state_id: int
    match_id: int
    game_time: int
    radiant_score: int
    dire_score: int
    radiant_net_worth_advantage: int
    buildings: Buildings
    keys: tuple[str, ...]


@dataclass(frozen=True)
class SeriesPage:
    """Seed from the series HTML: teams, draft, last state, socket config."""

    series: LiveSeries
    ws: WsConfig
    streams: tuple[str, ...]
    is_team1_radiant: bool | None
    match_id: int | None
    match_number: int | None
    is_radiant_winner: bool | None
    picks: tuple[Pick, ...]
    last_tick: Tick | None
    history_times: tuple[int, ...]


@dataclass(frozen=True)
class PusherFrame:
    """One Pusher message: event name without the `App\\Events\\` prefix."""

    event: str
    channel: str
    payload: dict[str, object]


def extract_path(selector: str) -> str:
    """Return the URL path of a Hawk page, or the selector when it is not a URL."""
    if not selector.startswith("http"):
        return selector
    path = urlparse(selector).path.rstrip("/")
    if not path:
        raise SystemExit(f"no path in {selector!r}")
    return path


def read_inertia_payload(html_text: str) -> dict[str, object]:
    """Decode the Inertia `data-page` blob, or exit if the HTML has none."""
    match = DATA_PAGE_RE.search(html_text)
    if match is None:
        raise SystemExit("Hawk HTML has no data-page JSON")
    parsed = json.loads(html.unescape(match.group(1)))
    payload = as_map(parsed)
    if payload is None:
        raise SystemExit("Hawk data-page JSON is not an object")
    return payload


def props_of(payload: dict[str, object]) -> dict[str, object]:
    """Return the Inertia `props` object."""
    props = as_map(payload.get("props"))
    if props is None:
        raise SystemExit("Hawk data-page has no props object")
    return props


def read_ws_config(props: dict[str, object]) -> WsConfig:
    """Read `wsConfig` from any Hawk Inertia page."""
    raw = as_map(props.get("wsConfig"))
    if raw is None:
        raise SystemExit("Hawk page has no wsConfig")
    return WsConfig(
        host=as_str(raw.get("host")),
        port=as_str(raw.get("port")),
        key=as_str(raw.get("key")),
        force_tls=bool(raw.get("shouldForceTls")),
    )


def read_team_name(raw: object) -> str:
    """Display name of a Hawk team object."""
    fields = as_map(raw)
    if fields is None:
        return "?"
    name = fields.get("name")
    return as_str(name) if name else "?"


def read_live_series_row(raw: dict[str, object]) -> LiveSeries:
    """Project one homepage `seriesList` row."""
    championship = as_map(raw.get("championship")) or {}
    return LiveSeries(
        series_id=as_int(raw.get("id")),
        slug=as_str(raw.get("slug")),
        championship_slug=as_str(championship.get("slug")),
        championship_name=as_str(championship.get("name")),
        team1=read_team_name(raw.get("team1")),
        team2=read_team_name(raw.get("team2")),
        best_of=as_int(raw.get("bestOf")),
        start_at=as_str(raw.get("startAt")),
        team1_score=as_int(raw.get("team1Score")),
        team2_score=as_int(raw.get("team2Score")),
        current_match=as_int(raw.get("currentMatchNumber")),
    )


def fetch_inertia_props(client: httpx.Client, url: str) -> dict[str, object]:
    """GET a Hawk HTML page and return its Inertia props."""
    response = client.get(url)
    response.raise_for_status()
    return props_of(read_inertia_payload(response.text))


def list_live_series(client: httpx.Client) -> tuple[LiveSeries, ...]:
    """List the series Hawk currently puts on the homepage live strip."""
    props = fetch_inertia_props(client, HAWK_HOME)
    rows = props.get("seriesList")
    if not isinstance(rows, list):
        return ()
    series = tuple(
        read_live_series_row(mapped)
        for item in cast(list[object], rows)
        if (mapped := as_map(item)) is not None
    )
    return series


def log_series(series: tuple[LiveSeries, ...]) -> None:
    """Print the live-series table used to pick a match."""
    logger.info("live series: %s", len(series))
    for row in series:
        logger.info(
            "  %s map=%-2s score=%s-%-2s bo%s  %s vs %s  %s",
            row.series_id,
            row.current_match or "-",
            row.team1_score,
            row.team2_score,
            row.best_of,
            row.team1,
            row.team2,
            f"{row.championship_slug}/{row.slug}",
        )


def select_series(series: tuple[LiveSeries, ...], selector: str) -> LiveSeries:
    """Pick the series whose id, slug, or team name matches the selector."""
    needle = selector.lower()
    path = extract_path(selector)
    for row in series:
        if str(row.series_id) == selector:
            return row
        if row.slug == selector or path.endswith(f"/{row.slug}"):
            return row
        if needle in row.team1.lower() or needle in row.team2.lower():
            return row
    log_series(series)
    raise SystemExit(f"no live Hawk series matches {selector!r}")


def read_picks(raw: object) -> tuple[Pick, ...]:
    """Draft from a match object; empty before picks exist."""
    if not isinstance(raw, list):
        return ()
    picks: list[Pick] = []
    for item in cast(list[object], raw):
        fields = as_map(item)
        if fields is None:
            continue
        hero = as_map(fields.get("hero")) or {}
        player = as_map(fields.get("player")) or {}
        picks.append(
            Pick(
                is_radiant=bool(fields.get("isRadiant")),
                hero=as_str(hero.get("name")) or "?",
                player=as_str(player.get("name")) or "?",
            )
        )
    return tuple(picks)


def read_buildings(raw: object) -> Buildings:
    """Lane bitstrings from `buildingState`."""
    block = as_map(raw) or {}
    radiant = as_map(block.get("radiant")) or {}
    dire = as_map(block.get("dire")) or {}
    return Buildings(
        radiant_top=as_str(radiant.get("top")),
        radiant_mid=as_str(radiant.get("mid")),
        radiant_bot=as_str(radiant.get("bot")),
        radiant_t4=as_str(radiant.get("t4")),
        dire_top=as_str(dire.get("top")),
        dire_mid=as_str(dire.get("mid")),
        dire_bot=as_str(dire.get("bot")),
        dire_t4=as_str(dire.get("t4")),
    )


def read_tick(raw: Mapping[str, object], match_id: int) -> Tick:
    """Project one `matchState` object onto the fields this watcher prints."""
    return Tick(
        state_id=as_int(raw.get("id")),
        match_id=match_id,
        game_time=as_int(raw.get("gameTime")),
        radiant_score=as_int(raw.get("radiantScore")),
        dire_score=as_int(raw.get("direScore")),
        radiant_net_worth_advantage=as_int(raw.get("radiantNetWorthAdvantage")),
        buildings=read_buildings(raw.get("buildingState")),
        keys=tuple(sorted(raw.keys())),
    )


def history_gaps(times: tuple[int, ...]) -> tuple[int, ...]:
    """Positive gameTime steps in the stored state history."""
    return tuple(later - earlier for earlier, later in pairwise(times) if later > earlier)


def median_gap(times: tuple[int, ...]) -> float | None:
    """Median stored-history step in game seconds, or None when too short."""
    gaps = history_gaps(times)
    if not gaps:
        return None
    return float(statistics.median(gaps))


def format_picks(picks: tuple[Pick, ...], radiant: bool) -> str:
    """Render one side's draft as `Hero player, ...`."""
    side = [pick for pick in picks if pick.is_radiant is radiant]
    if not side:
        return "-"
    return "  ".join(f"{pick.hero} {pick.player}" for pick in side)


def format_buildings(tick: Tick) -> str:
    """Compact lane bits, `1` standing `0` down, T1-T3 then rax then T4."""
    bld = tick.buildings
    return (
        f"R t={bld.radiant_top} m={bld.radiant_mid} b={bld.radiant_bot} t4={bld.radiant_t4}  "
        f"D t={bld.dire_top} m={bld.dire_mid} b={bld.dire_bot} t4={bld.dire_t4}"
    )


def read_streams(raw: object) -> tuple[str, ...]:
    """Twitch channel names listed on the series page."""
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in cast(list[object], raw):
        fields = as_map(item)
        if fields is not None and fields.get("name"):
            names.append(as_str(fields.get("name")))
    return tuple(names)


def read_current_match(matches: object) -> dict[str, object] | None:
    """Last unfinished map, or the last map when the series is over."""
    if not isinstance(matches, list):
        return None
    dicts = [fields for item in cast(list[object], matches) if (fields := as_map(item)) is not None]
    if not dicts:
        return None
    live = [item for item in dicts if item.get("isRadiantWinner") is None]
    return live[-1] if live else dicts[-1]


def read_series_page(props: dict[str, object]) -> SeriesPage:
    """Project `seriesPageData` plus `wsConfig` from a series HTML page."""
    spd = as_map(props.get("seriesPageData"))
    if spd is None:
        raise SystemExit("Hawk series page has no seriesPageData")
    championship = as_map(spd.get("championship")) or {}
    current = read_current_match(spd.get("matches"))
    match_id = as_int(current.get("id")) if current and current.get("id") is not None else None
    raw_states = current.get("states") if current else None
    states = cast(list[object], raw_states) if isinstance(raw_states, list) else []
    last_raw = next((fields for item in reversed(states) if (fields := as_map(item))), None)
    times = tuple(as_int(fields.get("gameTime")) for item in states if (fields := as_map(item)))
    last_tick = read_tick(last_raw, match_id or 0) if last_raw else None
    is_team1_radiant = current.get("isTeam1Radiant") if current else None
    winner = current.get("isRadiantWinner") if current else None
    number = current.get("number") if current else None
    series = LiveSeries(
        series_id=as_int(spd.get("id")),
        slug=as_str(spd.get("slug")),
        championship_slug=as_str(championship.get("slug")),
        championship_name=as_str(championship.get("name")),
        team1=read_team_name(spd.get("team1")),
        team2=read_team_name(spd.get("team2")),
        best_of=as_int(spd.get("bestOf")),
        start_at=as_str(spd.get("startAt")),
        team1_score=0,
        team2_score=0,
        current_match=as_int(number),
    )
    return SeriesPage(
        series=series,
        ws=read_ws_config(props),
        streams=read_streams(spd.get("streams")),
        is_team1_radiant=None if is_team1_radiant is None else bool(is_team1_radiant),
        match_id=match_id,
        match_number=None if number is None else as_int(number),
        is_radiant_winner=None if winner is None else bool(winner),
        picks=read_picks(current.get("picks") if current else None),
        last_tick=last_tick,
        history_times=times,
    )


def radiant_name(page: SeriesPage) -> str:
    """Radiant display name from `isTeam1Radiant`, or `Radiant` before sides are set."""
    if page.is_team1_radiant is None:
        return "Radiant"
    return page.series.team1 if page.is_team1_radiant else page.series.team2


def dire_name(page: SeriesPage) -> str:
    """Dire display name from `isTeam1Radiant`, or `Dire` before sides are set."""
    if page.is_team1_radiant is None:
        return "Dire"
    return page.series.team2 if page.is_team1_radiant else page.series.team1


def board_unchanged(previous: Tick, tick: Tick) -> bool:
    """True when score, team gold, and buildings match; clock may still differ."""
    return (
        previous.radiant_score == tick.radiant_score
        and previous.dire_score == tick.dire_score
        and previous.radiant_net_worth_advantage == tick.radiant_net_worth_advantage
        and previous.buildings == tick.buildings
    )


def is_echo_snapshot(previous: Tick, tick: Tick) -> bool:
    """True for Hawk's +2s duplicate after the minute snapshot."""
    clock_dt = tick.game_time - previous.game_time
    return board_unchanged(previous, tick) and 0 < clock_dt <= ECHO_CLOCK_SECONDS


def log_tick(
    page: SeriesPage,
    tick: Tick,
    wall_dt: float | None,
    clock_dt: int | None,
    source: str,
) -> None:
    """Print one state line plus buildings."""
    logger.info(
        "t=%5s map=%s %2d:%-2d nw_R-D=%+7d dt=%s dclock=%s src=%s state=%s",
        format_clock(tick.game_time),
        page.match_number or "-",
        tick.radiant_score,
        tick.dire_score,
        tick.radiant_net_worth_advantage,
        f"{wall_dt:.1f}s" if wall_dt is not None else "-",
        f"{clock_dt:+d}" if clock_dt is not None else "-",
        source,
        tick.state_id,
    )
    logger.info("     R %-18s", radiant_name(page))
    logger.info("     D %-18s", dire_name(page))
    logger.info("     %s", format_buildings(tick))


def log_score_delta(tick: Tick, previous: Tick | None) -> None:
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


def log_seed(page: SeriesPage) -> None:
    """Print what the HTML already has before the socket starts."""
    series = page.series
    gap = median_gap(page.history_times)
    keys = page.last_tick.keys if page.last_tick else ()
    logger.info(
        "following %s: %s vs %s  bo%s map=%s team1_radiant=%s match=%s",
        series.series_id,
        series.team1,
        series.team2,
        series.best_of,
        page.match_number or "-",
        page.is_team1_radiant,
        page.match_id or "-",
    )
    logger.info("  %s", series.page_url)
    logger.info("  streams=%s", ",".join(page.streams) or "-")
    logger.info(
        "  history n=%d clock=%s -> %s median_gap=%s (snapshots, not 1s ticks) "
        "player_net_worth=absent keys=%s",
        len(page.history_times),
        format_clock(page.history_times[0]) if page.history_times else "-",
        format_clock(page.history_times[-1]) if page.history_times else "-",
        f"{gap:.0f}s" if gap is not None else "-",
        ",".join(keys) or "-",
    )
    logger.info("     R %-18s %s", radiant_name(page), format_picks(page.picks, True))
    logger.info("     D %-18s %s", dire_name(page), format_picks(page.picks, False))
    if page.last_tick is not None:
        log_tick(page, page.last_tick, None, None, "html")


def strip_event_prefix(event: str) -> str:
    """Drop Laravel's `App\\Events\\` namespace from a Pusher event name."""
    return event.removeprefix("App\\Events\\")


def parse_pusher_data(raw: object) -> dict[str, object]:
    """Pusher `data` is a JSON string; already-decoded objects pass through."""
    if isinstance(raw, str):
        parsed = json.loads(raw)
    else:
        parsed = raw
    return as_map(parsed) or {}


def parse_pusher_frame(raw: str) -> PusherFrame:
    """Decode one Pusher text frame."""
    frame = as_map(json.loads(raw))
    if frame is None:
        return PusherFrame(event="invalid", channel="", payload={})
    return PusherFrame(
        event=strip_event_prefix(as_str(frame.get("event"))),
        channel=as_str(frame.get("channel")),
        payload=parse_pusher_data(frame.get("data")),
    )


def build_socket_url(ws: WsConfig) -> str:
    """Pusher URL Hawk's Echo client uses (`wsConfig` + protocol 7)."""
    scheme = "wss" if ws.force_tls else "ws"
    query = urlencode(
        {"protocol": PUSHER_PROTOCOL, "client": "js", "version": "8.4.0", "flash": "false"}
    )
    return f"{scheme}://{ws.host}:{ws.port}/app/{ws.key}?{query}"


def subscribe_message(series_id: int) -> str:
    """Pusher subscribe for the public `series.{id}` channel the page listens on."""
    return json.dumps(
        {"event": "pusher:subscribe", "data": {"auth": "", "channel": f"series.{series_id}"}}
    )


def tick_from_state_event(payload: dict[str, object]) -> Tick | None:
    """Read `MatchStateCreated` `{matchId, matchState}`, or None if either is missing."""
    match_state = as_map(payload.get("matchState"))
    match_id = payload.get("matchId")
    if match_state is None or match_id is None:
        return None
    return read_tick(match_state, as_int(match_id))


class HawkWatch:
    """Latest series page, last printed tick, and when that tick arrived."""

    def __init__(self, page: SeriesPage) -> None:
        self.page = page
        self.picks = page.picks
        self.tick = page.last_tick
        self.tick_at = time.monotonic() if page.last_tick is not None else None
        self.stale = False

    def check_stale(self) -> None:
        """Log STALE when MatchStateCreated has been silent past STALE_SECONDS."""
        if self.tick_at is None:
            return
        silence = time.monotonic() - self.tick_at
        if silence < STALE_SECONDS:
            if self.stale:
                logger.info("hawk state recovered")
                self.stale = False
            return
        if not self.stale:
            logger.warning("STALE no MatchStateCreated for %.1fs", silence)
            self.stale = True

    def apply_tick(self, tick: Tick) -> None:
        """Print a new match state and the kill marker when the score changed."""
        previous = self.tick
        now = time.monotonic()
        wall_dt = None if self.tick_at is None else now - self.tick_at
        clock_dt = None if previous is None else tick.game_time - previous.game_time
        if previous is not None and tick.state_id == previous.state_id:
            return
        if previous is not None and is_echo_snapshot(previous, tick):
            return
        log_tick(self.page, tick, wall_dt, clock_dt, "ws")
        log_score_delta(tick, previous)
        self.tick = tick
        self.tick_at = now


def log_foreign_event(frame: PusherFrame) -> None:
    """Print non-state events so the log shows every field Hawk actually sends."""
    logger.info("event %s %s", frame.event, json.dumps(frame.payload, sort_keys=True))


def apply_frame(watch: HawkWatch, frame: PusherFrame) -> None:
    """Route one application event. Pusher control events are ignored here."""
    if frame.event == STATE_EVENT:
        tick = tick_from_state_event(frame.payload)
        if tick is None:
            logger.warning("MatchStateCreated missing matchState: %s", frame.payload)
            return
        watch.apply_tick(tick)
        return
    if frame.event.startswith("pusher"):
        if frame.event == "pusher_internal:subscription_succeeded":
            logger.info("subscribed %s", frame.channel)
        return
    if frame.event == PICKS_EVENT:
        picks = read_picks(frame.payload.get("picks"))
        if picks:
            watch.picks = picks
        logger.info("picks R %s", format_picks(watch.picks, True))
        logger.info("picks D %s", format_picks(watch.picks, False))
        return
    log_foreign_event(frame)


async def wait_for_hello(socket: websockets.ClientConnection) -> None:
    """Block until Pusher sends `connection_established`; subscribe before that is dropped."""
    raw = await asyncio.wait_for(socket.recv(), timeout=10)
    frame = parse_pusher_frame(str(raw))
    if frame.event != "pusher:connection_established":
        raise TimeoutError(f"expected connection_established, got {frame.event}")


async def consume_socket(socket: websockets.ClientConnection, watch: HawkWatch) -> None:
    """Read frames until the socket closes; ping so Pusher does not idle-drop us."""

    async def ping() -> None:
        while True:
            await asyncio.sleep(PING_SECONDS)
            await socket.send(json.dumps({"event": "pusher:ping", "data": {}}))
            watch.check_stale()

    pinger = asyncio.create_task(ping())
    try:
        async for raw in socket:
            apply_frame(watch, parse_pusher_frame(str(raw)))
    finally:
        pinger.cancel()


async def follow_series(page: SeriesPage) -> int:
    """Subscribe to `series.{id}` and print until the socket dies too many times."""
    url = build_socket_url(page.ws)
    logger.info(
        "socket %s channel=series.%s stale_after=%.0fs",
        url.split("?", 1)[0],
        page.series.series_id,
        STALE_SECONDS,
    )
    watch = HawkWatch(page)
    failures = 0
    while failures < MAX_CONSECUTIVE_FAILURES:
        try:
            async with websockets.connect(
                url, origin=cast(websockets.Origin, HAWK_ORIGIN), max_size=None
            ) as socket:
                failures = 0
                await wait_for_hello(socket)
                await socket.send(subscribe_message(page.series.series_id))
                await consume_socket(socket, watch)
        except (TimeoutError, websockets.ConnectionClosed, OSError) as exc:
            failures += 1
            logger.warning("%s (%s/%s)", exc, failures, MAX_CONSECUTIVE_FAILURES)
            await asyncio.sleep(RECONNECT_SECONDS)
    logger.error("stopping: %s closed sockets in a row", MAX_CONSECUTIVE_FAILURES)
    return 1


def resolve_page(client: httpx.Client, selector: str) -> SeriesPage:
    """Resolve a URL, series id, slug, or team name to a seeded series page."""
    if selector.startswith("http"):
        return read_series_page(fetch_inertia_props(client, selector))
    live = list_live_series(client)
    chosen = select_series(live, selector)
    return read_series_page(fetch_inertia_props(client, chosen.page_url))


def main() -> int:
    """List homepage live series, or follow the one named by the first argument."""
    setup_logging()
    with http_client() as client:
        if len(sys.argv) < 2:
            log_series(list_live_series(client))
            logger.info("pass a hawk url, series id, slug or team name to follow one series")
            return 0
        page = resolve_page(client, sys.argv[1])
    log_seed(page)
    return asyncio.run(follow_series(page))


if __name__ == "__main__":
    raise SystemExit(main())
