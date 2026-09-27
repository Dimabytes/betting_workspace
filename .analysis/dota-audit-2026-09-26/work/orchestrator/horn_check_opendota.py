"""Third clock: OpenDota start_time (+ pre_game_duration) vs live horn and vs GRID startedAt, on archive-horn maps."""
import gzip
import json
from pathlib import Path

import pandas as pd

from shared.utils.match_time import get_paused_seconds_before, parse_utc

g = pd.read_parquet("data/new_processed/grid_game_starts/grid_game_windows.parquet").drop_duplicates("condition_id").set_index("condition_id")
c = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet")
rows = []
for _, x in c.iterrows():
    path = Path(f"data/raw/opendota_matches/match_{int(x.match_id)}.json.gz")
    if not path.exists() or x.condition_id not in g.index:
        continue
    od = json.load(gzip.open(path))
    od = od.get("data", od)
    if not od.get("start_time"):
        continue
    pauses = json.loads(x.pauses_json) if isinstance(x.pauses_json, str) else []
    pre = get_paused_seconds_before(pauses, 0)
    started = parse_utc(g.loc[x.condition_id, "spawn_at"]).timestamp()
    rows.append(dict(
        match_id=x.match_id, source=x.horn_source, pre=pre,
        od_start_minus_grid=od["start_time"] - started,
        live_minus_od=(parse_utc(x.horn_at).timestamp() - od["start_time"]) if x.horn_source == "archive" else None,
        pgd=od.get("pre_game_duration"),
    ))
df = pd.DataFrame(rows)
print(len(df), "catalog maps with OpenDota start_time and GRID start")
print("OpenDota start_time - GRID startedAt (s):")
print(df.groupby(df.pre > 0).od_start_minus_grid.describe().round(1))
a = df[df.source == "archive"]
print("archive horn - OpenDota start_time (s), by pre-horn pause present:")
print(a.groupby(a.pre > 0).live_minus_od.describe().round(1))
print("pre_game_duration values:", df.pgd.value_counts().head(5).to_dict())
