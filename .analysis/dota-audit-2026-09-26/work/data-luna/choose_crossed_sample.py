from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from shared.constants.paths import RAW_TELONEX_POLYMARKET_DIR, MATCH_CATALOG_PATH
from market_data.build_market_data import market_seconds_cache_path
from shared.utils.match_catalog import CatalogEntry, load_match_catalog


ERA_CUTOFF = datetime(2026, 8, 8, tzinfo=UTC).timestamp()
MONTHS = tuple(
    f"{year:04d}-{month:02d}"
    for year, month in [(2025, m) for m in range(10, 13)]
    + [(2026, m) for m in range(1, 10)]
)
TARGET_PER_MONTH = 13


def _month_and_era(entry: CatalogEntry) -> tuple[str, str]:
    moment = datetime.fromtimestamp(entry.start_time, UTC)
    era = "collector" if moment.timestamp() >= ERA_CUTOFF else "paid_telonex"
    return moment.strftime("%Y-%m"), era


def _raw_available(entry: CatalogEntry) -> bool:
    for token_id in entry.gamma.token_ids:
        asset_dir = RAW_TELONEX_POLYMARKET_DIR / "book_snapshot_full" / f"asset_id={token_id}"
        if not asset_dir.is_dir() or not any(asset_dir.glob("*.parquet")):
            return False
    return market_seconds_cache_path(entry.match_id).is_file()


def _even_sample(rows: list[CatalogEntry], count: int) -> list[CatalogEntry]:
    rows = sorted(rows, key=lambda entry: (entry.start_time, entry.match_id))
    if count >= len(rows):
        return rows
    indexes = [round(value) for value in __import__("numpy").linspace(0, len(rows) - 1, count)]
    return [rows[index] for index in dict.fromkeys(indexes)]


catalog = load_match_catalog(MATCH_CATALOG_PATH)
by_month_era: dict[tuple[str, str], list[CatalogEntry]] = defaultdict(list)
for entry in catalog.values():
    month, era = _month_and_era(entry)
    if month in MONTHS and _raw_available(entry):
        by_month_era[(month, era)].append(entry)

selected: list[CatalogEntry] = []
for month in MONTHS:
    before = by_month_era[(month, "paid_telonex")]
    after = by_month_era[(month, "collector")]
    if month == "2026-08":
        # Preserve both source eras within the transition month; favor its larger cohort.
        pre_n = min(4, len(before))
        post_n = min(TARGET_PER_MONTH - pre_n, len(after))
        short = TARGET_PER_MONTH - pre_n - post_n
        if short:
            post_n += min(short, max(0, len(after) - post_n))
            short = TARGET_PER_MONTH - pre_n - post_n
        if short:
            pre_n += min(short, max(0, len(before) - pre_n))
        selected.extend(_even_sample(before, pre_n))
        selected.extend(_even_sample(after, post_n))
    else:
        cohort = before or after
        selected.extend(_even_sample(cohort, min(TARGET_PER_MONTH, len(cohort))))

rows = []
for entry in selected:
    month, era = _month_and_era(entry)
    rows.append(
        {
            "match_id": entry.match_id,
            "month": month,
            "era": era,
            "start_time": entry.start_time,
            "slug": entry.gamma.slug,
            "token_id_0": entry.gamma.token_ids[0],
            "token_id_1": entry.gamma.token_ids[1],
            "radiant_token_index": entry.radiant_token_index,
            "horn_at": entry.horn_at.isoformat(),
            "duration": entry.duration,
        }
    )
sample = pd.DataFrame(rows).sort_values(["month", "start_time", "match_id"])
out_path = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/data-luna/crossed_book_sample.csv")
sample.to_csv(out_path, index=False)
print("catalog", len(catalog), "sample", len(sample), "output", out_path)
print("available_by_month_era", {f"{m}:{e}": len(v) for (m, e), v in sorted(by_month_era.items())})
print("sample_by_month_era", sample.groupby(["month", "era"]).size().to_dict())
