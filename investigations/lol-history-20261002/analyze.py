import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from backtest.signals import SignalTiming, select_cadence_rows
from shared.constants.strategy import GRID_FEED_STALE_SECONDS
from shared.utils.dota_features import (
    DOTA_HISTORY_FIELDS,
    attach_catalog_features,
    history_feature_block,
)

ROOT = Path(__file__).resolve().parents[3] / "esports-trader"
OUT = Path(__file__).resolve().parent
DATA = ROOT / "data/lol/processed/datasets"


def calculate_metrics(frame, delta):
    current = frame.market_p_radiant.to_numpy()
    target = frame.signal_market_p_radiant_300s.to_numpy()
    predicted = np.clip(current + delta, 0, 1)
    error = np.abs(target - predicted)
    signal = np.abs(delta) >= 0.02
    token_price = np.where(delta >= 0, current, 1 - current)
    entry = signal & (token_price >= 0.45) & (token_price <= 0.85)
    direction = np.where(delta >= 0, 1, -1)
    return {
        "rows": len(frame),
        "model_mae_cents": float(error.mean() * 100),
        "mae_gain_cents": float((np.abs(target - current) - error).mean() * 100),
        "directional_cents": float((direction * (target - current)).mean() * 100),
        "bias_cents": float((predicted - target).mean() * 100),
        "signal_fraction": float(signal.mean()),
        "entry_fraction": float(entry.mean()),
        "entry_directional_cents": float(
            (direction[entry] * (target - current)[entry]).mean() * 100
        ),
    }


def bootstrap_events(frame, differences):
    sums = (
        pd.DataFrame({"event": frame.event_id, "difference": differences})
        .groupby("event")
        .difference.agg(["sum", "count"])
    )
    rng = np.random.default_rng(491)
    samples = []
    for _ in range(10000):
        draw = rng.integers(0, len(sums), len(sums))
        samples.append(sums["sum"].to_numpy()[draw].sum() / sums["count"].to_numpy()[draw].sum())
    return {
        "estimate": float(np.mean(differences)),
        "ci95": np.quantile(samples, [0.025, 0.975]).tolist(),
        "events": len(sums),
    }


