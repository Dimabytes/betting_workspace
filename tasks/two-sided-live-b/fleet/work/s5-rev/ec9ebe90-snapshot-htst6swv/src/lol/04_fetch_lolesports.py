"""Resume-download raw lolesports livestats windows and details for accepted LoL links."""

import gzip
import math
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from multiprocessing import get_context
from pathlib import Path
from typing import Annotated, Protocol, cast

import httpx
import msgspec
import typer

from lol.constants import (
    LOL_DETAILS_DIR,
    LOL_DOWNLOAD_AUDIT_PATH,
    LOL_FETCH_END_SECOND,
    LOL_FETCH_MAX_WALL_SECONDS,
    LOL_FETCH_STALL_RESPONSE_LIMIT,
    LOL_LINKS_PATH,
    LOL_MAX_CONCURRENCY,
    LOL_PAUSE_MIN_GAP_SECONDS,
    LOL_WINDOW_STEP_SECONDS,
    LOL_WINDOWS_DIR,
    REASON_DETAILS_RETRIES_EXHAUSTED,
    REASON_DOWNLOAD_COMPLETE,
    REASON_EMPTY_BODY,
    REASON_FEED_ENDED_NO_FLAG,
    REASON_HTTP_404,
    REASON_RETRIES_EXHAUSTED,
    REASON_WALL_TIME_LIMIT,
)
from lol.parquet_io import read_parquet_rows, write_parquet_rows
from lol.types import LolDownloadAuditRow, LolLinkRow
from shared.constants.api import HTTP_TIMEOUT_SECONDS
from shared.utils.log import print_count

AUDIT_COLUMNS: list[str] = list(LolDownloadAuditRow.__annotations__)
AUDIT_INTEGER_COLUMNS = [
    "game_number",
    "window_response_count",
    "unique_frame_count",
    "max_game_second",
    "details_response_count",
]
PRINT_REASONS = (
    REASON_DOWNLOAD_COMPLETE,
    REASON_WALL_TIME_LIMIT,
    REASON_HTTP_404,
    REASON_EMPTY_BODY,
    REASON_RETRIES_EXHAUSTED,
    REASON_FEED_ENDED_NO_FLAG,
    REASON_DETAILS_RETRIES_EXHAUSTED,
)
AUDIT_MAX_WORKERS = 8
AUDIT_PROGRESS_EVERY = 500
JSON_DECODER = msgspec.json.Decoder()


class LinkStage(Protocol):
    """Stage 03 HTTP and schema helpers used by the window downloader."""

    EmptyHttpBody: type[Exception]
    RetryableHttpError: type[Exception]

    def fetch_window_at(
        self, client: httpx.Client, esports_game_id: str, starting_time: str
    ) -> object | None:
        """GET one livestats window at startingTime."""
        ...

    def fetch_details_at(
        self, client: httpx.Client, esports_game_id: str, starting_time: str
    ) -> object | None:
        """GET one livestats details page at startingTime."""
        ...

    def validate_window_body(self, body: object) -> object:
        """Require string esportsGameId and a frames list."""
        ...

    def encode_json(self, value: object) -> str:
        """Stable JSON for one JSONL line."""
        ...


LINK = cast(LinkStage, cast(object, import_module("lol.03_link_lolesports")))


@dataclass(frozen=True)
class WindowFrame:
    """One livestats frame used for pause-aware game-second stop/audit."""

    wall_seconds: float
    stamp: str
    has_gold: bool
    finished: bool
    paused: bool


@dataclass(frozen=True)
class GameClock:
    """Pause-aware game-clock fold state over frames in fetch order."""

    spawn: float | None
    previous: float
    paused: float
    best: float


EMPTY_GAME_CLOCK = GameClock(spawn=None, previous=0.0, paused=0.0, best=0.0)


@dataclass(frozen=True)
class WindowsOutcome:
    """Stop reason and completeness of one map's window walk."""

    reason: str
    complete: bool


@dataclass(frozen=True)
class WindowResponse:
    """Updated window-walk state after one valid Riot response."""

    clock: GameClock
    stalled_responses: int
    outcome: WindowsOutcome | None


