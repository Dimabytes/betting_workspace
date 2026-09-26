from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from shared.constants.dataset import MODEL_START_SECOND, TRAIN_END_SECOND_EXCLUSIVE, TRAIN_LAG_SECONDS
from shared.utils.gbm import FEATURE_COLUMNS, load_predictor, predict_future_prices
from train_model.train_model import lagged_source_features

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23")
W = R / "work/train-luna"


def make_metrics(frame: pd.DataFrame, future_col: str, current_col: str, pred_col: str) -> dict[str, float | int]:
    current = frame[current_col].to_numpy(dtype=np.float64)
    future = frame[future_col].to_numpy(dtype=np.float64)
    predicted = frame[pred_col].to_numpy(dtype=np.float64)
    errors_no_move = np.abs(future - current)
    errors_model = np.abs(future - predicted)
    model_delta = predicted - current
    actual_delta = future - current
    direction = np.where(model_delta >= 0, 1.0, -1.0)
    directional = direction * actual_delta
    moved = actual_delta != 0.0
    direction_correct = (np.sign(model_delta[moved]) == np.sign(actual_delta[moved])).mean() if moved.any() else np.nan
    return {
        "rows": len(frame),
        "maps": int(frame["match_id"].nunique()),
        "no_move_mae_c": float(errors_no_move.mean() * 100),
        "model_mae_c": float(errors_model.mean() * 100),
        "gain_c": float((errors_no_move - errors_model).mean() * 100),
        "directional_markout_c": float(directional.mean() * 100),
        "direction_accuracy_nonflat": float(direction_correct),
        "flat_label_pct": float((actual_delta == 0.0).mean() * 100),
    }


