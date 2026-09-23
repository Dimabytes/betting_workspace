"""Scan ALL linked games for swapped blue/red window metadata.

For every linked game, read the first window payload's gameMetadata and take
blue/red participant summoner names. Summoner names carry team tags ('CCG Fiji',
'BLUE Philip'). Score the blue-side tag set against both teams' codes/names; a
swap means blue-side summoners match the team that metadata calls red.

For robustness also aggregate per-match: a team's roster is constant across the
series, so a game whose blue-side roster equals the OTHER team's roster is a
swap even when tags are unusual.
"""

import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
WIN = E / "data/lol/raw/lolesports/windows"
sys.path.insert(0, str(E / "src"))
from shared.utils.team_names import normalize_team_name  # noqa: E402

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()
games_by_gid = {g["esports_game_id"]: g for g in games}
games_by_match = defaultdict(list)
for g in games:
    games_by_match[g["esports_match_id"]].append(g)

TAG_RE = re.compile(r"^([A-Za-z0-9]{2,6})\s")


def first_metadata(gid):
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


def side_names(meta, key):
    side = meta.get(key) or {}
    out = []
    for row in side.get("participantMetadata") or []:
        if isinstance(row, dict) and row.get("summonerName"):
            out.append(str(row["summonerName"]))
    return out


def tags(names):
    out = []
    for n in names:
        m = TAG_RE.match(n)
        if m:
            out.append(m.group(1).upper())
    return out


# pass 1: per-match roster by team id across all processed games (majority vote)
roster_by_team = defaultdict(lambda: defaultdict(int))
meta_cache = {}
for g in games:
    meta = first_metadata(g["esports_game_id"])
    meta_cache[g["esports_game_id"]] = meta
    if not meta:
        continue
    for key, idkey in (("blueTeamMetadata", "blue_esports_team_id"),
                       ("redTeamMetadata", "red_esports_team_id")):
        tid = g[idkey]
        for nm in side_names(meta, key):
            roster_by_team[tid][nm] += 1


def roster_overlap(names, tid):
    r = roster_by_team.get(tid, {})
    return sum(1 for n in names if r.get(n))


suspects = []
for l in links:
    g = games_by_gid[l["esports_game_id"]]
    meta = meta_cache.get(l["esports_game_id"])
    if not meta:
        suspects.append((l["esports_game_id"], "no_meta", l["game_number"], g["team_a_name"], g["team_b_name"]))
        continue
    blue_id, red_id = g["blue_esports_team_id"], g["red_esports_team_id"]
    blue_names = side_names(meta, "blueTeamMetadata")
    red_names = side_names(meta, "redTeamMetadata")
    if not blue_names or not red_names:
        suspects.append((l["esports_game_id"], "no_names", l["game_number"], g["team_a_name"], g["team_b_name"]))
        continue
    # does blue-side roster match the team the metadata calls blue, or the other?
    own = roster_overlap(blue_names, blue_id)
    other = roster_overlap(blue_names, red_id)
    # only meaningful when blue_id's roster isn't polluted by the same names
    blue_ok = own >= 4 or (own >= 3 and own > other)
    swapped = other >= 4 and own <= 1
    # tag check: blue names' tags vs team codes
    a_code = (g["team_a_code"] or "").upper()
    b_code = (g["team_b_code"] or "").upper()
    blue_tags = tags(blue_names)
    blue_team_is_a = blue_id == g["team_a_id"]
    expect_code = a_code if blue_team_is_a else b_code
    other_code = b_code if blue_team_is_a else a_code
    tag_expect = sum(1 for t in blue_tags if t and (t in expect_code or expect_code in t))
    tag_other = sum(1 for t in blue_tags if t and (t in other_code or other_code in t))
    if swapped or (tag_other >= 4 and tag_expect == 0):
        suspects.append((l["esports_game_id"], "SWAPPED", l["game_number"],
                         g["team_a_name"], g["team_b_name"], own, other,
                         blue_names[:2], tag_expect, tag_other))
    elif not blue_ok and tag_other > tag_expect:
        suspects.append((l["esports_game_id"], "weak_swap?", l["game_number"],
                         g["team_a_name"], g["team_b_name"], own, other,
                         blue_names[:2], tag_expect, tag_other))

print(f"suspects: {len(suspects)}")
for s in suspects:
    print("  ", s)
