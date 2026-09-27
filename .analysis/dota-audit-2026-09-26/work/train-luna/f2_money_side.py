from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from market_data.build_market_data import market_seconds_cache_path
from shared.constants.paths import MATCH_CATALOG_PATH, RAW_TELONEX_POLYMARKET_DIR
from shared.utils.match_catalog import CatalogEntry, load_match_catalog
from shared.utils.telonex_book import US_PER_SECOND, find_asof_quote, load_token_book


E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
WORK = ROOT / "work" / "train-luna"
BACKTEST = E / "data/backtests/dota_maker/validation_join_delta02_x015_cut480_p4_archive-s3-20260924"
OUT = WORK / "f2_money_side.json"
OUT_ROWS = WORK / "f2_fair_bound_exposures.parquet"
OUT_CANDIDATES = WORK / "f2_fair_bound_quote_events.parquet"

QUOTE_COLS = [
    "match_id",
    "ts_ns",
    "kind",
    "token_index",
    "side",
    "price",
    "fair",
    "book_p_radiant",
    "spread",
    "episode_id",
    "order_id",
    "quantity",
]
FILL_COLS = [
    "match_id",
    "token_index",
    "side",
    "price",
    "quantity",
    "ts_ns",
    "fair",
    "book_p_radiant",
    "order_id",
    "episode_id",
    "position_after",
    "position_cost_basis",
    "maker_rebate",
    "taker_fee",
]
RESULT_COLS = [
    "match_id",
    "terminal_token_index",
    "terminal_position",
    "dust_position",
    "settlement_applied",
    "cash_flow",
    "engine_pnl",
    "signal_mode",
]


def ceil_tick(price: float) -> float:
    return math.ceil(price * 100.0 - 1e-10) / 100.0


