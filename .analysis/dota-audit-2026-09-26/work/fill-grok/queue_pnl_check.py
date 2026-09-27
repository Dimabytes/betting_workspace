"""Check all-fills cash+settlement against engine_pnl before the queue scan."""

import json
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
LIVE = E / "data/backtests/dota_maker/validation_join_delta02_x015_cut480_p4_archive-s3-20260924"
CAT = E / "data/new_processed/match_catalog/match_catalog.parquet"

cat = pq.read_table(
    CAT,
    columns=["match_id", "radiant_win", "radiant_token_index"],
).to_pydict()
win = {}
for i, mid in enumerate(cat["match_id"]):
    win[int(mid)] = (bool(cat["radiant_win"][i]), int(cat["radiant_token_index"][i]))

out = {}
for seed in (0, 1, 2):
    root = LIVE / f"seed{seed}"
    fills = pq.read_table(
        root / "fills.parquet",
        columns=["match_id", "token_index", "side", "price", "quantity", "maker_rebate"],
    ).to_pydict()
    results = pq.read_table(
        root / "results.parquet",
        columns=["match_id", "engine_pnl", "cash_flow", "signal_mode"],
    ).to_pydict()
    engine = {int(m): float(p) for m, p in zip(results["match_id"], results["engine_pnl"])}
    cash_r = {int(m): float(c) for m, c in zip(results["match_id"], results["cash_flow"])}
    inv = {}
    cash = {}
    rebate = 0.0
    for i, mid in enumerate(fills["match_id"]):
        mid = int(mid)
        tok = int(fills["token_index"][i])
        qty = float(fills["quantity"][i])
        px = float(fills["price"][i])
        rebate += float(fills["maker_rebate"][i] or 0.0)
        key = (mid, tok)
        held, spent = inv.get(key, (0.0, 0.0))
        if fills["side"][i] == "BUY":
            inv[key] = (held + qty, spent + px * qty)
            cash[mid] = cash.get(mid, 0.0) - px * qty
        else:
            take = min(qty, held)
            inv[key] = (held - take, spent)
            cash[mid] = cash.get(mid, 0.0) + px * qty
    settle = {}
    for (mid, tok), (qty, _spent) in inv.items():
        if qty <= 1e-9 or mid not in win:
            continue
        radiant_win, radiant_idx = win[mid]
        won = (tok == radiant_idx) == radiant_win
        settle[mid] = settle.get(mid, 0.0) + qty * (1.0 if won else 0.0)
    recon = 0.0
    eng = 0.0
    worst = 0.0
    cash_delta = 0.0
    for mid, pnl in engine.items():
        got = cash.get(mid, 0.0) + settle.get(mid, 0.0)
        recon += got
        eng += pnl
        worst = max(worst, abs(got - pnl))
        cash_delta = max(cash_delta, abs(cash.get(mid, 0.0) - cash_r.get(mid, 0.0)))
    out[seed] = {
        "engine": round(eng, 4),
        "recon_cash_settle": round(recon, 4),
        "delta": round(recon - eng, 4),
        "worst_match": round(worst, 6),
        "worst_cash_vs_results": round(cash_delta, 6),
        "rebate": round(rebate, 4),
        "fills": len(fills["match_id"]),
        "modes": sorted(set(results["signal_mode"])),
    }
print(json.dumps(out, indent=2))
