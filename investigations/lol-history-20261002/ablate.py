import gc
import json
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
from analyze import DATA, OUT, ROOT, calculate_metrics

from shared.constants.strategy import GRID_FEED_STALE_SECONDS
from shared.utils.dota_features import DOTA_XP_FEATURE_COLUMNS, attach_catalog_features
from shared.utils.gbm import (
    _member_match_ids,
    build_price_delta_labels,
    ensemble_member_params,
)


def load_features(name):
    print("Loading", name, flush=True)
    frame = pd.read_parquet(DATA / f"{name}.parquet")
    frame = frame.loc[frame.signal_market_p_radiant_300s.notna()].reset_index(drop=True)
    tape = pd.read_parquet(DATA / "game_features.parquet")
    tape = tape.loc[tape.match_id.isin(frame.match_id.unique())]
    enriched = attach_catalog_features(
        frame, tape, key_seconds=frame.second, max_pivot_gap_seconds=GRID_FEED_STALE_SECONDS
    )
    return enriched


def main():
    old_meta = json.loads(
        (ROOT / "data/lol/models/archive/research/20260930T191131Z/model.json").read_text()
    )
    training = load_features("training")
    validation = load_features("validation")
    common_train_ids = (
        training.loc[training.second <= 540, "match_id"].drop_duplicates().to_numpy(dtype=np.int64)
    )
    common_valid_ids = (
        validation.loc[validation.second <= 540, "match_id"]
        .drop_duplicates()
        .to_numpy(dtype=np.int64)
    )
    training = training.loc[training.match_id.isin(common_train_ids)].reset_index(drop=True)
    validation = validation.loc[validation.match_id.isin(common_valid_ids)].reset_index(drop=True)
    early = validation.loc[validation.second < 480].copy()
    early_y = build_price_delta_labels(early)
    results = []
    curve_rows = []
    for window in ["early", "full"]:
        for catalog, columns in [
            ("base12", old_meta["features"]),
            ("catalog77", DOTA_XP_FEATURE_COLUMNS),
        ]:
            arm = f"{catalog}_{window}"
            train_rows = training.loc[training.second <= 540] if window == "early" else training
            valid_rows = (
                validation.loc[validation.second <= 540] if window == "early" else validation
            )
            valid_y = build_price_delta_labels(valid_rows)
            predictions = []
            early_stop_predictions = []
            for member in range(3):
                started = time.monotonic()
                selected_ids = _member_match_ids(common_train_ids, member)
                group = train_rows.groupby("match_id", sort=False).indices
                positions = np.concatenate([group[int(match_id)] for match_id in selected_ids])
                selected = train_rows.iloc[positions]
                train_data = lgb.Dataset(selected[columns], build_price_delta_labels(selected))
                del selected, positions, group
                validation_data = lgb.Dataset(valid_rows[columns], valid_y, reference=train_data)
                early_data = lgb.Dataset(early[columns], early_y, reference=train_data)
                curve = {}
                print("Fitting", arm, member, "rows", len(train_rows), len(valid_rows), flush=True)
                model = lgb.train(
                    ensemble_member_params(),
                    train_data,
                    num_boost_round=180,
                    valid_sets=[validation_data, early_data],
                    valid_names=["fit_window", "buy_window"],
                    callbacks=[lgb.record_evaluation(curve)],
                )
                fit_best = int(np.argmin(curve["fit_window"]["l1"])) + 1
                buy_best = int(np.argmin(curve["buy_window"]["l1"])) + 1
                for iteration, (fit_error, buy_error) in enumerate(
                    zip(curve["fit_window"]["l1"], curve["buy_window"]["l1"], strict=True), 1
                ):
                    curve_rows.append(
                        {
                            "arm": arm,
                            "member": member,
                            "iteration": iteration,
                            "fit_mae_cents": fit_error * 100,
                            "buy_mae_cents": buy_error * 100,
                        }
                    )
                delta = model.predict(early[columns], num_iteration=fit_best, num_threads=1)
                early_stop_delta = model.predict(
                    early[columns], num_iteration=buy_best, num_threads=1
                )
                predictions.append(delta)
                early_stop_predictions.append(early_stop_delta)
                result = {
                    "arm": arm,
                    "member": member,
                    "fit_best": fit_best,
                    "buy_best": buy_best,
                    "seconds": time.monotonic() - started,
                    **calculate_metrics(early, delta),
                }
                results.append(result)
                print(json.dumps(result), flush=True)
                model.save_model(str(OUT / f"{arm}_member{member}.txt"), num_iteration=fit_best)
                if arm == "base12_early":
                    archived = lgb.Booster(
                        model_file=str(
                            ROOT
                            / "data/lol/models/archive/research/20260930T191131Z"
                            / old_meta["members"][member]
                        )
                    )
                    archived_delta = archived.predict(early[columns], num_threads=1)
                    print(
                        "Baseline reproduction max abs delta",
                        float(np.max(np.abs(archived_delta - delta))),
                        flush=True,
                    )
                del model, train_data, validation_data, early_data
                gc.collect()
            delta = np.mean(predictions, axis=0)
            stop_delta = np.mean(early_stop_predictions, axis=0)
            np.save(OUT / f"ablation_delta_{arm}.npy", delta)
            np.save(OUT / f"ablation_buy_stop_delta_{arm}.npy", stop_delta)
            for selector, selected_delta in [("fit_window", delta), ("buy_window", stop_delta)]:
                aggregate = {
                    "arm": arm,
                    "member": "ensemble3",
                    "selector": selector,
                    **calculate_metrics(early, selected_delta),
                }
                results.append(aggregate)
                print(json.dumps(aggregate), flush=True)
            pd.DataFrame(results).to_csv(OUT / "ablation.csv", index=False)
            pd.DataFrame(curve_rows).to_csv(OUT / "training_curves.csv", index=False)
    np.save(OUT / "ablation_keys.npy", early[["match_id", "second"]].to_numpy(dtype=np.int64))


if __name__ == "__main__":
    main()
