"""One-time pass: verify finished backtest runs, then drop their intermediates.

A finished run keeps results.parquet, fills.parquet, quote_events.parquet,
summary.json and manifest.json. Its intermediates — quote_event_parts/ and the
merged shard_*ofN dirs — are safe to drop only when every row they hold is
already inside those final artifacts. Old runs predate the compaction report,
so this pass re-verifies by reading a per-match (count, sum(ts_ns)) fingerprint
— two narrow columns, never the whole tape.

  uv run python scripts/cleanup_backtest_intermediates.py            # report only
  uv run python scripts/cleanup_backtest_intermediates.py --apply    # delete verified
"""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownParameterType=false
# pyright: reportMissingTypeArgument=false

import argparse
import contextlib
import json
import os
import re
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow.parquet as pq

from backtest.paths import (
    FILLS_FILENAME,
    MANIFEST_FILENAME,
    QUOTE_EVENTS_FILENAME,
    RESULTS_FILENAME,
    SUMMARY_FILENAME,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "data" / "backtests"
PARTS_DIRNAME = "quote_event_parts"
SHARD_DIR_RE = re.compile(r"shard_\d+of\d+")
DEFAULT_MIN_AGE_HOURS = 48.0
# macOS Finder litter plus per-shard log dirs the writers drop next to shard_*ofN.
IGNORED_ENTRIES = frozenset({".DS_Store", "_shard_logs", "logs"})
EXPECTED_RUN_ENTRIES = frozenset(
    {
        RESULTS_FILENAME,
        FILLS_FILENAME,
        QUOTE_EVENTS_FILENAME,
        SUMMARY_FILENAME,
        MANIFEST_FILENAME,
        PARTS_DIRNAME,
        *IGNORED_ENTRIES,
    }
)
EXPECTED_SHARD_ENTRIES = EXPECTED_RUN_ENTRIES - {SUMMARY_FILENAME, *IGNORED_ENTRIES}


@dataclass(frozen=True)
class RunVerdict:
    """One run dir: what may be dropped and why (not)."""

    report_dir: Path
    deletables: tuple[Path, ...]
    reclaim_bytes: int
    problems: tuple[str, ...]


@dataclass
class _MatchSums:
    """Per-match row counts and ts sums accumulated across parquet files."""

    counts: dict[int, int] = field(default_factory=dict)
    ts_sums: dict[int, int] = field(default_factory=dict)

    def add(self, path: Path) -> None:
        table = pq.read_table(path, columns=["match_id", "ts_ns"])
        for match_id, ts_ns in zip(
            table.column("match_id").to_pylist(),
            table.column("ts_ns").to_pylist(),
            strict=True,
        ):
            if match_id is None or ts_ns is None:
                raise ValueError(f"{path.name}: null match_id/ts_ns")
            key = int(match_id)
            self.counts[key] = self.counts.get(key, 0) + 1
            self.ts_sums[key] = self.ts_sums.get(key, 0) + int(ts_ns)

    def fingerprint(self, match_id: int) -> tuple[int, int]:
        return (self.counts.get(match_id, 0), self.ts_sums.get(match_id, 0))


def _dir_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            with contextlib.suppress(OSError):
                total += (Path(dirpath) / name).stat().st_size
    return total


def _newest_mtime(root: Path) -> float:
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (*dirnames, *filenames):
            with contextlib.suppress(OSError):
                newest = max(newest, (Path(dirpath) / name).stat().st_mtime)
    return newest


def _read_match_ids(path: Path) -> set[int]:
    column = pq.read_table(path, columns=["match_id"]).column("match_id")
    result: set[int] = set()
    for value in column.to_pylist():
        if value is None:
            raise ValueError(f"{path.name}: null match_id")
        result.add(int(value))
    return result


def _check_contained(sums: _MatchSums, part: Path, owner: str) -> list[str]:
    """Every per-match (count, ts-sum) in part must equal the final artifact's."""
    part_sums = _MatchSums()
    part_sums.add(part)
    return [
        f"{owner}: match {match_id} rows differ from final artifact"
        for match_id, count in part_sums.counts.items()
        if sums.fingerprint(match_id) != (count, part_sums.ts_sums[match_id])
    ]


def _verify_shard(shard: Path, result_ids: set[int], fill_sums: _MatchSums) -> list[str]:
    """A shard is droppable when its writer finished and its rows are in the finals."""
    problems = [
        f"{shard.name}: unexpected entry {entry.name}"
        for entry in sorted(shard.iterdir())
        if entry.name not in EXPECTED_SHARD_ENTRIES
    ]
    shard_results = shard / RESULTS_FILENAME
    if shard_results.exists() and not (shard / QUOTE_EVENTS_FILENAME).is_file():
        problems.append(f"{shard.name}: replayed checkpoint without {QUOTE_EVENTS_FILENAME}")
    if shard_results.exists():
        extra = _read_match_ids(shard_results) - result_ids
        if extra:
            problems.append(f"{shard.name}: results for {sorted(extra)} missing from parent")
    shard_fills = shard / FILLS_FILENAME
    if shard_fills.exists():
        problems += _check_contained(fill_sums, shard_fills, shard.name)
    return problems


def _check_parts(parts: Path, quote_sums: _MatchSums, owner: str) -> list[str]:
    """Every part file must be an expected <match>.parquet fully inside the final tape."""
    problems = []
    for entry in sorted(parts.iterdir()):
        stem = entry.name.removesuffix(".parquet")
        if not entry.is_file() or not stem.isdigit():
            problems.append(f"{owner}: unaccounted entry {entry.name}")
            continue
        problems += _check_contained(quote_sums, entry, f"{owner}/{entry.name}")
    return problems


@dataclass(frozen=True)
class _FinalArtifacts:
    """The surviving outputs of a finished run, fingerprinted for comparison."""

    result_ids: set[int]
    fill_sums: _MatchSums
    quote_sums: _MatchSums
    quote_events_present: bool
    problems: tuple[str, ...]


def _check_static_gates(report_dir: Path, now: float, min_age_seconds: float) -> list[str]:
    """Cheap guards that need no parquet reads: summary, foreign files, freshness."""
    problems: list[str] = []
    try:
        if not isinstance(json.loads((report_dir / SUMMARY_FILENAME).read_text()), dict):
            problems.append(f"{SUMMARY_FILENAME} is not an object")
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"no readable {SUMMARY_FILENAME}: {exc}")
    problems += [
        f"unexpected entry {entry.name}"
        for entry in sorted(report_dir.iterdir())
        if entry.name not in EXPECTED_RUN_ENTRIES and not SHARD_DIR_RE.fullmatch(entry.name)
    ]
    if now - _newest_mtime(report_dir) < min_age_seconds:
        problems.append(f"modified within the last {min_age_seconds / 3600:g}h")
    return problems


