"""GRID table at late frames for 9003856182 + leaver census over catalog."""
import json
from pathlib import Path

import pandas as pd

from shared.utils.json_io import read_gzip_json
from shared.utils.stratz import stratz_match_cache_path
from trader.grid_widgets import parse_frame, TABLE_SERVICE, read_net_worth, SCOREBOARD_SERVICE, read_map_scoreboard
from trader.grid_archive import iter_grid_archive_records

d = Path("data/trader/grid-3007267-m3")
records = list(iter_grid_archive_records(d / "grid_state.jsonl.gz"))
print("records:", len(records))
# take the last 3 table frames with a scoreboard clock for context
last_board = None
seen = 0
for rec in records:
    frame = parse_frame(rec["frame"])
    if frame.service == SCOREBOARD_SERVICE:
        b = read_map_scoreboard(frame.payload, 3)
        if b is not None:
            last_board = b
    elif frame.service == TABLE_SERVICE and seen < 4 and len(records) > 0:
        pass
# just print last two tables
tables = []
for rec in records:
    frame = parse_frame(rec["frame"])
    if frame.service == TABLE_SERVICE:
        t = read_net_worth(frame.payload, frame.delay)
        if t is not None:
            tables.append((rec["received_at_utc"], t))
for ts, t in tables[-2:]:
    print("table at", ts, "delay", t.feed_delay if hasattr(t, "feed_delay") else "?")
    for p in t.players:
        print(f"   team={p.team_id} nw={p.net_worth} level={p.level} deaths={p.deaths}")

# leaver census over catalog
cat = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet")
leaver_matches = 0
disc_by_mid = []
for mid in cat["match_id"].astype(int):
    p = stratz_match_cache_path(mid)
    if not p.exists():
        continue
    m = read_gzip_json(p)["data"]["match"]
    leavers = [pl for pl in m["players"] if pl["leaverStatus"] != "NONE"]
    if leavers:
        leaver_matches += 1
        disc_by_mid.append(mid)
print("catalog matches with a DISCONNECTED player:", leaver_matches, "of", len(cat))
print("ids:", disc_by_mid[:20])
