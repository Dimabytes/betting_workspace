from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
W = R / "work/parity-luna"


def parse_levels(value):
    if value is None:
        return []
    try:
        if isinstance(value, str):
            value = json.loads(value)
        return [(float(x["price"]), float(x["size"])) for x in value if x is not None]
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return []


def side_level_size(levels, side, px):
    parsed = parse_levels(levels)
    if not parsed:
        return None
    target = round(float(px), 2)
    for p, size in parsed:
        if round(p, 2) == target:
            return size
    vals = [p for p, _ in parsed]
    if min(vals) - 1e-9 <= target <= max(vals) + 1e-9:
        return 0.0
    return None


def book_summary(row):
    bids, asks = parse_levels(row.bids), parse_levels(row.asks)
    bid = max((x[0] for x in bids), default=None)
    ask = min((x[0] for x in asks), default=None)
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    return bid, ask, mid


def merge_windows(times_us, pad_us: int):
    windows = sorted((int(t) - pad_us, int(t) + pad_us) for t in times_us)
    merged = []
    for lo, hi in windows:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def load_day_files(root: Path, token: str, day: date, typ: str, columns: list[str], windows) -> pd.DataFrame:
    folder = root / typ / f"asset_id={token}"
    pieces = []
    filters = [[("timestamp_us", ">=", lo), ("timestamp_us", "<=", hi)] for lo, hi in windows]
    if not filters:
        return pd.DataFrame(columns=columns)
    for d in (day - timedelta(days=1), day, day + timedelta(days=1)):
        path = folder / f"{d.isoformat()}.parquet"
        if path.exists():
            pieces.append(pd.read_parquet(path, columns=columns, filters=filters))
    if not pieces:
        return pd.DataFrame(columns=columns)
    out = pd.concat(pieces, ignore_index=True)
    tcol = "timestamp_us"
    return out.sort_values(tcol).reset_index(drop=True)


