"""Population comparison: live-traded LoL maps vs training/backtest admission.

For every live map: tournament -> whitelist league (substring match on the
whitelist + aliases). Live-side signal coverage: share of signal rows with a
non-null market_p_radiant inside the model's 0..540s window (proxy for
thin_tape/missing_books admission) and whether a strict prior could have
existed (a priced signal near second 0 / before first signal).

Backtest side: maps per league in audit.parquet, split by included/reason.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data/trader"
sys.path.insert(0, str(E / "src"))
from shared.utils.lol_leagues import read_league_whitelist  # noqa: E402

WL = read_league_whitelist(E / "config" / "lol_league_whitelist.json")

import gzip

def open_maybe_gz(path):
    if path.exists():
        return open(path, "rt")
    gz = path.with_suffix(path.suffix + ".gz")
    if gz.exists():
        return gzip.open(gz, "rt")
    return None


def signals(path):
    fh = open_maybe_gz(path)
    if fh is None:
        return []
    out = []
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "signal":
                out.append(rec)
    return out


def league_of(tournament):
    """Map GRID tournament name to whitelist league via substring/alias."""
    if not tournament:
        return None
    t = tournament.lower()
    for name in WL.leagues:
        if name.lower() in t:
            return name
    for alias, base in WL.aliases.items():
        if alias.lower() in t:
            return base
    return None


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
    sig = signals(d / "session.jsonl")
    # model window is 0..540s
    win = [s for s in sig if s.get("second") is not None and 0 <= s["second"] <= 540]
    priced = [s for s in win if s.get("market_p_radiant") is not None]
    fills = 0
    fh = open_maybe_gz(d / "session.jsonl")
    if fh is not None:
        with fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "fill":
                    fills += 1
    rows.append({
        "dir": d.name,
        "tournament": meta.get("tournament"),
        "league": league_of(meta.get("tournament")),
        "map_number": meta.get("map_number"),
        "slug": market.get("market_slug"),
        "joined_utc": meta.get("joined_at_utc"),
        "fills": fills,
        "sig_rows": len(win),
        "priced_rows": len(priced),
        "priced_frac": (len(priced) / len(win)) if win else None,
        "pnl": (final.get("pnl") or {}).get("realized_pnl_usdc"),
    })

print(f"live lol maps: {len(rows)}")
by_league = Counter(r["league"] or f"UNMAPPED:{r['tournament']}" for r in rows)
print("\n== live maps by league ==")
for k, v in by_league.most_common():
    print(f"  {v:4d}  {k}")

traded = [r for r in rows if r["fills"] > 0]
print(f"\n== maps with fills by league == ({len(traded)})")
for k, v in Counter(r["league"] or f"UNMAPPED:{r['tournament']}" for r in traded).most_common():
    pnl = sum(r["pnl"] or 0 for r in traded if (r["league"] or f"UNMAPPED:{r['tournament']}") == k)
    print(f"  {v:4d}  {k}  pnl={pnl:+.2f}")

print("\n== no_live_feed leagues present live? ==")
for r in rows:
    if r["league"] in WL.no_live_feed:
        print("  ", r["dir"], r["tournament"], "fills:", r["fills"])

print("\n== unmapped tournaments ==")
for r in rows:
    if r["league"] is None:
        print("  ", r["dir"], repr(r["tournament"]), "fills:", r["fills"])

print("\n== live quote coverage in 0..540s window ==")
cov = [r for r in rows if r["sig_rows"]]
print(f"maps with any signal rows in window: {len(cov)}")
thin = [r for r in cov if (r["priced_frac"] or 0) < 0.5]
print(f"maps with <50% priced seconds in window: {len(thin)}")
for r in thin[:40]:
    print(f"  {r['dir']} {r['tournament']} priced={r['priced_frac']:.0%} ({r['priced_rows']}/{r['sig_rows']}) fills={r['fills']}")

# backtest admission side
print("\n== dataset audit: included by league ==")
audit = pq.read_table(E / "data/lol/processed/datasets/audit.parquet").to_pandas()
uni = pq.read_table(E / "data/lol/processed/universe/markets.parquet",
                  columns=["event_id", "league"]).to_pandas()
event_league = uni.dropna(subset=["league"]).drop_duplicates("event_id").set_index("event_id")["league"]
audit["event_id"] = audit["event_id"].astype(str)
el = event_league.copy()
el.index = el.index.astype(str)
audit["pm_league"] = audit["event_id"].map(el)
audit["base_league"] = audit["pm_league"].map(lambda x: None if x is None else (
    x if x in WL.leagues else WL.aliases.get(x, x)))
audit["whitelisted"] = audit["base_league"].map(lambda x: x in WL.leagues if x else False)
audit["no_feed"] = audit["base_league"].map(lambda x: x in WL.no_live_feed if x else False)
sel = audit[audit["whitelisted"] & ~audit["no_feed"]]
print(f"audit rows: {len(audit)}; whitelisted-not-no_feed: {len(sel)}; included: {int(sel['included'].sum())}")
print(sel.groupby("base_league")["included"].agg(["sum", "count"]).sort_values("count", ascending=False).to_string())
print("\nreason counts within backtest-scope events:")
print(sel["reason"].value_counts().to_string())
