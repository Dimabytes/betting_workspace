"""Compare PM per-market game_start_time / market_start_time to the linked game's anchor."""

import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()

uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
games_by_match = defaultdict(list)
for g in games:
    games_by_match[g["esports_match_id"]].append(g)
games_by_gid = {g["esports_game_id"]: g for g in games}

rows = []
for l in links:
    u = uni_by_cid.get(l["condition_id"])
    if u is None:
        continue
    rows.append((l, u))

deltas_start = []
deltas_mkt = []
no_start = 0
for l, u in rows:
    anchor = l["loading_anchor_ts"]
    gst = u["game_start_ts"] if "game_start_ts" in u else None
    if gst is None and u.get("market_start_time"):
        from shared.utils.parsing import parse_ts
        gst = parse_ts(u["market_start_time"])
    if gst is None:
        no_start += 1
        continue
    deltas_start.append((gst - anchor, l, u))

print(f"links={len(rows)} with game_start/market_start: {len(deltas_start)} missing: {no_start}")
vals = sorted(d for d, _, _ in deltas_start)
n = len(vals)
print("delta market.game_start_ts - loading_anchor_ts percentiles:",
      {p: vals[min(n - 1, int(n * p))] for p in (0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1)})

print("\nlargest |delta| (suspicious):")
for d, l, u in sorted(deltas_start, key=lambda t: -abs(t[0]))[:25]:
    g = games_by_gid[l["esports_game_id"]]
    sib = sorted(games_by_match[l["esports_match_id"]], key=lambda x: x["game_number"])
    sib_str = [(s["game_number"], s["loading_anchor_ts"] - anchor) for s in sib]
    print(f"  delta={d}s cid={l['condition_id'][:12]} q={u['question']!r} "
          f"link_num={l['game_number']} siblings(gnum,anchor-link_anchor)={sib_str}")

# also: does the market's game_start_ts land closer to a DIFFERENT game in the same match?
print("\nmarkets whose game_start_ts is closer to a sibling game's anchor:")
misassigned = []
for l, u in rows:
    gst = u["game_start_ts"]
    if gst is None:
        continue
    sibs = games_by_match[l["esports_match_id"]]
    own = abs(gst - l["loading_anchor_ts"])
    better = [s for s in sibs if abs(gst - s["loading_anchor_ts"]) < own]
    if better:
        misassigned.append((l, u, [(s["game_number"], s["loading_anchor_ts"] - gst) for s in better]))
print("count:", len(misassigned))
for l, u, better in misassigned[:20]:
    print(f"  cid={l['condition_id'][:12]} link_num={l['game_number']} q={u['question']!r} "
          f"own_delta={u['game_start_ts'] - l['loading_anchor_ts']}s closer_to={better}")
