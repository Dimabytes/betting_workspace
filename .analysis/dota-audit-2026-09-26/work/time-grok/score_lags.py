"""Offline lag sweep on published Dota models. Read-only."""

from __future__ import annotations

import json
import statistics
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
from shared.utils.gbm import NO_XP_FEATURE_COLUMNS, load_predictor
from shared.utils.market_scenario_report import (
    SeriesMetricTotals,
    bootstrap_series_cluster_ci,
)
from shared.utils.match_time import (
    get_horn_datetime,
    get_paused_seconds_before,
    parse_utc,
)
from trader.oddin_archive import iter_oddin_archive_records
from trader.oddin_feed import OddinSnapshotReducer, replay_oddin_records
from trader.paths import ODDIN_STATE_ARCHIVE_FILENAME

OUT = Path(__file__).resolve().parent / "lag_out.json"
E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data/trader"
LAGS = (0, 2, 5, 8, 10, 12, 16, 20, 30)
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


def _ci(numer: np.ndarray, event_ids: np.ndarray) -> dict[str, float] | None:
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


def _metrics(
    delta: np.ndarray,
    market: np.ndarray,
    future: np.ndarray,
    event_ids: np.ndarray,
) -> dict[str, object]:
    model_p = np.clip(market + delta, 0.0, 1.0)
    no_move = np.abs(future - market)
    model_err = np.abs(future - model_p)
    gain = no_move - model_err
    bias = model_p - future
    direction = np.where(model_p >= market, 1.0, -1.0)
    markout = direction * (future - market)
    entry = np.abs(delta) >= MIN_ABS_DELTA
    return {
        "rows": int(len(delta)),
        "events": int(len(set(event_ids.tolist()))),
        "mae_gain_300": float(gain.mean()),
        "mae_gain_300_ci": _ci(gain, event_ids),
        "model_mae_300": float(model_err.mean()),
        "no_move_mae_300": float(no_move.mean()),
        "bias_300": float(bias.mean()),
        "dir_markout_300": float(markout.mean()),
        "dir_markout_300_ci": _ci(markout, event_ids),
        "entry_share": float(entry.mean()),
        "entry_share_ci": _ci(entry.astype(np.float64), event_ids),
        "mean_abs_delta": float(np.abs(delta).mean()),
    }


def load_window() -> tuple[pd.DataFrame, pd.DataFrame]:
    val = pd.read_parquet(
        VALIDATION_DATASET_PATH,
        columns=[
            "match_id",
            "second",
            "event_id",
            "market_status",
            "market_p_radiant",
            "signal_market_p_radiant_300s",
        ],
    )
    val = val[
        (val["second"] >= 0)
        & (val["second"] < 480)
        & (val["market_status"] == "ok")
        & val["market_p_radiant"].notna()
        & val["signal_market_p_radiant_300s"].notna()
    ].reset_index(drop=True)
    ids = set(val["match_id"].tolist())
    feat = pd.read_parquet(
        GAME_FEATURES_DATASET_PATH,
        columns=["match_id", "game_second", *STATE_COLS],
    )
    feat = feat[feat["match_id"].isin(ids) & (feat["game_second"] >= -30) & (feat["game_second"] < 480)]
    feat = feat.drop_duplicates(["match_id", "game_second"]).set_index(["match_id", "game_second"])
    return val, feat


def align(val: pd.DataFrame, feat: pd.DataFrame, lag: int) -> pd.DataFrame:
    keys = pd.MultiIndex.from_arrays(
        [val["match_id"].to_numpy(), val["second"].to_numpy() - lag],
        names=["match_id", "game_second"],
    )
    state = feat.reindex(keys)
    state.index = val.index
    out = val.join(state)
    return out.dropna(subset=["radiant_nw"]).reset_index(drop=True)


