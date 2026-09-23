"""Backtest (GRID archive schedule) vs live on the same LoL maps, joined by condition_id."""
import glob, json, os
import numpy as np
import pandas as pd

E = "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader"


def live_rows(game: str) -> pd.DataFrame:
    rows = []
    for d in glob.glob(f"{E}/data/trader/*/"):
        mj, sj = os.path.join(d, "match.json"), os.path.join(d, "session.jsonl")
        if not (os.path.exists(mj) and os.path.exists(sj)):
            continue
        m = json.load(open(mj))
        if m.get("game", "dota") != game:
            continue
        mode, model, buy, fills, end = None, None, 0.0, 0, None
        for line in open(sj):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "session_start":
                mode = r.get("execution_mode"); model = (r.get("model") or {}).get("name")
            elif k == "fill" and r.get("price") is not None:
                fills += 1
                if r["side"] == "BUY":
                    buy += r["price"] * r["size"]
            elif k == "session_end":
                end = r
        pnl = None
        if end is not None and end.get("venue") == "polymarket":
            try:
                pnl = float(end.get("net_cash") or 0) + float(end.get("inventory_value") or 0)
            except Exception:
                pnl = None
        rows.append(dict(dir=os.path.basename(d.rstrip("/")), condition_id=m["market"]["condition_id"],
                         slug=m["market"].get("market_slug"), mode=mode, model=model, live_fills=fills,
                         live_buy=buy, live_pnl=pnl, joined=m.get("joined_at_utc", "")[:16],
                         tournament=m.get("tournament")))
    return pd.DataFrame(rows)


def backtest_rows(game_dir: str) -> pd.DataFrame:
    parts = []
    for s in (0, 1, 2):
        r = pd.read_parquet(f"{E}/data/backtests/{game_dir}/LIVE/seed{s}/results.parquet")
        f = pd.read_parquet(f"{E}/data/backtests/{game_dir}/LIVE/seed{s}/fills.parquet")
        buy = (f[f.side == "BUY"].assign(n=lambda x: x.price * x.quantity).groupby("match_id")["n"].sum())
        r = r.assign(bt_buy=r["match_id"].map(buy).fillna(0.0), seed=s)
        parts.append(r[["match_id", "condition_id", "slug", "feed_source", "engine_pnl", "buy_fills", "bt_buy", "seed"]])
    all_seeds = pd.concat(parts)
    return all_seeds.groupby(["match_id", "condition_id", "slug", "feed_source"], as_index=False).agg(
        bt_pnl=("engine_pnl", "mean"), bt_buy=("bt_buy", "mean"), bt_fills=("buy_fills", "mean"))


for game, game_dir in (("lol", "lol_maker"), ("dota", "dota_maker")):
    live = live_rows(game)
    bt = backtest_rows(game_dir)
    j = bt.merge(live, on="condition_id", how="inner", suffixes=("_bt", "_live"))
    j = j[j["mode"] == "live"]
    both = j[(j.live_buy > 0) & (j.bt_buy > 0)]
    print(f"== {game}: backtest maps={len(bt)} joined live-mode={len(j)} both traded={len(both)}")
    print(j.groupby("feed_source").size().to_dict())
    for label, sub in (("all joined", j), ("both traded", both)):
        lb, lp = sub.live_buy.sum(), sub.live_pnl.fillna(0).sum()
        bb, bp = sub.bt_buy.sum(), sub.bt_pnl.sum()
        print(f"  {label}: n={len(sub)} live traded={int((sub.live_buy>0).sum())} bt traded={int((sub.bt_buy>0).sum())} "
              f"live pnl/buy={100*lp/lb if lb else float('nan'):.2f}% (pnl {lp:.2f} on {lb:.0f}) "
              f"bt pnl/buy={100*bp/bb if bb else float('nan'):.2f}% (pnl {bp:.2f} on {bb:.0f})")
    if len(both) > 3:
        lr = both.live_pnl / both.live_buy
        br = both.bt_pnl / both.bt_buy
        print(f"  per-map return corr (both traded) = {np.corrcoef(lr, br)[0,1]:.3f}; sign agree = {float(np.mean(np.sign(lr)==np.sign(br))):.2f}")
    print("  models:", j.groupby("model").size().to_dict())
    out = f"/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/orchestrator/same_map_{game}.csv"
    j.to_csv(out, index=False)
    print("  ->", out)
