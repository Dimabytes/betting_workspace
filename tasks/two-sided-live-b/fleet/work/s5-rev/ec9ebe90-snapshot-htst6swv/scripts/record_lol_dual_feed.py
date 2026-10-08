"""Record one live LoL map from the GRID widget socket and lolesports livestats at once.

Both writers stamp every record with the wall time it arrived, so the offline
comparator can align the two clocks. Stop with Ctrl-C; the map ending also
stops the run.

    make run F=scripts/record_lol_dual_feed.py                       # list live candidates
    make run F=scripts/record_lol_dual_feed.py ARGS="lol-navi-gx-2026-08-29"
    make run F=scripts/record_lol_dual_feed.py ARGS="lol-navi-gx-2026-08-29 --esports-game-id 1155..."
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO, cast

import httpx
import websockets

from shared.constants.paths import DATA_DIR
from shared.utils.http import http_client
from shared.utils.log import get_logger, setup_logging
from trader.grid_widgets import GRID_WIDGETS_ORIGIN, build_socket_url

logger = get_logger(__name__)

LOL_GAMMA_TAG_ID = 65
GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
LOLESPORTS_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
LOLESPORTS_API = "https://esports-api.lolesports.com/persisted/gw"
LIVESTATS_FEED = "https://feed.lolesports.com/livestats/v1"
POLL_SECONDS = 10.0
WINDOW_BACK_SECONDS = 75
RECORD_ROOT = DATA_DIR / "lol_dual_feed"


@dataclass(frozen=True)
class LiveTarget:
    """One live map recorded from both sources."""

    slug: str
    title: str
    series_id: str
    esports_game_id: str


def stamp_now() -> str:
    """Current UTC time as ISO-8601 with a Z suffix."""
    return datetime.now(tz=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def aligned_starting_time(back_seconds: int) -> str:
    """A 10-second-aligned livestats window start that the feed accepts.

    The feed rejects any window younger than about 60 seconds with 400.
    """
    moment = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(seconds=back_seconds)
    moment = moment - timedelta(seconds=moment.second % 10)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def lolesports_live(client: httpx.Client) -> list[dict[str, object]]:
    """Fetch the lolesports live schedule and return its event objects."""
    response = client.get(
        f"{LOLESPORTS_API}/getLive",
        params={"hl": "en-US"},
        headers={"x-api-key": LOLESPORTS_KEY, "Accept": "application/json"},
        timeout=20.0,
    )
    response.raise_for_status()
    body = cast(dict[str, object], response.json())
    data = cast(dict[str, object], body.get("data") or {})
    schedule = cast(dict[str, object], data.get("schedule") or {})
    events = schedule.get("events")
    if not isinstance(events, list):
        return []
    return [item for item in cast(list[object], events) if isinstance(item, dict)]


def in_progress_games(event: dict[str, object]) -> list[tuple[str, str]]:
    """Return (game id, team codes) for every in-progress game of one lolesports event."""
    match = cast(dict[str, object], event.get("match") or {})
    teams_raw = match.get("teams")
    codes: list[str] = []
    if isinstance(teams_raw, list):
        for item in cast(list[object], teams_raw):
            if isinstance(item, dict):
                team = cast(dict[str, object], item)
                codes.append(str(team.get("code") or team.get("name") or "?"))
    games_raw = match.get("games")
    rows: list[tuple[str, str]] = []
    if isinstance(games_raw, list):
        for item in cast(list[object], games_raw):
            if not isinstance(item, dict):
                continue
            game = cast(dict[str, object], item)
            if game.get("state") != "inProgress":
                continue
            rows.append((str(game.get("id")), " vs ".join(codes)))
    return rows


def list_candidates() -> None:
    """Print the live Polymarket LoL events with a GRID id, and the live lolesports games."""
    with http_client() as client:
        response = client.get(
            GAMMA_EVENTS,
            params={
                "tag_id": LOL_GAMMA_TAG_ID,
                "closed": "false",
                "limit": 100,
                "order": "startDate",
                "ascending": "false",
            },
        )
        response.raise_for_status()
        events = cast(list[object], response.json())
        logger.info("live Polymarket LoL events:")
        for item in events:
            if not isinstance(item, dict):
                continue
            event = cast(dict[str, object], item)
            if not event.get("live"):
                continue
            metadata = cast(dict[str, object], event.get("eventMetadata") or {})
            logger.info(
                "  grid=%-10s %-30s %s",
                metadata.get("gridSeriesId") or "-",
                event.get("slug"),
                str(event.get("title"))[:60],
            )
        logger.info("live lolesports games:")
        for event in lolesports_live(client):
            league = cast(dict[str, object], event.get("league") or {})
            for game_id, codes in in_progress_games(event):
                logger.info("  game=%s %-12s %s", game_id, league.get("name"), codes)


def resolve_series_id(client: httpx.Client, slug: str) -> tuple[str, str]:
    """Return the GRID series id and title of one Polymarket event slug."""
    response = client.get(GAMMA_EVENTS, params={"slug": slug})
    response.raise_for_status()
    payload = cast(list[object], response.json())
    if not payload or not isinstance(payload[0], dict):
        raise SystemExit(f"gamma knows no event with slug {slug!r}")
    event = cast(dict[str, object], payload[0])
    metadata = cast(dict[str, object], event.get("eventMetadata") or {})
    series_id = metadata.get("gridSeriesId")
    if not isinstance(series_id, str) or not series_id:
        raise SystemExit(f"{slug} has no eventMetadata.gridSeriesId")
    return series_id, str(event.get("title") or slug)


def slug_tokens(slug: str) -> set[str]:
    """Lowercase dash-separated pieces of a Polymarket event slug."""
    return {piece for piece in slug.lower().split("-") if piece}


def pick_esports_game(client: httpx.Client, slug: str) -> str:
    """Pick the in-progress lolesports game whose team codes appear in the PM slug."""
    rows: list[tuple[str, str]] = []
    for event in lolesports_live(client):
        rows.extend(in_progress_games(event))
    tokens = slug_tokens(slug)
    matched = [
        row for row in rows if all(code.strip().lower() in tokens for code in row[1].split(" vs "))
    ]
    if len(matched) == 1:
        logger.info("matched lolesports game %s (%s)", matched[0][0], matched[0][1])
        return matched[0][0]
    if len(rows) == 1 and not matched:
        logger.info("one live lolesports game: %s (%s)", rows[0][0], rows[0][1])
        return rows[0][0]
    for game_id, codes in rows:
        logger.info("  candidate game=%s %s", game_id, codes)
    raise SystemExit(
        f"{len(matched)} of {len(rows)} live games match {slug!r}: pass --esports-game-id"
    )


async def record_grid(series_id: str, handle: TextIO, stop: asyncio.Event) -> None:
    """Append every widget socket frame with its receive stamp until `stop` is set."""
    url = build_socket_url(series_id)
    frames = 0
    while not stop.is_set():
        try:
            async with websockets.connect(
                url, origin=cast(websockets.Origin, GRID_WIDGETS_ORIGIN), max_size=None
            ) as socket:
                logger.info("grid socket open: %s", series_id)
                async for raw in socket:
                    handle.write(
                        json.dumps({"received_at_utc": stamp_now(), "frame": str(raw)}) + "\n"
                    )
                    handle.flush()
                    frames += 1
                    if frames % 20 == 0:
                        logger.info("grid frames: %d", frames)
                    if stop.is_set():
                        return
        except (websockets.ConnectionClosed, OSError) as error:
            logger.warning("grid socket closed: %s", error)
            await asyncio.sleep(3.0)


async def record_livestats(
    game_id: str, back_seconds: int, handle: TextIO, stop: asyncio.Event
) -> None:
    """Poll one livestats window every 10 seconds and append each raw response."""
    polls = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        while not stop.is_set():
            starting_time = aligned_starting_time(back_seconds)
            try:
                response = await client.get(
                    f"{LIVESTATS_FEED}/window/{game_id}",
                    params={"startingTime": starting_time},
                )
                if response.status_code == 200:
                    handle.write(
                        json.dumps(
                            {
                                "received_at_utc": stamp_now(),
                                "starting_time": starting_time,
                                "payload": response.json(),
                            }
                        )
                        + "\n"
                    )
                    handle.flush()
                    polls += 1
                    if polls % 6 == 0:
                        logger.info("livestats windows: %d", polls)
                elif response.status_code == 204:
                    logger.info("livestats has no frame yet for %s", starting_time)
                else:
                    logger.warning("livestats %s for %s", response.status_code, starting_time)
            except httpx.HTTPError as error:
                logger.warning("livestats request failed: %s", error)
            await asyncio.sleep(POLL_SECONDS)


async def run_recording(
    target: LiveTarget, out_dir: Path, back_seconds: int, minutes: float
) -> None:
    """Run both recorders until Ctrl-C, or for `minutes` when that is positive."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "target.json").write_text(
        json.dumps(
            {
                "slug": target.slug,
                "title": target.title,
                "series_id": target.series_id,
                "esports_game_id": target.esports_game_id,
                "started_at_utc": stamp_now(),
            },
            indent=2,
        )
    )
    stop = asyncio.Event()
    grid_path = out_dir / "grid.jsonl"
    live_path = out_dir / "livestats.jsonl"
    logger.info("writing %s and %s", grid_path, live_path)
    logger.info("stop with Ctrl-C after at least 6 minutes of game time")
    with (
        grid_path.open("a", encoding="utf-8") as grid_handle,
        live_path.open("a", encoding="utf-8") as live_handle,
    ):
        tasks = [
            asyncio.create_task(record_grid(target.series_id, grid_handle, stop)),
            asyncio.create_task(
                record_livestats(target.esports_game_id, back_seconds, live_handle, stop)
            ),
        ]
        try:
            if minutes > 0:
                await asyncio.sleep(minutes * 60.0)
                logger.info("time limit reached after %.1f minutes", minutes)
            else:
                await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            raise
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def main(argv: list[str]) -> int:
    """Resolve one live map on both sources and record it into a fresh run directory."""
    setup_logging()
    parser = argparse.ArgumentParser(prog="record-lol-dual-feed")
    parser.add_argument("slug", nargs="?", default=None)
    parser.add_argument("--esports-game-id", default=None)
    parser.add_argument("--back-seconds", type=int, default=WINDOW_BACK_SECONDS)
    parser.add_argument("--minutes", type=float, default=0.0)
    args = parser.parse_args(argv)
    if args.slug is None:
        list_candidates()
        return 0
    with http_client() as client:
        series_id, title = resolve_series_id(client, args.slug)
        game_id = args.esports_game_id or pick_esports_game(client, args.slug)
    target = LiveTarget(slug=args.slug, title=title, series_id=series_id, esports_game_id=game_id)
    logger.info("%s | grid=%s | livestats=%s", title, series_id, game_id)
    out_dir = RECORD_ROOT / f"{args.slug}-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}"
    try:
        asyncio.run(run_recording(target, out_dir, args.back_seconds, args.minutes))
    except KeyboardInterrupt:
        logger.info("stopped; recording is in %s", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
