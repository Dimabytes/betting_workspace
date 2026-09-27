"""Ground truth check: trades.channel side (per-asset) vs onchain taker_side (fill-devin)."""

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


for i in (0, 1):
    tid = TOKENS[i]
    f = load("onchain_fills", tid)
    f = f[(f.block_timestamp_us >= START_US) & (f.block_timestamp_us <= END_US)].copy()
    f["price_f"] = f.price.astype(float)
    f["amt_f"] = f.amount.astype(float)
    t = load("trades", tid)
    t = t[(t.timestamp_us >= START_US) & (t.timestamp_us <= END_US)].copy()
    t["price_f"] = t.price.astype(float)
    t["size_f"] = t["size"].astype(float)
    print(f"\n=== token{i}: fills={len(f)} trades={len(t)} ===")
    # join on price+size approx (within same minute); compare side
    rows = []
    for _, fr in f.iterrows():
        cand = t[
            (abs(t.price_f - fr.price_f) < 1e-9)
            & (abs(t.size_f - fr.amt_f) / fr.amt_f < 0.05)
            & (t.timestamp_us >= fr.block_timestamp_us - 120_000_000)
            & (t.timestamp_us <= fr.block_timestamp_us + 120_000_000)
        ]
        if cand.empty:
            continue
        tr = cand.iloc[0]
        rows.append(
            dict(
                taker_side=str(fr.taker_side),
                taker_asset=str(fr.taker_asset_id) == tid,
                trade_side=str(tr.side),
                mirrored=bool(fr.mirrored),
                lag_s=(int(fr.block_timestamp_us) - int(tr.timestamp_us)) / 1e6,
            )
        )
    m = pd.DataFrame(rows)
    if m.empty:
        print("  no join")
        continue
    m["agree"] = m.taker_side == m.trade_side
    print(m.groupby(["taker_asset", "taker_side", "trade_side"]).size().to_string())
    print("\nagree rate by taker_asset==file:")
    print(m.groupby("taker_asset").agree.describe())
    print("lag trades->blockstamp s: p10/p50/p90 =",
          m.lag_s.quantile([0.1, 0.5, 0.9]).round(2).tolist())
    print("lag range:", m.lag_s.min().round(2), m.lag_s.max().round(2))
