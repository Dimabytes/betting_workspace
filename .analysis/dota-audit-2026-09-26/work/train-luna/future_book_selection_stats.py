from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


WORK = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/train-luna")
OUT = WORK / "future_book_selection_regimes.json"


def second_bucket(value: int) -> str:
    if value < 0:
        return "prehorn [-60,0)"
    if value < 120:
        return "0-119"
    if value < 300:
        return "120-299"
    if value < 480:
        return "300-479"
    return "480-599"


def nw_bucket(value: int) -> str:
    value = abs(int(value))
    if value < 3_000:
        return "<3k"
    if value < 10_000:
        return "3k-<10k"
    return ">=10k"


def price_bucket(value: float) -> str:
    if value < 0.15:
        return "<0.15"
    if value < 0.50:
        return "0.15-<0.50"
    if value < 0.85:
        return "0.50-<0.85"
    return ">=0.85"


def event_cluster_diff_ci(kept: pd.DataFrame, dropped: pd.DataFrame, value_col: str, *, seed: int = 20260810) -> list[float] | None:
    events = sorted(set(kept.event_id.astype(str)) | set(dropped.event_id.astype(str)))
    if not events:
        return None
    kept_sums = kept.assign(event_id=kept.event_id.astype(str)).groupby("event_id")[value_col].agg(["sum", "count"])
    dropped_sums = dropped.assign(event_id=dropped.event_id.astype(str)).groupby("event_id")[value_col].agg(["sum", "count"])
    keep_num = kept_sums["sum"].reindex(events, fill_value=0.0).to_numpy(dtype=np.float64)
    keep_den = kept_sums["count"].reindex(events, fill_value=0.0).to_numpy(dtype=np.float64)
    drop_num = dropped_sums["sum"].reindex(events, fill_value=0.0).to_numpy(dtype=np.float64)
    drop_den = dropped_sums["count"].reindex(events, fill_value=0.0).to_numpy(dtype=np.float64)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(events), size=(2000, len(events)))
    keep_est = keep_num[indices].sum(axis=1) / keep_den[indices].sum(axis=1)
    drop_est = drop_num[indices].sum(axis=1) / drop_den[indices].sum(axis=1)
    delta = drop_est - keep_est
    return [float(np.percentile(delta, 2.5)), float(np.percentile(delta, 97.5))]


