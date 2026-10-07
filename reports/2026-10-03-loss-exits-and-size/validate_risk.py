import numpy as np, pandas as pd
from pathlib import Path
import bt_load
from bt_caps import simulate_cap
root = Path("/root/work/esports-trader/data/backtests/dota_maker")
def metrics(pnl, order):
    pnl = pnl.reindex(order).fillna(0.0)
    k = max(1, int(np.ceil(0.05 * len(pnl))))
    cum = pnl.cumsum()
    return dict(total=round(pnl.sum()), cvar5=round(np.sort(pnl.values)[:k].mean()), worst=round(pnl.min()), maxdd=round((cum.cummax() - cum).max()))
for seed in ("seed0", "seed1", "seed2"):
    fb, rb = bt_load.load_seed(root / "validation_join_delta02_x015_cut480_p45_sz300-l18", seed)
    fs, rs = bt_load.load_seed(root / "validation_join_delta02_x015_cut480_p45_sz300-l9", seed)
    common = sorted(set(rb.match_id) & set(rs.match_id))
    order = rs[rs.match_id.isin(common)].assign(h=pd.to_datetime(rs.horn_at, format="ISO8601", utc=True)).sort_values("h").match_id.values
    est = simulate_cap(fb[fb.match_id.isin(common)], 2700).pnl
    act = rs.set_index("match_id").engine_pnl
    print(seed, "estimate", metrics(est, order), " actual", metrics(act, order))
