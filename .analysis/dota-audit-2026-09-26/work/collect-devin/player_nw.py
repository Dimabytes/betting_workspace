"""Per-player NW live-GRID vs STRATZ for 9003856182 at a few seconds."""
import gzip
import json
import sys

from shared.utils.json_io import read_gzip_json
from shared.utils.stratz import stratz_match_cache_path
from trader.grid_widgets import parse_frame, TABLE_SERVICE, read_net_worth
from trader.grid_archive import iter_grid_archive_records
from pathlib import Path

mid = 9003856182
d = Path("data/trader/grid-3007267-m3")

# find a couple of table frames; print raw player rows
records = list(iter_grid_archive_records(d / "grid_state.jsonl.gz"))
printed = 0
for rec in records[300:1200]:
    frame = parse_frame(rec["frame"])
    if frame.service != TABLE_SERVICE:
        continue
    table = read_net_worth(frame.payload, frame.delay)
    if table is None:
        continue
    print("table delay:", frame.delay, "game_number:", table.game_number)
    for p in table.players:
        print(f"   team={p.team_id} player={p.player_id if hasattr(p,'player_id') else '?'} nw={p.net_worth} level={p.level} deaths={p.deaths}")
    printed += 1
    if printed >= 2:
        break
