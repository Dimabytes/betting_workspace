"""Find contiguous live+!ticking board runs in GRID archives and check whether the
GRID clock advances inside a pause."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src")

from trader.grid_archive import iter_grid_archive_records
from trader.grid_widgets import parse_frame, read_map_scoreboard
from shared.utils.match_time import parse_utc

TRADER = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/trader")


def boards_of(d: Path, map_number: int):
    out = []
    for record in iter_grid_archive_records(d / "grid_state.jsonl.gz"):
        frame = parse_frame(record["frame"])
        if frame.service != "series_scoreboard_v2" or not frame.payload:
            continue
        board = read_map_scoreboard(frame.payload, map_number)
        if board is None:
            continue
        out.append(board)
    return out


def main():
    names = sys.argv[1:]
    if not names:
        names = [p.name for p in sorted(TRADER.iterdir()) if (p / "match.json").exists()][:80]
    total_runs = 0
    moved = 0
    longest_summary = []
    for name in names:
        d = TRADER / name
        try:
            meta = json.loads((d / "match.json").read_text())
        except Exception:
            continue
        if meta.get("feed_source") != "grid":
            continue
        try:
            boards = boards_of(d, meta["map_number"])
        except FileNotFoundError:
            continue
        # contiguous runs of live + !ticking
        runs = []
        cur = []
        for b in boards:
            if b.game_status == "live" and not b.clock_ticking:
                cur.append(b)
            else:
                if cur:
                    runs.append(cur)
                    cur = []
        if cur:
            runs.append(cur)
        for run in runs:
            span = (
                parse_utc(run[-1].occurred_at) - parse_utc(run[0].occurred_at)
            ).total_seconds()
            if span < 5:
                continue
            total_runs += 1
            clocks = [b.clock_seconds for b in run]
            if len(set(clocks)) > 1:
                moved += 1
                longest_summary.append(
                    (name, span, min(clocks), max(clocks), len(run))
                )
    print(f"pause runs (live & !ticking, >5s span): {total_runs}; clock moved inside: {moved}")
    for row in sorted(longest_summary, key=lambda r: -r[1])[:20]:
        print("  match=%s span=%.0fs clock %d..%d boards=%d" % row)


main()
