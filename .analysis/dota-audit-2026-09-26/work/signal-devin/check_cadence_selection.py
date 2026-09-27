"""Measure real archive tick cadence in the model window and the selection
waterfall counts, plus >45s gaps (recovery-stale decision drops)."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(REPO / "src"))

from archive_index.schedule import read_schedule  # noqa: E402

INDEX = REPO / "data/archive_index/index.parquet"
RESULTS = REPO / "data/backtests/dota_maker/LIVE/seed0/results.parquet"
MANIFEST = json.load(open(REPO / "data/backtests/dota_maker/LIVE/seed0/manifest.json"))

index = pd.read_parquet(INDEX)
results = pd.read_parquet(RESULTS)

# --- selection waterfall ---------------------------------------------------
print("=== archive_exclusions in LIVE manifest ===")
reasons = pd.Series(MANIFEST["archive_exclusions"]).value_counts()
print(reasons.to_dict())

# --- cadence + gaps on schedule-mode maps ----------------------------------
sched_results = results[results["signal_mode"] == "schedule"]
print(f"schedule-mode maps: {len(sched_results)}")

gap45 = []  # matches with >45s gap inside window
gap_counts = []
intervals_all = []
pre_window_ticks = []
post_feature_ticks = []
first_last = []
links = pd.read_parquet(REPO / "data/new_processed/match_links/match_links.parquet").set_index("match_id")
by_arch = {
    (str(r.archive_root), str(r.archive_id)): r
    for r in index[index["admission"] == "admitted"].itertuples(index=False)
}
matched = 0
for match_id in sched_results["match_id"]:
    if match_id not in links.index:
        continue
    link = links.loc[match_id]
    row = by_arch.get((str(link["archive_root"]), str(link["archive_id"])))
    if row is None:
        continue
    matched += 1
    sp = row.schedule_path
    if not isinstance(sp, str) or not sp:
        continue
    ticks = read_schedule(Path(sp)).ticks
    rx = np.array([t.received_ns for t in ticks]) / 1e9
    gs = np.array([t.game_second for t in ticks])
    # model window ticks
    w = (gs >= -60) & (gs < 600)
    in_win_rx = rx[w]
    if len(in_win_rx) > 1:
        diffs = np.diff(in_win_rx)
        intervals_all.extend(diffs.tolist())
        gap_counts.append(int((diffs > 45).sum()))
    first_last.append((gs.min(), gs.max(), len(ticks)))
    pre_window_ticks.append(int((gs < -60).sum()))

print(f"matched schedules: {matched}")
iv = np.array(intervals_all)
if len(iv) == 0:
    raise SystemExit("no in-window intervals — join failed")
print(f"\nin-window inter-tick intervals: n={len(iv)} mean={iv.mean():.2f} "
      f"p50={np.percentile(iv,50):.2f} p90={np.percentile(iv,90):.2f} "
      f"p99={np.percentile(iv,99):.2f} max={iv.max():.0f}")
gc = np.array(gap_counts)
print(f"matches with >=1 gap>45s in window: {(gc>0).sum()}/{len(gc)}; total gaps: {gc.sum()}")
fl = np.array(first_last)
print(f"first game_second: min={fl[:,0].min()} p50={np.percentile(fl[:,0],50):.0f}; "
      f"last game_second: p50={np.percentile(fl[:,1],50):.0f} max={fl[:,1].max()}")
print(f"pre-window (second<-60) ticks per map: mean={np.mean(pre_window_ticks):.1f}")

# --- selection waterfall via live code -------------------------------------
from backtest.selection import load_market_sources, select_validation_matches  # noqa: E402
from backtest.signals import load_usable_signal_rows  # noqa: E402

signal_rows = load_usable_signal_rows()
sources = load_market_sources(signal_rows)
coverage, eligible = select_validation_matches(sources)
print("\n=== selection waterfall (current artifacts) ===")
print(
    f"validation={coverage.validation_matches} "
    f"book_gap={coverage.book_gap_excluded} "
    f"no_catalog={coverage.without_map_market} "
    f"no_signal_rows={coverage.without_signal_rows} "
    f"no_telonex={coverage.without_local_telonex} "
    f"eligible={coverage.eligible}"
)
sel = set(eligible)
arch = index[(index["game"] == "dota")]
sel_links = arch["steam_match_id"].astype(str)
print("dota archives total:", len(arch), "admitted:", (arch["admission"] == "admitted").sum())
