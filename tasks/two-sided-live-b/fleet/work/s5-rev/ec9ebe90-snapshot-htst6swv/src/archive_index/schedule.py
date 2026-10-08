"""Feed schedule extraction: replay an archive's raw feed into a timing artifact.

The schedule carries when each accepted feed update became available, the game
second it described, and the snapshot features the reducer showed live.
Predictions and model identity stay out. Re-running the extraction on the same
archive bytes with the same rules version reproduces the same fingerprint.
"""

import hashlib
import itertools
import json
import statistics
import zlib
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from lol.constants import LOL_GRID_START_SECOND
from shared.constants.dataset import MODEL_START_SECOND
from shared.constants.paths import ARCHIVE_INDEX_DIR, STATE_ARCHIVE_FILENAME
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.types.opendota import OpenDotaPause
from shared.utils.hashing import sha256_file
from shared.utils.json_io import read_json, write_json
from shared.utils.jsonl_io import decompressed_size, resolve_jsonl, sha256_maybe_gz
from shared.utils.match_time import datetime_to_ns, parse_utc
from shared.utils.parsing import opt_int, opt_str
from shared.utils.top_players import top_player_feature_values
from trader.archive_types import MatchMeta
from trader.game_profile import GAME_PROFILES
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import (
    FEED_GONE_SERVICE,
    GridFrameReducer,
    GridOrientationError,
)
from trader.grid_widgets import TABLE_SERVICE, parse_frame
from trader.live_feed import FeedEvent, KillTick, first_event_horn_iso
from trader.oddin_archive import iter_oddin_archive_records
from trader.oddin_feed import RECONNECT_EVENT, OddinSnapshotReducer, replay_oddin_records
from trader.oddin_types import OddinStateArchiveRecord
from trader.paths import (
    GRID_STATE_ARCHIVE_FILENAME,
    MATCH_META_FILENAME,
    ODDIN_STATE_ARCHIVE_FILENAME,
)
from trader.source_picker import MAX_FEED_DELAY_SECONDS

# Versioned contracts: bump on any change to what counts as an accepted update
# or to the admission gate. The gate pins the live feed-choice delay boundary
# (trader.source_picker.MAX_FEED_DELAY_SECONDS).
# ScheduleTick.phase is stored and has no reader. The Oddin label change
# (stuck negative clock: pre_horn -> pre_match) is not an acceptance change,
# so this version stays. Cached ticks keep the old label until the feed bytes
# or this version change.
EXTRACTION_RULES_VERSION = "feed-schedule-v8"
ADMISSION_RULES_VERSION = "admission-v3"
SCHEDULE_SCHEMA_VERSION = 5
# Game-state columns the live reducer puts on a snapshot. Prior and the book
# mid are not among them: the backtest still takes those from the dataset row
# and the book at receipt.
SNAPSHOT_FEATURE_COLUMNS = (
    "radiant_nw_adv",
    "radiant_nw",
    "dire_nw",
    "radiant_xp_adv",
    "deaths_radiant",
    "deaths_dire",
    "top1_nw_adv",
    "radiant_top1_nw_ratio",
    "dire_top1_nw_ratio",
    "top3_nw_adv",
    "radiant_top3_nw_ratio",
    "dire_top3_nw_ratio",
)

FEED_FILENAMES: dict[str, str] = {
    "grid": GRID_STATE_ARCHIVE_FILENAME,
    "oddin": ODDIN_STATE_ARCHIVE_FILENAME,
    "steam": STATE_ARCHIVE_FILENAME,
}

# Working-window coverage counts ticks in each game's model window.
MODEL_WINDOWS: dict[str, range] = {
    "dota": range(MODEL_START_SECOND, BUY_CUTOFF_SECOND),
    "lol": range(LOL_GRID_START_SECOND, BUY_CUTOFF_SECOND),
}


