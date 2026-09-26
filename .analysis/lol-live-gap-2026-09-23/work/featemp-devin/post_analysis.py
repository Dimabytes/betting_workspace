"""Consolidated post-analysis for the featemp-devin brief.

Reproduces, from work/featemp-devin/maps/*.parquet + out/*.csv:
  1. yes_fair conversion check (radiant_fair -> yes_fair via yes_is_radiant)
  2. live signal reason distribution (how often the book/feed allowed a model row)
  3. live stale-share: fraction of the 0..540 window with freshest tick >16s old
  4. live tick rate per grid-v1 cadence band
  5. `second`-convention sensitivity on the production model (S vs S-10 vs S+2.5)
  6. sanity.csv mismatch summary (live radiant_fair replay error)

Run:  cd esports-trader && PYTHONPATH=src uv run python <this file>
"""

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
W = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/"
    ".analysis/lol-live-gap-2026-09-23/work/featemp-devin"
)
STALE = 16.0
BANDS = [(0, 180, 8), (180, 360, 6), (360, 540, 5)]  # grid-v1 LoL mean intervals


def main() -> None:
    maps_dir = W / "maps"
    reasons: Counter[str] = Counter()
    yes_rows = []
    stale_rows = []
    band_ticks = {lo: [0, 0] for lo, _, _ in BANDS}

    for pf in sorted(maps_dir.glob("*.parquet")):
        mp = pf.with_suffix(".meta.json")
        if not mp.exists():
            continue
        meta = json.loads(mp.read_text())
        if meta.get("status") != "ok":
            continue
        df = pd.read_parquet(pf)
        sig = df[df.src == "signal"]
        if "reason" in df.columns:
            for k, v in sig.reason.value_counts().items():
                reasons[str(k)] += int(v)
        if "yes_fair" in df.columns:
            ok = sig[sig.yes_fair.notna() & sig.radiant_fair.notna()]
            if not ok.empty:
                expect = (
                    ok.radiant_fair
                    if bool(meta["yes_is_radiant"])
                    else 1.0 - ok.radiant_fair
                )
                err = (ok.yes_fair - expect).abs()
                yes_rows.append(dict(n=len(ok), max_err=err.max()))
        g = df[(df.src == "grid") & df.second.notna() & (df.second >= 0) & (df.second <= 540)]
        if len(g) >= 10 and g.received_at_utc.notna().mean() > 0.9:
            ts = np.sort(pd.to_datetime(g.received_at_utc).astype("int64").to_numpy() / 1e9)
            gaps = np.diff(ts)
            stale_share = min(np.clip(gaps - STALE, 0, None).sum() / 540.0, 1.0)
            stale_rows.append(
                dict(n=len(g), med_gap=np.median(gaps), max_gap=gaps.max(), stale=stale_share)
            )
        for lo, hi, _m in BANDS:
            band_ticks[lo][0] += int(((g.second >= lo) & (g.second < hi)).sum())
            band_ticks[lo][1] += 1

    y = pd.DataFrame(yes_rows)
    print(f"[yes_fair] maps={len(y)} rows={int(y.n.sum())} max|err|={y.max_err.max():.3e} "
          f"exact maps={(y.max_err < 1e-9).sum()}/{len(y)}")

    tot = sum(reasons.values())
    print(f"\n[signal reasons] n={tot}")
    for k, v in reasons.most_common():
        print(f"  {k:20s} {v:7d} {100 * v / tot:5.1f}%")

    r = pd.DataFrame(stale_rows)
    print(f"\n[live cadence] maps={len(r)}")
    print(f"  ticks/map med={r.n.median():.0f}  med_gap={r.med_gap.median():.1f}s "
          f"max_gap med={r.max_gap.median():.0f}s")
    print(f"  stale share (>16s no tick): mean={r.stale.mean():.3f} med={r.stale.median():.3f} "
          f"p90={r.stale.quantile(.9):.3f}")
    exp = sum((hi - lo) * (1 - 1 / m) ** 16 for lo, hi, m in BANDS) / 540
    print(f"  grid-v1 iid expected stale share ~= {exp:.3f}")
    for lo, hi, m in BANDS:
        tk, mp = band_ticks[lo]
        print(f"  band {lo}-{hi}: live {tk / mp:.1f} ticks/map vs grid-v1 ~{(hi - lo) / m:.0f}")

    print("\n[second-convention sensitivity] production model on 50k validation rows")
    from shared.utils.gbm import FEATURE_COLUMNS, load_predictor
    pred = load_predictor(E / "data/lol/models/production")
    val = pd.read_parquet(E / "data/lol/processed/datasets/validation.parquet")
    rows = val[(val.second >= 0) & (val.second <= 540)][FEATURE_COLUMNS].dropna().sample(
        50000, random_state=0
    )
    d0 = pred.predict(rows)
    for shift in (-10, 2.5):
        X = rows.copy()
        X["second"] = rows["second"] + shift
        d = pred.predict(X)
        dd = (d - d0) * 100
        print(f"  second{shift:+}: cents med|d|={np.median(np.abs(dd)):.4f} "
              f"p90|d|={np.quantile(np.abs(dd), .9):.4f} "
              f"signflip={100 * ((d * d0) < 0).mean():.2f}%")

    s = pd.read_csv(W / "out/sanity.csv")
    bad = s[s.err.abs() > 1e-9]
    print(f"\n[radiant_fair replay] n={len(s)} exact={100 * (s.err.abs() < 1e-9).mean():.4f}% "
          f"mismatch={len(bad)} rows in {bad.match.nunique()} maps "
          f"(all model=20260831T120859Z era; |err| med={bad.err.abs().median() * 100:.2f}c "
          f"max={bad.err.abs().max() * 100:.2f}c)")


if __name__ == "__main__":
    main()
