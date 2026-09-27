"""Probe join keys and onchain side labels before the honest-queue scan."""

import json
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
LIVE = E / "data/backtests/dota_maker/validation_join_delta02_x015_cut480_p4_archive-s3-20260924"
RAW = E / "data/raw/telonex/polymarket"

fills = pq.read_table(
    LIVE / "seed0/fills.parquet",
    columns=["match_id", "order_id", "side", "price", "queue_ahead", "level_moves", "ts_ns", "token_index"],
).to_pydict()
quotes = pq.read_table(
    LIVE / "seed0/quote_events.parquet",
    columns=["match_id", "order_id", "kind", "ts_ns", "side", "price"],
).to_pydict()
submit = {}
accept = {}
kinds = {}
for i, kind in enumerate(quotes["kind"]):
    kinds[kind] = kinds.get(kind, 0) + 1
    oid = quotes["order_id"][i]
    if not oid:
        continue
    if kind == "submitted":
        submit[oid] = quotes["ts_ns"][i]
    elif kind == "accepted":
        accept[oid] = quotes["ts_ns"][i]

n = len(fills["order_id"])
missing_s = sum(1 for oid in fills["order_id"] if oid not in submit)
missing_a = sum(1 for oid in fills["order_id"] if oid not in accept)
moves = {}
for m in fills["level_moves"]:
    moves[m] = moves.get(m, 0) + 1
lags = []
for oid in fills["order_id"]:
    if oid in submit and oid in accept:
        lags.append((accept[oid] - submit[oid]) / 1e9)

results = pq.read_schema(LIVE / "seed0/results.parquet")
onchain = next((RAW / "onchain_fills").glob("asset_id=*/2026-09-20.parquet"))
ot = pq.read_schema(onchain)
sample = pq.read_table(onchain, columns=["taker_side", "maker_side", "price", "amount"]).slice(0, 5)

print(json.dumps({
    "fills": n,
    "quote_kinds": kinds,
    "fills_missing_submit": missing_s,
    "fills_missing_accept": missing_a,
    "accept_lag_s_min": min(lags) if lags else None,
    "accept_lag_s_max": max(lags) if lags else None,
    "level_moves": {str(k): v for k, v in sorted(moves.items())},
    "results_cols": results.names,
    "onchain_cols": ot.names,
    "onchain_sample": sample.to_pydict(),
}, indent=2))
