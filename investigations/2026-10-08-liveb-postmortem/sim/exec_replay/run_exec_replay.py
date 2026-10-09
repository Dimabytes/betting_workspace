"""backtest.run with wallet B's journal orders replayed instead of the two-sided strategy.

Writes REPLAY_ORDERS (orders per match from the engine journal) before the run.
Each order: exchange PLACEMENT time, price, size, live fills and an end time:
the exchange CANCELLATION, else the converter's next-decision end, else (fully
filled live) the last live fill + END_AFTER_LAST_FILL_NS. Also loads the queue
probe actor when PROBE_OUT is set.

usage (from esports-trader):
  PYTHONPATH=src:scripts:../prediction-market-backtesting:<this dir>:<queue_probe dir>
  LIVEB_JOURNAL=... REPLAY_ORDERS=<out>.json REPLAY_LOG=<out>.jsonl [PROBE_OUT=...] \
  .venv/bin/python run_exec_replay.py <backtest.run args incl. --strategy two-sided --own-archive-root ...>
"""

import json
import os
import sys
from pathlib import Path

from journal_to_core_trace import archive_token_maps, load_orders, read_journal
from write_placed_core_trace import archive_ids

from backtest import run
from shared.constants.paths import MATCH_CATALOG_PATH

NS = 1_000
END_AFTER_LAST_FILL_NS = 50_000_000  # tape prints sit within 2 ms of the exchange fill time
REPLAY_STRATEGY = "journal_replay:JournalReplayStrategy"

if os.environ.get("PROBE_OUT"):
    import run_probe  # noqa: F401  (installs the queue probe actor)


def write_replay_orders(journal: Path, out: Path, match_ids: list[str]) -> None:
    rows = read_journal(journal)
    maps = archive_token_maps(MATCH_CATALOG_PATH)
    archive_of = archive_ids(match_ids)
    by_match: dict[str, list[dict]] = {}
    for match_id in match_ids:
        token_index = maps[archive_of[match_id]]
        token_of = {index: token for token, index in token_index.items()}
        orders = []
        for order in load_orders(rows, token_index):
            if order.end_us is not None:
                end_ns = order.end_us * NS
            elif order.fills:
                end_ns = max(us for us, _ in order.fills) * NS + END_AFTER_LAST_FILL_NS
            else:
                end_ns = None
            orders.append({
                "order_id": order.order_id, "token_id": token_of[order.token_index],
                "price": order.price, "size": order.size, "placed_ns": order.placed_us * NS,
                "decided_ns": order.accept_us * NS, "end_ns": end_ns,
                "live_fills": [[us * NS, qty] for us, qty in order.fills],
            })
        by_match[match_id] = orders
    out.write_text(json.dumps(by_match))


def _replay_configs(*args, **kwargs):
    configs = _original_build_strategy_configs(*args, **kwargs)
    for config in configs:
        config["strategy_path"] = REPLAY_STRATEGY
    return configs


_original_build_strategy_configs = run.build_strategy_configs
run.build_strategy_configs = _replay_configs

if __name__ == "__main__":
    argv = sys.argv[1:]
    ids_file = Path(argv[argv.index("--match-ids-file") + 1])
    match_ids = [line.strip() for line in ids_file.read_text().splitlines() if line.strip()]
    write_replay_orders(Path(os.environ["LIVEB_JOURNAL"]), Path(os.environ["REPLAY_ORDERS"]), match_ids)
    Path(os.environ["REPLAY_LOG"]).write_text("")
    sys.argv = ["backtest.run", *argv]
    run.main()
