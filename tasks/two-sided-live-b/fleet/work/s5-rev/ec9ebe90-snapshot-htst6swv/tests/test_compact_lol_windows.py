"""Compaction of LoL window archives keeps every frame and the archive summary."""

from importlib import import_module
from pathlib import Path

import pytest
from test_lol_prepare_dataset import played_frames, window_body

from lol.livestats_frames import dedup_sort_frames

compact = import_module("compact_lol_windows")
FETCH = import_module("lol.04_fetch_lolesports")

GAME_ID = "2001"
SPAWN_WALL = 1_772_000_000.0


def deduped_frames(path: Path) -> list[object]:
    """Deduped, wall-sorted frame payloads of one archive, as prepare reads them."""
    return [item.payload for item in dedup_sort_frames(FETCH.read_gzip_jsonl(path))]


def overlapping_archive(windows_dir: Path, game_id: str) -> Path:
    """Three window responses whose frame ranges overlap, as stage 04 appends them."""
    frames = played_frames(SPAWN_WALL)
    path = FETCH.archive_path(windows_dir, game_id)
    FETCH.write_gzip_jsonl(
        path,
        [
            window_body(game_id, frames[:1]),
            window_body(game_id, frames),
            window_body(game_id, frames),
        ],
    )
    return path


def test_compaction_keeps_frames_and_summary(tmp_path: Path) -> None:
    """One payload replaces three, with the same unique frames and archive summary."""
    path = overlapping_archive(tmp_path, GAME_ID)
    before = FETCH.summarize_window_archive(path)
    before_frames = deduped_frames(path)

    result = compact.compact_archive(path)

    assert result.reason == compact.COMPACTED
    assert result.bytes_after < result.bytes_before
    assert len(FETCH.read_gzip_jsonl(path)) == 1
    after = FETCH.summarize_window_archive(path)
    assert compact.summaries_agree(before, after)
    assert deduped_frames(path) == before_frames


def test_compaction_is_idempotent(tmp_path: Path) -> None:
    """A second pass leaves an already-compacted archive untouched."""
    path = overlapping_archive(tmp_path, GAME_ID)
    compact.compact_archive(path)
    digest = path.read_bytes()

    result = compact.compact_archive(path)

    assert result.reason == compact.SKIP_ALREADY_COMPACT
    assert path.read_bytes() == digest


def test_conflicting_patch_versions_keep_the_original(tmp_path: Path) -> None:
    """Payloads that disagree outside frames stay split, so prepare still drops the map."""
    frames = played_frames(SPAWN_WALL)
    path = FETCH.archive_path(tmp_path, GAME_ID)
    other = window_body(GAME_ID, frames)
    other["gameMetadata"] = {"patchVersion": "16.17.1"}
    FETCH.write_gzip_jsonl(path, [window_body(GAME_ID, frames), other])
    digest = path.read_bytes()

    result = compact.compact_archive(path)

    assert result.reason == compact.SKIP_MIXED_METADATA
    assert path.read_bytes() == digest


@pytest.mark.parametrize("payloads", [[], [{"esportsGameId": GAME_ID, "frames": []}]])
def test_short_archives_are_skipped(tmp_path: Path, payloads: list[object]) -> None:
    """An empty or single-payload archive needs no rewrite."""
    path = FETCH.archive_path(tmp_path, GAME_ID)
    FETCH.write_gzip_jsonl(path, payloads)
    digest = path.read_bytes()

    result = compact.compact_archive(path)

    assert result.reason in {compact.SKIP_EMPTY, compact.SKIP_ALREADY_COMPACT}
    assert path.read_bytes() == digest
