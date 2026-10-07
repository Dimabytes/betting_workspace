"""Which engine behaviours explain the control-B gap? Mimic each one inside grok-sim on the 16 maps."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
R = HERE.parents[1]
sys.path.insert(0, str(R / "work" / "grok-sim"))
import sim  # noqa: E402

from control_b_gap import CFG, load_maps  # noqa: E402

VARIANTS = {
    "base (grok-sim)": {},
    "floor_px": {"floor_px": True},
    "fair_pair": {"fair_pair": True},
    "band_hi 0.90": {"band_hi": 0.90},
    "shrink_add": {"shrink_add": True},
    "nautilus_fill": {"nautilus_fill": True},
    "through_cap (price priority, qty=print)": {"through_cap": True},
    "through_cap, pessimistic": {"through_cap": True, "pessimistic": True},
    "band 0.90 both legs": {"band_both": True},
    "floor+fair_pair": {"floor_px": True, "fair_pair": True},
    "floor+fair+shrink+band": {"floor_px": True, "fair_pair": True, "shrink_add": True, "band_hi": 0.90},
    "all five": {"floor_px": True, "fair_pair": True, "shrink_add": True, "band_hi": 0.90, "nautilus_fill": True},
    "all five, pessimistic": {"floor_px": True, "fair_pair": True, "shrink_add": True, "band_hi": 0.90,
                              "nautilus_fill": True, "pessimistic": True},
}


def main() -> None:
    sim.self_check()
    maps = load_maps()
    preps = {row.match_id: sim.prepare(sim.load_tape(R / "work/grok-sim/tapes" / f"{row.map_id}.npz"))
             for row in maps.itertuples()}
    rows = []
    for name, extra in VARIANTS.items():
        for match_id, prep in preps.items():
            out = sim.simulate(prep, {**CFG, **extra})
            out["variant"] = name
            out["match_id"] = match_id
            rows.append(out)
    df = pd.DataFrame(rows)
    df.to_parquet(HERE / "control_b_ablate.parquet", index=False)
    g = df.groupby("variant", sort=False).agg(
        pnl=("pnl", "sum"), bought=("buy_notional", "sum"), fills=("n_fills", "sum"),
        merged=("merged_pairs", "sum"), worst=("pnl", "min"), neg_maps=("pnl", lambda s: int((s < 0).sum())))
    g["c_per_$"] = (100 * g.pnl / g.bought).round(2)
    g["pnl"] = g.pnl.round(1)
    g["bought"] = g.bought.round(0)
    g["worst"] = g.worst.round(1)
    pd.set_option("display.width", 200)
    print(g.to_string())
    print("\nengine control B: pnl 27.2 (14.2 + 13.0 rebate)  bought 3942  fills 670  c/$ 0.69")
    base = df[df.variant == "base (grok-sim)"].pnl.sum()
    assert abs(base - 261.68) < 0.05, base


if __name__ == "__main__":
    main()
