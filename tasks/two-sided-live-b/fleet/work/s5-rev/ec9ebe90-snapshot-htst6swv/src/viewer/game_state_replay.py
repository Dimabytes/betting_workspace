"""Game-state replay: fold one match's feed archive into a GameState for the viewer.

match.json pins the feed source and horn; each source has its own jsonl archive
and reducer, but every path reduces to rows deduped per truncated second. X is
the VPS wall clock when the packet reached the bot, minus horn — the same
receipt clock the model and the order tape live on; the feed game second stays
available per row for hover. The same replay also yields the journal clock:
game second -> receipt wall second, used by the tape when no core trace exists.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from math import floor
from pathlib import Path
from typing import cast

from shared.constants.paths import STATE_ARCHIVE_FILENAME
from shared.utils.match_time import parse_utc
from trader.game_profile import GAME_PROFILES
from trader.grid_feed import GridFrameReducer, GridOrientationError, replay_grid_records
from trader.grid_widget_types import GridStateArchiveRecord
from trader.live_feed import FeedEvent, GameSnapshot
from trader.oddin_feed import OddinSnapshotReducer, replay_oddin_records
from trader.oddin_types import OddinStateArchiveRecord
from trader.paths import (
    GRID_STATE_ARCHIVE_FILENAME,
    ODDIN_STATE_ARCHIVE_FILENAME,
)
from trader.steam_feed import build_game_snapshot
from trader.steam_types import SteamRealtimeStats
from viewer.archive_read import (
    as_int,
    as_str,
    horn_at_utc,
    iter_json_objects,
    market_block,
    read_match_document,
)
from viewer.types import GameState, empty_game_state


@dataclass(frozen=True)
class FeedClock:
    """Journal game seconds mapped onto wall seconds from horn via feed receipts.

    `received_by_second` holds the last receipt wall time per game second;
    `median_delay` (receipt minus game second) covers seconds the archive missed.
    """

    received_by_second: dict[int, float]
    median_delay: float | None

    def wall_at(self, second: int) -> float:
        """Receipt wall second for one journal game second."""
        mapped = self.received_by_second.get(second)
        if mapped is not None:
            return mapped
        if self.median_delay is not None:
            return second + self.median_delay
        return float(second)


@dataclass(frozen=True)
class FeedReplay:
    """One feed archive folded once: the game-state panel and the journal clock."""

    state: GameState
    clock: FeedClock


@dataclass(frozen=True)
class _FeedRow:
    """One replayed tick: receipt wall second from horn, plus the snapshot."""

    received_second: float
    snapshot: GameSnapshot


def _yes_is_radiant(document: dict[str, object]) -> bool:
    """match.json market.yes_is_radiant, defaulting True when the block is unusable."""
    market = market_block(document)
    if market is None:
        return True
    value = market.get("yes_is_radiant")
    return value if isinstance(value, bool) else True


def _received_second(received_at_utc: str | None, horn_at: datetime | None) -> float | None:
    """Receipt wall seconds from horn; None when either side is unusable."""
    if received_at_utc is None or horn_at is None:
        return None
    try:
        return (parse_utc(received_at_utc) - horn_at).total_seconds()
    except ValueError:
        return None


def _feed_row(
    snapshot: GameSnapshot,
    received_at_utc: str | None,
    horn_at: datetime | None,
) -> _FeedRow | None:
    """One replay row keyed on the receipt wall second; None when receipt is unusable."""
    received_second = _received_second(received_at_utc, horn_at)
    if received_second is None:
        return None
    return _FeedRow(received_second, snapshot)


def _rows_from_events(events: Iterable[FeedEvent], horn_at: datetime | None) -> list[_FeedRow]:
    """Rows for oddin/grid keyed on each event's receipt wall second."""
    return [
        row
        for event in events
        if (row := _feed_row(event.snapshot, event.received_at_utc, horn_at)) is not None
    ]


def _dedupe_plot_snapshots(rows: list[_FeedRow]) -> list[_FeedRow]:
    """Keep the last tick of each truncated receipt second."""
    by_second: dict[int, _FeedRow] = {}
    for row in rows:
        row_second = float(floor(row.received_second))
        by_second[floor(row.received_second)] = _FeedRow(row_second, row.snapshot)
    return [by_second[key] for key in sorted(by_second)]


