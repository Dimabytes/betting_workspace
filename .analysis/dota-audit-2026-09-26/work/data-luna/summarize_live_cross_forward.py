from __future__ import annotations

import bisect
from collections import defaultdict
from pathlib import Path

import pandas as pd


ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
WORK = ROOT / "work" / "data-luna"
events = pd.read_csv(WORK / "live_cross_events.csv")
fills = pd.read_parquet(
    Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/backtests/dota_maker/LIVE/seed0/fills.parquet"),
    columns=["match_id", "token_index", "side", "price", "quantity", "ts_ns", "queue_ahead", "order_id"],
)
results = pd.read_parquet(
    Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/backtests/dota_maker/LIVE/seed0/results.parquet"),
    columns=["match_id", "signal_mode", "feed_source"],
)
source_by_match = {
    int(row.match_id): (str(row.signal_mode), str(row.feed_source))
    for row in results.itertuples(index=False)
}

strict_by_market_token = defaultdict(list)
for row in events[events.kind.eq("crossed")].itertuples(index=False):
    strict_by_market_token[(int(row.match_id), int(row.token_index))].append(int(row.ts_ns))
for key in strict_by_market_token:
    strict_by_market_token[key].sort()

candidate_by_order = defaultdict(list)
candidate_rows = events[events.kind.eq("ask_below_resting_buy")]
for row in candidate_rows.itertuples(index=False):
    candidate_by_order[str(row.order_id)].append(int(row.ts_ns))
for key in candidate_by_order:
    candidate_by_order[key].sort()
crossed_candidate_by_order = defaultdict(list)
crossed_candidates = candidate_rows[candidate_rows.book_strict_crossed.astype(str).str.casefold().eq("true")]
for row in crossed_candidates.itertuples(index=False):
    crossed_candidate_by_order[str(row.order_id)].append(int(row.ts_ns))
for key in crossed_candidate_by_order:
    crossed_candidate_by_order[key].sort()


def match_nearest(times: list[int], fill_ns: int, max_ns: int = 2_000_000_000):
    if not times:
        return None
    pos = bisect.bisect_left(times, fill_ns)
    candidates = [times[i] for i in (pos - 1, pos) if 0 <= i < len(times)]
    closest = min(candidates, key=lambda value: abs(value - fill_ns))
    delta = fill_ns - closest
    return delta if abs(delta) <= max_ns else None


for label, fill_frame, time_map in (
    ("same_token_any_strict_cross", fills, strict_by_market_token),
    ("same_order_buy_ask_below_limit_any_book", fills[fills.side.eq("BUY")], candidate_by_order),
    ("same_order_buy_ask_below_limit_strict_crossed_book", fills[fills.side.eq("BUY")], crossed_candidate_by_order),
):
    matched = []
    for row in fill_frame.itertuples(index=False):
        key = (int(row.match_id), int(row.token_index)) if label.startswith("same_token") else str(row.order_id)
        delta = match_nearest(time_map.get(key, []), int(row.ts_ns))
        if delta is not None:
            matched.append((row, delta))
    forward = [(row, delta) for row, delta in matched if delta >= 0]
    backward = [(row, delta) for row, delta in matched if delta < 0]
    print(label)
    for name, cohort in (("all_within_2s", matched), ("cross_precedes_fill", forward), ("fill_precedes_cross", backward)):
        frame = pd.DataFrame([row._asdict() for row, _ in cohort])
        deltas = [delta / 1e9 for _, delta in cohort]
        print(
            name,
            "fills", len(frame),
            "orders", int(frame.order_id.nunique()) if len(frame) else 0,
            "qty", float(frame.quantity.sum()) if len(frame) else 0.0,
            "notional_usd", float((frame.price * frame.quantity).sum()) if len(frame) else 0.0,
            "queue_positive", int((frame.queue_ahead > 0).sum()) if len(frame) else 0,
            "queue_zero", int((frame.queue_ahead <= 0).sum()) if len(frame) else 0,
            "queue_median", float(frame.queue_ahead.median()) if len(frame) else None,
            "delta_s_min", min(deltas) if deltas else None,
            "delta_s_median", sorted(deltas)[len(deltas)//2] if deltas else None,
            "delta_s_max", max(deltas) if deltas else None,
        )
    for mode, source in sorted(set(source_by_match.values())):
        cohort = [(row, delta) for row, delta in forward if source_by_match.get(int(row.match_id)) == (mode, source)]
        frame = pd.DataFrame([row._asdict() for row, _ in cohort])
        print(
            "source", mode, source or "(none)",
            "cross_precedes_fills", len(frame),
            "orders", int(frame.order_id.nunique()) if len(frame) else 0,
            "qty", float(frame.quantity.sum()) if len(frame) else 0.0,
            "notional_usd", float((frame.price * frame.quantity).sum()) if len(frame) else 0.0,
            "queue_positive", int((frame.queue_ahead > 0).sum()) if len(frame) else 0,
            "queue_zero", int((frame.queue_ahead <= 0).sum()) if len(frame) else 0,
            "queue_median", float(frame.queue_ahead.median()) if len(frame) else None,
        )
