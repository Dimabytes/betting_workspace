import json, numpy as np, pandas as pd
from pathlib import Path
import bt_load
from bt_caps import simulate_cap, risk_row
root = Path("/root/work/esports-trader/data/backtests/dota_maker")
RES = bt_load.RES
for big, small, cap in [("validation_join_delta02_x015_cut480_p45_sz300-l18", "validation_join_delta02_x015_cut480_p45_sz300-l9", 2700),
                        ("validation_join_delta02_x015_cut480_p45_sz300-l36", "validation_join_delta02_x015_cut480_p45_sz300-l18", 5400)]:
    for seed in ("seed0", "seed1", "seed2"):
        fb, rb = bt_load.load_seed(root / big, seed)
        fs, rs = bt_load.load_seed(root / small, seed)
        common = sorted(set(rb.match_id) & set(rs.match_id))
        miss = fb.value.isna().mean()
        fb = fb[fb.match_id.isin(common)]
        tr = simulate_cap(fb, cap)
        act = rs.set_index("match_id").engine_pnl.reindex(common)
        full = simulate_cap(fb, 1e12)
        print(f"{big[-8:]}->{small[-7:]} {seed}: maps={len(common)} nan_value={miss:.3f}  big_full={full.pnl.sum():8.0f}  "
              f"truncated_estimate={tr.pnl.reindex(common).sum():8.0f}  actual_small_run={act.sum():8.0f}")
