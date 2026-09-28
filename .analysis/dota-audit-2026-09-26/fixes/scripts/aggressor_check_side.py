"""After-fix aggressor_check: book move right after the trade vs computed `side`.

`side` = taker_side when the taker traded this file's token, flipped otherwise —
the column the backtest bridge now writes for the framework. Expectation: on
both taker_asset groups buy -> mid up, sell -> mid down in the majority.
"""

import bisect
from pathlib import Path

import pandas as pd

from shared.utils.telonex_book import load_token_book

c = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet").set_index("match_id")
row = c.loc[8982107035]
t0 = str(row.token_id_0)
day = "2026-09-04"
base = "data/raw/telonex/polymarket/onchain_fills"
f0 = pd.read_parquet(f"{base}/asset_id={t0}/{day}.parquet")

own = f0.taker_asset_id.astype(str) == f0.asset_id.astype(str)
f0["side"] = f0.taker_side.where(own, f0.taker_side.map({"buy": "sell", "sell": "buy"}))

start = int(f0.block_timestamp_us.min()) - 60_000_000
end = int(f0.block_timestamp_us.max()) + 60_000_000
book = load_token_book(
    token_id=t0, start_us=start, end_us=end, telonex_root=Path("data/raw/telonex/polymarket")
)
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
    res.append((str(r.taker_asset_id) == t0, r.side, move))
d = pd.DataFrame(res, columns=["taker_asset_is_file", "side", "mid_move_6s"])
print(pd.crosstab([d.taker_asset_is_file, d.side], d.mid_move_6s))
