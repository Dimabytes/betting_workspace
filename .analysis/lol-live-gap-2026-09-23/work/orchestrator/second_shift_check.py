"""Does the LoL grid-v1 backtest feed `second` or `second - 10`? Recompute stored predicted_delta."""
import numpy as np
import pandas as pd
from pathlib import Path
from shared.utils.gbm import load_predictor, FEATURE_COLUMNS

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
res = pd.read_parquet(E / "data/backtests/lol_maker/LIVE/seed0/results.parquet")[["match_id", "signal_mode"]]
fills = pd.read_parquet(E / "data/backtests/lol_maker/LIVE/seed0/fills.parquet")
fills = fills.merge(res, on="match_id")
buys = fills[(fills.side == "BUY") & (fills.signal_mode == "grid_v1")].head(400).copy()
buys["signal_us"] = ((buys.ts_ns - (buys.signal_age_seconds * 1e9)) / 1000).round().astype("int64")
val = pd.read_parquet(E / "data/lol/processed/datasets/validation.parquet")
val = val[val.match_id.isin(buys.match_id.unique())]
pred = load_predictor(E / "data/lol/models/research")
rows = []
for _, b in buys.iterrows():
    cand = val[(val.match_id == b.match_id)]
    i = (cand.state_ts_us - b.signal_us).abs().idxmin()
    row = cand.loc[[i]]
    if abs(int(row.state_ts_us.iloc[0]) - int(b.signal_us)) > 2000:
        continue
    p0 = float(pred.predict(row[FEATURE_COLUMNS])[0])
    p10 = float(pred.predict(row[FEATURE_COLUMNS].assign(second=row["second"] - 10))[0])
    rows.append((b.predicted_delta, p0, p10))
a = np.array(rows)
print("matched fills:", len(a))
print("mean |stored - pred(second)|      = %.6f" % np.mean(np.abs(a[:, 0] - a[:, 1])))
print("mean |stored - pred(second - 10)| = %.6f" % np.mean(np.abs(a[:, 0] - a[:, 2])))
for tol in (1e-9, 1e-6, 1e-4):
    print("tol %g: exact(second)=%d exact(second-10)=%d of %d" % (tol, int((np.abs(a[:,0]-a[:,1]) < tol).sum()), int((np.abs(a[:,0]-a[:,2]) < tol).sum()), len(a)))
print("median |stored-pred(second)| = %.6f ; median |stored-pred(second-10)| = %.6f" % (np.median(np.abs(a[:,0]-a[:,1])), np.median(np.abs(a[:,0]-a[:,2]))))
e0 = np.abs(a[:,0]-a[:,1]) < 1e-9; e10 = np.abs(a[:,0]-a[:,2]) < 1e-9
print("only unshifted matches: %d ; only shifted(-10) matches: %d ; both: %d" % (int((e0 & ~e10).sum()), int((e10 & ~e0).sum()), int((e0 & e10).sum())))
