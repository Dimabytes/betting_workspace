"""LoL backtest clock: as-of join, schedule offset, grid-v1 vs schedule markout."""

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from archive_index.schedule import read_schedule, schedule_path_for
from shared.constants.lol import (
    LOL_BACKTEST_AUDIT_PATH,
    LOL_DATASETS_DIR,
    LOL_RESEARCH_MODEL_DIR,
    LOL_SPLIT_PATH,
)
from shared.constants.paths import ARCHIVE_INDEX_DIR

BT = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/backtests/lol_maker"
    "/validation_join_delta02_x015_cut480_p35_s06-playback-gf/seed0"
)
OUT = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/clock-grok/bt_clock.json"
)
WINDOW_END = 540


def pct(values: np.ndarray) -> dict[str, float]:
    if values.size == 0:
        return {"n": 0}
    qs = np.percentile(values, [5, 25, 50, 75, 95])
    return {
        "n": int(values.size),
        "mean": float(values.mean()),
        "p05": float(qs[0]),
        "p25": float(qs[1]),
        "p50": float(qs[2]),
        "p75": float(qs[3]),
        "p95": float(qs[4]),
        "frac_negative": float(np.mean(values < 0)),
        "frac_lt_neg2": float(np.mean(values < -2)),
        "frac_gt_2": float(np.mean(values > 2)),
    }


def weighted_markout(markout: np.ndarray, qty: np.ndarray) -> float | None:
    total = float(qty.sum())
    if total <= 0:
        return None
    return float(np.sum(markout * qty) / total)


def asof_price(seconds: np.ndarray, prices: np.ndarray, target: int) -> float | None:
    idx = int(np.searchsorted(seconds, target, side="right") - 1)
    if idx < 0:
        return None
    return float(prices[idx])


