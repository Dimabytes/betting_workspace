"""Compare a two-sided run to n50, paired by map.

Per-map PnL is engine_pnl + maker rebate. Leftover and pairs follow
reports/2026-10-07-two-sided-merge/work/orchestrator/decompose_two_sided.py:
leftover = settlement cash minus the tail's average fill cost; pairs = total PnL minus leftover.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/backtests/dota_maker")
PREFIX = "validation_join_delta02_x015_cut480_p45_"


def archive(name: str) -> Path:
    return ROOT / f"{PREFIX}{name}" / "_archive"


def per_map(name: str) -> pd.Series:
    results, fills, rebate = _load(name)
    return (results.engine_pnl + rebate).rename(name)


def metrics(name: str) -> dict[str, float]:
    results, fills, rebate = _load(name)
    pnl = results.engine_pnl + rebate
    leftover_pnl, leftover_settlement = _leftover(results, fills)
    total = float(pnl.sum())
    return {
        "maps": float(len(results)),
        "pnl": total,
        "worst": float(pnl.min()),
        "p5": _p5(pnl.to_numpy()),
        "leftover": leftover_pnl,
        "leftover_settlement": leftover_settlement,
        "pairs": total - leftover_pnl,
        "merge_usdc": float(results.merge_usdc.sum()),
        "rebate": float(rebate.sum()),
        "engine_pnl": float(results.engine_pnl.sum()),
        "worst_engine": float(results.engine_pnl.min()),
        "p5_engine": _p5(results.engine_pnl.to_numpy()),
    }


def paired(base: str, cand: str) -> dict[str, float]:
    diff = (per_map(cand) - per_map(base)).dropna()
    n = len(diff)
    if n < 2:
        raise SystemExit(f"paired {base} vs {cand}: only {n} maps")
    mean = float(diff.mean())
    sd = float(diff.std(ddof=1))
    t = mean / (sd / math.sqrt(n)) if sd else float("inf")
    return {"n": float(n), "delta": float(diff.sum()), "t": t}


def manifest_diff(base: str, cand: str) -> list[str]:
    left = json.loads((archive(base) / "manifest.json").read_text())
    right = json.loads((archive(cand) / "manifest.json").read_text())
    keys = sorted(set(left) | set(right))
    lines: list[str] = []
    for key in keys:
        if left.get(key) != right.get(key):
            lines.append(f"{key}: {left.get(key)!r} -> {right.get(key)!r}")
    return lines


def _load(name: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    run = archive(name)
    results = pd.read_parquet(run / "results.parquet").set_index("match_id")
    fills = pd.read_parquet(run / "fills.parquet")
    fills["notional"] = fills.price * fills.quantity
    rebate = fills.groupby("match_id").maker_rebate.sum().reindex(results.index).fillna(0.0)
    return results, fills, rebate


def _leftover(results: pd.DataFrame, fills: pd.DataFrame) -> tuple[float, float]:
    side = fills.groupby(["match_id", "token_index"]).agg(
        shares=("quantity", "sum"), cost=("notional", "sum")
    )
    leftover_cost = 0.0
    for match_id, row in results.iterrows():
        for token_index, leftover in ((0, row.leftover_shares_0), (1, row.leftover_shares_1)):
            key = (match_id, token_index)
            if leftover > 1e-6 and key in side.index:
                leftover_cost += leftover * side.loc[key, "cost"] / side.loc[key, "shares"]
    settlement = float(results.leftover_settlement_usdc.sum())
    return settlement - leftover_cost, settlement


def _p5(values) -> float:
    ordered = sorted(float(v) for v in values)
    last = len(ordered) - 1
    position = 0.05 * last
    low = int(position)
    high = min(low + 1, last)
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: compare.py <candidate-name> [<baseline-name>]")
    cand = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else _baseline_for(cand)
    print(f"# {cand} vs {base}")
    base_m = metrics(base)
    cand_m = metrics(cand)
    keys = (
        "maps",
        "pnl",
        "worst",
        "p5",
        "leftover",
        "pairs",
        "merge_usdc",
        "leftover_settlement",
        "rebate",
        "engine_pnl",
        "worst_engine",
        "p5_engine",
    )
    print(f"{'metric':<22} {'base':>14} {'cand':>14} {'delta':>14}")
    for key in keys:
        delta = cand_m[key] - base_m[key]
        print(f"{key:<22} {base_m[key]:14.4f} {cand_m[key]:14.4f} {delta:14.4f}")
    stats = paired(base, cand)
    print(f"paired_n {stats['n']:.0f} paired_delta {stats['delta']:.4f} paired_t {stats['t']:.4f}")
    print("manifest_diff:")
    diffs = manifest_diff(base, cand)
    if not diffs:
        print("  (none)")
    for line in diffs:
        print(f"  {line}")


def _baseline_for(name: str) -> str:
    if name.endswith("-nq"):
        return "ts-full-n50-nq"
    return "ts-full-n50"


if __name__ == "__main__":
    main()
