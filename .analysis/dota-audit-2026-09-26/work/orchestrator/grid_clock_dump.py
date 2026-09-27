"""Dump GRID scoreboard clock for game 1 of an archive: received, occurredAt, currentSeconds, ticking flags."""
import gzip
import json
import sys
from datetime import datetime

path = sys.argv[1]
game_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
last = None
for line in gzip.open(path, "rt"):
    rec = json.loads(line)
    frame = json.loads(rec["frame"])
    if frame.get("service") != "integrity_safe_series_scoreboard_v2":
        continue
    for item in frame.get("data", []):
        data = json.loads(item["data"]) if isinstance(item.get("data"), str) else item.get("data")
        if not data or "games" not in data:
            continue
        games = data["games"]
        if game_index >= len(games):
            continue
        g = games[game_index]
        clock = g.get("gameClock") or {}
        key = (g.get("status"), clock.get("isTicking"), clock.get("tickingBackwards"), clock.get("currentSeconds"), clock.get("occurredAt"))
        if key == last:
            continue
        last = key
        occ = clock.get("occurredAt")
        horn = None
        if occ:
            t = datetime.fromisoformat(occ.replace("Z", "+00:00")).timestamp()
            sec = clock.get("currentSeconds") or 0
            horn = datetime.utcfromtimestamp(t - sec).strftime("%H:%M:%S")
        print(rec["received_at_utc"][11:19], g.get("status"), "tick" if clock.get("isTicking") else "stop",
              "back" if clock.get("tickingBackwards") else "fwd", clock.get("currentSeconds"), (occ or "")[11:19], "horn=", horn)