def report_groups(frame: pd.DataFrame, by: str, filename: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for label, group in frame.groupby(by, dropna=False, observed=True, sort=True):
        metric = make_metrics(group, "future", "current", "predicted")
        rows.append({by: str(label), **metric})
    result = pd.DataFrame(rows)
    result.to_csv(W / filename, index=False)
    return result


def main() -> None:
    data_dir = E / "data/lol/processed/datasets"
    val_cols = list(dict.fromkeys(["match_id", "event_id", "start_time", "second", *FEATURE_COLUMNS, "signal_market_p_radiant_300s"]))
    val = pd.read_parquet(data_dir / "validation.parquet", columns=val_cols)
    val = val.loc[
        val["second"].between(0, 540)
        & val["signal_market_p_radiant_300s"].notna()
    ].copy()
    val["current"] = val["market_p_radiant"]
    val["future"] = val["signal_market_p_radiant_300s"]
    X = val[FEATURE_COLUMNS]

    current_predictor = load_predictor(E / "data/lol/models/research")
    old_predictor = load_predictor(E / "data/lol/models/archive/research/20260915T210420Z")
    post_fix_predictor = load_predictor(E / "data/lol/models/archive/research/20260919T112924Z")
    val["predicted"] = predict_future_prices(
        current_predictor, X, val["current"].to_numpy(dtype=np.float64)
    )
    val["predicted_old_model_same_rows"] = predict_future_prices(
        old_predictor, X, val["current"].to_numpy(dtype=np.float64)
    )
    val["predicted_post_fix_model_same_rows"] = predict_future_prices(
        post_fix_predictor, X, val["current"].to_numpy(dtype=np.float64)
    )

    links = pd.read_parquet(
        E / "data/lol/processed/lolesports_links/links.parquet",
        columns=["esports_game_id", "event_id", "market_id", "condition_id", "game_number"],
    )
    markets = pd.read_parquet(
        E / "data/lol/processed/universe/markets.parquet",
        columns=["market_id", "condition_id", "league"],
    )
    links = links.merge(markets, on=["market_id", "condition_id"], how="left", validate="one_to_one")
    links["esports_game_id"] = links["esports_game_id"].astype("int64")
    aliases = json.loads((E / "config/lol_league_whitelist.json").read_text())
    alias_map = aliases["aliases"]
    whitelisted_leagues = set(aliases["leagues"])
    no_live_feed_leagues = set(aliases["no_live_feed"])
    links["league_norm"] = links["league"].map(lambda x: alias_map.get(x, x) if pd.notna(x) else "unknown")
    links["league_norm"] = links["league_norm"].fillna("unknown")
    links["no_live_feed"] = links["league_norm"].isin(no_live_feed_leagues)
    links["live_whitelist"] = links["league_norm"].isin(whitelisted_leagues)
    links["feed_class"] = np.select(
        [links["no_live_feed"], links["live_whitelist"]],
        ["whitelisted_no_live_feed", "whitelisted_live_feed"],
        default="not_whitelisted_or_unknown",
    )
    val = val.merge(
        links[["esports_game_id", "league", "league_norm", "no_live_feed", "feed_class"]],
        left_on="match_id",
        right_on="esports_game_id",
        how="left",
        validate="many_to_one",
    )
    val["league_norm"] = val["league_norm"].fillna("unknown")
    val["feed_class"] = val["feed_class"].fillna("not_whitelisted_or_unknown")
    val["month"] = pd.to_datetime(val["start_time"], unit="s", utc=True).dt.strftime("%Y-%m")
    val["game_second_bucket"] = (val["second"] // 60 * 60).astype(int).astype(str) + "-" + (val["second"] // 60 * 60 + 59).astype(int).astype(str)
    abs_edge_cents = (val["predicted"] - val["current"]).abs() * 100
    val["abs_pred_bucket"] = pd.cut(
        abs_edge_cents,
        bins=[-0.000001, 0.5, 1.0, 2.0, 5.0, np.inf],
        labels=["<0.5c", "0.5-1c", "1-2c", "2-5c", ">=5c"],
        right=False,
    )

    split = pd.read_parquet(data_dir / "split.parquet")
    audit = pd.read_parquet(data_dir / "audit.parquet", columns=["match_id", "pause_count", "pause_seconds", "included", "split"])
    current_meta = json.loads((E / "data/lol/models/research/model.json").read_text())
    old_meta = json.loads((E / "data/lol/models/archive/research/20260915T210420Z/model.json").read_text())

    print("LOL OVERALL CURRENT RESEARCH ON CURRENT LABELED 0..540 ROWS")
    val["predicted"] = val["predicted"]
    print(make_metrics(val, "future", "current", "predicted"))
    old_eval = val.copy()
    old_eval["predicted"] = old_eval["predicted_old_model_same_rows"]
    print("LOL ARCHIVED 20260915 RESEARCH ON THE SAME CURRENT ROWS/LABELS")
    print(make_metrics(old_eval, "future", "current", "predicted"))
    post_fix_eval = val.copy()
    post_fix_eval["predicted"] = post_fix_eval["predicted_post_fix_model_same_rows"]
    print("LOL ARCHIVED 20260919 POST-332 RESEARCH ON THE SAME CURRENT ROWS/LABELS")
    print(make_metrics(post_fix_eval, "future", "current", "predicted"))
    print("LOL PER MONTH")
    print(report_groups(val, "month", "lol_by_month.csv").to_string(index=False))
    print("LOL PER LEAGUE (canonical aliases from live whitelist)")
    print(report_groups(val, "league_norm", "lol_by_league.csv").to_string(index=False))
    print("LOL PER LIVE/NO-LIVE FEED")
    print(report_groups(val, "no_live_feed", "lol_by_feed_eligibility.csv").to_string(index=False))
    print("LOL PER EXACT LIVE WHITELIST STATUS")
    print(report_groups(val, "feed_class", "lol_by_whitelist_status.csv").to_string(index=False))
    print("LOL PER GAME SECOND BUCKET")
    print(report_groups(val, "game_second_bucket", "lol_by_second.csv").to_string(index=False))
    print("LOL PER ABS(PREDICTED EDGE) BUCKET")
    print(report_groups(val, "abs_pred_bucket", "lol_by_abs_pred.csv").to_string(index=False))

    print("LOL DATASET MAP/ROW/SPLIT / OVERLAP CHECKS")
    tr = pd.read_parquet(data_dir / "training.parquet", columns=["match_id", "event_id", "second", "market_p_radiant", "signal_market_p_radiant_300s"])
    train_model_rows = tr.loc[tr["second"].between(0, 540) & tr["signal_market_p_radiant_300s"].notna()]
    val_all = pd.read_parquet(data_dir / "validation.parquet", columns=["match_id", "event_id", "second", "market_p_radiant", "signal_market_p_radiant_300s"])
    val_model_rows = val_all.loc[val_all["second"].between(0, 540) & val_all["signal_market_p_radiant_300s"].notna()]
    event_overlap = set(train_model_rows.event_id.astype(str)) & set(val_model_rows.event_id.astype(str))
    map_overlap = set(train_model_rows.match_id) & set(val_model_rows.match_id)
    print({
        "split_rows": len(split),
        "split_unique_match": int(split.match_id.nunique()),
        "split_unique_event": int(split.event_id.nunique()),
        "split_split_counts": split.split.value_counts().to_dict(),
        "train_dataset_maps": int(train_model_rows.match_id.nunique()),
        "train_dataset_rows": len(train_model_rows),
        "validation_dataset_maps_with_labeled_0_540": int(val_model_rows.match_id.nunique()),
        "validation_dataset_rows_with_labeled_0_540": len(val_model_rows),
        "map_overlap": len(map_overlap),
        "event_overlap": len(event_overlap),
        "split_duplicate_match_id": int(split.match_id.duplicated().sum()),
        "split_match_with_multiple_events": int((split.groupby("match_id").event_id.nunique() > 1).sum()),
        "split_event_with_multiple_split_labels": int((split.groupby("event_id").split.nunique() > 1).sum()),
        "links_duplicate_esports_game_id": int(links.esports_game_id.duplicated().sum()),
        "links_duplicate_condition_id": int(links.condition_id.duplicated().sum()),
        "links_duplicate_event_game": int(links.duplicated(["event_id", "game_number"]).sum()),
        "val_match_missing_link": int(val_model_rows.match_id.isin(links.esports_game_id).eq(False).sum()),
    })
    backtest_results = pd.read_parquet(
        E / "data/backtests/lol_maker/LIVE/seed0/results.parquet",
        columns=["match_id", "model_name"],
    )
    validation_match_ids = set(split.loc[split["split"] == "validation", "match_id"])
    backtest_match_ids = set(backtest_results["match_id"])
    print("LIVE BACKTEST MAP/VALIDATION OVERLAP")
    print({
        "results_rows": len(backtest_results),
        "result_maps": len(backtest_match_ids),
        "validation_maps": len(validation_match_ids),
        "result_maps_in_validation": len(backtest_match_ids & validation_match_ids),
        "result_maps_not_in_validation": len(backtest_match_ids - validation_match_ids),
        "result_model_names": backtest_results.model_name.value_counts().to_dict(),
    })
    backtest_rows = val.loc[val["match_id"].isin(backtest_match_ids)].copy()
    print("LIVE BACKTEST MAPS EVALUATED AGAIN ON CURRENT VALIDATION LABELS")
    print(make_metrics(backtest_rows, "future", "current", "predicted"))
    print("LIVE BACKTEST SUBSET PER LEAGUE")
    print(report_groups(backtest_rows, "league_norm", "lol_backtest_subset_by_league.csv").to_string(index=False))
    split_summary = split.groupby("split").agg(
        maps=("match_id", "nunique"), events=("event_id", "nunique"),
        first_start=("start_time", "min"), last_start=("start_time", "max"),
        first_event_start=("event_start_time", "min"), last_event_start=("event_start_time", "max"),
    )
    print("split chronological ranges (unix seconds)")
    print(split_summary.to_string())
    print("by split event-start boundaries")
    print(split.groupby("split").event_start_time.agg(["min", "max"]).to_string())

    print("LOL LABEL STATS")
    for name, df in [("train", train_model_rows), ("validation", val_model_rows)]:
        y = df["signal_market_p_radiant_300s"].to_numpy() - df["market_p_radiant"].to_numpy()
        print(name, {
            "maps": int(df.match_id.nunique()), "rows": len(df),
            "flat_pct": float((y == 0.0).mean() * 100), "mean_delta_c": float(y.mean() * 100),
            "median_delta_c": float(np.median(y) * 100), "std_delta_c": float(y.std() * 100),
            "p01_p05_p25_p75_p95_p99_c": [float(x * 100) for x in np.quantile(y, [0.01, .05, .25, .75, .95, .99])],
            "abs_delta_lt_1c_pct": float((np.abs(y) < .01).mean() * 100),
            "abs_delta_ge_10c_pct": float((np.abs(y) >= .10).mean() * 100),
        })
        rows_by_second = df.groupby("second").size()
        all_map_seconds = df[["match_id", "second"]].drop_duplicates()
        print(name, "second row counts", {
            "0": int(rows_by_second.get(0, 0)), "1": int(rows_by_second.get(1, 0)),
            "60": int(rows_by_second.get(60, 0)), "480": int(rows_by_second.get(480, 0)),
            "539": int(rows_by_second.get(539, 0)), "540": int(rows_by_second.get(540, 0)),
            "maps": int(all_map_seconds.match_id.nunique()),
            "min_max_rows_per_map": [int(x) for x in df.groupby("match_id").size().agg(["min", "max"])],
            "map_rows_quantiles": {str(k): float(v) for k, v in df.groupby("match_id").size().quantile([0, .01, .10, .50, .90, .99, 1]).items()},
            "maps_under_100_rows": int((df.groupby("match_id").size() < 100).sum()),
            "maps_under_300_rows": int((df.groupby("match_id").size() < 300).sum()),
        })

    print("LOL 0..540 LABEL PRESENCE BY SECOND BUCKET")
    for name in ["training", "validation"]:
        raw_rows = pd.read_parquet(
            data_dir / f"{name}.parquet",
            columns=["match_id", "second", "signal_market_p_radiant_300s"],
        )
        raw_rows = raw_rows.loc[raw_rows["second"].between(0, 540)].copy()
        raw_rows["bucket"] = (raw_rows["second"] // 60 * 60).astype(int)
        raw_rows["labeled"] = raw_rows["signal_market_p_radiant_300s"].notna()
        coverage = raw_rows.groupby("bucket").agg(
            rows=("match_id", "size"), labeled=("labeled", "sum"), maps=("match_id", "nunique")
        )
        coverage["unlabeled"] = coverage["rows"] - coverage["labeled"]
        coverage["label_pct"] = coverage["labeled"] / coverage["rows"] * 100
        print(name, coverage.to_dict(orient="index"))

    print("LOL VALIDATION MODEL-WINDOW ROWS WHOSE T+300 WALL TIME EXCEEDS MAP END")
    full_val = pd.read_parquet(
        data_dir / "validation.parquet",
        columns=["match_id", "second", "state_ts_us", "signal_market_p_radiant_300s"],
    )
    full_val = full_val.loc[full_val["second"].between(0, 540)].copy()
    end_times = pd.read_parquet(
        data_dir / "backtest_audit.parquet", columns=["match_id", "game_ended_at_ts"]
    )
    full_val = full_val.merge(end_times, on="match_id", how="left", validate="many_to_one")
    full_val["t_plus_300_after_game_end"] = (
        full_val["state_ts_us"] + 300_000_000 > full_val["game_ended_at_ts"] * 1_000_000
    )
    full_val["labeled"] = full_val["signal_market_p_radiant_300s"].notna()
    end_stats = full_val.groupby("t_plus_300_after_game_end").agg(
        rows=("match_id", "size"), labeled=("labeled", "sum"), maps=("match_id", "nunique")
    )
    end_stats["label_pct"] = end_stats["labeled"] / end_stats["rows"] * 100
    print(end_stats.to_dict(orient="index"))

    print("LOL LINK LEAGUE JOIN")
    print({"linked_rows": len(links), "league_null_rows": int(links.league.isna().sum()), "league_top": links.league_norm.value_counts().head(25).to_dict()})
    audit["paused"] = audit["pause_count"] > 0
    print("LOL AUDIT PAUSE COUNTS BY SPLIT")
    print(audit.groupby("split").agg(maps=("match_id", "nunique"), paused_maps=("paused", "sum"), pause_count=("pause_count", "sum"), pause_seconds=("pause_seconds", "sum"), accepted=("included", "sum")).to_string())
    val_pauses = val.merge(audit[["match_id", "paused"]], on="match_id", how="left", validate="many_to_one")
    print("LOL METRICS BY AUDIT PAUSE STATUS")
    val_pauses["pause_status"] = val_pauses["paused"].fillna(False)
    print(report_groups(val_pauses, "pause_status", "lol_by_paused.csv").to_string(index=False))

    print("MODEL METADATA")
    print("current", {k: current_meta.get(k) for k in ["name", "train_matches", "validation_matches", "source_lag_seconds", "ensemble_arm", "ensemble_k", "member_trees", "metrics", "train_dataset_sha256", "validation_dataset_sha256"]})
    print("old", {k: old_meta.get(k) for k in ["name", "train_matches", "validation_matches", "source_lag_seconds", "ensemble_arm", "ensemble_k", "member_trees", "metrics", "train_dataset_sha256", "validation_dataset_sha256"]})

    print("DOTA RESEARCH ON VALIDATION PARQUET")
    dota_root = E / "data/new_processed/dataset"
    dota = pd.read_parquet(
        dota_root / "validation_dataset.parquet",
        columns=list(dict.fromkeys(["match_id", "start_time", "second", "market_status", *FEATURE_COLUMNS, "market_p_radiant", "signal_market_p_radiant_300s"])),
    )
    dota = dota.loc[
        (dota["second"] >= MODEL_START_SECOND)
        & (dota["second"] < TRAIN_END_SECOND_EXCLUSIVE)
        & (dota["market_status"] == "ok")
        & dota["signal_market_p_radiant_300s"].notna()
    ].copy()
    dota_X = lagged_source_features(dota, TRAIN_LAG_SECONDS, FEATURE_COLUMNS)
    dota_predictor = load_predictor(E / "data/new_model/research")
    dota["current"] = dota["market_p_radiant"]
    dota["future"] = dota["signal_market_p_radiant_300s"]
    dota["predicted"] = predict_future_prices(
        dota_predictor, dota_X, dota["current"].to_numpy(dtype=np.float64)
    )
    print("Dota canonical -60..599/market_status=ok", make_metrics(dota, "future", "current", "predicted"))
    dota_common = dota.loc[dota["second"].between(0, 540)].copy()
    print("Dota common 0..540/market_status=ok", make_metrics(dota_common, "future", "current", "predicted"))
    print("Dota second distinct values sampled", sorted(dota.second.unique())[:20], "... unique_count", dota.second.nunique())
    print("Dota map row distribution", dota.groupby("match_id").size().describe().to_dict())
    dota_train = pd.read_parquet(
        dota_root / "training_dataset.parquet",
        columns=["match_id", "second", "signal_market_p_radiant_300s"],
    )
    dota_train = dota_train.loc[
        (dota_train["second"] >= MODEL_START_SECOND)
        & (dota_train["second"] < TRAIN_END_SECOND_EXCLUSIVE)
        & dota_train["signal_market_p_radiant_300s"].notna()
    ]
    print("Dota training selected observations", {
        "rows": len(dota_train), "maps": int(dota_train.match_id.nunique()),
        "rows_per_map": dota_train.groupby("match_id").size().describe().to_dict(),
        "distinct_seconds": sorted(int(x) for x in dota_train.second.unique()),
    })
    print("Dota model metadata", json.loads((E / "data/new_model/research/model.json").read_text()))


if __name__ == "__main__":
    main()
