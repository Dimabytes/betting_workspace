from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from shared.constants.dataset import MODEL_START_SECOND, TRAIN_END_SECOND_EXCLUSIVE, TRAIN_LAG_SECONDS
from shared.constants.paths import MATCH_CATALOG_PATH, RESEARCH_MODEL_DIR, VALIDATION_DATASET_PATH
from shared.utils.gbm import FEATURE_COLUMNS, load_predictor


ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
OUT = ROOT / "work" / "train-luna" / "horn_exclusion_metrics.json"


def bootstrap_metric_delta(
    frame: pd.DataFrame,
    contribution: np.ndarray,
    eligible: np.ndarray,
    affected: np.ndarray,
    *,
    seed: int = 20260810,
    replicates: int = 2000,
) -> dict[str, object]:
    groups = pd.DataFrame(
        {
            "event_id": frame.event_id.astype(str).to_numpy(),
            "contribution": contribution,
            "eligible": eligible.astype(np.int64),
            "affected": affected.astype(np.int64),
        }
    ).groupby("event_id", sort=False).agg(
        contribution=("contribution", "sum"),
        eligible=("eligible", "sum"),
        affected=("affected", "sum"),
    )
    total_num = groups.contribution.to_numpy(dtype=np.float64)
    total_den = groups.eligible.to_numpy(dtype=np.float64)
    affected_den = groups.affected.to_numpy(dtype=np.float64)
    clean_num = total_num  # subtract the affected-row numerator below
    affected_num_frame = pd.DataFrame(
        {
            "event_id": frame.event_id.astype(str).to_numpy(),
            "contribution": contribution * affected,
        }
    ).groupby("event_id", sort=False).contribution.sum()
    affected_num = affected_num_frame.reindex(groups.index, fill_value=0.0).to_numpy(dtype=np.float64)
    clean_num = total_num - affected_num
    clean_den = total_den - affected_den
    rng = np.random.default_rng(seed)
    selected = rng.integers(0, len(groups), size=(replicates, len(groups)))
    full_estimates = total_num[selected].sum(axis=1) / total_den[selected].sum(axis=1)
    clean_counts = clean_den[selected].sum(axis=1)
    clean_estimates = clean_num[selected].sum(axis=1) / clean_counts
    delta = clean_estimates - full_estimates
    lo, hi = np.percentile(delta, [2.5, 97.5])
    return {
        "clean_minus_full_95ci_cents": [float(lo * 100), float(hi * 100)],
        "replicates": int(replicates),
        "event_clusters": int(len(groups)),
        "clean_maps_with_targets": int(frame.loc[~affected, "match_id"].nunique()),
    }


def summarize(frame: pd.DataFrame, prediction_delta: np.ndarray, label: str) -> dict[str, object]:
    current = frame.market_p_radiant.to_numpy(dtype=np.float64)
    future = frame.signal_market_p_radiant_300s.to_numpy(dtype=np.float64)
    fair = np.clip(current + prediction_delta, 0.0, 1.0)
    realized_delta = future - current
    effective_delta = fair - current
    gain = np.abs(realized_delta) - np.abs(future - fair)
    direction = np.where(effective_delta >= 0.0, 1.0, -1.0)
    directional = direction * realized_delta
    return {
        "label": label,
        "rows": int(len(frame)),
        "maps": int(frame.match_id.nunique()),
        "events": int(frame.event_id.nunique()),
        "no_move_mae_cents": float(np.abs(realized_delta).mean() * 100),
        "model_mae_cents": float(np.abs(future - fair).mean() * 100),
        "mae_gain_cents": float(gain.mean() * 100),
        "directional_markout_cents": float(directional.mean() * 100),
        "bias_cents": float((fair - future).mean() * 100),
    }


def main() -> None:
    catalog = pd.read_parquet(MATCH_CATALOG_PATH, columns=["match_id", "horn_source", "pauses_json"])
    def has_pre_horn_pause(value: object) -> bool:
        if not isinstance(value, str):
            return False
        pauses = json.loads(value)
        return any(
            -90 <= int(pause["time"]) < 0 and int(pause["duration"]) > 0
            for pause in pauses
        )

    catalog["has_pre_horn_pause"] = catalog.pauses_json.map(has_pre_horn_pause)
    affected_ids = set(
        catalog.loc[
            catalog.horn_source.eq("archive") & catalog.has_pre_horn_pause,
            "match_id",
        ].astype(int)
    )

    columns = list(
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
    validation = pd.read_parquet(VALIDATION_DATASET_PATH, columns=columns)
    validation = validation.loc[
        (validation.second >= MODEL_START_SECOND)
        & (validation.second < TRAIN_END_SECOND_EXCLUSIVE)
        & validation.market_status.eq("ok")
        & validation.signal_market_p_radiant_300s.notna()
    ].reset_index(drop=True)
    features = validation[FEATURE_COLUMNS].copy()
    features["second"] -= TRAIN_LAG_SECONDS
    research = load_predictor(RESEARCH_MODEL_DIR)
    prediction_delta = research.predict_one_thread(features).astype(np.float64)

    affected_mask = validation.match_id.astype(int).isin(affected_ids).to_numpy()
    full = summarize(validation, prediction_delta, "all_validation_labeled")
    clean_frame = validation.loc[~affected_mask].reset_index(drop=True)
    clean_delta = prediction_delta[~affected_mask]
    clean = summarize(clean_frame, clean_delta, "excluding_archive_horn_pre_horn_pause_maps")

    current = validation.market_p_radiant.to_numpy(dtype=np.float64)
    future = validation.signal_market_p_radiant_300s.to_numpy(dtype=np.float64)
    fair = np.clip(current + prediction_delta, 0.0, 1.0)
    target_delta = future - current
    effective_delta = fair - current
    gain = np.abs(target_delta) - np.abs(future - fair)
    directional = np.where(effective_delta >= 0.0, 1.0, -1.0) * target_delta
    result = {
        "affected_catalog_maps": int(len(affected_ids)),
        "affected_validation_maps_with_rows": int(validation.loc[affected_mask, "match_id"].nunique()),
        "affected_validation_rows": int(affected_mask.sum()),
        "all_validation": full,
        "excluding_affected_maps": clean,
        "clean_minus_full_point_change_cents": {
            "mae_gain": float(clean["mae_gain_cents"] - full["mae_gain_cents"]),
            "directional_markout": float(
                clean["directional_markout_cents"] - full["directional_markout_cents"]
            ),
        },
        "paired_event_cluster_bootstrap": {
            "mae_gain": bootstrap_metric_delta(
                validation,
                gain,
                np.ones(len(validation), dtype=bool),
                affected_mask,
            ),
            "directional_markout": bootstrap_metric_delta(
                validation,
                directional,
                np.ones(len(validation), dtype=bool),
                affected_mask,
            ),
        },
        "affected_group_definition": "match_catalog horn_source=archive and any positive-duration pauses_json pause at game-clock time -90 <= time < 0",
        "filter_definition": "current market_status=ok, market second in [-60,600), signal_market_p_radiant_300s non-null; same research model, no retraining",
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True))
    print("RESULT_JSON", OUT)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
