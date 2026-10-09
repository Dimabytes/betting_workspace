"""backtest.run with ONLY wallet B's real resting orders stripped from the book.

Wallet A is a third-party maker for B's twin: its size stays in the book (queue
ahead) and its fills stay in the tape. B's resting comes from the wallet engine
journal: accept at the matching orders_out decision (else PLACEMENT - 200 ms),
fill on UPDATE size_matched deltas, remove on CANCELLATION or, when that WS
event never came, at the next orders_out that quotes the same token again.
Run with TELONEX_TREE_CACHE and TELONEX_CACHE_ROOT pointed at scratch dirs.
"""

import bisect
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

import backtest.telonex_local as telonex_local
from backtest import run
from backtest.strip_own_book import _Resting

JOURNAL = Path(os.environ["LIVEB_JOURNAL"])
CATALOG = Path("data/new_processed/match_catalog/match_catalog.parquet")
US = 1_000_000
logger = logging.getLogger("run_b_only")


def price_key(price: float) -> int:
    return round(price * 100)


def load_b_events(token_index: dict[str, int]) -> list[tuple[int, str, object]]:
    rows = [json.loads(line) for line in JOURNAL.open()]
    decisions: dict[tuple[int, int, float], list[int]] = defaultdict(list)
    token_decisions: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        if row["kind"] != "orders_out":
            continue
        for order in row["data"]:
            index = token_index.get(order["token_id"])
            if index is not None and order["side"] == "BUY":
                key = (index, price_key(float(order["price"])), round(float(order["size"]), 2))
                decisions[key].append(int(row["ts"] * US))
                token_decisions[index].append(int(row["ts"] * US))
    for times in decisions.values():
        times.sort()
    for times in token_decisions.values():
        times.sort()
    events: list[tuple[int, str, object]] = []
    matched: dict[str, float] = {}
    removed: set[str] = set()
    placed: dict[str, tuple[int, int, float]] = {}
    for row in rows:
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
            accept_us = (
                times[position]
                if position >= 0 and event_us - times[position] <= 3 * US
                else event_us - 200_000
            )
            resting = _Resting(token_index=index, side="BUY", price=price, quantity=size - size_matched)
            events.append((accept_us, "accept", (order_id, resting)))
            matched[order_id] = size_matched
            placed[order_id] = (index, event_us, size)
            continue
        if order_id not in matched:
            continue
        if size_matched > matched[order_id] + 1e-9:
            events.append((event_us, "fill", (order_id, size_matched - matched[order_id])))
            matched[order_id] = size_matched
        if data["type"] == "CANCELLATION":
            events.append((event_us, "remove", order_id))
            removed.add(order_id)
    leaked = 0
    for order_id, (index, placed_us, size) in placed.items():
        if order_id in removed or matched[order_id] >= size - 1e-6:
            continue
        times = token_decisions[index]
        position = bisect.bisect_right(times, placed_us)
        if position < len(times):
            events.append((times[position] + 100_000, "remove", order_id))
            leaked += 1
    logger.warning("B strip: %s orders, %s without a WS cancel removed at the next decision", len(placed), leaked)
    return events


def build_token_maps() -> dict[str, dict[str, int]]:
    catalog = pd.read_parquet(CATALOG)
    with_archive = catalog.dropna(subset=["archive_id"])
    return {
        str(row.archive_id): {str(row.token_id_0): 0, str(row.token_id_1): 1}
        for row in with_archive.itertuples()
    }


TOKEN_MAPS = build_token_maps()


def load_b_only_events(archive_dir: Path) -> tuple[tuple[int, str, object], ...]:
    b_events = load_b_events(TOKEN_MAPS[archive_dir.name])
    logger.warning("strip %s: B events %s, A not stripped", archive_dir.name, len(b_events))
    return tuple(sorted(b_events, key=lambda event: event[0]))


telonex_local.load_resting_events = load_b_only_events

if __name__ == "__main__":
    sys.argv[0] = "backtest.run"
    run.main()
