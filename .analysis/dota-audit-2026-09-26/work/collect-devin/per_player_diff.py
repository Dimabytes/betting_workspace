"""Per-side and per-player live-vs-STRATZ at one second of 9003856182."""
import bisect
import json
from pathlib import Path

from prepare_dataset.stratz_seconds import collect_player_playbacks
from shared.utils.json_io import read_gzip_json
from shared.utils.stratz import stratz_match_cache_path, is_usable_stratz_match
from trader.grid_widgets import parse_frame, TABLE_SERVICE, read_net_worth
from trader.grid_archive import iter_grid_archive_records
from shared.utils.match_time import parse_utc

mid = 9003856182
d = Path("data/trader/grid-3007267-m3")
meta = json.loads((d / "match.json").read_text())
horn = parse_utc(meta["horn_at_utc"]).timestamp()  # unix seconds

records = list(iter_grid_archive_records(d / "grid_state.jsonl.gz"))
# index tables by received_at
tables = []
for rec in records:
    frame = parse_frame(rec["frame"])
    if frame.service == TABLE_SERVICE:
        t = read_net_worth(frame.payload, frame.delay)
        if t is not None:
            tables.append((parse_utc(rec["received_at_utc"]).timestamp(), frame.delay, t))
print("tables:", len(tables), "horn:", horn)

# stratz per-player NW at a second
payload = read_gzip_json(stratz_match_cache_path(mid))
match = payload["data"]["match"]

def stratz_side_nws(sec):
    out = {"r": [], "d": []}
    for pl in match["players"]:
        evs = sorted(pl["playbackData"]["playerUpdateGoldEvents"], key=lambda e: e["time"])
        nw = 0
        for e in evs:
            if e["time"] > sec:
                break
            nw = e["networth"]
        out["r" if pl["isRadiant"] else "d"].append((pl["heroId"], nw))
    return out

# horn + s + pauses + delay approx: find table with received_at nearest horn+s+350s+8
for target_s in (1488, 944):
    # pauses: -85:193, 758:24, 1059:133, 1594:101
    pauses = [( -85,193),(758,24),(1059,133),(1594,101)]
    shift = sum(dd for t,dd in pauses if t < target_s)
    target_wall = horn + target_s + shift + 8
    best = min(tables, key=lambda x: abs(x[0]-target_wall))
    print(f"\n== target game second {target_s}, nearest table at wall {best[0]} (target {target_wall}, off {best[0]-target_wall:+.0f}s)")
    from collections import Counter
    print("  team_ids:", Counter(p.team_id for p in best[2].players))
    ids = [k for k in Counter(p.team_id for p in best[2].players)]
    for tid in ids:
        vals = sorted(p.net_worth for p in best[2].players if p.team_id == tid)
        print(f"  grid team {tid} nws: {vals} sum {sum(vals)}")
    st = stratz_side_nws(target_s)
    print("  stratz radiant nws:", sorted(n for _, n in st["r"]), "sum", sum(n for _, n in st["r"]))
    print("  stratz dire nws:", sorted(n for _, n in st["d"]), "sum", sum(n for _, n in st["d"]))
    print("  stratz radiant heroes/nw:", sorted(st["r"], key=lambda x: -x[1]))
