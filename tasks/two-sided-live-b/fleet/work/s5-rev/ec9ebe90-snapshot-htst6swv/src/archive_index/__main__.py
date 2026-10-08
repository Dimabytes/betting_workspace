"""CLI: build the archive index and feed schedules.

Default root is the trader archive (`trader=<data>/trader`). Additional roots
come in as `--root label=path`, e.g. a mounted LoL archive root.
"""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from archive_index.index import run_index
from shared.constants.paths import ARCHIVE_INDEX_DIR, TRADER_DIR


def _parse_root(value: str) -> tuple[str, Path]:
    """One `label=path` CLI root argument."""
    label, _, raw_path = value.partition("=")
    if not label or not raw_path:
        raise argparse.ArgumentTypeError(f"expected label=path, got {value!r}")
    if label in (".", "..") or "/" in label or "\\" in label:
        raise argparse.ArgumentTypeError(f"label is a schedule path component: {label!r}")
    path = Path(raw_path)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"archive root is not a directory: {path}")
    return label, path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="archive_index",
        description="Scan match archives, audit them, and publish feed schedules.",
    )
    parser.add_argument(
        "--root",
        action="append",
        type=_parse_root,
        default=None,
        metavar="LABEL=PATH",
        help="archive root to scan; repeatable (default: trader=<data>/trader)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ARCHIVE_INDEX_DIR,
        help="artifact root for index.parquet, report.md, schedules/ (default: %(default)s)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=min(10, os.cpu_count() or 1),
        help="extraction worker processes (default: %(default)s; 1 = serial)",
    )
    args = parser.parse_args(argv)
    roots = dict(args.root) if args.root else {"trader": TRADER_DIR}
    summary = run_index(roots, args.out, args.jobs)
    print(
        f"archives={summary.meta_ok} admitted={summary.admitted} "
        f"schedules={summary.schedules_published} out={args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