def main():
    live_orders = pd.read_csv(W / "live_order_lifecycle.csv")
    trace_maps = set(live_orders.match_id.dropna().astype(int))
    matches = pd.read_csv(W / "same_map_live_backtest.csv")
    matches = matches[matches.match_id_bt.astype(int).isin(trace_maps)].copy()
    meta_by_id = {}
    raw_by_asset = {}
    root = E / "data/raw/telonex/polymarket"
    fills = pd.read_csv(W / "order_fill_marks.csv")
    fills = fills[fills.match_id.astype(int).isin(trace_maps)].copy()
    fills = fills[(fills.source == "backtest") | ((fills.source == "live") & fills.fill_order_joined.fillna(False))].copy()
    for _, row in matches.iterrows():
        mid = int(row.match_id_bt)
        archive = E / "data/trader" / str(row.dir)
        meta = json.loads((archive / "match.json").read_text())
        market = meta.get("market") or {}
        tokens = [str(market.get("yes_token_id") or ""), str(market.get("no_token_id") or "")]
        day = date.fromisoformat(str(row.date))
        meta_by_id[mid] = {"tokens": tokens, "day": day, "date": str(row.date)}
        for token_index, token in enumerate(tokens):
            if token and (mid, token) not in raw_by_asset:
                token_fills = fills[(fills.match_id.astype(int) == mid) & (fills.token_index.astype(int) == token_index)]
                times_us = (token_fills.ts_ns.astype("int64") // 1000).tolist()
                raw_by_asset[(mid, token)] = {
                    "trades": load_day_files(root, token, day, "trades", ["timestamp_us", "asset_id", "price", "side", "size", "trade_id", "origin_asset_id"], merge_windows(times_us, 15_000_000)),
                    "books": load_day_files(root, token, day, "book_snapshot_full", ["timestamp_us", "bids", "asks"], merge_windows(times_us, 2_000_000)),
                }
        print("loaded witness source", mid, flush=True)

    rows = []
    for _, fill in fills.iterrows():
        mid = int(fill.match_id)
        token_index = int(fill.token_index)
        token = meta_by_id[mid]["tokens"][token_index]
        asset = raw_by_asset[(mid, token)]
        trades, books = asset["trades"], asset["books"]
        fill_ns = int(fill.ts_ns)
        fill_us = fill_ns // 1000
        side = str(fill.side).upper()
        expected_aggressor = "sell" if side == "BUY" else "buy"
        px = float(fill.price)
        if len(trades):
            t_ns = trades.timestamp_us.to_numpy(dtype=np.int64) * 1000
            ix0 = int(np.searchsorted(t_ns, fill_ns - 15_000_000_000, side="left"))
            ix1 = int(np.searchsorted(t_ns, fill_ns + 15_000_000_000, side="right"))
            window = trades.iloc[ix0:ix1].copy()
            window["dt_s"] = (window.timestamp_us.astype("int64") * 1000 - fill_ns) / 1e9
            window["px_num"] = pd.to_numeric(window.price, errors="coerce")
            window["same_px"] = (window.px_num.round(2) - round(px, 2)).abs() < 0.001
            window["correct_side"] = window.side.astype(str).str.lower().eq(expected_aggressor)
            window["same_side_price"] = window.correct_side & window.same_px
            if side == "BUY":
                window["crossing_trade"] = window.correct_side & (window.px_num <= px + 1e-9)
            else:
                window["crossing_trade"] = window.correct_side & (window.px_num >= px - 1e-9)
            near2 = window[window.dt_s.abs() <= 2.0]
            near15 = window[window.dt_s.abs() <= 15.0]
            exact2 = near2[near2.same_side_price]
            exact15 = near15[near15.same_side_price]
            cross2 = near2[near2.crossing_trade]
            cross15 = near15[near15.crossing_trade]
            exact_trade = exact2.iloc[np.argmin(exact2.dt_s.abs().to_numpy())] if len(exact2) else None
            cross_trade = cross2.iloc[np.argmin(cross2.dt_s.abs().to_numpy())] if len(cross2) else None
            exact_trade_15 = exact15.iloc[np.argmin(exact15.dt_s.abs().to_numpy())] if len(exact15) else None
        else:
            window = pd.DataFrame()
            exact_trade = exact_trade_15 = cross_trade = None
            exact2 = exact15 = cross2 = cross15 = pd.DataFrame()

        mid = bid = ask = None
        book_age_ms = None
        book_cross = False
        pre_size = post_size = None
        level_disappeared = False
        if len(books):
            b_ns = books.timestamp_us.to_numpy(dtype=np.int64) * 1000
            pos = int(np.searchsorted(b_ns, fill_ns, side="right")) - 1
            if pos >= 0:
                book_age_ms = (fill_ns - int(b_ns[pos])) / 1e6
                bid, ask, mid = book_summary(books.iloc[pos])
                book_cross = (ask is not None and ask <= px + 1e-9) if side == "BUY" else (bid is not None and bid >= px - 1e-9)
            before_pos = int(np.searchsorted(b_ns, fill_ns - 1_000_000, side="right")) - 1
            after_pos = int(np.searchsorted(b_ns, fill_ns + 1_000_000, side="left"))
            book_side = "bids" if side == "BUY" else "asks"
            if before_pos >= 0:
                pre_size = side_level_size(books.iloc[before_pos][book_side], book_side, px)
            if after_pos < len(books):
                post_size = side_level_size(books.iloc[after_pos][book_side], book_side, px)
            level_disappeared = pre_size is not None and pre_size > 0 and post_size == 0

        price_vs_mid = (px - mid) if side == "BUY" and mid is not None else ((mid - px) if side == "SELL" and mid is not None else None)
        rows.append({
            "source": fill.source, "cohort": fill.cohort,
            "map_id": int(fill.match_id), "token_index": token_index, "side": side,
            "order_id": fill.order_id, "price": px, "qty": float(fill.qty), "fill_ns": fill_ns,
            "is_maker": fill.is_maker, "queue_ahead": fill.queue_ahead,
            "trade_at_own_price_opposite_side_within_2s": bool(len(exact2)),
            "trade_at_own_price_opposite_side_within_15s": bool(len(exact15)),
            "same_side_crossing_trade_within_2s": bool(len(cross2)),
            "same_side_crossing_trade_within_15s": bool(len(cross15)),
            "nearest_exact_trade_dt_s": float(exact_trade.dt_s) if exact_trade is not None else None,
            "nearest_exact_trade_id": str(exact_trade.trade_id) if exact_trade is not None else None,
            "nearest_exact_trade_price": float(exact_trade.px_num) if exact_trade is not None else None,
            "nearest_exact_trade_size": float(pd.to_numeric(exact_trade.size, errors="coerce")) if exact_trade is not None else None,
            "nearest_exact_trade_15s_dt_s": float(exact_trade_15.dt_s) if exact_trade_15 is not None else None,
            "nearest_cross_trade_dt_s": float(cross_trade.dt_s) if cross_trade is not None else None,
            "book_snapshot_age_ms": book_age_ms, "best_bid": bid, "best_ask": ask, "mid_at_fill": mid,
            "maker_price_advantage_vs_mid": price_vs_mid,
            "opposite_book_crosses_limit_at_fill": bool(book_cross),
            "same_side_level_size_before": pre_size, "same_side_level_size_after": post_size,
            "same_side_level_disappeared_near_fill": bool(level_disappeared),
        })

    out = pd.DataFrame(rows)
    out.to_csv(W / "fill_tape_witness.csv", index=False)
    print("fill rows", len(out), "live linked", int(((out.source == "live")).sum()), "BT traced", int(((out.source == "backtest")).sum()))
    for (src, side), g in out.groupby(["source", "side"]):
        def rate(c):
            return round(float(g[c].mean()), 4) if len(g) else None
        print(src, side, "n", len(g), "exact2", int(g.trade_at_own_price_opposite_side_within_2s.sum()), "exact15", int(g.trade_at_own_price_opposite_side_within_15s.sum()), "cross2", int(g.same_side_crossing_trade_within_2s.sum()), "cross15", int(g.same_side_crossing_trade_within_15s.sum()), "book_cross", int(g.opposite_book_crosses_limit_at_fill.sum()), "level_disappeared", int(g.same_side_level_disappeared_near_fill.sum()), "mid_n", int(g.maker_price_advantage_vs_mid.notna().sum()), "mean_price_advantage", g.maker_price_advantage_vs_mid.mean())


if __name__ == "__main__":
    main()
