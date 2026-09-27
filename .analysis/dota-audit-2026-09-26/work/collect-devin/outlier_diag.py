"""Diagnose match 9003856182 NW divergence + general STRATZ internals."""
import statistics
from collections import Counter

from shared.utils.json_io import read_gzip_json
from shared.utils.stratz import stratz_match_cache_path

mid = 9003856182
payload = read_gzip_json(stratz_match_cache_path(mid))
match = payload["data"]["match"]
print("fetched_at:", payload["fetched_at"], "profile:", payload["cache_profile"])
print("dur:", match["durationSeconds"], "start:", match["startDateTime"], "end:", match["endDateTime"])
print("end-start - duration:", match["endDateTime"] - match["startDateTime"] - match["durationSeconds"])
print("didRadiantWin:", match["didRadiantWin"], "league:", match["leagueId"])

for i, pl in enumerate(match["players"]):
    pb = pl.get("playbackData") or {}
    ge = pb.get("playerUpdateGoldEvents") or []
    times = [e["time"] for e in ge]
    print(
        f"p{i} radiant={pl['isRadiant']} hero={pl['heroId']} leaver={pl['leaverStatus']} "
        f"nw={pl['networth']} lvl={pl['level']} deaths={pl['deaths']} "
        f"gold_events={len(ge)} range=({times[0] if times else None}..{times[-1] if times else None}) "
        f"lvl_times={pl['stats']['level'][:6]}"
    )

# gold event cadence across players
gaps = []
for pl in match["players"]:
    ge = sorted((pl.get("playbackData") or {}).get("playerUpdateGoldEvents") or [], key=lambda e: e["time"])
    for a, b in zip(ge, ge[1:]):
        gaps.append(b["time"] - a["time"])
print("gold event gap p50/p95/p99/max:",
      statistics.median(gaps), sorted(gaps)[int(len(gaps)*.95)], sorted(gaps)[int(len(gaps)*.99)], max(gaps))

# nw_leads consistency at second -60 and 0
nw = match["radiantNetworthLeads"]
print("nw_leads[:6]:", nw[:6])
# playback lead at -60
for sec in (-60, 0, 60):
    leads = 0
    for pl in match["players"]:
        evs = sorted((pl["playbackData"] or {}).get("playerUpdateGoldEvents") or [], key=lambda e: e["time"])
        nwv = 0
        for e in evs:
            if e["time"] > sec:
                break
            nwv = e["networth"]
        leads += nwv if pl["isRadiant"] else -nwv
    print(f"playback lead @{sec}: {leads}  (nw_leads idx {sec//60+1}: {nw[sec//60+1] if sec//60+1 < len(nw) else None})")