class ScheduleExtractionError(Exception):
    """The archive could not be replayed into a schedule; `reason` is stable."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ScheduleTick:
    """One accepted feed update: arrival, label second, and the snapshot live saw."""

    seq: int
    received_at_utc: str
    received_ns: int
    game_second: int
    phase: str
    paused: bool
    terminal: bool
    horn_unix_seconds: int
    source_ts_unix: int
    radiant_nw_adv: int
    radiant_nw: int
    dire_nw: int
    radiant_xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top1_nw_adv: int
    radiant_top1_nw_ratio: float
    dire_top1_nw_ratio: float
    top3_nw_adv: int
    radiant_top3_nw_ratio: float
    dire_top3_nw_ratio: float


@dataclass(frozen=True)
class ScheduleKillWait:
    """One side's pending kill wait at a scoreboard marker."""

    awaited_deaths: int
    until_ns: int


@dataclass(frozen=True)
class ScheduleKillGate:
    """A scoreboard kill marker: receipt stamp plus both sides' pending waits."""

    received_ns: int
    radiant: ScheduleKillWait
    dire: ScheduleKillWait


@dataclass(frozen=True)
class ScheduleInterruption:
    """A feed marker that emits no snapshot: Oddin reconnect, GRID feed-gone.

    `seq_after` is the number of schedule ticks emitted before the marker, so
    ticks[seq_after] — when present — is the first post-interruption update.
    The marker's own receipt stamp is what a plain per-tick flag cannot carry.
    """

    seq_after: int
    received_at_utc: str
    received_ns: int
    kind: str


@dataclass(frozen=True)
class ScheduleIdentity:
    """The stable map identity a schedule binds to; enough to join the dataset."""

    game: str
    archive_id: str
    match_id: str
    feed_source: str
    condition_id: str
    market_slug: str
    event_slug: str
    event_id: str | None
    map_number: int
    steam_match_id: str | None
    yes_token_id: str
    no_token_id: str
    yes_is_radiant: bool
    yes_token_index: int | None
    joined_at_utc: str
    joined_at_second: int
    horn_at_utc: str | None


@dataclass(frozen=True)
class ScheduleStats:
    """Coverage and delay facts measured during extraction."""

    tick_count: int
    interruption_count: int
    terminal: bool
    terminal_interrupted: bool
    first_received_at_utc: str | None
    last_received_at_utc: str | None
    first_game_second: int | None
    last_game_second: int | None
    window_ticks: int
    warmup_ticks: int
    paused_ticks: int
    max_arrival_gap_seconds: float | None
    delay_s: float | None
    delay_evidence: str


@dataclass(frozen=True)
class FeedSchedule:
    """The persisted schedule artifact: identity, fingerprints, stats, ticks."""

    schema_version: int
    rules_version: str
    admission_rules_version: str
    max_feed_delay_seconds: int
    fingerprint: str
    identity: ScheduleIdentity
    stats: ScheduleStats
    interruptions: tuple[ScheduleInterruption, ...]
    kill_gates: tuple[ScheduleKillGate, ...]
    match_json_sha256: str
    feed_sha256: str
    feed_file: str
    feed_size: int
    ticks: tuple[ScheduleTick, ...]


def _tick(seq: int, event: FeedEvent) -> ScheduleTick:
    """Project one accepted FeedEvent onto its schedule fields."""
    snapshot = event.snapshot
    top = top_player_feature_values(snapshot.top)
    received_dt = parse_utc(event.received_at_utc)
    return ScheduleTick(
        seq=seq,
        received_at_utc=event.received_at_utc,
        received_ns=datetime_to_ns(received_dt),
        game_second=snapshot.second,
        phase=snapshot.phase.value,
        paused=snapshot.paused,
        terminal=snapshot.finished,
        horn_unix_seconds=event.horn_unix_seconds,
        source_ts_unix=snapshot.server_timestamp,
        radiant_nw_adv=snapshot.radiant_nw_adv,
        radiant_nw=snapshot.radiant_nw,
        dire_nw=snapshot.dire_nw,
        radiant_xp_adv=snapshot.radiant_xp_adv,
        deaths_radiant=snapshot.deaths_radiant,
        deaths_dire=snapshot.deaths_dire,
        top1_nw_adv=int(top["top1_nw_adv"]),
        radiant_top1_nw_ratio=float(top["radiant_top1_nw_ratio"]),
        dire_top1_nw_ratio=float(top["dire_top1_nw_ratio"]),
        top3_nw_adv=int(top["top3_nw_adv"]),
        radiant_top3_nw_ratio=float(top["radiant_top3_nw_ratio"]),
        dire_top3_nw_ratio=float(top["dire_top3_nw_ratio"]),
    )


