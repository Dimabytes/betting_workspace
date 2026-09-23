"""Split LIVE seed0 PnL and buy markout by signal_mode. Read-only."""

from pathlib import Path

import pandas as pd

ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/backtests")
RUN = "validation_join_delta02_x015_cut480_p35_s06-playback-gf"


def weighted_markout(fills: pd.DataFrame, column: str) -> float:
    buys = fills[(fills["side"] == "BUY") & fills[column].notna()]
    qty = buys["quantity"].sum()
    if qty == 0:
        return float("nan")
    return float((buys[column] * buys["quantity"]).sum() / qty)


def report(game: str) -> None:
    seed = ROOT / f"{game}_maker" / RUN / "seed0"
    results = pd.read_parquet(seed / "results.parquet", columns=["match_id", "signal_mode", "feed_source", "engine_pnl", "buy_fills"])
    fills = pd.read_parquet(
        seed / "fills.parquet",
        columns=["match_id", "side", "quantity", "markout_30s", "markout_300s"],
    )
    merged = fills.merge(results[["match_id", "signal_mode", "feed_source"]], on="match_id", how="left")
    print(f"\n=== {game} seed0 ===")
    print("maps", results.groupby(["signal_mode", "feed_source"], dropna=False).size().to_string())
    print("engine_pnl", results.groupby(["signal_mode", "feed_source"], dropna=False)["engine_pnl"].sum().to_string())
    buys = merged[merged["side"] == "BUY"]
    for (mode, feed), group in buys.groupby(["signal_mode", "feed_source"], dropna=False):
        qty = group["quantity"].sum()
        m30 = (group["markout_30s"] * group["quantity"]).sum() / qty
        m300 = (group["markout_300s"] * group["quantity"]).sum() / qty
        print(
            f"BUY {mode}:{feed or '-'} fills={len(group)} qty={qty:.1f} "
            f"markout30={m30 * 100:.3f}c markout300={m300 * 100:.3f}c"
        )
    print(
        "BUY all",
        f"markout30={weighted_markout(merged, 'markout_30s') * 100:.3f}c",
        f"markout300={weighted_markout(merged, 'markout_300s') * 100:.3f}c",
    )


def main() -> None:
    report("lol")
    report("dota")


if __name__ == "__main__":
    main()
