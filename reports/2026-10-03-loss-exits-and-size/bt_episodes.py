import numpy as np, pandas as pd
from pathlib import Path
from bt_load import load_seed
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
base = Path("/root/work/esports-trader/data/backtests/dota_maker/LIVE")
allep = []
for seed in ("seed0", "seed1", "seed2"):
    f, r = load_seed(base, seed)
    f["cash"] = np.where(f.side == "BUY", -f.price * f.quantity, f.price * f.quantity)
    f["sq"] = np.where(f.side == "BUY", f.quantity, -f.quantity)
    eps = []
    for (mid, ep), g in f.groupby(["match_id", "episode_id"], sort=False):
        left = g.groupby("token_index").agg(q=("sq", "sum"), v=("value", "first"))
        settle = (left.q.clip(lower=0) * left.v).sum()
        buys = g[g.side == "BUY"]
        if buys.empty:
            continue
        eps.append(dict(seed=seed, match_id=mid, episode_id=ep, t0=g.ts_ns.min(), token=buys.token_index.iloc[0],
                        first_px=buys.price.iloc[0], buy_usd=-g.cash[g.side == "BUY"].sum(),
                        pnl=g.cash.sum() + settle + g.maker_rebate.sum(), settled=settle > 0))
    E = pd.DataFrame(eps).sort_values(["match_id", "t0"])
    E["k"] = E.groupby("match_id").cumcount() + 1
    E["prev_pnl"] = E.groupby("match_id").pnl.shift(1)
    E["prev_token"] = E.groupby("match_id").token.shift(1)
    E["cum_prev"] = E.groupby("match_id").pnl.cumsum() - E.pnl
    allep.append(E)
E = pd.concat(allep)
def show(sub, label):
    g = sub.groupby("seed").pnl.agg(["count", "sum", "mean"])
    print(f"{label:52s} n/seed={g['count'].mean():6.0f}  pnl/seed={g['sum'].mean():8.0f}  mean/ep={g['mean'].mean():6.1f}  per-seed sums={g['sum'].round(0).tolist()}")
show(E, "all episodes")
show(E[E.k == 1], "first episode on map")
show(E[E.k >= 2], "re-entry (episode 2+)")
show(E[(E.k >= 2) & (E.prev_pnl < 0)], "re-entry after a LOSING episode")
show(E[(E.k >= 2) & (E.prev_pnl < 0) & (E.token == E.prev_token)], "  ...same token as the losing one")
show(E[(E.k >= 2) & (E.prev_pnl < 0) & (E.token != E.prev_token)], "  ...opposite token")
show(E[(E.k >= 2) & (E.prev_pnl >= 0)], "re-entry after a winning episode")
show(E[(E.k >= 2) & (E.cum_prev < -100)], "re-entry when map is already down > $100")
show(E[(E.k >= 2) & (E.cum_prev < -300)], "re-entry when map is already down > $300")
for lo, hi in [(0.45, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 0.86)]:
    show(E[(E.first_px >= lo) & (E.first_px < hi)], f"episode first price {lo:.2f}-{hi:.2f}")
E.to_parquet("bt_episodes.parquet")