def _build_interruptions(
    received_stamps: list[str],
    kind: str,
    ticks: list[ScheduleTick],
) -> list[ScheduleInterruption]:
    """Place feed markers on the tick timeline; markers emit no tick themselves.

    Boundary is the count of ticks received no later than the marker, so the
    interruption sits between ticks[seq_after-1] and ticks[seq_after].
    """
    tick_ns = [tick.received_ns for tick in ticks]
    return [
        ScheduleInterruption(
            seq_after=bisect_right(tick_ns, datetime_to_ns(parse_utc(stamp))),
            received_at_utc=stamp,
            received_ns=datetime_to_ns(parse_utc(stamp)),
            kind=kind,
        )
        for stamp in received_stamps
    ]


def _kill_gate(tick: KillTick) -> ScheduleKillGate:
    """Project one reducer KillTick onto its schedule fields."""
    received_ns = datetime_to_ns(parse_utc(tick.received_at_utc))
    return ScheduleKillGate(
        received_ns=received_ns,
        radiant=ScheduleKillWait(
            awaited_deaths=tick.radiant.awaited_deaths,
            until_ns=received_ns + round(tick.radiant.seconds_left * 1e9),
        ),
        dire=ScheduleKillWait(
            awaited_deaths=tick.dire.awaited_deaths,
            until_ns=received_ns + round(tick.dire.seconds_left * 1e9),
        ),
    )


@dataclass(frozen=True)
class _FeedReplay:
    """One archive replay: the ticks a schedule stores, plus the horn they pin."""

    ticks: list[ScheduleTick]
    interruptions: list[ScheduleInterruption]
    kill_gates: list[ScheduleKillGate]
    terminal_interrupted: bool
    table_delays: list[int]
    horn_at_utc: str | None


def _extract_grid(feed_path: Path, meta: MatchMeta) -> _FeedReplay:
    """Replay grid_state.jsonl through the frame reducer.

    The record loop is inlined (it is `replay_grid_records` verbatim) so a
    feed-gone marker stays paired with the terminal artifact the reducer emits
    for it — no downstream timestamp matching, and each frame parses once.
    Kill markers ride along next to ticks; they carry no snapshot.
    """
    market = meta["market"]
    game = meta.get("game") or "dota"
    reducer = GridFrameReducer(
        meta["map_number"],
        market["outcome_0_name"],
        market["outcome_1_name"],
        GAME_PROFILES[game],
    )
    ticks: list[ScheduleTick] = []
    events: list[FeedEvent] = []
    interruptions: list[ScheduleInterruption] = []
    kill_gates: list[ScheduleKillGate] = []
    table_delays: list[int] = []
    terminal_interrupted = False
    for record in iter_grid_archive_records(feed_path):
        frame = parse_frame(record["frame"])
        if frame.service == TABLE_SERVICE:
            table_delays.append(frame.delay)
        event = reducer.reduce_frame(frame, parse_utc(record["received_at_utc"]))
        if isinstance(event, KillTick):
            kill_gates.append(_kill_gate(event))
            continue
        if frame.service == FEED_GONE_SERVICE:
            interruptions.append(
                ScheduleInterruption(
                    seq_after=len(ticks),
                    received_at_utc=record["received_at_utc"],
                    received_ns=datetime_to_ns(parse_utc(record["received_at_utc"])),
                    kind="feed_gone",
                )
            )
        if event is not None:
            terminal_interrupted = frame.service == FEED_GONE_SERVICE
            events.append(event)
            ticks.append(_tick(len(ticks), event))
    return _FeedReplay(
        ticks=ticks,
        interruptions=interruptions,
        kill_gates=kill_gates,
        terminal_interrupted=terminal_interrupted,
        table_delays=table_delays,
        horn_at_utc=first_event_horn_iso(events),
    )


