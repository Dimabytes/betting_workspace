"""Check (a) STRATZ cache profiles for catalog ids, (b) series_state staleness."""
import gzip
import json
from pathlib import Path

import pandas as pd

cat = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet")
ids = set(cat["match_id"].astype(int))

RAW = Path("data/raw/stratz_matches")
profiles = {}
missing_stats = []
for mid in ids:
    p = RAW / f"match_{mid}.json.gz"
    if not p.exists():
        profiles[mid] = "MISSING"
        continue
    with gzip.open(p, "rt") as f:
        payload = json.load(f)
    profiles[mid] = payload.get("cache_profile")
    m = (payload.get("data") or {}).get("match") or {}
    for pl in m.get("players") or []:
        if "stats" not in pl:
            missing_stats.append(mid)
            break

from collections import Counter
print("catalog cache profiles:", Counter(profiles.values()))
print("catalog matches with a player missing stats:", len(set(missing_stats)))

# ---- series_state staleness ----
sd = Path("data/raw/polymarket_dota/grid_game_starts/series_state")
stale = 0
total = 0
unfinished_games = 0
started_no_startedAt = 0
for p in sorted(sd.glob("*.json")):
    payload = json.load(open(p))
    ss = (payload.get("data") or {}).get("seriesState")
    if ss is None:
        continue
    total += 1
    if not ss.get("finished"):
        stale += 1
    for g in ss.get("games") or []:
        if not g.get("finished"):
            unfinished_games += 1
        if g.get("started") and not g.get("startedAt"):
            started_no_startedAt += 1
print(f"series_state files={total} not-finished={stale} unfinished games inside={unfinished_games} started-but-no-startedAt={started_no_startedAt}")
