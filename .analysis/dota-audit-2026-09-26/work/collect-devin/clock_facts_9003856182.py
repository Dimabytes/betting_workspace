"""Clock facts for match 9003856182: STRATZ event clock vs durationSeconds vs GRID."""
import json
from pathlib import Path

import pandas as pd

from shared.utils.json_io import read_gzip_json
from shared.utils.stratz import stratz_match_cache_path

MID = 9003856182

entry = read_gzip_json(stratz_match_cache_path(MID))
match = entry["data"]["match"]
dur = match["durationSeconds"]
print("startDateTime", match["startDateTime"], "endDateTime", match["endDateTime"],
      "durationSeconds", dur, "end-start", match["endDateTime"] - match["startDateTime"])
print("nw leads len", len(match.get("radiantNetworthLeads") or []),
      "xp leads len", len(match.get("radiantExperienceLeads") or []))

max_t = -10**9
min_t = 10**9
n_ev = 0
for p in match["players"]:
    pb = p.get("playbackData") or {}
    for ev in pb.get("playerUpdateGoldEvents") or []:
        n_ev += 1
        max_t = max(max_t, ev["time"])
        min_t = min(min_t, ev["time"])
    st = p.get("stats") or {}
    for ev in st.get("deathEvents") or []:
        max_t = max(max_t, ev["time"])
    lv = st.get("level") or []
    npm = st.get("networthPerMinute") or []
print("gold events:", n_ev, "min_t", min_t, "max_t", max_t, "dur", dur)
for p in match["players"][:3]:
    st = p.get("stats") or {}
    print("  lvl len", len(st.get("level") or []), "npm len", len(st.get("networthPerMinute") or []),
          "npm last", (st.get("networthPerMinute") or [None])[-1],
          "leaver", p.get("leaverStatus"), "hero", p.get("heroId"), "isRadiant", p.get("isRadiant"))

# what GRID said about this series, if cached
grid_dir = Path("data/raw/polymarket_dota/grid_game_starts/series_state")
links = pd.read_parquet("data/match_links.parquet")
row = links[links.match_id == MID]
print("\nlink row:")
print(row.T.to_string())
