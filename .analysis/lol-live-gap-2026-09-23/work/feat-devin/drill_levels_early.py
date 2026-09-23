"""Early-window GRID increaseLevel cells vs livestats level, m4 + one clean map."""

import gzip
import json
from pathlib import Path
from typing import cast

import pandas as pd

from lol.constants import LOL_WINDOWS_DIR
from lol.livestats_frames import (
    assign_game_times,
    dedup_sort_frames,
    find_spawn_index,
    parse_sides,
    read_archive_payloads,
)
from shared.utils.match_time import parse_utc
from trader.game_profile import GAME_PROFILES
from trader.grid_feed import GridFrameReducer
from trader.grid_widgets import TABLE_SERVICE, parse_frame

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
LINKS = E / "data/lol/processed/lolesports_links/links.parquet"


def run(tape_name: str) -> None:
    tape = E / "data/trader" / tape_name
    meta = json.loads((tape / "match.json").read_text())
    links = pd.read_parquet(LINKS)
    link_row = links[links["condition_id"] == meta["market"]["condition_id"]].iloc[0]
    game_id = str(link_row["esports_game_id"])

    with gzip.open(tape / "grid_state.jsonl.gz", "rt") as handle:
        records = [json.loads(l) for l in handle if l.strip()]

    market = meta["market"]
    reducer = GridFrameReducer(
        meta["map_number"], market["outcome_0_name"], market["outcome_1_name"], GAME_PROFILES["lol"]
    )
    payloads = read_archive_payloads(LOL_WINDOWS_DIR, game_id)
    frames = dedup_sort_frames(payloads)
    spawn_index = find_spawn_index(frames, int(link_row["loading_anchor_ts"]))
    clock = assign_game_times(frames[spawn_index:])
    ls_levels = []
    for cf in clock.timed:
        sides = parse_sides(cf.payload)
        if isinstance(sides, int):
            continue
        lv = {p.participant_id: p.level for p in (*sides.blue.players, *sides.red.players)}
        ls_levels.append((cf.game_time, lv))

    print(f"\n##### {tape_name} (offset assumed +2.7)")
    seen = 0
    for record in records:
        frame = parse_frame(record["frame"])
        now = parse_utc(record["received_at_utc"])
        # capture raw increaseLevel cells straight from payload
        raw_cells = None
        if frame.service == TABLE_SERVICE and frame.payload:
            table = cast(dict, json.loads(frame.payload))
            for group in table["stateGroups"]:
                if group["name"] != "Game" or not group["states"]:
                    continue
                state = group["states"][-1]
                for eg in state["entityGroups"]:
                    if eg["name"] != "Player" or not eg["tables"]:
                        continue
                    raw_cells = []
                    for row in eg["tables"][0]["tableRows"]:
                        nick = row["entity"]["value"]
                        il = row.get("increaseLevel")
                        raw_cells.append((nick, il["value"] if il else None))
        event = reducer.reduce_frame(frame, now)
        if event is None or raw_cells is None or reducer._sides is None:
            continue
        snap = event.snapshot
        if snap.second > 340:
            break
        target = snap.second - 2.7
        best = min(ls_levels, key=lambda t: abs(t[0] - target))
        gt, lv = best
        grid_by_team = {}
        for p in reducer._table.players:
            grid_by_team.setdefault(p.team_id, []).append(p.level)
        ls_rad = sorted(lv[i] for i in range(1, 6))
        ls_dire = sorted(lv[i] for i in range(6, 11))
        g_rad = sorted(grid_by_team.get(reducer._sides.radiant_id, []))
        g_dire = sorted(grid_by_team.get(reducer._sides.dire_id, []))
        raw_show = [(n, v) for n, v in raw_cells][:10]
        print(
            f"S={snap.second:4d} ls≈{gt:6.1f} | grid rad{g_rad} dire{g_dire} "
            f"| ls rad{ls_rad} dire{ls_dire}"
        )
        if seen < 4:
            print("    raw increaseLevel cells:", raw_show)
        seen += 1
        if seen > 60:
            break


for t in ["grid-3000372-m4", "grid-3000375-m1"]:
    run(t)
