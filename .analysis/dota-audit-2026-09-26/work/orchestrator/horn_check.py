"""Archive-measured horn vs GRID-derived horn (startedAt + 90 s + pre-horn pauses) on catalog maps with both."""
import json

import pandas as pd

from shared.utils.match_time import get_horn_datetime, get_paused_seconds_before, parse_utc

g = pd.read_parquet("data/new_processed/grid_game_starts/grid_game_windows.parquet").drop_duplicates("condition_id").set_index("condition_id")
c = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet")
a = c[c.horn_source == "archive"].copy()
rows = []
for _, x in a.iterrows():
    if x.condition_id not in g.index:
        continue
    pauses = json.loads(x.pauses_json) if isinstance(x.pauses_json, str) else []
    grid_start = parse_utc(g.loc[x.condition_id, "spawn_at"])
    grid_horn = get_horn_datetime(grid_start, pauses)
    archive_horn = parse_utc(x.horn_at)
    rows.append(dict(
        match_id=x.match_id,
        diff=round((archive_horn - grid_horn).total_seconds(), 1),
        pre_horn_pause=get_paused_seconds_before(pauses, 0),
        neg_pauses=sum(p["duration"] for p in pauses if p["time"] < 0),
        n_pauses=len(pauses),
        pauses=[(p["time"], p["duration"]) for p in pauses][:4],
        catalog_spawn=str(x.spawn_at)[:19],
        grid_spawn=str(g.loc[x.condition_id, "spawn_at"])[:19],
        archive=x.archive_id,
    ))
df = pd.DataFrame(rows).sort_values("diff")
pd.set_option("display.width", 260)
pd.set_option("display.max_colwidth", 60)
print(len(df), "maps")
print(df.head(25).to_string())
print("corr(diff, -pre_horn_pause) =", round(df["diff"].corr(-df.pre_horn_pause), 3))
print("diff + pre_horn_pause:")
print((df["diff"] + df.pre_horn_pause).describe().round(2))
print("maps |diff|>5s:", int((df["diff"].abs() > 5).sum()), " of which pre_horn_pause>0:", int(((df["diff"].abs() > 5) & (df.pre_horn_pause > 0)).sum()))
