import numpy as np
import pandas as pd

from shared.constants.dataset import TRAIN_LAG_SECONDS, MODEL_TARGET_HORIZON_SECONDS
from shared.constants.paths import MATCH_CATALOG_PATH
from shared.utils.match_catalog import load_match_catalog
from shared.utils.match_time import datetime_to_ns, get_state_available_ts


catalog = load_match_catalog(MATCH_CATALOG_PATH)
ended_us = {
    match_id: datetime_to_ns(entry.ended_at) // 1_000
    for match_id, entry in catalog.items()
}

validation = pd.read_parquet(
    "data/new_processed/dataset/validation_dataset.parquet",
    columns=[
        "match_id",
        "second",
        "state_ts_us",
        "market_p_radiant",
        "signal_market_p_radiant_300s",
        "radiant_win",
    ],
)
validation = validation[validation.signal_market_p_radiant_300s.notna()].copy()
validation["ended_us"] = validation.match_id.map(ended_us)
validation["after_end"] = (
    validation.state_ts_us + MODEL_TARGET_HORIZON_SECONDS * 1_000_000
) > validation.ended_us
validation["abs_delta"] = (
    validation.signal_market_p_radiant_300s - validation.market_p_radiant
).abs()
validation["terminal_error"] = (
    validation.signal_market_p_radiant_300s - validation.radiant_win.astype(float)
).abs()
print(
    "VALIDATION_ALL",
    "labels",
    len(validation),
    "after_end",
    int(validation.after_end.sum()),
    "share",
    float(validation.after_end.mean()),
    "maps",
    int(validation.loc[validation.after_end, "match_id"].nunique()),
)
post = validation[validation.after_end]
print(
    "VALIDATION_POST_END_MAGNITUDE",
    "abs_delta_mean_median_p95",
    (
        round(float(post.abs_delta.mean()), 5),
        round(float(post.abs_delta.median()), 5),
        round(float(post.abs_delta.quantile(0.95)), 5),
    ),
    "terminal_within_5c",
    int((post.terminal_error <= 0.05).sum()),
    "terminal_share",
    float((post.terminal_error <= 0.05).mean()),
)
validation["state_second"] = validation.second - TRAIN_LAG_SECONDS
train_eval = validation[
    validation.state_second.between(-60, 540, inclusive="both")
]
print(
    "VALIDATION_TRAIN_WINDOW",
    "labels",
    len(train_eval),
    "after_end",
    int(train_eval.after_end.sum()),
    "share",
    float(train_eval.after_end.mean()),
    "maps",
    int(train_eval.loc[train_eval.after_end, "match_id"].nunique()),
)
buy = validation[
    validation.state_second.between(-60, 479, inclusive="both")
]
print(
    "VALIDATION_BUY_WINDOW",
    "labels",
    len(buy),
    "after_end",
    int(buy.after_end.sum()),
    "maps",
    int(buy.loc[buy.after_end, "match_id"].nunique()),
)

train = pd.read_parquet(
    "data/new_processed/dataset/training_dataset.parquet",
    columns=["match_id", "second", "market_p_radiant", "signal_market_p_radiant_300s", "radiant_win"],
)
train = train[train.signal_market_p_radiant_300s.notna()].copy()
train["target_us"] = [
    datetime_to_ns(
        get_state_available_ts(
            horn=catalog[int(match_id)].horn_at,
            second=int(second) + TRAIN_LAG_SECONDS,
            pauses=catalog[int(match_id)].pauses,
        )
    )
    // 1_000
    + MODEL_TARGET_HORIZON_SECONDS * 1_000_000
    for match_id, second in zip(train.match_id, train.second, strict=True)
]
train["ended_us"] = train.match_id.map(ended_us)
train["after_end"] = train.target_us > train.ended_us
train["after_end_seconds"] = (train.target_us - train.ended_us) / 1_000_000
train["abs_delta"] = (
    train.signal_market_p_radiant_300s - train.market_p_radiant
).abs()
print(
    "TRAIN_ALL",
    "labels",
    len(train),
    "after_end",
    int(train.after_end.sum()),
    "share",
    float(train.after_end.mean()),
    "maps",
    int(train.loc[train.after_end, "match_id"].nunique()),
)
print(
    "TRAIN_POST_END_ROWS",
    train.loc[train.after_end, [
        "match_id", "second", "after_end_seconds", "market_p_radiant",
        "signal_market_p_radiant_300s", "radiant_win", "abs_delta",
    ]].to_dict("records"),
)
print(
    "TRAIN_BUY_WINDOW",
    "labels",
    len(train[train.second.between(-60, 479, inclusive="both")]),
    "after_end",
    int(train.loc[train.second.between(-60, 479, inclusive="both"), "after_end"].sum()),
)
