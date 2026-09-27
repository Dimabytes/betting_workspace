from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from shared.constants.dataset import MODEL_START_SECOND, TRAIN_END_SECOND_EXCLUSIVE, TRAIN_LAG_SECONDS
from shared.constants.paths import RESEARCH_MODEL_DIR, TRAINING_DATASET_PATH, VALIDATION_DATASET_PATH
from shared.utils.gbm import (
    FEATURE_COLUMNS,
    _member_match_ids,
    build_price_delta_labels,
    fit_ensemble_member_fixed_trees,
    group_rows_by_match_id,
    load_predictor,
    member_train_frame,
)


ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
WORK = ROOT / "work" / "train-luna"
E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
OUT = WORK / "future_book_small_fit.json"


def bootstrap_cluster_ci(event_ids: pd.Series, values: np.ndarray, *, seed: int = 1) -> list[float] | None:
    frame = pd.DataFrame({"event_id": event_ids.astype(str).to_numpy(), "value": values})
    grouped = frame.groupby("event_id", sort=False).value.agg(["sum", "count"])
    if grouped.empty:
        return None
    sums = grouped["sum"].to_numpy(dtype=np.float64)
    counts = grouped["count"].to_numpy(dtype=np.float64)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(sums), size=(2000, len(sums)))
    estimates = sums[indices].sum(axis=1) / counts[indices].sum(axis=1)
    return [float(np.percentile(estimates, 2.5)), float(np.percentile(estimates, 97.5))]


def metrics(frame: pd.DataFrame, predicted_delta: np.ndarray) -> dict[str, object]:
    current = frame.market_p_radiant.to_numpy(dtype=np.float64)
    target = frame.target_delta.to_numpy(dtype=np.float64)
    fair = np.clip(current + predicted_delta, 0.0, 1.0)
    effective_delta = fair - current
    gain_values = np.abs(target) - np.abs(target - effective_delta)
    direction = np.where(effective_delta >= 0.0, 1.0, -1.0)
    directional_values = direction * target
    large = np.abs(effective_delta) >= 0.05
    large_values: dict[str, object] | None = None
    if large.any():
        pred_abs = np.abs(effective_delta[large])
        target_abs = np.abs(target[large])
        large_values = {
            "rows": int(large.sum()),
            "maps": int(frame.loc[large, "match_id"].nunique()),
            "share_of_slice": float(large.mean()),
            "mean_abs_predicted_delta_cents": float(pred_abs.mean() * 100),
            "mean_abs_realized_delta_cents": float(target_abs.mean() * 100),
            "abs_scale_gap_pred_minus_realized_cents": float((pred_abs.mean() - target_abs.mean()) * 100),
            "direction_accuracy": float((direction[large] * target[large] > 0.0).mean()),
        }
    return {
        "rows": int(len(frame)),
        "maps": int(frame.match_id.nunique()),
        "no_move_mae_cents": float(np.abs(target).mean() * 100),
        "model_mae_cents": float(np.abs(target - effective_delta).mean() * 100),
        "mae_gain_cents": float(gain_values.mean() * 100),
        "model_bias_cents": float((effective_delta - target).mean() * 100),
        "directional_markout_cents": float(directional_values.mean() * 100),
        "large_abs_delta_calibration_ge_5c": large_values,
    }


def metric_differences(
    frame: pd.DataFrame, base_delta: np.ndarray, aug_delta: np.ndarray, *, large_mask: np.ndarray
) -> dict[str, object]:
    current = frame.market_p_radiant.to_numpy(dtype=np.float64)
    target = frame.target_delta.to_numpy(dtype=np.float64)
    base_eff = np.clip(current + base_delta, 0.0, 1.0) - current
    aug_eff = np.clip(current + aug_delta, 0.0, 1.0) - current
    base_gain = np.abs(target) - np.abs(target - base_eff)
    aug_gain = np.abs(target) - np.abs(target - aug_eff)
    gain_diff = aug_gain - base_gain
    large_abs_gap_diff = (np.abs(aug_eff[large_mask]) - np.abs(base_eff[large_mask]))
    return {
        "augmented_minus_base_mae_gain_cents": float(gain_diff.mean() * 100),
        "mae_gain_difference_event_cluster_95ci_cents": [
            float(value * 100)
            for value in bootstrap_cluster_ci(frame.event_id, gain_diff)
        ],
        "large_abs_delta_calibration_gap_change_cents": float(large_abs_gap_diff.mean() * 100),
        "large_abs_delta_calibration_gap_change_event_cluster_95ci_cents": [
            float(value * 100)
            for value in bootstrap_cluster_ci(frame.loc[large_mask, "event_id"], large_abs_gap_diff)
        ] if large_abs_gap_diff.size else None,
        "large_subset_rows_fixed_from_base_abs_delta_ge_5c": int(large_mask.sum()),
    }


