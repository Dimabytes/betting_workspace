"""Player-level playbackData.playerUpdateGoldEvents coverage among catalog matches."""
import pandas as pd

from shared.constants.paths import MATCH_CATALOG_PATH
from shared.utils.json_io import read_gzip_json
from shared.utils.stratz import stratz_match_cache_path

cat = pd.read_parquet(MATCH_CATALOG_PATH)
split = pd.read_parquet("data/new_model/research/split.parquet")
val_ids = set(split.loc[split["split"] == "validation", "match_id"].astype(int))
train_ids = set(split.loc[split["split"] == "train", "match_id"].astype(int))
print(f"catalog={len(cat)} val={len(val_ids)} train={len(train_ids)}")

stats = {"full": 0, "empty_events": 0, "no_pb": 0, "some_missing": 0}
val_stats = {"full": 0, "empty_events": 0, "no_pb": 0, "some_missing": 0}
empty_ids = []
low_events = []
for mid in cat["match_id"].astype(int):
    entry = read_gzip_json(stratz_match_cache_path(mid))
    match = (entry.get("data") or {}).get("match") or {}
    n_players = len(match.get("players") or [])
    n_with_events = 0
    total_events = 0
    for p in match.get("players") or []:
        pb = p.get("playbackData") or {}
        evs = pb.get("playerUpdateGoldEvents") or []
        if evs:
            n_with_events += 1
            total_events += len(evs)
    bucket = val_stats if mid in val_ids else stats
    if n_players == 0:
        bucket["no_pb"] += 1
    elif n_with_events == 0:
        bucket["empty_events"] += 1
        empty_ids.append(mid)
    elif n_with_events < n_players:
        bucket["some_missing"] += 1
    else:
        bucket["full"] += 1
        if total_events < 200:
            low_events.append((mid, total_events, n_players))

print("non-validation catalog:", stats)
print("validation catalog:", val_stats)
print("empty gold-event ids:", empty_ids[:20])
print("suspiciously few events:", low_events[:20])
