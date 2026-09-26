"""Adjudicate flagged maps with series-score ground truth.

For each flagged match: print official score, processed games, linked labels
(which side won each linked map per PM resolution), so a decider map label can
be compared against the official series winner.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(E / "src"))
from lol.lolesports_match import score_lol_team_name  # noqa: E402

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()
uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
uni_by_event = defaultdict(list)
for u in universe:
    uni_by_event[u["event_id"]].append(u)
games_by_match = defaultdict(list)
for g in games:
    games_by_match[g["esports_match_id"]].append(g)
links_by_match = defaultdict(list)
for l in links:
    links_by_match[l["esports_match_id"]].append(l)

flagged_matches = {
    "116884625219920160", "116884625039565023", "116895891142270589",
    "116929405062330570", "116793933805935744", "116792888905448368",
    "115565004607949387", "116566854547769596", "116634566270405039",
    "116634566270405095", "116809554933343364", "116855104460702334",
    "117155436343202178", "116889604984222893",
}

for mid in sorted(flagged_matches):
    gs = sorted(games_by_match.get(mid, []), key=lambda g: g["game_number"])
    if not gs:
        print(f"{mid}: no games")
        continue
    g0 = gs[0]
    print(f"\nmatch={mid} {g0['team_a_name']}({g0['team_a_code']}) vs {g0['team_b_name']}({g0['team_b_code']}) "
          f"score={g0['team_a_game_wins']}-{g0['team_b_game_wins']} bo={g0['best_of']} league={g0['league_slug']}")
    print(f"  processed games: {[g['game_number'] for g in gs]}")
    for g in gs:
        print(f"    game {g['game_number']}: blue={g['blue_esports_team_id']} "
              f"({'A' if g['blue_esports_team_id']==g0['team_a_id'] else 'B' if g['blue_esports_team_id']==g0['team_b_id'] else '?'})")
    for l in sorted(links_by_match.get(mid, []), key=lambda l: l["game_number"]):
        outcomes = json.loads(l["outcomes_json"])
        winner = outcomes[l["resolved_outcome_index"]]
        u = uni_by_cid.get(l["condition_id"], {})
        print(f"    link g{l['game_number']} {l['assignment']}: outcomes={outcomes} rti={l['radiant_token_index']} "
              f"winner={winner!r} kind={u.get('contract_kind')} q={u.get('question')!r}")
    # match-winner resolution
    for ev in {l["event_id"] for l in links_by_match.get(mid, [])}:
        for u in uni_by_event.get(ev, []):
            if u["contract_kind"] == "match_winner":
                print(f"    MW market: resolved={u['resolved_outcome']!r} q={u['question']!r}")