def _load_final_artifacts(report_dir: Path) -> _FinalArtifacts:
    problems: list[str] = []
    result_ids: set[int] = set()
    if (report_dir / RESULTS_FILENAME).exists():
        result_ids = _read_match_ids(report_dir / RESULTS_FILENAME)
    else:
        problems.append(f"{RESULTS_FILENAME} missing")
    fill_sums = _MatchSums()
    if (report_dir / FILLS_FILENAME).exists():
        fill_sums.add(report_dir / FILLS_FILENAME)
    quote_sums = _MatchSums()
    quote_events_present = (report_dir / QUOTE_EVENTS_FILENAME).exists()
    if quote_events_present:
        quote_sums.add(report_dir / QUOTE_EVENTS_FILENAME)
    return _FinalArtifacts(
        result_ids=result_ids,
        fill_sums=fill_sums,
        quote_sums=quote_sums,
        quote_events_present=quote_events_present,
        problems=tuple(problems),
    )


def _check_all_parts(
    report_dir: Path, shard_dirs: list[Path], finals: _FinalArtifacts
) -> list[str]:
    """Parent parts plus every shard's parts, all checked against the final tape."""
    problems: list[str] = []
    parts = report_dir / PARTS_DIRNAME
    if parts.is_dir():
        problems += _check_parts(parts, finals.quote_sums, PARTS_DIRNAME)
    for shard in shard_dirs:
        shard_parts = shard / PARTS_DIRNAME
        if shard_parts.is_dir():
            owner = f"{shard.name}/{PARTS_DIRNAME}"
            problems += _check_parts(shard_parts, finals.quote_sums, owner)
    return problems


