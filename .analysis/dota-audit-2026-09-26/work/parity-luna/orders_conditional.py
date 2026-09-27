from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
W = R / "work/parity-luna"


def truth(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def match_orders(live: pd.DataFrame, bt: pd.DataFrame, time_tolerance: float) -> pd.DataFrame:
    pairs = []
    for (mid, token, rung, bucket), lg in live.groupby(["match_id", "token_index", "submit_level_index", "queue_bucket"], dropna=False):
        bg = bt[
            (bt.match_id == mid)
            & (bt.token_index == token)
            & (bt.submit_level_index == rung)
            & (bt.queue_bucket == bucket)
        ]
        if lg.empty or bg.empty or bucket == "unknown":
            continue
        cand = []
        for li, l in lg.iterrows():
            if pd.isna(l.game_second):
                continue
            for bi, b in bg.iterrows():
                if pd.isna(b.game_second):
                    continue
                ds = abs(float(l.game_second) - float(b.game_second))
                dp = abs(float(l.price) - float(b.price))
                dq = abs(float(l.queue_ahead) - float(b.queue_ahead))
                if ds <= time_tolerance and dp <= 0.01001:
                    cand.append((ds, dp, dq, str(l.order_id), str(b.order_id), li, bi))
        cand.sort()
        used_l, used_b = set(), set()
        for ds, dp, dq, _lid, _bid, li, bi in cand:
            if li in used_l or bi in used_b:
                continue
            used_l.add(li)
            used_b.add(bi)
            l, b = lg.loc[li], bg.loc[bi]
            pairs.append({
                "match_id": int(mid), "event_slug": str(l.event_slug), "token_index": int(token),
                "rung": int(rung), "queue_bucket": str(bucket),
                "live_order_id": str(l.order_id), "bt_order_id": str(b.order_id),
                "live_price": float(l.price), "bt_price": float(b.price),
                "live_second": float(l.game_second), "bt_second": float(b.game_second),
                "time_diff_s": float(ds), "price_diff": float(dp), "queue_diff": float(dq),
                "live_queue": float(l.queue_ahead), "bt_queue": float(b.queue_ahead),
                "live_filled": truth(l.has_fill), "bt_filled": truth(b.has_fill),
                "live_time_to_first_fill_s": float(l.time_to_first_fill_s) if pd.notna(l.time_to_first_fill_s) else np.nan,
                "bt_time_to_first_fill_s": float(b.time_to_first_fill_s) if pd.notna(b.time_to_first_fill_s) else np.nan,
                "live_exposure_min": float(l.exposure_min) if pd.notna(l.exposure_min) else 0.0,
                "bt_exposure_min": float(b.exposure_min) if pd.notna(b.exposure_min) else 0.0,
            })
    return pd.DataFrame(pairs)


def ratio_stats(pairs: pd.DataFrame, iterations: int = 10000) -> dict[str, object]:
    if pairs.empty:
        return {"pairs": 0}
    lf = pairs.live_filled.astype(bool).to_numpy()
    bf = pairs.bt_filled.astype(bool).to_numpy()
    le = pairs.live_exposure_min.to_numpy(dtype=float)
    be = pairs.bt_exposure_min.to_numpy(dtype=float)
    live_rate = lf.sum() / le.sum() if le.sum() else float("nan")
    bt_rate = bf.sum() / be.sum() if be.sum() else float("nan")
    hr = bt_rate / live_rate if live_rate and math.isfinite(live_rate) else float("nan")
    live_p = float(lf.mean())
    bt_p = float(bf.mean())
    rr = bt_p / live_p if live_p else float("nan")
    clusters = sorted(pairs.event_slug.unique())
    by_cluster = {c: pairs[pairs.event_slug == c] for c in clusters}
    rng = np.random.default_rng(260927)
    hr_draws, rr_draws = [], []
    for _ in range(iterations):
        sampled = rng.choice(clusters, size=len(clusters), replace=True)
        selected = pd.concat([by_cluster[c] for c in sampled], ignore_index=True)
        sl = selected.live_filled.astype(bool).to_numpy()
        sb = selected.bt_filled.astype(bool).to_numpy()
        se_l = selected.live_exposure_min.to_numpy(dtype=float).sum()
        se_b = selected.bt_exposure_min.to_numpy(dtype=float).sum()
        sr_l = sl.sum() / se_l if se_l else 0.0
        sr_b = sb.sum() / se_b if se_b else 0.0
        sp_l = float(sl.mean()) if len(sl) else 0.0
        sp_b = float(sb.mean()) if len(sb) else 0.0
        if sr_l > 0:
            hr_draws.append(sr_b / sr_l)
        if sp_l > 0:
            rr_draws.append(sp_b / sp_l)
    return {
        "pairs": len(pairs), "maps": int(pairs.match_id.nunique()), "event_series": len(clusters),
        "live_filled_orders": int(lf.sum()), "bt_filled_orders": int(bf.sum()),
        "live_fill_probability": live_p, "bt_fill_probability": bt_p,
        "fill_probability_ratio_bt_live": rr,
        "live_exposure_min": float(le.sum()), "bt_exposure_min": float(be.sum()),
        "live_first_fill_hazard_per_min": float(live_rate), "bt_first_fill_hazard_per_min": float(bt_rate),
        "hazard_ratio_bt_live": hr,
        "hazard_ratio_cluster_ci95": [float(np.quantile(hr_draws, .025)), float(np.quantile(hr_draws, .975))] if hr_draws else None,
        "fill_probability_ratio_cluster_ci95": [float(np.quantile(rr_draws, .025)), float(np.quantile(rr_draws, .975))] if rr_draws else None,
        "live_median_time_to_fill_filled_s": float(pairs.loc[pairs.live_filled, "live_time_to_first_fill_s"].median()) if pairs.live_filled.any() else None,
        "bt_median_time_to_fill_filled_s": float(pairs.loc[pairs.bt_filled, "bt_time_to_first_fill_s"].median()) if pairs.bt_filled.any() else None,
    }


def main() -> None:
    live = pd.read_csv(W / "live_order_lifecycle.csv")
    bt = pd.read_csv(W / "backtest_order_lifecycle.csv")
    live = live[(live.cohort == "GRID-49") & (live.side == "BUY") & live.accepted_wall_ns.notna() & live.queue_ahead.notna() & live.game_second.notna() & live.submit_level_index.isin([0, 1, 2])].copy()
    trace_map_ids = set(live.match_id.astype(int))
    bt = bt[(bt.cohort == "GRID-49") & (bt.side == "BUY") & bt.match_id.astype(int).isin(trace_map_ids) & bt.accepted_wall_ns.notna() & bt.queue_ahead.notna() & bt.game_second.notna() & bt.submit_level_index.isin([0, 1, 2])].copy()
    output = {}
    all_pairs = []
    for tolerance in (15.0, 30.0, 60.0):
        pairs = match_orders(live, bt, tolerance)
        pairs.to_csv(W / f"conditional_pairs_{int(tolerance)}s.csv", index=False)
        stats = ratio_stats(pairs)
        output[str(int(tolerance))] = stats
        print("window", tolerance, json.dumps(stats, sort_keys=True))
        if tolerance == 30.0:
            all_pairs = pairs.to_dict("records")
            rows = []
            for rung, group in pairs.groupby("rung"):
                rows.append({"rung": int(rung), **ratio_stats(group)})
            pd.DataFrame(rows).to_csv(W / "conditional_hazard_by_rung.csv", index=False)
    (W / "conditional_hazard_summary.json").write_text(json.dumps(output, indent=2))
    print("30s pairs by queue bucket", pd.DataFrame(all_pairs).groupby("queue_bucket").size().to_dict() if all_pairs else {}, flush=True)


if __name__ == "__main__":
    main()
