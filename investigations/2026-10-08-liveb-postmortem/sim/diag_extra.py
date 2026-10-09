"""Why does the sim fill where live B at the same price did not? Per extra fill: timing, queue, prints.

Run from esports-trader root: PYTHONPATH=src .venv/bin/python <this> <scratch liveb dir>
"""

import bisect
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from backtest.onchain_retime import add_timestamp_us, load_trade_times
from backtest.onchain_side import add_aggressor_side
from backtest.strip_own_book import _Resting, load_resting_events, resting_by_token_at

LIVEB = Path(sys.argv[1])
VARIANT = sys.argv[2]
RUNS = Path(sys.argv[3])
JOURNAL = LIVEB / "live.jsonl"
RAW = Path("data/raw/telonex/polymarket")
DAY = "2026-10-08"
US = 1_000_000
CATALOG = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet").set_index("match_id")
SEGMENTS = [
    (9034957701, "9034957701", "h3", 0, pd.Timestamp("2026-10-08T14:47:23Z").value),
    (9034957701, "9034957701", "957-h6", pd.Timestamp("2026-10-08T14:48:39Z").value, 2**62),
    (9035220432, "9035220432", "h6", 0, 2**62),
    (9035256154, "grid-3011822-m1", "h6", 0, 2**62),
    (9035318247, "9035318247", "h6", 0, 2**62),
    (9035434210, "9035434210", "h6", 0, 2**62),
]
ROWS = [json.loads(line) for line in JOURNAL.open()]


def price_key(price: float) -> int:
    return round(price * 100)


def load_b_orders(token_index: dict[str, int]) -> tuple[pd.DataFrame, list]:
    """Same reconstruction as run_b.load_b_events, plus a per-order table."""
    decisions: dict[tuple[int, int, float], list[int]] = defaultdict(list)
    for row in ROWS:
        if row["kind"] != "orders_out":
            continue
        for order in row["data"]:
            index = token_index.get(order["token_id"])
            if index is not None and order["side"] == "BUY":
                decisions[(index, price_key(float(order["price"])), round(float(order["size"]), 2))].append(int(row["ts"] * US))
    for times in decisions.values():
        times.sort()
    events = []
    matched: dict[str, float] = {}
    orders: dict[str, dict] = {}
    for row in ROWS:
        if row["kind"] != "user_order":
            continue
        data = row["data"]
        index = token_index.get(data["asset_id"])
        if index is None or data["side"] != "BUY":
            continue
        order_id = data["id"]
        event_us = int(data["timestamp"]) * 1000
        size_matched = float(data["size_matched"])
        if data["type"] == "PLACEMENT":
            if order_id in matched:
                continue
            price = float(data["price"])
            size = float(data["original_size"])
            times = decisions.get((index, price_key(price), round(size, 2)), [])
            position = bisect.bisect_left(times, event_us) - 1
            accept_us = times[position] if position >= 0 and event_us - times[position] <= 3 * US else event_us - 200_000
            events.append((accept_us, "accept", (order_id, _Resting(token_index=index, side="BUY", price=price, quantity=size - size_matched))))
            matched[order_id] = size_matched
            orders[order_id] = {"order_id": order_id, "tok": index, "px": round(price, 2), "size": size, "decided_us": accept_us,
                                "placed_us": event_us, "end_us": 2**62, "filled": 0.0, "first_fill_us": None}
            continue
        if order_id not in matched:
            continue
        if size_matched > matched[order_id] + 1e-9:
            events.append((event_us, "fill", (order_id, size_matched - matched[order_id])))
            orders[order_id]["filled"] += size_matched - matched[order_id]
            if orders[order_id]["first_fill_us"] is None:
                orders[order_id]["first_fill_us"] = event_us
            matched[order_id] = size_matched
        if data["type"] == "CANCELLATION":
            events.append((event_us, "remove", order_id))
            orders[order_id]["end_us"] = min(orders[order_id]["end_us"], event_us)
    placed_timed = [((payload[0] and orders[payload[0]]["placed_us"] - 50_000) if op == "accept" else t, op, payload) for t, op, payload in events]
    token_decisions: dict[int, list[int]] = defaultdict(list)
    for key, times in decisions.items():
        token_decisions[key[0]] += times
    for times in token_decisions.values():
        times.sort()
    for order in orders.values():
        if order["end_us"] < 2**62 or order["filled"] >= order["size"] - 1e-6:
            continue
        times = token_decisions[order["tok"]]
        position = bisect.bisect_right(times, order["placed_us"])
        if position < len(times):
            order["end_us"] = times[position] + 100_000
            events.append((order["end_us"], "remove", order["order_id"]))
    return pd.DataFrame(orders.values()), events, placed_timed