@dataclass(frozen=True)
class DetailsOutcome:
    """Response count and completeness of one map's details backfill."""

    response_count: int
    complete: bool


@dataclass(frozen=True)
class DetailsAttempt:
    """One details GET: continue the walk, or stop."""

    query_ts: int
    response_count: int
    complete: bool
    stop: bool


@dataclass(frozen=True)
class ArchiveSummary:
    """Bounded-memory metadata for one published window archive."""

    response_count: int
    unique_frame_count: int
    max_game_second: int | None
    last_frame_wall_seconds: float | None
    finished: bool


@dataclass(frozen=True)
class FetchPlan:
    """Maps served from published archives and maps requiring worker execution."""

    cached: tuple[LolLinkRow, ...]
    queued: tuple[LolLinkRow, ...]


def feed_game_clock(clock: GameClock, frames: Sequence[WindowFrame]) -> GameClock:
    """Fold new frames into the clock; non-increasing stamps are duplicates and skipped."""
    spawn, previous, paused, best = clock.spawn, clock.previous, clock.paused, clock.best
    for frame in frames:
        if spawn is None:
            if frame.has_gold:
                spawn = frame.wall_seconds
                previous = frame.wall_seconds
            continue
        if frame.wall_seconds <= previous:
            continue
        gap = frame.wall_seconds - previous
        if gap > LOL_PAUSE_MIN_GAP_SECONDS:
            paused += gap
        previous = frame.wall_seconds
        game_time = frame.wall_seconds - spawn - paused
        if game_time > best:
            best = game_time
    return GameClock(spawn=spawn, previous=previous, paused=paused, best=best)


def game_clock_reached(clock: GameClock, end_second: int) -> bool:
    """True when the folded game clock has hit the fetch stop second."""
    return clock.spawn is not None and math.floor(clock.best) >= end_second


def archive_path(windows_dir: Path, esports_game_id: str) -> Path:
    """Gzip JSONL path for one map's raw window archive."""
    return windows_dir / f"{esports_game_id}.jsonl.gz"


def partial_path(target: Path) -> Path:
    """Work file written before atomic replace of the archive."""
    return target.with_suffix(target.suffix + ".partial")


def format_starting_time(ts: int) -> str:
    """Format unix seconds as a lolesports startingTime query value."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def grid_origin(loading_anchor_ts: int) -> int:
    """Floor the loading anchor to the 10-second startingTime grid."""
    return loading_anchor_ts - (loading_anchor_ts % LOL_WINDOW_STEP_SECONDS)


def parse_rfc460_seconds(stamp: str) -> float | None:
    """Parse rfc460Timestamp to float unix seconds; None if invalid."""
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp()


def participant_has_gold(raw: object) -> bool:
    """True when one participant object has numeric totalGold > 0."""
    if not isinstance(raw, dict):
        return False
    gold = cast(dict[str, object], raw).get("totalGold")
    return isinstance(gold, int | float) and not isinstance(gold, bool) and gold > 0


def team_has_gold(team: object) -> bool:
    """True when any blueTeam/redTeam participant has gold."""
    if not isinstance(team, dict):
        return False
    participants = cast(dict[str, object], team).get("participants")
    if not isinstance(participants, list):
        return False
    return any(participant_has_gold(item) for item in cast(list[object], participants))


def frame_has_gold(frame: Mapping[str, object]) -> bool:
    """True when any participant on either side has totalGold > 0."""
    return team_has_gold(frame.get("blueTeam")) or team_has_gold(frame.get("redTeam"))


def frame_is_finished(frame: Mapping[str, object]) -> bool:
    """True when Riot marks one livestats frame as terminal."""
    return frame.get("gameState") == "finished"


def frame_is_paused(frame: Mapping[str, object]) -> bool:
    """True when Riot marks one livestats frame as a game pause."""
    return frame.get("gameState") == "paused"


def frames_from_payload(payload: object) -> list[WindowFrame]:
    """Collect stamped frames from one raw window object."""
    if not isinstance(payload, dict):
        return []
    root = cast(dict[str, object], payload)
    raw_frames = root.get("frames")
    if not isinstance(raw_frames, list):
        return []
    frames: list[WindowFrame] = []
    for raw in cast(list[object], raw_frames):
        if not isinstance(raw, dict):
            continue
        frame = cast(dict[str, object], raw)
        stamp = frame.get("rfc460Timestamp")
        if not isinstance(stamp, str) or not stamp:
            continue
        wall = parse_rfc460_seconds(stamp)
        if wall is None:
            continue
        frames.append(
            WindowFrame(
                wall, stamp, frame_has_gold(frame), frame_is_finished(frame), frame_is_paused(frame)
            )
        )
    return frames


def frames_from_payloads(payloads: Sequence[object]) -> list[WindowFrame]:
    """Collect stamped frames from every saved window payload."""
    frames: list[WindowFrame] = []
    for payload in payloads:
        frames.extend(frames_from_payload(payload))
    return frames


def payload_is_finished(payload: object) -> bool:
    """True when a window payload contains Riot's terminal frame state."""
    return any(frame.finished for frame in frames_from_payload(payload))


