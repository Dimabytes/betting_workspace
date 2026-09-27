"""Rebuild market-second rows in memory for a sample of catalog maps and diff them with the stored cache."""
import random
import sys

import numpy as np
import pandas as pd

from market_data.build_market_data import build_market_second_rows, resolve_catalog_row
from shared.constants.paths import MATCH_CATALOG_PATH, RAW_TELONEX_POLYMARKET_DIR
from shared.utils.match_catalog import load_match_catalog

N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
catalog = load_match_catalog(MATCH_CATALOG_PATH)
entries = [e for e in catalog.values()]
random.seed(7)
sample = random.sample(entries, N)
out = []
for e in sample:
    job = resolve_catalog_row(e, RAW_TELONEX_POLYMARKET_DIR)
    if not job.cache_path.exists():
        out.append((e.match_id, str(e.horn_at)[:10], "no_cache", None, None, None, None))
        continue
    old = pd.read_parquet(job.cache_path)
    rows = build_market_second_rows(job)
    if rows is None:
        out.append((e.match_id, str(e.horn_at)[:10], "rebuild_none", len(old), None, None, None))
        continue
    new = pd.DataFrame(rows)
    m = old.merge(new, on="second", how="outer", suffixes=("_old", "_new"), indicator=True)
    both = m[m["_merge"] == "both"]
    status_diff = int((both.market_status_old != both.market_status_new).sum())
    p_old = both.market_p_radiant_old.astype(float)
    p_new = both.market_p_radiant_new.astype(float)
    p_diff = np.nanmax(np.abs(p_old - p_new)) if len(both) else np.nan
    ts_diff = int((both.state_ts_us_old != both.state_ts_us_new).sum())
    lab_old = both.signal_market_p_radiant_300s_old.astype(float)
    lab_new = both.signal_market_p_radiant_300s_new.astype(float)
    lab_changed = int(((lab_old - lab_new).abs() > 1e-9).sum() + (lab_old.isna() != lab_new.isna()).sum())
    out.append((e.match_id, str(e.horn_at)[:10], f"rows old={len(old)} new={len(new)} only_old={int((m._merge=='left_only').sum())} only_new={int((m._merge=='right_only').sum())}",
                status_diff, ts_diff, round(float(p_diff), 4) if p_diff == p_diff else None, lab_changed))
df = pd.DataFrame(out, columns=["match_id", "horn_day", "rows", "status_diff", "state_ts_diff", "max_abs_p_diff", "label_changed"])
pd.set_option("display.width", 250)
print(df.sort_values("horn_day").to_string())
print("maps with any diff:", int(((df.status_diff.fillna(0) > 0) | (df.state_ts_diff.fillna(0) > 0) | (df.label_changed.fillna(0) > 0)).sum()), "of", len(df))
