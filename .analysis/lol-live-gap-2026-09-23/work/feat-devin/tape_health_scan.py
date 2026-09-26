"""Scan recent LoL tapes: null cells, team sizes, tick-vs-signal coverage."""

import gzip
import json
import sys
from pathlib import Path
from typing import cast

from trader.grid_widgets import TABLE_SERVICE, parse_frame, read_net_worth

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data/trader"


def scan(dirpath: Path) -> None:
    path = dirpath / "grid_state.jsonl.gz"
    if not path.exists():
        path = dirpath / "grid_state.jsonl"
    opener = gzip.open if str(path).endswith(".gz") else open
    tables = 0
    rows_total = 0
    null_nw = null_deaths = null_kills = null_il = no_il_col = 0
    team_sizes = {}
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            frame = parse_frame(json.loads(line)["frame"])
            if frame.service != TABLE_SERVICE or not frame.payload:
                continue
            tables += 1
            payload = cast(dict, json.loads(frame.payload))
            for group in payload["stateGroups"]:
                if group["name"] != "Game" or not group["states"]:
                    continue
                for state in group["states"]:
                    for eg in state["entityGroups"]:
                        if eg["name"] != "Player" or not eg["tables"]:
                            continue
                        for row in eg["tables"][0]["tableRows"]:
                            rows_total += 1
                            if "increaseLevel" not in row:
                                no_il_col += 1
                            elif row["increaseLevel"].get("value") is None:
                                null_il += 1
                            for cell in ("NetWorth", "Deaths", "Kills"):
                                if row[cell].get("value") is None:
                                    if cell == "NetWorth":
                                        null_nw += 1
                                    elif cell == "Deaths":
                                        null_deaths += 1
                                    else:
                                        null_kills += 1
            snap = read_net_worth(frame.payload, frame.delay)
            if snap is None:
                continue
            per_team = {}
            for p in snap.players:
                per_team[p.team_id] = per_team.get(p.team_id, 0) + 1
            key = tuple(sorted(per_team.values()))
            team_sizes[key] = team_sizes.get(key, 0) + 1
    print(
        f"{dirpath.name}: tables={tables} player_rows={rows_total} "
        f"nullNW={null_nw} nullDeaths={null_deaths} nullKills={null_kills} "
        f"il_absent={no_il_col} il_null={null_il} team_size_dist={team_sizes}"
    )


for name in sys.argv[1:]:
    scan(TRADER / name)