def main():
    report = {"backtests": {}}
    for name in ["validation-20260930-s3-sh6", "rebuild-20260930", "cat77", "cat77lv6"]:
        run = (
            ROOT / "data/backtests/lol_maker" / ("validation_join_delta02_x015_cut480_p45_" + name)
        )
        seeds = json.loads((run / "seeds.json").read_text())
        arms = [
            json.loads((run / f"seed{seed}/summary.json").read_text())["arms"][0]
            for seed in range(3)
        ]
        report["backtests"][name] = {
            "means": seeds["mean"],
            "sd": seeds["sd"],
            "buy_turnover": float(np.mean([arm["buy_turnover"] for arm in arms])),
            "pnl_per_share_cents": float(
                np.mean([arm["pnl_per_bought_share"] for arm in arms]) * 100
            ),
            "pnl_per_share_rebate_cents": float(
                np.mean([arm["pnl_per_bought_share_with_rebate"] for arm in arms]) * 100
            ),
        }
    print("Loading early validation and tape", flush=True)
    valid = pd.read_parquet(DATA / "validation.parquet", filters=[("second", "<", 480)])
    valid = valid.loc[valid.signal_market_p_radiant_300s.notna()].reset_index(drop=True)
    tape = pd.read_parquet(DATA / "game_features.parquet", filters=[("game_second", "<", 480)])
    features = attach_catalog_features(
        valid, tape, key_seconds=valid.second, max_pivot_gap_seconds=GRID_FEED_STALE_SECONDS
    )
    np.save(OUT / "early_keys.npy", valid[["match_id", "second"]].to_numpy(dtype=np.int64))
    predictions = {}
    importances = []
    for name, path in [
        ("live_old", ROOT / "data/lol/models/archive/research/20260927T155450Z"),
        ("old", ROOT / "data/lol/models/archive/research/20260930T191131Z"),
        ("new", ROOT / "data/lol/models/research"),
    ]:
        meta = json.loads((path / "model.json").read_text())
        members = [lgb.Booster(model_file=str(path / file)) for file in meta["members"]]
        x = features[meta["features"]]
        delta = np.mean([member.predict(x, num_threads=1) for member in members], axis=0)
        predictions[name] = delta
        np.save(OUT / f"early_delta_{name}.npy", delta)
        report[name] = calculate_metrics(valid, delta)
        bucket_metrics = []
        for bucket in range(0, 480, 60):
            mask = (valid.second >= bucket) & (valid.second < bucket + 60)
            bucket_metrics.append(
                {"bucket": bucket, **calculate_metrics(valid.loc[mask], delta[mask])}
            )
        pd.DataFrame(bucket_metrics).to_csv(OUT / f"minutes_{name}.csv", index=False)
        if name == "new":
            gain = np.mean([m.feature_importance(importance_type="gain") for m in members], axis=0)
            splits = np.mean(
                [m.feature_importance(importance_type="split") for m in members], axis=0
            )
            for field, g, s in zip(meta["features"], gain, splits, strict=True):
                importances.append(
                    {"feature": field, "gain_fraction": float(g / gain.sum()), "splits": float(s)}
                )
    pd.DataFrame(importances).sort_values("gain_fraction", ascending=False).to_csv(
        OUT / "feature_importance.csv", index=False
    )
    current = valid.market_p_radiant.to_numpy()
    target = valid.signal_market_p_radiant_300s.to_numpy()
    old_error = np.abs(np.clip(current + predictions["old"], 0, 1) - target)
    new_error = np.abs(np.clip(current + predictions["new"], 0, 1) - target)
    report["paired_mae_improvement_cents"] = bootstrap_events(valid, (old_error - new_error) * 100)
    old_dir = np.where(predictions["old"] >= 0, 1, -1) * (target - current)
    new_dir = np.where(predictions["new"] >= 0, 1, -1) * (target - current)
    report["paired_directional_improvement_cents"] = bootstrap_events(
        valid, (new_dir - old_dir) * 100
    )
    records = []
    for bucket in range(0, 480, 60):
        group = features.loc[(features.second >= bucket) & (features.second < bucket + 60)]
        for lag in range(1, 6):
            field = f"game_change_{lag}m_radiant_nw_adv"
            records.append(
                {
                    "bucket": bucket,
                    "lag": lag,
                    "missing_fraction": float(group[field].isna().mean()),
                    "zero_fraction_of_present": float((group[field].dropna() == 0).mean())
                    if group[field].notna().any()
                    else None,
                }
            )
    pd.DataFrame(records).to_csv(OUT / "history_coverage.csv", index=False)
    (OUT / "diagnostics.json").write_text(json.dumps(report, indent=2))
    print("Comparing synthetic cadence history to dense training tape", flush=True)
    sampled_ids = valid.match_id.drop_duplicates().iloc[::20].to_list()
    cadence_records = []
    for seed in range(3):
        sparse = select_cadence_rows(
            valid.loc[valid.match_id.isin(sampled_ids)],
            game="lol",
            timing=SignalTiming(seed, GRID_FEED_STALE_SECONDS),
            lag_seconds=0,
        )
        sparse_groups = dict(tuple(sparse.groupby("match_id", sort=False)))
        for match_id, group in features.loc[features.match_id.isin(sampled_ids)].groupby(
            "match_id", sort=False
        ):
            ticks = sparse_groups.get(match_id)
            if ticks is None:
                continue
            decisions = group.loc[group.second.isin(ticks.second)]
            block = history_feature_block(
                ticks.second.to_numpy(dtype=np.int64),
                ticks[list(DOTA_HISTORY_FIELDS)].to_numpy(dtype=np.float64),
                decisions.second.to_numpy(dtype=np.int64),
                decisions[list(DOTA_HISTORY_FIELDS)].to_numpy(dtype=np.float64),
                GRID_FEED_STALE_SECONDS,
            )
            field = "game_change_1m_radiant_nw_adv"
            a = decisions[field].to_numpy()
            b = block[field]
            eligible = decisions.second.to_numpy() >= 60
            present = eligible & np.isfinite(a) & np.isfinite(b)
            cadence_records.append(
                {
                    "seed": seed,
                    "match_id": int(match_id),
                    "post60": int(eligible.sum()),
                    "dense_present": int((eligible & np.isfinite(a)).sum()),
                    "sparse_present": int((eligible & np.isfinite(b)).sum()),
                    "paired": int(present.sum()),
                    "abs_gold_error_sum": float(np.abs(a[present] - b[present]).sum()),
                    "differing": int((np.abs(a[present] - b[present]) > 0).sum()),
                }
            )
    pd.DataFrame(cadence_records).to_csv(OUT / "cadence_history.csv", index=False)
    (OUT / "diagnostics.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "backtests"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
