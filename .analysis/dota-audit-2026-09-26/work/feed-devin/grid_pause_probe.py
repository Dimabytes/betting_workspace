"""Scan grid_state archives: does GRID currentSeconds freeze when isTicking=false?
Also: how do clock_seconds, isTicking, occurred_at behave across a pause."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src")

from trader.grid_archive import iter_grid_archive_records
from trader.grid_widgets import parse_frame, read_map_scoreboard
from shared.utils.match_time import parse_utc

TRADER = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/trader")


def scan(d: Path, map_number: int):
    boards = []
    for record in iter_grid_archive_records(d / "grid_state.jsonl.gz"):
        frame = parse_frame(record["frame"])
        if frame.service != "series_scoreboard_v2" or not frame.payload:
            continue
        board = read_map_scoreboard(frame.payload, map_number)
        if board is None:
            continue
        boards.append(
            dict(
                recv=record["received_at_utc"],
                occurred=board.occurred_at,
                clock=board.clock_seconds,
                ticking=board.clock_ticking,
                status=board.game_status,
            )
        )
    return boards


def main():
    dirs = sys.argv[1:]
    rows_out = []
    for name in dirs:
        d = TRADER / name
        meta = json.loads((d / "match.json").read_text())
        boards = scan(d, meta["map_number"])
        # find isTicking False stretches while status==live
        pause_boards = [b for b in boards if not b["ticking"] and b["status"] == "live"]
        print(f"\n=== {name} map={meta['map_number']} boards={len(boards)} pause_boards={len(pause_boards)}")
        if pause_boards:
            # print board sequence around the first pause
            i0 = boards.index(pause_boards[0])
            for b in boards[max(0, i0 - 2) : i0 + 40]:
                print("   ", b)
            # does clock advance during pause?
            clocks = [b["clock"] for b in pause_boards]
            occs = [parse_utc(b["occurred"]).timestamp() for b in pause_boards]
            print(
                f"    pause clock range {min(clocks)}..{max(clocks)} "
                f"occurred span {occs[-1]-occs[0]:.0f}s n={len(pause_boards)}"
            )
            if len(set(clocks)) > 1:
                print("    >>> CLOCK MOVED WHILE isTicking=false <<<")
            else:
                print("    clock frozen during pause")


main()
