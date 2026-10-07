"""Load every live Dota map from the trader archives into one parquet of fills + one of maps."""
import json, sys
from pathlib import Path
import pandas as pd

ROOTS = [("live", Path("/root/work/esports-trader/data/trader_live")),
         ("legacy", Path("/root/work/esports-trader/data/live_paper"))]
FEE_RATE = 0.05
REBATE_RATE = 0.15

def rebate(price, size, maker):
    if not maker or not (0 < price < 1) or size <= 0:
        return 0.0
    return REBATE_RATE * FEE_RATE * size * price * (1 - price)

def iter_jsonl(p):
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

seen = set()
maps = []
fills = []
for tree, root in ROOTS:
    if not root.is_dir():
        continue
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name == "wallet":
            continue
        mj = d / "match.json"
        sj = d / "session.jsonl"
        if not mj.is_file() or not sj.is_file():
            continue
        try:
            meta = json.loads(mj.read_text())
        except Exception:
            continue
        game = meta.get("game") or "dota"
        if game != "dota":
            continue
        start = None; end = None; mfills = []
        nsig = 0
        for r in iter_jsonl(sj):
            k = r.get("kind")
            if k == "session_start":
                start = r
            elif k == "session_end":
                end = r
            elif k in ("fill", "late_fill"):
                mfills.append(r)
        if start is None:
            continue
        mode = start.get("execution_mode")
        if mode != "live":
            continue
        mid = meta.get("match_id") or d.name
        key = (mid, (meta.get("market") or {}).get("condition_id"))
        if key in seen:
            continue
        seen.add(key)
        market = meta.get("market") or {}
        final = meta.get("final") or {}
        winner = final.get("winner")
        yes_tok = market.get("yes_token_id"); no_tok = market.get("no_token_id")
        yes_is_radiant = market.get("yes_is_radiant")
        teams = meta.get("teams") or {}
        model = (start.get("model") or {}).get("name")
        maps.append(dict(
            tree=tree, match_id=mid, dir=str(d), slug=market.get("market_slug"),
            event_slug=market.get("event_slug"),
            series=market.get("grid_series_id") or market.get("event_slug"),
            radiant=teams.get("radiant"), dire=teams.get("dire"), map_number=meta.get("map_number"),
            league_id=meta.get("league_id"), joined=meta.get("joined_at_utc"), horn=meta.get("horn_at_utc"),
            winner=winner, yes_is_radiant=yes_is_radiant, model=model,
            git=start.get("git_commit"), clip=start.get("clip_usdc"), clip_reason=start.get("clip_reason"),
            feed=meta.get("feed_source"), has_end=end is not None,
            end_net_cash=(end or {}).get("net_cash"), end_inv=(end or {}).get("inventory_value"),
            n_fills=len(mfills), duration=final.get("duration_seconds"),
        ))
        for r in mfills:
            tok = str(r.get("token_id"))
            if tok == str(yes_tok):
                is_yes = True
            elif tok == str(no_tok):
                is_yes = False
            else:
                is_yes = None
            tok_radiant = None if (is_yes is None or yes_is_radiant is None) else (is_yes == bool(yes_is_radiant))
            won = None
            if winner in ("radiant", "dire") and tok_radiant is not None:
                won = (winner == "radiant") == tok_radiant
            price = float(r.get("price") or 0); size = float(r.get("size") or 0)
            fills.append(dict(
                match_id=mid, kind=r.get("kind"), ts=r.get("ts_utc"), second=r.get("second"),
                side=r.get("side"), token=tok, is_yes=is_yes, tok_radiant=tok_radiant, won=won,
                price=price, qty=size, maker=bool(r.get("is_maker")), pos_after=r.get("position_after"),
                rebate=rebate(price, size, bool(r.get("is_maker"))), fill_key=r.get("fill_key"),
            ))
M = pd.DataFrame(maps); F = pd.DataFrame(fills)
for c in ("end_net_cash", "end_inv", "clip"):
    M[c] = pd.to_numeric(M[c], errors="coerce")
for c in ("second", "pos_after"):
    F[c] = pd.to_numeric(F[c], errors="coerce")
M["league_id"] = pd.to_numeric(M["league_id"], errors="coerce")
M["map_number"] = pd.to_numeric(M["map_number"], errors="coerce")
M["duration"] = pd.to_numeric(M["duration"], errors="coerce")
for c in ("series", "yes_is_radiant"):
    M[c] = M[c].astype(str)
M.to_parquet("live_maps.parquet"); F.to_parquet("live_fills.parquet")
print(len(M), "maps;", len(F), "fills;", M[M.n_fills > 0].shape[0], "traded maps")
print(M.groupby("tree").size())
print(M.winner.value_counts(dropna=False))
