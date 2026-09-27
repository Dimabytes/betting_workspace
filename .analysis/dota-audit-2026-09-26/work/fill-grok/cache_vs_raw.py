"""Compare _backtest_cache to raw day files, and one LIVE fill to the book.

Footer stats plus a timestamp-filtered read of one book file. No full-tree scan.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

RAW = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket")
CACHE = RAW / "_backtest_cache"
CATALOG = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/new_processed/match_catalog/match_catalog.parquet")
FILLS = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/backtests/dota_maker/validation_join_delta02_x015_cut480_p4_archive-s3-20260924/seed0/fills.parquet")
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/fill-grok/cache_vs_raw.json")

MAPS = [
    9007618767, 9007618656, 9007700576, 9007784381, 9007918871, 9007993994,
    9008125103, 9008160103, 9008150824, 9008292090, 9008297395, 9008413867,
    9008403049, 9008436237, 9008510292, 9008530635, 9008670336, 9008779767,
]


def stamp(us: int | None) -> str | None:
    if us is None:
        return None
    return datetime.fromtimestamp(us / 1_000_000, timezone.utc).isoformat()


def footer_span(path: Path, col: str) -> tuple[int, int | None, int | None]:
    pf = pq.ParquetFile(path)
    md = pf.metadata
    names = pf.schema_arrow.names
    if col not in names:
        return md.num_rows, None, None
    idx = pf.schema_arrow.get_field_index(col)
    lo = hi = None
    for rg in range(md.num_row_groups):
        st = md.row_group(rg).column(idx).statistics
        if st is None or not st.has_min_max:
            return md.num_rows, None, None
        a, b = int(st.min), int(st.max)
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    return md.num_rows, lo, hi


def index_cache(channel: str) -> dict[str, dict]:
    """condition_id -> one file. Readers refuse two files; record if that happens."""
    out: dict[str, dict] = {}
    folder = CACHE / channel
    for entry in os.scandir(folder):
        if not entry.name.endswith(".parquet"):
            continue
        cond, _, _rest = entry.name.partition("-")
        path = Path(entry.path)
        rows, lo, hi = footer_span(path, "timestamp_us")
        slot = out.setdefault(cond, {"files": 0, "rows": 0, "min_us": None, "max_us": None, "mtime": 0})
        slot["files"] += 1
        slot["rows"] += rows
        slot["mtime"] = max(slot["mtime"], path.stat().st_mtime)
        if lo is not None:
            slot["min_us"] = lo if slot["min_us"] is None else min(slot["min_us"], lo)
        if hi is not None:
            slot["max_us"] = hi if slot["max_us"] is None else max(slot["max_us"], hi)
    return out


def raw_span(channel: str, token: str, days: list[str]) -> dict:
    rows = 0
    files = 0
    lo = hi = None
    missing = []
    for day in days:
        path = RAW / channel / f"asset_id={token}" / f"{day}.parquet"
        if not path.is_file():
            missing.append(day)
            continue
        files += 1
        n, a, b = footer_span(path, "timestamp_us" if channel != "onchain_fills" else "block_timestamp_us")
        rows += n
        if a is not None:
            lo = a if lo is None else min(lo, a)
        if b is not None:
            hi = b if hi is None else max(hi, b)
    return {"files": files, "rows": rows, "min": stamp(lo), "max": stamp(hi), "missing_days": missing}


def days_between(lo: str | None, hi: str | None) -> list[str]:
    if not lo or not hi:
        return []
    start = datetime.fromisoformat(lo).date()
    end = datetime.fromisoformat(hi).date()
    out = []
    day = start
    while day <= end:
        out.append(day.isoformat())
        day = day.fromordinal(day.toordinal() + 1)
    return out


def best_at(path: Path, target_us: int) -> dict | None:
    """Last snapshot at or before target_us, within 5s. Predicate on timestamp_us."""
    table = pq.read_table(
        path,
        columns=["timestamp_us", "bids", "asks"],
        filters=[
            ("timestamp_us", ">=", target_us - 5_000_000),
            ("timestamp_us", "<=", target_us),
        ],
    )
    if table.num_rows == 0:
        return {"rows_in_5s": 0}
    ts = table.column("timestamp_us").to_pylist()
    idx = max(range(len(ts)), key=lambda i: ts[i])

    def best(levels, want_max: bool) -> float | None:
        if levels is None:
            return None
        prices = []
        for item in levels:
            if item is None:
                continue
            price = float(item["price"])
            size = float(item["size"])
            if size > 0:
                prices.append(price)
        if not prices:
            return None
        return max(prices) if want_max else min(prices)

    return {
        "rows_in_5s": table.num_rows,
        "ts": stamp(int(ts[idx])),
        "bid": best(table.column("bids")[idx].as_py(), True),
        "ask": best(table.column("asks")[idx].as_py(), False),
    }


def main() -> None:
    trade_ix = index_cache("trade")
    book_ix = index_cache("book")
    cat = pd.read_parquet(
        CATALOG,
        columns=["match_id", "condition_id", "market_slug", "token_id_0", "token_id_1", "horn_at", "ended_at"],
    )
    cat["condition_id"] = cat["condition_id"].astype(str)
    # mtime span of the cache
    def mtime_span(ix: dict) -> dict:
        mtimes = [v["mtime"] for v in ix.values()]
        return {
            "markets": len(ix),
            "multi_file": sum(1 for v in ix.values() if v["files"] > 1),
            "min_mtime": datetime.fromtimestamp(min(mtimes), timezone.utc).isoformat(),
            "max_mtime": datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat(),
        }

    focus = []
    for mid in MAPS:
        row = cat.loc[cat.match_id == mid]
        if row.empty:
            focus.append({"match_id": mid, "in_catalog": False})
            continue
        rec = row.iloc[0]
        cond = rec.condition_id
        focus.append(
            {
                "match_id": mid,
                "slug": rec.market_slug,
                "condition_id": cond,
                "trade_cache": cond in trade_ix,
                "book_cache": cond in book_ix,
                "trade_rows": None if cond not in trade_ix else trade_ix[cond]["rows"],
                "trade_span": None
                if cond not in trade_ix
                else [stamp(trade_ix[cond]["min_us"]), stamp(trade_ix[cond]["max_us"])],
            }
        )

    # 20 maps that have both caches, spread across the catalog
    both = cat[cat.condition_id.isin(set(trade_ix) & set(book_ix))].sort_values("horn_at")
    step = max(1, len(both) // 20)
    sample_rows = both.iloc[::step].head(20)
    sample = []
    for rec in sample_rows.itertuples(index=False):
        t = trade_ix[rec.condition_id]
        b = book_ix[rec.condition_id]
        t_span = [stamp(t["min_us"]), stamp(t["max_us"])]
        b_span = [stamp(b["min_us"]), stamp(b["max_us"])]
        days = days_between(t_span[0], t_span[1])
        raw_trades = [
            raw_span("trades", rec.token_id_0, days),
            raw_span("trades", rec.token_id_1, days),
        ]
        raw_books = [
            raw_span("book_snapshot_full", rec.token_id_0, days),
            raw_span("book_snapshot_full", rec.token_id_1, days),
        ]
        sample.append(
            {
                "match_id": int(rec.match_id),
                "slug": rec.market_slug,
                "trade_cache_rows": t["rows"],
                "trade_cache_span": t_span,
                "raw_trade_rows": sum(x["rows"] for x in raw_trades),
                "raw_trade_files": sum(x["files"] for x in raw_trades),
                "raw_trade_missing": sorted({d for x in raw_trades for d in x["missing_days"]}),
                "book_cache_rows": b["rows"],
                "book_cache_span": b_span,
                "raw_book_rows": sum(x["rows"] for x in raw_books),
                "raw_book_span": [raw_books[0]["min"], raw_books[0]["max"], raw_books[1]["min"], raw_books[1]["max"]],
            }
        )

    # one fill vs the book
    fills = pd.read_parquet(FILLS, columns=["match_id", "token_index", "side", "price", "ts_ns"])
    probe_id = 9007618767
    probe_fills = fills[fills.match_id == probe_id].sort_values("ts_ns")
    rec = cat.loc[cat.match_id == probe_id].iloc[0]
    probes = []
    for fill in probe_fills.itertuples(index=False):
        token = rec.token_id_0 if int(fill.token_index) == 0 else rec.token_id_1
        day = datetime.fromtimestamp(fill.ts_ns / 1e9, timezone.utc).date().isoformat()
        path = RAW / "book_snapshot_full" / f"asset_id={token}" / f"{day}.parquet"
        target_us = int(fill.ts_ns // 1000)
        book = best_at(path, target_us) if path.is_file() else None
        probes.append(
            {
                "side": fill.side,
                "price": float(fill.price),
                "ts": datetime.fromtimestamp(fill.ts_ns / 1e9, timezone.utc).isoformat(),
                "token_index": int(fill.token_index),
                "book_file": path.is_file(),
                "book": book,
            }
        )

    payload = {
        "trade_cache": mtime_span(trade_ix),
        "book_cache": mtime_span(book_ix),
        "focus": focus,
        "sample": sample,
        "probe": probes,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print("trade markets", payload["trade_cache"])
    print("book markets", payload["book_cache"])
    print("focus cache hits", sum(1 for x in focus if x.get("trade_cache")), "/", len(focus))
    for row in sample:
        print(
            row["match_id"],
            "trade cache", row["trade_cache_rows"], "raw", row["raw_trade_rows"],
            "book cache", row["book_cache_rows"], "raw", row["raw_book_rows"],
            "missing", row["raw_trade_missing"],
        )
    print("probe", json.dumps(probes, indent=2))


if __name__ == "__main__":
    main()
