"""Stat day files only. No row scans. Writes day coverage JSON."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket")
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/fill-grok/day_coverage.json")


def scan(channel: str) -> dict:
    base = ROOT / channel
    # day -> [files, bytes, min_mtime, max_mtime]
    days: dict[str, list] = {}
    assets = 0
    for asset in os.scandir(base):
        if not asset.is_dir(follow_symlinks=False):
            continue
        assets += 1
        for entry in os.scandir(asset.path):
            name = entry.name
            if not name.endswith(".parquet"):
                continue
            day = name[:10]
            st = entry.stat(follow_symlinks=False)
            row = days.get(day)
            if row is None:
                days[day] = [1, st.st_size, st.st_mtime, st.st_mtime]
            else:
                row[0] += 1
                row[1] += st.st_size
                if st.st_mtime < row[2]:
                    row[2] = st.st_mtime
                if st.st_mtime > row[3]:
                    row[3] = st.st_mtime
    return {
        "assets": assets,
        "days": {
            day: {"files": v[0], "bytes": v[1], "min_mtime": v[2], "max_mtime": v[3]}
            for day, v in sorted(days.items())
        },
    }


def main() -> None:
    payload = {name: scan(name) for name in ("book_snapshot_full", "trades", "onchain_fills")}
    OUT.write_text(json.dumps(payload))
    for name, body in payload.items():
        days = body["days"]
        print(name, "assets", body["assets"], "days", len(days), "first", next(iter(days)), "last", next(reversed(days)))


if __name__ == "__main__":
    main()
