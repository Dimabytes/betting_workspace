"""Live lolesports schedule plus Gamma LoL events, for the livestats watcher."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx

from lol.constants import (
    LOL_SPAWN_SEARCH_SECONDS,
    LOL_WINDOW_STEP_SECONDS,
    LOLESPORTS_API_KEY,
    LOLESPORTS_ESPORTS_API,
    LOLESPORTS_WINDOW_API,
)
from lol.livestats_frames import ParsedPlayer, ParsedSides, parse_sides
from lol.lolesports_match import score_lol_team_name
from shared.constants.api import POLYMARKET_GAMMA_API
from shared.utils.team_names import pick_pair_orientation

GAMMA_EVENT_LIMIT = 100
LOL_TAG_SLUG = "league-of-legends"
WINDOW_BACK_SECONDS = 75
VS_SPLIT = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)
TITLE_RIGHT_TRIM = re.compile(r"\s+\(")


@dataclass(frozen=True)
class LivePmEvent:
    """One Gamma-live LoL event. GRID id may be missing."""

    slug: str
    title: str
    grid_series_id: str | None
    team_a: str
    team_b: str
    score: str
    period: str


@dataclass(frozen=True)
class LiveLolGame:
    """One in-progress lolesports map from getLive."""

    esports_game_id: str
    game_number: int | None
    league: str
    team_a_name: str
    team_a_code: str
    team_b_name: str
    team_b_code: str


@dataclass(frozen=True)
class LivePair:
    """A unique name match from one Polymarket event onto one getLive map."""

    event: LivePmEvent
    game: LiveLolGame


@dataclass(frozen=True)
class WindowFetch:
    """One livestats window HTTP result. `payload` is set only on 200."""

    status_code: int
    payload: object | None


@dataclass(frozen=True)
class TickPlayer:
    """One participant on a livestats tick, richest-first printer input."""

    side: str
    label: str
    level: int
    gold: int


@dataclass(frozen=True)
class LivestatsTick:
    """Newest parseable frame in a window, with age versus wall clock."""

    stamp: str
    wall_seconds: float
    frame_age_seconds: float
    game_seconds: float | None
    blue_gold: int
    red_gold: int
    blue_kills: int
    red_kills: int
    players: tuple[TickPlayer, ...]


def aligned_starting_time(now: datetime, back_seconds: int) -> str:
    """A 10-second-aligned livestats window start that the feed accepts.

    The feed rejects any window younger than about 60 seconds with 400.
    """
    moment = now.astimezone(UTC).replace(microsecond=0) - timedelta(seconds=back_seconds)
    remainder = moment.second % LOL_WINDOW_STEP_SECONDS
    moment = moment - timedelta(seconds=remainder)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def names_from_title(title: str) -> tuple[str, str] | None:
    """Split `LoL: A vs B (BO5) - league` into the two team names."""
    text = title.strip()
    if text.lower().startswith("lol:"):
        text = text[4:].strip()
    parts = VS_SPLIT.split(text, maxsplit=1)
    if len(parts) != 2:
        return None
    left = parts[0].strip()
    right = TITLE_RIGHT_TRIM.split(parts[1], maxsplit=1)[0].strip()
    if not left or not right:
        return None
    return left, right


def names_from_outcomes(raw: object) -> tuple[str, str] | None:
    """Read two outcome labels from a Gamma `outcomes` string or list."""
    labels: list[str]
    if isinstance(raw, str):
        try:
            parsed: object = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, list):
            return None
        labels = [str(item) for item in cast(list[object], parsed)]
    elif isinstance(raw, list):
        labels = [str(item) for item in cast(list[object], raw)]
    else:
        return None
    if len(labels) != 2 or not labels[0] or not labels[1]:
        return None
    return labels[0], labels[1]


def _mapping(value: object) -> Mapping[str, object] | None:
    """Return a string-key mapping, or None when the JSON node is not an object."""
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    """Non-empty string, else empty."""
    if isinstance(value, str) and value:
        return value
    return ""


def _outcome_pair_from_markets(markets: object) -> tuple[str, str] | None:
    """First two-label outcome pair on a Gamma markets list."""
    if not isinstance(markets, list):
        return None
    for item in cast(list[object], markets):
        market = _mapping(item)
        if market is None:
            continue
        pair = names_from_outcomes(market.get("outcomes"))
        if pair is not None:
            return pair
    return None


def parse_live_pm_event(raw: object) -> LivePmEvent | None:
    """Project one Gamma event onto watcher fields. Skip non-objects and blank slugs."""
    event = _mapping(raw)
    if event is None:
        return None
    slug = _text(event.get("slug"))
    title = _text(event.get("title"))
    if not slug:
        return None
    metadata = _mapping(event.get("eventMetadata")) or {}
    grid = metadata.get("gridSeriesId")
    grid_series_id = grid if isinstance(grid, str) and grid else None
    names = _outcome_pair_from_markets(event.get("markets"))
    if names is None:
        names = names_from_title(title)
    team_a = names[0] if names is not None else ""
    team_b = names[1] if names is not None else ""
    return LivePmEvent(
        slug=slug,
        title=title or slug,
        grid_series_id=grid_series_id,
        team_a=team_a,
        team_b=team_b,
        score=_text(event.get("score")),
        period=_text(event.get("period")),
    )


def _read_live_team(raw: object) -> tuple[str, str] | None:
    """Name and code from one getLive match team object."""
    team = _mapping(raw)
    if team is None:
        return None
    name = _text(team.get("name"))
    code = _text(team.get("code"))
    if not name and not code:
        return None
    return name or code, code


def _game_number(raw: object) -> int | None:
    """Map number when getLive sends a whole int."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def parse_in_progress_games(raw: object) -> tuple[LiveLolGame, ...]:
    """Every in-progress game on one getLive event object."""
    event = _mapping(raw)
    if event is None:
        return ()
    league_node = _mapping(event.get("league")) or {}
    league = _text(league_node.get("name")) or _text(league_node.get("slug"))
    match = _mapping(event.get("match"))
    if match is None:
        return ()
    teams_raw = match.get("teams")
    if not isinstance(teams_raw, list) or len(cast(list[object], teams_raw)) < 2:
        return ()
    team_rows = cast(list[object], teams_raw)
    team_a = _read_live_team(team_rows[0])
    team_b = _read_live_team(team_rows[1])
    if team_a is None or team_b is None:
        return ()
    games_raw = match.get("games")
    if not isinstance(games_raw, list):
        return ()
    rows: list[LiveLolGame] = []
    for item in cast(list[object], games_raw):
        game = _mapping(item)
        if game is None or game.get("state") != "inProgress":
            continue
        game_id = _text(game.get("id"))
        if not game_id:
            continue
        rows.append(
            LiveLolGame(
                esports_game_id=game_id,
                game_number=_game_number(game.get("number")),
                league=league,
                team_a_name=team_a[0],
                team_a_code=team_a[1],
                team_b_name=team_b[0],
                team_b_code=team_b[1],
            )
        )
    return tuple(rows)


