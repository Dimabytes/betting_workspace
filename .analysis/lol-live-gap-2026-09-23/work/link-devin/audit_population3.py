"""Population check v3: authoritative event_id from session_start, equity PnL,
split at the league-filter deploy (commit 96756568, 2026-09-08 14:55 UTC).
"""

import gzip
import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data/trader"
sys.path.insert(0, str(E / "src"))
from shared.utils.lol_leagues import (  # noqa: E402
    is_allowed_league, read_league_whitelist, resolve_base_league,
)

WL = read_league_whitelist(E / "config" / "lol_league_whitelist.json")
FILTER_TS = "2026-09-08T14:55"  # commit 96756568 16:55+0200 -> UTC

uni = pq.read_table(E / "data/lol/processed/universe/markets.parquet",
                    columns=["event_id", "event_slug", "league"]).to_pandas()
id_league = {}
for _, r in uni.iterrows():
    id_league.setdefault(str(r["event_id"]),
                         str(r["league"]) if r["league"] is not None else None)


def open_maybe_gz(path):
    if path.exists():
        return open(path, "rt")
    gz = path.with_suffix(path.suffix + ".gz")
    if gz.exists():
        return gzip.open(gz, "rt")
    return None


def session_facts(path):
    fh = open_maybe_gz(path)
    if fh is None:
        return {"fills": 0, "start": None, "end": None, "win_rows": 0, "priced_rows": 0}
    fills = 0
    start = None
    end = None
    win_rows = 0
    priced_rows = 0
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = rec.get("kind")
            if k == "fill":
                fills += 1
            elif k == "session_start" and start is None:
                start = rec
            elif k == "session_end":
                end = rec
            elif k == "signal":
                s = rec.get("second")
                if s is not None and 0 <= s <= 540:
                    win_rows += 1
                    if rec.get("market_p_radiant") is not None:
                        priced_rows += 1
    return {"fills": fills, "start": start, "end": end,
            "win_rows": win_rows, "priced_rows": priced_rows}


rows = []
for d in sorted(TRADER.glob("grid-*")):
    mj = d / "match.json"
    if not mj.exists():
        continue
    try:
        meta = json.loads(mj.read_text())
    except Exception:
        continue
    if meta.get("game") != "lol":
        continue
    market = meta.get("market") or {}
    facts = session_facts(d / "session.jsonl")
    start = facts.get("start") or {}
    sb = start.get("sidecar_binding") or {}
    event_id = sb.get("event_id") or market.get("event_id")
    league = id_league.get(str(event_id))
    joined = meta.get("joined_at_utc") or ""
    rows.append({
        "dir": d.name,
        "tournament": meta.get("tournament"),
        "event_id": str(event_id) if event_id else None,
        "pm_league": league,
        "allowed": is_allowed_league(WL, league),
        "map_number": meta.get("map_number"),
        "slug": market.get("market_slug"),
        "fills": facts["fills"],
        "equity": (facts.get("end") or {}).get("equity"),
        "joined_utc": joined,
        "post_filter": joined >= FILTER_TS,
        "execution_mode": start.get("execution_mode"),
        "win_rows": facts["win_rows"],
        "priced_rows": facts["priced_rows"],
    })

n = len(rows)
print(f"live lol maps: {n}")
print(f"execution_mode: {Counter(r['execution_mode'] for r in rows)}")
print(f"pre-filter (joined < {FILTER_TS}): {sum(1 for r in rows if not r['post_filter'])}")
print(f"post-filter: {sum(1 for r in rows if r['post_filter'])}")

for label, sub in (("PRE", [r for r in rows if not r["post_filter"]]),
                   ("POST", [r for r in rows if r["post_filter"]])):
    print(f"\n== {label}-filter ({len(sub)} maps) ==")
    dis = [r for r in sub if not r["allowed"]]
    print(f"non-whitelisted: {len(dis)} maps, fills={sum(r['fills'] for r in dis)}, "
          f"equity={sum(r['equity'] or 0 for r in dis):+.2f}")
    print(Counter(r["pm_league"] for r in dis))
    allowed = [r for r in sub if r["allowed"]]
    print(f"whitelisted: {len(allowed)} maps, fills={sum(r['fills'] for r in allowed)}, "
          f"equity={sum(r['equity'] or 0 for r in allowed):+.2f}")
    print(Counter(r["pm_league"] for r in allowed))

print("\n== POST-filter non-whitelisted maps (filter failures) ==")
for r in rows:
    if r["post_filter"] and not r["allowed"]:
        print("  ", r["dir"], r["pm_league"], r["slug"], r["fills"], r["equity"], r["tournament"])

print("\n== league x equity (all live maps) ==")
for k, v in sorted(Counter(r["pm_league"] for r in rows).items(), key=lambda kv: -kv[1]):
    sub = [r for r in rows if r["pm_league"] == k]
    eq = sum(r["equity"] or 0 for r in sub)
    print(f"  {len(sub):4d} allowed={is_allowed_league(WL, k)!s:5} {k}  equity={eq:+.2f}")

Path("/tmp/pop3_rows.json").write_text(json.dumps(rows, indent=1, default=str))
