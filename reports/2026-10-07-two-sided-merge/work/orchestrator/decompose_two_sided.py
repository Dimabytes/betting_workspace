"""Pairs-vs-leftover decomposition for two-sided backtest runs.

    cd ../esports-trader && uv run python ../betting_workspace/reports/2026-10-07-two-sided-merge/work/orchestrator/decompose_two_sided.py \
        data/backtests/dota_maker/validation_join_*_ts-full-*/_archive

Each argument is a run directory holding results.parquet and fills.parquet.
Leftover cost is approximated at the side's average fill price on that map.
"""

import glob
import sys
from pathlib import Path

import pandas as pd


def decompose(run_dir: Path) -> dict[str, float | str]:
    results = pd.read_parquet(run_dir / "results.parquet").set_index("match_id")
    fills = pd.read_parquet(run_dir / "fills.parquet")
    fills["notional"] = fills.price * fills.quantity
    rebate = fills.groupby("match_id").maker_rebate.sum().reindex(results.index).fillna(0.0)
    per_map = results.engine_pnl + rebate
    side_totals = fills.groupby(["match_id", "token_index"]).agg(shares=("quantity", "sum"), cost=("notional", "sum"))
    leftover_cost = 0.0
    for match_id, row in results.iterrows():
        for token_index, leftover in ((0, row.leftover_shares_0), (1, row.leftover_shares_1)):
            key = (match_id, token_index)
            if leftover > 1e-6 and key in side_totals.index:
                leftover_cost += leftover * side_totals.loc[key, "cost"] / side_totals.loc[key, "shares"]
    leftover_shares = (results.leftover_shares_0 + results.leftover_shares_1).sum()
    leftover_pnl = results.leftover_settlement_usdc.sum() - leftover_cost
    total = per_map.sum()
    bought = fills.notional.sum()
    merged = results.merge_usdc.sum()
    return {
        "run": run_dir.parent.name.split("_p45_")[-1] + "/" + run_dir.name,
        "maps": len(results),
        "pnl": round(total, 1),
        "bought": round(bought),
        "c_per_$": round(100 * total / bought, 2) if bought else float("nan"),
        "per_map": round(per_map.mean(), 2),
        "neg_%": round(100 * (per_map < 0).mean(), 1),
        "worst": round(per_map.min(), 1),
        "pairs": round(total - leftover_pnl, 1),
        "c_per_pair_$": round(100 * (total - leftover_pnl) / merged, 2) if merged else float("nan"),
        "leftover": round(leftover_pnl, 1),
        "leftover_sh": round(leftover_shares),
        "leftover_pays_%": round(100 * results.leftover_settlement_usdc.sum() / leftover_shares, 0) if leftover_shares else float("nan"),
        "fills_per_map": round(len(fills) / len(results), 1),
    }


def main() -> None:
    run_dirs = [Path(p) for pattern in sys.argv[1:] for p in sorted(glob.glob(pattern))]
    if not run_dirs:
        raise SystemExit(__doc__)
    table = pd.DataFrame([decompose(run_dir) for run_dir in run_dirs if (run_dir / "fills.parquet").exists()])
    pd.set_option("display.width", 250)
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