def parse_get_live_games(body: object) -> tuple[LiveLolGame, ...]:
    """Flatten in-progress maps out of a getLive JSON body."""
    root = _mapping(body)
    if root is None:
        return ()
    data = _mapping(root.get("data")) or {}
    schedule = _mapping(data.get("schedule")) or {}
    events = schedule.get("events")
    if not isinstance(events, list):
        return ()
    games: list[LiveLolGame] = []
    for item in cast(list[object], events):
        games.extend(parse_in_progress_games(item))
    return tuple(games)


def fetch_gamma_live_lol(client: httpx.Client) -> tuple[LivePmEvent, ...]:
    """List Gamma events currently flagged live for the LoL tag."""
    payload = client.get(
        f"{POLYMARKET_GAMMA_API}/events",
        params={"tag_slug": LOL_TAG_SLUG, "closed": "false", "limit": GAMMA_EVENT_LIMIT},
    )
    payload.raise_for_status()
    body: object = payload.json()
    if not isinstance(body, list):
        return ()
    events: list[LivePmEvent] = []
    for item in cast(list[object], body):
        event = parse_live_pm_event(item)
        if event is None:
            continue
        live_flag = _mapping(item)
        if live_flag is None or live_flag.get("live") is not True:
            continue
        events.append(event)
    return tuple(events)


def fetch_gamma_event(client: httpx.Client, slug: str) -> LivePmEvent:
    """Load one Gamma event by slug, live or not."""
    payload = client.get(f"{POLYMARKET_GAMMA_API}/events", params={"slug": slug})
    payload.raise_for_status()
    body: object = payload.json()
    if not isinstance(body, list) or not body:
        raise SystemExit(f"gamma knows no event with slug {slug!r}")
    event = parse_live_pm_event(cast(list[object], body)[0])
    if event is None:
        raise SystemExit(f"gamma event {slug!r} is unreadable")
    return event