def main() -> None:
    report: dict[str, object] = {}

    gains = np.zeros(12, dtype=np.float64)
    names: list[str] | None = None
    for path in sorted(LOL_RESEARCH_MODEL_DIR.glob("member_*.txt")):
        booster = lgb.Booster(model_file=str(path))
        if names is None:
            names = list(booster.feature_name())
        gains += booster.feature_importance(importance_type="gain")
    assert names is not None
    order = np.argsort(-gains)
    report["research_gain"] = [
        {"feature": names[int(i)], "gain": float(gains[int(i)]), "share": float(gains[int(i)] / gains.sum())}
        for i in order
    ]

    audit = pd.read_parquet(
        LOL_BACKTEST_AUDIT_PATH, columns=["match_id", "condition_id", "radiant_token_index", "eligible"]
    )
    audit["condition_id"] = audit["condition_id"].str.lower()
    condition_to_match = {
        str(row.condition_id): int(row.match_id) for row in audit.itertuples(index=False)
    }
    radiant_index = {
        int(row.match_id): int(row.radiant_token_index) for row in audit.itertuples(index=False)
    }

    index = pd.read_parquet(ARCHIVE_INDEX_DIR / "index.parquet")
    lol_index = index[(index["meta_ok"] == True) & (index["game"].astype(str) == "lol")]  # noqa: E712
    admitted = lol_index[lol_index["admission"].astype(str) == "admitted"]
    schedule_match_ids: set[int] = set()
    missing_schedule = 0
    for row in admitted.itertuples(index=False):
        condition = str(row.condition_id or "").lower()
        match_id = condition_to_match.get(condition)
        if match_id is None:
            continue
        path = schedule_path_for(ARCHIVE_INDEX_DIR, str(row.archive_root), "lol", str(row.archive_id))
        if path.is_file():
            schedule_match_ids.add(match_id)
        else:
            missing_schedule += 1
    report["admitted_lol_archives"] = int(len(admitted))
    report["schedule_matches_in_audit"] = len(schedule_match_ids)
    report["admitted_missing_schedule_file"] = missing_schedule

    results = pd.read_parquet(BT / "results.parquet")
    report["results_columns"] = list(results.columns)
    pnl_col = "net_pnl" if "net_pnl" in results.columns else None
    if pnl_col is None:
        for candidate in results.columns:
            if "pnl" in candidate:
                pnl_col = candidate
                break
    report["pnl_column"] = pnl_col
    results["match_id"] = results["match_id"].astype(np.int64)
    results["cohort"] = np.where(results["match_id"].isin(schedule_match_ids), "schedule", "grid_v1")
    cohort_counts = results["cohort"].value_counts().to_dict()
    report["result_maps"] = {key: int(value) for key, value in cohort_counts.items()}
    if pnl_col is not None:
        grouped = results.groupby("cohort")[pnl_col].agg(["count", "sum", "mean"])
        report["pnl_by_cohort"] = {
            str(idx): {"maps": int(row["count"]), "sum": float(row["sum"]), "mean": float(row["mean"])}
            for idx, row in grouped.iterrows()
        }
        report["pnl_total"] = float(results[pnl_col].sum())

    market = pd.read_parquet(
        LOL_DATASETS_DIR / "market_seconds.parquet",
        columns=["match_id", "second", "state_ts_us", "market_p_radiant", "market_status"],
    )
    market["match_id"] = market["match_id"].astype(np.int64)
    market["second"] = market["second"].astype(np.int64)
    walls = market[["match_id", "second", "state_ts_us"]].drop_duplicates(["match_id", "second"])
    wall_lookup = {
        (int(row.match_id), int(row.second)): int(row.state_ts_us) for row in walls.itertuples(index=False)
    }

    # Schedule tick offset vs the livestats wall of the named game second.
    tick_offsets: list[float] = []
    internal_lags: list[float] = []
    spawn_offsets: list[float] = []
    match_median_offsets: list[float] = []
    ahead_ticks = 0
    used_ticks = 0
    schedule_dir = ARCHIVE_INDEX_DIR / "schedules" / "trader" / "lol"
    for path in sorted(schedule_dir.glob("*.json")):
        schedule = read_schedule(path)
        match_id = condition_to_match.get(schedule.identity.condition_id.lower())
        if match_id is None or match_id not in schedule_match_ids:
            continue
        spawn_wall = wall_lookup.get((match_id, 0))
        if spawn_wall is not None and schedule.ticks:
            horn = schedule.ticks[0].horn_unix_seconds
            spawn_offsets.append(horn - spawn_wall / 1_000_000)
        per_match: list[float] = []
        for tick in schedule.ticks:
            if tick.paused or tick.terminal:
                continue
            if tick.game_second < 0 or tick.game_second > WINDOW_END:
                continue
            wall = wall_lookup.get((match_id, tick.game_second))
            if wall is None:
                continue
            offset = tick.received_ns / 1_000_000_000 - wall / 1_000_000
            internal = tick.received_ns / 1_000_000_000 - (tick.horn_unix_seconds + tick.game_second)
            tick_offsets.append(offset)
            internal_lags.append(internal)
            per_match.append(offset)
            used_ticks += 1
            if offset < -2:
                ahead_ticks += 1
        if per_match:
            match_median_offsets.append(float(np.median(per_match)))
    offsets = np.asarray(tick_offsets, dtype=np.float64)
    report["schedule_offset_received_minus_livestats_wall"] = pct(offsets)
    report["schedule_internal_lag_received_minus_horn_plus_second"] = pct(
        np.asarray(internal_lags, dtype=np.float64)
    )
    report["schedule_spawn_horn_minus_livestats_second0"] = pct(np.asarray(spawn_offsets, dtype=np.float64))
    report["schedule_match_median_offset"] = pct(np.asarray(match_median_offsets, dtype=np.float64))
    report["schedule_ticks_used"] = used_ticks
    report["schedule_ticks_ahead_by_more_than_2s"] = ahead_ticks

    # Dataset market_p is the as-of-0 join if it matches the same-second mid, not second+10.
    validation = pd.read_parquet(
        LOL_DATASETS_DIR / "validation.parquet",
        columns=[
            "match_id",
            "second",
            "state_ts_us",
            "market_p_radiant",
            "deaths_radiant",
            "deaths_dire",
            "signal_market_p_radiant_300s",
        ],
    )
    validation["match_id"] = validation["match_id"].astype(np.int64)
    validation["second"] = validation["second"].astype(np.int64)
    ok_market = market[market["market_status"] == "ok"][
        ["match_id", "second", "market_p_radiant"]
    ].rename(columns={"market_p_radiant": "mid_at_second"})
    joined = validation.merge(ok_market, on=["match_id", "second"], how="inner")
    plus = ok_market.rename(columns={"second": "second_plus", "mid_at_second": "mid_plus"})
    plus["second"] = plus["second_plus"] - 10
    joined = joined.merge(plus[["match_id", "second", "mid_plus"]], on=["match_id", "second"], how="left")
    diff0 = (joined["market_p_radiant"] - joined["mid_at_second"]).abs().to_numpy(dtype=np.float64)
    diff10 = (joined["market_p_radiant"] - joined["mid_plus"]).abs().dropna().to_numpy(dtype=np.float64)
    report["validation_abs_mid_error_vs_same_second"] = pct(diff0)
    report["validation_abs_mid_error_vs_second_plus_10"] = pct(diff10)
    frame_vs_boundary = validation.merge(
        walls.rename(columns={"state_ts_us": "boundary_us"}), on=["match_id", "second"], how="inner"
    )
    age = (frame_vs_boundary["boundary_us"] - frame_vs_boundary["state_ts_us"]) / 1_000_000
    report["frame_wall_before_second_boundary_s"] = pct(age.to_numpy(dtype=np.float64))

    # Kill reaction on the livestats clock: signed so a blue-side kill is positive.
    series: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    ok_sorted = ok_market.sort_values(["match_id", "second"])
    for match_id, group in ok_sorted.groupby("match_id", sort=False):
        series[int(match_id)] = (
            group["second"].to_numpy(dtype=np.int64),
            group["mid_at_second"].to_numpy(dtype=np.float64),
            np.empty(0),
        )
    death_rows = validation.sort_values(["match_id", "second"])
    horizons = (4, 5, 8, 10, 30)
    signed: dict[int, list[float]] = {h: [] for h in horizons}
    raw: dict[int, list[float]] = {h: [] for h in horizons}
    kill_count = 0
    for match_id, group in death_rows.groupby("match_id", sort=False):
        packed = series.get(int(match_id))
        if packed is None:
            continue
        seconds, mids, _ = packed
        prev_r = None
        prev_d = None
        for second, deaths_r, deaths_d, mid in zip(
            group["second"].to_numpy(dtype=np.int64),
            group["deaths_radiant"].to_numpy(dtype=np.int64),
            group["deaths_dire"].to_numpy(dtype=np.int64),
            group["market_p_radiant"].to_numpy(dtype=np.float64),
            strict=True,
        ):
            if prev_r is None or prev_d is None:
                prev_r = int(deaths_r)
                prev_d = int(deaths_d)
                continue
            dr = int(deaths_r) - prev_r
            dd = int(deaths_d) - prev_d
            prev_r = int(deaths_r)
            prev_d = int(deaths_d)
            if dr <= 0 and dd <= 0:
                continue
            if dr > 0 and dd > 0:
                continue
            kill_count += 1
            direction = 1.0 if dd > 0 else -1.0
            for horizon in horizons:
                later = asof_price(seconds, mids, int(second) + horizon)
                if later is None:
                    continue
                move = later - float(mid)
                raw[horizon].append(move)
                signed[horizon].append(direction * move)
    report["validation_kill_rows"] = kill_count
    report["kill_signed_move"] = {str(h): pct(np.asarray(signed[h], dtype=np.float64)) for h in horizons}
    report["kill_raw_move"] = {str(h): pct(np.asarray(raw[h], dtype=np.float64)) for h in horizons}

    # All labeled seconds: how much of the 300s label is already in the first 8/10s.
    labeled = joined.dropna(subset=["mid_plus", "signal_market_p_radiant_300s"])
    label = (
        labeled["signal_market_p_radiant_300s"] - labeled["market_p_radiant"]
    ).to_numpy(dtype=np.float64)
    first10 = (labeled["mid_plus"] - labeled["market_p_radiant"]).to_numpy(dtype=np.float64)
    report["label_300_minus_current"] = pct(label)
    report["mid_plus10_minus_current"] = pct(first10)
    report["share_of_abs_label_inside_10s"] = float(np.abs(first10).sum() / np.abs(label).sum())

    split = pd.read_parquet(LOL_SPLIT_PATH, columns=["match_id", "split"])
    train_ids = set(split.loc[split["split"] == "train", "match_id"].astype(np.int64))
    # Training file is large; check 8 files of row groups via a filtered read of two columns on a sample of matches.
    sample_ids = list(train_ids)[:40]
    training = pd.read_parquet(
        LOL_DATASETS_DIR / "training.parquet",
        columns=["match_id", "second", "market_p_radiant"],
        filters=[("match_id", "in", sample_ids)],
    )
    training["match_id"] = training["match_id"].astype(np.int64)
    training["second"] = training["second"].astype(np.int64)
    train_joined = training.merge(ok_market, on=["match_id", "second"], how="inner")
    train_joined = train_joined.merge(plus[["match_id", "second", "mid_plus"]], on=["match_id", "second"], how="left")
    train_diff0 = (train_joined["market_p_radiant"] - train_joined["mid_at_second"]).abs().to_numpy(dtype=np.float64)
    train_diff10 = (
        (train_joined["market_p_radiant"] - train_joined["mid_plus"]).abs().dropna().to_numpy(dtype=np.float64)
    )
    report["training_sample_matches"] = int(training["match_id"].nunique())
    report["training_abs_mid_error_vs_same_second"] = pct(train_diff0)
    report["training_abs_mid_error_vs_second_plus_10"] = pct(train_diff10)

    fills = pd.read_parquet(BT / "fills.parquet")
    report["fill_columns"] = list(fills.columns)
    fills["match_id"] = fills["match_id"].astype(np.int64)
    fills["cohort"] = np.where(fills["match_id"].isin(schedule_match_ids), "schedule", "grid_v1")
    buys = fills[fills["side"] == "BUY"].copy()
    report["buy_fills"] = int(len(buys))
    report["buy_fills_by_cohort"] = {k: int(v) for k, v in buys["cohort"].value_counts().to_dict().items()}
    report["maker_share_buys"] = float(buys["is_maker"].mean()) if "is_maker" in buys.columns else None

    mid_series: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    ok_full = market[market["market_status"] == "ok"].sort_values(["match_id", "state_ts_us"])
    for match_id, group in ok_full.groupby("match_id", sort=False):
        mid_series[int(match_id)] = (
            group["state_ts_us"].to_numpy(dtype=np.int64),
            group["market_p_radiant"].to_numpy(dtype=np.float64),
        )

    def token_mid_at(match_id: int, token_index: int, target_us: int) -> float | None:
        packed = mid_series.get(match_id)
        if packed is None:
            return None
        stamps, mids = packed
        idx = int(np.searchsorted(stamps, target_us, side="right") - 1)
        if idx < 0:
            return None
        radiant = float(mids[idx])
        if token_index == radiant_index.get(match_id):
            return radiant
        return 1.0 - radiant

    horizons_s = (0, 4, 8, 10, 30)
    reconstructed: dict[str, dict[str, float | None]] = {}
    check_errors: list[float] = []
    for cohort, cohort_fills in buys.groupby("cohort"):
        bucket: dict[str, float | None] = {}
        qty = cohort_fills["quantity"].to_numpy(dtype=np.float64)
        stored = cohort_fills["markout_30s"].to_numpy(dtype=np.float64)
        bucket["stored_buy_markout_30_weighted"] = weighted_markout(stored, qty)
        bucket["n"] = int(len(cohort_fills))
        bucket["qty"] = float(qty.sum())
        for horizon in horizons_s:
            marks = np.empty(len(cohort_fills), dtype=np.float64)
            keep = np.zeros(len(cohort_fills), dtype=bool)
            for i, fill in enumerate(cohort_fills.itertuples(index=False)):
                target_us = int(fill.ts_ns // 1000 + horizon * 1_000_000)
                mid = token_mid_at(int(fill.match_id), int(fill.token_index), target_us)
                if mid is None:
                    continue
                marks[i] = mid - float(fill.price)
                keep[i] = True
                if horizon == 30 and fill.reference_source_30s == "mid":
                    check_errors.append(mid - float(fill.reference_30s))
            bucket[f"markout_{horizon}s_weighted"] = weighted_markout(marks[keep], qty[keep])
            bucket[f"markout_{horizon}s_dollars"] = float(np.sum(marks[keep] * qty[keep]))
            bucket[f"markout_{horizon}s_n"] = int(keep.sum())
        reconstructed[str(cohort)] = bucket
    report["buy_markout_by_cohort"] = reconstructed
    report["reconstructed_vs_stored_reference_30_error"] = pct(np.asarray(check_errors, dtype=np.float64))

    sells = fills[fills["side"] == "SELL"]
    sell_out: dict[str, dict[str, float | None]] = {}
    for cohort, cohort_fills in sells.groupby("cohort"):
        qty = cohort_fills["quantity"].to_numpy(dtype=np.float64)
        sell_out[str(cohort)] = {
            "n": int(len(cohort_fills)),
            "stored_sell_markout_30_weighted": weighted_markout(
                cohort_fills["markout_30s"].to_numpy(dtype=np.float64), qty
            ),
        }
    report["sell_markout_30_by_cohort"] = sell_out

    if "signal_age_seconds" in buys.columns:
        report["buy_signal_age"] = {
            str(cohort): pct(group["signal_age_seconds"].to_numpy(dtype=np.float64))
            for cohort, group in buys.groupby("cohort")
        }
    if "book_p_radiant" in buys.columns and "dataset_market_p" in buys.columns:
        gap = (buys["book_p_radiant"] - buys["dataset_market_p"]).to_numpy(dtype=np.float64)
        report["buy_book_minus_anchor"] = pct(gap)
        report["buy_book_minus_anchor_by_cohort"] = {
            str(cohort): pct((group["book_p_radiant"] - group["dataset_market_p"]).to_numpy(dtype=np.float64))
            for cohort, group in buys.groupby("cohort")
        }

    summary = json.loads((BT / "summary.json").read_text())
    arms = summary.get("arms")
    maker = arms[0] if isinstance(arms, list) and arms else {}
    report["summary_markout"] = maker.get("markout")
    report["summary_net_pnl"] = maker.get("net_pnl")
    report["summary_pnl_before_rebate"] = maker.get("pnl_before_rebate")
    report["summary_matches"] = maker.get("matches")
    report["summary_buy_fills"] = maker.get("buy_fills")

    OUT.write_text(json.dumps(report, indent=2))
    print("wrote", OUT)
    print("cohorts", report["result_maps"])
    print("pnl", report.get("pnl_by_cohort"))
    print("offset", report["schedule_offset_received_minus_livestats_wall"])
    print("internal", report["schedule_internal_lag_received_minus_horn_plus_second"])
    print("spawn", report["schedule_spawn_horn_minus_livestats_second0"])
    print("asof0", report["validation_abs_mid_error_vs_same_second"])
    print("asof10", report["validation_abs_mid_error_vs_second_plus_10"])
    print("kills", report["kill_signed_move"])
    print("buys", report["buy_markout_by_cohort"])


if __name__ == "__main__":
    main()
