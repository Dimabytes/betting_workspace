"""Link audit over LoL history: labels vs end-state, orientation, numbering, dups."""

import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()
audit = pq.read_table(E / "data/lol/processed/lolesports_links/audit.parquet").to_pylist()
split = pq.read_table(E / "data/lol/processed/datasets/split.parquet").to_pylist()
pa_audit = pq.read_table(E / "data/lol/processed/datasets/audit.parquet").to_pylist()

print(f"links={len(links)} games={len(games)} universe={len(universe)} link_audit={len(audit)} split={len(split)} prepare_audit={len(pa_audit)}")

games_by_gid = {g["esports_game_id"]: g for g in games}
uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
split_by_gid = {s["esports_game_id"]: s for s in split}
paud_by_gid = {a["esports_game_id"]: a for a in pa_audit}

# ---------- (d) duplicates ----------
print("\n== duplicates ==")
gids = [l["esports_game_id"] for l in links]
cids = [l["condition_id"] for l in links]
pairs = [(l["event_id"], l["game_number"]) for l in links]
print("dup esports_game_id:", [g for g, c in Counter(gids).items() if c > 1])
print("dup condition_id:", [c for c, n in Counter(cids).items() if n > 1])
print("dup (event,game_number):", [p for p, n in Counter(pairs).items() if n > 1])
match_events = defaultdict(set)
for l in links:
    match_events[l["esports_match_id"]].add(l["event_id"])
multi = {m: ev for m, ev in match_events.items() if len(ev) > 1}
print("matches claimed by >1 event:", len(multi), dict(list(multi.items())[:10]))
event_matches = defaultdict(set)
for l in links:
    event_matches[l["event_id"]].add(l["esports_match_id"])
multi_ev = {e: m for e, m in event_matches.items() if len(m) > 1}
print("events linked to >1 match:", len(multi_ev), dict(list(multi_ev.items())[:10]))

# ---------- (c) game_number consistency ----------
print("\n== game_number vs universe ==")
bad = []
for l in links:
    u = uni_by_cid.get(l["condition_id"])
    if u is None:
        bad.append((l["condition_id"], "no universe row"))
        continue
    if u["game_number"] != l["game_number"]:
        bad.append((l["condition_id"], l["game_number"], u["game_number"], u["question"]))
print("link.game_number != universe.game_number:", len(bad), bad[:10])

# game numbering within a series: duplicates / gaps / count > best_of
print("\n== series game numbering ==")
by_match = defaultdict(list)
for g in games:
    by_match[g["esports_match_id"]].append(g)
dup_num, gap_num, over_bo = [], [], []
for mid, gs in by_match.items():
    nums = sorted(g["game_number"] for g in gs)
    if len(nums) != len(set(nums)):
        dup_num.append((mid, nums))
    if nums != list(range(1, len(nums) + 1)):
        gap_num.append((mid, nums))
    bo = gs[0]["best_of"]
    if bo is not None and len(nums) > bo:
        over_bo.append((mid, bo, nums))
print("completed games with duplicate numbers:", len(dup_num), dup_num[:10])
print("non-contiguous numbering:", len(gap_num), gap_num[:10])
print("completed count > best_of:", len(over_bo), over_bo[:10])

# linked maps pointing at anomalous series
linked_mids = {l["esports_match_id"] for l in links}
anomalous = {m for m, _ in dup_num + gap_num + over_bo}
print("linked matches with numbering anomalies:", sorted(anomalous & linked_mids))

# ---------- event/match-level audit reasons ----------
print("\n== link audit reasons ==")
ev_reasons = Counter(a["reason"] for a in audit if a["scope"] == "event")
map_reasons = Counter(a["reason"] for a in audit if a["scope"] == "map")
print("event:", dict(ev_reasons))
print("map:", dict(map_reasons))

# ---------- loading anchor ordering inside a series ----------
print("\n== anchor ordering ==")
bad_order = []
for mid, gs in by_match.items():
    ordered = sorted(gs, key=lambda g: g["game_number"])
    anchors = [g["loading_anchor_ts"] for g in ordered]
    if anchors != sorted(anchors):
        bad_order.append((mid, [(g["game_number"], g["loading_anchor_ts"]) for g in ordered]))
print("matches where anchor_ts not increasing with game_number:", len(bad_order))
for row in bad_order[:10]:
    print("  ", row)

