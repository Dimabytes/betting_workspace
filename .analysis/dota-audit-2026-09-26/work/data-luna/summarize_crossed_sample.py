from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
WORK = ROOT / "work" / "data-luna"
maps = pd.read_csv(WORK / "crossed_sample_per_map.csv")
errors = pd.read_csv(WORK / "crossed_sample_mid_errors.csv")

for frame in (maps,):
    for col in frame.columns:
        if col not in ("month", "era"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0)

def dump(label: str, frame: pd.DataFrame) -> None:
    raw = {
        "maps": int(frame.match_id.nunique()),
        "raw_snapshots": int((frame.token0_snapshots + frame.token1_snapshots).sum()),
        "two_sided": int((frame.token0_two_sided + frame.token1_two_sided).sum()),
        "crossed": int((frame.token0_crossed + frame.token1_crossed).sum()),
        "locked": int((frame.token0_locked + frame.token1_locked).sum()),
        "ok_seconds": int(frame.market_ok.sum()),
        "ok_flagged": int(frame.market_ok_cross_or_locked.sum()),
        "current_cross_any": int(frame.market_ok_cross_any.sum()),
        "current_locked_any": int(frame.market_ok_locked_any.sum()),
        "both_cross_sum_pass": int(frame.market_both_cross_pair_sum_pass.sum()),
        "train_rows": int(frame.train_rows.sum()),
        "train_current_flag": int(frame.train_current_cross_or_locked.sum()),
        "train_labels": int(frame.train_label_rows.sum()),
        "train_label_flag": int(frame.train_label_cross_or_locked.sum()),
        "val_rows": int(frame.val_rows.sum()),
        "val_ok": int(frame.val_ok.sum()),
        "val_ok_flag": int(frame.val_ok_cross_or_locked.sum()),
        "val_labels": int(frame.val_label_rows.sum()),
        "val_label_flag": int(frame.val_label_cross_or_locked.sum()),
        "val_label_cross": int(frame.val_label_cross_any.sum()),
        "val_label_locked": int(frame.val_label_locked_any.sum()),
        "entry_rows": int(frame.entry_rows_le489.sum()),
        "entry_ok": int(frame.entry_ok_le489.sum()),
        "entry_ok_flag": int(frame.entry_ok_cross_or_locked_le489.sum()),
    }
    raw["cross_rate_of_two_sided"] = raw["crossed"] / max(raw["two_sided"], 1)
    raw["locked_rate_of_two_sided"] = raw["locked"] / max(raw["two_sided"], 1)
    raw["current_flag_rate_of_ok"] = raw["ok_flagged"] / max(raw["ok_seconds"], 1)
    raw["val_flag_rate_of_ok"] = raw["val_ok_flag"] / max(raw["val_ok"], 1)
    raw["val_label_flag_rate"] = raw["val_label_flag"] / max(raw["val_labels"], 1)
    raw["entry_flag_rate_of_ok"] = raw["entry_ok_flag"] / max(raw["entry_ok"], 1)
    print(label, raw)


for (month, era), frame in maps.groupby(["month", "era"], sort=True):
    dump(f"{month} {era}", frame)
print("-- ERA TOTALS --")
for era, frame in maps.groupby("era", sort=True):
    dump(era, frame)
print("-- VALIDATION CURRENT STRICT/LOCKED --")
pair = errors.groupby(["match_id", "market_second"], sort=False).agg(
    crossed=("condition", lambda col: bool(col.eq("crossed").any())),
    locked=("condition", lambda col: bool(col.eq("locked").any())),
    count=("condition", "size"),
).reset_index()
print("all", len(pair), "cross_pair_seconds", int(pair.crossed.sum()), "locked_pair_seconds", int(pair.locked.sum()), "mixed", int((pair.crossed & pair.locked).sum()))
entry = pair[pair.market_second <= 489]
print("entry", len(entry), "cross_pair_seconds", int(entry.crossed.sum()), "locked_pair_seconds", int(entry.locked.sum()), "mixed", int((entry.crossed & entry.locked).sum()))
val = pd.read_parquet(
    Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/new_processed/dataset/validation_dataset.parquet"),
    columns=["match_id", "second", "market_status"],
)
val = val[val.match_id.astype("int64").isin(set(maps.match_id.astype("int64")))].copy()
val_pairs = pair.merge(val[["match_id", "second", "market_status"]], left_on=["match_id", "market_second"], right_on=["match_id", "second"], how="inner")
val_pairs_ok = val_pairs[val_pairs.market_status.eq("ok")]
val_entry_ok = val_pairs_ok[val_pairs_ok.market_second.le(489)]
print("validation_flags_all", len(val_pairs), "cross", int(val_pairs.crossed.sum()), "locked", int(val_pairs.locked.sum()), "ok", len(val_pairs_ok), "ok_cross", int(val_pairs_ok.crossed.sum()), "ok_locked", int(val_pairs_ok.locked.sum()))
print("validation_entry_ok", len(val_entry_ok), "cross", int(val_entry_ok.crossed.sum()), "locked", int(val_entry_ok.locked.sum()))
print("-- MID ERROR OUTLIERS --")
print(errors.sort_values("pair_p_error_cents", ascending=False).head(10).to_string(index=False))
