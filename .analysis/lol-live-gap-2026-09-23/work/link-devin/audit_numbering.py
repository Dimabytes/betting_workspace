"""Drill into series with non-contiguous completed game numbers."""

import gzip
import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()

by_match = defaultdict(list)
for g in games:
    by_match[g["esports_match_id"]].append(g)

uni_by_event = defaultdict(list)
for u in universe:
    uni_by_event[u["event_id"]].append(u)
uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
links_by_match = defaultdict(list)
for l in links:
    links_by_match[l["esports_match_id"]].append(l)

# matches with non-contiguous completed numbering
odd = []
for mid, gs in by_match.items():
    nums = sorted(g["game_number"] for g in gs)
    if nums != list(range(1, len(nums) + 1)):
        odd.append(mid)

print(f"non-contiguous matches: {len(odd)}")
for mid in odd:
    gs = sorted(by_match[mid], key=lambda g: g["game_number"])
    lks = links_by_match.get(mid, [])
    marker = "LINKED" if lks else "      "
    print(f"\n{marker} match {mid}  bo={gs[0]['best_of']}  league={gs[0]['league_slug']} "
          f"teams={gs[0]['team_a_name']} vs {gs[0]['team_b_name']}")
    # raw event details: all games incl non-completed
    ev_path = E / "data/lol/raw/lolesports/events" / f"{mid}.json.gz"
    if ev_path.exists():
        body = json.loads(gzip.open(ev_path).read())
        ev = body["data"]["event"]
        for g in ev["match"]["games"]:
            print(f"    raw game id={g['id']} num={g.get('number')} state={g.get('state')}")
    else:
        print("    (no raw event cache)")
    for g in gs:
        print(f"    parquet game id={g['esports_game_id']} num={g['game_number']} "
              f"blue={g['blue_esports_team_id'][-6:]} red={g['red_esports_team_id'][-6:]}")
    for l in lks:
        u = uni_by_cid.get(l["condition_id"], {})
        print(f"    LINK num={l['game_number']} gid={l['esports_game_id']} "
              f"assign={l['assignment']} q={u.get('question')!r} "
              f"res={l['resolved_outcome']!r} rti={l['radiant_token_index']}")
