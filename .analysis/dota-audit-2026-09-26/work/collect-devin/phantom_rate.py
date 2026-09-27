"""How often do GRID tables carry >5 players per side (phantom roster rows)?"""
import json
import random
from collections import Counter
from pathlib import Path

from trader.grid_archive import iter_grid_archive_records
from trader.grid_widgets import TABLE_SERVICE, parse_frame, read_net_worth

trader = Path("data/trader")
archives = [
    p for p in sorted(trader.iterdir())
    if (p / "grid_state.jsonl.gz").exists() and (p / "match.json").exists()
]
random.seed(0)
sample = random.sample(archives, 60)

bad = 0
good = 0
errors = 0
detail = []
for d in sample:
    meta = json.loads((d / "match.json").read_text())
    if meta.get("feed_source") != "grid":
        continue
    counts = Counter()
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
            side_counts = Counter(p.team_id for p in t.players)
            for tid, c in side_counts.items():
                counts[c] += 1  # count of sides with `c` players
    except Exception as e:
        errors += 1
        print(d.name, "err", e)
        continue
    # a table is phantom-bearing if any side has != 5 players
    phantom = any(c != 5 for c in counts)
    if phantom:
        bad += 1
        detail.append((d.name, dict(counts)))
    else:
        good += 1

print(f"archives sampled={len(sample)} good={good} phantom-bearing={bad} errors={errors}")
for name, c in detail[:15]:
    print(" ", name, c)
