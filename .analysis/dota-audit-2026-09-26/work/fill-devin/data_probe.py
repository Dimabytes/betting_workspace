"""Probe Telonex book/fill timestamp relations for one map (fill-devin audit)."""

import pandas as pd
import pyarrow.parquet as pq

ROOT = "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket"
DAY = "2026-09-04"
TOKENS = {
    0: "36946747883824898023808254946925983326269817251004902998289698829747635065694",
    1: "82086840383991785924234653677813734460597199808125936662356188867404162091207",
}
def load(channel, token):
    path = f"{ROOT}/{channel}/asset_id={token}/{DAY}.parquet"
    return pq.read_table(path).to_pandas()


books = {i: load("book_snapshot_full", t) for i, t in TOKENS.items()}
fills = {i: load("onchain_fills", t) for i, t in TOKENS.items()}
trades = {i: load("trades", t) for i, t in TOKENS.items()}

# restrict to match window (horn 13:11:02Z - 2min, ended 13:53:52Z + 1min)
START_US, END_US = 1788527342000000, 1788530092000000

for i in (0, 1):
    b = books[i]
    b = b[(b.timestamp_us >= START_US) & (b.timestamp_us <= END_US)].sort_values("timestamp_us")
    gaps = b.timestamp_us.diff().dropna() / 1e6
    print(f"token{i}: {len(b)} book snapshots in window")
    print("  cadence s: p10/p50/p90/max =",
          gaps.quantile(0.1).round(3), gaps.quantile(0.5).round(3),
          gaps.quantile(0.9).round(3), gaps.max().round(1))
    lag = (b.local_timestamp_us - b.timestamp_us) / 1e6
    print("  local-ts minus ts (s): p50=", lag.quantile(0.5).round(3), "p90=", lag.quantile(0.9).round(3))

    f = fills[i]
    f = f[(f.block_timestamp_us >= START_US) & (f.block_timestamp_us <= END_US)]
    print(f"  onchain fills in window: {len(f)}, sides:", f.maker_side.value_counts().to_dict(),
          "taker:", f.taker_side.value_counts().to_dict())
    uniq_blk = f.block_timestamp_us.nunique()
    print(f"  unique block timestamps: {uniq_blk}; fills/block: {(len(f)/max(uniq_blk,1)):.2f}")
    mod = (f.block_timestamp_us % 1_000_000)
    print("  block_ts mod-1s spread:", mod.min(), "..", mod.max(), "unique:", mod.nunique())

    t = trades[i]
    t = t[(t.timestamp_us >= START_US) & (t.timestamp_us <= END_US)]
    print(f"  trades channel rows in window: {len(t)}")

# Per-fill: nearest book snapshot before/after block stamp; check whether level
# at fill price already shrank before the fill's stamped time.
print("\n=== per-fill level inspection (token1 radiant, winner) ===")
b = books[1][(books[1].timestamp_us >= START_US) & (books[1].timestamp_us <= END_US)].sort_values("timestamp_us")
f = fills[1][(fills[1].block_timestamp_us >= START_US) & (fills[1].block_timestamp_us <= END_US)].sort_values("block_timestamp_us")
print("sample fills:")
print(f[["block_timestamp_us", "price", "amount", "maker_side", "taker_side"]].head(15).to_string())

# build level series for asks & bids: snapshot ts -> {price: size}
def level_frame(bk, side):
    rows = []
    for ts, lv in zip(bk.timestamp_us, bk[side]):
        for lvl in lv:
            rows.append((ts, float(lvl[0]), float(lvl[1])))
    return pd.DataFrame(rows, columns=["ts_us", "price", "size"])

asks = level_frame(b, "asks")
bids = level_frame(b, "bids")
print("ask levels rows:", len(asks), "bid levels:", len(bids))

# For each fill: what side of the book was consumed? maker_side describes resting order side.
# A fill with maker_side=Sell consumed ask depth at price.
lag_stats = []
for _, fr in f.head(400).iterrows():
    px = float(fr.price)
    side = "asks" if str(fr.maker_side).lower() in ("sell", "ask") else "bids"
    lvl = asks if side == "asks" else bids
    at_px = lvl[lvl.price == px]
    before = at_px[at_px.ts_us <= fr.block_timestamp_us]
    after = at_px[at_px.ts_us > fr.block_timestamp_us]
    if before.empty or after.empty:
        continue
    lag_stats.append({
        "fill_ts": fr.block_timestamp_us,
        "size": float(fr.amount),
        "lvl_before": before.iloc[-1].size,
        "lvl_after": after.iloc[0].size,
        "before_ts": before.iloc[-1].ts_us,
        "after_ts": after.iloc[0].ts_us,
    })

lag = pd.DataFrame(lag_stats)
if not lag.empty:
    lag["shrank_at_fill"] = lag.lvl_after < lag.lvl_before - 1e-9
    lag["snap_to_fill_s"] = (lag.fill_ts - lag.before_ts) / 1e6
    lag["fill_to_next_s"] = (lag.after_ts - lag.fill_ts) / 1e6
    print(lag.describe().to_string())
    print("level shrank across fill stamp:", lag.shrank_at_fill.mean().round(3))
