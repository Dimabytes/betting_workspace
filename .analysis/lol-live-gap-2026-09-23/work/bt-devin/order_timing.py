"""For LoL seed0 BUY fills: join quote_events to get submit/accept ts, compute
  order_signal_ts = latest dataset state_ts_us <= submit_ts (the signal that placed it)
  fill_ts - order_signal_ts  (how far into the event window the fill lands)
  accept_ts - order_signal_ts (signal->accept latency in sim)
and markout by time-since-signal buckets.
Also: fraction of fills landing before state_ts_us+8s (the GRID-arrival horizon).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23")
BASE = E / "data/backtests/lol_maker/LIVE/seed0"

fills = pd.read_parquet(BASE / "fills.parquet")
results = pd.read_parquet(BASE / "results.parquet")
fills = fills.merge(results[["match_id", "signal_mode"]], on="match_id", how="left")

qe = pd.read_parquet(BASE / "quote_events.parquet",
                     columns=["match_id", "ts_ns", "kind", "order_id", "side", "price", "reason"])
acc = qe[qe.kind.isin(["submitted", "accepted"])].copy()
acc_map = {}
for r in acc.itertuples():
    acc_map.setdefault(r.order_id, {})[r.kind] = (r.ts_ns, r.price)
    acc_map[r.order_id]["match_id"] = r.match_id

# latest signal ts per (match, time): use validation state_ts_us as decision grid (grid_v1);
# for schedule matches use archive received_ns
SCHED_DIR = E / "data/archive_index/schedules/trader/lol"
idx = pd.read_parquet(E / "data/archive_index/index.parquet")
audit = pd.read_parquet(E / "data/lol/processed/datasets/backtest_audit.parquet",
                      columns=["match_id", "condition_id"])
cond_by_match = dict(zip(audit.match_id, audit.condition_id.str.lower()))
idx_lol = idx[(idx.game == "lol") & (idx.admission == "admitted")]
arch_by_cond = {r.condition_id.lower(): r.archive_id for r in idx_lol.itertuples()
                if isinstance(r.condition_id, str)}
sched_by_match = {}
for mid in results.loc[results.signal_mode == "schedule", "match_id"]:
    aid = arch_by_cond.get(cond_by_match.get(mid, ""), None)
    if aid and (SCHED_DIR / f"{aid}.json").is_file():
        ticks = json.loads((SCHED_DIR / f"{aid}.json").read_text())["ticks"]
        sched_by_match[mid] = np.sort(np.array([t["received_ns"] for t in ticks], dtype=np.int64))

val = pd.read_parquet(E / "data/lol/processed/datasets/validation.parquet",
                      columns=["match_id", "second", "state_ts_us"])
sig_ts = {mid: np.sort(g["state_ts_us"].to_numpy(dtype=np.int64) * 1000)
          for mid, g in val.groupby("match_id")}
sec_ts = {mid: (g.sort_values("state_ts_us")["second"].to_numpy(),
                np.sort(g["state_ts_us"].to_numpy(dtype=np.int64)) * 1000)
          for mid, g in val.groupby("match_id")}

buys = fills[fills.side == "BUY"].copy().reset_index(drop=True)
n = len(buys)
submit_ns = np.full(n, np.nan)
accept_ns = np.full(n, np.nan)
sig_ts_of_order = np.full(n, np.nan)
game_sec_of_fill = np.full(n, np.nan)
for i, r in enumerate(buys.itertuples()):
    info = acc_map.get(r.order_id)
    if info is not None:
        if "submitted" in info:
            submit_ns[i] = info["submitted"][0]
        if "accepted" in info:
            accept_ns[i] = info["accepted"][0]
    grid = sched_by_match.get(r.match_id)
    if grid is None:
        grid = sig_ts.get(r.match_id)
    if grid is not None and not np.isnan(submit_ns[i]):
        pos = np.searchsorted(grid, np.int64(submit_ns[i]), side="right") - 1
        if pos >= 0:
            sig_ts_of_order[i] = grid[pos]
    sec = sec_ts.get(r.match_id)
    if sec is not None:
        secs, ts = sec
        pos = np.searchsorted(ts, r.ts_ns, side="right") - 1
        if pos >= 0:
            game_sec_of_fill[i] = secs[pos]

buys["submit_ns"] = submit_ns
buys["accept_ns"] = accept_ns
buys["sig_ts_ns"] = sig_ts_of_order
buys["game_second"] = game_sec_of_fill
buys["signal_to_submit_s"] = (buys.submit_ns - buys.sig_ts_ns) / 1e9
buys["submit_to_accept_s"] = (buys.accept_ns - buys.submit_ns) / 1e9
buys["signal_to_fill_s"] = (buys.ts_ns - buys.sig_ts_ns) / 1e9
buys["accept_to_fill_s"] = (buys.ts_ns - buys.accept_ns) / 1e9
# how late the fill lands vs when live GRID could deliver that state (~state+8s)
buys["fill_vs_grid8_s"] = buys.signal_to_fill_s - 8.0

def wavg(d, c="markout_30s", w="quantity"):
    wv = d[w].to_numpy(dtype=float)
    return np.nan if wv.sum() == 0 or len(d) == 0 else float(np.average(d[c], weights=wv))

print("n buys", len(buys), "with submit", buys.submit_ns.notna().sum())
print("\nsignal->submit (s):", buys.signal_to_submit_s.describe([.05,.25,.5,.75,.95,.99]).to_string())
print("\nsubmit->accept (s):", buys.submit_to_accept_s.describe([.05,.5,.95,.99]).to_string())
print("\nsignal->fill (s):", buys.signal_to_fill_s.describe([.05,.25,.5,.75,.95,.99]).to_string())
print("\naccept->fill (s):", buys.accept_to_fill_s.describe([.05,.25,.5,.75,.95,.99]).to_string())

print("\n-- mk30 by signal->fill bucket --")
buys["s2f"] = pd.cut(buys.signal_to_fill_s, [-1e9, 2, 4, 6, 8, 10, 15, 30, 60, 1e9])
for k, d in buys.groupby("s2f", observed=True):
    print(f"{str(k):>18} n={len(d):5d} qty={d.quantity.sum():9.0f} mk30={100*wavg(d):7.3f}c "
          f"mk300={100*wavg(d,'markout_300s'):7.3f}c")

print("\n-- mk30 by signal->fill bucket, grid_v1 only --")
g1 = buys[buys.signal_mode == "grid_v1"]
for k, d in g1.groupby(g1.s2f, observed=True):
    print(f"{str(k):>18} n={len(d):5d} qty={d.quantity.sum():9.0f} mk30={100*wavg(d):7.3f}c")

print("\n-- mk30 by signal->fill bucket, schedule only --")
g2 = buys[buys.signal_mode == "schedule"]
for k, d in g2.groupby(g2.s2f, observed=True):
    print(f"{str(k):>18} n={len(d):5d} qty={d.quantity.sum():9.0f} mk30={100*wavg(d):7.3f}c")

# fills inside the live-impossible window: fill before state+8s AND before live order
# could exist (live submit ~ state+8.3s)
buys["pre_grid_fill"] = buys.signal_to_fill_s < 8.0
print("\nfills landing before state+8s (live cannot be in queue):",
      buys.pre_grid_fill.sum(), "qty", buys.loc[buys.pre_grid_fill, "quantity"].sum())
for mode in ("grid_v1", "schedule"):
    d = buys[buys.signal_mode == mode]
    early = d[d.signal_to_fill_s < 8]
    late = d[d.signal_to_fill_s >= 8]
    print(f"{mode}: early n={len(early)} qty={early.quantity.sum():.0f} "
          f"mk30={100*wavg(early):.3f}c | late n={len(late)} qty={late.quantity.sum():.0f} "
          f"mk30={100*wavg(late):.3f}c")

# PnL attribution proxy: sum of qty*markout_300s for early vs late fills
for mode in ("grid_v1", "schedule", "all"):
    d = buys if mode == "all" else buys[buys.signal_mode == mode]
    early = d[d.signal_to_fill_s < 8]
    for lab, dd in (("early<8s", early), ("late>=8s", d[d.signal_to_fill_s >= 8])):
        edge30 = (dd.markout_30s * dd.quantity).sum()
        edge300 = (dd.markout_300s * dd.quantity).sum()
        print(f"{mode} {lab}: qty={dd.quantity.sum():.0f} sum qty*mk30=${edge30/100:.0f} "
              f"sum qty*mk300=${edge300/100:.0f}")

buys.to_parquet(R / "work/bt-devin/lol_buys_timing.parquet")
