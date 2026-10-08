"""Gzip finalized match-archive feeds and dedup plain/gz twins.

Daemon (compose service `compress`): hourly scan over every `--root`. A match
dir is eligible only with `execution_cleanup.json` present and every existing
whitelisted feed untouched for `--min-age-hours` (default 48). The whitelist is
explicit names, never `*.jsonl`: `session.jsonl` stays plain forever because
late fills get appended to closed archives.

  PYTHONPATH=src uv run python -m trader.compress_archives --root data/trader --dry-run
  PYTHONPATH=src uv run python -m trader.compress_archives --root data/trader --once
  PYTHONPATH=src uv run python -m trader.compress_archives --root ...   # daemon loop
  PYTHONPATH=src uv run python -m trader.compress_archives --once --no-marker --root data/live_paper

Per original: capture identity → write `f.jsonl.gz.tmp` → read the gzip back
and byte-compare with the original → identity still intact → atomic rename →
identity re-check → delete the original. A published `.gz` is never overwritten;
a `plain + .gz` pair keeps both unless content compares equal (the dedup path,
shared with scripts/sync_trader.py after its rsync pass). Compressor and sync
share one flock per archive-root; the trader itself never takes it and relies
on the marker + age gate instead.
"""

import argparse
import gzip
import os
import shutil
import sys
import time
import zlib
from collections import Counter
from dataclasses import dataclass, field
from io import BufferedIOBase
from pathlib import Path

from shared.constants.paths import STATE_ARCHIVE_FILENAME
from shared.utils.log import get_logger, setup_logging
from trader.paths import (
    CORE_TRACE_FILENAME,
    EXECUTION_CLEANUP_FILENAME,
    GRID_STATE_ARCHIVE_FILENAME,
    ODDIN_STATE_ARCHIVE_FILENAME,
    SESSION_JOURNAL_FILENAME,
)
from trader.process_lock import FileLock, FileLockHeld, acquire_file_lock

logger = get_logger(__name__)

WHITELIST_FILENAMES = (
    GRID_STATE_ARCHIVE_FILENAME,
    ODDIN_STATE_ARCHIVE_FILENAME,
    STATE_ARCHIVE_FILENAME,
    CORE_TRACE_FILENAME,
)
LOCK_FILENAME = ".compress_archive.lock"
WALLET_DIR_NAME = "wallet"
DEFAULT_MIN_AGE_HOURS = 48.0
DEFAULT_SCAN_INTERVAL_S = 3600.0
COPY_CHUNK = 1 << 20


class GzCorrupt(Exception):
    """A .gz stream failed to decompress cleanly."""


@dataclass(frozen=True)
class FileIdentity:
    """Inode plus content stamps; equal means the file was not touched."""

    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class FileResult:
    """Outcome for one whitelisted file: what happened and why."""

    path: Path
    action: str
    reason: str
    freed_bytes: int


@dataclass(frozen=True)
class DirSkip:
    """A match dir left untouched this pass."""

    match_dir: Path
    reason: str


@dataclass
class ScanReport:
    """Everything one pass over one archive-root did or refused to do."""

    root: Path
    results: list[FileResult] = field(default_factory=list)
    skips: list[DirSkip] = field(default_factory=list)
    lock_held: bool = False


def capture_identity(path: Path) -> FileIdentity | None:
    """Stat-based identity, or None when the file vanished."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return FileIdentity(
        dev=stat.st_dev,
        ino=stat.st_ino,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        ctime_ns=stat.st_ctime_ns,
    )


def identity_intact(path: Path, ident: FileIdentity) -> bool:
    """True when the path still names the same untouched file."""
    return capture_identity(path) == ident


def _read_full(stream: BufferedIOBase, size: int) -> bytes:
    """Exactly `size` bytes; a short underlying read is not EOF."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def gz_content_matches(plain: Path, gz_path: Path) -> bool:
    """True when the decompressed gzip bytes equal the plain file's bytes.

    The whole gzip stream is read, so a truncated or corrupt member raises
    GzCorrupt instead of surfacing as an early content mismatch.
    """
    try:
        with plain.open("rb") as expected, gzip.open(gz_path, "rb") as actual:
            while True:
                want = _read_full(expected, COPY_CHUNK)
                got = _read_full(actual, COPY_CHUNK)
                if want != got:
                    return False
                if not want:
                    return True
    except (OSError, EOFError, zlib.error) as exc:
        raise GzCorrupt(str(gz_path)) from exc


def write_gzip(plain: Path, tmp: Path) -> None:
    """Stream `plain` into `tmp` as gzip, fsynced before publish."""
    with plain.open("rb") as src, tmp.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb") as gz:
            shutil.copyfileobj(src, gz, COPY_CHUNK)
        raw.flush()
        os.fsync(raw.fileno())