def group_summary(candidate: pd.DataFrame, drops: pd.DataFrame) -> dict[str, object]:
    current_ok = candidate.loc[candidate.market_status.eq("ok")].copy()
    missing = current_ok.signal_market_p_radiant_300s.isna()
    dropped = current_ok.loc[missing].merge(
        drops[["match_id", "market_second", "future_status", "relaxed_label", "relaxed_offset_seconds"]],
        on=["match_id", "market_second"],
        how="left",
        validate="one_to_one",
    )
    kept = current_ok.loc[~missing].copy()
    recovered = dropped.loc[dropped.relaxed_label.notna()].copy()
    kept["move"] = kept.signal_market_p_radiant_300s.astype(float) - kept.market_p_radiant.astype(float)
    recovered["move"] = recovered.relaxed_label.astype(float) - recovered.market_p_radiant.astype(float)
    kept["abs_move"] = kept.move.abs()
    recovered["abs_move"] = recovered.move.abs()
    kept["big_10c"] = (kept.abs_move >= 0.10).astype(float)
    recovered["big_10c"] = (recovered.abs_move >= 0.10).astype(float)

    status_counts = Counter(dropped.future_status.fillna("not_reconstructed").tolist())
    regimes: dict[str, object] = {}
    current_ok["second_bucket"] = current_ok.game_second.map(second_bucket)
    current_ok["nw_bucket"] = current_ok.radiant_nw_adv.map(nw_bucket)
    current_ok["price_bucket"] = current_ok.market_p_radiant.map(price_bucket)
    current_ok["favorite_result"] = np.where(
        current_ok.market_favorite_correct.astype(bool), "favorite_correct", "favorite_wrong"
    )
    for column in ["second_bucket", "nw_bucket", "price_bucket", "favorite_result"]:
        table: dict[str, object] = {}
        for bucket, group in current_ok.groupby(column, sort=True):
            group_drops = dropped.loc[dropped.index.isin(group.index)]
            # Index membership is ambiguous after a merge; construct the bucket-specific
            # dropped row subset from its own current-row fields instead.
            dropped_keys = set(
                tuple(value)
                for value in group.loc[group.signal_market_p_radiant_300s.isna(), ["match_id", "market_second"]].to_numpy()
            )
            group_drops = dropped.loc[
                [
                    (int(row.match_id), int(row.market_second)) in dropped_keys
                    for row in dropped.itertuples(index=False)
                ]
            ]
            counts = Counter(group_drops.future_status.fillna("not_reconstructed").tolist())
            n_drop = int(group.signal_market_p_radiant_300s.isna().sum())
            table[str(bucket)] = {
                "current_ok_rows": int(len(group)),
                "kept_labeled_rows": int(len(group) - n_drop),
                "dropped_rows": n_drop,
                "drop_share": float(n_drop / len(group)) if len(group) else None,
                "future_status_counts": {str(k): int(v) for k, v in counts.items()},
            }
        regimes[column] = table

    move_compare = {
        "kept_rows": int(len(kept)),
        "dropped_rows_with_relaxed_label": int(len(recovered)),
        "mean_abs_move_gap_dropped_minus_kept_cents": float(
            (recovered.abs_move.mean() - kept.abs_move.mean()) * 100
        ),
        "mean_abs_move_gap_event_cluster_95ci_cents": [
            float(v * 100) for v in event_cluster_diff_ci(kept, recovered, "abs_move")
        ],
        "share_abs_move_ge_10c_gap_dropped_minus_kept_pp": float(
            (recovered.big_10c.mean() - kept.big_10c.mean()) * 100
        ),
        "share_abs_move_ge_10c_gap_event_cluster_95ci_pp": [
            float(v * 100) for v in event_cluster_diff_ci(kept, recovered, "big_10c")
        ],
        "recovered_drop_fraction": float(len(recovered) / len(dropped)) if len(dropped) else None,
        "recovered_dropped_share_abs_move_ge_10c": float(recovered.big_10c.mean()) if len(recovered) else None,
        "kept_share_abs_move_ge_10c": float(kept.big_10c.mean()) if len(kept) else None,
    }
    return {
        "rows_any_status": int(len(candidate)),
        "current_ok_rows": int(len(current_ok)),
        "kept_labeled_rows": int(len(kept)),
        "dropped_current_ok_rows": int(len(dropped)),
        "drop_share": float(len(dropped) / len(current_ok)) if len(current_ok) else None,
        "future_status_counts": {str(k): int(v) for k, v in status_counts.items()},
        "regimes": regimes,
        "move_comparison": move_compare,
    }


def main() -> None:
    minute = pd.read_parquet(WORK / "minute_candidate_rows.parquet")
    validation = pd.read_parquet(WORK / "validation_fixed_candidate_rows.parquet")
    drops = pd.read_parquet(WORK / "future_book_dropped_labels.parquet")
    result: dict[str, object] = {}
    result["research_train"] = group_summary(
        minute.loc[minute.split.eq("research_train")],
        drops.loc[drops.split.eq("research_train")],
    )
    result["validation"] = group_summary(
        validation,
        drops.loc[drops.split.eq("validation")],
    )
    production_candidates = minute.copy()
    production_candidates["split"] = "production"
    production_drops = drops.loc[drops.split.isin(["research_train", "validation_minute"])].copy()
    result["production"] = group_summary(production_candidates, production_drops)
    result["method"] = {
        "regime_scope": "current-status-ok candidate rows only; future statuses split dropped rows into wide_spread, stale_quote, missing_quote, inconsistent_pair where present",
        "move_test": "event-series cluster bootstrap, 2,000 replicates; dropped rows use the recovered relaxed label",
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True))
    print("RESULT_JSON", OUT)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
