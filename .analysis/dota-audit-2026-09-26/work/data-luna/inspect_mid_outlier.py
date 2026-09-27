from pathlib import Path

import pandas as pd

from shared.constants.paths import RAW_TELONEX_POLYMARKET_DIR
from shared.utils.match_catalog import load_match_catalog
from shared.utils.telonex_book import US_PER_SECOND, load_token_book
from market_data.build_market_data import market_seconds_cache_path


match_id = 8642367784
market_second = 2575
catalog = load_match_catalog(Path("data/new_processed/match_catalog/match_catalog.parquet"))
entry = catalog[match_id]
cache = pd.read_parquet(market_seconds_cache_path(match_id))
row = cache.loc[cache.second.eq(market_second)].iloc[0]
target = int(row.state_ts_us)
print("match", match_id, "second", market_second, "timestamp_us", target, "cached_status", row.market_status, "cached_p", row.market_p_radiant, "radiant_token_index", entry.radiant_token_index)
for index, token in enumerate(entry.gamma.token_ids):
    book = load_token_book(token_id=token, start_us=target - 8 * US_PER_SECOND, end_us=target + 8 * US_PER_SECOND, telonex_root=RAW_TELONEX_POLYMARKET_DIR)
    print("TOKEN", index, token, "radiant" if index == entry.radiant_token_index else "dire")
    for timestamp, bid, ask in zip(book.timestamps_us, book.bids, book.asks, strict=True):
        if abs(timestamp - target) <= 8 * US_PER_SECOND:
            print(f"  dt_s={(timestamp-target)/US_PER_SECOND:+.6f} bid={bid} ask={ask}")
