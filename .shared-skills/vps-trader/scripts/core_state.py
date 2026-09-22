#!/usr/bin/env python3
"""Replay a match core trace and print the strategy state the journals hide.

`session.jsonl` records fills and the places and cancels that reached the venue.
An order whose place never got there, or whose cancel the venue never proved, is
in neither tape. This prints that state, the plan `requote` would make on the
next wake, and a VERDICT line naming any stuck order.

The report itself lives in the trader repo (`src/trader/core_state_report.py`),
so it moves with the code it reads. This wrapper only resolves VPS paths.

    cd /root/work/esports-trader
    PYTHONPATH=src uv run python \\
      /root/work/betting_workspace/.shared-skills/vps-trader/scripts/core_state.py <match_id>
    PYTHONPATH=src uv run python \\
      /root/work/betting_workspace/.shared-skills/vps-trader/scripts/core_state.py --all --quiet

Exit code is 1 when any match is wedged, so it greps and cron-checks cleanly.
"""

import argparse
import sys
from pathlib import Path

from trader.core_state_report import (
    STUCK_ORDER_SECONDS,
    build_report,
    format_report,
    format_verdict,
)
from trader.paths import CORE_TRACE_FILENAME

DATA_ROOT = Path("/root/work/esports-trader/data")
LIVE_ROOT = DATA_ROOT / "trader_live"
PAPER_ROOT = DATA_ROOT / "trader_paper"


def find_trace(target: str) -> Path:
    """Resolve a directory, or a match id under the live then the paper root."""
    given = Path(target)
    if given.is_dir():
        return given / CORE_TRACE_FILENAME
    for root in (LIVE_ROOT, PAPER_ROOT):
        candidate = root / target / CORE_TRACE_FILENAME
        if candidate.exists():
            return candidate
    raise SystemExit(f"no {CORE_TRACE_FILENAME} for {target} (looked in {LIVE_ROOT}, {PAPER_ROOT})")


def recent_traces(root: Path, limit: int) -> list[Path]:
    """Traces under one root, newest write first. The wallet directory is not a match."""
    traces = [
        child / CORE_TRACE_FILENAME
        for child in root.iterdir()
        if child.is_dir() and child.name != "wallet" and (child / CORE_TRACE_FILENAME).exists()
    ]
    traces.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return traces[:limit]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="core_state")
    parser.add_argument("target", nargs="*", help="match id or archive directory")
    parser.add_argument("--all", action="store_true", help="every recent match under a root")
    parser.add_argument("--paper", action="store_true", help="use the paper root for --all")
    parser.add_argument("--limit", type=int, default=6, help="how many matches --all takes")
    parser.add_argument("--quiet", action="store_true", help="print the verdict line only")
    parser.add_argument("--policy", choices=("archive", "current"), default="archive")
    parser.add_argument("--max-idle-s", type=float, default=STUCK_ORDER_SECONDS)
    parser.add_argument("--tail", type=int, default=10, help="how many acting plans to show")
    args = parser.parse_args(argv)
    if args.all:
        traces = recent_traces(PAPER_ROOT if args.paper else LIVE_ROOT, args.limit)
    elif args.target:
        traces = [find_trace(target) for target in args.target]
    else:
        parser.error("give a match id, a directory, or --all")
    wedged = False
    for trace in traces:
        report = build_report(
            path=trace,
            use_current_policy=args.policy == "current",
            max_idle_s=args.max_idle_s,
            tail=0 if args.quiet else args.tail,
        )
        wedged = wedged or report.wedged
        if args.quiet:
            print(format_verdict(report))
            continue
        print("\n".join(format_report(report)))
        print("")
    return 1 if wedged else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
