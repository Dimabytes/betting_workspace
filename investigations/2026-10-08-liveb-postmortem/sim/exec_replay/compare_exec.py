"""Compare a journal replay with live, order by order: did Nautilus fill what live filled?

usage (from esports-trader): python compare_exec.py RUN_DIR ORDERS_JSON REPLAY_LOG [OUT_CSV]
RUN_DIR is the replay's seed0 dir (fills.parquet, quote_events.parquet).
PnL and the 30 s markout use the same rules as sim/compare.py: settlement
payout minus price, and the Telonex book mid 30 s after the fill.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path("data/raw/telonex/polymarket/book_snapshot_full")
DAY = "2026-10-08"
CATALOG = Path("data/new_processed/match_catalog/match_catalog.parquet")
MARKOUT_NS = 30_000_000_000


class Mids:
    def __init__(self, token: str) -> None:
        book = pd.read_parquet(RAW / f"asset_id={token}" / f"{DAY}.parquet",
                               columns=["timestamp_us", "bids", "asks"])
        self.ts = book.timestamp_us.to_numpy()
        self.bids = book.bids.to_numpy()
        self.asks = book.asks.to_numpy()

    def at(self, t_ns: int) -> float:
        i = max(int(np.searchsorted(self.ts, t_ns // 1000, side="right")) - 1, 0)
        bid = max((float(x["price"]) for x in self.bids[i]), default=0.0)
        ask = min((float(x["price"]) for x in self.asks[i]), default=1.0)
        return (bid + ask) / 2


def load(run_dir: Path, orders_path: Path, log_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Per-order table plus live and sim fill tables keyed by live order id."""
    orders = [{**o, "match_id": int(m)} for m, rows in json.loads(orders_path.read_text()).items() for o in rows]
    log = [json.loads(line) for line in log_path.open()]
    live_of = {row["cid"]: row["live_id"] for row in log if row["kind"] == "submit"}
    sim = pd.read_parquet(run_dir / "fills.parquet")
    sim["live_id"] = sim.order_id.map(live_of)
    quotes = pd.read_parquet(run_dir / "quote_events.parquet")
    rejected = {live_of.get(cid) for cid in quotes[quotes.kind == "rejected"].order_id}
    live = pd.DataFrame([{"live_id": o["order_id"], "ts_ns": ns, "quantity": qty}
                         for o in orders for ns, qty in o["live_fills"]])
    table = pd.DataFrame(orders).rename(columns={"order_id": "live_id"})
    table["live_qty"] = table.live_id.map(live.groupby("live_id").quantity.sum()).fillna(0.0)
    table["sim_qty"] = table.live_id.map(sim.groupby("live_id").quantity.sum()).fillna(0.0)
    table["live_first_ns"] = table.live_id.map(live.groupby("live_id").ts_ns.min())
    table["sim_first_ns"] = table.live_id.map(sim.groupby("live_id").ts_ns.min())
    table["submitted"] = table.live_id.isin(set(live_of.values()))
    table["rejected"] = table.live_id.isin(rejected)
    attrs = table.set_index("live_id")[["match_id", "token_id", "price"]]
    live = live.join(attrs, on="live_id")
    sim = sim[["live_id", "ts_ns", "quantity"]].join(attrs, on="live_id")
    return table, live, sim


def value(fills: pd.DataFrame, catalog: pd.DataFrame, mids: dict[str, Mids]) -> tuple[float, float]:
    """(PnL at settlement, 30 s markout in cents per share)."""
    if fills.empty:
        return 0.0, float("nan")
    row = catalog.loc[fills.match_id]
    winner_token = np.where(row.radiant_win.to_numpy() == (row.radiant_token_index.to_numpy() == 0),
                            row.token_id_0.astype(str).to_numpy(), row.token_id_1.astype(str).to_numpy())
    payout = (fills.token_id.to_numpy() == winner_token).astype(float)
    pnl = float((fills.quantity * (payout - fills.price)).sum())
    marks = np.array([mids[t].at(ns + MARKOUT_NS) for t, ns in zip(fills.token_id, fills.ts_ns)])
    mk = float(100 * (fills.quantity * (marks - fills.price)).sum() / fills.quantity.sum())
    return pnl, mk


def main() -> None:
    run_dir, orders_path, log_path = (Path(arg) for arg in sys.argv[1:4])
    table, live, sim = load(run_dir, orders_path, log_path)
    catalog = pd.read_parquet(CATALOG).set_index("match_id")
    mids = {token: Mids(token) for token in table.token_id.unique()}
    both = (table.live_qty > 0) & (table.sim_qty > 0)
    live_only = (table.live_qty > 0) & (table.sim_qty == 0)
    sim_only = (table.live_qty == 0) & (table.sim_qty > 0)
    print(f"orders {len(table)}, submitted {table.submitted.sum()}, rejected post-only {table.rejected.sum()}")
    print(pd.DataFrame({
        "orders": [both.sum(), live_only.sum(), sim_only.sum()],
        "live_sh": [table.live_qty[m].sum() for m in (both, live_only, sim_only)],
        "sim_sh": [table.sim_qty[m].sum() for m in (both, live_only, sim_only)],
    }, index=["filled in both", "live only", "sim only"]).round(1).to_string())
    gap = (table.sim_first_ns - table.live_first_ns)[both] / 1e6
    print("first fill sim - live, ms: p10 %.0f p50 %.0f p90 %.0f" % tuple(np.percentile(gap, [10, 50, 90])))
    for name, fills in (("live", live), ("sim", sim)):
        pnl, mk = value(fills, catalog, mids)
        print(f"{name}: fills {len(fills)}, shares {fills.quantity.sum():.1f}, pnl {pnl:.2f}, mk30 {mk:.2f} c/share")
    per_match = table.groupby("match_id")[["live_qty", "sim_qty"]].sum().round(1)
    print(per_match.to_string())
    if len(sys.argv) > 4:
        table.to_csv(sys.argv[4], index=False)


if __name__ == "__main__":
    main()
