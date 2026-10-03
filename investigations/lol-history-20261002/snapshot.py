import gc
import json
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
from analyze import DATA, OUT, calculate_metrics

from shared.utils.dota_features import DOTA_XP_FEATURE_COLUMNS, market_derived_columns
from shared.utils.gbm import _member_match_ids, build_price_delta_labels, ensemble_member_params


def load_rows(name):
    rows = pd.read_parquet(DATA / f"{name}.parquet")
    rows = rows.loc[rows.signal_market_p_radiant_300s.notna()].reset_index(drop=True)
    return rows.assign(**market_derived_columns(rows))


def main():
    training = load_rows("training")
    validation = load_rows("validation")
    ids = (
        training.loc[training.second <= 540, "match_id"].drop_duplicates().to_numpy(dtype=np.int64)
    )
    valid_ids = (
        validation.loc[validation.second <= 540, "match_id"]
        .drop_duplicates()
        .to_numpy(dtype=np.int64)
    )
    training = training.loc[training.match_id.isin(ids)]
    validation = validation.loc[validation.match_id.isin(valid_ids)]
    early = validation.loc[validation.second < 480]
    columns = [name for name in DOTA_XP_FEATURE_COLUMNS if not name.startswith("game_")]
    results = []
    for window in ["early", "full"]:
        train_rows = training.loc[training.second <= 540] if window == "early" else training
        valid_rows = validation.loc[validation.second <= 540] if window == "early" else validation
        parts = []
        for member in range(3):
            start = time.monotonic()
            group = train_rows.groupby("match_id", sort=False).indices
            positions = np.concatenate(
                [group[int(match_id)] for match_id in _member_match_ids(ids, member)]
            )
            selected = train_rows.iloc[positions]
            train_data = lgb.Dataset(selected[columns], build_price_delta_labels(selected))
            del selected, positions, group
            valid_data = lgb.Dataset(
                valid_rows[columns], build_price_delta_labels(valid_rows), reference=train_data
            )
            buy_data = lgb.Dataset(
                early[columns], build_price_delta_labels(early), reference=train_data
            )
            curve = {}
            print("Fitting snapshot17", window, member, flush=True)
            model = lgb.train(
                ensemble_member_params(),
                train_data,
                num_boost_round=180,
                valid_sets=[valid_data, buy_data],
                valid_names=["fit_window", "buy_window"],
                callbacks=[lgb.record_evaluation(curve)],
            )
            fit_best = int(np.argmin(curve["fit_window"]["l1"])) + 1
            buy_best = int(np.argmin(curve["buy_window"]["l1"])) + 1
            prediction = model.predict(early[columns], num_iteration=fit_best, num_threads=1)
            parts.append(prediction)
            row = {
                "arm": f"snapshot17_{window}",
                "member": member,
                "fit_best": fit_best,
                "buy_best": buy_best,
                "seconds": time.monotonic() - start,
                **calculate_metrics(early, prediction),
            }
            results.append(row)
            print(json.dumps(row), flush=True)
            del model, train_data, valid_data, buy_data
            gc.collect()
        delta = np.mean(parts, axis=0)
        np.save(OUT / f"ablation_delta_snapshot17_{window}.npy", delta)
        row = {
            "arm": f"snapshot17_{window}",
            "member": "ensemble3",
            **calculate_metrics(early, delta),
        }
        results.append(row)
        print(json.dumps(row), flush=True)
        pd.DataFrame(results).to_csv(OUT / "snapshot_ablation.csv", index=False)


if __name__ == "__main__":
    main()
