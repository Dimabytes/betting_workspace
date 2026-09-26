"""Measure offset between dataset state_ts_us (rfc460) and GRID archive received_ns.

For schedule-bound matches in the LoL LIVE backtest, align per-second:
  offset_s = state_ts_us(second=s) - received_ns(second=s)
offset > 0 means the backtest signal fires LATER than real GRID arrival;
offset < 0 means the backtest signal fires EARLIER than live could see the state.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
SCHED_DIR = E / "data/archive_index/schedules/trader/lol"
VAL = E / "data/lol/processed/datasets/validation.parquet"
RES = E / "data/backtests/lol_maker/LIVE/seed0/results.parquet"
INDEX = E / "data/archive_index/index.parquet"

results = pd.read_parquet(RES)
print("signal_mode counts:", results.signal_mode.value_counts().to_dict())
sched_ids = results.loc[results.signal_mode.str.contains("schedule", na=False), "match_id"].tolist()
print("schedule matches:", len(sched_ids))

# map match_id -> condition_id -> archive schedule
idx = pd.read_parquet(INDEX)
idx = idx[(idx.game == "lol") & (idx.admission == "admitted")]
audit = pd.read_parquet(E / "data/lol/processed/datasets/backtest_audit.parquet")
cond = dict(zip(audit.match_id, audit.condition_id.str.lower()))
arch_by_cond = {r.condition_id.lower(): r.archive_id for r in idx.itertuples() if isinstance(r.condition_id, str)}

val = pd.read_parquet(VAL, columns=["match_id", "second", "state_ts_us"])

rows = []
per_match = {}
for mid in sched_ids:
    cond = audit.loc[audit.match_id == mid, "condition_id"]
    if cond.empty:
        continue
    c = str(cond.iloc[0]).lower()
    aid = arch_by_cond.get(c)
    if aid is None:
        continue
    sp = SCHED_DIR / f"{aid}.json"
    if not sp.is_file():
        print("missing schedule", mid, aid)
        continue
    sched = json.loads(sp.read_text())
    tick_by_second = {}
    for t in sched["ticks"]:
        gs = int(t["game_second"])
        # keep earliest received for that second
        if gs not in tick_by_second or t["received_ns"] < tick_by_second[gs]:
            tick_by_second[gs] = int(t["received_ns"])
    v = val[val.match_id == mid]
    diffs = []
    for r in v.itertuples():
        gs = int(r.second)
        if gs in tick_by_second:
            diffs.append((gs, (int(r.state_ts_us) * 1000 - tick_by_second[gs]) / 1e9))
    if diffs:
        arr = np.array([d[1] for d in diffs])
        per_match[mid] = arr
        rows.append({
            "match_id": mid, "archive_id": aid, "n": len(arr),
            "mean": arr.mean(), "p10": np.percentile(arr, 10),
            "p50": np.percentile(arr, 50), "p90": np.percentile(arr, 90),
        })

out = pd.DataFrame(rows)
print(out.describe().to_string())
out.to_csv(Path(sys.argv[0]).with_name("clock_offset_per_match.csv"), index=False)
all_diffs = np.concatenate(list(per_match.values())) if per_match else np.array([])
if len(all_diffs):
    print("pooled n=", len(all_diffs))
    for q in (1, 5, 10, 25, 50, 75, 90, 95, 99):
        print(f"  p{q}: {np.percentile(all_diffs, q):.2f}s")
    print(f"  mean: {all_diffs.mean():.2f}s")
