"""Cancel-latency asymmetry + in-flight fills on LIVE seed0 (fill-devin audit)."""

import pandas as pd
import pyarrow.parquet as pq

QE = "data/backtests/dota_maker/LIVE/seed0/quote_events.parquet"
FILLS = "data/backtests/dota_maker/LIVE/seed0/fills.parquet"
ROOT = "data/raw/telonex/polymarket"

qe = pq.read_table(QE).to_pandas()
fills = pq.read_table(FILLS).to_pandas()

print("quote kinds:", qe.kind.value_counts().to_dict())

# order lifecycle per order_id
sub = qe[qe.kind == "submitted"].set_index("order_id")
acc = qe[qe.kind == "accepted"].set_index("order_id")
creq = qe[qe.kind == "cancel_request"].set_index("order_id")
cack = qe[qe.kind == "cancel_ack"].set_index("order_id")
cnl = qe[qe.kind == "canceled"].set_index("order_id")

# 1) submit->accept latency (insert latency check)
common = sub.index.intersection(acc.index)
lat = (acc.loc[common].ts_ns - sub.loc[common].ts_ns) / 1e6
print("\nsubmit->accept ms: p50/p90/max", lat.quantile([0.5, 0.9]).tolist(), lat.max())

# 2) cancel_request -> cancel_ack latency (should be ~85ms strategy delay + ~0 engine)
common = creq.index.intersection(cack.index)
clat = (cack.loc[common].ts_ns - creq.loc[common].ts_ns) / 1e6
print("cancel_request->ack ms: p50/p90/max", clat.quantile([0.5, 0.9]).tolist(), clat.max())

# 3) fills landing while a cancel was pending (cancel_request < fill <= cancel_ack)
fl = fills.set_index("order_id")
in_flight = 0
for oid, frow in fl.iterrows():
    if oid in creq.index and oid in cack.index:
        if creq.loc[oid].ts_ns < frow.ts_ns <= cack.loc[oid].ts_ns:
            in_flight += 1
print("fills during pending cancel (in-flight):", in_flight, "of", len(fl))

# 4) fills within X ms AFTER cancel_request (racing the delay)
deltas = []
for oid, frow in fl.iterrows():
    if oid in creq.index:
        deltas.append((frow.ts_ns - creq.loc[oid].ts_ns) / 1e6)
import numpy as np
d = np.array(deltas)
print("fill_ts - cancel_request ms: n=", len(d))
for q in (0.05, 0.1, 0.25, 0.5):
    print("  p", q, np.quantile(d, q).round(1))
print("  fills within 0..85ms after cancel_request:", ((d > 0) & (d <= 85)).sum())

# 5) queue_ahead stats on fills
print("\nqueue_ahead on fills: describe")
print(fl.queue_ahead.describe())

# 6) per-match fills for the cancel-window estimate: for each canceled order, look for
# onchain fills at same token+price within [cancel_ack, cancel_ack+80ms] (would-have-filled candidates)
# Need token_id mapping: results has token index; map match->token ids via catalog
from shared.constants.paths import MATCH_CATALOG_PATH
cat = pd.read_parquet(MATCH_CATALOG_PATH)
tok = {int(r.match_id): (str(r.token_id_0), str(r.token_id_1)) for r in cat.itertuples()}

# canceled orders only, with side+price
cnl_rows = qe[qe.kind == "canceled"][["match_id", "ts_ns", "order_id", "side", "price", "token_index"]]
print("\ncanceled orders:", len(cnl_rows))

# build fill time index per (asset_id) from onchain for a sample of matches
import datetime as dt
sel = cnl_rows.sample(min(4000, len(cnl_rows)), random_state=0)
hits60 = 0
hits140 = 0
checked = 0
missing = 0
by_match = sel.groupby("match_id")
for mid, grp in by_match:
    toks = tok.get(mid)
    if toks is None:
        continue
    for _, r in grp.iterrows():
        tid = toks[int(r.token_index)]
        day = pd.Timestamp(r.ts_ns, unit="ns", tz="UTC").strftime("%Y-%m-%d")
        try:
            oc = pq.read_table(
                f"{ROOT}/onchain_fills/asset_id={tid}/{day}.parquet",
                columns=["block_timestamp_us", "price"],
            ).to_pandas()
        except Exception:
            missing += 1
            continue
        checked += 1
        lo = r.ts_ns // 1000
        w = oc[(oc.block_timestamp_us * 1000 > r.ts_ns) & (oc.block_timestamp_us * 1000 <= r.ts_ns + 140_000_000)]
        w = w[abs(w.price.astype(float) - r.price) < 0.005]
        if len(w):
            hits140 += 1
            w60 = w[w.block_timestamp_us * 1000 <= r.ts_ns + 60_000_000]
            if len(w60):
                hits60 += 1
print(f"checked {checked} cancels (missing days {missing}); onchain fill at same px within +60ms: {hits60}, +140ms: {hits140}")
