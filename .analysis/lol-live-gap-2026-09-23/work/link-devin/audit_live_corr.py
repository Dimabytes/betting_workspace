"""Per-live-map corr(radiant_fair, market_p_radiant) — swap detector.

If GRID bound BLUE to the wrong team for a map, model fair (built from blue-side
stats but attributed to the radiant-named team) anti-tracks the market mid.
Also catches chronic fair-vs-market divergence (systematic mispricing view).
"""

import gzip
import json
import math
from pathlib import Path

import numpy as np

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data/trader"


def open_maybe_gz(path):
    if path.exists():
        return open(path, "rt")
    gz = path.with_suffix(path.suffix + ".gz")
    if gz.exists():
        return gzip.open(gz, "rt")
    return None


rows = []
for d in sorted(TRADER.glob("grid-*")):
    mj = d / "match.json"
    if not mj.exists():
        continue
    try:
        meta = json.loads(mj.read_text())
    except Exception:
        continue
    if meta.get("game") != "lol":
        continue
    fh = open_maybe_gz(d / "session.jsonl")
    if fh is None:
        continue
    mids = []
    fairs = []
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") != "signal":
                continue
            m = rec.get("market_p_radiant")
            f = rec.get("radiant_fair")
            if m is not None and f is not None:
                mids.append(m)
                fairs.append(f)
    if len(mids) < 20:
        continue
    c = float(np.corrcoef(mids, fairs)[0, 1])
    gap = float(np.mean([f - m for m, f in zip(mids, fairs)]))
    rows.append((d.name, c, gap, len(mids),
                 (meta.get("teams") or {}).get("radiant"),
                 (meta.get("teams") or {}).get("dire"),
                 meta.get("map_number"), meta.get("tournament")))

rows.sort(key=lambda r: r[1])
print(f"maps with >=20 priced signals: {len(rows)}")
print("\nworst correlations (fair vs mid):")
for r in rows[:30]:
    print(f"  {r[0]} corr={r[1]:+.2f} gap={r[2]:+.3f} n={r[3]} "
          f"map{r[6]} {r[4]} vs {r[5]} | {r[7]}")
cs = [r[1] for r in rows if math.isfinite(r[1])]
print(f"\ncorr dist: min={min(cs):.2f} p5={np.percentile(cs,5):.2f} "
      f"median={np.median(cs):.2f} p95={np.percentile(cs,95):.2f} max={max(cs):.2f}")