def main() -> None:
    model_meta = json.loads((RESEARCH_MODEL_DIR / "model.json").read_text())
    tree_counts = [int(v) for v in model_meta["member_trees"]]
    train = pd.read_parquet(TRAINING_DATASET_PATH)
    train = train.loc[
        train.second.between(MODEL_START_SECOND, TRAIN_END_SECOND_EXCLUSIVE - 1)
        & train.signal_market_p_radiant_300s.notna()
    ].reset_index(drop=True)
    candidate_minute = pd.read_parquet(WORK / "minute_candidate_rows.parquet")
    drops = pd.read_parquet(WORK / "future_book_dropped_labels.parquet")
    base_ids = set(train.match_id.astype(int).unique())
    start_times = train[["match_id", "start_time"]].drop_duplicates("match_id").set_index("match_id").start_time
    extra = candidate_minute.loc[
        candidate_minute.split.eq("research_train")
        & candidate_minute.market_status.eq("ok")
        & candidate_minute.signal_market_p_radiant_300s.isna()
        & candidate_minute.match_id.isin(base_ids)
    ].merge(
        drops.loc[
            drops.split.eq("research_train"),
            ["match_id", "market_second", "relaxed_label", "future_status", "relaxed_offset_seconds"],
        ],
        on=["match_id", "market_second"],
        how="left",
        validate="one_to_one",
    )
    extra = extra.loc[extra.relaxed_label.notna()].copy()
    extra["start_time"] = extra.match_id.map(start_times)
    extra["signal_market_p_radiant_300s"] = extra.relaxed_label.astype(float)
    train_columns = list(train.columns)
    extra_train = extra[train_columns].copy()
    augmented_train = pd.concat([train, extra_train], ignore_index=True)
    if augmented_train.duplicated(["match_id", "second"]).any():
        raise ValueError("augmented training data contains duplicate (match_id, second) rows")

    validation_columns = list(
        dict.fromkeys(
            [
                "match_id",
                "event_id",
                "start_time",
                "second",
                "market_status",
                "market_p_radiant",
                "signal_market_p_radiant_300s",
                *FEATURE_COLUMNS,
            ]
        )
    )
    validation = pd.read_parquet(VALIDATION_DATASET_PATH, columns=validation_columns)
    validation = validation.loc[
        (validation.second >= MODEL_START_SECOND)
        & (validation.second < TRAIN_END_SECOND_EXCLUSIVE)
        & validation.market_status.eq("ok")
    ].copy()
    map_order = (
        validation[["match_id", "start_time"]]
        .drop_duplicates("match_id")
        .sort_values(["start_time", "match_id"])
        .match_id.to_numpy(dtype=np.int64)
    )
    heldout_ids = set(map_order[int(len(map_order) * 0.60) :].tolist())
    validation["target_delta"] = validation.signal_market_p_radiant_300s - validation.market_p_radiant
    validation["feature_second"] = validation.second - TRAIN_LAG_SECONDS
    validation["market_second"] = validation.second
    latest = validation.loc[validation.match_id.isin(heldout_ids)].copy().reset_index(drop=True)
    latest_kept = latest.loc[latest.signal_market_p_radiant_300s.notna()].copy().reset_index(drop=True)
    latest_all = latest.merge(
        drops.loc[
            drops.split.eq("validation"),
            ["match_id", "market_second", "relaxed_label", "relaxed_offset_seconds"],
        ],
        on=["match_id", "market_second"],
        how="left",
        validate="one_to_one",
    )
    latest_all["target_price"] = latest_all.signal_market_p_radiant_300s.fillna(latest_all.relaxed_label)
    latest_all = latest_all.loc[latest_all.target_price.notna()].copy().reset_index(drop=True)
    latest_all["target_delta"] = latest_all.target_price - latest_all.market_p_radiant

    features_kept = latest_kept[FEATURE_COLUMNS].copy()
    features_kept["second"] -= TRAIN_LAG_SECONDS
    features_all = latest_all[FEATURE_COLUMNS].copy()
    features_all["second"] -= TRAIN_LAG_SECONDS

    unique_ids = train.match_id.drop_duplicates().to_numpy(dtype=np.int64)
    grouped_base, ids_base = group_rows_by_match_id(train)
    grouped_aug, ids_aug = group_rows_by_match_id(augmented_train)
    if not np.array_equal(ids_base, ids_aug) or len(unique_ids) != len(ids_base):
        raise ValueError("base and augmented model map order changed")

    paired_indices = [0, 2, 4, 6, 8]
    baseline_predictions: list[np.ndarray] = []
    augmented_predictions: list[np.ndarray] = []
    base_all_predictions: list[np.ndarray] = []
    aug_all_predictions: list[np.ndarray] = []
    member_notes: list[dict[str, object]] = []
    for member_index in paired_indices:
        base_member = member_train_frame(grouped_base, ids_base, member_index)
        aug_member = member_train_frame(grouped_aug, ids_aug, member_index)
        base_booster = fit_ensemble_member_fixed_trees(
            base_member[FEATURE_COLUMNS],
            build_price_delta_labels(base_member),
            tree_counts[member_index],
        )
        aug_booster = fit_ensemble_member_fixed_trees(
            aug_member[FEATURE_COLUMNS],
            build_price_delta_labels(aug_member),
            tree_counts[member_index],
        )
        baseline_predictions.append(base_booster.predict(features_kept).astype(np.float64))
        augmented_predictions.append(aug_booster.predict(features_kept).astype(np.float64))
        base_all_predictions.append(base_booster.predict(features_all).astype(np.float64))
        aug_all_predictions.append(aug_booster.predict(features_all).astype(np.float64))
        member_notes.append(
            {
                "member_index": member_index,
                "tree_count": tree_counts[member_index],
                "base_rows": int(len(base_member)),
                "augmented_rows": int(len(aug_member)),
                "sampled_maps": int(base_member.match_id.nunique()),
            }
        )
        print("FIT_MEMBER", member_index, "base", len(base_member), "augmented", len(aug_member), flush=True)

    # Re-run the paired five-member models on the extended target, using the same fitted boosters.
    # The member objects are intentionally not written to disk; see model arrays assembled below.
    # Fit outputs for the two validation frames in the loop above are kept only for labeled rows,
    # so recompute model predictions directly from the retained serialized booster strings is avoided
    # by fitting the small pairs once more on latest_all after metrics below. (This code path keeps the
    # main training memory low and leaves exact model execution visible in the script.)
    baseline_delta_kept = np.mean(np.stack(baseline_predictions), axis=0)
    augmented_delta_kept = np.mean(np.stack(augmented_predictions), axis=0)
    baseline_kept_stats = metrics(latest_kept, baseline_delta_kept)
    augmented_kept_stats = metrics(latest_kept, augmented_delta_kept)
    large_mask = np.abs(np.clip(latest_kept.market_p_radiant.to_numpy() + baseline_delta_kept, 0, 1) - latest_kept.market_p_radiant.to_numpy()) >= 0.05
    paired_kept = metric_differences(latest_kept, baseline_delta_kept, augmented_delta_kept, large_mask=large_mask)

    baseline_delta_all = np.mean(np.stack(base_all_predictions), axis=0)
    augmented_delta_all = np.mean(np.stack(aug_all_predictions), axis=0)
    baseline_all_stats = metrics(latest_all, baseline_delta_all)
    augmented_all_stats = metrics(latest_all, augmented_delta_all)
    large_all_mask = np.abs(
        np.clip(latest_all.market_p_radiant.to_numpy() + baseline_delta_all, 0, 1)
        - latest_all.market_p_radiant.to_numpy()
    ) >= 0.05
    paired_all = metric_differences(
        latest_all, baseline_delta_all, augmented_delta_all, large_mask=large_all_mask
    )

    # Compare the published 10-member model on kept labels for an anchor against the paired subset fits.
    research = load_predictor(RESEARCH_MODEL_DIR)
    published_delta_kept = research.predict(features_kept).astype(np.float64)
    published_kept_stats = metrics(latest_kept, published_delta_kept)

    result = {
        "method": {
            "training_rows_current": int(len(train)),
            "training_maps_current": int(train.match_id.nunique()),
            "relaxed_training_rows_added": int(len(extra_train)),
            "relaxed_training_maps_added_to": int(extra_train.match_id.nunique()),
            "training_dropped_rows_with_relaxed_label_in_included_maps": int(len(extra_train)),
            "training_dropped_rows_total": int(
                len(pd.read_parquet(WORK / "future_book_dropped_labels.parquet").query("split == 'research_train'"))
            ),
            "paired_member_indices": paired_indices,
            "published_member_tree_counts": tree_counts,
            "latest_validation_maps": int(len(heldout_ids)),
            "latest_validation_kept_rows": int(len(latest_kept)),
            "latest_validation_current_ok_rows_with_relaxed_target": int(len(latest_all)),
            "large_prediction_threshold": "absolute clipped predicted delta >= 5 cents",
            "large_calibration_subset": "fixed per validation slice using the paired base fit; same rows scored by augmented fit",
            "relaxed_targets": "same-target relaxed midpoint for wide rows; first OK target in next 0-30 seconds for stale/missing rows",
        },
        "latest40_official_kept_labels": {
            "paired_base_fit": baseline_kept_stats,
            "paired_relaxed_train_fit": augmented_kept_stats,
            "paired_change": paired_kept,
            "published_research_10_member": published_kept_stats,
        },
        "latest40_extended_relaxed_labels": {
            "paired_base_fit": baseline_all_stats,
            "paired_relaxed_train_fit": augmented_all_stats,
            "paired_change": paired_all,
        },
        "member_training": member_notes,
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True))
    print("RESULT_JSON", OUT)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
