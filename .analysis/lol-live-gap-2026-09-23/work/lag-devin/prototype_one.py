"""Prototype: event-level clock alignment for one LoL map (grid-2964617-m1)."""

import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(E / "src"))

from lol.livestats_frames import (  # noqa: E402
    assign_game_times,
    dedup_sort_frames,
    find_spawn_index,
    parse_sides,
    read_archive_payloads,
)
from trader.grid_widgets import (  # noqa: E402
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    parse_frame,
    read_map_scoreboard,
    read_net_worth,
)

MATCH_DIR = E / "data/trader/grid-2964617-m1"
EGID = "115564797163821176"
ANCHOR_TS = None  # fill from links
WINDOWS = E / "data/lol/raw/lolesports/windows"


def parse_iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main() -> None:
    import pandas as pd

    links = pd.read_parquet(E / "data/lol/processed/lolesports_links/links.parquet")
    link = links[links.esports_game_id.astype(str) == EGID].iloc[0]
    anchor_ts = int(link["loading_anchor_ts"])
    print("anchor", link["loading_anchor"], anchor_ts, "game_number", link["game_number"])

    match = json.loads((MATCH_DIR / "match.json").read_text())
    map_number = match["map_number"]
    print("map_number", map_number, match["teams"])

    payloads = read_archive_payloads(WINDOWS, EGID)
    frames = dedup_sort_frames(payloads)
    print("livestats frames:", len(frames), frames[0].wall_seconds, frames[-1].wall_seconds)
    spawn_idx = find_spawn_index(frames, anchor_ts)
    print("spawn_idx", spawn_idx, "spawn wall", frames[spawn_idx].wall_seconds if spawn_idx else None)
    clock = assign_game_times(frames[spawn_idx:])
    print("timed:", len(clock.timed), "pauses", clock.pause_count, clock.pause_seconds)

    # livestats death events: total deaths per side (sum participant deaths)
    ls_events = []  # (counter_value, wall_seconds, game_time, side)
    prev = {"blue": 0, "red": 0}
    for cf in clock.timed:
        sides = parse_sides(cf.payload)
        if isinstance(sides, int):
            continue
        for side, team in (("blue", sides.blue), ("red", sides.red)):
            deaths = sum(p.deaths for p in team.players)
            if deaths > prev[side]:
                for c in range(prev[side] + 1, deaths + 1):
                    ls_events.append(
                        {"counter": c, "side": side, "ls_wall": cf.wall_seconds, "ls_gt": cf.game_time}
                    )
                prev[side] = deaths
    print("livestats death events:", len(ls_events))
    for e in ls_events[:8]:
        print("  ls", e)

    # GRID events
    sb_events = []  # scoreboard kill increments
    tb_events = []  # table death increments
    team_side = {}  # team_id -> BLUE/RED
    prev_kills = {"BLUE": 0, "RED": 0}
    prev_tb_deaths = {"BLUE": 0, "RED": 0}
    sb_frames = 0
    tb_frames = 0
    clock_rows = []  # (recv, occurredAt, currentSeconds, ticking)
    path = MATCH_DIR / "grid_state.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            recv = parse_iso(rec["received_at_utc"])
            try:
                frame = parse_frame(rec["frame"])
            except Exception:
                continue
            if not frame.payload:
                continue
            if frame.service == SCOREBOARD_SERVICE:
                board = read_map_scoreboard(frame.payload, map_number)
                if board is None:
                    continue
                sb_frames += 1
                for team in board.teams:
                    team_side[team.team_id] = team.side
                clock_rows.append(
                    (recv, board.occurred_at, board.clock_seconds, board.clock_ticking, board.game_status)
                )
                for side in ("BLUE", "RED"):
                    kills = sum(t.kills for t in board.teams if t.side == side)
                    if kills > prev_kills[side]:
                        for c in range(prev_kills[side] + 1, kills + 1):
                            sb_events.append(
                                {
                                    "counter": c,
                                    "side": side,
                                    "sb_recv": recv,
                                    "sb_occ": parse_iso(board.occurred_at),
                                    "sb_clock": board.clock_seconds,
                                }
                            )
                        prev_kills[side] = kills
            elif frame.service == TABLE_SERVICE:
                table = read_net_worth(frame.payload, frame.delay)
                if table is None or table.game_number != map_number:
                    continue
                tb_frames += 1
                for side in ("BLUE", "RED"):
                    deaths = sum(
                        p.deaths for p in table.players if team_side.get(p.team_id) == side
                    )
                    if deaths > prev_tb_deaths[side]:
                        for c in range(prev_tb_deaths[side] + 1, deaths + 1):
                            tb_events.append({"counter": c, "side": side, "tb_recv": recv})
                        prev_tb_deaths[side] = deaths
    print("sb_frames", sb_frames, "tb_frames", tb_frames)
    print("sb_events", len(sb_events), "tb_events", len(tb_events))
    for e in sb_events[:8]:
        print("  sb", e)
    for e in tb_events[:8]:
        print("  tb", e)

    # clock_rows: sample of (recv, occ, clock) around first kills
    for r in clock_rows[:3]:
        print("  clock", r)
    for r in clock_rows[-3:]:
        print("  clock", r)

    # join events on (counter, side): livestats deaths of side X == GRID kills of the OTHER side
    # livestats deaths_blue increments == blue players died == red team kills (sb kills RED)
    other = {"blue": "RED", "red": "BLUE"}
    ls_idx = {(e["counter"], e["side"]): e for e in ls_events}
    sb_idx = {(e["counter"], e["side"]): e for e in sb_events}
    tb_idx = {(e["counter"], e["side"]): e for e in tb_events}
    print("\ncount side | ls_wall | sb_recv-ls | sb_occ-ls | tb_recv-ls | sb_clock - ls_gt")
    rows = []
    for (c, ls_side), le in sorted(ls_idx.items()):
        se = sb_idx.get((c, other[ls_side]))
        te = tb_idx.get((c, other[ls_side]))
        if se is None:
            continue
        row = {
            "c": c,
            "dead_side": ls_side,
            "ls_wall": le["ls_wall"],
            "ls_gt": le["ls_gt"],
            "recv_minus_ls": se["sb_recv"] - le["ls_wall"],
            "occ_minus_ls": se["sb_occ"] - le["ls_wall"],
            "tb_minus_ls": (te["tb_recv"] - le["ls_wall"]) if te else None,
            "clock_minus_gt": se["sb_clock"] - le["ls_gt"],
        }
        rows.append(row)
        print(
            f"{c:3d} {ls_side:4s} | {le['ls_wall']:.1f} | {row['recv_minus_ls']:+8.1f} | "
            f"{row['occ_minus_ls']:+8.1f} | {row['tb_minus_ls'] if te else float('nan'):+8.1f} | "
            f"{row['clock_minus_gt']:+8.1f}"
        )
    import statistics

    for k in ("recv_minus_ls", "occ_minus_ls", "tb_minus_ls", "clock_minus_gt"):
        vals = [r[k] for r in rows if r[k] is not None]
        if vals:
            print(k, "n", len(vals), "median", statistics.median(vals), "min", min(vals), "max", max(vals))


if __name__ == "__main__":
    main()
