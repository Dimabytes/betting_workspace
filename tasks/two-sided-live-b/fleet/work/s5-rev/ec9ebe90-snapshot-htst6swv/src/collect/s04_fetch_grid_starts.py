import re
import sys
import time
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, NotRequired, TypedDict, cast

import httpx
import pandas as pd
import typer
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect.common.catalog_types import GridGameWindowRow, MatchLinkRow
from collect.common.paths import GRID_GAME_WINDOWS_PATH, MATCH_LINKS_PATH
from collect.common.timestamps import require_ts
from shared.constants import api
from shared.constants.paths import RAW_POLYMARKET_DOTA_DIR
from shared.utils.environment import env_value
from shared.utils.json_io import read_json, write_json
from shared.utils.log import print_count
from shared.utils.parquet_io import write_parquet
from shared.utils.parsing import opt_int, parse_ts

GRID_CENTRAL_DATA_API = "https://api-op.grid.gg/central-data/graphql"
GRID_SERIES_STATE_API = "https://api-op.grid.gg/live-data-feed/series-state/graphql"
GRID_DOTA_TITLE_ID = "2"

GRID_STARTS_DIR = RAW_POLYMARKET_DOTA_DIR / "grid_game_starts"
SERIES_INDEX_DIR = GRID_STARTS_DIR / "series_index"
SERIES_STATE_DIR = GRID_STARTS_DIR / "series_state"

GRID_REQUEST_SLEEP_SECONDS = 3.2
INDEX_BUFFER_DAYS = 2

MAX_MAP_LOAD_AFTER_MATCH_START_SECONDS = 1800

SERIES_FETCH_WINDOW_SECONDS = 8 * 60 * 60

GRID_GAME_WINDOW_COLUMNS: list[str] = list(GridGameWindowRow.__annotations__)


def slug(value: str, max_len: int = 120) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_")
    return value[:max_len] or "unknown"


