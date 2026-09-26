"""Population check v2: live event_slug -> PM league (universe) -> whitelist.

The live LolLeagueFilter admits an event when its Polymarket event league is in
the canonical whitelist (aliases resolved). We replicate that here by joining
each live map's event_slug to markets.parquet's league column.
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

uni = pq.read_table(E / "data/lol/processed/universe/markets.parquet",
                    columns=["event_id", "event_slug", "league", "condition_id"]).to_pandas()
slug_league = {}
for _, r in uni.drop_duplicates("event_slug").iterrows():
    slug_league[str(r["event_slug"])] = (str(r["league"]) if r["league"] is not None else None,
                                         str(r["event_id"]))
# also id-keyed (slug may drift)
id_league = {str(r["event_id"]): (str(r["league"]) if r["league"] is not None else None)
             for _, r in uni.iterrows()}


def open_maybe_gz(path):
    if path.exists():
        return open(path, "rt")
    gz = path.with_suffix(path.suffix + ".gz")
    if gz.exists():
        return gzip.open(gz, "rt")
    return None


def fills_and_signals(path):
    fh = open_maybe_gz(path)
    if fh is None:
        return 0, []
    fills = 0
    sigs = []
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "fill":
                fills += 1
            elif rec.get("kind") == "signal":
                sigs.append(rec)
    return fills, sigs


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
    final = meta.get("final") or {}
    fills, sigs = fills_and_signals(d / "session.jsonl")
    win = [s for s in sigs if s.get("second") is not None and 0 <= s["second"] <= 540]
    priced = [s for s in win if s.get("market_p_radiant") is not None]
    event_slug = market.get("event_slug")
    cond = market.get("condition_id")
    # league by event_slug; fall back to condition_id->event via universe
    lg, eid = slug_league.get(str(event_slug), (None, None))
    if lg is None and eid is None:
        hit = uni[uni["condition_id"] == cond]
        if len(hit):
            lg = str(hit.iloc[0]["league"]) if hit.iloc[0]["league"] is not None else None
            eid = str(hit.iloc[0]["event_id"])
    rows.append({
        "dir": d.name,
        "tournament": meta.get("tournament"),
        "event_slug": event_slug,
        "event_id": eid,
        "pm_league": lg,
        "base_league": (resolve_base_league(WL, lg) if lg else None),
        "allowed": is_allowed_league(WL, lg),
        "in_universe": lg is not None or eid is not None,
        "map_number": meta.get("map_number"),
        "slug": market.get("market_slug"),
        "fills": fills,
        "sig_rows": len(win),
        "priced_rows": len(priced),
        "pnl": (final.get("pnl") or {}).get("realized_pnl_usdc"),
        "joined_utc": meta.get("joined_at_utc"),
    })

print(f"live lol maps: {len(rows)}")
print(f"matched to universe event: {sum(1 for r in rows if r['in_universe'])}")
print(f"allowed by whitelist: {sum(1 for r in rows if r['allowed'])}")
print("\n== by PM league ==")
for k, v in Counter((r["pm_league"], r["allowed"]) for r in rows).most_common():
    pnl = sum(r["pnl"] or 0 for r in rows if (r["pm_league"], r["allowed"]) == k)
    print(f"  {v:4d}  allowed={k[1]!s:5}  {k[0]}   pnl={pnl:+.2f}")

print("\n== NOT allowed (non-whitelisted league) but live ==")
bad = [r for r in rows if not r["allowed"]]
for r in bad:
    print(f"  {r['dir']} league={r['pm_league']!r} slug={r['slug']} fills={r['fills']} "
          f"pnl={r['pnl']} tournament={r['tournament']!r}")
print(f"total: {len(bad)} maps, fills: {sum(r['fills'] for r in bad)}, "
      f"pnl: {sum(r['pnl'] or 0 for r in bad):+.2f}")

print("\n== not in universe at all ==")
for r in rows:
    if not r["in_universe"]:
        print("  ", r["dir"], r["event_slug"], r["slug"], r["tournament"])

# admission-rule proxy: priced coverage in the model window
print("\n== priced coverage (0..540s) for live maps ==")
cov = [r for r in rows if r["sig_rows"]]
print(f"maps with signal rows in window: {len(cov)}/{len(rows)}")
buckets = Counter()
for r in cov:
    f = r["priced_rows"] / r["sig_rows"]
    buckets["0%"] += f == 0
    buckets["<50%"] += 0 < f < 0.5
    buckets["50-90%"] += 0.5 <= f < 0.9
    buckets[">=90%"] += f >= 0.9
print(buckets)
print("\nmaps with 0 priced seconds in window (would be missing_books/prior live):")
for r in cov:
    if r["priced_rows"] == 0:
        print(f"  {r['dir']} league={r['pm_league']} fills={r['fills']} sig_rows={r['sig_rows']}")

Path("/tmp/pop_rows.json").write_text(json.dumps(rows, indent=1, default=str))
