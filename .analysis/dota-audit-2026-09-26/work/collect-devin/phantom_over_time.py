"""Per-table team sizes over time for grid-3007267-m3 and partial-table timing in 2 others."""
import json
from collections import Counter
from pathlib import Path

from trader.grid_archive import iter_grid_archive_records
from trader.grid_widgets import TABLE_SERVICE, parse_frame, read_net_worth

trader = Path("data/trader")

def scan(dirname):
    d = trader / dirname
    meta = json.loads((d / "match.json").read_text())
    horn = meta["horn_at_utc"]
    print(f"== {dirname} horn={horn}")
    i = 0
    for rec in iter_grid_archive_records(d / "grid_state.jsonl.gz"):
        frame = parse_frame(rec["frame"])
        if frame.service != TABLE_SERVICE:
            continue
        t = read_net_worth(frame.payload, frame.delay)
        if t is None:
            continue
        counts = Counter(p.team_id for p in t.players)
        # print only when any side != 5, plus every 200th table
        if any(c != 5 for c in counts.values()) or i % 200 == 0:
            print(f"  table#{i} ts={rec.get('ts')} delay={frame.delay} counts={dict(counts)}")
        i += 1

scan("grid-3007267-m3")
scan("grid-3005971-m2")