def _extract_oddin(feed_path: Path, meta: MatchMeta) -> _FeedReplay:
    """Replay oddin_state.jsonl; tap reconnect markers (they emit no tick)."""
    reducer = OddinSnapshotReducer(meta["map_number"], meta["market"]["yes_is_radiant"])
    reconnect_stamps: list[str] = []

    def tapped_records() -> Iterator[OddinStateArchiveRecord]:
        for record in iter_oddin_archive_records(feed_path):
            if record["event"] == RECONNECT_EVENT:
                reconnect_stamps.append(record["received_at_utc"])
            yield record

    events = list(replay_oddin_records(tapped_records(), reducer))
    ticks = [_tick(seq, event) for seq, event in enumerate(events)]
    return _FeedReplay(
        ticks=ticks,
        interruptions=_build_interruptions(reconnect_stamps, "reconnect", ticks),
        kill_gates=[],
        terminal_interrupted=False,
        table_delays=[],
        horn_at_utc=first_event_horn_iso(events),
    )


def _resolve_delay(
    meta: MatchMeta,
    table_delays: list[int],
    ticks: list[ScheduleTick],
) -> tuple[float | None, str]:
    """The archive's feed delay for admission: declared first, else measured.

    GRID measures the series_table delay that shifts game seconds; finalize_match
    persists the same median into grid_delay_s. Oddin measured delay is the
    per-tick received minus lastUpdatedAt lag, the quantity the live probe reads.
    """
    if meta["feed_source"] == "grid":
        declared = meta["grid_delay_s"]
        if declared is not None:
            return float(declared), "meta"
        if table_delays:
            return float(statistics.median(table_delays)), "measured"
        return None, "none"
    declared = meta.get("oddin_delay_s")
    if declared is not None:
        return float(declared), "meta"
    lags = [tick.received_ns / 1e9 - tick.source_ts_unix for tick in ticks]
    if lags:
        return float(statistics.median(lags)), "measured"
    return None, "none"


def _build_stats(
    ticks: list[ScheduleTick],
    interruptions: list[ScheduleInterruption],
    terminal_interrupted: bool,
    delay_s: float | None,
    delay_evidence: str,
    window: range,
) -> ScheduleStats:
    """Reduce the emitted ticks to coverage and delay facts."""
    received_ns = [tick.received_ns for tick in ticks]
    gaps = [b - a for a, b in itertools.pairwise(received_ns)]
    last = ticks[-1] if ticks else None
    return ScheduleStats(
        tick_count=len(ticks),
        interruption_count=len(interruptions),
        terminal=bool(last and last.terminal),
        terminal_interrupted=terminal_interrupted,
        first_received_at_utc=ticks[0].received_at_utc if ticks else None,
        last_received_at_utc=ticks[-1].received_at_utc if ticks else None,
        first_game_second=ticks[0].game_second if ticks else None,
        last_game_second=ticks[-1].game_second if ticks else None,
        window_ticks=sum(1 for tick in ticks if tick.game_second in window),
        warmup_ticks=sum(1 for tick in ticks if tick.game_second < 0),
        paused_ticks=sum(1 for tick in ticks if tick.paused),
        max_arrival_gap_seconds=(max(gaps) / 1e9 if gaps else None),
        delay_s=delay_s,
        delay_evidence=delay_evidence,
    )


