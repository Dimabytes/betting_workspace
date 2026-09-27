from __future__ import annotations

import bisect
import csv
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from shared.constants.paths import RAW_TELONEX_POLYMARKET_DIR
from shared.utils.match_catalog import load_match_catalog
from shared.utils.telonex_book import NS_PER_US, US_PER_SECOND, load_token_book


LIVE = Path("data/backtests/dota_maker/LIVE/seed0")
ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
WORK = ROOT / "work" / "data-luna"
SUMMARY = WORK / "live_cross_summary.txt"
MAP_OUTPUT = WORK / "live_cross_per_map.csv"
EVENT_OUTPUT = WORK / "live_cross_events.csv"
MATCH_CATALOG = Path("data/new_processed/match_catalog/match_catalog.parquet")


def main() -> None:
    catalog = load_match_catalog(MATCH_CATALOG)
    quote = pd.read_parquet(
        LIVE / "quote_events.parquet",
        columns=["match_id", "ts_ns", "kind", "token_index", "side", "price", "order_id", "quantity", "reason"],
    )
    fills = pd.read_parquet(
        LIVE / "fills.parquet",
        columns=["match_id", "token_index", "side", "price", "quantity", "submitted_quantity", "ts_ns", "queue_ahead", "order_id"],
    )
    manifest_match_count = 613

    fills_by_order = {str(key): frame.sort_values("ts_ns") for key, frame in fills.groupby("order_id", sort=False)}
    order_events = quote[(quote.order_id != "") & (quote.kind != "no_quote")].copy()
    order_events.sort_values(["match_id", "order_id", "ts_ns"], inplace=True)
    order_rows = []
    for (match_id, order_id), frame in order_events.groupby(["match_id", "order_id"], sort=False):
        accepted = frame[frame.kind.eq("accepted")]
        if accepted.empty:
            continue
        first_accept = accepted.iloc[0]
        start_ns = int(first_accept.ts_ns)
        terminal = frame[frame.kind.eq("cancel_ack")]
        end_ns = int(terminal.ts_ns.min()) if not terminal.empty else None
        fill_frame = fills_by_order.get(str(order_id))
        full_fill_ns = None
        if fill_frame is not None and not fill_frame.empty:
            submitted_quantity = float(first_accept.quantity)
            cumulative = fill_frame.quantity.cumsum()
            complete = fill_frame.loc[cumulative + 1e-9 >= submitted_quantity]
            if not complete.empty:
                full_fill_ns = int(complete.ts_ns.iloc[0])
        if end_ns is None:
            end_ns = full_fill_ns
        elif full_fill_ns is not None:
            end_ns = min(end_ns, full_fill_ns)
        if end_ns is None:
            match_events = quote[quote.match_id.eq(int(match_id))]
            end_ns = int(match_events.ts_ns.max())
        order_rows.append({
            "match_id": int(match_id), "token_index": int(first_accept.token_index), "order_id": str(order_id),
            "side": str(first_accept.side), "price": float(first_accept.price), "quantity": float(first_accept.quantity),
            "start_ns": start_ns, "end_ns": max(start_ns, end_ns),
            "ended_by_full_fill": bool(full_fill_ns is not None and full_fill_ns <= end_ns),
            "had_cancel_ack": not terminal.empty,
        })
    orders = pd.DataFrame(order_rows)

    active_by_match_token: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    for order in order_rows:
        active_by_match_token[(int(order["match_id"]), int(order["token_index"]))].append(order)
    for intervals in active_by_match_token.values():
        intervals.sort(key=lambda row: (int(row["start_ns"]), str(row["order_id"])))

    fills_by_match_token: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    for row in fills.to_dict("records"):
        fills_by_match_token[(int(row["match_id"]), int(row["token_index"]))].append(row)
    for records in fills_by_match_token.values():
        records.sort(key=lambda row: int(row["ts_ns"]))

    per_map_rows = []
    live_events = []
    all_cross_ns: dict[tuple[int, int], list[int]] = defaultdict(list)
    buy_under_ns_by_order: dict[str, list[int]] = defaultdict(list)
    all_metrics = Counter()
    ids_with_orders = sorted({int(row["match_id"]) for row in order_rows})

    for ordinal, match_id in enumerate(ids_with_orders, start=1):
        entry = catalog[match_id]
        map_metrics = Counter()
        map_order_count = 0
        map_orders = {}
        for token_index in (0, 1):
            intervals = active_by_match_token.get((match_id, token_index), [])
            if not intervals:
                continue
            map_order_count += len(intervals)
            map_orders[token_index] = intervals
            start_us = min(int(row["start_ns"]) for row in intervals) // NS_PER_US
            end_us = max(int(row["end_ns"]) for row in intervals) // NS_PER_US
            book = load_token_book(
                token_id=entry.gamma.token_ids[token_index],
                start_us=start_us,
                end_us=end_us,
                telonex_root=RAW_TELONEX_POLYMARKET_DIR,
            )
            if book is None:
                map_metrics["missing_books"] += 1
                continue

            starts = sorted((int(row["start_ns"]), str(row["order_id"]), row) for row in intervals)
            ends = sorted((int(row["end_ns"]), str(row["order_id"])) for row in intervals)
            active: dict[str, dict[str, object]] = {}
            si = ei = 0
            for ts_us, bid, ask in zip(book.timestamps_us, book.bids, book.asks, strict=True):
                ts_ns = int(ts_us) * NS_PER_US
                while ei < len(ends) and ends[ei][0] <= ts_ns:
                    active.pop(ends[ei][1], None)
                    ei += 1
                while si < len(starts) and starts[si][0] <= ts_ns:
                    active[starts[si][1]] = starts[si][2]
                    si += 1
                if not active:
                    continue
                map_metrics["book_snapshots_while_resting"] += 1
                if ask is not None:
                    for order_id, active_order in active.items():
                        if active_order["side"] == "BUY" and ask < float(active_order["price"]):
                            map_metrics["ask_below_resting_buy_order_snapshots"] += 1
                            is_strict_crossed_book = bid is not None and bid > ask
                            map_metrics["ask_below_buy_order_on_crossed_book_snapshots"] += int(is_strict_crossed_book)
                            buy_under_ns_by_order[order_id].append(ts_ns)
                            live_events.append({
                                "match_id": match_id, "token_index": token_index, "ts_ns": ts_ns,
                                "kind": "ask_below_resting_buy", "best_bid": bid, "best_ask": ask,
                                "active_order_count": len(active), "active_buy_count": sum(order["side"] == "BUY" for order in active.values()),
                                "order_id": order_id, "order_price": active_order["price"], "order_quantity": active_order["quantity"],
                                "book_strict_crossed": is_strict_crossed_book,
                            })
                if bid is not None:
                    for order_id, active_order in active.items():
                        if active_order["side"] == "SELL" and bid > float(active_order["price"]):
                            map_metrics["bid_above_resting_sell_order_snapshots"] += 1
                            map_metrics["bid_above_sell_order_on_crossed_book_snapshots"] += int(ask is not None and bid > ask)
                            live_events.append({
                                "match_id": match_id, "token_index": token_index, "ts_ns": ts_ns,
                                "kind": "bid_above_resting_sell", "best_bid": bid, "best_ask": ask,
                                "active_order_count": len(active), "active_buy_count": sum(order["side"] == "BUY" for order in active.values()),
                                "order_id": order_id, "order_price": active_order["price"], "order_quantity": active_order["quantity"],
                                "book_strict_crossed": ask is not None and bid > ask,
                            })
                if bid is None or ask is None:
                    continue
                if bid > ask:
                    map_metrics["crossed_snapshots_while_resting"] += 1
                    all_cross_ns[(match_id, token_index)].append(ts_ns)
                    live_events.append({
                        "match_id": match_id, "token_index": token_index, "ts_ns": ts_ns,
                        "kind": "crossed", "best_bid": bid, "best_ask": ask,
                        "active_order_count": len(active), "active_buy_count": sum(order["side"] == "BUY" for order in active.values()),
                        "book_strict_crossed": True,
                    })
                elif bid == ask:
                    map_metrics["locked_snapshots_while_resting"] += 1

        map_fills = fills[fills.match_id.eq(match_id)]
        related_fill_indexes = set()
        ask_below_fill_indexes = set()
        cross_fill_time_deltas = []
        candidate_fill_time_deltas = []
        for fill_index, fill in map_fills.iterrows():
            key = (match_id, int(fill.token_index))
            cross_times = all_cross_ns.get(key, [])
            # Cross timestamps for a token were appended in book order; use nearest to fill.
            if cross_times:
                pos = bisect.bisect_left(cross_times, int(fill.ts_ns))
                candidates = [cross_times[i] for i in (pos - 1, pos) if 0 <= i < len(cross_times)]
                closest = min(candidates, key=lambda value: abs(value - int(fill.ts_ns)))
                delta = int(fill.ts_ns) - closest
                if abs(delta) <= 2_000_000_000:
                    related_fill_indexes.add(fill_index)
                    cross_fill_time_deltas.append(delta / 1e9)
            same_order_cross = buy_under_ns_by_order.get(str(fill.order_id), [])
            if fill.side == "BUY" and same_order_cross:
                pos = bisect.bisect_left(same_order_cross, int(fill.ts_ns))
                candidates = [same_order_cross[i] for i in (pos - 1, pos) if 0 <= i < len(same_order_cross)]
                closest = min(candidates, key=lambda value: abs(value - int(fill.ts_ns)))
                delta = int(fill.ts_ns) - closest
                if abs(delta) <= 2_000_000_000:
                    ask_below_fill_indexes.add(fill_index)
                    candidate_fill_time_deltas.append(delta / 1e9)

        cross_fills = map_fills.loc[sorted(related_fill_indexes)] if related_fill_indexes else map_fills.iloc[0:0]
        ask_below_fills = map_fills.loc[sorted(ask_below_fill_indexes)] if ask_below_fill_indexes else map_fills.iloc[0:0]
        map_metrics["fills_with_cross_within_2s"] = len(cross_fills)
        map_metrics["cross_fill_notional_usd"] = float((cross_fills.price * cross_fills.quantity).sum())
        map_metrics["cross_fill_quantity"] = float(cross_fills.quantity.sum())
        map_metrics["ask_below_buy_fill_count"] = len(ask_below_fills)
        map_metrics["ask_below_buy_fill_notional_usd"] = float((ask_below_fills.price * ask_below_fills.quantity).sum())
        map_metrics["ask_below_buy_fill_quantity"] = float(ask_below_fills.quantity.sum())
        map_metrics["ask_below_buy_fills_queue_positive"] = int((ask_below_fills.queue_ahead > 0).sum())
        map_metrics["ask_below_buy_fills_queue_zero"] = int((ask_below_fills.queue_ahead <= 0).sum())
        row = {"match_id": match_id, "orders": map_order_count, "fills": len(map_fills), **dict(map_metrics)}
        per_map_rows.append(row)
        for name, value in map_metrics.items():
            all_metrics[name] += value
        if ordinal % 25 == 0 or ordinal == len(ids_with_orders):
            print(f"processed {ordinal}/{len(ids_with_orders)} LIVE maps", flush=True)

    related_fill_mask = []
    candidate_fill_mask = []
    cross_deltas = []
    candidate_deltas = []
    for fill_index, fill in fills.iterrows():
        key = (int(fill.match_id), int(fill.token_index))
        cross_times = all_cross_ns.get(key, [])
        if cross_times:
            pos = bisect.bisect_left(cross_times, int(fill.ts_ns))
            candidates = [cross_times[i] for i in (pos - 1, pos) if 0 <= i < len(cross_times)]
            closest = min(candidates, key=lambda value: abs(value - int(fill.ts_ns)))
            delta = int(fill.ts_ns) - closest
            if abs(delta) <= 2_000_000_000:
                related_fill_mask.append(fill_index)
                cross_deltas.append(delta / 1e9)
        same_order_cross = buy_under_ns_by_order.get(str(fill.order_id), [])
        if fill.side == "BUY" and same_order_cross:
            pos = bisect.bisect_left(same_order_cross, int(fill.ts_ns))
            candidates = [same_order_cross[i] for i in (pos - 1, pos) if 0 <= i < len(same_order_cross)]
            closest = min(candidates, key=lambda value: abs(value - int(fill.ts_ns)))
            delta = int(fill.ts_ns) - closest
            if abs(delta) <= 2_000_000_000:
                candidate_fill_mask.append(fill_index)
                candidate_deltas.append(delta / 1e9)

    cross_fills_all = fills.loc[related_fill_mask]
    ask_below_fills_all = fills.loc[candidate_fill_mask]
    map_frame = pd.DataFrame(per_map_rows)
    count_fields = (
        "book_snapshots_while_resting", "crossed_snapshots_while_resting", "locked_snapshots_while_resting",
        "ask_below_resting_buy_order_snapshots", "ask_below_buy_order_on_crossed_book_snapshots",
        "bid_above_resting_sell_order_snapshots", "bid_above_sell_order_on_crossed_book_snapshots",
    )
    for field in count_fields:
        if field not in map_frame:
            map_frame[field] = 0
        map_frame[field] = map_frame[field].fillna(0)
    map_frame.to_csv(MAP_OUTPUT, index=False)
    event_columns = [
        "match_id", "token_index", "ts_ns", "kind", "best_bid", "best_ask", "active_order_count",
        "active_buy_count", "order_id", "order_price", "order_quantity",
        "book_strict_crossed",
    ]
    pd.DataFrame(live_events, columns=event_columns).to_csv(EVENT_OUTPUT, index=False)

    total_raw = Counter()
    for _, row in map_frame.iterrows():
        for field in (
            "book_snapshots_while_resting", "crossed_snapshots_while_resting", "locked_snapshots_while_resting",
            "ask_below_resting_buy_order_snapshots", "ask_below_buy_order_on_crossed_book_snapshots",
            "bid_above_resting_sell_order_snapshots", "bid_above_sell_order_on_crossed_book_snapshots",
        ):
            total_raw[field] += int(row.get(field, 0))
    accepted_orders = int(order_events.kind.eq("accepted").sum())
    full_orders = sum(bool(row["ended_by_full_fill"]) for row in order_rows)
    no_cancel_ack = sum(not row["had_cancel_ack"] and not row["ended_by_full_fill"] for row in order_rows)
    post_only_rejected = int(quote.kind.eq("rejected").sum())
    rejected_reasons = quote.loc[quote.kind.eq("rejected"), "reason"].value_counts(dropna=False).to_dict()

    lines = [
        f"manifest_selected_matches={manifest_match_count} quote_event_matches={quote.match_id.nunique()} maps_with_accepted_orders={len(ids_with_orders)}",
        f"quote_event_rows={len(quote)} accepted_orders={accepted_orders} active_order_intervals={len(order_rows)} full_fill_ended_intervals={full_orders} unresolved_to_last_map_event={no_cancel_ack} rejected_events={post_only_rejected}",
        f"rejected_reasons={rejected_reasons}",
        f"fills={len(fills)} fill_matches={fills.match_id.nunique()} fill_orders={fills.order_id.nunique()} overall_notional_usd={(fills.price*fills.quantity).sum():.6f}",
        f"raw_book_snapshots_while_any_order_resting={total_raw['book_snapshots_while_resting']} strict_cross_snapshots_while_resting={total_raw['crossed_snapshots_while_resting']} locked_snapshots_while_resting={total_raw['locked_snapshots_while_resting']}",
        f"strict_cross_maps={int(map_frame.loc[map_frame.crossed_snapshots_while_resting.fillna(0).gt(0),'match_id'].nunique())} locked_maps={int(map_frame.loc[map_frame.locked_snapshots_while_resting.fillna(0).gt(0),'match_id'].nunique())}",
        f"ask_below_active_buy_order_snapshot_pairs={total_raw['ask_below_resting_buy_order_snapshots']} of_which_on_strict_crossed_book={total_raw['ask_below_buy_order_on_crossed_book_snapshots']} bid_above_active_sell_order_snapshot_pairs={total_raw['bid_above_resting_sell_order_snapshots']} of_which_on_strict_crossed_book={total_raw['bid_above_sell_order_on_crossed_book_snapshots']} unique_buy_orders_with_ask_below_limit={len(buy_under_ns_by_order)}",
        f"fills_with_strict_cross_same_token_within_2s={len(cross_fills_all)} unique_orders={cross_fills_all.order_id.nunique()} quantity={cross_fills_all.quantity.sum():.6f} notional_usd={(cross_fills_all.price*cross_fills_all.quantity).sum():.6f} queue_ahead_positive={int((cross_fills_all.queue_ahead>0).sum())} queue_ahead_zero={int((cross_fills_all.queue_ahead<=0).sum())} delta_s_median={float(np.median(cross_deltas)) if cross_deltas else None}",
        f"same_order_buy_fills_with_ask_below_resting_limit_within_2s={len(ask_below_fills_all)} unique_orders={ask_below_fills_all.order_id.nunique()} quantity={ask_below_fills_all.quantity.sum():.6f} notional_usd={(ask_below_fills_all.price*ask_below_fills_all.quantity).sum():.6f} queue_ahead_positive={int((ask_below_fills_all.queue_ahead>0).sum())} queue_ahead_zero={int((ask_below_fills_all.queue_ahead<=0).sum())} delta_s_median={float(np.median(candidate_deltas)) if candidate_deltas else None}",
        f"outputs: {MAP_OUTPUT} ; {EVENT_OUTPUT}",
    ]
    SUMMARY.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
