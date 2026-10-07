import numpy as np, pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 200)
K = pd.read_parquet("live_known_maps.parquet"); F = pd.read_parquet("live_fills_enriched.parquet")
F = F[F.match_id.isin(K.match_id)].copy()
F["ts"] = pd.to_datetime(F.ts, utc=True)
F = F.sort_values(["match_id", "ts"])
# drop manual fills: they are not the bot's decisions
bot = F[F.maker].copy()

def sim(g, cap):
    orig = {}; tq = {}; tc = {}; cash = 0.0; reb = 0.0; peak = 0.0
    for r in g.itertuples(index=False):
        t = r.token
        orig.setdefault(t, 0.0); tq.setdefault(t, 0.0); tc.setdefault(t, 0.0)
        if r.side == "BUY":
            room = cap - sum(tc.values())
            acc = 0.0 if room <= 0 else min(r.qty, room / r.price)
            orig[t] += r.qty
            if acc > 0:
                tq[t] += acc; tc[t] += acc * r.price; cash -= acc * r.price
                reb += r.rebate * acc / r.qty
        else:
            frac = min(1.0, r.qty / orig[t]) if orig[t] > 1e-9 else 1.0
            orig[t] = max(0.0, orig[t] - r.qty)
            s = tq[t] * frac; avg = tc[t] / tq[t] if tq[t] > 1e-9 else 0.0
            tq[t] -= s; tc[t] -= avg * s; cash += s * r.price
            reb += r.rebate * (s / r.qty if r.qty > 0 else 0)
        peak = max(peak, sum(tc.values()))
    won = g.groupby("token").won.first()
    settle = sum(tq[t] * (1.0 if won.get(t) == True else 0.0) for t in tq)
    return cash + settle + reb, peak

big = K[K["clip"].isin([250.0, 400.0])].copy()
rows = []
for cap in (800, 1200, 1600, 2000, 2400, 1e12):
    for mid in big.match_id:
        g = bot[bot.match_id == mid]
        if g.empty:
            continue
        pnl, peak = sim(g, cap)
        rows.append(dict(cap=cap, match_id=mid, pnl=pnl, peak=peak))
R = pd.DataFrame(rows).merge(big[["match_id", "clip", "day", "radiant", "dire", "map_number", "slug"]], on="match_id")
s = R.groupby(["cap"]).pnl.agg(total="sum", sd="std", worst="min", best="max", n="count")
print("Big-clip live maps (clip 250/400), manual fills removed")
print(s.round(0))
print()
print(R.pivot_table(index="day", columns="cap", values="pnl", aggfunc="sum").round(0))
print()
t = R[R.day.astype(str) == "2026-10-03"].pivot_table(index=["slug"], columns="cap", values="pnl", aggfunc="sum").round(0)
print(t)
