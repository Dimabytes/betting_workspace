"""Live B resting bids against a sim run's resting bids on a 100 ms grid, per token.

Live orders from the journal (exchange PLACEMENT to CANCELLATION, last fill when
filled out, next-decision end when no WS cancel came). Sim orders from
quote_events (accepted to canceled, last fill when filled out). Segments as in
sim/compare.py: 9034957701 ran half-spread 3 until 14:47:23 UTC and 6 from 14:48:39.

usage (from esports-trader):
  PYTHONPATH=src:scripts .venv/bin/python compare_timeline.py <journal> <variant>
"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from journal_to_core_trace import load_orders

GRID_US = 100_000
CATALOG = Path("data/new_processed/match_catalog/match_catalog.parquet")
ROOT = Path(__file__).resolve().parents[2] / "work" / "sim-runs"
H3_END = pd.Timestamp("2026-10-08T14:47:23Z").value // 1000
H6_START = pd.Timestamp("2026-10-08T14:48:39Z").value // 1000
FAR = 2**62


def segments(variant: str) -> list[tuple[int, str, int, int, Path]]:
    run = lambda name: ROOT / f"validation_join_delta02_x015_cut480_p45_liveb1008-{name}-{variant}" / "seed0"  # noqa: E731
    out = [(9034957701, "h3", 0, H3_END, run("h3")), (9034957701, "h6", H6_START, FAR, run("957-h6"))]
    out += [(m, "h6", 0, FAR, run("h6")) for m in (9035220432, 9035256154, 9035318247, 9035434210)]
    return out


def live_intervals(rows: list[dict], tokens: dict[str, int]) -> pd.DataFrame:
    out = []
    for order in load_orders(rows, tokens):
        end = order.end_us
        if order.matched >= order.size - 1e-6 and order.fills:
            end = max(us for us, _ in order.fills) if end is None else min(end, max(us for us, _ in order.fills))
        out.append({"token": order.token_index, "start": order.placed_us, "end": FAR if end is None else end,
                    "price": order.price})
    return pd.DataFrame(out)


def sim_intervals(run_dir: Path, match_id: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    q = pd.read_parquet(run_dir / "quote_events.parquet")
    q = q[q.match_id == match_id]
    fills = pd.read_parquet(run_dir / "fills.parquet")
    fills = fills[fills.match_id == match_id]
    sub = q[q.kind == "submitted"].set_index("order_id")
    acc = q[q.kind == "accepted"].groupby("order_id").ts_ns.min()
    can = q[q.kind == "canceled"].groupby("order_id").ts_ns.min()
    filled = fills.groupby("order_id").agg(qty=("quantity", "sum"), last=("ts_ns", "max"))
    out = []
    for oid, s in sub.iterrows():
        if oid not in acc.index:
            continue
        end = can.get(oid, FAR * 1000)
        if oid in filled.index and filled.loc[oid, "qty"] >= s.quantity - 1e-6:
            end = min(end, filled.loc[oid, "last"])
        out.append({"token": int(s.token_index), "start": acc[oid] // 1000, "end": end // 1000, "price": s.price})
    reasons = q[q.kind.isin(["no_quote", "cancel_request"])][["ts_ns", "reason"]].sort_values("ts_ns")
    return pd.DataFrame(out), reasons


def price_at(intervals: pd.DataFrame, token: int, grid: np.ndarray) -> np.ndarray:
    price = np.full(grid.size, np.nan)
    for row in intervals[intervals.token == token].sort_values("start").itertuples():
        mask = (grid >= row.start) & (grid < row.end)
        price[mask] = row.price
    return price


def main() -> None:
    rows = [json.loads(line) for line in Path(sys.argv[1]).open() if line.strip()]
    variant = sys.argv[2]
    catalog = pd.read_parquet(CATALOG).set_index("match_id")
    total: Counter = Counter()
    why: Counter = Counter()
    lines = []
    for match_id, label, lo, hi, run_dir in segments(variant):
        entry = catalog.loc[match_id]
        tokens = {str(entry.token_id_0): 0, str(entry.token_id_1): 1}
        live = live_intervals(rows, tokens)
        sim, reasons = sim_intervals(run_dir, match_id)
        live = live[(live.start < hi) & (live.end > lo)]
        sim = sim[(sim.start < hi) & (sim.end > lo)]
        both = pd.concat([live, sim])
        start = max(lo, int(both.start.min()))
        end = min(hi, int(both.end[both.end < FAR].max()))
        grid = np.arange(start, end, GRID_US)
        c: Counter = Counter()
        for token in (0, 1):
            lp, sp = price_at(live, token, grid), price_at(sim, token, grid)
            has_l, has_s = ~np.isnan(lp), ~np.isnan(sp)
            same = has_l & has_s & (np.abs(lp - sp) < 1e-9)
            c["same"] += int(same.sum())
            c["diff"] += int((has_l & has_s & ~same).sum())
            c["live_only"] += int((has_l & ~has_s).sum())
            c["sim_only"] += int((~has_l & has_s).sum())
            c["neither"] += int((~has_l & ~has_s).sum())
            gap = grid[has_l & ~has_s] * 1000
            idx = np.searchsorted(reasons.ts_ns.to_numpy(), gap, side="right") - 1
            why.update(np.where(idx >= 0, reasons.reason.to_numpy()[np.maximum(idx, 0)], "none"))
        n = sum(c.values())
        live_n = int(((live.start >= start) & (live.start < end)).sum())
        sim_n = int(((sim.start >= start) & (sim.start < end)).sum())
        lines.append(f"{match_id} {label}: {n / 20 / 60:.1f} token-min | same {c['same'] / n:.1%} diff {c['diff'] / n:.1%} "
                     f"live_only {c['live_only'] / n:.1%} sim_only {c['sim_only'] / n:.1%} neither {c['neither'] / n:.1%} "
                     f"| placements live {live_n} sim {sim_n}")
        total.update(c)
    print("\n".join(lines))
    n = sum(total.values())
    print("total: " + " ".join(f"{k} {v / n:.1%}" for k, v in total.items()))
    print("sim's last pull reason during live_only:", dict(why.most_common()))


if __name__ == "__main__":
    main()
