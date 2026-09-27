"""Find trader archives with GRID replay coverage of early game seconds."""
import gzip
import json
from pathlib import Path

import pandas as pd

from shared.utils.stratz import stratz_match_cache_path
from trader.game_profile import GAME_PROFILES
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer, replay_grid_records
from trader.live_feed import FeedEvent

cat = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet")
cat_ids = set(cat["match_id"].astype(int))

trader = Path("data/trader")
rows = []
for d in sorted(trader.iterdir()):
    mj = d / "match.json"
    gz = d / "grid_state.jsonl.gz"
    if not mj.exists() or not gz.exists():
        continue
    meta = json.loads(mj.read_text())
    smid = meta.get("steam_match_id")
    if smid is None or not str(smid).isdigit() or meta.get("feed_source") != "grid":
        continue
    mid = int(smid)
    if mid not in cat_ids or not stratz_match_cache_path(mid).exists():
        continue
    rows.append((mid, d, meta, gz.stat().st_size))

rows.sort(key=lambda r: -r[3])
print("archives by size:", [(r[0], r[3] // 1024, r[2].get('joined_at_second')) for r in rows[:15]])

best = None
for mid, d, meta, size in rows[:30]:
    reducer = GridFrameReducer(
        meta["map_number"], meta["market"]["outcome_0_name"], meta["market"]["outcome_1_name"],
        GAME_PROFILES["dota"],
    )
    records = list(iter_grid_archive_records(d / "grid_state.jsonl.gz"))
    events = [e for e in replay_grid_records(records, reducer) if isinstance(e, FeedEvent)]
    secs = [e.snapshot.second for e in events if e.snapshot.phase.name == "IN_PROGRESS" and e.snapshot.second < 1500]
    live_secs = [s for s in secs if 0 <= s <= 1500]
    print(f"{mid}: records={len(records)} events={len(events)} in-window secs={len(live_secs)}")
    if best is None or len(live_secs) > best[0]:
        best = (len(live_secs), mid, str(d))
print("BEST:", best)