def load_prints(token: str) -> pd.DataFrame:
    """Onchain rows for this token with aggressor side and WS print time (same join as the backtest)."""
    path = RAW / "onchain_fills" / f"asset_id={token}" / f"{DAY}.parquet"
    table = add_aggressor_side(pq.read_table(path), path)
    trade_paths = [RAW / "trades" / f"asset_id={t}" / f"{DAY}.parquet" for t in TOKENS_OF_DAY]
    table, _ = add_timestamp_us(table, load_trade_times([p for p in trade_paths if p.is_file()]))
    frame = table.select(["timestamp_us", "block_timestamp_us", "price", "amount", "side", "maker", "tx_hash"]).to_pandas()
    frame["price"] = frame.price.astype(float)
    frame["amount"] = frame.amount.astype(float)
    return frame.sort_values("timestamp_us", ignore_index=True)


class Book:
    def __init__(self, token: str):
        book = pd.read_parquet(RAW / "book_snapshot_full" / f"asset_id={token}" / f"{DAY}.parquet", columns=["timestamp_us", "bids", "asks"])
        self.ts = book.timestamp_us.to_numpy()
        self.bids = book.bids.to_numpy()
        self.asks = book.asks.to_numpy()

    def level(self, t_us: int, px: float) -> float:
        i = max(int(np.searchsorted(self.ts, t_us, side="right")) - 1, 0)
        return sum(float(x["size"]) for x in self.bids[i] if price_key(float(x["price"])) == price_key(px))

    def best(self, t_us: int) -> tuple[float, float]:
        i = max(int(np.searchsorted(self.ts, t_us, side="right")) - 1, 0)
        bid = max((float(x["price"]) for x in self.bids[i]), default=0.0)
        ask = min((float(x["price"]) for x in self.asks[i]), default=1.0)
        return bid, ask


def min_level(book, events, t0: int, t1: int, tok: int, px: float) -> float:
    """Smallest stripped level size at px over book snapshots in (t0, t1]."""
    lo = int(np.searchsorted(book.ts, t0, side="right"))
    hi = int(np.searchsorted(book.ts, t1, side="right"))
    sizes = [book.level(int(book.ts[i]), px) - own_at(events, int(book.ts[i]), tok, px) for i in range(max(lo - 1, 0), hi)]
    return min(sizes) if sizes else float("nan")


def own_at(events, t_us: int, tok: int, px: float) -> float:
    resting = resting_by_token_at(events, wall_us=t_us).get(tok, {})
    return resting.get(("BUY", price_key(px)), 0.0)


TOKENS_OF_DAY: list[str] = []
for match_id, *_ in SEGMENTS:
    row = CATALOG.loc[match_id]
    TOKENS_OF_DAY += [str(row.token_id_0), str(row.token_id_1)]

