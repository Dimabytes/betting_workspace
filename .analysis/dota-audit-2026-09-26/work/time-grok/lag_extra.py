"""Paired lag differences, entry-subset metrics, and the two Oddin horn pins."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from shared.constants.paths import (
    GAME_FEATURES_DATASET_PATH,
    MATCH_CATALOG_PATH,
    PRODUCTION_NOXP_MODEL_DIR,
    RESEARCH_MODEL_DIR,
    VALIDATION_DATASET_PATH,
)
from shared.constants.strategy import MIN_ABS_DELTA
from shared.utils.gbm import load_predictor
from shared.utils.market_scenario_report import SeriesMetricTotals, bootstrap_series_cluster_ci
from shared.utils.match_time import get_paused_seconds_before, parse_utc
from trader.oddin_archive import iter_oddin_archive_records
from trader.oddin_feed import OddinSnapshotReducer, replay_oddin_records
from trader.paths import ODDIN_STATE_ARCHIVE_FILENAME

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
OUT = Path(__file__).resolve().parent / "lag_extra.json"
STATE_COLS = [
    "radiant_nw_adv",
    "radiant_nw",
    "dire_nw",
    "radiant_xp_adv",
    "deaths_radiant",
    "deaths_dire",
    "top1_nw_adv",
    "radiant_top1_nw_ratio",
    "dire_top1_nw_ratio",
    "market_radiant_prior",
]
FOCUS = ("9007618656", "9008125103")


def ci(numer: np.ndarray, event_ids: np.ndarray) -> dict[str, float] | None:
    totals: dict[str, list[float]] = {}
    for event_id, value in zip(event_ids, numer, strict=True):
        slot = totals.setdefault(str(event_id), [0.0, 0.0])
        slot[0] += float(value)
        slot[1] += 1.0
    series = tuple(
        SeriesMetricTotals(numerator_sum=pair[0], row_count=int(pair[1])) for pair in totals.values()
    )
    bounds = bootstrap_series_cluster_ci(series)
    if bounds is None:
        return None
    return {"low": bounds.low, "high": bounds.high, "events": float(len(series))}


def pack(delta: np.ndarray, market: np.ndarray, future: np.ndarray, event_ids: np.ndarray) -> dict[str, object]:
    model_p = np.clip(market + delta, 0.0, 1.0)
    gain = np.abs(future - market) - np.abs(future - model_p)
    bias = model_p - future
    direction = np.where(model_p >= market, 1.0, -1.0)
    markout = direction * (future - market)
    entry = np.abs(delta) >= MIN_ABS_DELTA
    return {
        "rows": int(len(delta)),
        "mae_gain_300": float(gain.mean()),
        "mae_gain_300_ci": ci(gain, event_ids),
        "bias_300": float(bias.mean()),
        "dir_markout_300": float(markout.mean()),
        "dir_markout_300_ci": ci(markout, event_ids),
        "entry_share": float(entry.mean()) if len(delta) else None,
        "mean_abs_realized": float(np.abs(future - market).mean()),
        "mean_realized_in_L_direction": float(markout.mean()),
    }


def predict(predictor: object, frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    data = frame[columns]
    stacked = [
        np.asarray(booster.predict(data, num_threads=2), dtype=np.float64)
        for booster in predictor._boosters  # type: ignore[attr-defined]
    ]
    return np.stack(stacked).mean(axis=0)


def score() -> dict[str, object]:
    val = pd.read_parquet(
        VALIDATION_DATASET_PATH,
        columns=["match_id", "second", "event_id", "market_status", "market_p_radiant", "signal_market_p_radiant_300s"],
    )
    val = val[
        (val["second"] >= 0)
        & (val["second"] < 480)
        & (val["market_status"] == "ok")
        & val["market_p_radiant"].notna()
        & val["signal_market_p_radiant_300s"].notna()
    ].reset_index(drop=True)
    feat = pd.read_parquet(GAME_FEATURES_DATASET_PATH, columns=["match_id", "game_second", *STATE_COLS])
    feat = feat[
        feat["match_id"].isin(set(val["match_id"]))
        & (feat["game_second"] >= -30)
        & (feat["game_second"] < 480)
    ].drop_duplicates(["match_id", "game_second"]).set_index(["match_id", "game_second"])
    models = {
        "research": (load_predictor(RESEARCH_MODEL_DIR), ["second", *STATE_COLS, "market_p_radiant"]),
        "production-noxp": (
            load_predictor(PRODUCTION_NOXP_MODEL_DIR),
            ["second", *[c for c in STATE_COLS if c != "radiant_xp_adv"], "market_p_radiant"],
        ),
    }
    frames: dict[int, pd.DataFrame] = {}
    for lag in (8, 10, 16):
        keys = pd.MultiIndex.from_arrays(
            [val["match_id"].to_numpy(), val["second"].to_numpy() - lag],
            names=["match_id", "game_second"],
        )
        state = feat.reindex(keys)
        state.index = val.index
        frame = val.join(state).dropna(subset=["radiant_nw"]).reset_index(drop=True)
        frame["second"] = frame["second"] - lag
        frames[lag] = frame
    out: dict[str, object] = {}
    for name, (predictor, columns) in models.items():
        deltas = {lag: predict(predictor, frames[lag], columns) for lag in (8, 10, 16)}
        base = frames[10]
        market = base["market_p_radiant"].to_numpy(dtype=np.float64)
        future = base["signal_market_p_radiant_300s"].to_numpy(dtype=np.float64)
        events = base["event_id"].to_numpy()
        # row order matches across lags: same val order, no drops (checked).
        if not all(len(frames[lag]) == len(base) for lag in (8, 16)):
            raise SystemExit("lag frames diverged")
        entry = np.abs(deltas[10]) >= MIN_ABS_DELTA
        side = {
            lag: np.where(np.clip(market + deltas[lag], 0.0, 1.0) >= market, 1.0, -1.0)
            for lag in (8, 10, 16)
        }
        subset = {}
        for lag in (8, 10, 16):
            metrics = pack(deltas[lag][entry], market[entry], future[entry], events[entry])
            metrics["side_agrees_with_L10"] = float((side[lag][entry] == side[10][entry]).mean())
            metrics["still_entry_share"] = float((np.abs(deltas[lag][entry]) >= MIN_ABS_DELTA).mean())
            subset[str(lag)] = metrics
        l10 = float(subset["10"]["dir_markout_300"])
        gain10 = np.abs(future - market) - np.abs(future - np.clip(market + deltas[10], 0.0, 1.0))
        mark10 = side[10] * (future - market)
        paired = {}
        for lag in (8, 16):
            gain = np.abs(future - market) - np.abs(future - np.clip(market + deltas[lag], 0.0, 1.0))
            mark = side[lag] * (future - market)
            paired[str(lag)] = {
                "mae_gain_minus_L10": float((gain - gain10).mean()),
                "mae_gain_minus_L10_ci": ci(gain - gain10, events),
                "markout_minus_L10": float((mark - mark10).mean()),
                "markout_minus_L10_ci": ci(mark - mark10, events),
                "entry_markout_ratio_vs_L10": float(subset[str(lag)]["dir_markout_300"]) / l10,
            }
        out[name] = {
            "entry_rows": int(entry.sum()),
            "entry_events": int(len(set(events[entry].tolist()))),
            "entry_subset": subset,
            "paired_vs_L10_all_rows": paired,
        }
        print(name, "entry", int(entry.sum()), flush=True)
    return out


def pin_archive(archive_id: str) -> dict[str, object]:
    directory = E / "data/trader" / archive_id
    meta = json.loads((directory / "match.json").read_text())
    market = meta.get("market") or {}
    reducer = OddinSnapshotReducer(int(meta["map_number"]), bool(market.get("yes_is_radiant", True)))
    pin: dict[str, object] | None = None
    ages: list[float] = []
    transports: list[float] = []
    paused_positive = 0
    positive = 0
    for event in replay_oddin_records(
        iter_oddin_archive_records(directory / ODDIN_STATE_ARCHIVE_FILENAME),
        reducer,
    ):
        snap = event.snapshot
        if snap.finished:
            break
        received = parse_utc(event.received_at_utc)
        updated_unix = snap.server_timestamp
        if pin is None and snap.second > 0:
            pin = {
                "second": int(snap.second),
                "paused": bool(snap.paused),
                "phase": snap.phase.value,
                "horn_unix": int(event.horn_unix_seconds),
                "horn_iso": parse_utc_from_unix(event.horn_unix_seconds),
                "received_at_utc": event.received_at_utc,
                "server_timestamp": int(updated_unix),
                "transport_s": received.timestamp() - updated_unix,
            }
        if snap.second > 0:
            positive += 1
            if snap.paused:
                paused_positive += 1
            if not snap.paused and snap.second <= 540:
                ages.append(received.timestamp() - (event.horn_unix_seconds + snap.second))
                transports.append(received.timestamp() - updated_unix)
    catalog = pd.read_parquet(
        MATCH_CATALOG_PATH,
        columns=["match_id", "horn_at", "horn_source", "pauses_json", "archive_feed_source"],
    )
    row = catalog[catalog["match_id"].astype(str) == archive_id].iloc[0]
    pauses = json.loads(row.pauses_json) if isinstance(row.pauses_json, str) else []
    pin_second = None if pin is None else int(pin["second"])
    inside = []
    if pin_second is not None:
        for pause in pauses:
            start = int(pause["time"])
            # game clock is frozen at `time` for `duration` wall seconds
            if start == pin_second or (start < 0 <= pin_second and start + int(pause["duration"]) > 0 and False):
                inside.append((start, int(pause["duration"])))
            if pin is not None and pin["paused"] and start == pin_second:
                inside.append((start, int(pause["duration"])))
    results = pd.read_parquet(
        E / "data/backtests/dota_maker/LIVE/seed0/results.parquet",
        columns=["match_id", "signal_mode", "feed_source", "engine_pnl", "model_name"],
    )
    live = results[results["match_id"].astype(str) == archive_id]
    return {
        "archive": archive_id,
        "match_json_feed": meta.get("feed_source"),
        "catalog_feed": row.archive_feed_source,
        "catalog_horn": str(row.horn_at),
        "horn_source": row.horn_source,
        "pre_horn_pause_s": get_paused_seconds_before(pauses, 0),
        "pauses": [(int(p["time"]), int(p["duration"])) for p in pauses],
        "pin": pin,
        "pin_second_matches_a_pause_start": bool(pin_second is not None and any(int(p["time"]) == pin_second for p in pauses)),
        "pin_paused_flag": None if pin is None else pin["paused"],
        "positive_ticks": positive,
        "paused_positive_ticks": paused_positive,
        "median_feature_age_s": None if not ages else float(np.median(ages)),
        "median_transport_s": None if not transports else float(np.median(transports)),
        "live": None
        if live.empty
        else {
            "signal_mode": str(live.iloc[0]["signal_mode"]),
            "feed_source": str(live.iloc[0]["feed_source"]),
            "engine_pnl": float(live.iloc[0]["engine_pnl"]),
            "model_name": str(live.iloc[0]["model_name"]),
        },
    }


def parse_utc_from_unix(unix_seconds: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(unix_seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    scored = score()
    pins = [pin_archive(archive_id) for archive_id in FOCUS]
    OUT.write_text(json.dumps({"scored": scored, "pins": pins}, indent=2))
    print(OUT, flush=True)


if __name__ == "__main__":
    main()
