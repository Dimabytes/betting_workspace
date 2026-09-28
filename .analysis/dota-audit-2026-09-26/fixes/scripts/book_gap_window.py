"""Which book-gap-excluded validation maps would return under a shorter window.

Run from esports-trader root:
  uv run python ../betting_workspace/.analysis/dota-audit-2026-09-26/fixes/scripts/book_gap_window.py

Recomputes `find_longest_book_gap` (run of non-ok seconds) on the current market cache
for [-60, 900) and [-60, 480), split by archive-linked (schedule) vs not, and prints
which status fills the longest run.
"""

from collections import Counter

import pandas as pd

from market_data.build_market_data import CACHE_VERSION
from shared.constants.dataset import MODEL_START_SECOND

# Historical gate this script was written to measure. The backtest no longer applies it.
MAX_BOOK_GAP_SECONDS = 120
from shared.constants.paths import MARKET_SECONDS_DIR, MATCH_CATALOG_PATH, RESEARCH_MODEL_SPLIT_PATH

BUY_CUTOFF_SECOND = 480
OLD_END_SECOND = 900


def find_longest_run(frame: pd.DataFrame, end_second: int) -> tuple[int, str]:
    window = frame[(frame["second"] >= MODEL_START_SECOND) & (frame["second"] < end_second)]
    longest, run, statuses, best = 0, 0, Counter(), Counter()
    for status in window["market_status"]:
        if status == "ok":
            run, statuses = 0, Counter()
            continue
        run += 1
        statuses[status] += 1
        if run > longest:
            longest, best = run, statuses.copy()
    top = best.most_common(1)[0][0] if best else ""
    return longest, top


def main() -> None:
    split = pd.read_parquet(RESEARCH_MODEL_SPLIT_PATH)
    validation = split[split["split"] == "validation"]
    flagged = validation[validation["backtest_book_gap_excluded"].astype(bool)]["match_id"]
    catalog = pd.read_parquet(MATCH_CATALOG_PATH, columns=["match_id", "archive_id"])
    archived = set(catalog[catalog["archive_id"].fillna("") != ""]["match_id"].astype(int))
    rows = []
    for match_id in flagged.astype(int):
        path = MARKET_SECONDS_DIR / f"v{CACHE_VERSION}" / f"match_id={match_id}.parquet"
        if not path.is_file():
            rows.append((match_id, match_id in archived, None, None, "", "no_cache"))
            continue
        frame = pd.read_parquet(path, columns=["second", "market_status"])
        old_run, old_status = find_longest_run(frame, OLD_END_SECOND)
        new_run, _ = find_longest_run(frame, BUY_CUTOFF_SECOND)
        rows.append((match_id, match_id in archived, old_run, new_run, old_status, "ok"))
    table = pd.DataFrame(
        rows, columns=["match_id", "archived", "run_900", "run_480", "status_900", "cache"]
    )
    table["returns_480"] = table["run_480"].le(MAX_BOOK_GAP_SECONDS)
    print(f"validation maps {len(validation)}, flagged {len(table)}")
    print(table.groupby(["archived", "cache"]).size().to_string())
    cached = table[table["cache"] == "ok"]
    print("returns with window 480:")
    print(cached.groupby("archived")["returns_480"].agg(["sum", "size"]).to_string())
    print("status filling the longest run (window 900):")
    print(cached.groupby(["archived", "status_900"]).size().to_string())
    print(cached[cached["archived"]].sort_values("run_480").to_string(index=False))


if __name__ == "__main__":
    main()