def payloads_are_finished(payloads: Sequence[object]) -> bool:
    """True when any saved window payload contains a terminal frame."""
    return any(payload_is_finished(payload) for payload in reversed(payloads))


def unique_frame_count(payloads: Sequence[object]) -> int:
    """Count unique rfc460Timestamp values across saved window lines."""
    stamps = {frame.stamp for frame in frames_from_payloads(payloads)}
    return len(stamps)


def max_game_second(payloads: Sequence[object]) -> int | None:
    """Floor of pause-aware game time, or None when no spawn frame exists."""
    frames = sorted(frames_from_payloads(payloads), key=lambda frame: frame.wall_seconds)
    spawn_index: int | None = None
    for index, frame in enumerate(frames):
        if frame.has_gold:
            spawn_index = index
            break
    if spawn_index is None:
        return None
    post_spawn = frames[spawn_index:]
    spawn = post_spawn[0].wall_seconds
    previous = spawn
    paused = 0.0
    best = 0.0
    for frame in post_spawn[1:]:
        gap = frame.wall_seconds - previous
        if gap > LOL_PAUSE_MIN_GAP_SECONDS:
            paused += gap
        previous = frame.wall_seconds
        game_time = frame.wall_seconds - spawn - paused
        if game_time > best:
            best = game_time
    return math.floor(best)


def reached_end_second(payloads: Sequence[object]) -> bool:
    """True when pause-aware game time has reached the fetch stop second."""
    maximum = max_game_second(payloads)
    return maximum is not None and maximum >= LOL_FETCH_END_SECOND


def next_starting_ts(payloads: Sequence[object], t0: int) -> int:
    """Resume T from the last saved line that has frame stamps, else t0."""
    for payload in reversed(list(payloads)):
        frames = frames_from_payload(payload)
        if not frames:
            continue
        latest = max(frame.wall_seconds for frame in frames)
        return int(latest - (latest % LOL_WINDOW_STEP_SECONDS)) + LOL_WINDOW_STEP_SECONDS
    return t0


def write_gzip_jsonl(path: Path, payloads: Sequence[object]) -> None:
    """Atomically write gzip JSONL through a .tmp sibling with stable gzip metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with (
        tmp.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped,
    ):
        for payload in payloads:
            zipped.write((LINK.encode_json(payload) + "\n").encode("utf-8"))
    tmp.replace(path)


def append_gzip_jsonl(path: Path, payload: object) -> None:
    """Append one JSONL line as its own gzip member without rewriting earlier windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        path.open("ab") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped,
    ):
        zipped.write((LINK.encode_json(payload) + "\n").encode("utf-8"))


def iter_gzip_jsonl(path: Path) -> Iterator[object]:
    """Yield JSONL values while tolerating a truncated trailing gzip member."""
    yielded = False
    try:
        with gzip.open(path, "rb") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                payload = cast(object, JSON_DECODER.decode(text))
                yielded = True
                yield payload
    except (EOFError, gzip.BadGzipFile, msgspec.DecodeError):
        if not yielded:
            raise


