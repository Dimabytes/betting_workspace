import numpy as np
import pandas as pd
from analyze import OUT, ROOT


def main():
    groups = []
    paired = []
    for seed in range(3):
        frames = {}
        for name in ["rebuild-20260930", "cat77lv6"]:
            run = (
                ROOT
                / "data/backtests/lol_maker"
                / ("validation_join_delta02_x015_cut480_p45_" + name)
                / f"seed{seed}"
            )
            results = pd.read_parquet(run / "results.parquet")
            fills = pd.read_parquet(run / "fills.parquet")
            rebates = fills.groupby("match_id").maker_rebate.sum()
            results["pnl_rebate"] = results.engine_pnl + results.match_id.map(rebates).fillna(0)
            results["feed"] = np.where(results.feed_source == "grid", "archive", "synthetic")
            frames[name] = results.set_index("match_id")
            for feed, maps in results.loc[~results.terminated_early].groupby("feed"):
                buys = fills.loc[fills.match_id.isin(maps.match_id) & (fills.side == "BUY")]
                groups.append(
                    {
                        "run": name,
                        "seed": seed,
                        "feed": feed,
                        "maps": len(maps),
                        "pnl": float(maps.engine_pnl.sum()),
                        "net_pnl": float(maps.pnl_rebate.sum()),
                        "buy_turnover": float((buys.price * buys.quantity).sum()),
                        "buy_quantity": float(buys.quantity.sum()),
                        "buy_markout_cents": float(buys.markout_300s.mean() * 100),
                        "weighted_buy_markout_cents": float(
                            np.average(buys.markout_300s, weights=buys.quantity) * 100
                        ),
                    }
                )
        old, new = frames["rebuild-20260930"], frames["cat77lv6"]
        keys = old.index.intersection(new.index)
        keys = keys[(~old.loc[keys, "terminated_early"]) & (~new.loc[keys, "terminated_early"])]
        for match_id in keys:
            paired.append(
                {
                    "seed": seed,
                    "match_id": int(match_id),
                    "slug": old.loc[match_id, "slug"],
                    "feed": old.loc[match_id, "feed"],
                    "old_pnl": float(old.loc[match_id, "engine_pnl"]),
                    "new_pnl": float(new.loc[match_id, "engine_pnl"]),
                    "difference": float(
                        new.loc[match_id, "engine_pnl"] - old.loc[match_id, "engine_pnl"]
                    ),
                }
            )
    pd.DataFrame(groups).to_csv(OUT / "trade_sources.csv", index=False)
    paired_frame = pd.DataFrame(paired)
    paired_frame.to_csv(OUT / "paired_trades.csv", index=False)
    by_map = (
        paired_frame.groupby(["match_id", "slug", "feed"])[["old_pnl", "new_pnl", "difference"]]
        .mean()
        .reset_index()
    )
    by_map.sort_values("difference").to_csv(OUT / "paired_maps.csv", index=False)
    print(pd.DataFrame(groups).groupby(["run", "feed"]).mean(numeric_only=True).to_string())
    print("Biggest deteriorations", by_map.nsmallest(5, "difference").to_dict("records"))
    print("Biggest improvements", by_map.nlargest(5, "difference").to_dict("records"))


if __name__ == "__main__":
    main()
