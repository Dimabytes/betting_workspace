"""Footer-only book completeness for every day, plus catalog maps on short days.

Reads parquet footers and catalog columns. Does not scan book row bodies
except the slug of the first row of each 2026-08-23 file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket/book_snapshot_full")
ONCHAIN = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket/onchain_fills")
CATALOG = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/new_processed/match_catalog/match_catalog.parquet")
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/fill-grok/short_days.json")

CUT_08_23 = datetime(2026, 8, 23, 13, 48, 54, tzinfo=timezone.utc)


def footer_max(path: str, col: str = "timestamp_us") -> tuple[int, int | None, str]:
    pf = pq.ParquetFile(path)
    md = pf.metadata
    schema = pf.schema_arrow
    sig = ",".join(f"{f.name}:{f.type}" for f in schema)
    if col not in schema.names:
        return md.num_rows, None, sig
    idx = schema.get_field_index(col)
    hi = None
    for rg in range(md.num_row_groups):
        st = md.row_group(rg).column(idx).statistics
        if st is None or not st.has_min_max:
            return md.num_rows, None, sig
        b = int(st.max)
        hi = b if hi is None else max(hi, b)
    return md.num_rows, hi, sig


def all_book_days() -> dict:
    days: dict[str, dict] = {}
    schemas: dict[str, int] = {}
    for asset in os.scandir(ROOT):
        if not asset.is_dir(follow_symlinks=False):
            continue
        for entry in os.scandir(asset.path):
            if not entry.name.endswith(".parquet"):
                continue
            day = entry.name[:10]
            rows, hi, sig = footer_max(entry.path)
            schemas[sig] = schemas.get(sig, 0) + 1
            bucket = days.setdefault(day, {"files": 0, "rows": 0, "max_us": None, "no_stats": 0})
            bucket["files"] += 1
            bucket["rows"] += rows
            if hi is None:
                bucket["no_stats"] += 1
            elif bucket["max_us"] is None or hi > bucket["max_us"]:
                bucket["max_us"] = hi
    short = []
    for day, row in sorted(days.items()):
        mx = row["max_us"]
        if mx is None:
            short.append({**row, "day": day, "max": None})
            continue
        stamp = datetime.fromtimestamp(mx / 1_000_000, timezone.utc)
        if stamp.hour < 22:
            short.append({**row, "day": day, "max": stamp.isoformat()})
    return {"n_days": len(days), "schema_counts": schemas, "short": short, "missing_note": "see caller"}


def day_files(token: str, day: str) -> Path | None:
    path = ROOT / f"asset_id={token}" / f"{day}.parquet"
    return path if path.is_file() else None


def catalog_hits() -> list[dict]:
    import pandas as pd

    frame = pd.read_parquet(
        CATALOG,
        columns=["match_id", "market_slug", "token_id_0", "token_id_1", "horn_at", "ended_at", "duration"],
    )
    out = []
    for rec in frame.itertuples(index=False):
        horn = datetime.fromisoformat(str(rec.horn_at).replace("Z", "+00:00"))
        ended = datetime.fromisoformat(str(rec.ended_at).replace("Z", "+00:00"))
        if horn.tzinfo is None:
            horn = horn.replace(tzinfo=timezone.utc)
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        days = {horn.date().isoformat(), ended.date().isoformat()}
        interesting = "2026-08-08" in days or "2026-08-23" in days or (
            horn <= CUT_08_23 <= ended
        )
        # also any map whose window is on a day we will fill after short scan — caller filters 08-23/08-08
        if not interesting:
            continue
        files = {}
        for label, token in (("t0", rec.token_id_0), ("t1", rec.token_id_1)):
            for day in sorted(days):
                path = day_files(str(token), day)
                if path is None:
                    files[f"{label}:{day}"] = None
                else:
                    rows, hi, _ = footer_max(str(path))
                    files[f"{label}:{day}"] = {
                        "rows": rows,
                        "max": None if hi is None else datetime.fromtimestamp(hi / 1_000_000, timezone.utc).isoformat(),
                    }
        gap = ended > CUT_08_23 and ("2026-08-23" in days)
        out.append(
            {
                "match_id": int(rec.match_id),
                "slug": rec.market_slug,
                "horn": horn.isoformat(),
                "ended": ended.isoformat(),
                "duration": int(rec.duration),
                "past_cut": gap,
                "files": files,
            }
        )
    return out


def onchain_0920() -> dict:
    day = "2026-09-20"
    rows_total = 0
    files = 0
    lo = hi = None
    # read the timestamp column; stats were absent and the day is ~1MB
    for asset in os.scandir(ONCHAIN):
        if not asset.is_dir(follow_symlinks=False):
            continue
        path = Path(asset.path) / f"{day}.parquet"
        if not path.is_file():
            continue
        files += 1
        table = pq.read_table(path, columns=["block_timestamp_us"])
        rows_total += table.num_rows
        if table.num_rows == 0:
            continue
        col = table.column("block_timestamp_us")
        a = int(col[0].as_py())
        # min/max
        values = col.to_numpy()
        a = int(values.min())
        b = int(values.max())
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    def stamp(us):
        return None if us is None else datetime.fromtimestamp(us / 1_000_000, timezone.utc).isoformat()
    return {"files": files, "rows": rows_total, "min": stamp(lo), "max": stamp(hi)}


def main() -> None:
    books = all_book_days()
    hits = catalog_hits()
    chain = onchain_0920()
    payload = {"books": books, "catalog_hits": hits, "onchain_2026_09_20": chain}
    OUT.write_text(json.dumps(payload, indent=2))
    print("days", books["n_days"], "schemas", len(books["schema_counts"]))
    for sig, n in books["schema_counts"].items():
        print(" schema files", n, sig[:180])
    print("short days", len(books["short"]))
    for row in books["short"]:
        print(" ", row["day"], "files", row["files"], "max", row["max"])
    print("catalog hits", len(hits))
    past = [h for h in hits if h["past_cut"]]
    print("past 08-23 cut", len(past))
    for h in hits:
        print(h["match_id"], h["slug"], h["horn"][11:19], "->", h["ended"][11:19], "past", h["past_cut"])
    print("onchain", chain)


if __name__ == "__main__":
    main()
