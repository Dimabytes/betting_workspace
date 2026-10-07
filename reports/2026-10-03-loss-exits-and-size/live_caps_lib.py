import numpy as np, pandas as pd
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

