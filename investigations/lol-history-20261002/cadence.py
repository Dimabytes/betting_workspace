import json

import lightgbm as lgb
import numpy as np
import pandas as pd
from analyze import DATA, OUT, ROOT, calculate_metrics

from backtest.signals import SignalTiming, select_cadence_rows
from shared.constants.strategy import GRID_FEED_STALE_SECONDS
from shared.utils.dota_features import (
    DOTA_HISTORY_FIELDS,
    attach_catalog_features,
    history_feature_block,
)


def main():
    raw = pd.read_parquet(DATA / "validation.parquet", filters=[("second", "<", 480)])
    ids = raw.match_id.drop_duplicates().iloc[::10].tolist()
    raw = raw.loc[raw.match_id.isin(ids)].reset_index(drop=True)
    tape = pd.read_parquet(
        DATA / "game_features.parquet", filters=[("game_second", "<", 480), ("match_id", "in", ids)]
    )
    features = attach_catalog_features(
        raw, tape, key_seconds=raw.second, max_pivot_gap_seconds=GRID_FEED_STALE_SECONDS
    )
    meta = json.loads((ROOT / "data/lol/models/research/model.json").read_text())
    models = [
        lgb.Booster(model_file=str(ROOT / "data/lol/models/research" / file))
        for file in meta["members"]
    ]
    records = []
    for seed in range(3):
        cadence = select_cadence_rows(
            raw, game="lol", timing=SignalTiming(seed, GRID_FEED_STALE_SECONDS), lag_seconds=0
        )
        cadence_groups = dict(tuple(cadence.groupby("match_id", sort=False)))
        dense_parts = []
        sparse_parts = []
        for match_id, group in features.groupby("match_id", sort=False):
            ticks = cadence_groups.get(match_id)
            if ticks is None:
                continue
            decisions = group.loc[
                group.second.isin(ticks.second) & group.signal_market_p_radiant_300s.notna()
            ].copy()
            block = history_feature_block(
                ticks.second.to_numpy(dtype=np.int64),
                ticks[list(DOTA_HISTORY_FIELDS)].to_numpy(dtype=np.float64),
                decisions.second.to_numpy(dtype=np.int64),
                decisions[list(DOTA_HISTORY_FIELDS)].to_numpy(dtype=np.float64),
                GRID_FEED_STALE_SECONDS,
            )
            dense_parts.append(decisions)
            sparse_parts.append(decisions.assign(**block))
        dense = pd.concat(dense_parts, ignore_index=True)
        sparse = pd.concat(sparse_parts, ignore_index=True)
        dense_delta = np.mean(
            [model.predict(dense[meta["features"]], num_threads=1) for model in models], axis=0
        )
        sparse_delta = np.mean(
            [model.predict(sparse[meta["features"]], num_threads=1) for model in models], axis=0
        )
        records.append(
            {
                "seed": seed,
                "dense": calculate_metrics(dense, dense_delta),
                "sparse": calculate_metrics(sparse, sparse_delta),
                "mean_abs_delta_shift_cents": float(
                    np.mean(np.abs(dense_delta - sparse_delta)) * 100
                ),
                "signal_gate_flip_fraction": float(
                    np.mean((np.abs(dense_delta) >= 0.02) != (np.abs(sparse_delta) >= 0.02))
                ),
                "direction_flip_fraction": float(
                    np.mean((dense_delta >= 0) != (sparse_delta >= 0))
                ),
            }
        )
    (OUT / "cadence_predictions.json").write_text(json.dumps(records, indent=2))
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
