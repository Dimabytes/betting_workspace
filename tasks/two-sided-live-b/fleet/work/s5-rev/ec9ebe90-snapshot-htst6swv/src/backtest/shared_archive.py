"""One finished archive checkpoint, grafted into every seed with the same fingerprint."""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backtest.lol_inputs import NO_REPLAY_STOP_REASONS
from backtest.marks import EnrichedFill
from backtest.postprocess import read_fills_checkpoint, write_fills_parquet
from backtest.quote_store import parts_dir, write_quote_event_parts
from backtest.results import (
    MakerMatchResult,
    assert_manifest_matches,
    read_manifest,
    read_quote_events_checkpoint,
    read_results_checkpoint,
    write_manifest,
    write_results_checkpoint,
)
from backtest.telemetry import QuoteEvent
from shared.utils.log import get_logger

logger = get_logger(__name__)

ARCHIVE_DIRNAME = "_archive"
ARCHIVE_DONE_FILENAME = "DONE"


@dataclass(frozen=True)
class SharedArchive:
    """Finished archive checkpoint a seed grafts instead of replaying."""

    results: tuple[MakerMatchResult, ...]
    fills: tuple[EnrichedFill, ...]
    events: tuple[QuoteEvent, ...]
    ids: frozenset[int]


def empty_shared_archive() -> SharedArchive:
    """No archive yet: overlay and graft leave the seed rows alone."""
    return SharedArchive(results=(), fills=(), events=(), ids=frozenset())


@dataclass(frozen=True)
class ArchiveOverlay:
    """Checkpoint rows after archive ids replace whatever was there."""

    results: tuple[MakerMatchResult, ...]
    fills: tuple[EnrichedFill, ...]


def overlay_archive(
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    archive: SharedArchive,
) -> ArchiveOverlay:
    """Drop archive ids from the rows, then put the archive rows first."""
    kept_results = tuple(row for row in results if row.match_id not in archive.ids)
    kept_fills = tuple(fill for fill in fills if fill.match_id not in archive.ids)
    return ArchiveOverlay(
        results=(*archive.results, *kept_results),
        fills=(*archive.fills, *kept_fills),
    )


def write_archive_parts(report_dir: Path, archive: SharedArchive) -> None:
    """Replace quote-event parts for archive maps with the archive tape."""
    if not archive.ids:
        return
    target = parts_dir(report_dir)
    for match_id in archive.ids:
        part = target / f"{match_id}.parquet"
        if part.is_file():
            part.unlink()
    replayed = [
        result.match_id
        for result in archive.results
        if result.stop_reason not in NO_REPLAY_STOP_REASONS
    ]
    if not replayed:
        return
    replayed_ids = set(replayed)
    write_quote_event_parts(
        report_dir=report_dir,
        events=[event for event in archive.events if event.match_id in replayed_ids],
        match_ids=replayed,
    )


def write_archive_done(report_dir: Path) -> None:
    """Atomically mark a verified archive finished. Seeds treat this file as the latch."""
    target = report_dir / ARCHIVE_DONE_FILENAME
    tmp = target.with_suffix(".tmp")
    tmp.write_text("ok\n")
    os.replace(tmp, target)


def graft_shared_archive(
    report_dir: Path, archive: SharedArchive, manifest: Mapping[str, Any]
) -> None:
    """Replace seed rows for archive ids with the archive checkpoint."""
    if not archive.ids:
        return
    overlaid = overlay_archive(
        read_results_checkpoint(report_dir),
        read_fills_checkpoint(report_dir),
        archive,
    )
    write_results_checkpoint(report_dir=report_dir, results=overlaid.results)
    write_fills_parquet(report_dir=report_dir, fills=overlaid.fills)
    write_manifest(report_dir, manifest)
    write_archive_parts(report_dir, archive)


def load_shared_archive(archive_dir: Path, expected_manifest: Mapping[str, Any]) -> SharedArchive:
    """Read a finished archive. An empty archive means the seed replays those maps."""
    if not (archive_dir / ARCHIVE_DONE_FILENAME).is_file():
        return empty_shared_archive()
    try:
        assert_manifest_matches(read_manifest(archive_dir), expected_manifest)
    except ValueError as exc:
        logger.warning(
            "shared archive at %s does not match this run (%s); replaying archive maps",
            archive_dir,
            exc,
        )
        return empty_shared_archive()
    results = tuple(read_results_checkpoint(archive_dir))
    return SharedArchive(
        results=results,
        fills=tuple(read_fills_checkpoint(archive_dir)),
        events=tuple(read_quote_events_checkpoint(archive_dir)),
        ids=frozenset(result.match_id for result in results),
    )
