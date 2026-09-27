"""Catalog STRATZ integrity: player counts, leaver status, durations, sides."""
from collections import Counter

import pandas as pd

from shared.constants.paths import MATCH_CATALOG_PATH
from shared.utils.json_io import read_gzip_json
from shared.utils.stratz import stratz_match_cache_path

cat = pd.read_parquet(MATCH_CATALOG_PATH)
n_players = Counter()
leaver = Counter()
side_counts = Counter()
dur = []
fetched = []
short_games = []
for mid in cat["match_id"].astype(int):
    entry = read_gzip_json(stratz_match_cache_path(int(mid)))
    fetched.append(entry.get("fetched_at"))
    m = (entry.get("data") or {}).get("match") or {}
    players = m.get("players") or []
    n_players[len(players)] += 1
    sides = Counter(bool(p.get("isRadiant")) for p in players)
    side_counts[(sides.get(True, 0), sides.get(False, 0))] += 1
    for p in players:
        leaver[p.get("leaverStatus")] += 1
    d = m.get("durationSeconds")
    dur.append(d)
    if d is not None and d < 900:
        short_games.append((int(mid), d))

print("players per match:", dict(n_players))
print("side split (radiant,dire):", dict(side_counts))
print("leaverStatus:", dict(leaver))
print("duration min/median/max:", min(dur), sorted(dur)[len(dur)//2], max(dur))
print("games <15min:", len(short_games), short_games[:15])
fx = pd.Series(fetched)
print("fetched_at range:", fx.min(), "->", fx.max())
