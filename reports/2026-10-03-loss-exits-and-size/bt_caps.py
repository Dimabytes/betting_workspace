import numpy as np, pandas as pd
from pathlib import Path
from bt_load import load_seed
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
base = Path("/root/work/esports-trader/data/backtests/dota_maker/LIVE")

def simulate_cap(f, cap):
    """Accept BUY fills while held cost stays under cap; scale SELLs by the fraction sold."""
    out = {}
    for mid, g in f.groupby("match_id", sort=False):
        orig_q = {0: 0.0, 1: 0.0}
        tq = {0: 0.0, 1: 0.0}; tc = {0: 0.0, 1: 0.0}
        cash = 0.0; reb = 0.0; peak = 0.0
        for row in g.itertuples(index=False):
            t = row.token_index; px = row.price; q = row.quantity
            if row.side == "BUY":
                room = cap - (tc[0] + tc[1])
                acc = 0.0 if room <= 0 else min(q, room / px)
                orig_q[t] += q
                if acc > 0:
                    tq[t] += acc; tc[t] += acc * px; cash -= acc * px
                    reb += row.maker_rebate * (acc / q)
            else:
                frac = min(1.0, q / orig_q[t]) if orig_q[t] > 1e-9 else 1.0
                orig_q[t] = max(0.0, orig_q[t] - q)
                sell = tq[t] * frac
                avg = tc[t] / tq[t] if tq[t] > 1e-9 else 0.0
                tq[t] -= sell; tc[t] -= avg * sell; cash += sell * px
                reb += row.maker_rebate * (sell / q if q > 0 else 0)
            peak = max(peak, tc[0] + tc[1])
        vals = {t: g[g.token_index == t].value.iloc[0] if (g.token_index == t).any() else 0.0 for t in (0, 1)}
        settle = tq[0] * vals[0] + tq[1] * vals[1]
        out[mid] = (cash + settle, reb, peak)
    return pd.DataFrame.from_dict(out, orient="index", columns=["pnl", "rebate", "peak"])

def risk_row(per, order, cap, seed):
    per = per.reindex(order).fillna(0.0)
    net = per.pnl + per.rebate
    traded = per[per.peak > 0]
    k = max(1, int(np.ceil(0.05 * len(net))))
    worst5 = np.sort(per.pnl.values)[:k].mean()
    cum = net.cumsum(); dd = (cum.cummax() - cum).max()
    return dict(seed=seed, cap=cap, net=net.sum(), engine=per.pnl.sum(), rebate=per.rebate.sum(), sd_map=net[per.peak > 0].std(),
                cvar5=worst5, worst=per.pnl.min(), maxdd=dd, loss_gt300=(net < -300).sum(), loss_gt500=(net < -500).sum(),
                peak_med=traded.peak.median())

if __name__ == "__main__":
    rows = []
    for seed in ("seed0", "seed1", "seed2"):
        f, r = load_seed(base, seed)
        order = r.assign(h=pd.to_datetime(r.horn_at, format="ISO8601", utc=True)).sort_values("h").match_id.values
        # sanity: full cap must reproduce engine pnl
        full = simulate_cap(f, 1e12)
        eng = r.set_index("match_id").engine_pnl.reindex(full.index)
        print(seed, "reproduce engine pnl (sum ours vs engine):", round(full.pnl.sum(), 1), round(eng.sum(), 1))
        for cap in (600, 900, 1200, 1500, 1800, 2400, 2700, 1e12):
            rows.append(risk_row(simulate_cap(f, cap), order, cap, seed))
    R = pd.DataFrame(rows)
    agg = R.groupby("cap").mean(numeric_only=True).round(0)
    agg["net_vs_full_%"] = (100 * agg.net / agg.loc[1e12, "net"]).round(0)
    agg["maxdd_vs_full_%"] = (100 * agg.maxdd / agg.loc[1e12, "maxdd"]).round(0)
    agg["cvar_vs_full_%"] = (100 * agg.cvar5 / agg.loc[1e12, "cvar5"]).round(0)
    print(agg.to_string())
    R.to_csv("bt_caps.csv", index=False)
