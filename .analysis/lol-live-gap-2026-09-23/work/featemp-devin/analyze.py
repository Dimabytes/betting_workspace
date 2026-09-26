"""Align GRID (live) and livestats (train) feature series per map; measure skew.

Vectorized pairing: for an offset d, each livestats second S pairs with the
nearest GRID sample at second ~ S+d within TOL. Writes:
  offsets.csv   per-map clock offset estimates (deaths / gold curve / horn)
  featdiff.csv  per-map per-feature diff stats at aligned and nominal pairing
  leadlag.csv   per-map per-feature argmax cross-correlation shift
  pairs.parquet aligned pairs (per ls second) for the model step
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

W = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/"
    ".analysis/lol-live-gap-2026-09-23/work/featemp-devin"
)
MAPS = W / "maps"
OUT = W / "out"
OUT.mkdir(exist_ok=True)

FEATURES = [
    "radiant_nw_adv", "radiant_nw", "dire_nw", "radiant_xp_adv",
    "deaths_radiant", "deaths_dire", "top1_nw_adv",
    "radiant_top1_nw_ratio", "dire_top1_nw_ratio",
]
OFFSET_LO, OFFSET_HI = -40, 40
TOL = 1.6


def death_steps(times: np.ndarray, values: np.ndarray) -> dict[int, float]:
    out: dict[int, float] = {}
    prev = 0
    for t, v in zip(times, values):
        v = int(v)
        if v <= prev:
            continue
        for k in range(prev + 1, v + 1):
            out[k] = float(t)
        prev = v
    return out


def death_offset(gsec: np.ndarray, ls_sec: np.ndarray,
                 gdeaths: np.ndarray, ldeaths: np.ndarray) -> tuple[float, int, float]:
    offsets: list[float] = []
    g = death_steps(gsec, gdeaths)
    l = death_steps(ls_sec, ldeaths)
    for k in sorted(set(g) & set(l)):
        offsets.append(g[k] - l[k])
    if len(offsets) < 3:
        return (float("nan"), len(offsets), float("nan"))
    return (float(np.median(offsets)), len(offsets), float(max(offsets) - min(offsets)))


class GridIndex:
    """Sorted GRID seconds for vectorized nearest lookups."""

    def __init__(self, grid: pd.DataFrame) -> None:
        order = np.argsort(grid["second"].to_numpy(float))
        self.order = order
        self.sec = grid["second"].to_numpy(float)[order]
        self.grid = grid

    def pair(self, ls_sec: np.ndarray, offset: float, tol: float = TOL) -> tuple[np.ndarray, np.ndarray]:
        """Return (grid row positions, gap) per ls row; position -1 = unmatched."""
        target = ls_sec + offset
        pos = np.searchsorted(self.sec, target)
        n = len(self.sec)
        idx_right = np.clip(pos, 0, n - 1)
        idx_left = np.clip(pos - 1, 0, n - 1)
        gap_right = np.abs(self.sec[idx_right] - target)
        gap_left = np.abs(self.sec[idx_left] - target)
        use_left = gap_left <= gap_right
        pick = np.where(use_left, idx_left, idx_right)
        gap = np.where(use_left, gap_left, gap_right)
        pick = np.where(gap <= tol, pick, -1)
        gap = np.where(gap <= tol, gap, np.nan)
        return self.order[np.clip(pick, 0, n - 1)], np.where(pick >= 0, gap, np.nan), pick


def pairs_frame(gi: GridIndex, ls: pd.DataFrame, offset: float, tol: float = TOL) -> pd.DataFrame:
    ls_sec = ls["second"].to_numpy(float)
    gpos, gap, pick = gi.pair(ls_sec, offset, tol)
    mask = pick >= 0
    rows = {"ls_second": ls_sec[mask], "grid_second": gi.sec[pick[mask]], "gap": gap[mask]}
    grid = gi.grid
    for col in ("event_index", "received_at_utc", "state_wall_us", "paused"):
        if col == "state_wall_us":
            rows["state_wall_us"] = ls["state_wall_us"].to_numpy()[mask] \
                if "state_wall_us" in ls else np.full(mask.sum(), np.nan)
        elif col in grid.columns:
            rows[f"g_{col}"] = grid[col].to_numpy()[gpos[mask]]
    for f in FEATURES:
        rows[f"ls_{f}"] = ls[f].to_numpy(float)[mask]
        rows[f"g_{f}"] = grid[f].to_numpy(float)[gpos[mask]]
    return pd.DataFrame(rows)


def curve_offset_cost(gi: GridIndex, ls: pd.DataFrame, offset: float) -> tuple[float, int]:
    ls_sec = ls["second"].to_numpy(float)
    gpos, _, pick = gi.pair(ls_sec, offset, tol=2.0)
    mask = pick >= 0
    if mask.sum() < 30:
        return (float("inf"), int(mask.sum()))
    diff = np.abs(
        gi.grid["radiant_nw_adv"].to_numpy(float)[gpos[mask]]
        - ls["radiant_nw_adv"].to_numpy(float)[mask]
    )
    return (float(np.median(diff)), int(mask.sum()))


def leadlag(gi: GridIndex, ls: pd.DataFrame, feature: str) -> tuple[int, float, int]:
    best = (0, -2.0, 0)
    a = ls[feature].to_numpy(float)
    b = gi.grid[feature].to_numpy(float)
    ls_sec = ls["second"].to_numpy(float)
    for shift in range(-15, 16):
        gpos, _, pick = gi.pair(ls_sec, float(shift), tol=1.6)
        mask = pick >= 0
        if mask.sum() < 60:
            continue
        x = a[mask]
        y = b[gpos[mask]]
        if x.std() < 1e-9 or y.std() < 1e-9:
            continue
        corr = float(np.corrcoef(x, y)[0, 1])
        if corr > best[1]:
            best = (shift, corr, int(mask.sum()))
    return best


def main() -> None:
    only = set(sys.argv[1:])
    offset_rows, feat_rows, leadlag_rows = [], [], []
    pair_frames = []
    for pf in sorted(MAPS.glob("*.parquet")):
        match = pf.stem
        if only and match not in only:
            continue
        meta_path = pf.with_suffix(".meta.json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("status") != "ok":
            continue
        df = pd.read_parquet(pf)
        grid = df[df.src == "grid"].reset_index(drop=True)
        ls = df[df.src == "ls"].reset_index(drop=True)
        if grid.empty or ls.empty:
            continue
        gi = GridIndex(grid)
        ls_sec = ls["second"].to_numpy(float)
        gsec = gi.sec
        d_off, d_n, d_spread = death_offset(
            gsec, ls_sec,
            grid["deaths_radiant"].to_numpy(float) + grid["deaths_dire"].to_numpy(float),
            ls["deaths_radiant"].to_numpy(float) + ls["deaths_dire"].to_numpy(float),
        )
        scored = [(off, *curve_offset_cost(gi, ls, float(off)))
                  for off in range(OFFSET_LO, OFFSET_HI + 1)]
        usable = [r for r in scored if r[1] != float("inf")]
        g_off, g_cost, g_n = min(usable, key=lambda r: r[1]) if usable else (float("nan"), float("nan"), 0)
        horn = grid["horn_unix_seconds"].dropna()
        horn_zero = float(horn.iloc[0] - meta["spawn_wall_seconds"]) if len(horn) else float("nan")
        chosen = d_off if (d_n >= 3 and not math.isnan(d_off)) else g_off
        offset_rows.append(dict(
            match=match, death_offset=d_off, death_n=d_n, death_spread=d_spread,
            gold_offset=g_off, gold_cost=g_cost, gold_n=g_n,
            horn_minus_spawn_wall=horn_zero, chosen_offset=chosen,
            n_grid=len(grid), n_ls=len(ls), pause_seconds=meta.get("pause_seconds"),
        ))
        if math.isnan(chosen):
            continue
        pairs = pairs_frame(gi, ls, chosen)
        pairs["match"] = match
        pair_frames.append(pairs)
        nominal = pairs_frame(gi, ls, 0.0)
        for f in FEATURES:
            for tag, p in (("aligned", pairs), ("nominal", nominal)):
                if p.empty:
                    continue
                a = p[f"ls_{f}"].to_numpy(float)
                b = p[f"g_{f}"].to_numpy(float)
                d = b - a
                corr = float(np.corrcoef(a, b)[0, 1]) if a.std() > 1e-9 and b.std() > 1e-9 else float("nan")
                feat_rows.append(dict(
                    match=match, feature=f, pairing=tag, n=len(p),
                    mean_diff=float(d.mean()), med_abs=float(np.median(np.abs(d))),
                    p90_abs=float(np.quantile(np.abs(d), 0.9)), corr=corr,
                ))
        for f in ("radiant_nw_adv", "radiant_xp_adv", "deaths_radiant", "deaths_dire"):
            sh, corr, nn = leadlag(gi, ls, f)
            leadlag_rows.append(dict(match=match, feature=f, argmax_shift=sh, corr=corr, n=nn))

    pd.DataFrame(offset_rows).to_csv(OUT / "offsets.csv", index=False)
    pd.DataFrame(feat_rows).to_csv(OUT / "featdiff.csv", index=False)
    pd.DataFrame(leadlag_rows).to_csv(OUT / "leadlag.csv", index=False)
    if pair_frames:
        pd.concat(pair_frames, ignore_index=True).to_parquet(OUT / "pairs.parquet")
    o = pd.DataFrame(offset_rows)
    print("maps:", len(o))
    print(o[["death_offset", "gold_offset", "horn_minus_spawn_wall", "death_n"]].describe().to_string())


if __name__ == "__main__":
    main()
