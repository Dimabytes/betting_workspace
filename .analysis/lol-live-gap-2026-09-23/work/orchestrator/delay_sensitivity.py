"""Offline edge of the research model when the market input and label start d seconds after the state.

LoL rows: state at game second S, market as-of the frame wall time (d=0 is the LoL backtest/training
assumption). Dota rows: market second M with state at M-10 (d=0 is the Dota backtest assumption).
For delay d we take game features from row S and market_p / 300s label from row S+d of the same map.
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from shared.utils.gbm import load_predictor, FEATURE_COLUMNS
from shared.constants.lol import LOL_VALIDATION_PATH, LOL_RESEARCH_MODEL_DIR
from shared.constants.paths import VALIDATION_DATASET_PATH, RESEARCH_MODEL_DIR

GAME_COLS = [c for c in FEATURE_COLUMNS if c not in ("market_p_radiant",)]
DELAYS = [0, 3, 5, 8, 10, 12, 15, 20, 30]


def evaluate(name: str, path: Path, model_dir: Path, lo: int, hi: int, max_maps: int, second_shift: int) -> None:
    cols = ["match_id", "second", "market_p_radiant", "signal_market_p_radiant_300s", *[c for c in GAME_COLS if c != "second"]]
    frame = pd.read_parquet(path, columns=cols)
    maps = np.sort(frame["match_id"].unique())
    rng = np.random.default_rng(0)
    if len(maps) > max_maps:
        maps = rng.choice(maps, size=max_maps, replace=False)
    frame = frame[frame["match_id"].isin(maps)].sort_values(["match_id", "second"])
    predictor = load_predictor(model_dir)
    base = frame[(frame["second"] >= lo) & (frame["second"] <= hi)]
    keyed = frame.set_index(["match_id", "second"])[["market_p_radiant", "signal_market_p_radiant_300s"]]
    print(f"== {name}: maps={frame['match_id'].nunique()} rows_in_window={len(base)} model={model_dir}")
    print(" d  rows    corr   edge@2c(c)  n@2c   edge@3c(c)  n@3c  mean|pred|(c)")
    for d in DELAYS:
        idx = pd.MultiIndex.from_arrays([base["match_id"].to_numpy(), base["second"].to_numpy() + d])
        later = keyed.reindex(idx)
        ok = later["market_p_radiant"].notna().to_numpy() & later["signal_market_p_radiant_300s"].notna().to_numpy()
        rows = base[ok].copy()
        rows["market_p_radiant"] = later["market_p_radiant"].to_numpy()[ok]
        realized = later["signal_market_p_radiant_300s"].to_numpy()[ok] - rows["market_p_radiant"].to_numpy()
        feats = rows[FEATURE_COLUMNS].assign(second=rows["second"] - second_shift)
        pred = np.asarray(predictor.predict(feats), dtype=float)
        corr = float(np.corrcoef(pred, realized)[0, 1])
        out = [f"{d:2d} {len(rows):7d} {corr:7.4f}"]
        for gate in (0.02, 0.03):
            m = np.abs(pred) >= gate
            edge = 100 * float(np.mean(np.sign(pred[m]) * realized[m])) if m.any() else float("nan")
            out.append(f"{edge:10.3f} {int(m.sum()):6d}")
        out.append(f"{100*float(np.mean(np.abs(pred))):10.3f}")
        print("  ".join(out), flush=True)


if __name__ == "__main__":
    max_maps = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    evaluate("lol", LOL_VALIDATION_PATH, LOL_RESEARCH_MODEL_DIR, 0, 540, max_maps, 0)
    evaluate("dota", VALIDATION_DATASET_PATH, RESEARCH_MODEL_DIR, 10, 610, max_maps, 10)
