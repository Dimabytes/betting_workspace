"""Control B gap: grok-sim vs engine on the same 16 maps, fill by fill.

Run from esports-trader's venv:
  uv run --project ../../../../../esports-trader python control_b_gap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
R = HERE.parents[1]
sys.path.insert(0, str(R / "work" / "grok-sim"))
import sim  # noqa: E402

ET = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
RUN = ET / "data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_twosided-control-b/seed0"
CFG = {"h": 3, "g": 0.0002, "nmax": 100, "s": 20, "taker": False, "pessimistic": False, "sticky": False}
US = 1_000  # ns per us


def load_maps() -> pd.DataFrame:
    maps = pd.read_parquet(R / "work/grok-sim/selected_maps.parquet").reset_index(drop=True)
    maps["map_id"] = maps.index
    ids = [int(x) for x in (R / "work/control-b-match-ids.txt").read_text().split()]
    return maps[maps["match_id"].isin(ids)].copy()


def run_sim(maps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, fills = [], []
    for row in maps.itertuples():
        prep = sim.prepare(sim.load_tape(R / "work/grok-sim/tapes" / f"{row.map_id}.npz"))
        log: list = []
        out = sim.simulate(prep, {**CFG, "fill_log": log})
        out["match_id"] = row.match_id
        out["horn_us"] = prep["horn"]
        out["end_us"] = prep["end"]
        rows.append(out)
        for ts, side, px, qty, through, ahead, live, print_px, print_sz in log:
            fills.append(
                dict(match_id=row.match_id, ts_us=ts, side=side, px=px, qty=qty, through=through,
                     ahead=ahead, live_us=live, print_px=print_px, print_sz=print_sz,
                     game_s=(ts - prep["horn"]) / 1e6)
            )
    return pd.DataFrame(rows), pd.DataFrame(fills)


def engine_tables(maps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    res = pd.read_parquet(RUN / "results.parquet")
    fills = pd.read_parquet(RUN / "fills.parquet")
    q = pd.read_parquet(RUN / "quote_events.parquet")
    rti = maps.set_index("match_id")["radiant_token_index"]
    horn = maps.set_index("match_id")["horn_at"].map(lambda s: pd.Timestamp(s).value)
    for df in (fills, q):
        df["side"] = (df["token_index"] != df["match_id"].map(rti)).astype(int)
        df.loc[df["token_index"] < 0, "side"] = -1
        df["game_s"] = (df["ts_ns"] - df["match_id"].map(horn)) / 1e9
    return res, fills, q


def resting_intervals(q: pd.DataFrame, fills: pd.DataFrame, game_end_ns: dict[int, int]) -> pd.DataFrame:
    """One row per engine order: accepted_ns .. gone_ns (cancel_ack, full fill, or game end)."""
    acc = q[q.kind == "accepted"].groupby("order_id")["ts_ns"].min()
    sub = q[q.kind == "submitted"].set_index("order_id")
    ack = q[q.kind == "cancel_ack"].groupby("order_id")["ts_ns"].min()
    req = q[q.kind == "cancel_request"].groupby("order_id")["ts_ns"].min()
    fq = fills.groupby("order_id").agg(filled=("quantity", "sum"), last_fill_ns=("ts_ns", "max"))
    df = sub[["match_id", "side", "price", "quantity", "ts_ns"]].rename(columns={"ts_ns": "submitted_ns"})
    df["accepted_ns"] = acc
    df["cancel_req_ns"] = req
    df["cancel_ack_ns"] = ack
    df = df.join(fq)
    df["filled"] = df["filled"].fillna(0.0)
    full = df["filled"] >= df["quantity"] - 1e-6
    gone = df["cancel_ack_ns"].copy()
    gone[full] = df.loc[full, "last_fill_ns"]
    end = df["match_id"].map(game_end_ns)
    gone = gone.fillna(end)
    df["gone_ns"] = np.minimum(gone, end)
    df = df[df["accepted_ns"].notna()].copy()
    df["rest_s"] = (df["gone_ns"] - df["accepted_ns"]) / 1e9
    return df.reset_index()


def main() -> None:
    maps = load_maps()
    sim_res, sim_fills = run_sim(maps)
    res, eng_fills, q = engine_tables(maps)
    res = res[res.match_id.isin(maps.match_id)]
    eng_fills["notional"] = eng_fills.price * eng_fills.quantity
    sim_fills["notional"] = sim_fills.px * sim_fills.qty

    print("=== totals on the 16 maps ===")
    print(f"sim    pnl {sim_res.pnl.sum():8.2f}  bought {sim_res.buy_notional.sum():9.1f}  "
          f"c/$ {100 * sim_res.pnl.sum() / sim_res.buy_notional.sum():.2f}  fills {sim_res.n_fills.sum()}")
    eng_pnl = res.engine_pnl.sum() + eng_fills.maker_rebate.sum()
    print(f"engine pnl {res.engine_pnl.sum():8.2f} (+rebate {eng_fills.maker_rebate.sum():.2f} = {eng_pnl:.2f})  "
          f"bought {eng_fills.notional.sum():9.1f}  c/$ {100 * eng_pnl / eng_fills.notional.sum():.2f}  fills {len(eng_fills)}")

    print("\n=== per map ===")
    per = sim_res.set_index("match_id")[["pnl", "buy_notional", "n_fills"]].rename(
        columns={"pnl": "sim_pnl", "buy_notional": "sim_buy", "n_fills": "sim_fills"})
    e = res.set_index("match_id")
    per["eng_pnl"] = e.engine_pnl
    per["eng_buy"] = eng_fills.groupby("match_id").notional.sum()
    per["eng_fills"] = e.buy_fills
    per["eng_submitted"] = e.orders_submitted
    per["window_s"] = e.window_seconds
    per["live_s"] = e.live_order_seconds
    gate = pd.DataFrame(e.gate_seconds.tolist(), index=e.index)
    per = per.join(gate)
    per["slug"] = e.slug
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(per.fillna(0).round(1).to_string())

    print("\n=== sim fills: through vs at-price ===")
    g = sim_fills.groupby("through").agg(n=("qty", "size"), shares=("qty", "sum"), notional=("notional", "sum"))
    print(g)
    print("sim fills with unknown/zero queue ahead:", (sim_fills.ahead == 0).mean().round(3))
    print("sim at-price fills: median ahead", sim_fills[~sim_fills.through].ahead.median())

    print("\n=== engine fills: queue_ahead at submit, is_maker ===")
    print(eng_fills[["queue_ahead", "quantity", "price"]].describe().round(2))
    print("engine fills per side:", eng_fills.groupby("side").size().to_dict(), " sim:", sim_fills.groupby("side").size().to_dict())

    game_end_ns = {int(r.match_id): pd.Timestamp(r.game_ended_at).value for r in res.itertuples()}
    rest = resting_intervals(q, eng_fills, game_end_ns)
    rest.to_parquet(HERE / "control_b_engine_orders.parquet", index=False)
    sim_fills.to_parquet(HERE / "control_b_sim_fills.parquet", index=False)
    eng_fills.to_parquet(HERE / "control_b_engine_fills.parquet", index=False)

    print("\n=== engine order life ===")
    print("orders accepted:", len(rest), " median rest_s", rest.rest_s.median().round(3),
          " p25", rest.rest_s.quantile(0.25).round(3), " p75", rest.rest_s.quantile(0.75).round(3))
    print("submit->accept ms median", ((rest.accepted_ns - rest.submitted_ns) / 1e6).median())
    print("cancel req->ack ms median", ((rest.cancel_ack_ns - rest.cancel_req_ns) / 1e6).median())

    # coverage: for each sim fill, what did the engine have resting on that side at that time?
    print("\n=== each sim fill vs engine state at that instant ===")
    cats = []
    for f in sim_fills.itertuples():
        ts_ns = f.ts_us * US
        r = rest[(rest.match_id == f.match_id) & (rest.side == f.side)
                 & (rest.accepted_ns <= ts_ns) & (rest.gone_ns > ts_ns)]
        if r.empty:
            # was anything about to be live (submitted but not yet accepted)?
            pend = rest[(rest.match_id == f.match_id) & (rest.side == f.side)
                        & (rest.submitted_ns <= ts_ns) & (rest.accepted_ns > ts_ns)]
            cats.append("in_flight" if not pend.empty else "no_order")
            continue
        px = float(r.price.iloc[0])
        if abs(px - f.px) < 1e-6:
            cats.append("same_px_no_fill")
        elif px > f.px:
            cats.append("eng_px_higher")
        else:
            cats.append("eng_px_lower")
    sim_fills["engine_state"] = cats
    # did the engine fill the same side within +-2s?
    hit = []
    for f in sim_fills.itertuples():
        ts_ns = f.ts_us * US
        m = eng_fills[(eng_fills.match_id == f.match_id) & (eng_fills.side == f.side)
                      & ((eng_fills.ts_ns - ts_ns).abs() <= 2_000_000_000)]
        hit.append(not m.empty)
    sim_fills["engine_filled_near"] = hit
    summary = sim_fills.groupby(["engine_state", "engine_filled_near"]).agg(
        n=("qty", "size"), notional=("notional", "sum"), through_share=("through", "mean")).round(2)
    print(summary)
    print("\nby through flag:")
    print(sim_fills.groupby(["through", "engine_state"]).agg(n=("qty", "size"), notional=("notional", "sum")).round(1))
    sim_fills.to_parquet(HERE / "control_b_sim_fills.parquet", index=False)

    # no_order breakdown: what was the engine's no_quote reason at that second, if any
    nq = q[q.kind == "no_quote"].copy()
    nq["sec"] = nq.ts_ns // 1_000_000_000
    reasons = []
    for f in sim_fills[sim_fills.engine_state == "no_order"].itertuples():
        sec = (f.ts_us * US) // 1_000_000_000
        m = nq[(nq.match_id == f.match_id) & (nq.sec.between(sec - 1, sec + 1))]
        reasons.append(m.reason.iloc[0] if not m.empty else "churn/band/size")
    print("\nno_order reasons:", pd.Series(reasons).value_counts().to_dict())


if __name__ == "__main__":
    main()
