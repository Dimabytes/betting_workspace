"""Rewrite each LoL windows archive as one payload holding only unique frames.

Stage 04 appends every overlapping lolesports window response, so a map archive
stores each frame about six times. `dedup_sort_frames` throws the copies away on
every prepare run, after paying to gunzip and JSON-decode them.

This rewrites an archive in place as a single payload whose frames are already
deduped and sorted by wall time. A file is replaced only when the rewritten
archive reports the same `summarize_window_archive` metadata as the original.
"""

from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from importlib import import_module
from multiprocessing import get_context
from pathlib import Path
from typing import Annotated, Protocol, cast

import typer

from lol.constants import LOL_PREPARE_WORKERS, LOL_WINDOWS_DIR
from lol.livestats_frames import dedup_sort_frames
from shared.utils.log import print_count


class ArchiveSummary(Protocol):
    """Stage 04 window-archive metadata used to prove a rewrite changed nothing."""

    @property
    def unique_frame_count(self) -> int: ...
    @property
    def max_game_second(self) -> int | None: ...
    @property
    def last_frame_wall_seconds(self) -> float | None: ...
    @property
    def finished(self) -> bool: ...


class FetchStage(Protocol):
    """Stage 04 helpers used to read and republish raw window archives."""

    def read_gzip_jsonl(self, path: Path) -> list[object]:
        """Read gzip JSONL into parsed window objects."""
        ...

    def write_gzip_jsonl(self, path: Path, payloads: Sequence[object]) -> None:
        """Atomically write gzip JSONL through a .tmp sibling."""
        ...

    def summarize_window_archive(self, path: Path) -> ArchiveSummary:
        """Stream one archive into bounded-memory metadata."""
        ...


FETCH = cast(FetchStage, cast(object, import_module("lol.04_fetch_lolesports")))

COMPACTED = "compacted"
SKIP_ALREADY_COMPACT = "already_compact"
SKIP_EMPTY = "empty_archive"
SKIP_MIXED_METADATA = "mixed_metadata"
SKIP_SUMMARY_MISMATCH = "summary_mismatch"
SKIP_UNREADABLE = "unreadable"

PROGRESS_EVERY = 250


@dataclass(frozen=True)
class CompactResult:
    """Outcome and size delta of one archive rewrite."""

    reason: str
    bytes_before: int
    bytes_after: int


def payload_header(payload: object) -> dict[str, object] | None:
    """Every field of one window payload except its frames; None when malformed."""
    if not isinstance(payload, dict):
        return None
    fields = cast(dict[str, object], payload)
    return {key: value for key, value in fields.items() if key != "frames"}


def shared_header(payloads: Sequence[object]) -> dict[str, object] | None:
    """The common non-frame payload fields; None when the payloads disagree.

    `game_patch_from_payloads` drops a map whose payloads carry different patch
    versions. Collapsing such a map into one payload would hide that, so such a
    map keeps its original archive.
    """
    first = payload_header(payloads[0])
    if first is None:
        return None
    for payload in payloads[1:]:
        if payload_header(payload) != first:
            return None
    return first


def summaries_agree(before: ArchiveSummary, after: ArchiveSummary) -> bool:
    """Compare every summary field except the payload count, which must shrink."""
    return (
        before.unique_frame_count == after.unique_frame_count
        and before.max_game_second == after.max_game_second
        and before.last_frame_wall_seconds == after.last_frame_wall_seconds
        and before.finished == after.finished
    )


def compact_archive(path: Path) -> CompactResult:
    """Rewrite one archive; replace the original only when its summary is unchanged."""
    bytes_before = path.stat().st_size
    try:
        payloads = FETCH.read_gzip_jsonl(path)
    except (OSError, EOFError, UnicodeDecodeError, ValueError):
        return CompactResult(SKIP_UNREADABLE, bytes_before, bytes_before)
    if not payloads:
        return CompactResult(SKIP_EMPTY, bytes_before, bytes_before)
    if len(payloads) == 1:
        return CompactResult(SKIP_ALREADY_COMPACT, bytes_before, bytes_before)
    header = shared_header(payloads)
    if header is None:
        return CompactResult(SKIP_MIXED_METADATA, bytes_before, bytes_before)

    frames = [frame.payload for frame in dedup_sort_frames(payloads)]
    candidate = path.with_suffix(path.suffix + ".compact")
    FETCH.write_gzip_jsonl(candidate, [{**header, "frames": frames}])
    if not summaries_agree(
        FETCH.summarize_window_archive(path), FETCH.summarize_window_archive(candidate)
    ):
        candidate.unlink()
        return CompactResult(SKIP_SUMMARY_MISMATCH, bytes_before, bytes_before)

    bytes_after = candidate.stat().st_size
    candidate.replace(path)
    return CompactResult(COMPACTED, bytes_before, bytes_after)


def compact_windows(windows_dir: Path, workers: int) -> None:
    """Compact every archive in the windows directory on a process pool."""
    paths = sorted(windows_dir.glob("*.jsonl.gz"))
    if not paths:
        raise SystemExit(f"no window archives under {windows_dir}")
    reasons: dict[str, int] = {}
    saved_bytes = 0
    done = 0
    pool = ProcessPoolExecutor(
        max_workers=min(len(paths), workers), mp_context=get_context("spawn")
    )
    try:
        futures = [pool.submit(compact_archive, path) for path in paths]
        for future in as_completed(futures):
            result = future.result()
            reasons[result.reason] = reasons.get(result.reason, 0) + 1
            saved_bytes += result.bytes_before - result.bytes_after
            done += 1
            if done == len(paths) or done % PROGRESS_EVERY == 0:
                print(f"compacted {done}/{len(paths)} saved={saved_bytes / 1e9:.2f}GB", flush=True)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    for reason, count in sorted(reasons.items()):
        print_count(reason, count)
    print_count("saved_mb", round(saved_bytes / 1e6))


def main(
    windows_dir: Annotated[Path, typer.Option("--windows-dir")] = LOL_WINDOWS_DIR,
    workers: Annotated[int, typer.Option("--workers")] = LOL_PREPARE_WORKERS,
) -> None:
    """Drop duplicate frames from the LoL window archives."""
    compact_windows(windows_dir, workers)


if __name__ == "__main__":
    typer.run(main)