def read_gzip_jsonl(path: Path) -> list[object]:
    """Read gzip JSONL; keep complete members if the trailing member is truncated."""
    return list(iter_gzip_jsonl(path))


def summarize_window_archive(path: Path) -> ArchiveSummary:
    """Build audit metadata without retaining raw payload dictionaries."""
    response_count = 0
    stamps: set[str] = set()
    clock = EMPTY_GAME_CLOCK
    last_frame_wall_seconds: float | None = None
    finished = False
    for payload in iter_gzip_jsonl(path):
        frames = frames_from_payload(payload)
        response_count += 1
        stamps.update(frame.stamp for frame in frames)
        clock = feed_game_clock(clock, frames)
        for frame in frames:
            if last_frame_wall_seconds is None or frame.wall_seconds > last_frame_wall_seconds:
                last_frame_wall_seconds = frame.wall_seconds
        finished = finished or any(frame.finished for frame in frames)
    maximum = math.floor(clock.best) if clock.spawn is not None else None
    return ArchiveSummary(response_count, len(stamps), maximum, last_frame_wall_seconds, finished)


def count_archive_payloads(path: Path) -> int:
    """Count details payloads without retaining them."""
    return sum(1 for _payload in iter_gzip_jsonl(path))


def load_existing_payloads(target: Path) -> list[object]:
    """Prefer a .partial (crashed run), else the published archive, else empty."""
    partial = partial_path(target)
    if partial.exists():
        return read_gzip_jsonl(partial)
    if target.exists():
        return read_gzip_jsonl(target)
    return []


def load_previous_audit(path: Path) -> dict[str, LolDownloadAuditRow]:
    """Index an existing download audit by esports_game_id, if present."""
    if not path.exists():
        return {}
    rows = cast(list[LolDownloadAuditRow], read_parquet_rows(path))
    return {row["esports_game_id"]: row for row in rows}


def build_audit_row(
    link: LolLinkRow,
    payloads: Sequence[object],
    complete: bool,
    reason: str,
    details_response_count: int,
) -> LolDownloadAuditRow:
    """Fill one download-audit row from the link and saved window payloads."""
    return {
        "esports_game_id": str(link["esports_game_id"]),
        "event_id": str(link["event_id"]),
        "esports_match_id": str(link["esports_match_id"]),
        "game_number": int(link["game_number"]),
        "window_response_count": len(payloads),
        "unique_frame_count": unique_frame_count(payloads),
        "max_game_second": max_game_second(payloads),
        "details_response_count": details_response_count,
        "complete": complete,
        "reason": reason,
    }


def should_skip_http(
    payloads: Sequence[object], previous: LolDownloadAuditRow | None, t0: int
) -> str | None:
    """Skip HTTP when the archive already hit end, a complete 404, or the current wall."""
    if not payloads:
        return None
    if payloads_are_finished(payloads):
        return REASON_DOWNLOAD_COMPLETE
    if reached_end_second(payloads):
        return REASON_DOWNLOAD_COMPLETE
    if (
        previous is not None
        and previous["reason"] == REASON_HTTP_404
        and bool(previous["complete"])
    ):
        return REASON_HTTP_404
    query_ts = next_starting_ts(payloads, t0)
    if query_ts - t0 > LOL_FETCH_MAX_WALL_SECONDS:
        return REASON_WALL_TIME_LIMIT
    return None


def apply_window_response(
    body: object,
    payloads: list[object],
    partial: Path,
    clock: GameClock,
    stalled_responses: int,
) -> WindowResponse:
    """Append advancing data, stop on finished, or count a duplicate response."""
    LINK.validate_window_body(body)
    frames = frames_from_payload(body)
    finished = any(frame.finished for frame in frames)
    stalled = (
        clock.spawn is not None
        and bool(frames)
        and max(frame.wall_seconds for frame in frames) <= clock.previous
    )
    if stalled and not finished:
        if frames[-1].paused:
            return WindowResponse(clock, 0, None)
        stalled_responses += 1
        outcome = None
        if stalled_responses >= LOL_FETCH_STALL_RESPONSE_LIMIT:
            outcome = WindowsOutcome(reason=REASON_FEED_ENDED_NO_FLAG, complete=True)
        return WindowResponse(clock, stalled_responses, outcome)
    payloads.append(body)
    append_gzip_jsonl(partial, body)
    clock = feed_game_clock(clock, frames)
    outcome = None
    if finished or game_clock_reached(clock, LOL_FETCH_END_SECOND):
        outcome = WindowsOutcome(reason=REASON_DOWNLOAD_COMPLETE, complete=True)
    return WindowResponse(clock, 0, outcome)