# ---------- team name re-orientation (2b) ----------
sys.path.insert(0, str(E / "src"))
from lol.lolesports_match import score_lol_team_name  # noqa: E402
from shared.utils.team_names import pick_pair_orientation  # noqa: E402

print("\n== orientation re-check (outcomes vs team names) ==")
margins = []
suspicious = []
for l in links:
    g = games_by_gid.get(l["esports_game_id"])
    if g is None:
        continue
    outcomes = json.loads(l["outcomes_json"])
    o0, o1 = outcomes[0], outcomes[1]
    # series team names from the game row
    fa = score_lol_team_name(o0, g["team_a_name"], g["team_a_code"])
    fb = score_lol_team_name(o1, g["team_b_name"], g["team_b_code"])
    ra = score_lol_team_name(o0, g["team_b_name"], g["team_b_code"])
    rb = score_lol_team_name(o1, g["team_a_name"], g["team_a_code"])
    forward = fa + fb
    reverse = ra + rb
    margins.append((abs(forward - reverse), l["condition_id"], o0, o1, g["team_a_name"], g["team_b_name"]))
    orient = pick_pair_orientation(fa, fb, ra, rb)
    if orient is None:
        suspicious.append(("unorientable", l["condition_id"]))
        continue
    outcome_0_id = g["team_a_id"] if orient else g["team_b_id"]
    expected_rti = 0 if outcome_0_id == g["blue_esports_team_id"] else 1
    if expected_rti != l["radiant_token_index"]:
        suspicious.append(("rti_mismatch", l["condition_id"], expected_rti, l["radiant_token_index"]))
margins.sort()
print("unorientable/rti_mismatch:", suspicious[:20], "total", len(suspicious))
print("lowest orientation margins (|fwd-rev|):")
for m in margins[:15]:
    print("   %.3f %s %r/%r vs %r/%r" % m)

# does the resolved outcome label match the team it was bound to?
print("\n== resolved_outcome name vs bound team ==")
bad_res = []
for l in links:
    g = games_by_gid.get(l["esports_game_id"])
    if g is None:
        continue
    outcomes = json.loads(l["outcomes_json"])
    res_name = outcomes[l["resolved_outcome_index"]]
    # team that owns the resolved token: radiant token's team if rti==res_idx
    rti = l["radiant_token_index"]
    res_idx = l["resolved_outcome_index"]
    if res_name != l["resolved_outcome"]:
        bad_res.append(("label_mismatch", l["condition_id"], res_name, l["resolved_outcome"]))
print("resolved_outcome != outcomes[resolved_outcome_index]:", len(bad_res), bad_res[:10])

# winner name vs blue/red team names
close_wins = []
for l in links:
    g = games_by_gid.get(l["esports_game_id"])
    if g is None:
        continue
    outcomes = json.loads(l["outcomes_json"])
    res_idx = l["resolved_outcome_index"]
    rti = l["radiant_token_index"]
    radiant_win = res_idx == rti
    winner_name = outcomes[res_idx]
    s_blue = max(
        score_lol_team_name(winner_name, g["team_a_name"], g["team_a_code"])
        if g["team_a_id"] == g["blue_esports_team_id"]
        else score_lol_team_name(winner_name, g["team_b_name"], g["team_b_code"]),
        0,
    )
    s_red = max(
        score_lol_team_name(winner_name, g["team_b_name"], g["team_b_code"])
        if g["team_b_id"] == g["red_esports_team_id"]
        else score_lol_team_name(winner_name, g["team_a_name"], g["team_a_code"]),
        0,
    )
    margin = s_blue - s_red
    if (radiant_win and margin < 0) or (not radiant_win and margin > 0):
        close_wins.append(
            ("NAME_VS_SIDE_FLIP", l["condition_id"], l["esports_game_id"], winner_name,
             g["team_a_name"], g["team_b_name"], radiant_win, round(s_blue,3), round(s_red,3))
        )
    elif abs(margin) < 0.15:
        close_wins.append(
            ("borderline", l["condition_id"], l["esports_game_id"], winner_name,
             g["team_a_name"], g["team_b_name"], radiant_win, round(s_blue,3), round(s_red,3))
        )
print("winner-name-vs-side contradictions/borderline:", len(close_wins))
for row in close_wins[:30]:
    print("  ", row)
