"""Independent clock: live match.json horn (Steam or GRID) vs GRID startedAt + 90 s, with and without pre-horn pauses."""
import json
from pathlib import Path

import pandas as pd

from shared.utils.match_time import get_horn_datetime, get_paused_seconds_before, parse_utc

g = pd.read_parquet("data/new_processed/grid_game_starts/grid_game_windows.parquet").drop_duplicates("condition_id").set_index("condition_id")
c = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet").drop_duplicates("condition_id").set_index("condition_id")
rows = []
for meta_path in sorted(Path("data/trader").glob("*/match.json")):
    try:
        meta = json.loads(meta_path.read_text())
    except Exception:
        continue
    if meta.get("game", "dota") != "dota":
        continue
    cid = (meta.get("market") or {}).get("condition_id")
    horn = meta.get("horn_at_utc")
    if cid is None or horn is None or cid not in g.index or cid not in c.index:
        continue
    pauses = json.loads(c.loc[cid, "pauses_json"]) if isinstance(c.loc[cid, "pauses_json"], str) else []
    started = parse_utc(g.loc[cid, "spawn_at"])
    live_horn = parse_utc(horn)
    pre = get_paused_seconds_before(pauses, 0)
    rows.append(dict(
        dir=meta_path.parent.name,
        feed=meta.get("feed_source"),
        pre_horn_pause=pre,
        live_minus_start90=round((live_horn - started).total_seconds() - 90, 1),
        live_minus_formula=round((live_horn - get_horn_datetime(started, pauses)).total_seconds(), 1),
    ))
df = pd.DataFrame(rows)
print(len(df), "live Dota archives with a GRID start and catalog pauses")
for feed, part in df.groupby("feed"):
    with_pause = part[part.pre_horn_pause > 0]
    print(f"feed={feed}: maps={len(part)} with_pre_horn_pause={len(with_pause)}")
    print("  live - (start+90)         all:", part.live_minus_start90.describe()[["25%", "50%", "75%"]].round(1).to_dict())
    if len(with_pause):
        print("  live - (start+90)   paused maps:", with_pause.live_minus_start90.describe()[["min", "25%", "50%", "75%", "max"]].round(1).to_dict())
        print("  live - formula      paused maps:", with_pause.live_minus_formula.describe()[["min", "25%", "50%", "75%", "max"]].round(1).to_dict())
