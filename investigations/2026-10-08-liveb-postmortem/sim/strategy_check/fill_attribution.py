"""Each sim fill against live B's state on the same token at the fill time.

live_state: the live bid at that moment was at the same price, another price, or absent.
live_filled: a live fill on the same token and price within +-2 s.

usage (from esports-trader):
  PYTHONPATH=src:scripts:<this dir> .venv/bin/python fill_attribution.py <journal> <variant>
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from compare_timeline import CATALOG, live_intervals, segments
from journal_to_core_trace import load_orders

WINDOW_US = 2_000_000

rows = [json.loads(line) for line in Path(sys.argv[1]).open() if line.strip()]
catalog = pd.read_parquet(CATALOG).set_index("match_id")
out = []
for match_id, label, lo, hi, run_dir in segments(sys.argv[2]):
    entry = catalog.loc[match_id]
    tokens = {str(entry.token_id_0): 0, str(entry.token_id_1): 1}
    live = live_intervals(rows, tokens)
    live_fills = [(o.token_index, o.price, us, qty) for o in load_orders(rows, tokens) for us, qty in o.fills]
    fills = pd.read_parquet(run_dir / "fills.parquet")
    fills = fills[(fills.match_id == match_id) & (fills.ts_ns // 1000 >= lo) & (fills.ts_ns // 1000 < hi)]
    for f in fills.itertuples():
        t = f.ts_ns // 1000
        resting = live[(live.token == f.token_index) & (live.start <= t) & (live.end > t)]
        if resting.empty:
            state = "live_none"
        elif (np.abs(resting.price - f.price) < 1e-9).any():
            state = "live_same"
        else:
            state = "live_other"
        near = [q for tok, p, us, q in live_fills
                if tok == f.token_index and abs(p - f.price) < 1e-9 and abs(us - t) <= WINDOW_US]
        out.append({"match_id": match_id, "seg": label, "state": state, "live_filled": bool(near), "qty": f.quantity})
frame = pd.DataFrame(out)
table = frame.groupby(["state", "live_filled"]).qty.agg(["count", "sum"]).round(1)
print(table.to_string())
print(f"sim shares {frame.qty.sum():.1f}")
