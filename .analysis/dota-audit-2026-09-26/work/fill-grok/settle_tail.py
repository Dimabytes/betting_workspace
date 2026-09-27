"""Reproduce LIVE settlement tail and accounting identities. Read-only."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from market_data.build_market_data import market_seconds_cache_path
from shared.constants.paths import MATCH_CATALOG_PATH
from shared.utils.match_time import datetime_to_ns, parse_utc
from shared.utils.trading import maker_rebate_usdc

R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
OUT = R / "work" / "fill-grok"
LIVE = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/backtests/dota_maker/LIVE")
QTY_EPS = 1e-6
MONEY_EPS = 0.02


def settlement_value(token_index: int, radiant_token_index: int, radiant_win: bool) -> float:
    if token_index == radiant_token_index:
        return 1.0 if radiant_win else 0.0
    return 0.0 if radiant_win else 1.0


def token_px(market_p: float, token_index: int, radiant_token_index: int) -> float:
    if token_index == radiant_token_index:
        return market_p
    return 1.0 - market_p


def load_catalog() -> pd.DataFrame:
    frame = pd.read_parquet(
        MATCH_CATALOG_PATH,
        columns=["match_id", "radiant_win", "radiant_token_index", "market_slug", "duration", "ended_at", "horn_at"],
    )
    frame["match_id"] = frame["match_id"].astype(int)
    return frame.set_index("match_id")


def walk_match(fills: pd.DataFrame) -> dict:
    """Per-token lots. A row is one fill, already time-sorted."""
    qty = {0: 0.0, 1: 0.0}
    cost = {0: 0.0, 1: 0.0}
    opened_ns = {0: None, 1: None}
    opened_rows = {0: None, 1: None}
    both = 0
    closed: list[dict] = []
    ts = fills["ts_ns"].tolist()
    mono = all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1))
    last_pos_after = {0: None, 1: None}
    for row in fills.itertuples(index=False):
        token = int(row.token_index)
        other = 1 - token
        if row.side == "BUY":
            if qty[token] <= QTY_EPS:
                opened_ns[token] = int(row.ts_ns)
                opened_rows[token] = row
            qty[token] += float(row.quantity)
            cost[token] += float(row.price) * float(row.quantity)
        else:
            sell_qty = float(row.quantity)
            avg = cost[token] / qty[token] if qty[token] > QTY_EPS else float(row.price)
            qty[token] -= sell_qty
            cost[token] -= avg * sell_qty
            if qty[token] <= QTY_EPS:
                closed.append(
                    {
                        "token": token,
                        "exit_px": float(row.price),
                        "exit_ns": int(row.ts_ns),
                        "fair": float(row.fair_at_fill),
                        "book_p": float(row.book_p_radiant),
                        "gate": str(row.gate_reason_at_fill),
                        "open_ns": opened_ns[token],
                        "qty_sold_end": sell_qty,
                    }
                )
                qty[token] = 0.0
                cost[token] = 0.0
                opened_ns[token] = None
                opened_rows[token] = None
        last_pos_after[token] = float(row.position_after)
        if qty[0] > QTY_EPS and qty[1] > QTY_EPS:
            both += 1
        # position_after must be this token's qty, not the other leg
        if abs(float(row.position_after) - qty[token]) > 1e-4:
            last_pos_after[token] = float("nan")  # marker; counted below
    open_lots = []
    for token in (0, 1):
        if qty[token] > QTY_EPS:
            open_lots.append(
                {
                    "token": token,
                    "qty": qty[token],
                    "cost": cost[token],
                    "open_ns": opened_ns[token],
                }
            )
    reported_mismatch = 0
    # recompute mismatch count properly
    qty2 = {0: 0.0, 1: 0.0}
    pos_mismatch = 0
    for row in fills.itertuples(index=False):
        token = int(row.token_index)
        if row.side == "BUY":
            qty2[token] += float(row.quantity)
        else:
            qty2[token] -= float(row.quantity)
            if qty2[token] < QTY_EPS:
                qty2[token] = 0.0
        if abs(float(row.position_after) - qty2[token]) > 1e-3:
            pos_mismatch += 1
    return {
        "mono": mono,
        "both": both,
        "closed": closed,
        "open": open_lots,
        "pos_mismatch": pos_mismatch,
        "end_qty": qty2,
    }


def last_ok_mid(match_id: int) -> dict | None:
    path = market_seconds_cache_path(match_id)
    if not path.is_file():
        return None
    frame = pd.read_parquet(path, columns=["second", "state_ts_us", "market_p_radiant", "market_status"])
    if frame.empty:
        return None
    ok = frame[frame["market_status"] == "ok"]
    last = frame.iloc[-1]
    last_ok = ok.iloc[-1] if not ok.empty else None
    return {
        "n": int(len(frame)),
        "last_second": int(last["second"]),
        "last_status": str(last["market_status"]),
        "last_p": float(last["market_p_radiant"]) if pd.notna(last["market_p_radiant"]) else None,
        "last_ts_us": int(last["state_ts_us"]),
        "ok_second": None if last_ok is None else int(last_ok["second"]),
        "ok_p": None if last_ok is None else float(last_ok["market_p_radiant"]),
        "ok_ts_us": None if last_ok is None else int(last_ok["state_ts_us"]),
        "first_second": int(frame.iloc[0]["second"]),
        "max_ts_ns": int(ok["state_ts_us"].max()) * 1000 if not ok.empty else None,
    }


def game_second_at(match_id: int, ts_ns: int) -> int | None:
    path = market_seconds_cache_path(match_id)
    if not path.is_file():
        return None
    frame = pd.read_parquet(path, columns=["second", "state_ts_us"])
    us = ts_ns // 1000
    prior = frame[frame["state_ts_us"] <= us]
    if prior.empty:
        return None
    return int(prior.iloc[-1]["second"])


def analyze_seed(seed: int, catalog: pd.DataFrame) -> dict:
    seed_dir = LIVE / f"seed{seed}"
    summary = json.loads((seed_dir / "summary.json").read_text())
    arm = summary["arms"][0]
    results = pd.read_parquet(seed_dir / "results.parquet")
    fills = pd.read_parquet(seed_dir / "fills.parquet")
    quotes = pd.read_parquet(
        seed_dir / "quote_events.parquet",
        columns=["match_id", "ts_ns", "kind", "token_index", "side", "price", "reason", "book_p_radiant", "fair", "spread"],
    )

    clean = results[~results["terminated_early"]]
    sum_checks = {
        "engine_pnl": (float(clean["engine_pnl"].sum()), float(arm["total_engine_pnl"])),
        "cash_flow": (float(clean["cash_flow"].sum()), float(arm["cash_flow"])),
        "settlement_remainder": (
            float(clean["engine_pnl"].sum() - clean["cash_flow"].sum()),
            float(arm["settlement_remainder"]),
        ),
        "buy_fills": (int(clean["buy_fills"].sum()), int(arm["buy_fills"])),
        "sell_fills": (int(clean["sell_fills"].sum()), int(arm["sell_fills"])),
        "buy_qty": (float(clean["buy_quantity"].sum()), float(arm["bought_shares"])),
        "sell_qty": (float(clean["sell_quantity"].sum()), None),
        "terminal_inv": (float(clean["terminal_position"].sum()), float(arm["terminal_inventory"])),
        "dust": (int(clean["dust_position"].sum()), int(arm["dust_positions"])),
        "completed": (int(len(clean)), int(arm["completed"])),
        "terminated": (int(results["terminated_early"].sum()), int(arm["terminated"])),
        "matches": (int(len(results)), int(arm["matches"])),
        "maker_rebate": (float(fills["maker_rebate"].sum()), float(arm["maker_rebate"])),
        "taker_fee": (float(fills["taker_fee"].sum()), float(arm["taker_fee"])),
        "buy_turnover": (
            float((fills.loc[fills["side"] == "BUY", "price"] * fills.loc[fills["side"] == "BUY", "quantity"]).sum()),
            float(arm["buy_turnover"]),
        ),
        "sell_turnover": (
            float((fills.loc[fills["side"] == "SELL", "price"] * fills.loc[fills["side"] == "SELL", "quantity"]).sum()),
            float(arm["sell_turnover"]),
        ),
    }
    # fills of terminated matches should be excluded from rebate if summarize drops them
    term_ids = set(results.loc[results["terminated_early"], "match_id"].astype(int))
    live_fills = fills[~fills["match_id"].isin(term_ids)]
    sum_checks["maker_rebate_clean"] = (float(live_fills["maker_rebate"].sum()), float(arm["maker_rebate"]))
    sum_checks["taker_fee_clean"] = (float(live_fills["taker_fee"].sum()), float(arm["taker_fee"]))

    rebate_recompute = 0.0
    rebate_mismatch = 0
    for row in live_fills.itertuples(index=False):
        expect = maker_rebate_usdc(price=float(row.price), size=float(row.quantity)) if row.is_maker else 0.0
        rebate_recompute += expect
        if abs(expect - float(row.maker_rebate)) > 1e-6:
            rebate_mismatch += 1

    markout_checks = {}
    for side, horizon, key in (
        ("BUY", 30, "buy_30s"),
        ("BUY", 300, "buy_300s"),
        ("SELL", 30, "sell_30s"),
        ("SELL", 300, "sell_300s"),
    ):
        part = live_fills[live_fills["side"] == side]
        col = f"markout_{horizon}s"
        weighted = float((part[col] * part["quantity"]).sum() / part["quantity"].sum()) if len(part) else None
        stored = arm["markout"][key]["estimate"] if arm["markout"][key] else None
        markout_checks[key] = (weighted, stored, int(len(part)))

    source_counts = {}
    for horizon in (30, 300):
        col = f"reference_source_{horizon}s"
        counts = live_fills[col].value_counts().to_dict()
        source_counts[horizon] = {str(k): int(v) for k, v in counts.items()}

    # settlement-valued markouts: reference equals 0 or 1
    ref_extreme = {}
    for horizon in (30, 300):
        col = f"reference_{horizon}s"
        src = f"reference_source_{horizon}s"
        extreme = live_fills[live_fills[col].isin([0.0, 1.0])]
        ref_extreme[horizon] = {
            "n": int(len(extreme)),
            "by_source": {str(k): int(v) for k, v in extreme[src].value_counts().to_dict().items()},
            "qty": float(extreme["quantity"].sum()) if len(extreme) else 0.0,
        }

    identity_bad = []
    held_rows = []
    closed_rows = []
    both_matches = []
    pos_mismatch_matches = []
    nonmono = []
    post_game_fills = 0
    post_game_sell_qty = 0.0
    post_game_buy_qty = 0.0
    settlement_applied_false = []
    terminal_vs_walk = []

    held_ids: list[int] = []

    for match_id, match_fills in live_fills.groupby("match_id", sort=False):
        match_id = int(match_id)
        ordered = match_fills.sort_values(["ts_ns", "token_index"])
        walked = walk_match(ordered)
        if not walked["mono"]:
            nonmono.append(match_id)
        if walked["both"]:
            both_matches.append((match_id, walked["both"]))
        if walked["pos_mismatch"]:
            pos_mismatch_matches.append((match_id, walked["pos_mismatch"]))
        cat = catalog.loc[match_id]
        rti = int(cat["radiant_token_index"])
        win = bool(cat["radiant_win"])
        result = clean.loc[clean["match_id"] == match_id].iloc[0]
        game_end_ns = datetime_to_ns(parse_utc(str(result["game_ended_at"])))
        expected = 0.0
        for token, qty in walked["end_qty"].items():
            expected += qty * settlement_value(token, rti, win)
        remainder = float(result["engine_pnl"]) - float(result["cash_flow"])
        if abs(remainder - expected) > MONEY_EPS:
            identity_bad.append(
                {
                    "match_id": match_id,
                    "remainder": remainder,
                    "expected": expected,
                    "end_qty": dict(walked["end_qty"]),
                    "terminal_position": float(result["terminal_position"]),
                    "terminal_token": int(result["terminal_token_index"]),
                    "settlement_applied": bool(result["settlement_applied"]),
                }
            )
        if not bool(result["settlement_applied"]) and (result["buy_fills"] + result["sell_fills"]) > 0:
            settlement_applied_false.append(match_id)
        reported_qty = float(result["terminal_position"])
        reported_token = int(result["terminal_token_index"])
        walk_open_qty = sum(walked["end_qty"].values())
        if abs(reported_qty - walk_open_qty) > 1e-3:
            terminal_vs_walk.append(
                {
                    "match_id": match_id,
                    "reported_qty": reported_qty,
                    "reported_token": reported_token,
                    "walk": dict(walked["end_qty"]),
                }
            )
        late = ordered[ordered["ts_ns"] > game_end_ns]
        if len(late):
            post_game_fills += int(len(late))
            post_game_sell_qty += float(late.loc[late["side"] == "SELL", "quantity"].sum())
            post_game_buy_qty += float(late.loc[late["side"] == "BUY", "quantity"].sum())
        for lot in walked["open"]:
            token = lot["token"]
            held_rows.append(
                {
                    "seed": seed,
                    "match_id": match_id,
                    "slug": str(result["slug"]),
                    "token": token,
                    "side": "radiant" if token == rti else "dire",
                    "qty": lot["qty"],
                    "avg_px": lot["cost"] / lot["qty"],
                    "cost": lot["cost"],
                    "open_ns": lot["open_ns"],
                    "radiant_win": win,
                    "radiant_token_index": rti,
                    "won": settlement_value(token, rti, win) == 1.0,
                    "payout": lot["qty"] * settlement_value(token, rti, win),
                    "game_ended_at": str(result["game_ended_at"]),
                    "horn_at": str(result["horn_at"]),
                    "settlement_applied": bool(result["settlement_applied"]),
                    "reported_terminal_qty": reported_qty,
                    "reported_terminal_token": reported_token,
                    "reported_side": str(result["terminal_side"]),
                    "engine_pnl": float(result["engine_pnl"]),
                    "cash_flow": float(result["cash_flow"]),
                    "duration": int(cat["duration"]),
                }
            )
            held_ids.append(match_id)
        for episode in walked["closed"]:
            token = episode["token"]
            book_p = episode["book_p"]
            mid = token_px(book_p, token, rti) if book_p == book_p else float("nan")
            closed_rows.append(
                {
                    "seed": seed,
                    "match_id": match_id,
                    "token": token,
                    "won": settlement_value(token, rti, win) == 1.0,
                    "exit_px": episode["exit_px"],
                    "fair": episode["fair"],
                    "mid": mid,
                    "exit_ns": episode["exit_ns"],
                    "game_end_ns": game_end_ns,
                    "secs_before_end": (game_end_ns - episode["exit_ns"]) / 1e9,
                    "gate": episode["gate"],
                }
            )

    held = pd.DataFrame(held_rows)
    closed = pd.DataFrame(closed_rows)

    # quote: last SELL submit and last book event per held match
    if held_ids:
        q = quotes[quotes["match_id"].isin(held_ids)]
        sells = q[(q["side"] == "SELL") & (q["kind"] == "submitted")]
        last_sell = (
            sells.sort_values("ts_ns").groupby(["match_id", "token_index"], as_index=False).tail(1)
            if len(sells)
            else sells
        )
        books = q[q["book_p_radiant"] > 0]
        last_book = books.sort_values("ts_ns").groupby("match_id", as_index=False).tail(1) if len(books) else books
    else:
        last_sell = pd.DataFrame()
        last_book = pd.DataFrame()

    # enrich held with cache + quotes
    enriched = []
    for row in held_rows:
        mid = last_ok_mid(row["match_id"])
        entry_second = game_second_at(row["match_id"], int(row["open_ns"])) if row["open_ns"] else None
        sell = None
        if len(last_sell):
            hit = last_sell[
                (last_sell["match_id"] == row["match_id"]) & (last_sell["token_index"] == row["token"])
            ]
            if len(hit):
                sell = hit.iloc[-1]
        book = None
        if len(last_book):
            hitb = last_book[last_book["match_id"] == row["match_id"]]
            if len(hitb):
                book = hitb.iloc[-1]
        token_end = None
        if mid and mid["ok_p"] is not None:
            token_end = token_px(mid["ok_p"], row["token"], row["radiant_token_index"])
        horn_ns = datetime_to_ns(parse_utc(row["horn_at"]))
        entry_wall = None if row["open_ns"] is None else (int(row["open_ns"]) - horn_ns) / 1e9
        enriched.append(
            {
                **{k: row[k] for k in row if k not in ("open_ns",)},
                "entry_second": entry_second,
                "entry_wall_s": entry_wall,
                "last_sell_px": None if sell is None else float(sell["price"]),
                "last_sell_ts_ns": None if sell is None else int(sell["ts_ns"]),
                "last_sell_wall_before_end_s": None
                if sell is None
                else (datetime_to_ns(parse_utc(row["game_ended_at"])) - int(sell["ts_ns"])) / 1e9,
                "last_quote_book_p": None if book is None else float(book["book_p_radiant"]),
                "last_quote_spread": None if book is None else float(book["spread"]),
                "last_quote_kind": None if book is None else str(book["kind"]),
                "end_second": None if mid is None else mid["ok_second"],
                "end_status_second": None if mid is None else mid["last_second"],
                "end_token_mid": token_end,
                "cache_last_status": None if mid is None else mid["last_status"],
                "duration_gap_s": None
                if mid is None or mid["ok_second"] is None
                else row["duration"] - mid["ok_second"],
            }
        )

    # markout horizon past last cached mid, traded matches only (sample via cache max ts)
    # done once per match from a light column read
    past = {30: {"n": 0, "qty": 0.0, "weighted_num": 0.0}, 300: {"n": 0, "qty": 0.0, "weighted_num": 0.0}}
    inside = {30: {"n": 0, "qty": 0.0, "weighted_num": 0.0}, 300: {"n": 0, "qty": 0.0, "weighted_num": 0.0}}
    missing_cache = 0
    for match_id, match_fills in live_fills.groupby("match_id", sort=False):
        info = last_ok_mid(int(match_id))
        if info is None or info["max_ts_ns"] is None:
            missing_cache += 1
            continue
        max_ns = info["max_ts_ns"]
        for horizon in (30, 300):
            col = f"markout_{horizon}s"
            late_mask = match_fills["ts_ns"] + horizon * 1_000_000_000 > max_ns
            for bucket, mask in ((past, late_mask), (inside, ~late_mask)):
                part = match_fills[late_mask] if bucket is past else match_fills[~late_mask]
                # fix: use mask properly
            part_past = match_fills[match_fills["ts_ns"] + horizon * 1_000_000_000 > max_ns]
            part_in = match_fills[match_fills["ts_ns"] + horizon * 1_000_000_000 <= max_ns]
            past[horizon]["n"] += int(len(part_past))
            past[horizon]["qty"] += float(part_past["quantity"].sum()) if len(part_past) else 0.0
            past[horizon]["weighted_num"] += float((part_past[col] * part_past["quantity"]).sum()) if len(part_past) else 0.0
            inside[horizon]["n"] += int(len(part_in))
            inside[horizon]["qty"] += float(part_in["quantity"].sum()) if len(part_in) else 0.0
            inside[horizon]["weighted_num"] += float((part_in[col] * part_in["quantity"]).sum()) if len(part_in) else 0.0

    def pack_bucket(bucket: dict) -> dict:
        out = {}
        for horizon, stats in bucket.items():
            qty = stats["qty"]
            out[horizon] = {
                "n": stats["n"],
                "qty": qty,
                "mean": None if qty <= 0 else stats["weighted_num"] / qty,
            }
        return out

    # closed / held outcome split
    def outcome_split(frame: pd.DataFrame, qty_col: str | None) -> dict:
        if frame.empty:
            return {"n": 0}
        won = frame[frame["won"]]
        lost = frame[~frame["won"]]
        return {
            "n": int(len(frame)),
            "wins": int(len(won)),
            "losses": int(len(lost)),
            "win_qty": None if qty_col is None else float(won[qty_col].sum()) if len(won) else 0.0,
            "loss_qty": None if qty_col is None else float(lost[qty_col].sum()) if len(lost) else 0.0,
        }

    # loser sells: price vs mid and fair
    loser_sells = closed[~closed["won"]] if len(closed) else closed
    winner_sells = closed[closed["won"]] if len(closed) else closed

    def sell_stats(frame: pd.DataFrame) -> dict:
        if frame.empty:
            return {"n": 0}
        above_fair = frame["exit_px"] + 1e-9 >= frame["fair"]
        # fair nan
        fair_ok = frame["fair"].notna()
        return {
            "n": int(len(frame)),
            "median_exit": float(frame["exit_px"].median()),
            "median_mid": float(frame["mid"].median()),
            "median_fair": float(frame.loc[fair_ok, "fair"].median()) if fair_ok.any() else None,
            "median_exit_minus_mid": float((frame["exit_px"] - frame["mid"]).median()),
            "median_exit_minus_fair": float((frame.loc[fair_ok, "exit_px"] - frame.loc[fair_ok, "fair"]).median())
            if fair_ok.any()
            else None,
            "frac_exit_ge_fair": float(above_fair[fair_ok].mean()) if fair_ok.any() else None,
            "median_secs_before_end": float(frame["secs_before_end"].median()),
            "sells_after_game_end": int((frame["secs_before_end"] < 0).sum()),
            "p10_secs_before_end": float(frame["secs_before_end"].quantile(0.1)),
        }

    is_maker_false = int((~live_fills["is_maker"]).sum())
    net = float(arm["net_pnl"])
    engine = float(arm["total_engine_pnl"])
    rebate = float(arm["maker_rebate"])
    taker = float(arm["taker_fee"])

    payload = {
        "seed": seed,
        "summary_identity_net": abs(net - (engine + rebate - taker)) < 1e-6,
        "net": net,
        "engine": engine,
        "cash_flow": float(arm["cash_flow"]),
        "settlement_remainder": float(arm["settlement_remainder"]),
        "sum_checks": {
            k: {"from_parquet": a, "summary": b, "ok": b is None or abs(a - b) < 1e-4}
            for k, (a, b) in sum_checks.items()
        },
        "rebate_recompute": rebate_recompute,
        "rebate_row_mismatches": rebate_mismatch,
        "markout_checks": {
            k: {"weighted": a, "summary": b, "n": n, "ok": a is not None and b is not None and abs(a - b) < 1e-9}
            for k, (a, b, n) in markout_checks.items()
        },
        "source_counts": source_counts,
        "ref_extreme": ref_extreme,
        "identity_bad_n": len(identity_bad),
        "identity_bad": identity_bad[:20],
        "settlement_applied_false_traded": settlement_applied_false,
        "terminal_vs_walk": terminal_vs_walk,
        "both_leg_matches": both_matches,
        "pos_mismatch_matches": pos_mismatch_matches[:20],
        "pos_mismatch_n": len(pos_mismatch_matches),
        "nonmono": nonmono,
        "post_game_fills": post_game_fills,
        "post_game_sell_qty": post_game_sell_qty,
        "post_game_buy_qty": post_game_buy_qty,
        "held_n": int(len(held_rows)),
        "held_wins": int(sum(1 for row in held_rows if row["won"])),
        "held_losses": int(sum(1 for row in held_rows if not row["won"])),
        "held_qty": float(sum(row["qty"] for row in held_rows)),
        "held_cost": float(sum(row["cost"] for row in held_rows)),
        "held_payout": float(sum(row["payout"] for row in held_rows)),
        "closed_split_episodes": outcome_split(closed, None),
        "loser_exit": sell_stats(loser_sells),
        "winner_exit": sell_stats(winner_sells),
        "markout_past_book": pack_bucket(past),
        "markout_inside_book": pack_bucket(inside),
        "missing_cache_matches": missing_cache,
        "is_maker_false": is_maker_false,
        "held": enriched,
    }
    return payload


def main() -> None:
    catalog = load_catalog()
    all_held = []
    summaries = []
    for seed in (0, 1, 2):
        print(f"seed {seed}", flush=True)
        payload = analyze_seed(seed, catalog)
        held = payload.pop("held")
        all_held.extend(held)
        summaries.append(payload)
        slim = {k: payload[k] for k in payload if k != "sum_checks"}
        print(json.dumps(slim, indent=2, default=str)[:4000])
        print("--- sum_checks fails ---")
        for key, row in payload["sum_checks"].items():
            if not row["ok"]:
                print(key, row)
    (OUT / "seed_summaries.json").write_text(json.dumps(summaries, indent=2, default=str))
    pd.DataFrame(all_held).to_csv(OUT / "held_positions.csv", index=False)
    print("wrote", OUT / "held_positions.csv", "rows", len(all_held))


if __name__ == "__main__":
    main()
