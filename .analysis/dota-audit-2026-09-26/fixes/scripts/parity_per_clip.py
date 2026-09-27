"""Live vs backtest on the same Dota maps, with live PnL scaled to the backtest clip.

Run from the esports-trader root: uv run python <this file> [RUN_DIR]
RUN_DIR defaults to data/backtests/dota_maker/LIVE. Seeds are averaged per map.

Live side, per data/trader/<session>/:
  pnl   = match.json final.pnl realized + unrealized
  buy   = BUY price x size over polymarket fills in session.jsonl, deduped by fill_key
  clip  = policy.level_usdc from the core_trace header; no core_trace -> map dropped
Scaled to the backtest clip: pnl and buy x BACKTEST_CLIP / clip.
Join key: (steam match id, condition id). Only maps where both systems bought.
Windows are kernel epochs: make parity replays sessions after the 24.09 deploy with 0 plan
mismatches and fails on older traces (missing mid_spike / kill_gate digests).
CI is a bootstrap over maps, not over series.
"""

import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKTEST_CLIP = 100.0
TRADER_ROOT = Path("data/trader")
EPOCH_EDGES = pd.to_datetime(
    ["2026-08-01T00:00:00Z", "2026-09-20T00:00:00Z", "2026-09-24T18:39:00Z", "2026-12-31T00:00:00Z"],
    utc=True,
    format="ISO8601",
)
EPOCH_LABELS = ["до 20.09", "20.09–24.09", "после деплоя 24.09"]


def read_clip(session_dir):
    for name in ("core_trace.jsonl", "core_trace.jsonl.gz"):
        path = session_dir / name
        if path.exists():
            opener = gzip.open if name.endswith(".gz") else open
            with opener(path, "rt") as handle:
                header = json.loads(handle.readline())
            return header["policy"]["level_usdc"]
    return None


def read_buy_notional(session_path):
    fills = {}
    with session_path.open() as handle:
        for line in handle:
            if '"kind":"fill"' in line and '"venue":"polymarket"' in line:
                fill = json.loads(line)
                fills[fill["fill_key"]] = fill
    return sum(f["price"] * f["size"] for f in fills.values() if f["side"] == "BUY")


def load_live():
    rows = []
    for session_dir in sorted(TRADER_ROOT.iterdir()):
        match_path = session_dir / "match.json"
        session_path = session_dir / "session.jsonl"
        if not (match_path.exists() and session_path.exists()):
            continue
        match = json.loads(match_path.read_text())
        final_pnl = (match.get("final") or {}).get("pnl") or {}
        steam_id = match.get("steam_match_id") or (session_dir.name if session_dir.name.isdigit() else None)
        clip = read_clip(session_dir)
        if match.get("game") != "dota" or final_pnl.get("realized_pnl_usdc") is None or not steam_id or not clip:
            continue
        scale = BACKTEST_CLIP / clip
        rows.append(
            dict(
                session=session_dir.name,
                match_id=int(steam_id),
                condition_id=match["market"]["condition_id"],
                joined_at=pd.Timestamp(match["joined_at_utc"]),
                feed=match["feed_source"],
                model=match["model"]["name"],
                clip=clip,
                live_pnl=(final_pnl["realized_pnl_usdc"] + (final_pnl.get("unrealized_pnl_usdc") or 0)) * scale,
                live_buy=read_buy_notional(session_path) * scale,
            )
        )
    return pd.DataFrame(rows)


def load_backtest(run_dir):
    per_seed = []
    for seed_dir in sorted(path for path in run_dir.glob("seed*") if path.is_dir()):
        results = pd.read_parquet(seed_dir / "results.parquet", columns=["match_id", "condition_id", "signal_mode", "engine_pnl"])
        fills = pd.read_parquet(seed_dir / "fills.parquet", columns=["match_id", "side", "price", "quantity"])
        buys = fills[fills["side"].astype(str).str.upper().str.contains("BUY")]
        buy_by_match = (buys["price"] * buys["quantity"]).groupby(buys["match_id"]).sum()
        results["bt_buy"] = results["match_id"].map(buy_by_match).fillna(0.0)
        per_seed.append(results)
    stacked = pd.concat(per_seed)
    return stacked.groupby(["match_id", "condition_id", "signal_mode"], as_index=False)[["engine_pnl", "bt_buy"]].mean()


def bootstrap_ci(values, draws=10_000):
    rng = np.random.default_rng(0)
    sums = [rng.choice(values, len(values)).sum() for _ in range(draws)]
    return np.percentile(sums, [2.5, 97.5])


def summarize(group):
    difference = (group["live_pnl"] - group["engine_pnl"]).to_numpy()
    low, high = bootstrap_ci(difference)
    return pd.Series(
        {
            "maps": len(group),
            "live_$": group["live_pnl"].sum(),
            "bt_$": group["engine_pnl"].sum(),
            "diff_ci_low": low,
            "diff_ci_high": high,
            "live_buy": group["live_buy"].sum(),
            "bt_buy": group["bt_buy"].sum(),
            "buy_ratio": group["live_buy"].sum() / group["bt_buy"].sum(),
            "live_%": 100 * group["live_pnl"].sum() / group["live_buy"].sum(),
            "bt_%": 100 * group["engine_pnl"].sum() / group["bt_buy"].sum(),
        }
    )


def main():
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/backtests/dota_maker/LIVE")
    joined = load_backtest(run_dir).merge(load_live(), on=["match_id", "condition_id"], how="inner")
    both = joined[(joined["live_buy"] > 0) & (joined["bt_buy"] > 0)].copy()
    both["epoch"] = pd.cut(both["joined_at"], EPOCH_EDGES, labels=EPOCH_LABELS)
    pd.set_option("display.width", 200)
    modes = joined["signal_mode"].value_counts().to_dict()
    print(f"run: {run_dir.resolve().name}; joined maps: {len(joined)} {modes}; both bought: {len(both)}")
    print(both.groupby(["epoch", "feed"], observed=True).apply(summarize, include_groups=False).round(2).to_string())
    print(summarize(both).round(2).to_string())
    spearman = both["live_pnl"].corr(both["engine_pnl"], method="spearman")
    same_sign = int(((both["live_pnl"] > 0) == (both["engine_pnl"] > 0)).sum())
    print(f"per map: spearman {spearman:.2f}, same sign {same_sign} of {len(both)}")


if __name__ == "__main__":
    main()
