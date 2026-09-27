"""Event study: at which cache lag does the market react to STRATZ kills, for maps with and without pre-horn pauses.

The market-seconds cache is keyed by game second under the catalog horn. If the catalog horn is late by the
pre-horn pause D, the market reaction to a kill at STRATZ second k shows up at cache second k - D + delta.
If the catalog horn is right, it shows up at k + delta (delta = market reaction, a few seconds).
"""
import json
import sys

import numpy as np
import pandas as pd

from market_data.build_market_data import market_seconds_cache_path
from shared.utils.match_time import get_paused_seconds_before
from shared.utils.stratz import death_times, get_stratz_match_reach_data

LAGS = np.arange(-600, 181, 5)
STEP = 15  # market move measured over [s, s + STEP]
MAX_MAPS = int(sys.argv[1]) if len(sys.argv) > 1 else 120

c = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet")
c = c[c.horn_source == "archive"].copy()
c["pre"] = [get_paused_seconds_before(json.loads(p) if isinstance(p, str) else [], 0) for p in c.pauses_json]
paused = c[(c.pre >= 20) & (c.pre <= 500)].sample(frac=1.0, random_state=1).head(MAX_MAPS)
control = c[c.pre == 0].sample(frac=1.0, random_state=1).head(MAX_MAPS)


def response_curve(match_id: int) -> np.ndarray | None:
    path = market_seconds_cache_path(int(match_id))
    if not path.exists():
        return None
    m = pd.read_parquet(path, columns=["second", "market_status", "market_p_radiant"])
    p = m.set_index("second").market_p_radiant.where(m.set_index("second").market_status == "ok")
    p = p.reindex(range(int(p.index.min()), int(p.index.max()) + 1)).ffill(limit=5)
    try:
        match = get_stratz_match_reach_data(int(match_id))
    except Exception:
        return None
    kills = [(t, +1.0) for t in death_times(match, radiant=False)] + [(t, -1.0) for t in death_times(match, radiant=True)]
    kills = [(t, s) for t, s in kills if 60 <= t <= 1800]
    if len(kills) < 5:
        return None
    curve = np.full(len(LAGS), np.nan)
    for i, lag in enumerate(LAGS):
        moves = []
        for t, sign in kills:
            a, b = t + lag, t + lag + STEP
            if a in p.index and b in p.index:
                pa, pb = p.loc[a], p.loc[b]
                if pa == pa and pb == pb:
                    moves.append(sign * (pb - pa))
        if moves:
            curve[i] = float(np.mean(moves))
    return curve


def summarize(frame: pd.DataFrame, label: str) -> None:
    peaks, peaks_minus_d, curves = [], [], []
    for _, row in frame.iterrows():
        curve = response_curve(row.match_id)
        if curve is None or np.all(np.isnan(curve)):
            continue
        curves.append(curve)
        peak = int(LAGS[int(np.nanargmax(curve))])
        peaks.append(peak)
        peaks_minus_d.append(peak + int(row.pre))
    peaks = np.array(peaks)
    print(f"{label}: maps={len(peaks)}")
    print("  peak lag (s):", pd.Series(peaks).describe()[["25%", "50%", "75%"]].to_dict())
    print("  peak lag + D (s):", pd.Series(peaks_minus_d).describe()[["25%", "50%", "75%"]].to_dict())
    mean_curve = np.nanmean(np.vstack(curves), axis=0)
    top = np.argsort(-mean_curve)[:5]
    print("  mean-curve top lags:", [(int(LAGS[j]), round(float(mean_curve[j]) * 100, 3)) for j in top], "(cents)")


summarize(control, "archive-horn control (no pre-horn pause)")
summarize(paused, "archive-horn paused (20 <= D <= 500 s)")
