"""Scan ALL grid archives for live+!ticking runs of any length."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src")

from trader.grid_archive import iter_grid_archive_records
from trader.grid_widgets import parse_frame, read_map_scoreboard
from shared.utils.match_time import parse_utc

TRADER = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/trader")


def main():
    found = []
    scanned = 0
    for d in sorted(TRADER.iterdir()):
        meta_p = d / "match.json"
        if not meta_p.exists():
            continue
        try:
            meta = json.loads(meta_p.read_text())
        except Exception:
            continue
        if meta.get("feed_source") != "grid":
            continue
        gfile = d / "grid_state.jsonl.gz"
        if not gfile.exists():
            continue
        scanned += 1
        boards = []
        for record in iter_grid_archive_records(gfile):
            frame = parse_frame(record["frame"])
            if frame.service != "series_scoreboard_v2" or not frame.payload:
                continue
            board = read_map_scoreboard(frame.payload, meta["map_number"])
            if board is None:
                continue
            boards.append(board)
        # contiguous live & !ticking runs
        cur = []
        for b in boards:
            if b.game_status == "live" and not b.clock_ticking:
                cur.append(b)
            else:
                if cur:
                    span = (
                        parse_utc(cur[-1].occurred_at) - parse_utc(cur[0].occurred_at)
                    ).total_seconds()
                    found.append((d.name, span, cur[0].clock_seconds, cur[-1].clock_seconds, len(cur)))
                    cur = []
        if cur:
            span = (
                parse_utc(cur[-1].occurred_at) - parse_utc(cur[0].occurred_at)
            ).total_seconds()
            found.append((d.name, span, cur[0].clock_seconds, cur[-1].clock_seconds, len(cur)))
    print(f"scanned {scanned} grid archives; live+!ticking runs: {len(found)}")
    for row in sorted(found, key=lambda r: -r[1])[:40]:
        print("  match=%s span=%.0fs clock %d..%d boards=%d" % row)


main()