def attach_book_at_quote(quotes: pd.DataFrame, catalog: dict[int, CatalogEntry]) -> pd.DataFrame:
    quotes = quotes.copy()
    quotes["best_bid"] = np.nan
    quotes["best_ask"] = np.nan
    quotes["raw_token_mid"] = np.nan
    quotes["raw_book_status"] = "missing"
    total_groups = quotes.groupby(["match_id", "token_index"], sort=False).ngroups
    for group_n, ((match_id, token_index), group) in enumerate(
        quotes.groupby(["match_id", "token_index"], sort=False), start=1
    ):
        entry = catalog[int(match_id)]
        token = entry.gamma.token_ids[int(token_index)]
        event_us = (group.ts_ns.to_numpy(dtype=np.int64) // 1000)
        book = load_token_book(
            token_id=token,
            start_us=int(event_us.min()) - 5 * US_PER_SECOND,
            end_us=int(event_us.max()) + 1,
            telonex_root=RAW_TELONEX_POLYMARKET_DIR,
        )
        if book is None:
            continue
        rows = []
        for idx, target_us in zip(group.index, event_us, strict=True):
            quote = find_asof_quote(book, int(target_us))
            if quote.status != "ok" or quote.quote is None:
                rows.append((idx, None, None, None, quote.status))
                continue
            rows.append(
                (
                    idx,
                    quote.quote.bid,
                    quote.quote.ask,
                    quote.quote.mid,
                    "ok",
                )
            )
        for idx, bid, ask, mid, status in rows:
            quotes.loc[idx, "best_bid"] = np.nan if bid is None else float(bid)
            quotes.loc[idx, "best_ask"] = np.nan if ask is None else float(ask)
            quotes.loc[idx, "raw_token_mid"] = np.nan if mid is None else float(mid)
            quotes.loc[idx, "raw_book_status"] = status
        if group_n % 100 == 0:
            print("RAW_QUOTE_PROGRESS", group_n, "/", total_groups, flush=True)
    return quotes


def load_seed_events(seed: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    seed_dir = BACKTEST / f"seed{seed}"
    events = pd.read_parquet(
        seed_dir / "quote_events.parquet",
        columns=QUOTE_COLS,
        filters=[("kind", "==", "submitted"), ("side", "==", "SELL")],
    )
    fair_ceils = np.ceil(events.fair.to_numpy(dtype=np.float64) * 100.0 - 1e-10) / 100.0
    candidates = events.loc[
        events.fair.notna()
        & (events.fair > 0.0)
        & (events.fair < 1.0)
        & (np.abs(events.price.to_numpy(dtype=np.float64) - fair_ceils) < 1e-8)
    ].copy()
    candidates = candidates.reset_index(drop=True)
    candidates["submitted_sell_count"] = int(len(events))
    fills = pd.read_parquet(seed_dir / "fills.parquet", columns=FILL_COLS)
    results = pd.read_parquet(seed_dir / "results.parquet", columns=RESULT_COLS)
    return candidates, fills, results


def track_position_value(
    event: pd.Series,
    fills_by_match: dict[int, pd.DataFrame],
    result_by_match: dict[int, pd.Series],
    entry: CatalogEntry,
) -> dict[str, object]:
    match_id = int(event.match_id)
    token_index = int(event.token_index)
    ts_ns = int(event.ts_ns)
    fills = fills_by_match[match_id]
    token_fills = fills.loc[
        fills.token_index.eq(token_index)
        & fills.side.isin(["BUY", "SELL"])
        & (fills.ts_ns < ts_ns)
    ].sort_values(["ts_ns", "order_id"], kind="stable")
    bought = float(token_fills.loc[token_fills.side.eq("BUY"), "quantity"].sum())
    sold = float(token_fills.loc[token_fills.side.eq("SELL"), "quantity"].sum())
    position = max(0.0, bought - sold)
    quoted_qty = float(event.quantity)
    tracked_qty = min(position, quoted_qty)

    all_later_token_sells = fills.loc[
        fills.token_index.eq(token_index)
        & fills.side.eq("SELL")
        & (fills.ts_ns > ts_ns)
    ].sort_values(["ts_ns", "order_id"], kind="stable")
    remaining = tracked_qty
    actual_value = 0.0
    assigned_sell_qty = 0.0
    assigned_sell_value = 0.0
    for fill in all_later_token_sells.itertuples(index=False):
        if remaining <= 1e-9:
            break
        qty = min(remaining, float(fill.quantity))
        actual_value += qty * float(fill.price)
        assigned_sell_qty += qty
        assigned_sell_value += qty * float(fill.price)
        remaining -= qty

    result = result_by_match[match_id]
    terminal_idx = int(result.terminal_token_index)
    terminal_qty = float(result.terminal_position)
    settlement_qty = 0.0
    settlement_payout: float | None = None
    if remaining > 1e-9 and bool(result.settlement_applied) and terminal_idx == token_index:
        settlement_qty = min(remaining, terminal_qty)
        winner_idx = entry.radiant_token_index if entry.radiant_win else 1 - entry.radiant_token_index
        settlement_payout = 1.0 if token_index == winner_idx else 0.0
        actual_value += settlement_qty * settlement_payout
        remaining -= settlement_qty

    order_fills = fills.loc[
        fills.order_id.eq(str(event.order_id))
        & fills.side.eq("SELL")
        & (fills.ts_ns > ts_ns)
    ]
    direct_order_qty = float(order_fills.quantity.sum())
    direct_order_vwap = (
        float(np.average(order_fills.price, weights=order_fills.quantity))
        if direct_order_qty > 0.0
        else None
    )
    ask = float(event.best_ask)
    counterfactual_value = tracked_qty * ask
    complete = remaining <= 1e-6
    if settlement_qty > 1e-6:
        fate = "held_to_settlement_win" if settlement_payout == 1.0 else "held_to_settlement_loss"
    elif assigned_sell_qty >= tracked_qty - 1e-6 and tracked_qty > 0.0:
        fate = "sold_later"
    elif tracked_qty <= 1e-6:
        fate = "no_open_position_at_submit"
    else:
        fate = "partly_unresolved"

    return {
        "position_before_quote": position,
        "tracked_quantity": tracked_qty,
        "direct_order_sell_fill_quantity": direct_order_qty,
        "direct_order_sell_fill_vwap": direct_order_vwap,
        "later_sell_qty_allocated_fifo": assigned_sell_qty,
        "later_sell_vwap_allocated_fifo": (
            assigned_sell_value / assigned_sell_qty if assigned_sell_qty > 0.0 else None
        ),
        "settled_qty_allocated_fifo": settlement_qty,
        "settlement_per_share": settlement_payout,
        "terminal_token_index": terminal_idx,
        "terminal_position": terminal_qty,
        "terminal_dust": bool(result.dust_position),
        "fate": fate,
        "actual_value_after_quote": actual_value,
        "counterfactual_sell_at_best_ask_value": counterfactual_value,
        "actual_minus_ask_counterfactual_dollars": actual_value - counterfactual_value,
        "actual_value_path_complete": complete,
        "engine_pnl": float(result.engine_pnl),
        "cash_flow": float(result.cash_flow),
        "map_settlement_part": float(result.engine_pnl - result.cash_flow),
    }


def main() -> None:
    event_frames: list[pd.DataFrame] = []
    fills_by_seed: dict[int, pd.DataFrame] = {}
    results_by_seed: dict[int, pd.DataFrame] = {}
    for seed in range(3):
        candidates, fills, results = load_seed_events(seed)
        event_frames.append(candidates.assign(seed=seed))
        fills_by_seed[seed] = fills
        results_by_seed[seed] = results
        print("SEED_CANDIDATES", seed, "ceil_fair_rows", len(candidates), "maps", candidates.match_id.nunique(), flush=True)

    # The raw books are common to all cadence seeds. Attach each match/token time range once.
    combined_candidates = attach_book_at_quote(pd.concat(event_frames, ignore_index=True), catalog)
    combined_candidates["fair_ceil"] = np.ceil(combined_candidates.fair.to_numpy(dtype=np.float64) * 100.0 - 1e-10) / 100.0
    combined_candidates["ask_ceil"] = combined_candidates.best_ask.map(
        lambda x: np.nan if pd.isna(x) else ceil_tick(float(x))
    )
    combined_candidates["fair_bound_above_ask"] = (
        combined_candidates.raw_book_status.eq("ok")
        & (combined_candidates.price.to_numpy(dtype=np.float64) == combined_candidates.fair_ceil.to_numpy(dtype=np.float64))
        & (combined_candidates.fair_ceil > combined_candidates.ask_ceil)
    )
    combined_candidates["held_token_mid"] = combined_candidates.raw_token_mid
    combined_candidates["held_price_bucket"] = np.where(
        combined_candidates.held_token_mid >= 0.85, ">=0.85", "<0.85"
    )
    combined_candidates["raw_spread"] = combined_candidates.best_ask - combined_candidates.best_bid
    combined_candidates["raw_spread_abs_error"] = (
        combined_candidates.raw_spread - combined_candidates.spread
    ).abs()
    combined_candidates.to_parquet(OUT_CANDIDATES, index=False)
    seed_payload = {
        seed: (
            combined_candidates.loc[combined_candidates.seed.eq(seed)].drop(columns="seed").reset_index(drop=True),
            fills_by_seed[seed],
            results_by_seed[seed],
        )
        for seed in range(3)
    }

    exposures: list[pd.DataFrame] = []
    held_summary: dict[str, object] = {}
    for seed, (candidates, fills, results) in seed_payload.items():
        filled = candidates.loc[candidates.fair_bound_above_ask].copy()
        fill_maps = {int(match_id): group for match_id, group in fills.groupby("match_id", sort=False)}
        result_rows = {int(row.match_id): row for row in results.itertuples(index=False)}
        # Keep the first fair-bound quote in each price bucket per position episode so a
        # position that later crosses 0.85 is represented in both requested price regimes.
        filled = filled.sort_values(["match_id", "token_index", "episode_id", "ts_ns"], kind="stable")
        first_exposures = filled.drop_duplicates(
            ["match_id", "token_index", "episode_id", "held_price_bucket"], keep="first"
        ).copy()
        outcome_rows: list[dict[str, object]] = []
        for event in first_exposures.itertuples(index=False):
            event_series = pd.Series(event._asdict())
            match_id = int(event.match_id)
            outcome = track_position_value(
                event_series,
                fill_maps,
                result_rows,
                catalog[match_id],
            )
            outcome_rows.append(
                {
                    "seed": seed,
                    "match_id": match_id,
                    "token_index": int(event.token_index),
                    "episode_id": int(event.episode_id),
                    "ts_ns": int(event.ts_ns),
                    "order_id": str(event.order_id),
                    "fair": float(event.fair),
                    "fair_ceil": float(event.fair_ceil),
                    "best_ask": float(event.best_ask),
                    "ask_ceil": float(event.ask_ceil),
                    "held_token_mid": float(event.held_token_mid),
                    "held_price_bucket": str(event.held_price_bucket),
                    "quote_price": float(event.price),
                    "quote_quantity": float(event.quantity),
                    "map_winner_token_index": int(
                        catalog[match_id].radiant_token_index
                        if catalog[match_id].radiant_win
                        else 1 - catalog[match_id].radiant_token_index
                    ),
                    **outcome,
                }
            )
        episode_df = pd.DataFrame(outcome_rows)
        exposures.append(episode_df)

        all_results = results.merge(
            pd.DataFrame(
                {
                    "match_id": [int(r.match_id) for r in results.itertuples(index=False)],
                    "winner_idx": [
                        catalog[int(r.match_id)].radiant_token_index
                        if catalog[int(r.match_id)].radiant_win
                        else 1 - catalog[int(r.match_id)].radiant_token_index
                        for r in results.itertuples(index=False)
                    ],
                }
            ),
            on="match_id",
            validate="one_to_one",
        )
        settled = all_results.loc[
            all_results.settlement_applied
            & (all_results.terminal_position > 0.0)
            & (all_results.terminal_token_index >= 0)
        ]
        held_summary[str(seed)] = {
            "maps": int(len(results)),
            "held_maps_with_any_terminal_inventory": int(len(settled)),
            "held_maps_non_dust": int((~settled.dust_position).sum()),
            "held_maps_winner_positions": int((settled.terminal_token_index == settled.winner_idx).sum()),
            "held_maps_loser_positions": int((settled.terminal_token_index != settled.winner_idx).sum()),
            "settlement_part_all_maps_dollars": float((results.engine_pnl - results.cash_flow).sum()),
            "engine_pnl_all_maps_dollars": float(results.engine_pnl.sum()),
            "cash_flow_all_maps_dollars": float(results.cash_flow.sum()),
            "held_maps_settlement_part_dollars": float((settled.engine_pnl - settled.cash_flow).sum()),
            "held_maps_cash_flow_dollars": float(settled.cash_flow.sum()),
            "held_maps_engine_pnl_dollars": float(settled.engine_pnl.sum()),
        }
        print("SEED_POSITION", seed, "fair_bound_events", len(filled), "unique_episodes", len(episode_df), flush=True)

    exposures_df = pd.concat(exposures, ignore_index=True) if exposures else pd.DataFrame()
    exposures_df.to_parquet(OUT_ROWS, index=False)
    summary: dict[str, object] = {
        "method": {
            "event_filter": "quote_events kind=submitted, side=SELL, price equals cent-ceil(token fair), and raw token best ask at event time has a lower cent-ceil",
            "held_token_price": "raw selected-token midpoint at submit time, from Telonex as-of quote",
            "position_unit": "first qualifying quote per (match, token, episode, held-price bucket); tracked quantity=min(open position before submit, submitted quote quantity)",
            "path_attribution": "later same-token SELL fills consume the tracked initial quantity FIFO, then terminal settlement inventory; compare gross value with tracked quantity times raw best ask",
            "counterfactual_limit": "sell-at-ask is valued as if the full tracked quantity sold there; maker queue/fill probability and fees are not modeled",
            "raw_book_rows": "raw Telonex quote at each quote event; stale/missing selected-token books excluded from exact ask condition",
        },
        "all_sell_order_candidates": {
            str(seed): {
                "submitted_sell_orders": int(seed_payload[seed][0].submitted_sell_count.iloc[0]) if len(seed_payload[seed][0]) else 0,
                "ceil_fair_candidate_rows": int(len(seed_payload[seed][0])),
                "raw_book_ok_rows": int(seed_payload[seed][0].raw_book_status.eq("ok").sum()),
                "fair_bound_above_ask_events": int(seed_payload[seed][0].fair_bound_above_ask.sum()),
                "fair_bound_unique_maps": int(seed_payload[seed][0].loc[seed_payload[seed][0].fair_bound_above_ask, "match_id"].nunique()),
            }
            for seed in range(3)
        },
        "n1_held_position_reconciliation": held_summary,
        "raw_book_alignment": {
            "fair_ceil_candidate_rows": int(len(combined_candidates)),
            "fresh_two_sided_raw_book_rows": int(combined_candidates.raw_book_status.eq("ok").sum()),
            "event_spread_exact_match_rows": int(
                (combined_candidates.raw_spread_abs_error < 1e-9).sum()
            ),
            "median_abs_spread_difference": float(combined_candidates.raw_spread_abs_error.median()),
            "p95_abs_spread_difference": float(combined_candidates.raw_spread_abs_error.quantile(0.95)),
            "max_abs_spread_difference": float(combined_candidates.raw_spread_abs_error.max()),
        },
        "first_episode_exposures": {},
        "artifact_paths": {
            "quotes": str(BACKTEST / "seed{0,1,2}" / "quote_events.parquet"),
            "fills": str(BACKTEST / "seed{0,1,2}" / "fills.parquet"),
            "results": str(BACKTEST / "seed{0,1,2}" / "results.parquet"),
            "exposure_rows": str(OUT_ROWS),
            "candidate_quote_rows": str(OUT_CANDIDATES),
        },
    }
    for seed in range(3):
        candidates, _, _ = seed_payload[seed]
        candidates_only = candidates.loc[candidates.fair_bound_above_ask]
        summary["all_sell_order_candidates"][str(seed)]["held_price_bucket_counts"] = {
            str(k): int(v) for k, v in candidates_only.held_price_bucket.value_counts().items()
        }
        seed_exposure = exposures_df.loc[exposures_df.seed.eq(seed)]
        per_bucket: dict[str, object] = {}
        for bucket, frame in seed_exposure.groupby("held_price_bucket", sort=True):
            per_bucket[str(bucket)] = {
                "unique_position_episodes": int(len(frame)),
                "episodes_with_open_position": int((frame.tracked_quantity > 1e-6).sum()),
                "fate_counts": {str(k): int(v) for k, v in Counter(frame.fate).items()},
                "later_fair_order_fill_count": int((frame.direct_order_sell_fill_quantity > 1e-6).sum()),
                "direct_order_fill_vwap_cents_median": (
                    float(frame.direct_order_sell_fill_vwap.dropna().median() * 100)
                    if frame.direct_order_sell_fill_vwap.notna().any()
                    else None
                ),
                "median_later_sell_vwap_cents": (
                    float(frame.later_sell_vwap_allocated_fifo.dropna().median() * 100)
                    if frame.later_sell_vwap_allocated_fifo.notna().any()
                    else None
                ),
                "settled_episode_count": int((frame.settled_qty_allocated_fifo > 1e-6).sum()),
                "settled_winner_episode_count": int(
                    ((frame.settled_qty_allocated_fifo > 1e-6) & (frame.settlement_per_share == 1.0)).sum()
                ),
                "settled_loser_episode_count": int(
                    ((frame.settled_qty_allocated_fifo > 1e-6) & (frame.settlement_per_share == 0.0)).sum()
                ),
                "path_complete_episode_count": int(frame.actual_value_path_complete.sum()),
                "counterfactual_tracked_shares": float(frame.tracked_quantity.sum()),
                "counterfactual_ask_value_dollars": float(frame.counterfactual_sell_at_best_ask_value.sum()),
                "actual_later_value_dollars": float(frame.actual_value_after_quote.sum()),
                "actual_minus_ask_counterfactual_dollars": float(frame.actual_minus_ask_counterfactual_dollars.sum()),
                "actual_minus_counterfactual_per_share_cents": (
                    float(
                        frame.actual_minus_ask_counterfactual_dollars.sum()
                        / frame.tracked_quantity.sum()
                        * 100
                    )
                    if frame.tracked_quantity.sum() > 0
                    else None
                ),
                "complete_path_only_actual_minus_ask_dollars": float(
                    frame.loc[frame.actual_value_path_complete, "actual_minus_ask_counterfactual_dollars"].sum()
                ),
                "unresolved_tracked_shares": float(
                    frame.loc[~frame.actual_value_path_complete, "tracked_quantity"].sum()
                    - frame.loc[~frame.actual_value_path_complete, "later_sell_qty_allocated_fifo"].sum()
                    - frame.loc[~frame.actual_value_path_complete, "settled_qty_allocated_fifo"].sum()
                ),
            }
        summary["first_episode_exposures"][str(seed)] = per_bucket
    OUT.write_text(json.dumps(summary, indent=2, sort_keys=True))
    print("RESULT_JSON", OUT)
    print(json.dumps(summary, indent=2, sort_keys=True))


catalog = {entry.match_id: entry for entry in load_match_catalog(MATCH_CATALOG_PATH).values()}


if __name__ == "__main__":
    main()
