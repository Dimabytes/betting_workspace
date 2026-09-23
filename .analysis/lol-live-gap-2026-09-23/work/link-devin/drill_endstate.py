"""Check whether flagged maps' livestats actually reached game end.

For each flagged gid: scan the window archive, take the last frame's gameState,
blue/red totalGold, totalKills, inhibitors, towers. A 'finished' last frame plus
gold/kills disagreeing with radiant_win = label/link problem; a non-finished
frame = feed ended early, disagreement is meaningless.
"""

import gzip
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
WIN = E / "data/lol/raw/lolesports/windows"

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()
uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
link_by_gid = {l["esports_game_id"]: l for l in links}

flagged = [
    "116884625219920161", "116884625039565024", "116895891142270590",
    "116929405062330571", "116793933806001282", "116792888905448372",
    "115565004607949389", "116566854547769598", "116634566270405041",
    "116634566270405098", "116809554933343366", "116855104460702335",
    "117155436343202181", "116889604984222896",
]

for gid in flagged:
    path = WIN / f"{gid}.jsonl.gz"
    l = link_by_gid.get(gid)
    rw = None
    q = ""
    if l:
        rw = int(l["resolved_outcome_index"]) == int(l["radiant_token_index"])
        q = uni_by_cid.get(l["condition_id"], {}).get("question", "")
    if not path.exists():
        print(f"{gid}: NO ARCHIVE  rw={rw} {q}")
        continue
    last_frame = None
    n_frames = 0
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                payload = json.loads(line)
                root = payload.get("window", payload)
                for fr in root.get("frames") or []:
                    n_frames += 1
                    last_frame = fr
    except Exception as exc:  # noqa: BLE001
        print(f"{gid}: read error {exc}")
        continue
    if last_frame is None:
        print(f"{gid}: no frames rw={rw}")
        continue
    state = last_frame.get("gameState")
    ts = last_frame.get("rfc460Timestamp")
    def team(key):
        t = last_frame.get(key) or {}
        return dict(gold=t.get("totalGold"), kills=t.get("totalKills"),
                    towers=t.get("towers"), inh=t.get("inhibitors"),
                    barons=t.get("barons"), dragons=t.get("dragons"))
    b, r = team("blueTeam"), team("redTeam")
    print(f"{gid}: state={state} frames={n_frames} rw={rw}")
    print(f"    {q}")
    print(f"    ts={ts}")
    print(f"    blue={b}")
    print(f"    red ={r}")
