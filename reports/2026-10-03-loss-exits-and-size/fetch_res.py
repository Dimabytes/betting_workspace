import json, time, urllib.request, urllib.parse, sys
from pathlib import Path
import pandas as pd
base = Path("/root/work/esports-trader/data/backtests/dota_maker/LIVE")
slugs = set()
for s in ("seed0", "seed1", "seed2"):
    r = pd.read_parquet(base / s / "results.parquet", columns=["slug"])
    slugs |= set(r.slug.dropna())
cache_p = Path("resolutions.json")
cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
todo = sorted(s for s in slugs if s not in cache)
print("slugs", len(slugs), "todo", len(todo), flush=True)
for i, slug in enumerate(todo):
    url = "https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode({"slug": slug, "closed": "true"})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "research"}), timeout=20) as resp:
            d = json.load(resp)
        if d:
            m = d[0]
            cache[slug] = {"outcomePrices": json.loads(m.get("outcomePrices") or "[]"),
                           "clobTokenIds": json.loads(m.get("clobTokenIds") or "[]"),
                           "outcomes": json.loads(m.get("outcomes") or "[]"),
                           "status": m.get("umaResolutionStatus")}
        else:
            cache[slug] = None
    except Exception as e:
        print("err", slug, type(e).__name__, flush=True)
    if i % 50 == 0:
        cache_p.write_text(json.dumps(cache)); print(i, flush=True)
    time.sleep(0.15)
cache_p.write_text(json.dumps(cache))
print("done", sum(1 for v in cache.values() if v), "resolved of", len(cache))
