"""Is onchain_fills.taker_side the aggressor on THIS file's book? Compare with the book move right after the trade."""
import pandas as pd

from shared.utils.telonex_book import load_token_book

c = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet").set_index("match_id")
row = c.loc[8982107035]
t0, t1 = str(row.token_id_0), str(row.token_id_1)
day = "2026-09-04"
base = "data/raw/telonex/polymarket/onchain_fills"
f0 = pd.read_parquet(f"{base}/asset_id={t0}/{day}.parquet")
f1 = pd.read_parquet(f"{base}/asset_id={t1}/{day}.parquet")
key = ["tx_hash", "log_index"]
m = f0.merge(f1, on=key, suffixes=("_0", "_1"))
print("rows file0/file1/shared:", len(f0), len(f1), len(m))
print("price_0+price_1==1 share:", float(((m.price_0.astype(float) + m.price_1.astype(float)) - 1).abs().lt(1e-6).mean()))
print("taker_side same in both files:", float((m.taker_side_0 == m.taker_side_1).mean()))
print("taker_asset==file asset (file0):", float((f0.taker_asset_id.astype(str) == f0.asset_id.astype(str)).mean()))
print("mirrored flag counts file0:", f0.mirrored.value_counts().to_dict())
print(pd.crosstab([f0.taker_asset_id.astype(str) == t0, f0.mirrored], f0.taker_side))
# book check: after a trade on file0's token, which side of THAT token's book shrank more (bid = seller hit, ask = buyer lifted)
start = int(f0.block_timestamp_us.min()) - 60_000_000
end = int(f0.block_timestamp_us.max()) + 60_000_000
book = load_token_book(token_id=t0, start_us=start, end_us=end, telonex_root=__import__("pathlib").Path("data/raw/telonex/polymarket"))
import bisect
ts = list(book.timestamps_us)
res = []
for _, r in f0.iterrows():
    t = int(r.block_timestamp_us)
    i0 = bisect.bisect_right(ts, t - 3_000_000) - 1
    i1 = bisect.bisect_right(ts, t + 3_000_000) - 1
    if i0 < 0 or i1 < 0:
        continue
    b0, a0, b1, a1 = book.bids[i0], book.asks[i0], book.bids[i1], book.asks[i1]
    if None in (b0, a0, b1, a1):
        continue
    move = "down" if (b1 + a1) < (b0 + a0) - 1e-9 else "up" if (b1 + a1) > (b0 + a0) + 1e-9 else "flat"
    res.append((str(r.taker_asset_id) == t0, r.taker_side, move))
d = pd.DataFrame(res, columns=["taker_asset_is_file", "taker_side", "mid_move_6s"])
print(pd.crosstab([d.taker_asset_is_file, d.taker_side], d.mid_move_6s))