def predict_delta(predictor: object, frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    names = list(predictor.feature_names)  # type: ignore[attr-defined]
    if names != columns:
        raise SystemExit(f"feature mismatch: {names} != {columns}")
    data = frame[columns]
    stacked = [
        np.asarray(booster.predict(data, num_threads=2), dtype=np.float64)
        for booster in predictor._boosters  # type: ignore[attr-defined]
    ]
    return np.stack(stacked).mean(axis=0)


def score_models(val: pd.DataFrame, feat: pd.DataFrame) -> dict[str, object]:
    models = {
        "research": (load_predictor(RESEARCH_MODEL_DIR), ["second", *STATE_COLS, "market_p_radiant"]),
        "production-noxp": (
            load_predictor(PRODUCTION_NOXP_MODEL_DIR),
            ["second", *[c for c in STATE_COLS if c != "radiant_xp_adv"], "market_p_radiant"],
        ),
    }
    # research features are STATE_COLS which already ends with prior; market_p appended
    # NO_XP drops radiant_xp_adv. Order must match model.json: second, state..., prior, market_p.
    rows: list[dict[str, object]] = []
    deltas_at: dict[tuple[str, int], np.ndarray] = {}
    frames_at: dict[int, pd.DataFrame] = {}
    for lag in LAGS:
        frame = align(val, feat, lag)
        frame = frame.copy()
        frame["second"] = frame["second"] - lag
        frames_at[lag] = frame
        for name, (predictor, columns) in models.items():
            # columns use frame["second"] as the feature and frame["market_p_radiant"] at M
            delta = predict_delta(predictor, frame, columns)
            deltas_at[(name, lag)] = delta
            metrics = _metrics(
                delta,
                frame["market_p_radiant"].to_numpy(dtype=np.float64),
                frame["signal_market_p_radiant_300s"].to_numpy(dtype=np.float64),
                frame["event_id"].to_numpy(),
            )
            metrics["model"] = name
            metrics["lag"] = lag
            metrics["aligned_rows"] = int(len(frame))
            rows.append(metrics)
            print(f"scored {name} L={lag} rows={len(frame)} gain={metrics['mae_gain_300']:.5f}", flush=True)
    base = frames_at[10]
    base_delta = deltas_at[("research", 10)]
    entry = np.abs(base_delta) >= MIN_ABS_DELTA
    entry_ids = base["event_id"].to_numpy()[entry]
    market = base["market_p_radiant"].to_numpy(dtype=np.float64)[entry]
    future = base["signal_market_p_radiant_300s"].to_numpy(dtype=np.float64)[entry]
    realized = future - market
    subset: dict[str, object] = {
        "definition": "research |delta|>=0.02 at L=10, same market seconds, seconds in [0, 480)",
        "rows": int(entry.sum()),
        "events": int(len(set(entry_ids.tolist()))),
        "mean_realized_move": float(realized.mean()),
        "mean_abs_realized_move": float(np.abs(realized).mean()),
    }
    by_lag = {}
    for lag in (8, 10, 16):
        other = frames_at[lag]
        # same row order as base only if align keeps val order and dropna is identical.
        # Re-merge on match_id+second to be sure.
        keyed = other.copy()
        keyed["market_second"] = keyed["second"] + lag
        left = base.loc[entry, ["match_id", "second", "event_id", "market_p_radiant", "signal_market_p_radiant_300s"]].copy()
        left["market_second"] = left["second"] + 10
        right_delta = pd.Series(deltas_at[("research", lag)], index=keyed.index)
        keyed = keyed.assign(delta=right_delta.to_numpy())
        merged = left.merge(
            keyed[["match_id", "market_second", "delta"]],
            on=["match_id", "market_second"],
            how="inner",
        )
        delta = merged["delta"].to_numpy(dtype=np.float64)
        mkt = merged["market_p_radiant"].to_numpy(dtype=np.float64)
        fut = merged["signal_market_p_radiant_300s"].to_numpy(dtype=np.float64)
        direction = np.where(np.clip(mkt + delta, 0.0, 1.0) >= mkt, 1.0, -1.0)
        markout = direction * (fut - mkt)
        events = merged["event_id"].to_numpy()
        by_lag[str(lag)] = {
            "rows": int(len(merged)),
            "dir_markout_300": float(markout.mean()),
            "dir_markout_300_ci": _ci(markout, events),
            "mean_abs_delta": float(np.abs(delta).mean()),
            "still_entry_share": float((np.abs(delta) >= MIN_ABS_DELTA).mean()),
        }
    subset["by_lag"] = by_lag
    l10 = by_lag["10"]["dir_markout_300"]
    subset["markout_ratio_vs_L10"] = {
        "8": by_lag["8"]["dir_markout_300"] / l10 if l10 else None,
        "16": by_lag["16"]["dir_markout_300"] / l10 if l10 else None,
    }
    payload = {"window": "market second in [0, 480), status ok, label present", "rows": rows, "entry_subset": subset}
    OUT.write_text(json.dumps({"scored": payload}, indent=2))
    print("wrote partial", OUT, flush=True)
    return payload


def oddin_feature_ages() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for directory in sorted(p for p in TRADER.iterdir() if p.is_dir()):
        meta_path = directory / "match.json"
        archive = directory / ODDIN_STATE_ARCHIVE_FILENAME
        if not meta_path.is_file():
            continue
        if not archive.exists() and not Path(str(archive) + ".gz").exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("feed_source") != "oddin":
            continue
        market = meta.get("market") or {}
        reducer = OddinSnapshotReducer(int(meta["map_number"]), bool(market.get("yes_is_radiant", True)))
        ages: list[float] = []
        first_positive: int | None = None
        try:
            for event in replay_oddin_records(iter_oddin_archive_records(archive), reducer):
                snap = event.snapshot
                if first_positive is None and snap.second > 0 and not snap.finished:
                    first_positive = int(snap.second)
                if snap.paused or snap.finished or snap.second <= 0 or snap.second > 540:
                    continue
                received = parse_utc(event.received_at_utc).timestamp()
                ages.append(received - (event.horn_unix_seconds + snap.second))
        except Exception as exc:
            found.append({"archive": directory.name, "error": type(exc).__name__})
            continue
        if len(ages) < 30:
            continue
        found.append(
            {
                "archive": directory.name,
                "match_id": meta.get("match_id"),
                "steam_match_id": meta.get("steam_match_id"),
                "n": len(ages),
                "median_age_s": float(statistics.median(ages)),
                "p90_age_s": float(np.percentile(ages, 90)),
                "first_positive_second": first_positive,
            }
        )
    found.sort(key=lambda row: float(row.get("median_age_s", 0)), reverse=True)
    return found


def oddin_horn_maps(ages: list[dict[str, object]]) -> list[dict[str, object]]:
    catalog = pd.read_parquet(
        MATCH_CATALOG_PATH,
        columns=["match_id", "condition_id", "horn_at", "horn_source", "pauses_json", "spawn_at", "archive_id", "archive_feed_source"],
    )
    grid = pd.read_parquet(E / "data/new_processed/grid_game_starts/grid_game_windows.parquet")
    grid = grid.drop_duplicates("condition_id").set_index("condition_id")
    archive = catalog[catalog["horn_source"] == "archive"]
    out: list[dict[str, object]] = []
    for row in archive.itertuples(index=False):
        if row.condition_id not in grid.index:
            continue
        pauses = json.loads(row.pauses_json) if isinstance(row.pauses_json, str) else []
        pre = get_paused_seconds_before(pauses, 0)
        if pre <= 0:
            continue
        grid_horn = get_horn_datetime(parse_utc(str(grid.loc[row.condition_id, "spawn_at"])), pauses)
        diff = (parse_utc(str(row.horn_at)) - grid_horn).total_seconds()
        if row.archive_feed_source != "oddin":
            continue
        out.append(
            {
                "match_id": int(row.match_id),
                "archive_id": row.archive_id,
                "horn_diff_s": round(diff, 2),
                "pre_horn_pause_s": pre,
                "pauses": [(int(p["time"]), int(p["duration"])) for p in pauses],
                "diff_plus_pause": round(diff + pre, 2),
            }
        )
    age_by_archive = {str(row.get("archive")): row for row in ages}
    index = pd.read_parquet(
        E / "data/archive_index/index.parquet",
        columns=["archive_id", "game", "feed_source", "admission", "match_id", "delay_s"],
    )
    results = pd.read_parquet(
        E / "data/backtests/dota_maker/LIVE/seed0/results.parquet",
        columns=["match_id"],
    )
    live_ids = set(int(x) for x in results["match_id"].tolist())
    for row in out:
        age = age_by_archive.get(str(row["archive_id"]))
        row["local_first_positive_second"] = None if age is None else age.get("first_positive_second")
        row["local_median_age_s"] = None if age is None else age.get("median_age_s")
        hit = index[index["archive_id"].astype(str) == str(row["archive_id"])]
        row["index_admission"] = None if hit.empty else str(hit.iloc[0]["admission"])
        row["in_live_backtest"] = int(row["match_id"]) in live_ids
        # a positive clock is inside a pre-horn pause only if some pause with time<0
        # still covers that game second. Pause duration is wall time; the game clock
        # stays at pause["time"] for the whole pause, so second>0 is after it.
        first = row["local_first_positive_second"]
        row["first_positive_equals_a_pause_start"] = bool(
            first is not None and any(int(t) == int(first) for t, _dur in row["pauses"])
        )
    return out


def main() -> None:
    print("loading", flush=True)
    val, feat = load_window()
    print(f"window rows {len(val)} feature keys {len(feat)}", flush=True)
    scored = score_models(val, feat)
    print("oddin ages", flush=True)
    ages = oddin_feature_ages()
    horns = oddin_horn_maps(ages)
    worst = [row for row in ages if float(row.get("median_age_s", 0)) >= 60]
    index = pd.read_parquet(
        E / "data/archive_index/index.parquet",
        columns=["archive_id", "admission", "feed_source", "match_id", "delay_s"],
    )
    results = pd.read_parquet(
        E / "data/backtests/dota_maker/LIVE/seed0/results.parquet",
        columns=["match_id"],
    )
    live_ids = set(int(x) for x in results["match_id"].tolist())
    for row in worst:
        hit = index[index["archive_id"].astype(str) == str(row["archive"])]
        row["index_rows"] = int(len(hit))
        row["admission"] = None if hit.empty else str(hit.iloc[0]["admission"])
        row["index_match_id"] = None if hit.empty else str(hit.iloc[0]["match_id"])
        mid = row.get("match_id")
        row["in_live_backtest"] = int(mid) in live_ids if mid is not None else False
    payload = {
        "scored": scored,
        "oddin_age_worst": worst,
        "oddin_age_p50_of_medians": float(statistics.median([float(r["median_age_s"]) for r in ages if "median_age_s" in r])),
        "oddin_maps_scanned": len(ages),
        "oddin_horn_maps": horns,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print(OUT, flush=True)


if __name__ == "__main__":
    main()
