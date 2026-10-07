import json, numpy as np, pandas as pd
from pathlib import Path
pd.set_option("display.width", 250)
K = pd.read_parquet("live_known_maps.parquet"); F = pd.read_parquet("live_fills_enriched.parquet")
F = F[F.match_id.isin(K.match_id) & F.maker].copy()
F["ts"] = pd.to_datetime(F.ts, utc=True); F = F.sort_values(["match_id", "ts"])
dirs = K.set_index("match_id")["dir"]
def signals(mid):
    out = []
    with open(Path(dirs[mid]) / "session.jsonl") as fh:
        for line in fh:
            if '"signal"' not in line:
                continue
            r = json.loads(line)
            if r.get("kind") != "signal":
                continue
            p = r.get("market_p_radiant"); s = r.get("second")
            if p is None or s is None or s < 0:
                continue
            out.append((s, p))
    return pd.DataFrame(out, columns=["second", "p"]).drop_duplicates("second", keep="last")
rows = []
for mid, g in F.groupby("match_id"):
    sig = signals(mid)
    if len(sig) < 5:
        continue
    q = 0.0; ep = None
    for r in g.itertuples(index=False):
        if ep is None and r.side == "BUY":
            t0 = r.second
            w = sig[(sig.second >= t0 - 180) & (sig.second < t0)]
            vol = w.p.diff().abs().mean() if len(w) >= 3 else np.nan
            rng = (w.p.max() - w.p.min()) if len(w) >= 3 else np.nan
            ep = dict(match_id=mid, t0=t0, first_px=r.price, vol=vol, rng=rng, cash=0.0, buy=0.0, token=r.token, won=r.won)
        if ep is None:
            continue
        sign = -1 if r.side == "BUY" else 1
        ep["cash"] += sign * r.qty * r.price + r.rebate
        if r.side == "BUY":
            ep["buy"] += r.qty * r.price
        q += r.qty if r.side == "BUY" else -r.qty
        if q < 5.0:
            ep["pnl"] = ep["cash"]; rows.append(ep); ep = None; q = 0.0
    if ep is not None:
        ep["pnl"] = ep["cash"] + q * (1.0 if ep["won"] == True else 0.0); rows.append(ep)
E = pd.DataFrame(rows).dropna(subset=["vol"])
E["vol_bin"] = pd.qcut(E.vol, 3, labels=["calm", "mid", "volatile"])
E["rng_bin"] = pd.qcut(E.rng, 3, labels=["narrow", "mid", "wide"])
for col in ("vol_bin", "rng_bin"):
    t = E.groupby(col, observed=True).agg(n=("pnl", "size"), buy=("buy", "sum"), pnl=("pnl", "sum"), win=("won", lambda s: s.astype(float).mean()))
    t["per100"] = 100 * t.pnl / t.buy
    print(t.round(2)); print()
print("episodes with vol:", len(E), " vol tercile edges:", np.round(E.vol.quantile([1/3, 2/3]).values, 4))

E = E.merge(K[["match_id", "series", "joined"]], on="match_id")
rng_ = np.random.default_rng(3)
def per100(df):
    return 100 * df.pnl.sum() / df.buy.sum()
def diff_ci(df, n=4000):
    series = df.series.unique()
    by = {s: df[df.series == s] for s in series}
    sims = []
    for _ in range(n):
        pick = rng_.choice(series, len(series))
        d = pd.concat([by[s] for s in pick])
        c = d[d.vol_bin == "calm"]; v = d[d.vol_bin == "volatile"]
        if c.buy.sum() > 0 and v.buy.sum() > 0:
            sims.append(per100(c) - per100(v))
    return np.round(np.percentile(sims, [2.5, 97.5]), 1)
print("calm minus volatile per100, all live:", round(per100(E[E.vol_bin == "calm"]) - per100(E[E.vol_bin == "volatile"]), 1), "CI", diff_ci(E))
late = E[E.joined >= "2026-09-21"]
print("since 09-21:", late.groupby("vol_bin", observed=True).apply(lambda d: pd.Series({"n": len(d), "buy": d.buy.sum(), "per100": per100(d)})).round(1).to_string())
early = E[E.joined < "2026-09-21"]
print("before 09-21:", early.groupby("vol_bin", observed=True).apply(lambda d: pd.Series({"n": len(d), "buy": d.buy.sum(), "per100": per100(d)})).round(1).to_string())

E = E.merge(K[["match_id", "feed"]], on="match_id")
print(E.groupby(["feed", "vol_bin"], observed=True).apply(lambda d: pd.Series({"n": len(d), "buy": d.buy.sum(), "per100": per100(d)})).round(1).to_string())
G = E[E.feed == "grid"].copy()
G["vb"] = pd.qcut(G.vol, 3, labels=["calm", "mid", "volatile"])
print("GRID-only terciles:", G.groupby("vb", observed=True).apply(lambda d: pd.Series({"n": len(d), "buy": d.buy.sum(), "pnl": d.pnl.sum(), "per100": per100(d)})).round(1).to_string())