def download_map_windows(
    client: httpx.Client,
    game_id: str,
    target: Path,
    partial: Path,
    payloads: list[object],
    t0: int,
) -> WindowsOutcome:
    """Walk the startingTime grid appending windows into payloads and the partial."""
    query_ts = next_starting_ts(payloads, t0)
    if payloads and not partial.exists():
        write_gzip_jsonl(partial, payloads)
    # Fold the game clock incrementally: a full frames rescan per appended window
    # was O(n^2) per map and pegged one core, starving every worker on the GIL.
    clock = feed_game_clock(EMPTY_GAME_CLOCK, frames_from_payloads(payloads))
    stalled_responses = 0
    reason: str | None = None
    complete = False
    while query_ts - t0 <= LOL_FETCH_MAX_WALL_SECONDS:
        starting_time = format_starting_time(query_ts)
        try:
            body = LINK.fetch_window_at(client, game_id, starting_time)
        except LINK.EmptyHttpBody:
            query_ts += LOL_WINDOW_STEP_SECONDS
            continue
        except LINK.RetryableHttpError:
            reason = REASON_RETRIES_EXHAUSTED
            complete = False
            break
        if body is None:
            reason = REASON_HTTP_404
            complete = max_game_second(payloads) is not None
            break
        response = apply_window_response(body, payloads, partial, clock, stalled_responses)
        clock = response.clock
        stalled_responses = response.stalled_responses
        if response.outcome is not None:
            reason = response.outcome.reason
            complete = response.outcome.complete
            break
        query_ts += LOL_WINDOW_STEP_SECONDS
    if reason is None:
        if not payloads:
            reason = REASON_EMPTY_BODY
            complete = False
        else:
            reason = REASON_WALL_TIME_LIMIT
            complete = True
    write_gzip_jsonl(partial, payloads)
    partial.replace(target)
    return WindowsOutcome(reason=reason, complete=complete)


def details_body_is_valid(body: object) -> bool:
    """True when one details payload is a dict with a frames list (no game id inside)."""
    if not isinstance(body, dict):
        return False
    return isinstance(cast(dict[str, object], body).get("frames"), list)


def load_details_payloads(target: Path) -> list[object]:
    """Prefer a details .partial (crashed run), else the published archive, else empty."""
    partial = partial_path(target)
    if partial.exists():
        return read_gzip_jsonl(partial)
    if target.exists():
        return read_gzip_jsonl(target)
    return []


def fetch_details_page(
    client: httpx.Client,
    game_id: str,
    starting_time: str,
    partial: Path,
    query_ts: int,
    response_count: int,
) -> DetailsAttempt:
    """One details GET: append a valid page, skip empty, or stop."""
    try:
        body = LINK.fetch_details_at(client, game_id, starting_time)
    except LINK.EmptyHttpBody:
        return DetailsAttempt(
            query_ts=query_ts + LOL_WINDOW_STEP_SECONDS,
            response_count=response_count,
            complete=True,
            stop=False,
        )
    except LINK.RetryableHttpError:
        return DetailsAttempt(
            query_ts=query_ts,
            response_count=response_count,
            complete=False,
            stop=True,
        )
    if body is None:
        return DetailsAttempt(
            query_ts=query_ts,
            response_count=response_count,
            complete=True,
            stop=True,
        )
    if not details_body_is_valid(body):
        raise RuntimeError(f"incompatible details schema for {game_id}")
    append_gzip_jsonl(partial, body)
    return DetailsAttempt(
        query_ts=query_ts + LOL_WINDOW_STEP_SECONDS,
        response_count=response_count + 1,
        complete=True,
        stop=False,
    )