def _feed_clock(rows: list[_FeedRow]) -> FeedClock:
    """game second -> last receipt wall second, plus the median feed delay."""
    received_by_second: dict[int, float] = {}
    delays: list[float] = []
    for row in rows:
        second = row.snapshot.second
        received_by_second[second] = row.received_second
        delays.append(row.received_second - second)
    median_delay = sorted(delays)[len(delays) // 2] if delays else None
    return FeedClock(received_by_second, median_delay)


def _snapshots_to_state(rows: list[_FeedRow]) -> GameState:
    """One GameState from replay rows; empty when none."""
    ticks = _dedupe_plot_snapshots(rows)
    if not ticks:
        return empty_game_state()
    return GameState(
        seconds=tuple(row.received_second for row in ticks),
        game_seconds=tuple(float(row.snapshot.second) for row in ticks),
        radiant_nw=tuple(float(row.snapshot.radiant_nw) for row in ticks),
        dire_nw=tuple(float(row.snapshot.dire_nw) for row in ticks),
        radiant_nw_adv=tuple(float(row.snapshot.radiant_nw_adv) for row in ticks),
        top1_nw_adv=tuple(float(row.snapshot.top.top1_nw_adv) for row in ticks),
        radiant_xp_adv=tuple(float(row.snapshot.radiant_xp_adv) for row in ticks),
        deaths_radiant=tuple(float(row.snapshot.deaths_radiant) for row in ticks),
        deaths_dire=tuple(float(row.snapshot.deaths_dire) for row in ticks),
    )


def _steam_snapshots(archive_dir: Path, horn_at: datetime | None) -> list[_FeedRow]:
    """Reduce state.jsonl payloads; X is the VPS receipt time minus horn."""
    rows: list[_FeedRow] = []
    previous: GameSnapshot | None = None
    for record in iter_json_objects(archive_dir / STATE_ARCHIVE_FILENAME):
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        try:
            snapshot = build_game_snapshot(cast(SteamRealtimeStats, payload), previous)
        except (KeyError, TypeError, ValueError):
            continue
        row = _feed_row(snapshot, as_str(record.get("received_at_utc")), horn_at)
        if row is not None:
            rows.append(row)
        previous = snapshot
    return rows


def _grid_snapshots(
    archive_dir: Path, document: dict[str, object], horn_at: datetime | None
) -> list[_FeedRow]:
    """Replay grid_state.jsonl; X is the VPS receipt time minus horn."""
    market = market_block(document)
    map_number = as_int(document.get("map_number"))
    outcome_0 = as_str(market.get("outcome_0_name")) if market is not None else None
    outcome_1 = as_str(market.get("outcome_1_name")) if market is not None else None
    if map_number is None or outcome_0 is None or outcome_1 is None:
        return []
    raw_game = document.get("game")
    profile = GAME_PROFILES.get(raw_game if isinstance(raw_game, str) else "dota")
    if profile is None:
        return []
    reducer = GridFrameReducer(map_number, outcome_0, outcome_1, profile)
    records: list[GridStateArchiveRecord] = []
    for raw in iter_json_objects(archive_dir / GRID_STATE_ARCHIVE_FILENAME):
        received = as_str(raw.get("received_at_utc"))
        frame_raw = as_str(raw.get("frame"))
        if received is None or frame_raw is None:
            continue
        records.append({"received_at_utc": received, "frame": frame_raw})
    try:
        return _rows_from_events(replay_grid_records(records, reducer), horn_at)
    except GridOrientationError:
        return []


def _oddin_snapshots(
    archive_dir: Path, document: dict[str, object], horn_at: datetime | None
) -> list[_FeedRow]:
    """Replay oddin_state.jsonl; X is the VPS receipt time minus horn."""
    map_number = as_int(document.get("map_number"))
    if map_number is None:
        return []
    reducer = OddinSnapshotReducer(map_number, _yes_is_radiant(document))
    records: list[OddinStateArchiveRecord] = []
    for raw in iter_json_objects(archive_dir / ODDIN_STATE_ARCHIVE_FILENAME):
        received = as_str(raw.get("received_at_utc"))
        event_name = as_str(raw.get("event"))
        if received is None or event_name is None:
            continue
        records.append(
            {"received_at_utc": received, "event": event_name, "payload": raw.get("payload")}
        )
    return _rows_from_events(replay_oddin_records(records, reducer), horn_at)


def _replay_rows(archive_dir: Path) -> list[_FeedRow]:
    """Replay the pinned feed archive once; empty when match.json or source is missing."""
    document = read_match_document(archive_dir)
    if document is None:
        return []
    horn_at = horn_at_utc(document)
    feed_source = document.get("feed_source")
    if feed_source == "grid":
        return _grid_snapshots(archive_dir, document, horn_at)
    if feed_source == "steam":
        return _steam_snapshots(archive_dir, horn_at)
    if feed_source == "oddin":
        return _oddin_snapshots(archive_dir, document, horn_at)
    return []


def load_live_replay(archive_dir: Path) -> FeedReplay:
    """Fold the pinned feed archive: game-state rows plus the journal clock."""
    rows = _replay_rows(archive_dir)
    return FeedReplay(state=_snapshots_to_state(rows), clock=_feed_clock(rows))


def load_live_game_state(archive_dir: Path) -> GameState:
    """GameState from the pinned feed archive: steam, grid, or oddin jsonl."""
    return load_live_replay(archive_dir).state


def load_feed_clock(archive_dir: Path) -> FeedClock:
    """Journal-second -> wall-second map from the pinned feed archive."""
    return load_live_replay(archive_dir).clock
