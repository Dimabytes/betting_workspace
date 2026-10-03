import json

import numpy as np
import pandas as pd
from analyze import DATA, OUT, ROOT, bootstrap_events, calculate_metrics


def main():
    validation = pd.read_parquet(DATA / "validation.parquet", filters=[("second", "<", 480)])
    validation = validation.loc[validation.signal_market_p_radiant_300s.notna()].reset_index(
        drop=True
    )
    keys = validation[["match_id", "second"]].to_numpy(dtype=np.int64)
    assert np.array_equal(keys, np.load(OUT / "ablation_keys.npy"))
    runs = ROOT / "data/backtests/lol_maker"
    selected = pd.read_parquet(
        runs / "validation_join_delta02_x015_cut480_p45_cat77lv6/seed0/results.parquet"
    )
    mask = validation.match_id.isin(selected.match_id).to_numpy()
    rows = []
    predictions = {}
    for path in sorted(OUT.glob("ablation*delta_*.npy")):
        delta = np.load(path)
        predictions[path.stem] = delta
        for cohort, included in [("all", np.ones(len(mask), dtype=bool)), ("selected", mask)]:
            rows.append(
                {
                    "variant": path.stem,
                    "cohort": cohort,
                    **calculate_metrics(validation.loc[included], delta[included]),
                }
            )
    quality = pd.DataFrame(rows)
    quality.to_csv(OUT / "ablation_quality.csv", index=False)
    comparisons = [
        ("history_early", "snapshot17_early", "catalog77_early"),
        ("history_full", "snapshot17_full", "catalog77_full"),
        ("early_training_77", "catalog77_full", "catalog77_early"),
    ]
    paired = {}
    target = validation.signal_market_p_radiant_300s.to_numpy(dtype=np.float64)
    current = validation.market_p_radiant.to_numpy(dtype=np.float64)
    for name, before, after in comparisons:
        old = predictions[f"ablation_delta_{before}"]
        new = predictions[f"ablation_delta_{after}"]
        difference = (
            np.abs(np.clip(current + old, 0, 1) - target)
            - np.abs(np.clip(current + new, 0, 1) - target)
        ) * 100
        direction_difference = (
            (np.where(new >= 0, 1, -1) - np.where(old >= 0, 1, -1)) * (target - current) * 100
        )
        paired[name] = {
            "selected_mae_improvement_cents": bootstrap_events(
                validation.loc[mask], difference[mask]
            ),
            "selected_directional_improvement_cents": bootstrap_events(
                validation.loc[mask], direction_difference[mask]
            ),
        }
    (OUT / "ablation_paired_quality.json").write_text(json.dumps(paired, indent=2))
    report = json.loads((OUT / "diagnostics.json").read_text())
    rows = []
    for name in ["rebuild-20260930", "cat77lv6", "cat77"]:
        values = report["backtests"][name]
        means = values["means"]
        rows.append(
            {
                "run": name,
                "net_pnl": means["net_pnl"],
                "pnl_sd_between_seeds": values["sd"]["net_pnl"],
                "buy_turnover": values["buy_turnover"],
                "cents_per_bought_share": values["pnl_per_share_cents"],
                "cents_per_bought_share_with_rebate": values["pnl_per_share_rebate_cents"],
                "cvar5": means["cvar_5"],
                "worst_map": means["worst_match"],
                "loss_map_rate": means["loss_match_rate"],
                "buy_300s_cents": means["buy_300s"] * 100,
            }
        )
    pd.DataFrame(rows).to_csv(OUT / "backtest_comparison.csv", index=False)
    print(quality.loc[quality.cohort == "selected"].to_string(index=False))
    print(json.dumps(paired, indent=2))


if __name__ == "__main__":
    main()