def download_map_details(
    client: httpx.Client,
    game_id: str,
    details_dir: Path,
    end_wall: float | None,
    t0: int,
) -> DetailsOutcome:
    """Backfill the details archive over the window archive's frame range."""
    if end_wall is None:
        return DetailsOutcome(response_count=0, complete=True)
    target = archive_path(details_dir, game_id)
    partial = partial_path(target)
    payloads = load_details_payloads(target)
    response_count = len(payloads)
    query_ts = next_starting_ts(payloads, t0)
    if query_ts > end_wall:
        if partial.exists():
            partial.replace(target)
        return DetailsOutcome(response_count=response_count, complete=True)
    if payloads and not partial.exists():
        write_gzip_jsonl(partial, payloads)
    del payloads
    complete = True
    while query_ts <= end_wall:
        page = fetch_details_page(
            client, game_id, format_starting_time(query_ts), partial, query_ts, response_count
        )
        query_ts = page.query_ts
        response_count = page.response_count
        complete = page.complete
        if page.stop:
            break
    if not partial.exists():
        write_gzip_jsonl(partial, [])
    partial.replace(target)
    return DetailsOutcome(response_count=response_count, complete=complete)


def summary_completion_reason(summary: ArchiveSummary) -> str | None:
    """Return the successful stop reason represented by an archive summary."""
    if summary.finished:
        return REASON_DOWNLOAD_COMPLETE
    if summary.max_game_second is not None and summary.max_game_second >= LOL_FETCH_END_SECOND:
        return REASON_DOWNLOAD_COMPLETE
    return None


def build_summary_audit_row(
    link: LolLinkRow,
    summary: ArchiveSummary,
    complete: bool,
    reason: str,
    details_response_count: int,
) -> LolDownloadAuditRow:
    """Fill an audit row from streaming window metadata."""
    return {
        "esports_game_id": str(link["esports_game_id"]),
        "event_id": str(link["event_id"]),
        "esports_match_id": str(link["esports_match_id"]),
        "game_number": int(link["game_number"]),
        "window_response_count": summary.response_count,
        "unique_frame_count": summary.unique_frame_count,
        "max_game_second": summary.max_game_second,
        "details_response_count": details_response_count,
        "complete": complete,
        "reason": reason,
    }


def finish_terminal_existing_archive(
    client: httpx.Client,
    link: LolLinkRow,
    target: Path,
    partial: Path,
    details_dir: Path,
    t0: int,
) -> LolDownloadAuditRow | None:
    """Publish a terminal resume archive and backfill details without bulk loading windows."""
    source = partial if partial.exists() else target
    if not source.exists():
        return None
    summary = summarize_window_archive(source)
    window_reason = summary_completion_reason(summary)
    if window_reason is None:
        return None
    if source == partial:
        partial.replace(target)
    game_id = str(link["esports_game_id"])
    details = download_map_details(
        client, game_id, details_dir, summary.last_frame_wall_seconds, t0
    )
    reason = window_reason if details.complete else REASON_DETAILS_RETRIES_EXHAUSTED
    return build_summary_audit_row(link, summary, details.complete, reason, details.response_count)


def download_one_map(
    client: httpx.Client,
    link: LolLinkRow,
    windows_dir: Path,
    details_dir: Path,
    previous_audit: Mapping[str, LolDownloadAuditRow],
) -> LolDownloadAuditRow:
    """Download or skip one map's windows, then backfill its details archive."""
    game_id = str(link["esports_game_id"])
    target = archive_path(windows_dir, game_id)
    partial = partial_path(target)
    t0 = grid_origin(int(link["loading_anchor_ts"]))
    terminal = finish_terminal_existing_archive(client, link, target, partial, details_dir, t0)
    if terminal is not None:
        return terminal
    payloads = load_existing_payloads(target)
    skip_reason = should_skip_http(payloads, previous_audit.get(game_id), t0)
    if skip_reason is not None:
        if partial.exists():
            partial.replace(target)
        windows = WindowsOutcome(reason=skip_reason, complete=True)
    else:
        windows = download_map_windows(client, game_id, target, partial, payloads, t0)
    frames = frames_from_payloads(payloads)
    end_wall = max((frame.wall_seconds for frame in frames), default=None)
    details = download_map_details(client, game_id, details_dir, end_wall, t0)
    reason = windows.reason if details.complete else REASON_DETAILS_RETRIES_EXHAUSTED
    complete = windows.complete and details.complete
    return build_audit_row(link, payloads, complete, reason, details.response_count)


