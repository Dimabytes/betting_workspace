"""Estimate fills live would catch that sim misses: trades-channel hits at our price
in (cancel_ack, cancel_ack + feed_lag] using real-time `trades` timestamps (fill-devin)."""

import pandas as pd
import pyarrow.parquet as pq
from collections import defaultdict

QE = "data/backtests/dota_maker/LIVE/seed0/quote_events.parquet"
FILLS = "data/backtests/dota_maker/LIVE/seed0/fills.parquet"
ROOT = "data/raw/telonex/polymarket"

qe = pq.read_table(QE).to_pandas()
fills = pd.read_parquet(FILLS)

from shared.constants.paths import MATCH_CATALOG_PATH
cat = pd.read_parquet(MATCH_CATALOG_PATH)
tok = {int(r.match_id): (str(r.token_id_0), str(r.token_id_1)) for r in cat.itertuples()}

cnl = qe[qe.kind == "cancel_ack"][["match_id", "ts_ns", "order_id", "side", "price", "token_index"]]
print("cancel_acks:", len(cnl))

# group cancels by (match, token_index, day)
need = defaultdict(list)
for r in cnl.itertuples():
    toks = tok.get(int(r.match_id))
    if toks is None:
        continue
    tid = toks[int(r.token_index)]
    day = pd.Timestamp(r.ts_ns, unit="ns", tz="UTC").strftime("%Y-%m-%d")
    need[(tid, day)].append((int(r.ts_ns), str(r.side), float(r.price)))

print("unique (asset,day) to load:", len(need))

# load trades per (asset, day), only columns needed
trade_cache = {}
missing = 0
for key in need:
    tid, day = key
    try:
        t = pq.read_table(
            f"{ROOT}/trades/asset_id={tid}/{day}.parquet",
            columns=["timestamp_us", "price", "side", "size"],
        ).to_pandas()
        t["ts_ns"] = t.timestamp_us * 1000
        t["price"] = t.price.astype(float)
        trade_cache[key] = t.sort_values("ts_ns")
    except Exception:
        trade_cache[key] = None
        missing += 1

for lag_ms in (60, 100, 200, 500):
    hits = 0
    eligible = 0
    for (tid, day), cancels in need.items():
        t = trade_cache.get((tid, day))
        if t is None or t.empty:
            continue
        ts = t.ts_ns.values
        for cts, side, price in cancels:
            # a resting BUY at price P is hit by taker-side SELL prints at P; SELL by buy prints
            opp = "sell" if side.upper() == "BUY" else "buy"
            lo, hi = cts, cts + lag_ms * 1_000_000
            i0 = ts.searchsorted(lo, "left")
            i1 = ts.searchsorted(hi, "right")
            eligible += 1
            if any(
                (t.side.iloc[i].lower() == opp) and abs(t.price.iloc[i] - price) < 1e-9
                for i in range(i0, i1)
            ):
                hits += 1
    print(f"lag +{lag_ms}ms: cancels with a contra-side trade at our px: {hits} / {eligible}")

# Same for fills: what fraction of fills arrived while order was in 'accepted' resting state,
# versus within 85ms of submit (i.e., filled during insert latency — impossible, order not yet
# live) — sanity only.
sub = qe[qe.kind == "submitted"].set_index("order_id")
acc = qe[qe.kind == "accepted"].set_index("order_id")
early_fill = 0
for oid, frow in fills.set_index("order_id").iterrows():
    if oid in acc.index and frow.ts_ns < acc.loc[oid].ts_ns:
        early_fill += 1
print("fills stamped BEFORE accept (should be ~0):", early_fill)