def compute_fingerprint(
    *,
    feed_sha256: str,
    meta: MatchMeta,
    rules_version: str,
    event_id: str | None,
    yes_token_index: int | None,
) -> str:
    """Hash the schedule's inputs: feed bytes, consumed identity fields, rules.

    Universe-derived fields (event_id, yes_token_index) are inside the payload:
    a universe rebuild must invalidate schedules that embedded the old values.
    Deliberately not covered: dataset hash, feature list, model identity, and
    match.json fields the extractor does not read (pnl, model, final block).
    """
    market = meta["market"]
    payload = {
        "rules_version": rules_version,
        "feed_sha256": feed_sha256,
        "match_id": meta["match_id"],
        "game": meta.get("game") or "dota",
        "condition_id": market["condition_id"],
        "map_number": meta["map_number"],
        "outcome_0_name": market["outcome_0_name"],
        "outcome_1_name": market["outcome_1_name"],
        "yes_is_radiant": market["yes_is_radiant"],
        "event_id": event_id,
        "yes_token_index": yes_token_index,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_schedule(
    archive_dir: Path,
    meta: MatchMeta,
    *,
    event_id: str | None,
    yes_token_index: int | None,
) -> FeedSchedule:
    """Replay the archive's pinned feed into a FeedSchedule, or raise.

    Reasons: unsupported_source (steam has no schedule extractor), feed_missing,
    feed_corrupt, orientation_failed, archive_changing.
    """
    source = meta["feed_source"]
    feed_name = FEED_FILENAMES.get(source)
    if feed_name is None or source == "steam":
        raise ScheduleExtractionError(
            "unsupported_source", f"no schedule extractor for feed_source {source!r}"
        )
    feed_path = resolve_jsonl(archive_dir / feed_name)
    if not feed_path.is_file():
        raise ScheduleExtractionError("feed_missing", f"missing {feed_path}")
    stat_before = feed_path.stat()
    try:
        replay = (
            _extract_grid(feed_path, meta) if source == "grid" else _extract_oddin(feed_path, meta)
        )
    except GridOrientationError as exc:
        raise ScheduleExtractionError("orientation_failed", str(exc)) from exc
    except (
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        RecursionError,
        OSError,
        EOFError,
        zlib.error,
    ) as exc:
        raise ScheduleExtractionError("feed_corrupt", f"{feed_path.name}: {exc}") from exc
    stat_after = feed_path.stat()
    if (stat_before.st_mtime_ns, stat_before.st_size) != (
        stat_after.st_mtime_ns,
        stat_after.st_size,
    ):
        raise ScheduleExtractionError("archive_changing", f"{feed_path} changed during extraction")
    feed_sha256 = sha256_maybe_gz(feed_path)
    delay_s, delay_evidence = _resolve_delay(meta, replay.table_delays, replay.ticks)
    market = meta["market"]
    identity = ScheduleIdentity(
        game=meta.get("game") or "dota",
        archive_id=archive_dir.name,
        match_id=meta["match_id"],
        feed_source=source,
        condition_id=market["condition_id"],
        market_slug=market["market_slug"],
        event_slug=market["event_slug"],
        event_id=event_id,
        map_number=meta["map_number"],
        steam_match_id=meta["steam_match_id"],
        yes_token_id=market["yes_token_id"],
        no_token_id=market["no_token_id"],
        yes_is_radiant=market["yes_is_radiant"],
        yes_token_index=yes_token_index,
        joined_at_utc=meta["joined_at_utc"],
        joined_at_second=meta["joined_at_second"],
        horn_at_utc=replay.horn_at_utc,
    )
    return FeedSchedule(
        schema_version=SCHEDULE_SCHEMA_VERSION,
        rules_version=EXTRACTION_RULES_VERSION,
        admission_rules_version=ADMISSION_RULES_VERSION,
        max_feed_delay_seconds=MAX_FEED_DELAY_SECONDS,
        fingerprint=compute_fingerprint(
            feed_sha256=feed_sha256,
            meta=meta,
            rules_version=EXTRACTION_RULES_VERSION,
            event_id=event_id,
            yes_token_index=yes_token_index,
        ),
        identity=identity,
        stats=_build_stats(
            replay.ticks,
            replay.interruptions,
            replay.terminal_interrupted,
            delay_s,
            delay_evidence,
            MODEL_WINDOWS[meta.get("game") or "dota"],
        ),
        interruptions=tuple(replay.interruptions),
        kill_gates=tuple(replay.kill_gates),
        match_json_sha256=sha256_file(archive_dir / MATCH_META_FILENAME),
        feed_sha256=feed_sha256,
        feed_file=feed_name,
        feed_size=decompressed_size(feed_path),
        ticks=tuple(replay.ticks),
    )


def write_schedule(schedule: FeedSchedule, path: Path) -> None:
    """Atomically write the schedule JSON."""
    write_json(path, asdict(schedule))


def read_schedule(path: Path) -> FeedSchedule:
    """Parse a published schedule JSON back into the typed artifact."""
    payload = read_json(path)
    return FeedSchedule(
        schema_version=int(payload["schema_version"]),
        rules_version=str(payload["rules_version"]),
        admission_rules_version=str(payload["admission_rules_version"]),
        max_feed_delay_seconds=int(payload["max_feed_delay_seconds"]),
        fingerprint=str(payload["fingerprint"]),
        identity=ScheduleIdentity(**payload["identity"]),
        stats=ScheduleStats(**payload["stats"]),
        interruptions=tuple(
            ScheduleInterruption(**interruption) for interruption in payload["interruptions"]
        ),
        kill_gates=tuple(
            ScheduleKillGate(
                received_ns=int(gate["received_ns"]),
                radiant=ScheduleKillWait(**gate["radiant"]),
                dire=ScheduleKillWait(**gate["dire"]),
            )
            for gate in payload.get("kill_gates", ())
        ),
        match_json_sha256=str(payload["match_json_sha256"]),
        feed_sha256=str(payload["feed_sha256"]),
        feed_file=str(payload["feed_file"]),
        feed_size=int(payload["feed_size"]),
        ticks=tuple(ScheduleTick(**tick) for tick in payload["ticks"]),
    )


def pauses_from_schedule(schedule: FeedSchedule) -> list[OpenDotaPause] | None:
    """Reconstruct OpenDota-shape pauses from a schedule's paused ticks.

    A maximal run of `paused` ticks is one pause: `time` is the game second of
    the first paused tick and `duration` is the server-stamp gap bracketing the
    run, which stays correct across a receive gap. Returns None (unknown, not
    `[]`) when a pause boundary is unobserved — the record opens already paused
    or ends mid-pause. `[]` means a complete record observed no pause runs.
    """
    ticks = schedule.ticks
    pauses: list[OpenDotaPause] = []
    index = 0
    while index < len(ticks):
        if not ticks[index].paused:
            index += 1
            continue
        if index == 0:
            return None
        end = index + 1
        while end < len(ticks) and ticks[end].paused:
            end += 1
        if end == len(ticks):
            return None
        pauses.append(
            OpenDotaPause(
                time=ticks[index].game_second,
                duration=ticks[end].source_ts_unix - ticks[index - 1].source_ts_unix,
            )
        )
        index = end
    return pauses


def load_archive_pauses(links: pd.DataFrame) -> dict[int, list[OpenDotaPause] | None]:
    """match_id -> archive-derived pauses; a key means the row carries an archive.

    The value is None when the boundary is unobserved (record opens paused,
    ends mid-pause, or the schedule file is missing) — unknown, not empty.
    """
    pauses: dict[int, list[OpenDotaPause] | None] = {}
    for row in links.itertuples(index=False):
        match_id = opt_int(row.match_id)
        archive_id = opt_str(row.archive_id)
        archive_root = opt_str(row.archive_root)
        if match_id is None or archive_id is None or archive_root is None:
            continue
        path = schedule_path_for(ARCHIVE_INDEX_DIR, archive_root, "dota", archive_id)
        if not path.is_file():
            pauses[match_id] = None
            continue
        pauses[match_id] = pauses_from_schedule(read_schedule(path))
    return pauses


def schedule_path_for(out_dir: Path, root: str, game: str, archive_id: str) -> Path:
    """`schedules/<root>/<game>/<archive_id>.json` under the artifact root.

    The index key is (archive_root, archive_id): the root label must sit in the
    path or two roots holding same-named dirs would overwrite each other.
    """
    return out_dir / "schedules" / root / game / f"{archive_id}.json"


def iter_archive_dirs(root: Path) -> Iterator[Path]:
    """Direct subfolders of an archive root, minus dotdirs and the wallet folder."""
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name == "wallet":
            continue
        yield child
