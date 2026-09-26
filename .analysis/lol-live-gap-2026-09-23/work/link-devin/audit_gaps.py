"""Quantify processed-game gaps: series score vs rows in games.parquet.

team_a_game_wins + team_b_game_wins = official games played. If processed rows
are fewer, the pipeline never saw those games (fetch gap) -> PM markets for them
are silently unlinked, and 'non-contiguous numbering' may actually be shifted
numbering from unprocessed games.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()

games_by_match = defaultdict(list)
for g in games:
    games_by_match[g["esports_match_id"]].append(g)
linked_matches = {l["esports_match_id"] for l in links}
links_by_match = defaultdict(list)
for l in links:
    links_by_match[l["esports_match_id"]].append(l)

uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
uni_by_event = defaultdict(list)
for u in universe:
    uni_by_event[u["event_id"]].append(u)

print("== score vs processed game count ==")
gap_rows = []
for mid, gs in games_by_match.items():
    g0 = gs[0]
    aw, bw = g0["team_a_game_wins"], g0["team_b_game_wins"]
    if aw is None or bw is None:
        continue
    official = aw + bw
    have = len({g["game_number"] for g in gs})
    if official != have:
        gap_rows.append((mid, official, have, sorted(g["game_number"] for g in gs),
                         g0["team_a_name"], g0["team_b_name"], g0["best_of"],
                         mid in linked_matches, g0["league_slug"]))
print(f"matches where official_games != processed_games: {len(gap_rows)} / {len(games_by_match)}")
linked_gaps = [r for r in gap_rows if r[7]]
print(f"...of which linked: {len(linked_gaps)}")
for r in sorted(linked_gaps, key=lambda r: r[0])[:40]:
    print("  ", r)

# For linked gap matches: which PM markets exist for the missing game numbers?
print("\n== PM markets for game numbers missing from processed data ==")
missed = 0
for mid, official, have, nums, ta, tb, bo, linked, slug in sorted(linked_gaps, key=lambda r: r[0]):
    missing_nums = set(range(1, official + 1)) - set(nums)
    ev_ids = {l["event_id"] for l in links_by_match[mid]}
    for ev in ev_ids:
        for u in uni_by_event.get(ev, []):
            if u["game_number"] in missing_nums and u["included"]:
                missed += 1
                print(f"  mid={mid} {ta} vs {tb} missing_games={sorted(missing_nums)} "
                      f"pm market game {u['game_number']} resolved={u['resolved_outcome']!r} q={u['question']!r}")
print(f"PM included markets for missing games: {missed}")

# Remake check: official < processed (extra games) or dup numbers
print("\n== processed > official (extra/remake rows) ==")
extra = [r for r in gap_rows if r[1] < r[2]]
print(len(extra))
for r in sorted(extra, key=lambda r: r[0])[:40]:
    print("  ", r)
