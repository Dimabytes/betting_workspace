"""Measure fill-stamp vs book-snapshot inversion for queue double-decrement (fill-devin)."""

import pandas as pd
import pyarrow.parquet as pq

ROOT = "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket"
DAY = "2026-09-04"
TOKENS = {
    0: "36946747883824898023808254946925983326269817251004902998289698829747635065694",
    1: "82086840383991785924234653677813734460597199808125936662356188867404162091207",
}
START_US, END_US = 1788527342000000, 1788530092000000


def load(channel, token):
    return pq.read_table(f"{ROOT}/{channel}/asset_id={token}/{DAY}.parquet").to_pandas()


def level_series(bk):
    """Per (side, price): sorted arrays of (ts, size) across snapshots."""
    out = {}
    for side in ("bids", "asks"):
        rows = []
        for ts, lv in zip(bk.timestamp_us, bk[side]):
            for l in lv:
                rows.append((ts, float(l["price"]), float(l["size"])))
        out[side] = pd.DataFrame(rows, columns=["ts_us", "price", "size"])
    return out


for i in (0, 1):
    b = load("book_snapshot_full", TOKENS[i])
    b = b[(b.timestamp_us >= START_US) & (b.timestamp_us <= END_US)].sort_values("timestamp_us")
    f = load("onchain_fills", TOKENS[i])
    f = f[(f.block_timestamp_us >= START_US) & (f.block_timestamp_us <= END_US)].sort_values(
        "block_timestamp_us"
    )
    print(f"\n=== token{i}: {len(b)} snaps, {len(f)} fills ===")

    levels = level_series(b)
    snap_ts = b.timestamp_us.values

    inv = 0
    same = 0
    fresh = 0
    results = []
    for _, fr in f.iterrows():
        fts = int(fr.block_timestamp_us)
        px = float(fr.price)
        # maker_side=ask/sell -> resting ask consumed -> look at asks side
        side = "asks" if str(fr.maker_side).lower() in ("sell", "ask") else "bids"
        lvl = levels[side]
        lvl = lvl[lvl.price == px]
        before = lvl[lvl.ts_us <= fts]
        after = lvl[lvl.ts_us > fts]
        if before.empty or after.empty:
            continue
        last_before_ts = int(before.ts_us.iloc[-1])
        last_before_sz = float(before["size"].iloc[-1])
        first_after_sz = float(after["size"].iloc[0])
        first_after_ts = int(after.ts_us.iloc[0])
        # number of snapshots strictly before fill stamp
        n_snaps_before = (snap_ts <= fts).sum()
        results.append(
            dict(
                ts=fts,
                px=px,
                amt=float(fr.amount),
                gap_to_prev_snap_s=(fts - last_before_ts) / 1e6,
                gap_to_next_snap_s=(first_after_ts - fts) / 1e6,
                shrank=first_after_sz < last_before_sz - 1e-9,
                delta_sz=first_after_sz - last_before_sz,
            )
        )
    df = pd.DataFrame(results)
    if df.empty:
        print("  no fills with both-sided level data")
        continue
    print("  fills with level context:", len(df))
    print("  snap->fill gap s p50/p90:", df.gap_to_prev_snap_s.quantile([0.5, 0.9]).round(2).tolist())
    print("  fill->next snap s p50/p90:", df.gap_to_next_snap_s.quantile([0.5, 0.9]).round(2).tolist())
    print("  level shrank across fill stamp:", df.shrank.mean().round(3))
    print("  delta_sz distribution p10/p50/p90:", df.delta_sz.quantile([0.1, 0.5, 0.9]).round(2).tolist())
    neg = df[df.delta_sz < -1e-9]
    print("  |delta| >= amt/2 on shrinks:", (abs(neg.delta_sz) >= neg.amt / 2).mean().round(3) if len(neg) else "n/a")

# cross-check trades-channel vs onchain block stamps for the same assets
t1 = load("trades", TOKENS[1])
t1 = t1[(t1.timestamp_us >= START_US) & (t1.timestamp_us <= END_US)]
f1 = load("onchain_fills", TOKENS[1])
f1 = f1[(f1.block_timestamp_us >= START_US) & (f1.block_timestamp_us <= END_US)]
print("\ntoken1: trades channel n=", len(t1), " onchain n=", len(f1))
print("trades side:", t1.side.value_counts().to_dict())
print("trades price grid:", sorted(t1.price.astype(float).round(2).unique())[:20])