def fetch_live_lol_games(client: httpx.Client) -> tuple[LiveLolGame, ...]:
    """Fetch getLive and return in-progress maps."""
    payload = client.get(
        f"{LOLESPORTS_ESPORTS_API}/getLive",
        params={"hl": "en-US"},
        headers={"x-api-key": LOLESPORTS_API_KEY},
    )
    payload.raise_for_status()
    return parse_get_live_games(payload.json())


def _window_from_response(payload: httpx.Response) -> WindowFetch:
    """Keep JSON only on HTTP 200."""
    if payload.status_code != 200:
        return WindowFetch(payload.status_code, None)
    try:
        body: object = payload.json()
    except ValueError:
        return WindowFetch(payload.status_code, None)
    return WindowFetch(payload.status_code, body)


def fetch_window(client: httpx.Client, esports_game_id: str, starting_time: str) -> WindowFetch:
    """GET one livestats window at startingTime. Body is kept only for HTTP 200."""
    payload = client.get(
        f"{LOLESPORTS_WINDOW_API}/{esports_game_id}",
        params={"startingTime": starting_time},
    )
    return _window_from_response(payload)


def fetch_first_window(client: httpx.Client, esports_game_id: str) -> WindowFetch:
    """GET the earliest livestats window (no startingTime). Used to find spawn."""
    payload = client.get(f"{LOLESPORTS_WINDOW_API}/{esports_game_id}")
    return _window_from_response(payload)


def event_matches_game(event: LivePmEvent, game: LiveLolGame) -> bool:
    """True when both Polymarket names orient against this getLive pair."""
    if not event.team_a or not event.team_b:
        return False
    orientation = pick_pair_orientation(
        score_lol_team_name(event.team_a, game.team_a_name, game.team_a_code),
        score_lol_team_name(event.team_b, game.team_b_name, game.team_b_code),
        score_lol_team_name(event.team_a, game.team_b_name, game.team_b_code),
        score_lol_team_name(event.team_b, game.team_a_name, game.team_a_code),
    )
    return orientation is not None


def games_for_pm_event(event: LivePmEvent, games: Sequence[LiveLolGame]) -> tuple[LiveLolGame, ...]:
    """getLive maps whose team names clear the linker thresholds for this event."""
    return tuple(game for game in games if event_matches_game(event, game))


def match_live_pairs(
    events: Sequence[LivePmEvent], games: Sequence[LiveLolGame]
) -> tuple[LivePair, ...]:
    """Unique event-to-map pairs. Ambiguous events are omitted."""
    pairs: list[LivePair] = []
    for event in events:
        hits = games_for_pm_event(event, games)
        if len(hits) != 1:
            continue
        pairs.append(LivePair(event=event, game=hits[0]))
    return tuple(pairs)


def parse_rfc460_seconds(stamp: str) -> float | None:
    """Parse rfc460Timestamp to float unix seconds; None if invalid."""
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp()


def _participant_label(raw: object) -> tuple[int, str] | None:
    """participantId plus summoner or champion label from one metadata row."""
    row = _mapping(raw)
    if row is None:
        return None
    participant_id = row.get("participantId")
    if isinstance(participant_id, bool) or not isinstance(participant_id, int):
        return None
    summoner = _text(row.get("summonerName"))
    champion = _text(row.get("championId"))
    label = summoner or champion
    if not label:
        return None
    return participant_id, label


def read_participant_labels(metadata: object) -> Mapping[int, str]:
    """participantId -> summoner/champion from both sides' metadata lists."""
    root = _mapping(metadata)
    if root is None:
        return {}
    labels: dict[int, str] = {}
    for key in ("blueTeamMetadata", "redTeamMetadata"):
        side = _mapping(root.get(key))
        if side is None:
            continue
        rows = side.get("participantMetadata")
        if not isinstance(rows, list):
            continue
        for item in cast(list[object], rows):
            parsed = _participant_label(item)
            if parsed is None:
                continue
            labels[parsed[0]] = parsed[1]
    return labels


