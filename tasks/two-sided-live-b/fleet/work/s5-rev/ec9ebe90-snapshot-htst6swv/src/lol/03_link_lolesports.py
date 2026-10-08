"""Fetch and cache lolesports schedule/events/windows, then link Polymarket events."""

import gzip
import json
import shutil
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

import httpx
import typer
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from lol.constants import (
    LOL_GAMES_PATH,
    LOL_LINK_AUDIT_PATH,
    LOL_LINKS_DIR,
    LOL_LINKS_PATH,
    LOL_MAX_CONCURRENCY,
    LOL_RAW_LOLESPORTS_DIR,
    LOL_SERIES_START_WINDOW_SECONDS,
    LOLESPORTS_API_KEY,
    LOLESPORTS_DETAILS_API,
    LOLESPORTS_ESPORTS_API,
    LOLESPORTS_WINDOW_API,
)
from lol.lolesports_match import LinkResult, LolTeam, link_events, optional_int
from lol.parquet_io import read_parquet_rows, write_parquet_rows
from lol.types import LolGameRow, LolLinkAuditRow, LolLinkRow, LolUniverseMarketRow
from shared.constants.lol import LOL_UNIVERSE_PATH
from shared.utils.filesystem import staging_directory
from shared.utils.http import http_client
from shared.utils.json_io import read_gzip_json
from shared.utils.log import print_count
from shared.utils.parsing import parse_ts

GAMES_COLUMNS: list[str] = list(LolGameRow.__annotations__)
LINKS_COLUMNS: list[str] = list(LolLinkRow.__annotations__)
AUDIT_COLUMNS: list[str] = list(LolLinkAuditRow.__annotations__)
GAMES_INTEGER_COLUMNS = [
    "start_ts",
    "best_of",
    "game_number",
    "team_a_game_wins",
    "team_b_game_wins",
    "loading_anchor_ts",
]
LINKS_INTEGER_COLUMNS = [
    "game_number",
    "loading_anchor_ts",
    "radiant_token_index",
    "resolved_outcome_index",
]
AUDIT_INTEGER_COLUMNS = ["game_number", "candidate_count"]


class RetryableHttpError(Exception):
    """Transport, HTTP 429, or 5xx that fetch retries."""


class EmptyHttpBody(Exception):
    """HTTP 200 with an empty or whitespace-only body."""


@dataclass(frozen=True)
class SchedulePages:
    """getSchedule pages.older / pages.newer tokens."""

    older: str | None
    newer: str | None


@dataclass(frozen=True)
class ScheduleEvent:
    """One getSchedule event used for paging and completed-match ids."""

    event_type: str | None
    state: str | None
    start_time: str | None
    match_id: str | None


@dataclass(frozen=True)
class ScheduleBody:
    """Validated getSchedule payload."""

    events: tuple[ScheduleEvent, ...]
    pages: SchedulePages
    raw: dict[str, object]


@dataclass(frozen=True)
class MatchGame:
    """One getEventDetails games[] entry."""

    game_id: str
    number: int | None
    state: str | None
    blue_id: str | None
    red_id: str | None


@dataclass(frozen=True)
class EventDetails:
    """Validated getEventDetails payload."""

    match_id: str
    start_time: str | None
    league_slug: str | None
    league_name: str | None
    best_of: int | None
    team_a: LolTeam | None
    team_b: LolTeam | None
    games: tuple[MatchGame, ...]


@dataclass(frozen=True)
class WindowBody:
    """Validated first livestats window."""

    esports_game_id: str
    frame_stamps: tuple[str, ...]
    patch_version: str | None
    blue_esports_team_id: str | None
    red_esports_team_id: str | None


@dataclass(frozen=True)
class LoadingAnchor:
    """Earliest rfc460Timestamp on the first livestats window."""

    stamp: str
    ts: int


@dataclass(frozen=True)
class GammaTimeRange:
    """Min/max non-null scheduled_ts across the universe."""

    min_ts: int
    max_ts: int


@dataclass(frozen=True)
class GamesBuild:
    """games.parquet rows plus completed schedule-match count."""

    rows: tuple[LolGameRow, ...]
    schedule_matches: int