def reuse_audit_row(link: LolLinkRow, previous: LolDownloadAuditRow) -> LolDownloadAuditRow:
    """Carry cached metrics forward while refreshing link identity fields."""
    return {
        "esports_game_id": str(link["esports_game_id"]),
        "event_id": str(link["event_id"]),
        "esports_match_id": str(link["esports_match_id"]),
        "game_number": int(link["game_number"]),
        "window_response_count": int(previous["window_response_count"]),
        "unique_frame_count": int(previous["unique_frame_count"]),
        "max_game_second": previous["max_game_second"],
        "details_response_count": int(previous["details_response_count"]),
        "complete": bool(previous["complete"]),
        "reason": str(previous["reason"]),
    }


def build_cached_audit_row(
    link: LolLinkRow,
    windows_dir: Path,
    details_dir: Path,
    previous: LolDownloadAuditRow | None,
) -> LolDownloadAuditRow:
    """Reuse prior audit data or stream one trusted published archive."""
    if previous is not None:
        return reuse_audit_row(link, previous)
    game_id = str(link["esports_game_id"])
    windows = summarize_window_archive(archive_path(windows_dir, game_id))
    details_count = count_archive_payloads(archive_path(details_dir, game_id))
    reason = summary_completion_reason(windows) or REASON_WALL_TIME_LIMIT
    return build_summary_audit_row(link, windows, True, reason, details_count)


def previous_allows_cache(previous: LolDownloadAuditRow | None) -> bool:
    """True when prior status does not require another window walk."""
    if previous is None:
        return True
    return bool(previous["complete"]) and previous["reason"] != REASON_WALL_TIME_LIMIT


def plan_fetches(
    links: Sequence[LolLinkRow],
    windows_dir: Path,
    details_dir: Path,
    previous_audit: Mapping[str, LolDownloadAuditRow],
) -> FetchPlan:
    """Exclude published complete pairs before constructing worker futures."""
    cached: list[LolLinkRow] = []
    queued: list[LolLinkRow] = []
    for link in links:
        game_id = str(link["esports_game_id"])
        windows_target = archive_path(windows_dir, game_id)
        details_target = archive_path(details_dir, game_id)
        previous = previous_audit.get(game_id)
        if (
            windows_target.is_file()
            and details_target.is_file()
            and previous_allows_cache(previous)
        ):
            partial_path(windows_target).unlink(missing_ok=True)
            partial_path(details_target).unlink(missing_ok=True)
            cached.append(link)
        else:
            queued.append(link)
    return FetchPlan(tuple(cached), tuple(queued))


def build_cached_audit_rows(
    links: Sequence[LolLinkRow],
    windows_dir: Path,
    details_dir: Path,
    previous_audit: Mapping[str, LolDownloadAuditRow],
    workers: int,
) -> list[LolDownloadAuditRow]:
    """Reuse prior rows and scan missing audit metadata on a bounded process pool."""
    rows: list[LolDownloadAuditRow] = []
    scan_links: list[LolLinkRow] = []
    for link in links:
        game_id = str(link["esports_game_id"])
        previous = previous_audit.get(game_id)
        if previous is None:
            scan_links.append(link)
        else:
            rows.append(reuse_audit_row(link, previous))
    scan_count = len(scan_links)
    if scan_count == 0:
        return rows
    audit_workers = max(1, min(AUDIT_MAX_WORKERS, workers, scan_count))
    if audit_workers == 1:
        futures = (
            build_cached_audit_row(link, windows_dir, details_dir, None) for link in scan_links
        )
        for scanned, row in enumerate(futures, start=1):
            rows.append(row)
            if scanned == scan_count or scanned % AUDIT_PROGRESS_EVERY == 0:
                print(f"lolesports_progress audit={scanned}/{scan_count}", flush=True)
        return rows
    pool = ProcessPoolExecutor(max_workers=audit_workers, mp_context=get_context("spawn"))
    try:
        pending = [
            pool.submit(build_cached_audit_row, link, windows_dir, details_dir, None)
            for link in scan_links
        ]
        for scanned, future in enumerate(as_completed(pending), start=1):
            rows.append(future.result())
            if scanned == scan_count or scanned % AUDIT_PROGRESS_EVERY == 0:
                print(f"lolesports_progress audit={scanned}/{scan_count}", flush=True)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return rows