def _tick_players(sides: ParsedSides, labels: Mapping[int, str]) -> tuple[TickPlayer, ...]:
    """BLUE then RED players, each side richest first."""

    def side_players(side: str, players: tuple[ParsedPlayer, ...]) -> list[TickPlayer]:
        ranked = sorted(players, key=lambda player: -player.gold)
        return [
            TickPlayer(
                side=side,
                label=labels.get(player.participant_id, f"p{player.participant_id}"),
                level=player.level,
                gold=player.gold,
            )
            for player in ranked
        ]

    return tuple(
        (*side_players("BLUE", sides.blue.players), *side_players("RED", sides.red.players))
    )


def spawn_wall_from_payload(payload: object) -> float | None:
    """Wall seconds of the first parseable frame that already has gold (spawn)."""
    root = _mapping(payload)
    if root is None:
        return None
    frames = root.get("frames")
    if not isinstance(frames, list):
        return None
    for item in cast(list[object], frames):
        frame = _mapping(item)
        if frame is None:
            continue
        stamp = _text(frame.get("rfc460Timestamp"))
        wall = parse_rfc460_seconds(stamp)
        if wall is None:
            continue
        sides = parse_sides(frame)
        if isinstance(sides, int):
            continue
        golds = [player.gold for player in (*sides.blue.players, *sides.red.players)]
        if any(gold > 0 for gold in golds):
            return wall
    return None


def earliest_wall_from_payload(payload: object) -> float | None:
    """Wall seconds of the first frame stamp in a window, gold or not."""
    root = _mapping(payload)
    if root is None:
        return None
    frames = root.get("frames")
    if not isinstance(frames, list):
        return None
    for item in cast(list[object], frames):
        frame = _mapping(item)
        if frame is None:
            continue
        wall = parse_rfc460_seconds(_text(frame.get("rfc460Timestamp")))
        if wall is not None:
            return wall
    return None


def spawn_seek_starts(origin_wall: float, search_seconds: int) -> tuple[str, ...]:
    """10-second startingTime values from the origin stamp through the spawn search cap."""
    origin = int(origin_wall)
    cursor = origin - (origin % LOL_WINDOW_STEP_SECONDS)
    end = origin + search_seconds
    starts: list[str] = []
    while cursor <= end:
        starts.append(datetime.fromtimestamp(cursor, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        cursor += LOL_WINDOW_STEP_SECONDS
    return tuple(starts)


def seek_spawn_wall(client: httpx.Client, esports_game_id: str) -> float | None:
    """Find spawn gold. The no-startingTime window is often still all zeros."""
    first = fetch_first_window(client, esports_game_id)
    if first.status_code != 200 or first.payload is None:
        return None
    spawn = spawn_wall_from_payload(first.payload)
    if spawn is not None:
        return spawn
    origin = earliest_wall_from_payload(first.payload)
    if origin is None:
        return None
    for starting_time in spawn_seek_starts(origin, LOL_SPAWN_SEARCH_SECONDS):
        fetched = fetch_window(client, esports_game_id, starting_time)
        if fetched.status_code != 200 or fetched.payload is None:
            continue
        spawn = spawn_wall_from_payload(fetched.payload)
        if spawn is not None:
            return spawn
    return None


def project_tick(
    payload: object, now_seconds: float, spawn_wall_seconds: float | None
) -> LivestatsTick | None:
    """Newest parseable window frame as a tick, or None when none parse."""
    root = _mapping(payload)
    if root is None:
        return None
    frames = root.get("frames")
    if not isinstance(frames, list):
        return None
    labels = read_participant_labels(root.get("gameMetadata"))
    chosen: LivestatsTick | None = None
    for item in cast(list[object], frames):
        frame = _mapping(item)
        if frame is None:
            continue
        stamp = _text(frame.get("rfc460Timestamp"))
        wall = parse_rfc460_seconds(stamp)
        if wall is None:
            continue
        sides = parse_sides(frame)
        if isinstance(sides, int):
            continue
        game_seconds = None if spawn_wall_seconds is None else wall - spawn_wall_seconds
        chosen = LivestatsTick(
            stamp=stamp,
            wall_seconds=wall,
            frame_age_seconds=now_seconds - wall,
            game_seconds=game_seconds,
            blue_gold=sides.blue.total_gold,
            red_gold=sides.red.total_gold,
            blue_kills=sides.blue.total_kills,
            red_kills=sides.red.total_kills,
            players=_tick_players(sides, labels),
        )
    return chosen
