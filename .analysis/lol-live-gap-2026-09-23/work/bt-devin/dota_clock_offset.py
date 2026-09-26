"""Dota: dataset state_ts_us vs archive feed received_ns offset (same method as LoL)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
SCHED_DIR = E / "data/archive_index/schedules/trader/dota"
RES = E / "data/backtests/dota_maker/LIVE/seed0/results.parquet"
INDEX = E / "data/archive_index/index.parquet"
VAL = E / "data/new_processed/dataset/validation_dataset.parquet"

results = pd.read_parquet(RES)
print("dota signal_mode:", results.signal_mode.value_counts().to_dict())
print("dota feed_source:", results.feed_source.value_counts().to_dict())
sched = results[results.signal_mode.str.contains("schedule", na=False)]
print("schedule matches:", len(sched))

idx = pd.read_parquet(INDEX)
dota = idx[(idx.game == "dota") & (idx.admission == "admitted")]
print("dota archives admitted:", len(dota), "feeds:", dota.feed_source.value_counts().to_dict())

val = pd.read_parquet(VAL, columns=["match_id", "second", "state_ts_us"])

# archive join: dota matches via steam_match_id or condition_id
arch_by_steam = {r.steam_match_id: r.archive_id for r in dota.itertuples()
                 if isinstance(r.steam_match_id, str)}
arch_by_cond = {r.condition_id.lower(): r.archive_id for r in dota.itertuples()
                if isinstance(r.condition_id, str)}
cond_by_match = dict(zip(results.match_id, results.condition_id.str.lower()))

rows = []
all_diffs = []
for r in sched.itertuples():
    mid = int(r.match_id)
    aid = arch_by_steam.get(str(mid)) or arch_by_cond.get(cond_by_match.get(mid, ""))
    if aid is None:
        continue
    sp = SCHED_DIR / f"{aid}.json"
    if not sp.is_file():
        continue
    ticks = json.loads(sp.read_text())["ticks"]
    tick_by_second = {}
    for t in ticks:
        gs = int(t["game_second"])
        if gs not in tick_by_second or t["received_ns"] < tick_by_second[gs]:
            tick_by_second[gs] = int(t["received_ns"])
    v = val[val.match_id == mid]
    feed = [t for t in [r]] # noqa
    diffs = []
    for row in v.itertuples():
        gs = int(row.second)
        if gs in tick_by_second:
            diffs.append((int(row.state_ts_us) * 1000 - tick_by_second[gs]) / 1e9)
    if diffs:
        arr = np.array(diffs)
        all_diffs.append(arr)
        rows.append({"match_id": mid, "archive_id": aid, "n": len(arr),
                     "mean": arr.mean(), "p50": np.percentile(arr, 50),
                     "feed_source": r.feed_source})
out = pd.DataFrame(rows)
print(out.to_string(index=False))
if all_diffs:
    pooled = np.concatenate(all_diffs)
    print("pooled n=", len(pooled))
    for q in (5, 25, 50, 75, 95):
        print(f"  p{q}: {np.percentile(pooled, q):.2f}s")
    print("  mean:", pooled.mean())
