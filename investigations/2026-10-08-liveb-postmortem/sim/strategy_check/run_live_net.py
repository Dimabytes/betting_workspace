"""backtest.run with LiveNetStrategy (live_net.py) instead of the two-sided strategy.

Writes LIVE_NET before the run: per match, [receipt ns, radiant shares, dire
shares] after each size_matched increase of wallet B's BUY orders.

usage (from esports-trader):
  PYTHONPATH=src:scripts:../prediction-market-backtesting:<this dir> LIVEB_JOURNAL=... LIVE_NET=<out>.json \
  .venv/bin/python run_live_net.py <backtest.run args incl. --strategy two-sided --own-archive-root ...>
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

from backtest import run
from shared.constants.paths import MATCH_CATALOG_PATH

STRATEGY = "live_net:LiveNetStrategy"


def write_live_net(journal: Path, out: Path, match_ids: list[str]) -> None:
    rows = [json.loads(line) for line in journal.open() if line.strip()]
    catalog = pd.read_parquet(MATCH_CATALOG_PATH).set_index("match_id")
    result: dict[str, list[list[float]]] = {}
    for match_id in match_ids:
        entry = catalog.loc[int(match_id)]
        radiant = int(entry.radiant_token_index)
        side_of = {str(entry.token_id_0): 0 if radiant == 0 else 1, str(entry.token_id_1): 0 if radiant == 1 else 1}
        matched: dict[str, float] = defaultdict(float)
        held = [0.0, 0.0]
        steps: list[list[float]] = []
        for row in sorted((r for r in rows if r["kind"] == "user_order"), key=lambda r: r["ts"]):
            data = row["data"]
            leg = side_of.get(data["asset_id"])
            if leg is None or data["side"] != "BUY":
                continue
            step = float(data["size_matched"]) - matched[data["id"]]
            if step <= 1e-9:
                continue
            matched[data["id"]] += step
            held[leg] += step
            steps.append([int(row["ts"] * 1_000_000) * 1_000, held[0], held[1]])
        result[match_id] = steps
    out.write_text(json.dumps(result))


def _configs(*args, **kwargs):
    configs = _original(*args, **kwargs)
    for config in configs:
        config["strategy_path"] = STRATEGY
    return configs


_original = run.build_strategy_configs
run.build_strategy_configs = _configs

if __name__ == "__main__":
    argv = sys.argv[1:]
    ids_file = Path(argv[argv.index("--match-ids-file") + 1])
    match_ids = [line.strip() for line in ids_file.read_text().splitlines() if line.strip()]
    write_live_net(Path(os.environ["LIVEB_JOURNAL"]), Path(os.environ["LIVE_NET"]), match_ids)
    sys.argv = ["backtest.run", *argv]
    run.main()
