"""Drill into orientation/label suspects.

For each flagged game: read the raw window archive's gameMetadata -> blue/red
team ids + participant summoner names. Compare the flagged game's blue-side
summoner set to the roster each team fields in sibling games of the same match.
If 'blue' players belong to the opposing team, the window metadata swapped
sides -> the linked radiant token is bound to the real red team.
"""

import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
WIN = E / "data/lol/raw/lolesports/windows"

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()

uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
link_by_gid = {l["esports_game_id"]: l for l in links}
games_by_match = defaultdict(list)
for g in games:
    games_by_match[g["esports_match_id"]].append(g)


def first_metadata(gid):
    path = WIN / f"{gid}.jsonl.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt") as fh:
        for line in fh:
            payload = json.loads(line)
            root = payload.get("window", payload)
            meta = root.get("gameMetadata")
            if meta:
                return meta
    return None


def side_info(meta, key):
    side = meta.get(key) or {}
    tid = side.get("esportsTeamId")
    names = []
    for row in side.get("participantMetadata") or []:
        if isinstance(row, dict):
            nm = row.get("summonerName") or row.get("championId")
            if nm:
                names.append(str(nm))
    return tid, names


def analyze(gid):
    l = link_by_gid.get(gid)
    g = next((x for x in games if x["esports_game_id"] == gid), None)
    if l is None or g is None:
        print(f"  gid={gid}: no link/game row")
        return
    meta = first_metadata(gid)
    if meta is None:
        print(f"  gid={gid}: no window archive/metadata")
        return
    blue_id, blue_names = side_info(meta, "blueTeamMetadata")
    red_id, red_names = side_info(meta, "redTeamMetadata")
    print(f"\n  gid={gid} match={l['esports_match_id']} num={l['game_number']} "
          f"q={uni_by_cid.get(l['condition_id'], {}).get('question')!r}")
    print(f"    link radiant_token_index={l['radiant_token_index']} resolved_idx={l['resolved_outcome_index']}")
    print(f"    games.parquet: blue_id={g['blue_esports_team_id']} red_id={g['red_esports_team_id']} "
          f"teams {g['team_a_name']}({g['team_a_id']}) vs {g['team_b_name']}({g['team_b_id']})")
    print(f"    window meta blue_id={blue_id} red_id={red_id}")
    print(f"    blue participants: {blue_names}")
    print(f"    red participants:  {red_names}")
    # sibling games: map team_id -> set of summoner names seen on EITHER side
    roster = defaultdict(set)
    for sib in games_by_match[l["esports_match_id"]]:
        smeta = first_metadata(sib["esports_game_id"])
        if not smeta:
            continue
        for key in ("blueTeamMetadata", "redTeamMetadata"):
            tid, names = side_info(smeta, key)
            if tid:
                roster[tid].update(names)
    if blue_id in roster:
        overlap_blue = roster[blue_id] & set(blue_names)
        print(f"    blue team_id {blue_id}: roster overlap w/ own side = {len(overlap_blue)}/5")
    for tid, names in roster.items():
        if tid != blue_id and set(blue_names) & names:
            print(f"    !! blue-side summoners appear under team_id {tid} elsewhere: {set(blue_names) & names}")
        if tid != red_id and set(red_names) & names:
            print(f"    !! red-side summoners appear under team_id {tid} elsewhere: {set(red_names) & names}")


flagged = [
    "116884625219920161",  # Blue Otter vs CCG g1 corr -0.97
    "116884625039565024",  # Conviction vs Winthrop g1 corr -0.59
    "116895891142270590",  # Lodis vs DOCISK g1 corr -0.63
    "116929405062330571",  # DK vs HLE BO1 corr -0.30
    "116793933806001282",  # mCon vs Dynasty g2 mid 0.44 rw=1
    "116792888905448372",  # Maryville vs NRG g4 mid 0.645 rw=0
    "115565004607949389",  # LYON vs C9 g2
    "116566854547769598",  # WE vs AL g2
    "116634566270405041",  # Heretics Acad vs Forsaken g2
    "116634566270405098",  # Spandau vs Anubis g3
    "116809554933343366",  # RMD vs paiN Acad g2
    "116855104460702335",  # MKOI vs GAM g1
    "117155436343202181",  # IG vs WE g3
    "116889604984222896",  # DRX Chall vs BNK g3
]
for gid in flagged:
    try:
        analyze(gid)
    except Exception as exc:  # noqa: BLE001
        print(f"  gid={gid}: ERROR {exc}")
