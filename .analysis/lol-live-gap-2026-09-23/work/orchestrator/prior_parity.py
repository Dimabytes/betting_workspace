"""Live market_radiant_prior (session.jsonl) vs training-style prior (validation.parquet) on the same LoL maps."""
import json
import numpy as np
import pandas as pd

E = "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader"
O = "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/orchestrator"
j = pd.read_csv(f"{O}/same_map_lol.csv")
val = pd.read_parquet(f"{E}/data/lol/processed/datasets/validation.parquet",
                      columns=["match_id", "second", "market_radiant_prior", "market_p_radiant"])
first = val.sort_values("second").groupby("match_id").agg(train_prior=("market_radiant_prior", "first"),
                                                          train_mid0=("market_p_radiant", "first"),
                                                          train_first_second=("second", "first"))
rows = []
for _, r in j.iterrows():
    live_prior, live_mid, live_sec = None, None, None
    with open(f"{E}/data/trader/{r['dir']}/session.jsonl") as fh:
        for line in fh:
            d = json.loads(line)
            if d.get("kind") != "signal":
                continue
            if live_prior is None and d.get("market_radiant_prior") is not None:
                live_prior = d["market_radiant_prior"]
            if d.get("reason") == "model" and d.get("market_p_radiant") is not None:
                live_mid, live_sec = d["market_p_radiant"], d["second"]
                break
    mid = int(r["match_id"])
    if mid not in first.index or live_prior is None:
        continue
    t = first.loc[mid]
    rows.append(dict(dir=r["dir"], train_prior=t.train_prior, live_prior=live_prior,
                     train_mid0=t.train_mid0, live_first_mid=live_mid, live_first_model_second=live_sec))
df = pd.DataFrame(rows)
df["diff_c"] = 100 * (df.live_prior - df.train_prior)
df["flip_diff_c"] = 100 * (df.live_prior - (1 - df.train_prior))
print("maps", len(df))
print("live-train prior (cents): mean %.2f  median %.2f  mean|.| %.2f  p90|.| %.2f" % (
    df.diff_c.mean(), df.diff_c.median(), df.diff_c.abs().mean(), df.diff_c.abs().quantile(0.9)))
print("if orientation were flipped: mean|.| %.2f" % df.flip_diff_c.abs().mean())
print("share |diff| > 3c: %.2f ; > 5c: %.2f" % ((df.diff_c.abs() > 3).mean(), (df.diff_c.abs() > 5).mean()))
print("first live model second: median", df.live_first_model_second.median(), " p90", df.live_first_model_second.quantile(0.9))
print(df.sort_values("diff_c", key=abs, ascending=False).head(12).round(3).to_string())
df.to_csv(f"{O}/prior_parity_lol.csv", index=False)
