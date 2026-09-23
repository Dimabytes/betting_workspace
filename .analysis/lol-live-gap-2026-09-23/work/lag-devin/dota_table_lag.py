"""Dota GRID table lag: match series_table death increments to scoreboard kills.

A RADIANT-side kill in the scoreboard is a DIRE death in the table. The table
frame has no occurredAt; the scoreboard kill's occurredAt is the event time.
team_id -> side is learned from the scoreboard, same as the LoL measurement.
"""

import gzip
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(E / "src"))
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/lag-devin")

from trader.grid_widgets import (  # noqa: E402
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    parse_frame,
    read_map_scoreboard,
    read_net_worth,
)


def parse_iso(s):
    if not s:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def measure_map(match_dir: Path, map_number: int) -> list[dict]:
    path = match_dir / "grid_state.jsonl.gz"
    if not path.is_file():
        path = match_dir / "grid_state.jsonl"
        if not path.is_file():
            return []
    opener = gzip.open if path.suffix == ".gz" else open
    kills = {"RADIANT": 0, "DIRE": 0}
    deaths = {"RADIANT": 0, "DIRE": 0}
    team_side: dict[str, str] = {}
    pending: dict[tuple[str, int], float] = {}
    rows = []
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
                recv = parse_iso(rec["received_at_utc"])
                frame = parse_frame(rec["frame"])
            except Exception:
                continue
            if recv is None or not frame.payload:
                continue
            if frame.service == SCOREBOARD_SERVICE:
                try:
                    board = read_map_scoreboard(frame.payload, map_number)
                except Exception:
                    continue
                if board is None:
                    continue
                occ = parse_iso(board.occurred_at)
                for team in board.teams:
                    team_side[team.team_id] = team.side
                for side in ("RADIANT", "DIRE"):
                    k = sum(t.kills for t in board.teams if t.side == side)
                    if k > kills[side]:
                        for c in range(kills[side] + 1, k + 1):
                            dead = "DIRE" if side == "RADIANT" else "RADIANT"
                            pending[(dead, c)] = occ
                        kills[side] = k
            elif frame.service == TABLE_SERVICE:
                try:
                    table = read_net_worth(frame.payload, frame.delay)
                except Exception:
                    continue
                if table is None or table.game_number != map_number:
                    continue
                for side in ("RADIANT", "DIRE"):
                    d = sum(
                        p.deaths for p in table.players if team_side.get(p.team_id) == side
                    )
                    if d > deaths[side]:
                        for c in range(deaths[side] + 1, d + 1):
                            occ = pending.pop((side, c), None)
                            if occ is not None:
                                rows.append({"match_id": match_dir.name, "lag": recv - occ})
                        deaths[side] = d
    return rows


def process(match_id: str) -> list[dict]:
    match_dir = E / "data/trader" / match_id
    try:
        meta = json.loads((match_dir / "match.json").read_text())
    except Exception:
        return []
    return measure_map(match_dir, int(meta["map_number"]))


def main() -> None:
    ids = []
    for d in sorted((E / "data/trader").iterdir()):
        mp = d / "match.json"
        if not mp.is_file():
            continue
        try:
            if json.loads(mp.read_text()).get("game") == "dota":
                ids.append(d.name)
        except Exception:
            continue
    print("dota maps:", len(ids))
    rows = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        for res in pool.map(process, ids, chunksize=4):
            rows.extend(res)
    df = pd.DataFrame(rows)
    df.to_parquet(OUT / "dota_table_lags.parquet")
    s = df["lag"].dropna()
    print(
        f"dota table death lag vs scoreboard occ: n={len(s)} "
        f"p10={s.quantile(.1):.2f} med={s.median():.2f} mean={s.mean():.2f} p90={s.quantile(.9):.2f}"
    )


if __name__ == "__main__":
    main()
