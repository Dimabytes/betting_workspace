"""Sim placements against live placements: same token and price within +-1 s, and what the rest look like.

usage (from esports-trader):
  PYTHONPATH=src:scripts:<this dir> .venv/bin/python placement_match.py <journal> <variant>
"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from compare_timeline import CATALOG, live_intervals, segments

WINDOW_US = 1_000_000

rows = [json.loads(line) for line in Path(sys.argv[1]).open() if line.strip()]
catalog = pd.read_parquet(CATALOG).set_index("match_id")
kinds: Counter = Counter()
prev_gap: Counter = Counter()
for match_id, label, lo, hi, run_dir in segments(sys.argv[2]):
    entry = catalog.loc[match_id]
    live = live_intervals(rows, {str(entry.token_id_0): 0, str(entry.token_id_1): 1})
    q = pd.read_parquet(run_dir / "quote_events.parquet")
    q = q[(q.match_id == match_id) & (q.ts_ns // 1000 >= lo) & (q.ts_ns // 1000 < hi)]
    sub = q[q.kind == "submitted"].sort_values("ts_ns")
    for token in (0, 1):
        lv = live[(live.token == token) & (live.start >= lo) & (live.start < hi)].sort_values("start")
        ls, lp = lv.start.to_numpy(), lv.price.to_numpy()
        used = np.zeros(len(ls), bool)
        last_price = None
        for s in sub[sub.token_index == token].itertuples():
            t = s.ts_ns // 1000
            cand = np.where((~used) & (np.abs(ls - t) <= WINDOW_US) & (np.abs(lp - s.price) < 1e-9))[0]
            if cand.size:
                used[cand[np.argmin(np.abs(ls[cand] - t))]] = True
                kinds["matched"] += 1
            else:
                resting = lv[(lv.start <= t) & (lv.end > t)]
                if resting.empty:
                    kinds["live_none"] += 1
                elif (np.abs(resting.price - s.price) < 1e-9).any():
                    kinds["live_resting_same"] += 1
                else:
                    kinds["live_resting_other"] += 1
                gap = "first" if last_price is None else f"{round(abs(s.price - last_price) / 0.01)} ticks"
                prev_gap[gap] += 1
            last_price = s.price
        kinds["live_unmatched"] += int((~used).sum())
print(dict(kinds))
print("unmatched sim placements, move from the sim's previous price:", dict(prev_gap.most_common(8)))
