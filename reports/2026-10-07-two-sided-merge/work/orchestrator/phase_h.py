"""Phase-/jump-dependent half-spread on grok-sim's 30 tapes. Reuses work/grok-sim/sim.py."""

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SIM = HERE.parent / "grok-sim"
sys.path.insert(0, str(SIM))
import sim  # noqa: E402

BASE = {"g": 0.0002, "nmax": 100, "taker": False, "sticky": False}
VARIANTS = {
    "h1 flat (control)": {"h": 1},
    "h3 flat (control)": {"h": 3},
    "phase 1/2/3": {"h": 1, "h_mid": 2, "h_late": 3},
    "phase 1/3/3": {"h": 1, "h_mid": 3, "h_late": 3},
    "phase 2/2/3": {"h": 2, "h_mid": 2, "h_late": 3},
    "h1 + jump+2": {"h": 1, "c_jump": 2},
    "h2 + jump+1": {"h": 2, "c_jump": 1},
    "phase 1/2/3 + jump+1": {"h": 1, "h_mid": 2, "h_late": 3, "c_jump": 1},
}
EXTRA = {
    "phase 1/2/3 + jump+1": {"h": 1, "h_mid": 2, "h_late": 3, "c_jump": 1},
    "phase 1/2/3 + jump+2": {"h": 1, "h_mid": 2, "h_late": 3, "c_jump": 2},
    "phase 1/2/3 + jump+3": {"h": 1, "h_mid": 2, "h_late": 3, "c_jump": 3},
    "h1 + jump+3": {"h": 1, "c_jump": 3},
    "phase 1/1/2 + jump+2": {"h": 1, "h_mid": 1, "h_late": 2, "c_jump": 2},
    "phase 1/2/2 + jump+2": {"h": 1, "h_mid": 2, "h_late": 2, "c_jump": 2},
}
if len(sys.argv) > 1 and sys.argv[1] == "extra":
    VARIANTS = EXTRA


def configs():
    out = []
    for name, v in VARIANTS.items():
        for s in (20, 50):
            for pess in (False, True):
                out.append({"name": name, "s": s, "pessimistic": pess, **BASE, **v})
    return out


def run_map(map_id: int) -> pd.DataFrame:
    tape = sim.prepare(sim.load_tape(SIM / "tapes" / f"{map_id}.npz"))
    rows = []
    for cfg in configs():
        r = sim.simulate(tape, cfg)
        r.update({k: v for k, v in cfg.items()})
        r["map_id"] = map_id
        rows.append(r)
    return pd.DataFrame(rows)


def main():
    sim.self_check()
    maps = pd.read_parquet(SIM / "map_stats.parquet")["map_id"].tolist()
    with ProcessPoolExecutor(10) as ex:
        df = pd.concat(list(ex.map(run_map, maps)), ignore_index=True)
    tag = "extra" if VARIANTS is EXTRA else "main"
    df.to_parquet(HERE / f"phase_h_results_{tag}.parquet", index=False)

    # control: must reproduce grok-sim's original grid for the flat configs
    orig = pd.read_parquet(SIM / "sim_grid.parquet")
    for h, lab in ((1, "h1 flat (control)"), (3, "h3 flat (control)")):
        if lab not in VARIANTS:
            continue
        o = orig[(orig.h == h) & (orig.g == 0.0002) & (orig.nmax == 100) & (orig.s == 20) & ~orig.taker & ~orig.pessimistic & ~orig.sticky].pnl_total.iloc[0]
        n = df[(df.name == lab) & (df.s == 20) & ~df.pessimistic].pnl.sum()
        assert abs(o - n) < 1e-6, (lab, o, n)
        print("control reproduces grok-sim grid:", lab)

    agg = df.groupby(["name", "s", "pessimistic"]).agg(
        pnl=("pnl", "sum"), bought=("buy_notional", "sum"), median_map=("pnl", "median"),
        worst=("pnl", "min"), neg=("pnl", lambda x: (x < 0).mean()), fills=("n_fills", "sum"),
        peak_p95=("peak_capital", lambda x: x.quantile(0.95)),
        p0_8=("phase_pnl_0_8", "sum"), p8_20=("phase_pnl_8_20", "sum"), p20=("phase_pnl_20", "sum"),
    ).reset_index()
    agg["c_per_$"] = 100 * agg.pnl / agg.bought
    order = list(VARIANTS)
    agg["name"] = pd.Categorical(agg.name, order)
    agg = agg.sort_values(["s", "pessimistic", "name"])
    pd.set_option("display.width", 220)
    cols = ["name", "s", "pessimistic", "pnl", "bought", "c_per_$", "median_map", "worst", "neg", "fills", "peak_p95", "p0_8", "p8_20", "p20"]
    print(agg[cols].round(2).to_string(index=False))
    agg[cols].to_csv(HERE / f"phase_h_summary_{tag}.csv", index=False)


if __name__ == "__main__":
    main()
