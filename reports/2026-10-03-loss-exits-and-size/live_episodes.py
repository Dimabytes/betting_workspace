import numpy as np, pandas as pd
pd.set_option("display.width", 250)
K = pd.read_parquet("live_known_maps.parquet"); F = pd.read_parquet("live_fills_enriched.parquet")
F = F[F.match_id.isin(K.match_id) & F.maker].copy()
F["ts"] = pd.to_datetime(F.ts, utc=True); F = F.sort_values(["match_id", "ts"])
eps = []
for mid, g in F.groupby("match_id"):
    q = {}; c = {}; ep = None
    for r in g.itertuples(index=False):
        t = r.token
        q.setdefault(t, 0.0); c.setdefault(t, 0.0)
        if ep is None and r.side == "BUY":
            ep = dict(match_id=mid, token=t, first_px=r.price, buy=0.0, cash=0.0, won=r.won, k=len([e for e in eps if e["match_id"] == mid]) + 1)
        if ep is None:
            continue
        if r.side == "BUY":
            q[t] += r.qty; c[t] += r.qty * r.price; ep["buy"] += r.qty * r.price; ep["cash"] -= r.qty * r.price
        else:
            take = min(r.qty, q[t]); avg = c[t] / q[t] if q[t] > 1e-9 else 0.0
            q[t] -= take; c[t] -= avg * take; ep["cash"] += r.qty * r.price
        ep["cash"] += r.rebate
        if sum(q.values()) < 5.0:
            ep["pnl"] = ep["cash"]; eps.append(ep); ep = None; q = {k: 0.0 for k in q}; c = {k: 0.0 for k in c}
    if ep is not None:
        ep["pnl"] = ep["cash"] + q[ep["token"]] * (1.0 if ep["won"] == True else 0.0); eps.append(ep)
E = pd.DataFrame(eps)
E["prev"] = E.groupby("match_id").pnl.shift(1)
E = E.merge(K[["match_id", "clip", "joined"]], on="match_id")
for label, sub in [("all live", E), ("since 09-28", E[E.joined >= "2026-09-28"])]:
    print(f"\n### {label}: episodes={len(sub)} pnl=${sub.pnl.sum():.0f}")
    for lo, hi in [(0.0, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 0.9)]:
        s = sub[(sub.first_px >= lo) & (sub.first_px < hi)]
        print(f"  first price {lo:.2f}-{hi:.2f}: n={len(s):4d} buy=${s.buy.sum():8.0f} pnl=${s.pnl.sum():7.0f} per100={100*s.pnl.sum()/max(s.buy.sum(),1):5.1f} win_rate={s.won.astype(float).mean():.2f}")
    s = sub[sub.k >= 2]; s2 = s[s.prev < 0]
    print(f"  re-entry: n={len(s)} pnl=${s.pnl.sum():.0f};  after a losing episode: n={len(s2)} pnl=${s2.pnl.sum():.0f} per100={100*s2.pnl.sum()/max(s2.buy.sum(),1):.1f}")
