"""Write wallet B core_trace files whose resting starts at the exchange PLACEMENT time.

scripts/journal_to_core_trace.py starts B's resting at the matching orders_out
decision, 50-200 ms before the order reaches the book, so the own-book strip
removes B's size from the Telonex book too early. This variant starts it at
the PLACEMENT timestamp instead; fills and ends are unchanged.

usage: PYTHONPATH=src:scripts python write_placed_core_trace.py JOURNAL OUT_ROOT IDS_FILE
"""

import sys
from pathlib import Path

import pandas as pd
from journal_to_core_trace import archive_token_maps, core_trace_lines, load_orders, read_journal

from shared.constants.paths import MATCH_CATALOG_PATH


def archive_ids(match_ids: list[str]) -> dict[str, str]:
    """match_id -> archive_id from the catalog (they differ for grid archives)."""
    catalog = pd.read_parquet(MATCH_CATALOG_PATH, columns=["match_id", "archive_id"])
    return {str(m): str(a) for m, a in zip(catalog.match_id, catalog.archive_id) if str(m) in match_ids}


def main() -> None:
    journal, out_root, ids_file = (Path(arg) for arg in sys.argv[1:4])
    rows = read_journal(journal)
    maps = archive_token_maps(MATCH_CATALOG_PATH)
    match_ids = ids_file.read_text().split()
    for match_id, archive_id in archive_ids(match_ids).items():
        orders = load_orders(rows, maps[archive_id])
        for order in orders:
            order.accept_us = order.placed_us
        target = out_root / archive_id / "core_trace.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(core_trace_lines(orders)) + "\n")
        print(f"{match_id}: {len(orders)} orders")


if __name__ == "__main__":
    main()