def iso_duration_seconds(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?",
        value.strip(),
    )
    if not match:
        return None
    hours, minutes, seconds = (float(part) if part else 0.0 for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


class GridSeriesFormat(TypedDict):
    name: str
    nameShortened: str


class GridTournament(TypedDict):
    name: str


class GridTeamBaseInfo(TypedDict):
    id: str
    name: str


class GridSeriesTeamRef(TypedDict):
    baseInfo: GridTeamBaseInfo


class GridSeriesNode(TypedDict):
    id: str
    startTimeScheduled: str
    format: GridSeriesFormat
    tournament: GridTournament
    teams: list[GridSeriesTeamRef]


class GridSeriesEdge(TypedDict):
    node: GridSeriesNode


class GridPageInfo(TypedDict):
    hasNextPage: bool
    endCursor: str


class GridAllSeries(TypedDict):
    pageInfo: GridPageInfo
    edges: list[GridSeriesEdge]


class GridAllSeriesData(TypedDict):
    allSeries: GridAllSeries


class GridAllSeriesResponse(TypedDict):
    data: GridAllSeriesData


class GridClock(TypedDict):
    type: str
    ticking: bool
    ticksBackwards: bool
    currentSeconds: int


class GridGameTeam(TypedDict):
    id: str
    name: str
    side: Literal["radiant", "dire"]
    won: bool
    score: int
    kills: int


class GridGameNode(TypedDict):
    id: str
    sequenceNumber: int
    started: bool
    finished: bool
    paused: bool
    startedAt: NotRequired[str]
    duration: str
    clock: GridClock
    teams: list[GridGameTeam]


class GridSeriesTeam(TypedDict):
    id: str
    name: str
    score: int
    won: bool


class GridSeriesState(TypedDict):
    id: str
    started: bool
    finished: bool
    startedAt: NotRequired[str]
    updatedAt: str
    duration: str
    format: str
    teams: list[GridSeriesTeam]
    games: list[GridGameNode]


class GridSeriesStateData(TypedDict):
    seriesState: NotRequired[GridSeriesState | None]


class GridSeriesStateResponse(TypedDict):
    data: NotRequired[GridSeriesStateData]
    error: NotRequired[str]


class GridRateLimitExtensions(TypedDict):
    errorType: NotRequired[str]
    rateLimitResetsIn: NotRequired[str]


class GridGraphQLError(TypedDict):
    message: NotRequired[str]
    extensions: NotRequired[GridRateLimitExtensions]


@dataclass(frozen=True)
class IndexedSeries:
    id: str
    start_ts: int


@dataclass(frozen=True)
class GridGame:
    game_id: str
    spawn_at: str
    spawn_ts: int
    clock_seconds: int


@dataclass(frozen=True)
class LinkedMap:
    condition_id: str
    match_id: int
    match_start_time: int
    grid_clock_seconds: int


@dataclass(frozen=True)
class IndexWindow:
    start_ts: int
    end_ts: int


def grid_token() -> str:
    token = env_value("GRID_GG_TOKEN")
    if not token:
        raise SystemExit("GRID_GG_TOKEN not set (.env). Cannot query GRID.")
    return token


@retry(wait=wait_exponential(multiplier=1, min=2, max=60), stop=stop_after_attempt(5))
def grid_graphql(
    token: str, url: str, query: str, variables: dict[str, object]
) -> dict[str, object]:
    headers = {
        "x-api-key": token,
        "User-Agent": "dota-2-model/0.1 research pipeline",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=api.HTTP_TIMEOUT_SECONDS, headers=headers) as client:
        response = client.post(url, json={"query": query, "variables": variables})
    if response.status_code == 429:
        time.sleep(int(response.headers.get("retry-after", "10")))
        response.raise_for_status()
    response.raise_for_status()
    payload = cast(dict[str, object], response.json())
    errors = payload.get("errors")
    if not errors:
        return payload
    for error in cast(list[GridGraphQLError], errors):
        extensions: GridRateLimitExtensions = error.get("extensions") or {}
        reset_seconds = iso_duration_seconds(extensions.get("rateLimitResetsIn"))
        rate_limited = (
            extensions.get("errorType") == "UNAVAILABLE"
            and "rate limit" in error.get("message", "").lower()
        )
        if rate_limited:
            time.sleep(reset_seconds + 1 if reset_seconds is not None else 10)
    raise RuntimeError(f"GRID GraphQL errors: {errors}")


ALL_SERIES_QUERY = """
query AllSeries($first: Int!, $after: String, $filter: SeriesFilter) {
  allSeries(first: $first, after: $after, filter: $filter,
            orderBy: StartTimeScheduled, orderDirection: ASC) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id
      startTimeScheduled
      format { name nameShortened }
      tournament { name }
      teams { baseInfo { id name } }
    } }
  }
}
"""


SERIES_STATE_QUERY = """
query SeriesState($id: ID!) {
  seriesState(id: $id) {
    id started finished startedAt updatedAt duration format
    teams { id name score won }
    games {
      id sequenceNumber started finished paused startedAt duration
      clock { type ticking ticksBackwards currentSeconds }
      teams { id name side won score kills }
    }
  }
}
"""


def load_linked_maps() -> dict[str, LinkedMap]:
    """Match links with OpenDota timing; archive-only rows have none to match on."""
    frame = pd.read_parquet(MATCH_LINKS_PATH)
    links = cast(list[MatchLinkRow], frame.to_dict(orient="records"))
    linked: dict[str, LinkedMap] = {}
    skipped = 0
    for link in links:
        match_id = opt_int(link["match_id"])
        match_start_time = opt_int(link["match_start_time"])
        grid_clock_seconds = opt_int(link["grid_clock_seconds"])
        if match_id is None or match_start_time is None or grid_clock_seconds is None:
            skipped += 1
            continue
        linked[link["map_condition_id"]] = LinkedMap(
            condition_id=link["map_condition_id"],
            match_id=match_id,
            match_start_time=match_start_time,
            grid_clock_seconds=grid_clock_seconds,
        )
    print_count("skipped_links_without_grid_clock", skipped)
    return linked


def index_window(linked: Mapping[str, LinkedMap]) -> IndexWindow:
    match_starts = [link.match_start_time for link in linked.values()]
    buffer_seconds = INDEX_BUFFER_DAYS * 24 * 60 * 60
    return IndexWindow(
        start_ts=min(match_starts) - buffer_seconds,
        end_ts=max(match_starts) + buffer_seconds,
    )


def index_page_path(window: IndexWindow, page: int) -> Path:
    name = f"series_index_{window.start_ts}_{window.end_ts}_p{page:03d}.json"
    return SERIES_INDEX_DIR / name


def fetch_series_index(token: str, window: IndexWindow, force: bool) -> None:
    gte = datetime.fromtimestamp(window.start_ts, tz=UTC).isoformat().replace("+00:00", "Z")
    lte = datetime.fromtimestamp(window.end_ts, tz=UTC).isoformat().replace("+00:00", "Z")
    series_filter = {
        "titleId": GRID_DOTA_TITLE_ID,
        "startTimeScheduled": {"gte": gte, "lte": lte},
        "workflowStatuses": ["PUBLISHED"],
    }
    after: str | None = None
    page = 0
    while True:
        out = index_page_path(window, page)
        if out.exists() and not force:
            payload = cast(GridAllSeriesResponse, read_json(out))
        else:
            response = grid_graphql(
                token,
                GRID_CENTRAL_DATA_API,
                ALL_SERIES_QUERY,
                {"first": 50, "after": after, "filter": series_filter},
            )
            payload = cast(GridAllSeriesResponse, response)
            write_json(out, payload)
            print(f"saved {out.name}", flush=True)
            time.sleep(GRID_REQUEST_SLEEP_SECONDS)
        info = payload["data"]["allSeries"]["pageInfo"]
        after = info["endCursor"]
        page += 1
        if not info["hasNextPage"] or not after:
            break


def load_series_index(window: IndexWindow) -> dict[str, IndexedSeries]:
    series: dict[str, IndexedSeries] = {}
    page = 0
    while True:
        path = index_page_path(window, page)
        payload = cast(GridAllSeriesResponse, read_json(path))
        all_series = payload["data"]["allSeries"]
        for edge in all_series["edges"]:
            node = edge["node"]
            series[node["id"]] = IndexedSeries(
                id=node["id"],
                start_ts=require_ts(node["startTimeScheduled"], "startTimeScheduled"),
            )
        page_info = all_series["pageInfo"]
        page += 1
        if not page_info["hasNextPage"] or not page_info["endCursor"]:
            break
    return series


def series_ids_near_links(
    series_by_id: Mapping[str, IndexedSeries], linked: Mapping[str, LinkedMap]
) -> list[str]:
    starts = sorted(link.match_start_time for link in linked.values())
    near: list[str] = []
    for series_id, series in series_by_id.items():
        first_start = bisect_left(starts, series.start_ts - SERIES_FETCH_WINDOW_SECONDS)
        if first_start < len(starts) and starts[first_start] <= (
            series.start_ts + SERIES_FETCH_WINDOW_SECONDS
        ):
            near.append(series_id)
    return sorted(near)


def state_path(series_id: str) -> Path:
    return SERIES_STATE_DIR / f"{slug(series_id)}.json"


def pending_state_ids(series_ids: list[str]) -> list[str]:
    return [series_id for series_id in series_ids if not state_path(series_id).exists()]


def fetch_series_states(token: str, series_ids: list[str]) -> None:
    total = len(series_ids)
    for position, series_id in enumerate(series_ids, start=1):
        response = grid_graphql(token, GRID_SERIES_STATE_API, SERIES_STATE_QUERY, {"id": series_id})
        payload = cast(GridSeriesStateResponse, response)
        data = payload.get("data")
        if payload.get("error") or data is None or data.get("seriesState") is None:
            print(f"skip series_state {series_id} ({position}/{total})", flush=True)
            continue
        out = state_path(series_id)
        write_json(out, payload)
        print(f"saved series_state/{out.name} ({position}/{total})", flush=True)
        time.sleep(GRID_REQUEST_SLEEP_SECONDS)


def load_grid_games(series_ids: list[str]) -> list[GridGame]:
    games: list[GridGame] = []
    for series_id in series_ids:
        path = state_path(series_id)
        if not path.exists():
            continue
        payload = cast(GridSeriesStateResponse, read_json(path))
        data = payload.get("data")
        state = None if data is None else data.get("seriesState")
        if state is None:
            continue
        for game in state["games"]:
            spawn_at = game.get("startedAt")
            spawn_ts = parse_ts(spawn_at)
            if spawn_at is None or spawn_ts is None:
                continue
            games.append(
                GridGame(
                    game_id=game["id"],
                    spawn_at=spawn_at,
                    spawn_ts=spawn_ts,
                    clock_seconds=game["clock"]["currentSeconds"],
                )
            )
    return games


def index_games_by_clock(games: list[GridGame]) -> dict[int, list[GridGame]]:
    by_clock: dict[int, list[GridGame]] = defaultdict(list)
    for game in games:
        by_clock[game.clock_seconds].append(game)
    return by_clock


def match_game(link: LinkedMap, games_by_clock: Mapping[int, list[GridGame]]) -> GridGame | None:
    candidates = [
        game
        for game in games_by_clock.get(link.grid_clock_seconds, [])
        if 0 <= game.spawn_ts - link.match_start_time <= MAX_MAP_LOAD_AFTER_MATCH_START_SECONDS
    ]
    if len(candidates) > 1:
        game_ids = [game.game_id for game in candidates]
        raise ValueError(
            f"multiple exact GRID games for {link.condition_id} / {link.match_id}: {game_ids}"
        )
    return candidates[0] if candidates else None


def resolve_window(
    link: LinkedMap,
    games_by_clock: Mapping[int, list[GridGame]],
) -> GridGameWindowRow | None:
    """The GRID window for one link, or None — windows mean `startedAt` only."""
    game = match_game(link, games_by_clock)
    if game is None:
        return None
    return {"condition_id": link.condition_id, "spawn_at": game.spawn_at}


def main(
    fetch: Annotated[
        bool, typer.Option("--fetch", help="Fetch the current GRID index and states.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Ignore cached index pages and selected series states.")
    ] = False,
) -> None:
    for directory in (SERIES_INDEX_DIR, SERIES_STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    linked = load_linked_maps()
    if not linked:
        frame = pd.DataFrame(columns=GRID_GAME_WINDOW_COLUMNS)
        write_parquet(frame, GRID_GAME_WINDOWS_PATH)
        print_count("linked_map_markets", 0)
        print(f"saved: {GRID_GAME_WINDOWS_PATH}")
        return
    window = index_window(linked)

    token = grid_token() if fetch else ""
    if fetch:
        fetch_series_index(token, window, force=force)

    series_by_id = load_series_index(window)
    needed = series_ids_near_links(series_by_id, linked)
    if fetch:
        targets = needed if force else pending_state_ids(needed)
        print_count("grid_series_states_to_fetch", len(targets))
        fetch_series_states(token, targets)

    games = load_grid_games(needed)
    games_by_clock = index_games_by_clock(games)
    rows: list[GridGameWindowRow] = []
    for link in linked.values():
        row = resolve_window(link, games_by_clock)
        if row is not None:
            rows.append(row)

    frame = pd.DataFrame(rows, columns=GRID_GAME_WINDOW_COLUMNS)
    write_parquet(frame, GRID_GAME_WINDOWS_PATH)
    print_count("linked_map_markets", len(linked))
    print_count("grid_started_games", len(games))
    print_count("matched_grid_windows", len(rows))
    print_count("unmatched_links", len(linked) - len(rows))
    print(f"saved: {GRID_GAME_WINDOWS_PATH}")


if __name__ == "__main__":
    app = typer.Typer()
    app.command(help="Resolve and publish GRID windows for OpenDota-linked map markets.")(main)
    app()