timing_rows = []
extra_rows = []
for match_id, archive, run, t_from, t_to in SEGMENTS:
    row = CATALOG.loc[match_id]
    tokens = [str(row.token_id_0), str(row.token_id_1)]
    token_index = {tokens[0]: 0, tokens[1]: 1}
    live, b_events, b_placed = load_b_orders(token_index)
    all_events = sorted(b_events, key=lambda e: e[0])
    placed_events = sorted(b_placed, key=lambda e: e[0])
    books = [Book(t) for t in tokens]
    prints = [load_prints(t) for t in tokens]
    run_dir = RUNS / f"validation_join_delta02_x015_cut480_p45_liveb1008-{run}-{VARIANT}" / "seed0"
    if not (run_dir / "quote_events.parquet").is_file():
        print("skip", run_dir)
        continue
    fills = pd.read_parquet(run_dir / "fills.parquet")
    fills = fills[(fills.match_id == match_id) & (fills.ts_ns >= t_from) & (fills.ts_ns < t_to)]
    events = pd.read_parquet(run_dir / "quote_events.parquet")
    events = events[events.match_id == match_id]
    submitted = events[events.kind == "submitted"].set_index("order_id").ts_ns
    cancel_req = events[events.kind == "cancel_request"].sort_values("ts_ns")
    accepted = events[events.kind == "accepted"].set_index("order_id").ts_ns
    # sim submit vs nearest live placement at the same tok/px
    for ev in events[(events.kind == "submitted") & (events.ts_ns >= t_from) & (events.ts_ns < t_to)].itertuples():
        t_us = ev.ts_ns // 1000
        cand = live[(live.tok == ev.token_index) & (live.px == round(ev.price, 2))]
        if cand.empty:
            timing_rows.append({"match_id": match_id, "seg": run, "sim_vs_live_s": np.nan})
            continue
        d = (cand.decided_us - t_us) / US
        nearest = d.iloc[int(np.argmin(np.abs(d.to_numpy())))]
        timing_rows.append({"match_id": match_id, "seg": run, "sim_vs_live_s": float(nearest) if abs(nearest) <= 10 else np.nan})
    for fill in fills.itertuples():
        t_us = fill.ts_ns // 1000
        tok, px = fill.token_index, round(fill.price, 2)
        same = live[(live.tok == tok) & (live.px == px) & (live.decided_us <= t_us) & (live.end_us >= t_us - 2 * US)]
        near = live[(live.tok == tok) & (live.decided_us <= t_us) & (live.end_us >= t_us)]
        if (same.filled > 0).any():
            status = "same px, filled"
        elif len(same):
            status = "same px, not filled"
        elif len(near):
            status = "other px"
        else:
            status = "no bid"
        sub_us = int(submitted.get(fill.order_id, 0)) // 1000
        acc_us = int(accepted.get(fill.order_id, 0)) // 1000
        pr = prints[tok]
        hits = pr[(pr.side == "sell") & (pr.price <= px + 1e-9)]
        rec = {"match_id": match_id, "seg": run, "status": status, "tok": tok, "px": px, "qty": round(fill.quantity, 2),
               "mk30": round(fill.markout_30s * 100, 2), "sim_submit": pd.Timestamp(sub_us, unit="us", tz="UTC").strftime("%H:%M:%S.%f")[:-3],
               "sim_fill_s": round((t_us - sub_us) / US, 2), "sim_qa": round(fill.queue_ahead, 1),
               "lvl_at_sim": round(books[tok].level(acc_us, px) - own_at(all_events, acc_us, tok, px), 1),
               "lvl_min_dec": round(min_level(books[tok], all_events, acc_us, t_us, tok, px), 1),
               "lvl_min_raw": round(min_level(books[tok], [], acc_us, t_us, tok, px), 1),
               "sim_cxl_s": next((round((c.ts_ns // 1000 - sub_us) / US, 2) for c in cancel_req[cancel_req.order_id == fill.order_id].itertuples() if c.ts_ns <= fill.ts_ns), None),
               "sim_cxl_reason": next((c.reason for c in cancel_req[cancel_req.order_id == fill.order_id].itertuples() if c.ts_ns <= fill.ts_ns), ""),
               "lvl_min_pl": round(min_level(books[tok], placed_events, acc_us, t_us, tok, px), 1),
               "prints_sim": round(hits[(hits.timestamp_us > acc_us) & (hits.timestamp_us <= t_us)].amount.sum(), 1),
               "prints_at_px_sim": round(hits[(hits.timestamp_us > acc_us) & (hits.timestamp_us <= t_us) & (hits.price == px)].amount.sum(), 1)}
        if status in ("same px, not filled", "same px, filled"):
            lo = same.sort_values("decided_us").iloc[0]
            rec.update({"live_decided_s": round((lo.decided_us - sub_us) / US, 2), "live_placed_s": round((lo.placed_us - sub_us) / US, 2),
                        "live_end_s": round((lo.end_us - sub_us) / US, 2) if lo.end_us < 2**62 else np.inf, "live_size": lo["size"], "live_filled": round(lo.filled, 2),
                        "lvl_at_live": round(books[tok].level(lo.placed_us, px) - own_at(all_events, lo.placed_us, tok, px), 1),
                        "prints_live": round(hits[(hits.timestamp_us > lo.placed_us) & (hits.timestamp_us <= t_us)].amount.sum(), 1)})
        elif status == "other px":
            newest = near.sort_values("decided_us").iloc[-1]
            rec["live_px"] = newest.px
            rec["dpx_ticks"] = int(round((px - newest.px) * 100))
            rec["live_age_s"] = round((t_us - newest.decided_us) / US, 2)
        extra_rows.append(rec)

timing = pd.DataFrame(timing_rows)
print("sim submit minus nearest live decision at same tok/px (s), per segment:")
print(timing.groupby("seg").sim_vs_live_s.describe()[["count", "mean", "25%", "50%", "75%"]].round(2).to_string())
print("no live order at that tok/px within 10 s:", int(timing.sim_vs_live_s.isna().sum()), "of", len(timing))
table = pd.DataFrame(extra_rows)
pd.set_option("display.width", 250)
print()
print(table.groupby("status").agg(fills=("qty", "size"), shares=("qty", "sum"), mk30=("mk30", "mean")).round(2).to_string())
print()
cols = ["match_id", "seg", "status", "tok", "px", "qty", "mk30", "sim_submit", "sim_fill_s", "sim_qa", "lvl_at_sim", "lvl_min_dec", "lvl_min_raw", "sim_cxl_s", "sim_cxl_reason", "prints_sim", "prints_at_px_sim",
        "live_decided_s", "live_placed_s", "live_end_s", "live_size", "live_filled", "lvl_at_live", "prints_live"]
print(table[table.status == "same px, not filled"][cols].to_string(index=False))
print()
print(table[table.status == "same px, filled"][cols].to_string(index=False))
print()
print(table[table.status.isin(["other px", "no bid"])][["match_id", "seg", "status", "tok", "px", "qty", "mk30", "sim_submit", "sim_fill_s", "sim_qa", "sim_cxl_s", "sim_cxl_reason", "live_px", "dpx_ticks", "live_age_s"]].to_string(index=False))
table.to_csv(LIVEB / f"diag_extra_{VARIANT}.csv", index=False)
