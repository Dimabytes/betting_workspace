"""Drill into grid-3000372-m4 xp_adv anomaly: per-player GRID increaseLevel+1
vs livestats level at aligned seconds."""

import gzip
import json
import statistics
import sys
from pathlib import Path
from typing import cast

import pandas as pd

from lol.constants import LOL_DETAILS_DIR, LOL_WINDOWS_DIR
from lol.livestats_frames import (
    assign_game_times,
    dedup_sort_frames,
    find_spawn_index,
    game_patch_from_payloads,
    parse_sides,
    read_archive_payloads,
)
from shared.utils.match_time import parse_utc
from trader.game_profile import GAME_PROFILES
from trader.grid_feed import GridFrameReducer
from trader.grid_widgets import TABLE_SERVICE, parse_frame, read_net_worth

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TAPE = E / "data/trader/grid-3000372-m4"
LINKS = E / "data/lol/processed/lolesports_links/links.parquet"


def main() -> None:
    meta = json.loads((TAPE / "match.json").read_text())
    links = pd.read_parquet(LINKS)
    link_row = links[links["condition_id"] == meta["market"]["condition_id"]].iloc[0]
    game_id = str(link_row["esports_game_id"])

    with gzip.open(TAPE / "grid_state.jsonl.gz", "rt") as handle:
        records = [json.loads(l) for l in handle if l.strip()]

    market = meta["market"]
    reducer = GridFrameReducer(
        meta["map_number"], market["outcome_0_name"], market["outcome_1_name"], GAME_PROFILES["lol"]
    )

    payloads = read_archive_payloads(LOL_WINDOWS_DIR, game_id)
    frames = dedup_sort_frames(payloads)
    spawn_index = find_spawn_index(frames, int(link_row["loading_anchor_ts"]))
    clock = assign_game_times(frames[spawn_index:])
    # livestats per-player levels by game_time
    ls_levels: list[tuple[float, dict[int, int]]] = []
    for cf in clock.timed:
        sides = parse_sides(cf.payload)
        if isinstance(sides, int):
            continue
        lv = {p.participant_id: p.level for p in (*sides.blue.players, *sides.red.players)}
        ls_levels.append((cf.game_time, lv))

    emitted = 0
    for record in records:
        frame = parse_frame(record["frame"])
        now = parse_utc(record["received_at_utc"])
        event = reducer.reduce_frame(frame, now)
        if event is None or reducer._sides is None:
            continue
        emitted += 1
        snap = event.snapshot
        if snap.second < 200 or snap.second > 560:
            continue
        # find the table that produced this snapshot: it's reducer._table
        table = reducer._table
        if table is None:
            continue
        target = snap.second - 2.7  # measured offset
        best = min(ls_levels, key=lambda t: abs(t[0] - target))
        if abs(best[0] - target) > 2.0:
            continue
        gt, lv = best
        rad = [p for p in table.players if p.team_id == reducer._sides.radiant_id]
        dire = [p for p in table.players if p.team_id == reducer._sides.dire_id]
        rad_lv = sorted(p.level for p in rad)
        dire_lv = sorted(p.level for p in dire)
        ls_rad = sorted(lv[i] for i in range(1, 6))
        ls_dire = sorted(lv[i] for i in range(6, 11))
        print(
            f"snap.second={snap.second:4d} (ls≈{gt:6.1f})  "
            f"grid rad {rad_lv} dire {dire_lv} | ls rad {ls_rad} dire {ls_dire} "
            f"| xp_adv grid={snap.radiant_xp_adv} "
        )
        if emitted > 400:
            break


if __name__ == "__main__":
    main()
