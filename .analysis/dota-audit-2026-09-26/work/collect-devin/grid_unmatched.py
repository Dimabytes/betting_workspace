"""Why do OpenDota links fail GRID window matching? Decompose the misses."""
import json
from pathlib import Path

import pandas as pd

from shared.utils.parsing import parse_ts

P = "data/new_processed"
links = pd.read_parquet(f"{P}/match_links/match_links.parquet")
win = pd.read_parquet(f"{P}/grid_game_starts/grid_game_windows.parquet")
matched = set(win["condition_id"])

linked = links[links["grid_clock_seconds"].notna()].copy()
unmatched = linked[~linked["map_condition_id"].isin(matched)]
print(f"clock-linked: {len(linked)}, unmatched: {len(unmatched)}")

# Load all series_state games once.
sd = Path("data/raw/polymarket_dota/grid_game_starts/series_state")
games = []  # (clock_seconds, spawn_ts, finished)
for p in sorted(sd.glob("*.json")):
    payload = json.load(open(p))
    ss = (payload.get("data") or {}).get("seriesState")
    if ss is None:
        continue
    for g in ss.get("games") or []:
        spawn = parse_ts(g.get("startedAt"))
        games.append({
            "clock": (g.get("clock") or {}).get("currentSeconds"),
            "spawn": spawn,
            "finished": g.get("finished"),
        })
print("grid games in cache:", len(games))

by_clock = {}
for g in games:
    by_clock.setdefault(g["clock"], []).append(g)

exact = 0
clock_off = []  # min |clock - grid_clock| when a spawn-close game exists
no_game_in_spawn_window = 0
spawn_window_only = 0  # clock matches but spawn outside [start, start+1800]
for row in unmatched.itertuples(index=False):
    dur = int(row.grid_clock_seconds)
    start = int(row.match_start_time)
    cands = by_clock.get(dur, [])
    in_window = [g for g in cands if g["spawn"] and 0 <= g["spawn"] - start <= 1800]
    if in_window:
        spawn_window_only += 1  # would have matched -> shouldn't happen
        continue
    # nearest clock among games whose spawn is in the window
    near = [g for g in games if g["spawn"] and 0 <= g["spawn"] - start <= 1800]
    if not near:
        no_game_in_spawn_window += 1
        continue
    deltas = [abs(g["clock"] - dur) for g in near if g["clock"] is not None]
    clock_off.append(min(deltas) if deltas else None)

print(f"would-have-matched (unexpected): {spawn_window_only}")
print(f"no GRID game with spawn within [start, start+1800]: {no_game_in_spawn_window}")
print(f"spawn-window game exists but clock != opendota duration: {len(clock_off)}")
import collections
print("min |grid_clock - opendota_duration| histogram:", collections.Counter(clock_off).most_common(20))
