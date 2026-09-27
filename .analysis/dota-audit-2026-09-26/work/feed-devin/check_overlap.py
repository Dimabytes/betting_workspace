"""Find local trader archives that also exist in game_features.parquet."""
import json
from pathlib import Path

import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data" / "trader"

features = pd.read_parquet(
    E / "data/new_processed/dataset/game_features.parquet", columns=["match_id"]
)
feature_ids = set(features["match_id"].tolist())
print("feature matches:", len(feature_ids))

rows = []
for d in sorted(TRADER.iterdir()):
    meta = d / "match.json"
    if not meta.exists():
        continue
    try:
        m = json.loads(meta.read_text())
    except Exception:
        continue
    steam_id = m.get("steam_match_id") or ""
    try:
        sid = int(steam_id)
    except (TypeError, ValueError):
        sid = None
    rows.append(
        dict(
            dir=d.name,
            feed=m.get("feed_source"),
            sid=sid,
            in_features=sid in feature_ids if sid else False,
            has_grid=(d / "grid_state.jsonl.gz").exists() or (d / "grid_state.jsonl").exists(),
            has_oddin=(d / "oddin_state.jsonl.gz").exists() or (d / "oddin_state.jsonl").exists(),
            has_session=(d / "session.jsonl").exists() or (d / "session.jsonl.gz").exists(),
            joined=m.get("joined_at_second"),
            horn=m.get("horn_at_utc"),
            map_number=m.get("map_number"),
        )
    )
df = pd.DataFrame(rows)
print(df.groupby(["feed", "in_features"]).size())
overlap = df[df["in_features"]]
print("overlap dirs:", len(overlap))
print(overlap.to_string())
