"""Scan all grid archives for roster-size anomalies using only the first 40 tables.

Phantom roster rows (a 6th substitute) persist from table#0, so reading the
first 40 tables per archive is enough to classify the whole archive.
"""
import json
from collections import Counter
from pathlib import Path

from trader.grid_archive import iter_grid_archive_records
from trader.grid_widgets import TABLE_SERVICE, parse_frame, read_net_worth

trader = Path("data/trader")
archives = sorted(
    p for p in trader.iterdir()
    if (p / "grid_state.jsonl.gz").exists() and (p / "match.json").exists()
)
print(f"grid archives: {len(archives)}")

persistent6 = []
transient = []
clean = 0
errors = 0
for d in archives:
    sizes = Counter()
    ntab = 0
    try:
        for rec in iter_grid_archive_records(d / "grid_state.jsonl.gz"):
            frame = parse_frame(rec["frame"])
            if frame.service != TABLE_SERVICE:
                continue
            t = read_net_worth(frame.payload, frame.delay)
            if t is None:
                continue
            ntab += 1
            for c in Counter(p.team_id for p in t.players).values():
                sizes[c] += 1
            if ntab >= 40:
                break
    except Exception:
        errors += 1
        continue
    if ntab == 0:
        errors += 1
        continue
    # dominant size per side across early tables
    if sizes.get(6, 0) >= 5 or sizes.get(7, 0) >= 5:
        persistent6.append((d.name, dict(sizes)))
    elif any(c != 5 for c in sizes):
        transient.append((d.name, dict(sizes)))
    else:
        clean += 1

print(f"clean={clean} persistent-extra-player={len(persistent6)} transient-partial={len(transient)} errors={errors}")
for n, c in persistent6[:25]:
    print("  6+:", n, c)
for n, c in transient[:10]:
    print("  tr:", n, c)
