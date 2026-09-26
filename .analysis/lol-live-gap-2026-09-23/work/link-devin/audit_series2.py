"""Series consistency, take 2: use games.parquet final score + completed game list.

Ground truth: the last completed lolesports game of a series is the decider; its
winner is the team with more gameWins. Compare against (a) the link label when
that game is linked, (b) the PM match-winner resolution. Also tally linked map
winners per team vs final score (a linked win count can never exceed the final
score), and flag linked maps numbered above the series length.
"""

import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()

uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
uni_by_event = defaultdict(list)
for u in universe:
    uni_by_event[u["event_id"]].append(u)
games_by_match = defaultdict(list)
for g in games:
    games_by_match[g["esports_match_id"]].append(g)
links_by_match = defaultdict(list)
links_by_event = defaultdict(list)
for l in links:
    links_by_match[l["esports_match_id"]].append(l)
    links_by_event[l["event_id"]].append(l)

from lol.lolesports_match import score_lol_team_name  # noqa
import sys
sys.path.insert(0, str(E / "src"))
import importlib
importlib.invalidate_caches()
from lol.lolesports_match import score_lol_team_name

def outcome_team_side(l, g):
    """Return 'a'/'b': which series team the resolved outcome name matches better."""
    outcomes = json.loads(l["outcomes_json"])
    winner = outcomes[l["resolved_outcome_index"]]
    sa = score_lol_team_name(winner, g["team_a_name"], g["team_a_code"])
    sb = score_lol_team_name(winner, g["team_b_name"], g["team_b_code"])
    return ("a" if sa >= sb else "b", winner, sa, sb)

issues = []
for mid, gs in games_by_match.items():
    lks = sorted(links_by_match.get(mid, []), key=lambda l: l["game_number"])
    if not lks:
        continue
    g0 = gs[0]
    aw, bw = g0["team_a_game_wins"], g0["team_b_game_wins"]
    final_winner = None
    if aw is not None and bw is not None and aw != bw:
        final_winner = "a" if aw > bw else "b"
    max_completed = max(g["game_number"] for g in gs)

    # linked map winners as team sides
    tally = defaultdict(int)
    per_map = []
    for l in lks:
        g = next(x for x in gs if x["esports_game_id"] == l["esports_game_id"])
        side, winner, sa, sb = outcome_team_side(l, g)
        tally[side] += 1
        per_map.append((l["game_number"], side, winner, l["assignment"], l["condition_id"][:10]))

    # 1) a linked winner tally can never exceed the final score
    if aw is not None and bw is not None:
        if tally["a"] > aw or tally["b"] > bw:
            issues.append((mid, "tally_exceeds_final", per_map, (aw, bw),
                           g0["team_a_name"], g0["team_b_name"]))
    # 2) if the decider (max completed game) is linked, its winner is final_winner
    if final_winner is not None:
        decider_link = [l for l in lks if l["game_number"] == max_completed]
        for l in decider_link:
            g = next(x for x in gs if x["esports_game_id"] == l["esports_game_id"])
            side, winner, sa, sb = outcome_team_side(l, g)
            if side != final_winner:
                issues.append((mid, "decider_label_wrong", l["game_number"], winner,
                               g0["team_a_name"], g0["team_b_name"], (aw, bw),
                               "scores", round(sa, 3), round(sb, 3)))

print(f"series with contradictions: {len(issues)}")
for row in issues:
    print("  ", row)

# 3) match-winner resolution vs final score winner (name check)
print("\n== match-winner market resolution vs series score ==")
mw_bad = []
for event_id, ev_rows in uni_by_event.items():
    mw = next((r for r in ev_rows if r["contract_kind"] == "match_winner" and r["included"]), None)
    if mw is None:
        continue
    lks = links_by_event.get(event_id, [])
    if not lks:
        continue
    mid = lks[0]["esports_match_id"]
    g0 = games_by_match[mid][0]
    aw, bw = g0["team_a_game_wins"], g0["team_b_game_wins"]
    if aw is None or bw is None or aw == bw:
        continue
    winner_name = g0["team_a_name"] if aw > bw else g0["team_b_name"]
    winner_code = g0["team_a_code"] if aw > bw else g0["team_b_code"]
    s_win = score_lol_team_name(mw["resolved_outcome"], winner_name, winner_code)
    loser_name = g0["team_b_name"] if aw > bw else g0["team_a_name"]
    loser_code = g0["team_b_code"] if aw > bw else g0["team_a_code"]
    s_lose = score_lol_team_name(mw["resolved_outcome"], loser_name, loser_code)
    if s_lose > s_win:
        mw_bad.append((event_id, mid, mw["resolved_outcome"], winner_name, loser_name,
                       round(s_win, 3), round(s_lose, 3), (aw, bw)))
print(f"match-winner resolution contradicts series score: {len(mw_bad)}")
for row in mw_bad:
    print("  ", row)