def encode_json(value: object) -> str:
    """Stable JSON for cache files."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def write_gzip_json(path: Path, payload: object) -> None:
    """Atomically write one gzip JSON cache file with stable metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with (
        tmp.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped,
    ):
        zipped.write(encode_json(payload).encode("utf-8"))
    tmp.replace(path)


def write_json_partial(path: Path, payload: object) -> None:
    """Atomically write one JSON cache file through a .partial sibling."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(encode_json(payload), encoding="utf-8")
    tmp.replace(path)


def require_dict(value: object, endpoint: str) -> dict[str, object]:
    """Return a JSON object or abort on an incompatible schema."""
    if not isinstance(value, dict):
        raise RuntimeError(f"incompatible {endpoint} schema")
    return cast(dict[str, object], value)


def require_list(value: object, endpoint: str) -> list[object]:
    """Return a JSON list or abort on an incompatible schema."""
    if not isinstance(value, list):
        raise RuntimeError(f"incompatible {endpoint} schema")
    return cast(list[object], value)


def require_string(value: object, endpoint: str) -> str:
    """Return a nonempty string or abort on an incompatible schema."""
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"incompatible {endpoint} schema")
    return value


def optional_page_token(value: object) -> str | None:
    """Read pages.older or pages.newer when it is a non-empty string."""
    if isinstance(value, str) and value:
        return value
    return None


def parse_schedule_pages(value: object) -> SchedulePages:
    """Parse getSchedule pages.older / pages.newer."""
    pages = require_dict(value, "getSchedule")
    return SchedulePages(
        optional_page_token(pages.get("older")), optional_page_token(pages.get("newer"))
    )


def parse_schedule_event(raw: object) -> ScheduleEvent | None:
    """Parse one getSchedule event, or skip a non-object item."""
    if not isinstance(raw, dict):
        return None
    event = cast(dict[str, object], raw)
    match_id: str | None = None
    match = event.get("match")
    if isinstance(match, dict):
        raw_id = cast(dict[str, object], match).get("id")
        if isinstance(raw_id, str) and raw_id:
            match_id = raw_id
    type_raw = event.get("type")
    state_raw = event.get("state")
    start_raw = event.get("startTime")
    return ScheduleEvent(
        event_type=type_raw if isinstance(type_raw, str) else None,
        state=state_raw if isinstance(state_raw, str) else None,
        start_time=start_raw if isinstance(start_raw, str) else None,
        match_id=match_id,
    )


def validate_schedule_body(body: object) -> ScheduleBody:
    """Require getSchedule `data.schedule.events` list and `pages` object."""
    root = require_dict(body, "getSchedule")
    data = require_dict(root.get("data"), "getSchedule")
    schedule = require_dict(data.get("schedule"), "getSchedule")
    events_raw = require_list(schedule.get("events"), "getSchedule")
    pages = parse_schedule_pages(schedule.get("pages"))
    events = tuple(event for raw in events_raw if (event := parse_schedule_event(raw)) is not None)
    return ScheduleBody(events, pages, root)


def read_team_id(raw: object) -> str | None:
    """Return esportsTeamId, or live id, from one team object."""
    if not isinstance(raw, dict):
        return None
    item = cast(dict[str, object], raw)
    for key in ("esportsTeamId", "id"):
        team_id = item.get(key)
        if isinstance(team_id, str) and team_id:
            return team_id
    return None


def read_side_id(teams: Sequence[object], side: str) -> str | None:
    """Return the blue/red team id (`esportsTeamId` or live `id`)."""
    for raw in teams:
        if not isinstance(raw, dict):
            continue
        item = cast(dict[str, object], raw)
        if item.get("side") != side:
            continue
        team_id = read_team_id(item)
        if team_id is not None:
            return team_id
    return None


def read_window_side_id(metadata: object, key: str) -> str | None:
    """Return blue/red team id from window gameMetadata side object."""
    if not isinstance(metadata, dict):
        return None
    return read_team_id(cast(dict[str, object], metadata).get(key))


def read_match_team(value: object) -> LolTeam | None:
    """Parse one getEventDetails series team, or None when id/name are missing."""
    if not isinstance(value, dict):
        return None
    team = cast(dict[str, object], value)
    team_id = team.get("id")
    name = team.get("name")
    if not isinstance(team_id, str) or not isinstance(name, str):
        return None
    code_raw = team.get("code")
    code = code_raw if isinstance(code_raw, str) else None
    result = team.get("result")
    wins: int | None = None
    if isinstance(result, dict):
        raw_wins = cast(dict[str, object], result).get("gameWins")
        if isinstance(raw_wins, bool):
            wins = None
        elif isinstance(raw_wins, int):
            wins = raw_wins
        elif isinstance(raw_wins, float):
            wins = int(raw_wins)
    return LolTeam(team_id, name, code, wins)


def parse_match_game(raw: object) -> MatchGame | None:
    """Parse one games[] entry that has a nonempty id."""
    if not isinstance(raw, dict):
        return None
    game = cast(dict[str, object], raw)
    game_id = game.get("id")
    if not isinstance(game_id, str) or not game_id:
        return None
    number = game.get("number")
    game_number = number if isinstance(number, int) and not isinstance(number, bool) else None
    state_raw = game.get("state")
    state = state_raw if isinstance(state_raw, str) else None
    teams = game.get("teams")
    blue_id: str | None = None
    red_id: str | None = None
    if isinstance(teams, list):
        side_teams = cast(list[object], teams)
        blue_id = read_side_id(side_teams, "blue")
        red_id = read_side_id(side_teams, "red")
    return MatchGame(game_id, game_number, state, blue_id, red_id)


def read_league_slug(value: object) -> str | None:
    """Read league.slug when present."""
    if not isinstance(value, dict):
        return None
    slug_raw = cast(dict[str, object], value).get("slug")
    return slug_raw if isinstance(slug_raw, str) else None


def read_league_name(value: object) -> str | None:
    """Read league.name when present."""
    if not isinstance(value, dict):
        return None
    name_raw = cast(dict[str, object], value).get("name")
    return name_raw if isinstance(name_raw, str) else None


def parse_best_of(value: object) -> int | None:
    """Read match.strategy.count when it is a non-bool int."""
    if not isinstance(value, dict):
        return None
    count = cast(dict[str, object], value).get("count")
    if isinstance(count, int) and not isinstance(count, bool):
        return count
    return None


def event_match_id(event: dict[str, object], match: dict[str, object]) -> str:
    """Prefer match.id; live getEventDetails only has event.id."""
    raw_id = match.get("id")
    if isinstance(raw_id, str) and raw_id:
        return raw_id
    return require_string(event.get("id"), "getEventDetails")


def validate_event_details(body: object) -> EventDetails:
    """Require getEventDetails match.id or event.id, teams list, and games list."""
    root = require_dict(body, "getEventDetails")
    data = require_dict(root.get("data"), "getEventDetails")
    event = require_dict(data.get("event"), "getEventDetails")
    match = require_dict(event.get("match"), "getEventDetails")
    match_id = event_match_id(event, match)
    teams_raw = require_list(match.get("teams"), "getEventDetails")
    games_raw = require_list(match.get("games"), "getEventDetails")
    team_a = read_match_team(teams_raw[0]) if teams_raw else None
    team_b = read_match_team(teams_raw[1]) if len(teams_raw) > 1 else None
    games = tuple(game for raw in games_raw if (game := parse_match_game(raw)) is not None)
    start_raw = event.get("startTime")
    start_time = start_raw if isinstance(start_raw, str) else None
    league = event.get("league")
    return EventDetails(
        match_id=match_id,
        start_time=start_time,
        league_slug=read_league_slug(league),
        league_name=read_league_name(league),
        best_of=parse_best_of(match.get("strategy")),
        team_a=team_a,
        team_b=team_b,
        games=games,
    )


def validate_window_body(body: object) -> WindowBody:
    """Require a livestats window with string esportsGameId and a frames list."""
    root = require_dict(body, "window")
    game_id = require_string(root.get("esportsGameId"), "window")
    frames = require_list(root.get("frames"), "window")
    stamps: list[str] = []
    for raw in frames:
        if not isinstance(raw, dict):
            continue
        stamp = cast(dict[str, object], raw).get("rfc460Timestamp")
        if isinstance(stamp, str) and stamp:
            stamps.append(stamp)
    metadata = root.get("gameMetadata")
    patch_version: str | None = None
    blue_id = read_window_side_id(metadata, "blueTeamMetadata")
    red_id = read_window_side_id(metadata, "redTeamMetadata")
    if isinstance(metadata, dict):
        patch_raw = cast(dict[str, object], metadata).get("patchVersion")
        patch_version = patch_raw if isinstance(patch_raw, str) else None
    return WindowBody(game_id, tuple(stamps), patch_version, blue_id, red_id)


def http_get(
    client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
) -> httpx.Response:
    """Issue one HTTP GET. Tests monkeypatch this to avoid the network."""
    return client.get(url, headers=headers, params=params)


@retry(
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(RetryableHttpError),
    reraise=True,
)
def fetch_json(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    endpoint: str,
) -> object | None:
    """GET JSON; abort on 401/403; None on 404; empty 200 raises; retry 429/5xx/transport."""
    try:
        response = http_get(client, url, headers, params)
    except httpx.TransportError as error:
        raise RetryableHttpError(str(error)) from error
    if response.status_code in (401, 403):
        raise RuntimeError(f"lolesports HTTP {response.status_code} for {url}")
    if response.status_code == 404:
        return None
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableHttpError(f"HTTP {response.status_code} for {url}")
    if response.status_code >= 400:
        raise RuntimeError(f"lolesports HTTP {response.status_code} for {url}")
    if not response.content.strip():
        raise EmptyHttpBody(f"empty body for {url}")
    try:
        return response.json()
    except ValueError as error:
        raise RuntimeError(f"incompatible {endpoint} schema") from error


def fetch_esports_json(
    client: httpx.Client, endpoint: str, params: dict[str, str]
) -> object | None:
    """GET one esports-api path with hl=en-US and the public frontend API key."""
    url = f"{LOLESPORTS_ESPORTS_API}/{endpoint}"
    query = {"hl": "en-US", **params}
    return fetch_json(client, url, {"x-api-key": LOLESPORTS_API_KEY}, query, endpoint)


def fetch_window_json(client: httpx.Client, esports_game_id: str) -> object | None:
    """GET the first livestats window for one game. No API key."""
    url = f"{LOLESPORTS_WINDOW_API}/{esports_game_id}"
    return fetch_json(client, url, {}, {}, "window")


def fetch_window_at(
    client: httpx.Client, esports_game_id: str, starting_time: str
) -> object | None:
    """GET one livestats window at startingTime. No API key."""
    url = f"{LOLESPORTS_WINDOW_API}/{esports_game_id}"
    return fetch_json(client, url, {}, {"startingTime": starting_time}, "window")


def fetch_details_at(
    client: httpx.Client, esports_game_id: str, starting_time: str
) -> object | None:
    """GET one livestats details page at startingTime. No API key."""
    url = f"{LOLESPORTS_DETAILS_API}/{esports_game_id}"
    return fetch_json(client, url, {}, {"startingTime": starting_time}, "details")


def loading_anchor_from_stamps(stamps: Sequence[str]) -> LoadingAnchor | None:
    """Return the earliest rfc460Timestamp and its unix seconds."""
    best_stamp: str | None = None
    best_ts: int | None = None
    for stamp in stamps:
        ts = parse_ts(stamp)
        if ts is None:
            continue
        if best_ts is None or ts < best_ts:
            best_ts = ts
            best_stamp = stamp
    if best_stamp is None or best_ts is None:
        return None
    return LoadingAnchor(best_stamp, best_ts)


def game_row_from_parsed(details: EventDetails, window_body: WindowBody) -> LolGameRow | None:
    """Build one games.parquet row from already-validated event details and window."""
    if details.team_a is None or details.team_b is None:
        return None
    chosen: MatchGame | None = None
    for game in details.games:
        if game.game_id == window_body.esports_game_id:
            chosen = game
            break
    if chosen is None or chosen.state != "completed" or chosen.number is None:
        return None
    blue_id = window_body.blue_esports_team_id
    red_id = window_body.red_esports_team_id
    if blue_id is None or red_id is None or blue_id == red_id:
        return None
    anchor = loading_anchor_from_stamps(window_body.frame_stamps)
    if anchor is None:
        return None
    return {
        "esports_game_id": window_body.esports_game_id,
        "esports_match_id": details.match_id,
        "league_slug": details.league_slug,
        "league_name": details.league_name,
        "start_time": details.start_time,
        "start_ts": parse_ts(details.start_time),
        "best_of": details.best_of,
        "game_number": chosen.number,
        "team_a_id": details.team_a.team_id,
        "team_a_name": details.team_a.name,
        "team_a_code": details.team_a.code,
        "team_b_id": details.team_b.team_id,
        "team_b_name": details.team_b.name,
        "team_b_code": details.team_b.code,
        "team_a_game_wins": details.team_a.game_wins,
        "team_b_game_wins": details.team_b.game_wins,
        "blue_esports_team_id": blue_id,
        "red_esports_team_id": red_id,
        "loading_anchor": anchor.stamp,
        "loading_anchor_ts": anchor.ts,
        "patch_version": window_body.patch_version,
    }


def build_game_row(event_details: object, window: object) -> LolGameRow | None:
    """Build one games.parquet row from getEventDetails plus a first window."""
    return game_row_from_parsed(validate_event_details(event_details), validate_window_body(window))


def newest_start_ts(events: Sequence[ScheduleEvent]) -> int | None:
    """Newest startTime on one schedule page."""
    times = [ts for event in events if (ts := parse_ts(event.start_time)) is not None]
    if not times:
        return None
    return max(times)


def oldest_start_ts(events: Sequence[ScheduleEvent]) -> int | None:
    """Oldest startTime on one schedule page."""
    times = [ts for event in events if (ts := parse_ts(event.start_time)) is not None]
    if not times:
        return None
    return min(times)


def fetch_schedule_page(client: httpx.Client, page_token: str | None) -> ScheduleBody:
    """Fetch and validate one getSchedule page."""
    params: dict[str, str] = {}
    if page_token is not None:
        params["pageToken"] = page_token
    body = fetch_esports_json(client, "getSchedule", params)
    if body is None:
        raise RuntimeError("incompatible getSchedule schema")
    return validate_schedule_body(body)


def write_schedule_page(
    staging: Path, page_number: int, page_token: str | None, body: ScheduleBody
) -> None:
    """Cache one getSchedule envelope under the staging schedule directory."""
    envelope = {
        "endpoint": "getSchedule",
        "page_token": page_token,
        "pages": {"older": body.pages.older, "newer": body.pages.newer},
        "fetched_at": datetime.now(tz=UTC).isoformat(),
        "body": body.raw,
    }
    write_gzip_json(staging / "schedule" / f"page_{page_number:05d}.json.gz", envelope)


def fetch_schedule_pages(
    client: httpx.Client, staging: Path, gamma_min_ts: int, gamma_max_ts: int
) -> None:
    """Page getSchedule older/newer until the Gamma range plus four hours is covered."""
    pad = LOL_SERIES_START_WINDOW_SECONDS
    page_number = 0
    first_body = fetch_schedule_page(client, None)
    write_schedule_page(staging, page_number, None, first_body)
    first_oldest = oldest_start_ts(first_body.events)
    older_token = first_body.pages.older
    while older_token is not None:
        page_number += 1
        body = fetch_schedule_page(client, older_token)
        write_schedule_page(staging, page_number, older_token, body)
        newest = newest_start_ts(body.events)
        if newest is None or newest < gamma_min_ts - pad:
            break
        older_token = body.pages.older
    if first_oldest is None or first_oldest > gamma_max_ts + pad:
        return
    newer_token = first_body.pages.newer
    while newer_token is not None:
        page_number += 1
        body = fetch_schedule_page(client, newer_token)
        write_schedule_page(staging, page_number, newer_token, body)
        oldest = oldest_start_ts(body.events)
        if oldest is None or oldest > gamma_max_ts + pad:
            break
        newer_token = body.pages.newer


def refresh_schedule(
    client: httpx.Client, raw_dir: Path, gamma_min_ts: int, gamma_max_ts: int
) -> None:
    schedule_dir = raw_dir / "schedule"
    with staging_directory(raw_dir, ".schedule.tmp-") as temp_root:
        fetch_schedule_pages(client, temp_root, gamma_min_ts, gamma_max_ts)
        shutil.rmtree(schedule_dir, ignore_errors=True)
        (temp_root / "schedule").replace(schedule_dir)


def load_schedule_events(raw_dir: Path) -> list[ScheduleEvent]:
    paths = sorted((raw_dir / "schedule").glob("page_*.json.gz"))
    if not paths:
        raise FileNotFoundError(raw_dir / "schedule")
    events: list[ScheduleEvent] = []
    for path in paths:
        payload = read_gzip_json(path)
        body = validate_schedule_body(payload.get("body"))
        events.extend(body.events)
    return events


def completed_match_ids(events: Sequence[ScheduleEvent]) -> list[str]:
    """Dedup completed schedule matches by match.id, first-seen order."""
    ids: list[str] = []
    seen: set[str] = set()
    for event in events:
        if event.event_type != "match" or event.state != "completed":
            continue
        if event.match_id is None or event.match_id in seen:
            continue
        seen.add(event.match_id)
        ids.append(event.match_id)
    return ids


def completed_game_ids(details: EventDetails) -> list[str]:
    """Return ids of completed games on a validated getEventDetails body."""
    return [game.game_id for game in details.games if game.state == "completed"]


def event_cache_path(raw_dir: Path, match_id: str) -> Path:
    """Gzip path for one cached getEventDetails body."""
    return raw_dir / "events" / f"{match_id}.json.gz"


def anchor_cache_path(raw_dir: Path, game_id: str) -> Path:
    """JSON path for one cached first-window body."""
    return raw_dir / "anchors" / f"{game_id}.json"


def load_event_details(path: Path) -> EventDetails:
    """Read and validate one cached getEventDetails body."""
    return validate_event_details(read_gzip_json(path))


def load_window(path: Path) -> WindowBody:
    """Read and validate one cached first-window body."""
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    return validate_window_body(payload)


def ensure_event_details(
    client: httpx.Client | None, raw_dir: Path, match_id: str
) -> EventDetails | None:
    path = event_cache_path(raw_dir, match_id)
    if path.exists():
        return load_event_details(path)
    if client is None:
        raise FileNotFoundError(path)
    body = fetch_esports_json(client, "getEventDetails", {"id": match_id})
    if body is None:
        return None
    validated = validate_event_details(body)
    write_gzip_json(path, body)
    return validated


def ensure_window(client: httpx.Client | None, raw_dir: Path, game_id: str) -> WindowBody | None:
    path = anchor_cache_path(raw_dir, game_id)
    if path.exists():
        return load_window(path)
    if client is None:
        # ponytail: empty-body/404 skips never wrote anchors; replay must match --fetch.
        return None
    try:
        body = fetch_window_json(client, game_id)
    except EmptyHttpBody:
        return None
    if body is None:
        return None
    validated = validate_window_body(body)
    write_json_partial(path, body)
    return validated


def require_unique_strings(values: Sequence[str], label: str) -> None:
    """Abort when a primary-key column contains duplicates."""
    if len(values) != len(set(values)):
        raise RuntimeError(f"duplicate {label}")


def require_unique_links(links: Sequence[LolLinkRow]) -> None:
    """Abort on duplicate esports_game_id or (event_id, game_number)."""
    require_unique_strings([row["esports_game_id"] for row in links], "esports_game_id")
    pairs = [f"{row['event_id']}:{row['game_number']}" for row in links]
    require_unique_strings(pairs, "event_id/game_number")


def load_universe_rows(path: Path) -> list[LolUniverseMarketRow]:
    return cast(list[LolUniverseMarketRow], read_parquet_rows(path))


def universe_time_range(rows: Sequence[LolUniverseMarketRow]) -> GammaTimeRange:
    """Min/max non-null scheduled_ts across the universe."""
    timestamps = [ts for row in rows if (ts := optional_int(row["scheduled_ts"])) is not None]
    if not timestamps:
        raise RuntimeError("universe has no scheduled_ts values")
    return GammaTimeRange(min(timestamps), max(timestamps))


def schedule_start_times(events: Sequence[ScheduleEvent]) -> dict[str, str]:
    """First-seen schedule startTime per completed match.id."""
    starts: dict[str, str] = {}
    for event in events:
        if event.match_id is None or event.start_time is None:
            continue
        if event.match_id not in starts:
            starts[event.match_id] = event.start_time
    return starts


def fill_missing_start(row: LolGameRow, stamp: str | None) -> LolGameRow:
    """Copy schedule startTime onto a game row when getEventDetails omitted it."""
    if row["start_ts"] is not None or stamp is None:
        return row
    filled = dict(row)
    filled["start_time"] = stamp
    filled["start_ts"] = parse_ts(stamp)
    return cast(LolGameRow, filled)


def rows_for_match(
    client: httpx.Client | None,
    raw_dir: Path,
    match_id: str,
    starts: dict[str, str],
) -> list[LolGameRow]:
    details = ensure_event_details(client, raw_dir, match_id)
    if details is None:
        return []
    rows: list[LolGameRow] = []
    for game_id in completed_game_ids(details):
        window = ensure_window(client, raw_dir, game_id)
        if window is None:
            continue
        row = game_row_from_parsed(details, window)
        if row is None:
            continue
        rows.append(fill_missing_start(row, starts.get(details.match_id)))
    return rows


def build_games_from_cache(raw_dir: Path, client: httpx.Client | None) -> GamesBuild:
    events = load_schedule_events(raw_dir)
    match_ids = completed_match_ids(events)
    starts = schedule_start_times(events)
    buckets: list[list[LolGameRow]] = [[] for _ in match_ids]
    pool = ThreadPoolExecutor(max_workers=LOL_MAX_CONCURRENCY)
    try:
        futures = {
            pool.submit(rows_for_match, client, raw_dir, match_id, starts): index
            for index, match_id in enumerate(match_ids)
        }
        for future in as_completed(futures):
            buckets[futures[future]] = future.result()
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    rows = [row for bucket in buckets for row in bucket]
    return GamesBuild(tuple(rows), len(match_ids))


def print_link_totals(schedule_matches: int, game_count: int, result: LinkResult) -> None:
    """Print schedule/game/event/link totals and every audit reason."""
    print_count("schedule_matches", schedule_matches)
    print_count("games", game_count)
    event_count = sum(1 for row in result.audit if row["scope"] == "event")
    print_count("pm_events", event_count)
    print_count("accepted_links", len(result.links))
    totals: dict[str, int] = {}
    for row in result.audit:
        reason = row["reason"]
        totals[reason] = totals.get(reason, 0) + 1
    for reason, count in sorted(totals.items()):
        print_count(reason, count)


def link_lolesports(
    universe_path: Path, raw_dir: Path, games_path: Path, links_dir: Path, fetch: bool
) -> None:
    """Optionally fetch lolesports, then write games, links, and audit parquets."""
    universe_rows = load_universe_rows(universe_path)
    gamma_range = universe_time_range(universe_rows)
    if fetch:
        with http_client() as client:
            refresh_schedule(client, raw_dir, gamma_range.min_ts, gamma_range.max_ts)
            built = build_games_from_cache(raw_dir, client)
    else:
        built = build_games_from_cache(raw_dir, None)
    game_rows = list(built.rows)
    require_unique_strings([row["esports_game_id"] for row in game_rows], "esports_game_id")
    write_parquet_rows(
        game_rows,
        games_path,
        GAMES_COLUMNS,
        GAMES_INTEGER_COLUMNS,
        ["start_ts", "esports_match_id", "game_number"],
    )
    result = link_events(universe_rows, game_rows)
    require_unique_links(result.links)
    write_parquet_rows(
        result.links,
        links_dir / LOL_LINKS_PATH.name,
        LINKS_COLUMNS,
        LINKS_INTEGER_COLUMNS,
        ["loading_anchor_ts", "event_id", "game_number"],
    )
    write_parquet_rows(
        result.audit,
        links_dir / LOL_LINK_AUDIT_PATH.name,
        AUDIT_COLUMNS,
        AUDIT_INTEGER_COLUMNS,
        ["event_id", "scope", "game_number"],
    )
    print_link_totals(built.schedule_matches, len(game_rows), result)


def main(
    fetch: Annotated[bool, typer.Option("--fetch")] = False,
    universe_path: Annotated[Path, typer.Option("--universe-path")] = LOL_UNIVERSE_PATH,
    raw_dir: Annotated[Path, typer.Option("--raw-dir")] = LOL_RAW_LOLESPORTS_DIR,
    games_path: Annotated[Path, typer.Option("--games-path")] = LOL_GAMES_PATH,
    links_dir: Annotated[Path, typer.Option("--links-dir")] = LOL_LINKS_DIR,
) -> None:
    """Download lolesports if requested, then write games/links/audit parquets."""
    link_lolesports(universe_path, raw_dir, games_path, links_dir, fetch)


if __name__ == "__main__":
    typer.run(main)
