"""For each sim fill: did live B rest a bid on the same token at the same price at that moment? Was it filled live?"""
import sys
from pathlib import Path

import pandas as pd

sys.argv, args = sys.argv[:1], sys.argv[1:]
import run_b  # noqa: E402

segments = [
    (9034957701, "9034957701", "h3", 0, pd.Timestamp("2026-10-08T14:47:23Z").value),
    (9034957701, "9034957701", "957-h6", pd.Timestamp("2026-10-08T14:48:39Z").value, 2**62),
    (9035220432, "9035220432", "h6", 0, 2**62),
    (9035256154, "grid-3011822-m1", "h6", 0, 2**62),
    (9035318247, "9035318247", "h6", 0, 2**62),
    (9035434210, "9035434210", "h6", 0, 2**62),
]
root = Path("data/backtests/dota_maker")
rows = []
for match_id, archive, run, t_from, t_to in segments:
    events = sorted(run_b.load_b_events(run_b.TOKEN_MAPS[archive]), key=lambda e: e[0])
    orders = {}
    for t_us, op, payload in events:
        if op == "accept":
            order_id, resting = payload
            orders[order_id] = {"tok": resting.token_index, "px": round(resting.price, 2), "start": t_us, "end": 2**62, "filled": 0.0}
        elif op == "fill":
            orders[payload[0]]["filled"] += payload[1]
        elif op == "remove" and payload in orders:
            orders[payload]["end"] = min(orders[payload]["end"], t_us)
    live = pd.DataFrame(orders.values())
    sim = pd.read_parquet(root / f"validation_join_delta02_x015_cut480_p45_liveb1008-{run}-bstrip/seed0/fills.parquet")
    sim = sim[(sim.match_id == match_id) & (sim.ts_ns >= t_from) & (sim.ts_ns < t_to)]
    for fill in sim.itertuples():
        t_us = fill.ts_ns // 1000
        same = live[(live.tok == fill.token_index) & (live.px == round(fill.price, 2)) & (live.start <= t_us) & (live.end >= t_us - 2_000_000)]
        near = live[(live.tok == fill.token_index) & (live.start <= t_us) & (live.end >= t_us)]
        live_px = near.px.max() if len(near) else None
        status = "live same px, filled" if (same.filled > 0).any() else "live same px, not filled" if len(same) else "live other px" if len(near) else "live no bid"
        rows.append({"match_id": match_id, "seg": run, "qty": fill.quantity, "status": status,
                     "dpx_ticks": None if live_px is None else round((fill.price - live_px) * 100), "mk30": fill.markout_30s})
table = pd.DataFrame(rows)
summary = table.groupby("status").agg(fills=("qty", "size"), shares=("qty", "sum"), mk30_c=("mk30", lambda s: round(100 * (s * table.loc[s.index, "qty"]).sum() / table.loc[s.index, "qty"].sum(), 2)))
print(summary.to_string())
print(table[table.status == "live other px"].groupby("dpx_ticks").qty.agg(["size", "sum"]).to_string())
