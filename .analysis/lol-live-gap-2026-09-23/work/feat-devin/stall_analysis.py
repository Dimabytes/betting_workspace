"""Was the m4 hole a table-content freeze or a transport stall?"""

import gzip
import json
import statistics
from pathlib import Path
from typing import cast

from shared.utils.match_time import parse_utc
from trader.grid_widgets import SCOREBOARD_SERVICE, TABLE_SERVICE, parse_frame

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TAPE = E / "data/trader/grid-3000372-m4"

records = []
with gzip.open(TAPE / "grid_state.jsonl.gz", "rt") as handle:
    records = [json.loads(l) for l in handle if l.strip()]

# arrival timeline per service
events = []
for r in records:
    t = parse_utc(r["received_at_utc"]).timestamp()
    f = parse_frame(r["frame"])
    events.append((t, f.service, f.payload))

t0 = events[0][0]
print("total records", len(records))
prev_t = None
for t, svc, payload in events:
    rel = t - t0
    if prev_t is not None and t - prev_t > 5:
        print(f"GAP {t - prev_t:.0f}s ending at rel={rel:.0f} on {svc}")
    prev_t = t

# distinct sequenceNumbers / state count in Game group around the hole
seen_seqs = []
for t, svc, payload in events:
    if svc != TABLE_SERVICE or not payload:
        continue
    table = cast(dict, json.loads(payload))
    for group in table["stateGroups"]:
        if group["name"] != "Game":
            continue
        seqs = [s["sequenceNumber"] for s in group["states"]]
        seen_seqs.append((t - t0, len(group["states"]), seqs))
last = None
for rel, n, seqs in seen_seqs:
    if seqs != last:
        print(f"rel={rel:7.0f} states={n} seqs={seqs}")
        last = seqs