def print_download_totals(rows: Sequence[LolDownloadAuditRow]) -> None:
    """Print stable per-reason counts for the pre-Telonex checkpoint."""
    totals: dict[str, int] = {reason: 0 for reason in PRINT_REASONS}
    for row in rows:
        reason = row["reason"]
        totals[reason] = totals.get(reason, 0) + 1
    for reason in PRINT_REASONS:
        print_count(reason, totals[reason])


def livestats_http_client(workers: int) -> httpx.Client:
    """HTTP client whose connection pool is large enough for the worker count."""
    return httpx.Client(
        timeout=HTTP_TIMEOUT_SECONDS,
        headers={"User-Agent": "dota-2-model/0.1 research pipeline"},
        follow_redirects=True,
        limits=httpx.Limits(
            max_keepalive_connections=workers,
            max_connections=workers,
        ),
    )


def fetch_lolesports(
    links_path: Path, windows_dir: Path, details_dir: Path, audit_path: Path, workers: int
) -> None:
    """Download livestats windows and details for every accepted link and write the audit."""
    links = cast(list[LolLinkRow], read_parquet_rows(links_path))
    previous_audit = load_previous_audit(audit_path)
    windows_dir.mkdir(parents=True, exist_ok=True)
    details_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_fetches(links, windows_dir, details_dir, previous_audit)
    queued_count = len(plan.queued)
    active_workers = min(workers, queued_count)
    print(
        f"lolesports_plan total={len(links)} cached={len(plan.cached)} "
        f"queued={queued_count} workers={active_workers}",
        flush=True,
    )
    rows: list[LolDownloadAuditRow] = []
    if queued_count == 0:
        print("lolesports_progress done=0/0", flush=True)
        rows.extend(
            build_cached_audit_rows(plan.cached, windows_dir, details_dir, previous_audit, workers)
        )
    else:
        with livestats_http_client(active_workers) as client:
            pool = ThreadPoolExecutor(max_workers=active_workers)
            try:
                futures = [
                    pool.submit(
                        download_one_map, client, link, windows_dir, details_dir, previous_audit
                    )
                    for link in plan.queued
                ]
                for done, future in enumerate(as_completed(futures), start=1):
                    rows.append(future.result())
                    if done == queued_count or done % 10 == 0:
                        print(f"lolesports_progress done={done}/{queued_count}", flush=True)
            except BaseException:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            pool.shutdown(wait=True)
        rows.extend(
            build_cached_audit_rows(plan.cached, windows_dir, details_dir, previous_audit, workers)
        )
    write_parquet_rows(rows, audit_path, AUDIT_COLUMNS, AUDIT_INTEGER_COLUMNS, ["esports_game_id"])
    print_download_totals(rows)
    if any(not row["complete"] for row in rows):
        raise typer.Exit(1)


def main(
    links_path: Annotated[Path, typer.Option("--links-path")] = LOL_LINKS_PATH,
    windows_dir: Annotated[Path, typer.Option("--windows-dir")] = LOL_WINDOWS_DIR,
    details_dir: Annotated[Path, typer.Option("--details-dir")] = LOL_DETAILS_DIR,
    audit_path: Annotated[Path, typer.Option("--audit-path")] = LOL_DOWNLOAD_AUDIT_PATH,
    workers: Annotated[int, typer.Option("--workers")] = LOL_MAX_CONCURRENCY,
) -> None:
    """Download raw livestats windows and details for every accepted link."""
    fetch_lolesports(links_path, windows_dir, details_dir, audit_path, workers)


if __name__ == "__main__":
    typer.run(main)