def compress_one(plain: Path, gz_path: Path, tmp: Path) -> FileResult:
    """gzip plain → tmp → verify content → atomic publish → drop the original."""
    ident = capture_identity(plain)
    if ident is None:
        return FileResult(plain, "skipped", "vanished", 0)
    try:
        write_gzip(plain, tmp)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return FileResult(plain, "error", f"gzip_write:{exc.__class__.__name__}", 0)
    try:
        matches = gz_content_matches(plain, tmp)
    except GzCorrupt:
        matches = False
    if not matches:
        tmp.unlink(missing_ok=True)
        return FileResult(plain, "error", "verify_failed", 0)
    if not identity_intact(plain, ident):
        tmp.unlink(missing_ok=True)
        return FileResult(plain, "skipped", "changed_during_compress", 0)
    if gz_path.exists():
        tmp.unlink(missing_ok=True)
        return FileResult(plain, "conflict", "gz_appeared", 0)
    os.replace(tmp, gz_path)
    if not identity_intact(plain, ident):
        return FileResult(plain, "skipped", "changed_after_publish", 0)
    freed = ident.size - gz_path.stat().st_size
    try:
        plain.unlink()
    except OSError as exc:
        return FileResult(plain, "error", f"unlink:{exc.__class__.__name__}", 0)
    return FileResult(plain, "compressed", "", freed)


def dedup_pair(plain: Path, gz_path: Path, *, dry_run: bool) -> FileResult:
    """Drop `plain` only when the published `.gz` proves identical content."""
    if dry_run:
        return FileResult(plain, "would_dedup", "", 0)
    ident = capture_identity(plain)
    if ident is None:
        return FileResult(plain, "skipped", "vanished", 0)
    try:
        if not gz_content_matches(plain, gz_path):
            return FileResult(plain, "conflict", "content_differs", 0)
    except GzCorrupt:
        return FileResult(plain, "conflict", "gz_corrupt", 0)
    if not identity_intact(plain, ident):
        return FileResult(plain, "skipped", "changed_during_compare", 0)
    try:
        plain.unlink()
    except OSError as exc:
        return FileResult(plain, "error", f"unlink:{exc.__class__.__name__}", 0)
    return FileResult(plain, "deduped", "", ident.size)


def process_dir(match_dir: Path, *, compress_new: bool, dry_run: bool) -> list[FileResult]:
    """Compress or dedup every whitelisted file inside one match dir."""
    results: list[FileResult] = []
    session_gz = match_dir / f"{SESSION_JOURNAL_FILENAME}.gz"
    if session_gz.exists():
        results.append(FileResult(session_gz, "conflict", "session_gz_unexpected", 0))
    for name in WHITELIST_FILENAMES:
        plain = match_dir / name
        gz_path = match_dir / f"{name}.gz"
        tmp = match_dir / f"{name}.gz.tmp"
        try:
            if not dry_run:
                tmp.unlink(missing_ok=True)  # interrupted write; content is re-verified anyway
            has_plain = plain.is_file()
            has_gz = gz_path.is_file()
            if has_plain and has_gz:
                results.append(dedup_pair(plain, gz_path, dry_run=dry_run))
            elif has_plain and compress_new:
                if dry_run:
                    results.append(FileResult(plain, "would_compress", "", 0))
                else:
                    results.append(compress_one(plain, gz_path, tmp))
        except OSError as exc:
            results.append(FileResult(plain, "error", exc.__class__.__name__, 0))
    return results


def dir_block_reason(match_dir: Path, *, require_marker: bool, oldest_mtime_ns: int) -> str | None:
    """Why this match dir must wait, or None when it is eligible now."""
    if require_marker and not (match_dir / EXECUTION_CLEANUP_FILENAME).is_file():
        return "no_cleanup_marker"
    for name in WHITELIST_FILENAMES:
        plain = match_dir / name
        if plain.exists():
            if plain.stat().st_mtime_ns > oldest_mtime_ns:
                return f"fresh:{name}"
            continue
        gz_path = match_dir / f"{name}.gz"
        if gz_path.exists() and gz_path.stat().st_mtime_ns > oldest_mtime_ns:
            return f"fresh:{name}.gz"
    return None


