"""Oddin oddin_state.jsonl reader and end-of-match summarizer."""

import json
from collections.abc import Iterator
from pathlib import Path

from shared.utils.jsonl_io import open_maybe_gz
from trader.archive_types import ArchiveOutcome, MatchArchiveSummary, MatchMeta
from trader.live_feed import MatchPhase, first_event_horn_iso
from trader.oddin_feed import OddinSnapshotReducer, replay_oddin_records
from trader.oddin_types import OddinStateArchiveRecord
from trader.paths import ODDIN_STATE_ARCHIVE_FILENAME
from trader.strict_json import StrictJsonError, require_exact_keys, require_object, require_str

_ODDIN_ARCHIVE_KEYS = frozenset({"received_at_utc", "event", "payload"})


def summarize(archive_dir: Path, document: MatchMeta) -> ArchiveOutcome:
    """Replay oddin_state.jsonl through the same reducer. Winner comes from previousMaps."""
    archive_path = archive_dir / ODDIN_STATE_ARCHIVE_FILENAME
    reducer = OddinSnapshotReducer(document["map_number"], document["market"]["yes_is_radiant"])
    events = list(replay_oddin_records(iter_oddin_archive_records(archive_path), reducer))
    if not events:
        raise ValueError("cannot summarize an empty archive")
    last = events[-1]
    summary = MatchArchiveSummary(
        snapshot_count=len(events),
        duration_seconds=last.snapshot.second,
        pause_seconds=None,
        missing_seconds=None,
        winner=reducer.winner,
        finished=last.snapshot.phase is MatchPhase.FINISHED,
    )
    return ArchiveOutcome(
        summary=summary,
        archive_path=archive_path,
        grid_delay_s=None,
        trusted_horn=first_event_horn_iso(events),
    )


def iter_oddin_archive_records(archive_path: Path) -> Iterator[OddinStateArchiveRecord]:
    """Yield each persisted Oddin record; malformed completed lines raise."""
    with open_maybe_gz(archive_path) as handle:
        for line_number, line in enumerate(handle, start=1):
            label = f"{archive_path} record {line_number}"
            try:
                loaded: object = json.loads(line)
                fields = require_object(loaded, label)
                require_exact_keys(fields, _ODDIN_ARCHIVE_KEYS, label)
                yield {
                    "received_at_utc": require_str(fields, "received_at_utc", label),
                    "event": require_str(fields, "event", label),
                    "payload": fields.get("payload"),
                }
            except (json.JSONDecodeError, UnicodeDecodeError, StrictJsonError) as exc:
                raise ValueError(
                    f"malformed archive record in {archive_path} at line {line_number}: "
                    f"{type(exc).__name__}"
                ) from exc
