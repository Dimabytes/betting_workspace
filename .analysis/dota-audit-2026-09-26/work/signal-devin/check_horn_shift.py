"""Check whether GRID schedule game_second labels are pause-aware (freeze during
pauses) or pause-blind (count wall time), and whether the per-tick
horn_unix_seconds drifts. Also measure the offset between the feed's wall time
of game_second 0 and both the catalog archive horn and the true horn
(spawn + 90 + pre-horn pause D)."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(REPO / "src"))

from archive_index.schedule import read_schedule  # noqa: E402
from shared.utils.match_time import parse_utc  # noqa: E402

LINKS = REPO / "data/new_processed/match_links/match_links.parquet"
WINDOWS = REPO / "data/new_processed/grid_game_starts/grid_game_windows.parquet"
CATALOG = REPO / "data/new_processed/match_catalog/match_catalog.parquet"
INDEX = REPO / "data/archive_index/index.parquet"
RESULTS = REPO / "data/backtests/dota_maker/LIVE/seed0/results.parquet"

links = pd.read_parquet(LINKS)
windows = pd.read_parquet(WINDOWS)
catalog = pd.read_parquet(CATALOG)
index = pd.read_parquet(INDEX)
results = pd.read_parquet(RESULTS)

cat = catalog.set_index("match_id")
spawn_by_cond = windows.set_index("condition_id")["spawn_at"]

sched_results = results[results["signal_mode"] == "schedule"]
print(f"schedule-mode maps in LIVE: {len(sched_results)}")

rows = []
for match_id in sched_results["match_id"]:
    link = links[links["match_id"] == match_id]
    if link.empty:
        continue
    link = link.iloc[0]
    idx_row = index[
        (index["archive_id"] == str(link["archive_id"]))
        & (index["archive_root"] == str(link["archive_root"]))
    ]
    if idx_row.empty:
        continue
    idx_row = idx_row.iloc[0]
    sched_path = idx_row["schedule_path"]
    if not isinstance(sched_path, str) or not sched_path:
        continue
    schedule = read_schedule(Path(sched_path))
    ticks = schedule.ticks
    if len(ticks) < 30:
        continue

    # horn stamps on ticks
    horns = np.array([t.horn_unix_seconds for t in ticks], dtype=np.int64)
    unique_horns = np.unique(horns)

    cond = str(link["map_condition_id"])
    spawn_raw = spawn_by_cond.get(cond)
    spawn = parse_utc(spawn_raw).timestamp() if isinstance(spawn_raw, str) else np.nan

    archive_horn_raw = link["archive_horn_at_utc"]
    archive_horn = (
        parse_utc(archive_horn_raw).timestamp() if isinstance(archive_horn_raw, str) else np.nan
    )

    c = cat.loc[match_id] if match_id in cat.index else None
    pauses = json.loads(c["pauses_json"]) if c is not None and isinstance(c.get("pauses_json"), str) else []
    pre_horn_d = sum(p["duration"] for p in pauses if -90 <= p["time"] < 0)
    horn_cat = parse_utc(c["horn_at"]).timestamp() if c is not None else np.nan

    # intercept: received_ns at game_second=0 via linear fit on unpaused ticks in [0,600]
    gs = np.array([t.game_second for t in ticks], dtype=np.int64)
    rx = np.array([t.received_ns for t in ticks], dtype=np.float64) / 1e9
    pa = np.array([t.paused for t in ticks])
    st = np.array([t.source_ts_unix for t in ticks], dtype=np.int64)
    mask = (gs >= 0) & (gs <= 600) & (~pa)
    if mask.sum() >= 5:
        coef = np.polyfit(gs[mask], rx[mask], 1)
        intercept = coef[1]  # wall time when label read 0
        slope = coef[0]
    else:
        intercept, slope = np.nan, np.nan

    # does game_second advance during paused ticks?
    paused_adv = np.nan
    if pa.any():
        idxs = np.where(pa)[0]
        paused_adv = gs[idxs[-1]] - gs[idxs[0]]

    rows.append(
        dict(
            match_id=match_id,
            feed=idx_row["feed_source"],
            n_ticks=len(ticks),
            spawn=spawn,
            archive_horn=archive_horn,
            catalog_horn=horn_cat,
            pre_horn_d=pre_horn_d,
            tick_horn_min=int(unique_horns.min()),
            tick_horn_max=int(unique_horns.max()),
            tick_horn_nuniq=len(unique_horns),
            intercept=intercept,
            slope=slope,
            paused_tick_adv=paused_adv,
        )
    )

df = pd.DataFrame(rows)
df["true_horn"] = df["spawn"] + 90 + df["pre_horn_d"]
df["horn_err"] = df["archive_horn"] - df["true_horn"]
df["intercept_minus_archive"] = df["intercept"] - df["archive_horn"]
df["intercept_minus_true"] = df["intercept"] - df["true_horn"]
df["tickhorn_minus_archive"] = df["tick_horn_min"] - df["archive_horn"]

grid = df[df["feed"] == "grid"].dropna(subset=["intercept"])
print(f"\n=== GRID maps with fit: {len(grid)} ===")
print("horn_err (archive - true):", grid["horn_err"].describe().round(1).to_dict())
print("intercept - archive_horn :", grid["intercept_minus_archive"].describe().round(1).to_dict())
print("intercept - true_horn    :", grid["intercept_minus_true"].describe().round(1).to_dict())
print("slope:", grid["slope"].describe().round(3).to_dict())
print("tick horn uniq counts:", grid["tick_horn_nuniq"].describe().round(1).to_dict())
print("tick_horn_min - archive:", grid["tickhorn_minus_archive"].describe().round(1).to_dict())
print("paused game_second advance:", grid["paused_tick_adv"].describe().round(1).to_dict())

affected = grid[grid["pre_horn_d"] >= 20]
print(f"\nGRID maps with pre_horn_d>=20: {len(affected)}")
print(affected[["match_id", "pre_horn_d", "horn_err", "intercept_minus_archive", "intercept_minus_true", "tick_horn_nuniq", "slope", "paused_tick_adv"]].round(1).to_string())

oddin = df[df["feed"] == "oddin"].dropna(subset=["intercept"])
print(f"\n=== ODDIN maps with fit: {len(oddin)} ===")
if len(oddin):
    print("horn_err:", oddin["horn_err"].describe().round(1).to_dict())
    print("intercept - archive:", oddin["intercept_minus_archive"].describe().round(1).to_dict())
    print("intercept - true   :", oddin["intercept_minus_true"].describe().round(1).to_dict())

df.to_csv("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/signal-devin/horn_shift.csv", index=False)
