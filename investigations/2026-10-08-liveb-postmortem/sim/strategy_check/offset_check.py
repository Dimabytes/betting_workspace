"""Which book time makes the calc match live: the mismatched placements of quote_check.py.

usage (from esports-trader, after quote_check.py):
  PYTHONPATH=src:scripts:<this dir> .venv/bin/python offset_check.py <quotes.csv>
"""

import sys

import numpy as np
import pandas as pd
from quote_check import BOOKS, CATALOG, DAY, H3_UNTIL_US, best_levels

from shared.utils.telonex_book import PAIR_SUM_TOLERANCE
from strategy.signals import normalize_pair_mids
from strategy.two_sided import HALF_SPREAD_TICKS, SKEW_PER_SHARE, TICK, inventory_skew, price_bids

OFFSETS_MS = (-1000, -500, -250, -100, -50, -20, 0, 20, 50, 100, 250)


def calc(fair: float, net: float, leg: int, t_us: int) -> float:
    skew = inventory_skew(fair=fair, net_shares=net, skew_per_share=SKEW_PER_SHARE)
    half = 3 if t_us < H3_UNTIL_US else HALF_SPREAD_TICKS
    ticks = price_bids(fair=fair, half_spread_ticks=half, skew=skew)
    return round((ticks.yes_ticks if leg == 0 else ticks.no_ticks) * TICK, 2)


frame = pd.read_csv(sys.argv[1])
frame["exact"] = (frame.live_price - frame.calc_price).abs() < 1e-9
catalog = pd.read_parquet(CATALOG).set_index("match_id")
rows = []
for match_id, group in frame[~frame.exact].groupby("match_id"):
    entry = catalog.loc[match_id]
    radiant = int(entry.radiant_token_index)
    books = [best_levels(BOOKS / f"asset_id={entry[f'token_id_{i}']}" / f"{DAY}.parquet") for i in (0, 1)]
    for rec in group.itertuples():
        hits = []
        for offset in OFFSETS_MS:
            t = rec.t_us + offset * 1000
            mids = []
            for ts, bid, ask in books:
                i = int(np.searchsorted(ts, t, side="right")) - 1
                mids.append((bid[i] + ask[i]) / 2.0)
            fair = normalize_pair_mids(
                radiant_mid=mids[radiant], dire_mid=mids[1 - radiant], tolerance=PAIR_SUM_TOLERANCE
            )
            leg = 0 if rec.is_radiant else 1
            if fair is not None and abs(calc(fair, rec.net, leg, rec.t_us) - rec.live_price) < 1e-9:
                hits.append(offset)
        # net as if the last fill were not yet known, or one more fill already known
        raw = (rec.fair - (3 if rec.t_us < H3_UNTIL_US else HALF_SPREAD_TICKS) * TICK) / TICK
        rows.append({"match_id": match_id, "hits": hits, "frac": raw - np.floor(raw), "net": rec.net})
out = pd.DataFrame(rows)
print(f"mismatched {len(out)}")
print(f"matched at some offset {out.hits.map(bool).mean():.3f}")
for offset in OFFSETS_MS:
    print(f"  offset {offset:+5d} ms: {out.hits.map(lambda h, o=offset: o in h).sum()}")
print(f"nearest-earlier offset that matches: "
      f"{out.hits.map(lambda h: max([o for o in h if o <= 0], default=None)).value_counts().sort_index().to_string()}")
print(f"net != 0 among unmatched by any offset: {(out[~out.hits.map(bool)].net != 0).mean():.3f}")
