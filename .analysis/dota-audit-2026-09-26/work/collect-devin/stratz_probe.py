"""Probe STRATZ cache conventions: profiles, times, nw_leads indexing, level arrays."""
import gzip
import json
import random
import sys
from collections import Counter
from pathlib import Path

RAW = Path("data/raw/stratz_matches")
paths = sorted(RAW.glob("match_*.json.gz"))
print(f"total cache files: {len(paths)}")

# 1. Profile / fetched_at distribution over ALL files (cheap: read gz head? need full json)
# do full pass but only parse the envelope fields; json is needed anyway.
profiles = Counter()
fetched_years = Counter()
dur_vs_leads = []
sample = random.Random(0).sample(paths, 800)
match_none = 0
short_leads = 0
neg_gold = 0
gold_seen = 0
level_first_vals = []
fb_times = []
leads_index0_neg = 0
npm_vs_lead = Counter()
dur_extra = Counter()
level0_stats = []

for p in paths:
    with gzip.open(p, "rt") as f:
        payload = json.load(f)
    profiles[payload.get("cache_profile")] += 1
    fetched_years[str(payload.get("fetched_at", ""))[:7]] += 1
    if p not in sample:
        continue
    m = (payload.get("data") or {}).get("match")
    if m is None:
        match_none += 1
        continue
    nw = m.get("radiantNetworthLeads") or []
    xp = m.get("radiantExperienceLeads") or []
    dur = m.get("durationSeconds")
    if dur is not None and nw:
        # how many seconds beyond the last minute index
        last_second = (len(nw) - 1) * 60 - 60  # index k <-> second 60*(k-1)
        dur_vs_leads.append(dur - last_second)
    fb = m.get("firstBloodTime")
    if fb is not None:
        fb_times.append(fb)
    players = m.get("players") or []
    if players:
        p0 = players[0]
        st = p0.get("stats")
        if st:
            lvl = st.get("level")
            if lvl:
                level_first_vals.append(lvl[0])
        pb = p0.get("playbackData")
        if pb:
            ev = pb.get("playerUpdateGoldEvents") or []
            if ev:
                gold_seen += 1
                times = [e["time"] for e in ev]
                if times and times[0] < 0:
                    neg_gold += 1

print("profiles:", profiles)
print("fetched_by_month:", dict(sorted(fetched_years.items())))
print(f"match_none_in_sample: {match_none}")
print(f"dur - last_lead_second: min={min(dur_vs_leads)}, max={max(dur_vs_leads)}")
import statistics
print(f"dur - last_lead_second: median={statistics.median(dur_vs_leads)}")
print(f"firstBloodTime: n={len(fb_times)} min={min(fb_times)} max={max(fb_times)} median={statistics.median(fb_times)}")
print(f"level[0] first values sample: {sorted(level_first_vals)[:20]}")
print(f"gold events present: {gold_seen}/{len(sample)}, with negative first time: {neg_gold}")
