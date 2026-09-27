"""Parquet footer only: row counts and timestamp_us min/max per day.

Does not read column data. Collector-era books (mtime after the 2026-08-08
bulk pull) plus a few earlier days for schema, and onchain/trades tails.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket")
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/fill-grok/day_footers.json")


def col_stats(pf: pq.ParquetFile, name: str) -> tuple[int | None, int | None]:
    md = pf.metadata
    schema = pf.schema_arrow
    if name not in schema.names:
        return None, None
    idx = schema.get_field_index(name)
    lo = hi = None
    for rg in range(md.num_row_groups):
        st = md.row_group(rg).column(idx).statistics
        if st is None or not st.has_min_max:
            return None, None
        a, b = int(st.min), int(st.max)
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    return lo, hi


def type_sig(pf: pq.ParquetFile) -> str:
    parts = []
    for field in pf.schema_arrow:
        parts.append(f"{field.name}:{field.type}")
    return "|".join(parts)


def scan_channel(channel: str, day_pred) -> dict:
    base = ROOT / channel
    days: dict[str, dict] = {}
    schemas: dict[str, str] = {}
    missing_stats = 0
    files = 0
    for asset in os.scandir(base):
        if not asset.is_dir(follow_symlinks=False):
            continue
        for entry in os.scandir(asset.path):
            name = entry.name
            if not name.endswith(".parquet"):
                continue
            day = name[:10]
            if not day_pred(day):
                continue
            files += 1
            path = entry.path
            try:
                pf = pq.ParquetFile(path)
            except Exception as exc:
                bucket = days.setdefault(day, _blank())
                bucket["unreadable"] += 1
                bucket["errors"].append(f"{path}: {exc}")
                continue
            sig = type_sig(pf)
            schemas.setdefault(sig, day)
            rows = pf.metadata.num_rows
            lo, hi = col_stats(pf, "timestamp_us")
            bucket = days.setdefault(day, _blank())
            bucket["files"] += 1
            bucket["rows"] += rows
            bucket["schemas"].add(sig)
            if lo is None:
                missing_stats += 1
                bucket["no_stats"] += 1
                continue
            bucket["with_stats"] += 1
            if bucket["min_us"] is None or lo < bucket["min_us"]:
                bucket["min_us"] = lo
            if bucket["max_us"] is None or hi > bucket["max_us"]:
                bucket["max_us"] = hi
            # hour of this file's max, UTC
            hour = datetime.fromtimestamp(hi / 1_000_000, timezone.utc).hour
            bucket["file_max_hour"][hour] = bucket["file_max_hour"].get(hour, 0) + 1
    out = {}
    for day, bucket in sorted(days.items()):
        bucket["schemas"] = sorted(bucket["schemas"])
        out[day] = bucket
    return {"files_opened": files, "missing_stats": missing_stats, "schema_first_day": schemas, "days": out}


def _blank() -> dict:
    return {
        "files": 0,
        "rows": 0,
        "with_stats": 0,
        "no_stats": 0,
        "unreadable": 0,
        "min_us": None,
        "max_us": None,
        "schemas": set(),
        "file_max_hour": {},
        "errors": [],
    }


def main() -> None:
    books = scan_channel("book_snapshot_full", lambda d: d >= "2026-08-09" or d in {"2026-07-20", "2026-08-07", "2026-08-08"})
    trades = scan_channel("trades", lambda d: d >= "2026-09-10")
    onchain = scan_channel("onchain_fills", lambda d: d >= "2026-09-15")
    payload = {"books": books, "trades": trades, "onchain": onchain}

    def shrink(node: dict) -> dict:
        # errors can be huge; keep a count
        days = {}
        for day, row in node["days"].items():
            row = dict(row)
            row["error_count"] = len(row["errors"])
            row["errors"] = row["errors"][:3]
            days[day] = row
        return {k: v for k, v in node.items() if k != "days"} | {"days": days}

    OUT.write_text(json.dumps({"books": shrink(books), "trades": shrink(trades), "onchain": shrink(onchain)}))
    for label, node in (("books", books), ("trades", trades), ("onchain", onchain)):
        print(label, "opened", node["files_opened"], "no_stats", node["missing_stats"])
        for day, row in node["days"].items():
            mx = row["max_us"]
            stamp = datetime.fromtimestamp(mx / 1_000_000, timezone.utc).strftime("%H:%M:%S") if mx else "-"
            print(f"  {day} files={row['files']} rows={row['rows']} max={stamp} nostats={row['no_stats']} unread={row['unreadable']}")


if __name__ == "__main__":
    main()
