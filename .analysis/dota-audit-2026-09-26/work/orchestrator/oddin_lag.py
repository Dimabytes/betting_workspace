"""Oddin transport lag: received_at_utc - payload.lastUpdatedAt over in-progress snapshots, per map."""
import gzip
import json
import statistics
from datetime import datetime
from pathlib import Path


def parse_oddin(ts: str) -> float:
    # "2026-09-19 21:44:57.490359061 +0000 UTC"
    date, clock = ts.split(" ")[0], ts.split(" ")[1]
    whole, _, frac = clock.partition(".")
    return datetime.fromisoformat(f"{date}T{whole}+00:00").timestamp() + float("0." + (frac or "0")[:6])


per_map = []
for path in sorted(Path("data/trader").glob("*/oddin_state.jsonl*")):
    opener = gzip.open if path.suffix == ".gz" else open
    lags = []
    with opener(path, "rt") as handle:
        for line in handle:
            rec = json.loads(line)
            payload = rec.get("payload") or {}
            current = payload.get("currentMap") or {}
            if payload.get("matchStatus") != "LIVE" or (current.get("gameTime") or 0) <= 0 or not payload.get("lastUpdatedAt"):
                continue
            received = datetime.fromisoformat(rec["received_at_utc"].replace("Z", "+00:00")).timestamp()
            lags.append(received - parse_oddin(payload["lastUpdatedAt"]))
    if len(lags) >= 30:
        per_map.append((path.parent.name, statistics.median(lags), min(lags), len(lags)))
meds = sorted(m for _, m, _, _ in per_map)
print("oddin maps:", len(per_map))
print("per-map median lag s: p10 %.2f p50 %.2f p90 %.2f" % (meds[len(meds) // 10], statistics.median(meds), meds[9 * len(meds) // 10]))
print("per-map min lag s (floor):", round(statistics.median(m for _, _, m, _ in per_map), 2))
