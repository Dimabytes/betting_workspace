"""GRID grid_state.jsonl reader and end-of-match summarizer."""

import json
import statistics
from collections.abc import Iterator
from pathlib import Path

from shared.utils.jsonl_io import open_maybe_gz
from trader.archive_types import (
    ArchiveOutcome,
    MatchArchiveSummary,
    MatchMeta,
    MatchWinner,
    match_game,
)
from trader.game_profile import GAME_PROFILES
from trader.grid_feed import GridFrameReducer, replay_grid_records
from trader.grid_widget_types import GridStateArchiveRecord
from trader.grid_widgets import (
    DIRE_SIDE,
    FINISHED_STATUS,
    RADIANT_SIDE,
    TABLE_SERVICE,
    Scoreboard,
    parse_frame,
)
from trader.live_feed import MatchPhase, first_event_horn_iso
from trader.paths import GRID_STATE_ARCHIVE_FILENAME
from trader.strict_json import StrictJsonError, require_exact_keys, require_object, require_str

_GRID_ARCHIVE_KEYS = frozenset({"received_at_utc", "frame"})


def summarize(archive_dir: Path, document: MatchMeta) -> ArchiveOutcome:
    """Replay grid_state.jsonl through the frame reducer and reduce emitted ticks."""
    archive_path = archive_dir / GRID_STATE_ARCHIVE_FILENAME
    market = document["market"]
    reducer = GridFrameReducer(
        document["map_number"],
        market["outcome_0_name"],
        market["outcome_1_name"],
        GAME_PROFILES[match_game(document)],
    )
    records = list(iter_grid_archive_records(archive_path))
    events = list(replay_grid_records(records, reducer))
    if not events:
        raise ValueError("cannot summarize an empty archive")
    table_delays: list[int] = []
    for record in records:
        frame = parse_frame(record["frame"])
        if frame.service == TABLE_SERVICE:
            table_delays.append(frame.delay)
    delay: float | None = None
    if table_delays:
        delay = float(statistics.median(table_delays))
    last = events[-1]
    summary = MatchArchiveSummary(
        snapshot_count=len(events),
        duration_seconds=last.snapshot.second,
        pause_seconds=None,
        missing_seconds=None,
        winner=_winner_from_grid_board(reducer.board),
        finished=last.snapshot.phase is MatchPhase.FINISHED,
    )
    return ArchiveOutcome(
        summary=summary,
        archive_path=archive_path,
        grid_delay_s=delay,
        trusted_horn=first_event_horn_iso(events),
    )


def iter_grid_archive_records(archive_path: Path) -> Iterator[GridStateArchiveRecord]:
    """Yield each persisted GRID socket record; malformed completed lines raise."""
    with open_maybe_gz(archive_path) as handle:
        for line_number, line in enumerate(handle, start=1):
            label = f"{archive_path} record {line_number}"
            try:
                loaded: object = json.loads(line)
                fields = require_object(loaded, label)
                require_exact_keys(fields, _GRID_ARCHIVE_KEYS, label)
                yield {
                    "received_at_utc": require_str(fields, "received_at_utc", label),
                    "frame": require_str(fields, "frame", label),
                }
            except (json.JSONDecodeError, UnicodeDecodeError, StrictJsonError) as exc:
                raise ValueError(
                    f"malformed archive record in {archive_path} at line {line_number}: "
                    f"{type(exc).__name__}"
                ) from exc


def _winner_from_grid_board(board: Scoreboard | None) -> MatchWinner | None:
    """Winner from `won` on the pinned finished map; live or ambiguous boards are null."""
    if board is None or board.game_status != FINISHED_STATUS:
        return None
    radiant_won = [team.won for team in board.teams if team.side == RADIANT_SIDE]
    dire_won = [team.won for team in board.teams if team.side == DIRE_SIDE]
    if len(radiant_won) != 1 or len(dire_won) != 1:
        return None
    if radiant_won[0] is True and dire_won[0] is False:
        return "radiant"
    if dire_won[0] is True and radiant_won[0] is False:
        return "dire"
    return None
