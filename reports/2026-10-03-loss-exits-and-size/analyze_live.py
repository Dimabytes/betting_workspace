import numpy as np, pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 400)
M = pd.read_parquet("live_maps.parquet"); F = pd.read_parquet("live_fills.parquet")
F["ts"] = pd.to_datetime(F["ts"], utc=True, errors="coerce")
M["joined"] = pd.to_datetime(M["joined"], utc=True, errors="coerce")
F = F.sort_values(["match_id", "ts"]).reset_index(drop=True)

# running avg cost per (match, token) and classify sells
rows = []
for (mid, tok), g in F.groupby(["match_id", "token"], sort=False):
    qty = 0.0; cost = 0.0; first_buy = None
    for i, r in g.iterrows():
        if r.side == "BUY":
            if first_buy is None:
                first_buy = r.price
            qty += r.qty; cost += r.qty * r.price
            rows.append((i, np.nan, first_buy))
        else:
            avg = cost / qty if qty > 1e-9 else np.nan
            take = min(r.qty, qty)
            if qty > 1e-9:
                cost -= avg * take; qty -= take
            if qty < 1e-6:
                qty = 0.0; cost = 0.0; first_buy = None
            rows.append((i, avg, first_buy))
idx, avgc, fb = zip(*rows)
F.loc[list(idx), "avg_cost_before"] = list(avgc)
F.loc[list(idx), "episode_first_buy"] = list(fb)

F["signed_cash"] = np.where(F.side == "BUY", -F.price * F.qty, F.price * F.qty)
F["signed_qty"] = np.where(F.side == "BUY", F.qty, -F.qty)

traded = M[M.n_fills > 0].copy()
known = traded[traded.winner.isin(["radiant", "dire"])].copy()
print("traded maps", len(traded), "with winner", len(known))

g = F.groupby("match_id")
per = pd.DataFrame({
    "cash": g.signed_cash.sum(),
    "buy_usd": g.apply(lambda x: (x.price * x.qty)[x.side == "BUY"].sum()),
    "rebate": g.rebate.sum(),
    "n_manual": g.apply(lambda x: (~x.maker).sum()),
})
# settlement of the leftover
left = F.groupby(["match_id", "token", "won"], dropna=False).signed_qty.sum().reset_index()
left["settle"] = np.where(left.won == True, left.signed_qty.clip(lower=0), 0.0)
per["settle"] = left.groupby("match_id").settle.sum()
per["leftover_sh"] = left.groupby("match_id").signed_qty.apply(lambda s: s.clip(lower=0).sum())
# hold advantage: what selling cost vs holding to settlement
S = F[F.side == "SELL"].copy()
S["hold_adv"] = S["qty"] * (S["won"].astype(float) - S["price"])
per["hold_adv"] = S.groupby("match_id").hold_adv.sum()
per = per.fillna({"hold_adv": 0.0, "settle": 0.0})
per["pnl"] = per.cash + per.settle + per.rebate
known = known.merge(per, left_on="match_id", right_index=True, how="left")
known["day"] = known.joined.dt.tz_convert("Europe/Berlin").dt.date
known["week"] = known.joined.dt.tz_convert("Europe/Berlin").dt.to_period("W").astype(str)
# peak exposure (cost basis held)
def peak_cost(x):
    qty = {}; cost = {}; peak = 0.0
    for _, r in x.iterrows():
        t = r.token
        q = qty.get(t, 0.0); c = cost.get(t, 0.0)
        if r.side == "BUY":
            q += r.qty; c += r.qty * r.price
        else:
            avg = c / q if q > 1e-9 else 0
            take = min(r.qty, q); q -= take; c -= avg * take
        qty[t] = q; cost[t] = c
        peak = max(peak, sum(cost.values()))
    return peak
pk = F.groupby("match_id").apply(peak_cost)
known["peak_cost"] = known.match_id.map(pk)
known.to_parquet("live_known_maps.parquet"); F.to_parquet("live_fills_enriched.parquet")

print("\n=== per-week")
wk = known.groupby("week").agg(maps=("match_id", "size"), clip_med=("clip", "median"), buy=("buy_usd", "sum"),
    pnl=("pnl", "sum"), hold_adv=("hold_adv", "sum"), peak_med=("peak_cost", "median"), peak_max=("peak_cost", "max"),
    worst=("pnl", "min"), best=("pnl", "max"))
wk["pnl_per_100"] = 100 * wk.pnl / wk.buy
print(wk.round(1))
print("\n=== per-clip")
ck = known.groupby("clip").agg(maps=("match_id", "size"), buy=("buy_usd", "sum"), pnl=("pnl", "sum"),
    hold_adv=("hold_adv", "sum"), peak_med=("peak_cost", "median"), worst=("pnl", "min"), best=("pnl", "max"), sd=("pnl", "std"))
ck["pnl_per_100"] = 100 * ck.pnl / ck.buy
print(ck.round(1))
print("\nmanual(taker) fills:", int(per.n_manual.sum()), "maps with taker fills:", int((per.n_manual > 0).sum()))