def verify_run(report_dir: Path, now: float, min_age_seconds: float) -> RunVerdict:
    """Verify one run dir; deletables is empty unless every check passed."""
    problems = _check_static_gates(report_dir, now, min_age_seconds)
    finals = _load_final_artifacts(report_dir)
    problems += finals.problems
    parts = report_dir / PARTS_DIRNAME
    shard_dirs = [
        entry
        for entry in sorted(report_dir.iterdir())
        if entry.is_dir() and SHARD_DIR_RE.fullmatch(entry.name)
    ]
    parts_exist = parts.is_dir() or any((shard / PARTS_DIRNAME).is_dir() for shard in shard_dirs)
    if not finals.quote_events_present and parts_exist:
        problems.append(f"{QUOTE_EVENTS_FILENAME} missing while parts exist")
    for shard in shard_dirs:
        problems += _verify_shard(shard, finals.result_ids, finals.fill_sums)
    problems += _check_all_parts(report_dir, shard_dirs, finals)

    deletables = (
        () if problems else tuple(target for target in (parts, *shard_dirs) if target.exists())
    )
    return RunVerdict(
        report_dir=report_dir,
        deletables=deletables,
        reclaim_bytes=sum(_dir_bytes(target) for target in deletables),
        problems=tuple(problems),
    )


def find_run_dirs(root: Path) -> list[Path]:
    """Dirs that directly hold quote_event_parts/ or shard_*ofN; symlink targets excluded."""
    live_targets: list[Path] = []
    run_dirs: set[Path] = set()
    for dirpath, dirnames, _filenames in os.walk(root):
        current = Path(dirpath)
        kept = []
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                live_targets.append(child.resolve())
            else:
                kept.append(name)
        dirnames[:] = kept
        if SHARD_DIR_RE.fullmatch(current.name):
            dirnames[:] = []
            continue
        if PARTS_DIRNAME in dirnames or any(SHARD_DIR_RE.fullmatch(n) for n in dirnames):
            run_dirs.add(current)
    return sorted(
        run_dir
        for run_dir in run_dirs
        if not any(run_dir.resolve().is_relative_to(target) for target in live_targets)
    )


def _verify_one(run_dir: Path, now: float, min_age_seconds: float) -> RunVerdict:
    try:
        return verify_run(run_dir, now, min_age_seconds)
    except Exception as exc:  # a broken artifact must not kill the pass
        return RunVerdict(
            report_dir=run_dir,
            deletables=(),
            reclaim_bytes=0,
            problems=(f"verify error: {exc}",),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--apply", action="store_true", help="delete verified intermediates")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help="skip runs touched more recently than this; 0 disables the age gate",
    )
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    min_age_seconds = args.min_age_hours * 3600

    now = time.time()
    run_dirs = find_run_dirs(root)
    print(f"{len(run_dirs)} run dirs to verify on {args.jobs} workers", flush=True)
    verdicts: list[RunVerdict] = []
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(_verify_one, run_dir, now, min_age_seconds) for run_dir in run_dirs]
        for done, future in enumerate(as_completed(futures), start=1):
            verdicts.append(future.result())
            step = max(1, len(run_dirs) // 40)
            if done % step == 0 or done == len(run_dirs):
                elapsed = time.monotonic() - started
                rate = done / elapsed
                eta_min = (len(run_dirs) - done) / rate / 60
                print(
                    f"  verified {done}/{len(run_dirs)} ({rate:.1f}/s, eta {eta_min:.1f} min)",
                    flush=True,
                )
    verdicts.sort(key=lambda verdict: verdict.report_dir)
    clean = [verdict for verdict in verdicts if verdict.deletables]
    skipped = [verdict for verdict in verdicts if not verdict.deletables]
    reclaim = sum(verdict.reclaim_bytes for verdict in clean)

    for verdict in skipped:
        print(f"SKIP {verdict.report_dir.relative_to(root)}: {'; '.join(verdict.problems)}")
    print(f"\nDELETE LIST ({len(clean)} runs, {reclaim / 2**30:.1f} GiB):")
    for verdict in clean:
        print(
            f"  {verdict.report_dir.relative_to(root)} "
            f"({verdict.reclaim_bytes / 2**20:.0f} MiB, {len(verdict.deletables)} dirs)"
        )
    if not args.apply:
        print("\ndry-run: pass --apply to delete the verified intermediates")
        return
    for verdict in clean:
        for target in verdict.deletables:
            shutil.rmtree(target)
    print(f"\ndeleted intermediates in {len(clean)} runs, freed {reclaim / 2**30:.1f} GiB")


if __name__ == "__main__":
    main()