def scan_root(
    root: Path,
    *,
    require_marker: bool,
    min_age_ns: int,
    now_ns: int,
    compress_new: bool,
    dry_run: bool,
) -> ScanReport:
    """One pass over every match dir under `root`. Caller holds the lock."""
    report = ScanReport(root=root)
    oldest_mtime_ns = now_ns - min_age_ns
    for match_dir in sorted(root.iterdir()):
        if not match_dir.is_dir() or match_dir.name == WALLET_DIR_NAME:
            continue
        try:
            reason = dir_block_reason(
                match_dir, require_marker=require_marker, oldest_mtime_ns=oldest_mtime_ns
            )
            if reason is not None:
                report.skips.append(DirSkip(match_dir, reason))
                continue
            report.results.extend(
                process_dir(match_dir, compress_new=compress_new, dry_run=dry_run)
            )
        except OSError as exc:
            report.results.append(FileResult(match_dir, "error", exc.__class__.__name__, 0))
    return report


def scan_roots(
    roots: list[Path],
    *,
    require_marker: bool,
    min_age_ns: int,
    now_ns: int,
    compress_new: bool,
    dry_run: bool,
) -> list[ScanReport]:
    """Scan every root under its shared flock; a held lock skips that root."""
    reports: list[ScanReport] = []
    for root in roots:
        if not root.is_dir():
            report = ScanReport(root=root)
            report.results.append(FileResult(root, "error", "root_missing", 0))
            reports.append(report)
            continue
        if dry_run:
            reports.append(
                scan_root(
                    root,
                    require_marker=require_marker,
                    min_age_ns=min_age_ns,
                    now_ns=now_ns,
                    compress_new=compress_new,
                    dry_run=True,
                )
            )
            continue
        lock: FileLock | None = None
        try:
            lock = acquire_file_lock(root / LOCK_FILENAME)
        except FileLockHeld:
            report = ScanReport(root=root, lock_held=True)
            reports.append(report)
            continue
        try:
            reports.append(
                scan_root(
                    root,
                    require_marker=require_marker,
                    min_age_ns=min_age_ns,
                    now_ns=now_ns,
                    compress_new=compress_new,
                    dry_run=False,
                )
            )
        finally:
            lock.close()
    return reports


def summarize_report(report: ScanReport) -> str:
    """One-line per-root tally for logs and the post-sync report."""
    counts = Counter(result.action for result in report.results)
    freed = sum(result.freed_bytes for result in report.results)
    fresh = sum(1 for skip in report.skips if skip.reason.startswith("fresh"))
    actions = " ".join(f"{action}={count}" for action, count in sorted(counts.items()))
    return (
        f"{report.root}: {actions or 'nothing'} "
        f"freed={freed / 2**20:.1f}MiB fresh_dirs={fresh} "
        f"no_marker_dirs={len(report.skips) - fresh}"
    )


def log_report(report: ScanReport, *, dry_run: bool) -> None:
    """Print dry-run candidates, warn on problems, log the tally."""
    if report.lock_held:
        logger.warning("compress %s: skipped, lock held by another process", report.root)
        return
    for result in report.results:
        if dry_run and result.action.startswith("would"):
            print(result.path, flush=True)
        elif result.action in ("conflict", "error"):
            logger.warning("compress %s %s (%s)", result.action, result.path, result.reason or "-")
    logger.info("compress %s", summarize_report(report))


def main(argv: list[str]) -> int:
    """CLI: daemon loop by default; --once/--dry-run for single passes."""
    parser = argparse.ArgumentParser(
        prog="compress_archives",
        description="Gzip finalized match-archive feeds; dedup plain/gz twins.",
    )
    parser.add_argument(
        "--root", type=Path, action="append", required=True, help="archive root; repeatable"
    )
    parser.add_argument("--once", action="store_true", help="one scan pass, then exit")
    parser.add_argument("--dry-run", action="store_true", help="list candidates, change nothing")
    parser.add_argument(
        "--no-marker",
        action="store_true",
        help="historical roots: drop the execution_cleanup.json gate",
    )
    parser.add_argument("--min-age-hours", type=float, default=DEFAULT_MIN_AGE_HOURS)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_SCAN_INTERVAL_S)
    args = parser.parse_args(argv)
    if args.no_marker and not (args.once or args.dry_run):
        parser.error("--no-marker is a one-shot mode; combine it with --once or --dry-run")
    setup_logging()
    min_age_ns = int(args.min_age_hours * 3_600_000_000_000)
    reports: list[ScanReport] = []
    while True:
        reports = scan_roots(
            args.root,
            require_marker=not args.no_marker,
            min_age_ns=min_age_ns,
            now_ns=time.time_ns(),
            compress_new=True,
            dry_run=args.dry_run,
        )
        for report in reports:
            log_report(report, dry_run=args.dry_run)
        if args.once or args.dry_run:
            break
        time.sleep(args.interval_seconds)
    failed = any(report.lock_held for report in reports) or any(
        result.action == "error" for report in reports for result in report.results
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
