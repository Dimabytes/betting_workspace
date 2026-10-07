import numpy as np, pandas as pd
from pathlib import Path
from bt_load import load_seed
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
base = Path("/root/work/esports-trader/data/backtests/dota_maker/LIVE")
rng = np.random.default_rng(11)

def annotate(f):
    avg_before = np.full(len(f), np.nan); first_buy = np.full(len(f), np.nan)
    for (mid, tok), g in f.groupby(["match_id", "token_index"], sort=False):
        q = 0.0; c = 0.0; fb = np.nan
        for i in g.index:
            side = f.at[i, "side"]; px = f.at[i, "price"]; qty = f.at[i, "quantity"]
            if side == "BUY":
                if q < 1e-6:
                    fb = px
                q += qty; c += qty * px; first_buy[i] = fb
            else:
                avg = c / q if q > 1e-9 else np.nan
                avg_before[i] = avg; first_buy[i] = fb
                take = min(qty, q)
                if q > 1e-9:
                    c -= avg * take; q -= take
                if q < 1e-6:
                    q = 0.0; c = 0.0
    f["avg_before"] = avg_before; f["first_buy"] = first_buy
    return f

def boot_ci(df, col, n=3000):
    per = df.groupby("match_id")[col].sum().values
    if len(per) < 3:
        return (np.nan, np.nan)
    sims = [rng.choice(per, len(per)).sum() for _ in range(n)]
    return tuple(np.round(np.percentile(sims, [2.5, 97.5]), 0))

def summ(df, label):
    w = df.quantity
    print(f"  {label:44s} sells={len(df):5d} maps={df.match_id.nunique():4d} avg_px={np.average(df.price, weights=w):.3f} "
          f"win(w)={np.average(df.value, weights=w):.3f} hold_adv=${df.hold_adv.sum():8.0f} CI={boot_ci(df, 'hold_adv')}")

for seed in ("seed0", "seed1", "seed2"):
    f, r = load_seed(base, seed)
    f = annotate(f)
    S = f[f.side == "SELL"].copy()
    S["hold_adv"] = S.quantity * (S.value - S.price)
    S["loss"] = S.price < S.avg_before - 0.005
    print(f"\n### {seed}")
    summ(S, "all sells")
    L = S[S.loss]
    summ(L, "LOSS exits")
    summ(L[L.value > 0.5], "  ...token later WON")
    summ(L[L.value < 0.5], "  ...token later LOST")
    summ(L[L.first_buy >= 0.65], "LOSS exits, entry >= 0.65")
    summ(L[L.first_buy < 0.65], "LOSS exits, entry < 0.65")
    summ(S[~S.loss], "non-loss exits")
