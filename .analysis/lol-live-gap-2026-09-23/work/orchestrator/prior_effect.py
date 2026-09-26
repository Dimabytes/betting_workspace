"""Model output with the training prior vs the live prior, on validation rows of the same LoL maps."""
import numpy as np
import pandas as pd
from shared.utils.gbm import load_predictor, FEATURE_COLUMNS

E = "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader"
O = "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/orchestrator"
pp = pd.read_csv(f"{O}/prior_parity_lol.csv")
j = pd.read_csv(f"{O}/same_map_lol.csv")[["dir", "match_id"]]
pp = pp.merge(j, on="dir")
val = pd.read_parquet(f"{E}/data/lol/processed/datasets/validation.parquet")
val = val[val.match_id.isin(pp.match_id) & (val.second >= 0) & (val.second <= 480)]
live_prior = pp.set_index("match_id")["live_prior"]
from pathlib import Path
pred = load_predictor(Path(E) / "data/lol/models/production")
a = np.asarray(pred.predict(val[FEATURE_COLUMNS]), dtype=float)
alt = val[FEATURE_COLUMNS].assign(market_radiant_prior=val.match_id.map(live_prior).to_numpy())
b = np.asarray(pred.predict(alt), dtype=float)
d = 100 * (b - a)
gate_a, gate_b = np.abs(a) >= 0.02, np.abs(b) >= 0.02
print("rows", len(val), "maps", val.match_id.nunique())
print("pred diff (cents): mean %.3f  mean|.| %.3f  p90|.| %.3f  p99|.| %.3f" % (d.mean(), np.abs(d).mean(), np.quantile(np.abs(d), .9), np.quantile(np.abs(d), .99)))
print("gate open train-prior %.3f  live-prior %.3f  disagree %.3f  sign flips among gated %.3f" % (
    gate_a.mean(), gate_b.mean(), (gate_a != gate_b).mean(), ((np.sign(a) != np.sign(b)) & (gate_a | gate_b)).mean()))
val = val.assign(d=d)
per_map = val.groupby("match_id")["d"].apply(lambda x: np.abs(x).mean()).rename("mean_abs_pred_diff_c")
pm = pp.set_index("match_id").join(per_map)
print(pm.sort_values("mean_abs_pred_diff_c", ascending=False).head(10)[["dir", "train_prior", "live_prior", "diff_c", "mean_abs_pred_diff_c"]].round(3).to_string())
print("corr(|prior diff|, |pred diff|) = %.3f" % np.corrcoef(pm.diff_c.abs(), pm.mean_abs_pred_diff_c)[0, 1])
