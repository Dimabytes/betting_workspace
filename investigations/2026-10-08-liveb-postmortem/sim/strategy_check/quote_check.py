"""Live B placements (orders_out) against the two-sided quote math on reconstructed inputs.

For every BUY in an orders_out row of the 5 catalog maps: the Telonex best bid/ask
of both tokens at the decision time, B's net shares from its own fills as the live
core knew them (journal receipt time of the size_matched increase), then
fair = normalize_pair_mids, skew, price_bids, scale_order_size: the functions the
backtest port and the live core share. Size is shown both ways: the port rounds,
the live core floors to 0.01.

usage (from esports-trader):
  PYTHONPATH=src:scripts .venv/bin/python quote_check.py <journal> <out.csv>
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from shared.utils.telonex_book import PAIR_SUM_TOLERANCE
from shared.utils.trading import share_floor
from strategy.signals import normalize_pair_mids
from strategy.two_sided import (
    HALF_SPREAD_TICKS,
    NET_MAX_SHARES,
    ORDER_SHARES,
    SKEW_PER_SHARE,
    TICK,
    inventory_skew,
    price_bids,
    scale_order_size,
)

BOOKS = Path("data/raw/telonex/polymarket/book_snapshot_full")
CATALOG = Path("data/new_processed/match_catalog/match_catalog.parquet")
DAY = "2026-10-08"
MATCH_IDS = (9034957701, 9035220432, 9035256154, 9035318247, 9035434210)
# Live B ran half-spread 3 ticks until 14:47:23 UTC and 6 from 14:48:39 (sim/compare.py).
H3_UNTIL_US = pd.Timestamp("2026-10-08T14:48:00Z").value // 1000


def best_levels(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per snapshot row: timestamp_us, best bid, best ask (nan when the side is empty)."""
    stamps, bids, asks = [], [], []
    for batch in pq.ParquetFile(path).iter_batches(batch_size=50_000, columns=["timestamp_us", "bids", "asks"]):
        stamps.append(batch.column("timestamp_us").to_numpy())
        for side, out, reduce in (("bids", bids, np.maximum), ("asks", asks, np.minimum)):
            levels = batch.column(side)
            offsets = levels.offsets.to_numpy()
            offsets = offsets - offsets[0]
            prices = pc.cast(pc.struct_field(levels.flatten(), "price"), pa.float64()).to_numpy()
            best = np.full(len(offsets) - 1, np.nan)
            filled = offsets[1:] > offsets[:-1]
            if prices.size:
                best[filled] = reduce.reduceat(prices, offsets[:-1][filled])
            out.append(best)
    return np.concatenate(stamps), np.concatenate(bids), np.concatenate(asks)


def net_timeline(rows: list[dict], tokens: dict[str, int]) -> tuple[np.ndarray, np.ndarray]:
    """Receipt time (us) and cumulative shares per token index after each size_matched increase."""
    matched: dict[str, float] = defaultdict(float)
    stamps: list[int] = []
    deltas: list[tuple[float, float]] = []
    for row in rows:
        if row["kind"] != "user_order":
            continue
        data = row["data"]
        index = tokens.get(data["asset_id"])
        if index is None or data["side"] != "BUY":
            continue
        now = float(data["size_matched"])
        step = now - matched[data["id"]]
        if step <= 1e-9:
            continue
        matched[data["id"]] = now
        stamps.append(int(row["ts"] * 1_000_000))
        deltas.append((step, 0.0) if index == 0 else (0.0, step))
    order = np.argsort(stamps, kind="stable")
    return np.asarray(stamps)[order], np.cumsum(np.asarray(deltas).reshape(-1, 2)[order], axis=0)


def main() -> None:
    journal, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = [json.loads(line) for line in journal.open() if line.strip()]
    catalog = pd.read_parquet(CATALOG).set_index("match_id")
    records = []
    for match_id in MATCH_IDS:
        entry = catalog.loc[match_id]
        tokens = {str(entry.token_id_0): 0, str(entry.token_id_1): 1}
        radiant = int(entry.radiant_token_index)
        books = [best_levels(BOOKS / f"asset_id={entry[f'token_id_{i}']}" / f"{DAY}.parquet") for i in (0, 1)]
        net_ts, held = net_timeline(rows, tokens)
        for row in rows:
            if row["kind"] != "orders_out":
                continue
            t_us = int(row["ts"] * 1_000_000)
            for placed in row["data"]:
                index = tokens.get(placed["token_id"])
                if index is None or placed["side"] != "BUY":
                    continue
                mids, ages = [], []
                for ts, bid, ask in books:
                    i = int(np.searchsorted(ts, t_us, side="right")) - 1
                    mids.append((bid[i] + ask[i]) / 2.0 if i >= 0 else np.nan)
                    ages.append((t_us - ts[i]) / 1e6 if i >= 0 else np.nan)
                k = int(np.searchsorted(net_ts, t_us, side="right")) - 1
                qty = held[k] if k >= 0 else np.zeros(2)
                net = float(qty[radiant] - qty[1 - radiant])
                fair = normalize_pair_mids(
                    radiant_mid=mids[radiant], dire_mid=mids[1 - radiant], tolerance=PAIR_SUM_TOLERANCE
                )
                rec = {
                    "match_id": match_id, "t_us": t_us, "token_index": index, "is_radiant": index == radiant,
                    "live_price": float(placed["price"]), "live_size": float(placed["size"]),
                    "radiant_mid": mids[radiant], "dire_mid": mids[1 - radiant],
                    "book_age_s": max(ages), "net": net, "fair": fair,
                }
                if fair is not None:
                    skew = inventory_skew(fair=fair, net_shares=net, skew_per_share=SKEW_PER_SHARE)
                    half = 3 if t_us < H3_UNTIL_US else HALF_SPREAD_TICKS
                    ticks = price_bids(fair=fair, half_spread_ticks=half, skew=skew)
                    leg = 0 if index == radiant else 1
                    size = scale_order_size(
                        size_shares=ORDER_SHARES, net_shares=net, net_max_shares=NET_MAX_SHARES, token_index=leg
                    )
                    rec["calc_price"] = round((ticks.yes_ticks if leg == 0 else ticks.no_ticks) * TICK, 2)
                    rec["port_size"] = round(size, 2)
                    rec["core_size"] = share_floor(size)
                records.append(rec)
    frame = pd.DataFrame(records)
    frame.to_csv(out, index=False)
    frame["tick_diff"] = ((frame.live_price - frame.calc_price) / TICK).round()
    print(f"placements {len(frame)}, no fair {int(frame.fair.isna().sum())}")
    print("price diff in ticks (live - calc):")
    print(frame.tick_diff.value_counts().sort_index().to_string())
    print(f"size = core floor {(frame.live_size - frame.core_size).abs().lt(1e-9).mean():.3f}, "
          f"size = port round {(frame.live_size - frame.port_size).abs().lt(1e-9).mean():.3f}")
    print(frame.groupby("match_id").tick_diff.agg(lambda d: (d == 0).mean()).rename("price exact").to_string())


if __name__ == "__main__":
    main()
