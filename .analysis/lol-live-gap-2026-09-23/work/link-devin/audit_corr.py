"""Per-map correlation between market_p_radiant and radiant_nw_adv.

A flipped side binding makes market_p_radiant anti-correlate with the blue-side
features. A wrong-game link decorrelates them entirely. Also market_end_time vs
game end sanity.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()
uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
link_by_gid = {l["esports_game_id"]: l for l in links}

ms = pq.read_table(
    E / "data/lol/processed/datasets/market_seconds.parquet",
    columns=["match_id", "second", "market_status", "market_p_radiant", "state_ts_us"],
).to_pandas()
gf = pq.read_table(
    E / "data/lol/processed/datasets/game_features.parquet",
    columns=["match_id", "game_second", "radiant_nw_adv", "deaths_radiant", "deaths_dire"],
).to_pandas()

ok = ms[ms["market_status"] == "ok"][["match_id", "second", "market_p_radiant"]]
merged = ok.merge(
    gf, left_on=["match_id", "second"], right_on=["match_id", "game_second"], how="inner"
)
print("joined rows:", len(merged), "maps:", merged.match_id.nunique())

rows = []
for match_id, grp in merged.groupby("match_id"):
    if len(grp) < 60:
        continue
    mid = grp["market_p_radiant"].to_numpy()
    nw = grp["radiant_nw_adv"].to_numpy()
    # correlate mid with normalized nw sign & raw nw
    c_raw = np.corrcoef(mid, nw)[0, 1]
    c_sign = np.corrcoef(mid, np.sign(nw))[0, 1]
    gid = str(match_id)
    l = link_by_gid.get(gid)
    rows.append((match_id, c_raw, c_sign, len(grp), l))

import math
neg = [(m, cr, cs, n, l) for m, cr, cs, n, l in rows if l is not None and (math.isfinite(cs) and cs < -0.3)]
print(f"\nmaps with corr(mid, sign(nw_adv)) < -0.3: {len(neg)}")
for m, cr, cs, n, l in sorted(neg, key=lambda r: r[2]):
    u = uni_by_cid.get(l["condition_id"], {})
    rw = int(l["resolved_outcome_index"]) == int(l["radiant_token_index"])
    print(f"  gid={m} num={l['game_number']} corr_sign={cs:.2f} corr_raw={cr:.2f} n={n} "
          f"radiant_win={rw} assign={l['assignment']} q={u.get('question')!r}")

weak = [(m, cr, cs, n, l) for m, cr, cs, n, l in rows if l is not None and (not math.isfinite(cs) or abs(cs) < 0.15)]
print(f"\nmaps with |corr_sign| < 0.15 (uncorrelated — wrong game? illiquid?): {len(weak)}")
for m, cr, cs, n, l in sorted(weak, key=lambda r: abs(r[2]) if math.isfinite(r[2]) else 0):
    u = uni_by_cid.get(l["condition_id"], {})
    rw = int(l["resolved_outcome_index"]) == int(l["radiant_token_index"])
    print(f"  gid={m} num={l['game_number']} corr_sign={cs:.2f} corr_raw={cr:.2f} n={n} "
          f"radiant_win={rw} assign={l['assignment']} q={u.get('question')!r}")

cvals = pd.Series([r[2] for r in rows if math.isfinite(r[2])])
print("\ncorr_sign distribution:", cvals.describe(percentiles=[.01,.05,.25,.5,.75,.95,.99]).round(3).to_dict())
