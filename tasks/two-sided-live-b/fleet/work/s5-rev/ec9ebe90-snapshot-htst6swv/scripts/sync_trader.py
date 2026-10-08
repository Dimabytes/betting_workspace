"""Rsync trader match archives from the VPS into the local data tree.

Remote is the live catalog `./data/trader_live` on host `sun`. The wallet
directory is never copied. Existing match files are updated; rsync does not
delete local extras. After a successful pass, local `X.jsonl`/`X.jsonl.gz`
twins left by the remote compressor are deduplicated (content must compare
equal; `session.jsonl` is never touched). A failed rsync or `--dry-run`
changes nothing, and the shared archive flock keeps a local compress pass
from swapping files mid-sync.

SSH uses host `sun` from ~/.ssh/config and will prompt for the key passphrase.

  PYTHONPATH=src uv run python scripts/sync_trader.py
  PYTHONPATH=src uv run python scripts/sync_trader.py --dry-run
"""

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import rsync_pull

from trader.compress_archives import (
    DEFAULT_MIN_AGE_HOURS,
    LOCK_FILENAME,
    ScanReport,
    scan_root,
    summarize_report,
)
from trader.process_lock import FileLock, FileLockHeld, acquire_file_lock

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "/root/work/esports-trader/data/trader_live"
DEFAULT_DEST = REPO_ROOT / "data" / "trader"
WALLET_DIR_NAME = "wallet"


@dataclass(frozen=True)
class ArchiveCoverage:
    """Local trader archive coverage after a sync."""

    matches: int
    finalized: int
    unfinalized: int
    min_joined: str | None
    max_joined: str | None


def build_rsync_argv(
    ssh_host: str,
    remote_trader: str,
    dest: Path,
    dry_run: bool,
) -> list[str]:
    """One rsync: remote trader_live/ → local tree, skip wallet, never delete."""
    return rsync_pull.rsync_argv(
        ssh_host,
        remote_trader,
        dest,
        ["-avz", f"--exclude={WALLET_DIR_NAME}/"],
        dry_run,
    )


def scan_archive(root: Path) -> ArchiveCoverage:
    """Count match dirs and join-stamp span from match.json files."""
    if not root.is_dir():
        return ArchiveCoverage(
            matches=0, finalized=0, unfinalized=0, min_joined=None, max_joined=None
        )
    matches = 0
    finalized = 0
    unfinalized = 0
    joined: list[str] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name == WALLET_DIR_NAME:
            continue
        matches += 1
        meta_path = child / "match.json"
        if not meta_path.is_file():
            unfinalized += 1
            continue
        try:
            document = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            unfinalized += 1  # a truncated sidecar is not worth aborting the report over
            continue
        if document.get("final") is None:
            unfinalized += 1
        else:
            finalized += 1
        stamp = document.get("joined_at_utc")
        if isinstance(stamp, str):
            joined.append(stamp)
    ordered = sorted(joined)
    return ArchiveCoverage(
        matches=matches,
        finalized=finalized,
        unfinalized=unfinalized,
        min_joined=ordered[0] if ordered else None,
        max_joined=ordered[-1] if ordered else None,
    )


def print_coverage(root: Path) -> None:
    """Print local match coverage after the transfer."""
    coverage = scan_archive(root)
    print(
        f"trader: matches={coverage.matches} finalized={coverage.finalized} "
        f"unfinalized={coverage.unfinalized} min={coverage.min_joined} "
        f"max={coverage.max_joined}",
        flush=True,
    )


def print_dedup(report: ScanReport) -> None:
    """Report post-sync plain/gz dedup results."""
    print(f"dedup: {summarize_report(report)}", flush=True)
    for result in report.results:
        if result.action in ("conflict", "error"):
            print(f"dedup {result.action}: {result.path} ({result.reason})", flush=True)


def main(argv: list[str]) -> None:
    """Rsync trader archives into data/trader, dedup gz twins, print coverage."""
    args = rsync_pull.parse_argv(argv, __doc__, DEFAULT_REMOTE, DEFAULT_DEST)
    dest = rsync_pull.prepared_dest(args.dest)
    lock: FileLock | None = None
    if not args.dry_run:
        try:
            lock = acquire_file_lock(dest / LOCK_FILENAME)
        except FileLockHeld:
            raise SystemExit(
                f"archive lock held under {dest}; a compress pass is running — retry later"
            ) from None
    try:
        rsync_pull.run_rsync(build_rsync_argv(args.ssh_host, args.remote, dest, args.dry_run))
        if not args.dry_run:
            print_dedup(
                scan_root(
                    dest,
                    require_marker=True,
                    min_age_ns=int(DEFAULT_MIN_AGE_HOURS * 3_600_000_000_000),
                    now_ns=time.time_ns(),
                    compress_new=False,
                    dry_run=False,
                )
            )
    finally:
        if lock is not None:
            lock.close()
    print_coverage(dest)


if __name__ == "__main__":
    main(sys.argv[1:])
