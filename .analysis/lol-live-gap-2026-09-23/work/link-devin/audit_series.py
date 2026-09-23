"""Series-level consistency + refined orientation check.

- per map: corr(market_p_radiant, radiant_nw_adv) raw; flip candidates corr < -0.2
- per series: winner of the last map == match-winner resolution; implied map-win
  tally vs best_of; a team must reach ceiling(bo/2) at the last linked map.
"""

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()

uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
uni_by_event = defaultdict(list)
for u in universe:
    uni_by_event[u["event_id"]].append(u)
games_by_gid = {g["esports_game_id"]: g for g in games}
links_by_event = defaultdict(list)
for l in links:
    links_by_event[l["event_id"]].append(l)

ms = pq.read_table(
    E / "data/lol/processed/datasets/market_seconds.parquet",
    columns=["match_id", "second", "market_status", "market_p_radiant"],
).to_pandas()
gf = pq.read_table(
    E / "data/lol/processed/datasets/game_features.parquet",
    columns=["match_id", "game_second", "radiant_nw_adv"],
).to_pandas()

ok = ms[ms["market_status"] == "ok"][["match_id", "second", "market_p_radiant"]]
merged = ok.merge(gf, left_on=["match_id", "second"], right_on=["match_id", "game_second"], how="inner")

corr_raw = {}
for match_id, grp in merged.groupby("match_id"):
    if len(grp) < 60:
        continue
    mid = grp["market_p_radiant"].to_numpy()
    nw = grp["radiant_nw_adv"].to_numpy()
    if np.std(nw) < 500:  # nothing moved
        corr_raw[match_id] = np.nan
        continue
    corr_raw[match_id] = float(np.corrcoef(mid, nw)[0, 1])

link_by_gid = {l["esports_game_id"]: l for l in links}
print("== maps with corr_raw(mid, nw_adv) < -0.2 ==")
for m, c in sorted(corr_raw.items(), key=lambda kv: kv[1]):
    if not (c == c) or c >= -0.2:
        continue
    l = link_by_gid.get(str(m))
    if l is None:
        continue
    u = uni_by_cid.get(l["condition_id"], {})
    rw = int(l["resolved_outcome_index"]) == int(l["radiant_token_index"])
    print(f"  gid={m} num={l['game_number']} corr={c:.2f} radiant_win={rw} "
          f"assign={l['assignment']} q={u.get('question')!r}")

# ---------- series consistency ----------
print("\n== series consistency ==")
# For each linked event: winner of the highest-numbered linked map should equal the
# Match Winner's resolution (if a match_winner market exists) — and no linked map
# may come after the series is already decided by implied wins.
issues = []
for event_id, lks in links_by_event.items():
    lks = sorted(lks, key=lambda l: l["game_number"])
    ev_rows = uni_by_event[event_id]
    mw = next((r for r in ev_rows if r["contract_kind"] == "match_winner" and r["included"]), None)
    gwins = defaultdict(int)
    for l in lks:
        outcomes = json.loads(l["outcomes_json"])
        winner = outcomes[l["resolved_outcome_index"]]
        gwins[winner] += 1
    last = lks[-1]
    last_winner = json.loads(last["outcomes_json"])[last["resolved_outcome_index"]]
    bo = next((r["best_of"] for r in ev_rows if r["best_of"]), None)
    needed = (bo // 2 + 1) if bo else None
    # match winner resolution consistency
    if mw is not None and mw["resolved_outcome"] is not None:
        mw_winner = mw["resolved_outcome"]
        if mw_winner != last_winner:
            issues.append((event_id, "last_map_winner!=match_winner", last_winner, mw_winner,
                           [(l["game_number"], json.loads(l["outcomes_json"])[l["resolved_outcome_index"]]) for l in lks],
                           mw["question"]))
    # implied tally: no team's win count may exceed needed; last map winner should
    # reach `needed` when the full series was linked
    if needed is not None:
        over = {w: c for w, c in gwins.items() if c > needed}
        if over:
            issues.append((event_id, "team_won_more_than_needed", dict(over), bo,
                           [(l["game_number"], json.loads(l["outcomes_json"])[l["resolved_outcome_index"]]) for l in lks],
                           None))
        # a linked map that exists after the series was decided
        tally = defaultdict(int)
        decided_at = None
        for l in lks:
            winner = json.loads(l["outcomes_json"])[l["resolved_outcome_index"]]
            tally[winner] += 1
            if tally[winner] == needed and decided_at is None:
                decided_at = l["game_number"]
        if decided_at is not None and lks[-1]["game_number"] > decided_at:
            issues.append((event_id, "linked_map_after_decider", decided_at, lks[-1]["game_number"],
                           [(l["game_number"], json.loads(l["outcomes_json"])[l["resolved_outcome_index"]]) for l in lks],
                           None))
print(f"series issues: {len(issues)}")
for row in issues[:40]:
    print("  ", row)
