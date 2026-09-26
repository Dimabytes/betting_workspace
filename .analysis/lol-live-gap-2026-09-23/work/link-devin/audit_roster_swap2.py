"""Roster swap scan v2 — leave-one-out, all linked games.

For each linked game, count how many OTHER games place each blue-side summoner
under the same team id (blue_esports_team_id) vs the opponent's id. A swap =
the blue-side five mostly appear under the RED team id elsewhere.
"""

import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
WIN = E / "data/lol/raw/lolesports/windows"

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()
games_by_gid = {g["esports_game_id"]: g for g in games}


def first_meta(gid):
    path = WIN / f"{gid}.jsonl.gz"
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                payload = json.loads(line)
                root = payload.get("window", payload)
                meta = root.get("gameMetadata")
                if meta:
                    return meta
    except Exception:  # noqa: BLE001
        return None
    return None


def names(meta, key):
    side = meta.get(key) or {}
    return [str(r["summonerName"]) for r in side.get("participantMetadata") or []
            if isinstance(r, dict) and r.get("summonerName")]


recs = {}
# name -> Counter(team_id -> #games where this name appeared under that team id)
name_teams = defaultdict(Counter)
for g in games:
    meta = first_meta(g["esports_game_id"])
    if not meta:
        continue
    blue_id, red_id = g["blue_esports_team_id"], g["red_esports_team_id"]
    bn, rn = names(meta, "blueTeamMetadata"), names(meta, "redTeamMetadata")
    recs[g["esports_game_id"]] = (blue_id, red_id, bn, rn)
    for n in set(bn):
        name_teams[n][blue_id] += 1
    for n in set(rn):
        name_teams[n][red_id] += 1

suspects = []
covered = 0
for l in links:
    gid = l["esports_game_id"]
    if gid not in recs:
        suspects.append((gid, "no_meta"))
        continue
    blue_id, red_id, blue_names, red_names = recs[gid]
    if not blue_names or not red_names:
        suspects.append((gid, "no_names"))
        continue
    votes = 0
    detail = []
    for name in blue_names:
        # leave-one-out: this game contributed 1 to name_teams[name][blue_id]
        ub = name_teams[name][blue_id] - 1
        ur = name_teams[name][red_id]
        if ub or ur:
            votes += 1 if ub >= ur else -1
            detail.append((name, ub, ur))
    if votes:
        covered += 1
        if votes <= -3:
            g = games_by_gid[gid]
            suspects.append((gid, "SWAPPED", votes, l["game_number"],
                             g["team_a_name"], g["team_b_name"], detail))
    else:
        suspects.append((gid, "no_cross_evidence"))

print(f"linked={len(links)} covered={covered} suspects={len(suspects)}")
for s in suspects:
    print("  ", s)
