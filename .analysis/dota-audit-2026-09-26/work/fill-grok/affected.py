"""Catalog windows vs short book days, double-timestamp date span, 2026-08-23 slugs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

BOOKS = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket/book_snapshot_full")
CATALOG = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/new_processed/match_catalog/match_catalog.parquet")
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/fill-grok/affected.json")

SHORT = {
    "2025-10-17": "2025-10-17T21:50:44.885000+00:00",
    "2025-10-18": "2025-10-18T21:41:40.198000+00:00",
    "2025-11-26": "2025-11-26T17:57:53.096000+00:00",
    "2025-12-07": "2025-12-07T13:39:46.801000+00:00",
    "2025-12-31": "2025-12-31T14:23:03.172000+00:00",
    "2026-01-03": "2026-01-03T19:14:47.596000+00:00",
    "2026-01-12": "2026-01-12T04:40:29.992000+00:00",
    "2026-01-18": "2026-01-18T19:52:40.187000+00:00",
    "2026-03-30": "2026-03-30T17:46:47.038000+00:00",
    "2026-08-23": "2026-08-23T13:48:54.158092+00:00",
}
ONCHAIN_CUT = datetime(2026, 9, 20, 20, 29, 11, tzinfo=timezone.utc)


def parse(text: str) -> datetime:
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def file_max(path: Path) -> str | None:
    if not path.is_file():
        return None
    pf = pq.ParquetFile(path)
    schema = pf.schema_arrow
    idx = schema.get_field_index("timestamp_us")
    hi = None
    md = pf.metadata
    for rg in range(md.num_row_groups):
        st = md.row_group(rg).column(idx).statistics
        if st is None or not st.has_min_max:
            return None
        b = int(st.max)
        hi = b if hi is None else max(hi, b)
    return datetime.fromtimestamp(hi / 1_000_000, timezone.utc).isoformat()


def main() -> None:
    frame = pd.read_parquet(
        CATALOG,
        columns=["match_id", "market_slug", "token_id_0", "token_id_1", "horn_at", "ended_at"],
    )
    by_day = {}
    affected = []
    onchain_maps = []
    for rec in frame.itertuples(index=False):
        horn = parse(rec.horn_at)
        ended = parse(rec.ended_at)
        day = horn.date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
        window_days = {horn.date().isoformat(), ended.date().isoformat()}
        hit_days = window_days & SHORT.keys()
        if hit_days:
            files = {}
            cut_short = False
            for token in (rec.token_id_0, rec.token_id_1):
                for d in sorted(window_days):
                    path = BOOKS / f"asset_id={token}" / f"{d}.parquet"
                    mx = file_max(path)
                    files[f"{token[-6:]}:{d}"] = mx
                    if d in SHORT and (mx is None or mx < ended.isoformat()):
                        cut_short = True
            if cut_short:
                affected.append(
                    {
                        "match_id": int(rec.match_id),
                        "slug": rec.market_slug,
                        "horn": horn.isoformat(),
                        "ended": ended.isoformat(),
                        "short_days": sorted(hit_days),
                        "file_max": files,
                    }
                )
        if horn.date().isoformat() == "2026-09-20" or ended.date().isoformat() == "2026-09-20":
            if horn < ONCHAIN_CUT:
                onchain_maps.append(
                    {
                        "match_id": int(rec.match_id),
                        "slug": rec.market_slug,
                        "horn": horn.isoformat(),
                        "ended": ended.isoformat(),
                        "starts_before_onchain": True,
                    }
                )
    # slugs on the 16 files of 2026-08-23
    slugs = []
    for asset in os.scandir(BOOKS):
        path = Path(asset.path) / "2026-08-23.parquet"
        if not path.is_file():
            continue
        table = pq.read_table(path, columns=["slug", "timestamp_us"], use_threads=False)
        # first and last via stats already known; slug is constant per file typically
        slug = table.column("slug")[0].as_py()
        ts = table.column("timestamp_us")
        slugs.append(
            {
                "asset": asset.name,
                "slug": slug,
                "rows": table.num_rows,
                "min": datetime.fromtimestamp(int(ts[0].as_py()) / 1e6, timezone.utc).isoformat() if table.num_rows else None,
                "max": datetime.fromtimestamp(int(ts[table.num_rows - 1].as_py()) / 1e6, timezone.utc).isoformat() if table.num_rows else None,
            }
        )
    # double local_timestamp date span
    double_days = {}
    int_days = {}
    for asset in os.scandir(BOOKS):
        if not asset.is_dir(follow_symlinks=False):
            continue
        for entry in os.scandir(asset.path):
            if not entry.name.endswith(".parquet"):
                continue
            day = entry.name[:10]
            # one file per day is enough if days are pure; count both
            pf = pq.ParquetFile(entry.path)
            field = pf.schema_arrow.field("local_timestamp_us")
            kind = str(field.type)
            bucket = double_days if kind == "double" else int_days
            bucket[day] = bucket.get(day, 0) + 1
    overlap = sorted(set(double_days) & set(int_days))
    payload = {
        "catalog_maps": int(len(frame)),
        "horn_counts_aug": {d: by_day.get(d, 0) for d in [
            "2026-08-06", "2026-08-07", "2026-08-08", "2026-08-09", "2026-08-22", "2026-08-23", "2026-08-24"
        ]},
        "horn_counts_sep": {d: by_day.get(d, 0) for d in [
            "2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21", "2026-09-25"
        ]},
        "affected": affected,
        "onchain_maps_before_cut": onchain_maps,
        "aug23_files": slugs,
        "double_first": min(double_days) if double_days else None,
        "double_last": max(double_days) if double_days else None,
        "double_days": len(double_days),
        "double_files": sum(double_days.values()),
        "int_first": min(int_days) if int_days else None,
        "int_last": max(int_days) if int_days else None,
        "overlap_days": overlap,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print("catalog", len(frame), "affected", len(affected))
    print("aug", payload["horn_counts_aug"])
    print("sep", payload["horn_counts_sep"])
    print("onchain maps before cut", len(onchain_maps))
    print("double", payload["double_first"], payload["double_last"], "days", payload["double_days"], "files", payload["double_files"])
    print("int", payload["int_first"], payload["int_last"], "overlap", overlap)
    print("aug23 slugs", len(slugs))
    for row in slugs:
        print(row["slug"], row["rows"], row["min"][11:19], row["max"][11:19])
    for row in affected:
        print("AFFECTED", row["match_id"], row["slug"], row["short_days"], row["ended"])


if __name__ == "__main__":
    main()
