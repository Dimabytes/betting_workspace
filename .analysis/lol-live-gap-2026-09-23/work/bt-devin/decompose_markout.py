"""Decompose LoL vs Dota BUY markout_30s by signal mode, timing, price, league, events."""
import sys
from bisect import bisect_right
from pathlib import Path

import numpy as np
import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23")


def wavg(df, col, wcol="quantity"):
    w = df[wcol].to_numpy(dtype=float)
    if w.sum() == 0 or len(df) == 0:
        return np.nan
    return float(np.average(df[col].to_numpy(dtype=float), weights=w))


def load_side(game):
    base = E / f"data/backtests/{'lol_maker' if game=='lol' else 'dota_maker'}/LIVE/seed0"
    fills = pd.read_parquet(base / "fills.parquet")
    results = pd.read_parquet(base / "results.parquet")
    fills = fills.merge(
        results[["match_id", "signal_mode", "engine_pnl", "slug"]].rename(
            columns={"engine_pnl": "match_pnl"}),
        on="match_id", how="left")
    return fills, results


def add_game_second(fills, sig_path, second_col="second", state_col="state_ts_us"):
    """For each fill, game second = last dataset second whose state_ts_us <= fill ts."""
    sig = pd.read_parquet(
        sig_path,
        columns=["match_id", second_col, state_col, "deaths_radiant", "deaths_dire",
                 "radiant_nw_adv"])
    out = np.full(len(fills), np.nan)
    for mid, grp in fills.groupby("match_id"):
        rows = sig[sig.match_id == mid].sort_values(state_col)
        if rows.empty:
            continue
        ts = rows[state_col].to_numpy(dtype=np.int64) * 1000  # us -> ns
        secs = rows[second_col].to_numpy(dtype=np.int64)
        pos = np.searchsorted(ts, grp["ts_ns"].to_numpy(), side="right") - 1
        vals = np.where(pos >= 0, secs[np.clip(pos, 0, len(secs) - 1)], np.nan).astype(float)
        vals[pos < 0] = np.nan
        out[grp.index] = vals
    fills["game_second"] = out
    return fills, sig


def add_time_since_event(fills, sig, game):
    """seconds since last second where deaths_total increased or |d nw_adv| >= 400."""
    out = np.full(len(fills), np.nan)
    nw_thr = 400 if game == "lol" else 1000
    for mid, grp in fills.groupby("match_id"):
        rows = sig[sig.match_id == mid].sort_values("second" if "second" in sig else "game_second")
        if rows.empty:
            continue
        scol = "second" if "second" in rows else "game_second"
        deaths = (rows["deaths_radiant"] + rows["deaths_dire"]).to_numpy(dtype=float)
        nw = np.abs(np.diff(rows["radiant_nw_adv"].to_numpy(dtype=float), prepend=np.nan))
        ddeaths = np.diff(deaths, prepend=np.nan)
        events = (ddeaths > 0) | (nw >= nw_thr)
        event_secs = rows[scol].to_numpy()[events]
        gs = grp["game_second"].to_numpy(dtype=float)
        pos = np.searchsorted(event_secs, gs, side="right") - 1
        tse = np.where(pos >= 0, gs - event_secs[np.clip(pos, 0, len(event_secs) - 1)], np.nan)
        out[grp.index] = tse
    fills["t_since_event"] = out
    return fills


def bucket_report(fills, name, key, min_qty=2000):
    g = fills.groupby(key, dropna=False)
    rows = []
    for k, d in g:
        if d["quantity"].sum() < min_qty:
            continue
        rows.append({
            "bucket": k, "n": len(d), "qty": round(d.quantity.sum(), 0),
            "mk30_c": round(100 * wavg(d, "markout_30s"), 3),
            "mk300_c": round(100 * wavg(d, "markout_300s"), 3),
            "sig_age": round(wavg(d, "signal_age_seconds"), 1),
            "spread_c": round(100 * wavg(d, "spread"), 2),
            "price": round(wavg(d, "price"), 3),
        })
    rep = pd.DataFrame(rows).sort_values("bucket")
    print(f"\n--- {name} by {key} ---")
    print(rep.to_string(index=False))
    return rep


