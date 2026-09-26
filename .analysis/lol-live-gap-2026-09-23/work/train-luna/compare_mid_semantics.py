from pathlib import Path

import numpy as np
import pandas as pd

from shared.utils.gbm import FEATURE_COLUMNS, load_predictor, predict_future_prices

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
OLD_PATH = E / "data/experiments/lol-horizon-train/datasets/validation.parquet"
NEW_PATH = E / "data/lol/processed/datasets/validation.parquet"
MODEL_PATHS = {
    "pre_332_20260915": E / "data/lol/models/archive/research/20260915T210420Z",
    "post_332_20260919": E / "data/lol/models/archive/research/20260919T112924Z",
    "current_20260921": E / "data/lol/models/research",
}


def metrics(frame: pd.DataFrame, prefix: str) -> dict[str, float | int]:
    current = frame[f"{prefix}_current"].to_numpy(dtype=np.float64)
    future = frame[f"{prefix}_future"].to_numpy(dtype=np.float64)
    predicted = frame[f"{prefix}_predicted"].to_numpy(dtype=np.float64)
    no_move = np.abs(future - current)
    model_error = np.abs(future - predicted)
    predicted_direction = np.where(predicted >= current, 1.0, -1.0)
    directional = predicted_direction * (future - current)
    return {
        "rows": len(frame),
        "maps": int(frame["match_id"].nunique()),
        "no_move_mae_c": float(no_move.mean() * 100),
        "model_mae_c": float(model_error.mean() * 100),
        "gain_c": float((no_move - model_error).mean() * 100),
        "directional_markout_c": float(directional.mean() * 100),
    }


def main() -> None:
    columns = list(dict.fromkeys(["match_id", "event_id", "start_time", "second", *FEATURE_COLUMNS, "signal_market_p_radiant_300s"]))
    old = pd.read_parquet(OLD_PATH, columns=columns)
    new = pd.read_parquet(NEW_PATH, columns=columns)
    for frame in (old, new):
        frame.dropna(subset=["signal_market_p_radiant_300s"], inplace=True)
        frame.drop(frame.loc[~frame["second"].between(0, 540)].index, inplace=True)
    old = old.rename(columns={"market_p_radiant": "old_current", "signal_market_p_radiant_300s": "old_future"})
    new = new.rename(columns={"market_p_radiant": "new_current", "signal_market_p_radiant_300s": "new_future"})
    common = old.merge(new, on=["match_id", "second"], how="inner", suffixes=("_old", "_new"), validate="one_to_one")
    print("OVERLAP")
    print({
        "old_labeled_rows": len(old), "old_labeled_maps": int(old.match_id.nunique()),
        "new_labeled_rows": len(new), "new_labeled_maps": int(new.match_id.nunique()),
        "common_labeled_rows": len(common), "common_labeled_maps": int(common.match_id.nunique()),
        "common_events": int(common.event_id_old.astype(str).nunique()),
        "different_match_id_or_event": int((common.event_id_old.astype(str) != common.event_id_new.astype(str)).sum()),
        "different_nonmarket_feature_values": {
            column: int((common[f"{column}_old"] != common[f"{column}_new"]).sum())
            for column in FEATURE_COLUMNS if column not in {"second", "market_p_radiant"}
        },
        "market_mid_abs_difference_c": float((common.old_current - common.new_current).abs().mean() * 100),
        "market_mid_abs_difference_p95_c": float((common.old_current - common.new_current).abs().quantile(.95) * 100),
        "label_abs_difference_c": float((common.old_future - common.new_future).abs().mean() * 100),
        "label_abs_difference_p95_c": float((common.old_future - common.new_future).abs().quantile(.95) * 100),
    })
    for name, path in MODEL_PATHS.items():
        predictor = load_predictor(path)
        for version in ("old", "new"):
            feature_columns = [column for column in FEATURE_COLUMNS if column != "market_p_radiant"]
            source_columns = {
                column: f"{column}_{version}" for column in feature_columns if column != "second"
            }
            features = common[[*source_columns.values(), "second"]].rename(
                columns={value: key for key, value in source_columns.items()}
            )
            features["market_p_radiant"] = common[f"{version}_current"].to_numpy()
            features = features[FEATURE_COLUMNS]
            common[f"{version}_predicted"] = predict_future_prices(
                predictor,
                features,
                common[f"{version}_current"].to_numpy(dtype=np.float64),
            )
        print(name, "old T+10/T+310 semantics", metrics(common, "old"))
        print(name, "new T/T+300 semantics", metrics(common, "new"))


if __name__ == "__main__":
    main()
