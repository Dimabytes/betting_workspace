"""Model impact of GRID-vs-livestats feature skew, per real map.

For every grid event that produced a 'model' signal row (market inputs known):
  sanity:  predict_fair(grid features, signal market inputs) with the map's own
           live model == signal.radiant_fair
  skew:    same market inputs, production model:
             A) nominal  - ls features at the same labeled second S
             B) aligned  - ls features at second S - delta (true-time match)
  report distribution of (fair_live - fair_train) in cents, sign flips, and
  how often |delta| >= 0.02 opens on one side only.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
W = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/"
    ".analysis/lol-live-gap-2026-09-23/work/featemp-devin"
)
MAPS = W / "maps"
OUT = W / "out"

FEATURE_ORDER = [
    "second", "radiant_nw_adv", "radiant_nw", "dire_nw", "radiant_xp_adv",
    "deaths_radiant", "deaths_dire", "top1_nw_adv",
    "radiant_top1_nw_ratio", "dire_top1_nw_ratio",
    "market_radiant_prior", "market_p_radiant",
]
GAME_FEATURES = FEATURE_ORDER[1:10]
MIN_ABS_DELTA = 0.02


def model_dirs() -> dict[str, Path]:
    out = {}
    prod = E / "data/lol/models/production"
    meta = json.loads((prod / "model.json").read_text())
    out[meta["name"]] = prod
    for d in (E / "data/lol/models/archive/production").iterdir():
        if (d / "model.json").exists():
            out[d.name] = d
    return out


def match_signals(grid: pd.DataFrame, sig: pd.DataFrame) -> pd.DataFrame:
    """Assign each signal row to the earliest unconsumed grid event with equal second."""
    sig = sig[(sig.second.notna())].copy()
    by_second: dict[int, list[int]] = {}
    for i, s in enumerate(grid["second"].to_numpy()):
        by_second.setdefault(int(s), []).append(i)
    rows = []
    for r in sig.itertuples():
        s = int(r.second)
        bucket = by_second.get(s)
        if not bucket:
            rows.append(dict(row_index=int(r.row_index), second=s, event_index=-1,
                             market_p_radiant=r.market_p_radiant,
                             market_radiant_prior=r.market_radiant_prior,
                             radiant_fair=r.radiant_fair, yes_fair=r.yes_fair,
                             reason=r.reason))
            continue
        ei = bucket.pop(0)
        rows.append(dict(row_index=int(r.row_index), second=s, event_index=ei,
                         market_p_radiant=r.market_p_radiant,
                         market_radiant_prior=r.market_radiant_prior,
                         radiant_fair=r.radiant_fair, yes_fair=r.yes_fair,
                         reason=r.reason))
    return pd.DataFrame(rows)


def predict_row(predictor, feats: dict) -> float:
    row = np.asarray([[float(feats[c]) for c in FEATURE_ORDER]], dtype=np.float64)
    return float(predictor.predict(row)[0])


class SingleBooster:
    """Pre-ensemble catalog adapter: one model.txt booster with .predict."""

    def __init__(self, booster) -> None:
        self._booster = booster

    def predict(self, data):
        import numpy as np
        return np.asarray(self._booster.predict(data), dtype=np.float64)


def load_any(model_dir: Path):
    from shared.utils.gbm import ModelCatalogError, load_predictor
    try:
        return load_predictor(model_dir)
    except ModelCatalogError:
        import lightgbm as lgb
        return SingleBooster(lgb.Booster(model_file=str(model_dir / "model.txt")))


def main() -> None:
    only = set(sys.argv[1:])
    dirs = model_dirs()
    prod_name = json.loads((E / "data/lol/models/production/model.json").read_text())["name"]
    predictors: dict[str, object] = {}

    def get(name: str):
        if name not in predictors:
            predictors[name] = load_any(dirs[name])
        return predictors[name]

    offsets = pd.read_csv(OUT / "offsets.csv").set_index("match")
    sanity_rows, diff_rows = [], []
    for pf in sorted(MAPS.glob("*.parquet")):
        match = pf.stem
        if only and match not in only:
            continue
        meta_p = pf.with_suffix(".meta.json")
        if not meta_p.exists():
            continue
        meta = json.loads(meta_p.read_text())
        if meta.get("status") != "ok" or match not in offsets.index:
            continue
        delta = offsets.loc[match, "chosen_offset"]
        df = pd.read_parquet(pf)
        grid = df[df.src == "grid"].reset_index(drop=True)
        ls = df[df.src == "ls"].set_index("second")
        sig = df[df.src == "signal"]
        pairs = match_signals(grid, sig)
        own_model = meta.get("model")
        for pr in pairs.itertuples():
            if pr.event_index < 0 or pr.reason != "model":
                continue
            if pr.market_p_radiant is None or pr.market_radiant_prior is None:
                continue
            if pr.radiant_fair is None or not math.isfinite(pr.radiant_fair):
                continue
            ev = grid.iloc[pr.event_index]
            s = int(ev["second"])
            if s < 0 or s > 720:
                continue
            gfeat = {f: float(ev[f]) for f in GAME_FEATURES}
            gfeat["second"] = float(s)
            gfeat["market_radiant_prior"] = float(pr.market_radiant_prior)
            gfeat["market_p_radiant"] = float(pr.market_p_radiant)
            # sanity: map's own model reproduces live radiant_fair
            if own_model in dirs:
                d_own = predict_row(get(own_model), gfeat)
                fair_own = min(1.0, max(0.0, pr.market_p_radiant + d_own))
                sanity_rows.append(dict(
                    match=match, second=s, model=own_model,
                    fair_live=float(pr.radiant_fair), fair_replay=fair_own,
                    err=fair_own - float(pr.radiant_fair),
                ))
            # skew: production model on grid vs livestats features
            ls_nom = ls.loc[s] if s in ls.index else None
            aligned_s = int(round(s - delta))
            ls_al = ls.loc[aligned_s] if aligned_s in ls.index else None
            d_prod_g = predict_row(get(prod_name), gfeat)
            rec = dict(match=match, second=s, delta_grid=d_prod_g,
                       market_p_radiant=float(pr.market_p_radiant),
                       market_radiant_prior=float(pr.market_radiant_prior))
            if ls_nom is not None:
                lfeat = {f: float(ls_nom[f]) for f in GAME_FEATURES}
                lfeat["second"] = float(s)
                lfeat["market_radiant_prior"] = gfeat["market_radiant_prior"]
                lfeat["market_p_radiant"] = gfeat["market_p_radiant"]
                rec["delta_ls_nominal"] = predict_row(get(prod_name), lfeat)
            if ls_al is not None:
                afeat = {f: float(ls_al[f]) for f in GAME_FEATURES}
                afeat["second"] = float(s)
                afeat["market_radiant_prior"] = gfeat["market_radiant_prior"]
                afeat["market_p_radiant"] = gfeat["market_p_radiant"]
                rec["delta_ls_aligned"] = predict_row(get(prod_name), afeat)
            diff_rows.append(rec)

    pd.DataFrame(sanity_rows).to_csv(OUT / "sanity.csv", index=False)
    pd.DataFrame(diff_rows).to_csv(OUT / "model_diff.csv", index=False)

    s = pd.DataFrame(sanity_rows)
    if not s.empty:
        print("sanity: n=%d  max|err|=%.2e  mean|err|=%.2e  exact(1e-9)=%.4f" % (
            len(s), s.err.abs().max(), s.err.abs().mean(), (s.err.abs() < 1e-9).mean()))
        print(s.groupby("model").err.apply(lambda x: x.abs().max()).to_string())
    d = pd.DataFrame(diff_rows)
    if not d.empty:
        for tag in ("nominal", "aligned"):
            col = f"delta_ls_{tag}"
            v = d.dropna(subset=[col])
            dd = (v["delta_grid"] - v[col]) * 100
            print(f"\n{tag}: n={len(v)}  pred diff (cents) mean={dd.mean():+.3f} "
                  f"median={dd.median():+.3f} p10={dd.quantile(.1):+.3f} p90={dd.quantile(.9):+.3f} "
                  f"med|d|={dd.abs().median():.3f} p90|d|={dd.abs().quantile(.9):.3f}")
            sign_flip = ((v["delta_grid"] * v[col]) < 0).mean()
            g_open = v["delta_grid"].abs() >= MIN_ABS_DELTA
            l_open = v[col].abs() >= MIN_ABS_DELTA
            print(f"   sign flips: {100*sign_flip:.1f}%  gate opens one side only: "
                  f"{100*(g_open ^ l_open).mean():.1f}%  "
                  f"(grid-only {100*(g_open & ~l_open).mean():.1f}%, ls-only {100*(l_open & ~g_open).mean():.1f}%)")


if __name__ == "__main__":
    main()