def main():
    out = R / "work/bt-devin/markout_decomp.txt"
    with open(out, "w") as fh:
        sys.stdout = fh
        for game in ("lol", "dota"):
            fills, results = load_side(game)
            buys = fills[fills.side == "BUY"].copy().reset_index(drop=True)
            print(f"===== {game} BUY fills={len(buys)} qty={buys.quantity.sum():.0f} =====")
            print(f"overall mk30 {100*wavg(buys,'markout_30s'):.3f}c mk300 {100*wavg(buys,'markout_300s'):.3f}c")
            if game == "lol":
                sig_path = E / "data/lol/processed/datasets/validation.parquet"
                buys, sig = add_game_second(buys, sig_path)
                buys["sec_bucket"] = pd.cut(buys.game_second, [0, 120, 240, 360, 480, 720, 1e9])
                # league
                mk = pd.read_parquet(E / "data/lol/processed/universe/markets.parquet",
                                     columns=["condition_id", "league", "event_slug", "team_a", "team_b"])
                audit = pd.read_parquet(E / "data/lol/processed/datasets/backtest_audit.parquet",
                                      columns=["match_id", "condition_id"])
                buys = buys.merge(audit, on="match_id", how="left").merge(
                    mk[["condition_id", "league", "event_slug"]], on="condition_id", how="left")
                buys = add_time_since_event(buys, sig, game)
                buys["tse_bucket"] = pd.cut(buys.t_since_event, [-1, 0, 2, 5, 10, 20, 40, 1e9])
                buys["age_bucket"] = pd.cut(buys.signal_age_seconds, [0, 2, 4, 8, 12, 16, 1e9])
                buys["price_bucket"] = pd.cut(buys.price, [0, .4, .5, .6, .7, .8, 1.0])
                buys["queue_bucket"] = pd.cut(buys.queue_ahead, [-1, 0, 100, 500, 2000, 1e9])
                buys["anchor_drift_c"] = 100 * (buys.book_p_radiant - buys.dataset_market_p)
                buys["drift_bucket"] = pd.cut(buys.anchor_drift_c, [-1e9, -2, -0.5, 0.5, 2, 1e9])
                for key in ["signal_mode", "sec_bucket", "age_bucket", "tse_bucket",
                            "price_bucket", "queue_bucket", "drift_bucket", "league", "is_maker"]:
                    bucket_report(buys, f"lol BUY", key)
                # per-map pnl by signal mode
                results2 = results.copy()
                print("\n--- lol per-map engine_pnl by signal_mode ---")
                print(results2.groupby("signal_mode")["engine_pnl"].describe().to_string())
                dump = buys.copy()
                for c in dump.columns:
                    if str(dump[c].dtype).startswith("interval") or isinstance(
                        dump[c].dtype, pd.CategoricalDtype):
                        dump[c] = dump[c].astype(str)
                dump.to_parquet(R / "work/bt-devin/lol_buys_enriched.parquet")
            else:
                sig_path = E / "data/new_processed/dataset/validation_dataset.parquet"
                buys, sig = add_game_second(buys, sig_path)
                buys["sec_bucket"] = pd.cut(buys.game_second, [0, 120, 240, 360, 480, 720, 1e9])
                buys = add_time_since_event(buys, sig, game)
                buys["tse_bucket"] = pd.cut(buys.t_since_event, [-1, 0, 2, 5, 10, 20, 40, 1e9])
                buys["age_bucket"] = pd.cut(buys.signal_age_seconds, [0, 2, 4, 8, 12, 16, 1e9])
                buys["price_bucket"] = pd.cut(buys.price, [0, .4, .5, .6, .7, .8, 1.0])
                buys["queue_bucket"] = pd.cut(buys.queue_ahead, [-1, 0, 100, 500, 2000, 1e9])
                for key in ["signal_mode", "sec_bucket", "age_bucket", "tse_bucket",
                            "price_bucket", "queue_bucket", "is_maker"]:
                    bucket_report(buys, "dota BUY", key)
                dump = buys.copy()
                for c in dump.columns:
                    if str(dump[c].dtype).startswith("interval") or isinstance(
                        dump[c].dtype, pd.CategoricalDtype):
                        dump[c] = dump[c].astype(str)
                dump.to_parquet(R / "work/bt-devin/dota_buys_enriched.parquet")
    sys.stdout = sys.__stdout__
    print("wrote", out)


main()
