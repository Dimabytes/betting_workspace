"""Read-only summaries of archived live sessions for LoL/Dota comparison."""
from __future__ import annotations

import csv
import bisect
import json
import math
import statistics
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path.cwd()
sys.path.insert(0, str(REPO))
from scripts.measure_live_grid_cadence import is_polymarket_signal, read_jsonl

RESEARCH = Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23')
WORK = RESEARCH / 'work' / 'live-luna'
DATA_ROOT = REPO / 'data' / 'trader'
PERIODS = {
    'since_2026-08-31': datetime(2026, 8, 31, tzinfo=timezone.utc),
    'since_2026-09-18': datetime(2026, 9, 18, tzinfo=timezone.utc),
}
HORIZONS = (10, 30, 60, 300)
MAX_SIGNAL_DISTANCE_SECONDS = 10.0


def parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def nearest_rank(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def mean_or_none(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    dx = [value - mx for value in xs]
    dy = [value - my for value in ys]
    xx = sum(value * value for value in dx)
    yy = sum(value * value for value in dy)
    if xx == 0 or yy == 0:
        return None
    return sum(x * y for x, y in zip(dx, dy, strict=True)) / math.sqrt(xx * yy)


def load_shared_net() -> dict[str, float]:
    """Load summary net only as a fallback when session_end has no usable cash block."""
    result: dict[str, float] = {}
    for game in ('lol', 'dota'):
        path = RESEARCH / 'work' / 'shared' / f'{game}_summary.txt'
        if not path.exists():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            fields = line.split()
            if not fields or '[live]' not in line or 'mode=live' not in line:
                continue
            match_id = fields[0]
            net_field = next((part for part in fields if part.startswith('net=')), None)
            if net_field is None or net_field == 'net=n/a':
                continue
            try:
                result[match_id] = float(net_field.split('=', 1)[1])
            except ValueError:
                continue
    return result


def signal_second(record: dict[str, Any]) -> int | None:
    second = record.get('second')
    if isinstance(second, bool) or not isinstance(second, int):
        return None
    return second


def signal_mid(record: dict[str, Any], outcome: str) -> float | None:
    key = 'yes_mid' if outcome == 'yes' else 'no_mid'
    return finite_number(record.get(key))


def market_outcome(token_id: object, market: dict[str, Any]) -> str | None:
    if token_id == market.get('yes_token_id'):
        return 'yes'
    if token_id == market.get('no_token_id'):
        return 'no'
    return None


def calculate_net(records: list[object], shared_net: dict[str, float], match_id: str) -> tuple[float | None, str]:
    endings = [record for record in records if isinstance(record, dict) and record.get('kind') == 'session_end']
    if endings:
        ending = endings[-1]
        cash = finite_number(ending.get('net_cash'))
        inventory = finite_number(ending.get('inventory_value'))
        if cash is not None and inventory is not None:
            return cash + inventory, 'session_end cash+inventory'
    if match_id in shared_net:
        return shared_net[match_id], 'shared summary net fallback (includes rebate)'
    return None, 'unknown'


def infer_holding(fills: list[dict[str, Any]]) -> tuple[float | None, float | None, float]:
    """Match SELL shares to earlier BUY shares per token, FIFO, using fill wall timestamps."""
    by_token: dict[str, deque[list[float | datetime | None]]] = defaultdict(deque)
    durations: list[float] = []
    duration_weights: list[float] = []
    matched_size = 0.0
    for fill in sorted(fills, key=lambda item: parse_utc(item.get('ts_utc')) or datetime.min.replace(tzinfo=timezone.utc)):
        token = str(fill.get('token_id', ''))
        size = finite_number(fill.get('size'))
        ts = parse_utc(fill.get('ts_utc'))
        if size is None or size <= 0 or ts is None:
            continue
        if fill.get('side') == 'BUY':
            by_token[token].append([size, ts])
        elif fill.get('side') == 'SELL':
            remaining = size
            while remaining > 1e-9 and by_token[token]:
                lot = by_token[token][0]
                lot_size = float(lot[0])
                buy_ts = lot[1]
                if not isinstance(buy_ts, datetime):
                    break
                amount = min(remaining, lot_size)
                elapsed = max(0.0, (ts - buy_ts).total_seconds())
                durations.append(elapsed)
                duration_weights.append(amount)
                matched_size += amount
                remaining -= amount
                lot[0] = lot_size - amount
                if float(lot[0]) <= 1e-9:
                    by_token[token].popleft()
    weighted_mean = None
    if duration_weights:
        weighted_mean = sum(d * w for d, w in zip(durations, duration_weights, strict=True)) / sum(duration_weights)
    return weighted_mean, median_or_none(durations), matched_size


def summarize_map(match_id: str, match: dict[str, Any], records: list[object], shared_net: dict[str, float]) -> dict[str, Any]:
    session_starts = [r for r in records if isinstance(r, dict) and r.get('kind') == 'session_start']
    live_starts = [r for r in session_starts if r.get('execution_mode') == 'live']
    if not live_starts:
        return {}
    joined = parse_utc(match.get('joined_at_utc'))
    if joined is None:
        return {}
    game = match.get('game', 'dota')
    if game not in ('lol', 'dota'):
        return {}
    signals = [r for r in records if isinstance(r, dict) and is_polymarket_signal(r)]
    fills_raw = [r for r in records if isinstance(r, dict) and r.get('kind') in ('fill', 'late_fill') and r.get('venue') in (None, 'polymarket')]
    unique_fills: list[dict[str, Any]] = []
    seen_fill_keys: set[str] = set()
    for row in fills_raw:
        key = row.get('fill_key')
        if isinstance(key, str) and key in seen_fill_keys:
            continue
        if isinstance(key, str):
            seen_fill_keys.add(key)
        unique_fills.append(row)
    market = match.get('market') if isinstance(match.get('market'), dict) else {}
    buys = [row for row in unique_fills if row.get('side') == 'BUY']
    sells = [row for row in unique_fills if row.get('side') == 'SELL']
    buy_notional = sum((finite_number(row.get('price')) or 0.0) * (finite_number(row.get('size')) or 0.0) for row in buys)
    sell_notional = sum((finite_number(row.get('price')) or 0.0) * (finite_number(row.get('size')) or 0.0) for row in sells)
    end_net, net_source = calculate_net(records, shared_net, match_id)
    buy_clip_notionals: list[float] = []
    for record in records:
        if not isinstance(record, dict) or record.get('kind') != 'quote':
            continue
        placed = record.get('placed')
        if not isinstance(placed, list):
            continue
        for order in placed:
            if not isinstance(order, dict) or order.get('side') != 'BUY':
                continue
            price = finite_number(order.get('price'))
            size = finite_number(order.get('size'))
            if price is not None and size is not None and price > 0 and size > 0:
                buy_clip_notionals.append(price * size)
    seconds_by_outcome: dict[str, dict[int, dict[str, Any]]] = {'yes': {}, 'no': {}}
    seconds_by_radiant: dict[int, dict[str, Any]] = {}
    for row in signals:
        second = signal_second(row)
        if second is None:
            continue
        for outcome in ('yes', 'no'):
            if signal_mid(row, outcome) is not None:
                seconds_by_outcome[outcome][second] = row
        if finite_number(row.get('market_p_radiant')) is not None:
            seconds_by_radiant[second] = row
    fills_with_time = [row for row in unique_fills if parse_utc(row.get('ts_utc')) is not None and signal_second(row) is not None]
    horn = parse_utc(match.get('horn_at_utc'))
    clock_offsets = []
    if horn is not None:
        for row in fills_with_time:
            ts = parse_utc(row.get('ts_utc'))
            second = signal_second(row)
            if ts is not None and second is not None:
                clock_offsets.append((ts - horn).total_seconds() - second)
    observed_offset = median_or_none(clock_offsets)
    if observed_offset is None:
        configured = finite_number(match.get('grid_delay_s' if game == 'lol' else 'steam_delay_s'))
        observed_offset = configured if configured is not None else 0.0
    first_model = [signal_second(row) for row in signals if row.get('reason') == 'model' and signal_second(row) is not None]
    reason_counts = Counter(str(row.get('reason') or '<missing>') for row in signals)
    entry_block_counts = Counter(str(row.get('entry_block') or '<missing>') for row in signals if row.get('reason') == 'model')
    all_seconds = sorted({second for row in signals if (second := signal_second(row)) is not None})
    signal_gaps = [float(right - left) for left, right in zip(all_seconds, all_seconds[1:]) if right > left]
    hold_mean, hold_median, held_size_matched = infer_holding(unique_fills)
    first_buy_second = min((signal_second(row) for row in buys if signal_second(row) is not None), default=None)
    outcomes = sorted({outcome for row in unique_fills if (outcome := market_outcome(row.get('token_id'), market)) is not None})
    unknown_token_fills = sum(market_outcome(row.get('token_id'), market) is None for row in unique_fills)
    return {
        'match_id': match_id,
        'game': game,
        'feed_source': match.get('feed_source') or '<missing>',
        'joined_at_utc': joined.isoformat(),
        'tournament': match.get('tournament') or '<missing>',
        'teams_radiant': (match.get('teams') or {}).get('radiant') if isinstance(match.get('teams'), dict) else None,
        'teams_dire': (match.get('teams') or {}).get('dire') if isinstance(match.get('teams'), dict) else None,
        'map_number': match.get('map_number'),
        'yes_is_radiant': market.get('yes_is_radiant'),
        'grid_delay_s': finite_number(match.get('grid_delay_s')),
        'horn_at_utc': match.get('horn_at_utc'),
        'signal_wall_offset_s': observed_offset,
        'fill_offset_range_s': [min(clock_offsets), max(clock_offsets)] if clock_offsets else [],
        'fill_offset_mad_s': median_or_none([abs(value - observed_offset) for value in clock_offsets]) if clock_offsets else None,
        'net': end_net,
        'net_source': net_source,
        'fills': len(unique_fills),
        'buy_fills': len(buys),
        'sell_fills': len(sells),
        'buy_notional': buy_notional,
        'sell_notional': sell_notional,
        'pnl_per_buy_notional': end_net / buy_notional if end_net is not None and buy_notional > 0 else None,
        'buy_quote_clips': buy_clip_notionals,
        'clip_buy_quote_median': median_or_none(buy_clip_notionals),
        'clip_buy_quote_p90': nearest_rank(buy_clip_notionals, 0.90),
        'clip_quote_count': len(buy_clip_notionals),
        'fill_outcomes': ','.join(outcomes) if outcomes else '<none>',
        'unknown_token_fills': unknown_token_fills,
        'first_entry_second': first_buy_second,
        'hold_seconds_weighted_mean': hold_mean,
        'hold_seconds_median_allocation': hold_median,
        'sell_matched_buy_shares': held_size_matched,
        'first_model_second': min(first_model) if first_model else None,
        'signal_rows': len(signals),
        'reason_counts': dict(reason_counts),
        'entry_block_counts': dict(entry_block_counts),
        'signal_gap_median_s': median_or_none(signal_gaps),
        'signal_gap_p90_s': nearest_rank(signal_gaps, 0.90),
        'signal_gap_gt5_s': sum(gap > 5 for gap in signal_gaps),
        'signal_gap_gt10_s': sum(gap > 10 for gap in signal_gaps),
        'signals': signals,
        'fills_rows': unique_fills,
        'seconds_by_outcome': seconds_by_outcome,
        'seconds_by_radiant': seconds_by_radiant,
    }


def nearest_signal(
    rows_by_second: dict[int, dict[str, Any]],
    target: float,
    value_key: str,
    max_distance: float = MAX_SIGNAL_DISTANCE_SECONDS,
    require_at_or_after: bool = False,
) -> tuple[float, int] | None:
    if not rows_by_second:
        return None
    seconds = sorted(rows_by_second)
    index = bisect.bisect_left(seconds, target)
    if require_at_or_after:
        for candidate in seconds[index:]:
            if candidate - target > max_distance:
                return None
            value = finite_number(rows_by_second[candidate].get(value_key))
            if value is not None:
                return value, candidate
        return None
    candidate_indices = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(seconds)]
    candidate_indices.sort(key=lambda candidate: (abs(seconds[candidate] - target), -seconds[candidate]))
    for candidate in candidate_indices:
        second = seconds[candidate]
        row = rows_by_second[second]
        value = finite_number(row.get(value_key))
        if value is not None and abs(second - target) <= max_distance:
            return value, second
    return None


def process_markouts(maps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in maps:
        horn = parse_utc(item.get('horn_at_utc'))
        offset = finite_number(item.get('signal_wall_offset_s')) or 0.0
        market = item.get('market', {})
        # Map IDs alone do not carry market data, so derive outcome from the token IDs retained in each map.
        for fill in item['fills_rows']:
            ts = parse_utc(fill.get('ts_utc'))
            price = finite_number(fill.get('price'))
            second = signal_second(fill)
            outcome = market_outcome(fill.get('token_id'), market)
            if ts is None or price is None or second is None or outcome is None or horn is None:
                continue
            for horizon in HORIZONS:
                target_wall = ts + timedelta(seconds=horizon)
                target_game_second = (target_wall - horn).total_seconds() - offset
                matched = nearest_signal(
                    item['seconds_by_outcome'][outcome],
                    target_game_second,
                    'yes_mid' if outcome == 'yes' else 'no_mid',
                    require_at_or_after=True,
                )
                if matched is None:
                    continue
                mid, matched_second = matched
                side_sign = 1.0 if fill.get('side') == 'BUY' else -1.0
                raw_markout = (mid - price) * 100.0
                results.append({
                    'match_id': item['match_id'],
                    'game': item['game'],
                    'side': fill.get('side'),
                    'outcome': outcome,
                    'fill_second': second,
                    'fill_ts_utc': fill.get('ts_utc'),
                    'fill_price': price,
                    'fill_size': finite_number(fill.get('size')),
                    'horizon_s': horizon,
                    'matched_signal_second': matched_second,
                    'target_game_second': target_game_second,
                    'match_distance_s': abs(matched_second - target_game_second),
                    'future_mid': mid,
                    'raw_mid_minus_fill_cents': raw_markout,
                    'signed_markout_cents': side_sign * raw_markout,
                })
    return results


def calibration(maps: list[dict[str, Any]]) -> dict[str, Any]:
    predictions: list[tuple[str, float, float, float]] = []
    missed_target = 0
    for item in maps:
        radiant_rows = item['seconds_by_radiant']
        for row in item['signals']:
            if row.get('reason') != 'model':
                continue
            second = signal_second(row)
            pred_fair = finite_number(row.get('radiant_fair'))
            current = finite_number(row.get('market_p_radiant'))
            if second is None or pred_fair is None or current is None:
                continue
            future = nearest_signal(radiant_rows, second + 300, 'market_p_radiant', require_at_or_after=True)
            if future is None:
                missed_target += 1
                continue
            future_mid, future_second = future
            predictions.append((item['match_id'], pred_fair - current, future_mid - current, float(future_second - second)))
    pred_values = [row[1] for row in predictions]
    realized = [row[2] for row in predictions]
    # Equal-count quartile bins make small live samples readable without implying precision at fixed edge thresholds.
    ordered = sorted(predictions, key=lambda row: row[1])
    quartile_bins = []
    if ordered:
        bin_count = min(4, len(ordered))
        for bin_index in range(bin_count):
            start = round(bin_index * len(ordered) / bin_count)
            end = round((bin_index + 1) * len(ordered) / bin_count)
            section = ordered[start:end]
            if not section:
                continue
            p = [row[1] for row in section]
            y = [row[2] for row in section]
            quartile_bins.append({
                'n': len(section),
                'predicted_min': min(p),
                'predicted_max': max(p),
                'mean_predicted_delta': statistics.fmean(p),
                'mean_realized_move_300s': statistics.fmean(y),
                'mean_abs_error': statistics.fmean(abs(a - b) for a, b in zip(p, y, strict=True)),
            })
    return {
        'paired_n': len(predictions),
        'model_rows_without_300s_pair': missed_target,
        'mean_predicted_delta': mean_or_none(pred_values),
        'mean_realized_move_300s': mean_or_none(realized),
        'pearson_r': correlation(pred_values, realized),
        'positive_prediction_share': sum(value > 0 for value in pred_values) / len(pred_values) if pred_values else None,
        'directional_accuracy': sum((p >= 0) == (y >= 0) for p, y in zip(pred_values, realized, strict=True)) / len(pred_values) if pred_values else None,
        'quartile_bins': quartile_bins,
    }


def aggregate_period(maps: list[dict[str, Any]], markouts: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for game in ('lol', 'dota'):
        group = [item for item in maps if item['game'] == game]
        known_net = [item for item in group if item['net'] is not None]
        total_buy = sum(item['buy_notional'] for item in group)
        closed_buy = sum(item['buy_notional'] for item in known_net)
        total_net = sum(item['net'] or 0.0 for item in known_net)
        all_clips = [clip for item in group for clip in item['buy_quote_clips']]
        all_gaps = [
            float(right - left)
            for item in group
            for left, right in zip(sorted({signal_second(row) for row in item['signals'] if signal_second(row) is not None}), sorted({signal_second(row) for row in item['signals'] if signal_second(row) is not None})[1:])
            if right > left
        ]
        reasons = Counter()
        entry_blocks = Counter()
        first_model = []
        map_first_model = 0
        for item in group:
            reasons.update(item['reason_counts'])
            entry_blocks.update(item['entry_block_counts'])
            if item['first_model_second'] is not None:
                first_model.append(float(item['first_model_second']))
                map_first_model += 1
        total_signals = sum(reasons.values())
        reason_shares = {
            label: reasons[label] / total_signals if total_signals else None
            for label in ('stale', 'paused', 'missing_book')
        }
        fill_offsets = [
            float(item['signal_wall_offset_s'])
            for item in group
            if item['signal_wall_offset_s'] is not None and item['fills'] > 0
        ]
        offset_residuals = [
            float(item['fill_offset_mad_s'])
            for item in group
            if item['fill_offset_mad_s'] is not None and item['fills'] > 0
        ]
        first_entry_seconds = [
            float(item['first_entry_second']) for item in group if item['first_entry_second'] is not None
        ]
        map_hold_durations = [
            float(item['hold_seconds_weighted_mean'])
            for item in group
            if item['hold_seconds_weighted_mean'] is not None
        ]
        outcome_map_counts = Counter(item['fill_outcomes'] for item in group if item['fills'] > 0)
        game_markouts: dict[str, Any] = {}
        for side in ('BUY', 'SELL'):
            for horizon in HORIZONS:
                values = [row['signed_markout_cents'] for row in markouts if row['game'] == game and row['side'] == side and row['horizon_s'] == horizon]
                raw_values = [row['raw_mid_minus_fill_cents'] for row in markouts if row['game'] == game and row['side'] == side and row['horizon_s'] == horizon]
                game_markouts[f'{side}_{horizon}s'] = {
                    'n': len(values),
                    'mean_signed_cents': mean_or_none(values),
                    'median_signed_cents': median_or_none(values),
                    'mean_raw_mid_minus_fill_cents': mean_or_none(raw_values),
                }
        summary[game] = {
            'maps': len(group),
            'maps_with_fills': sum(item['fills'] > 0 for item in group),
            'maps_with_known_net': len(known_net),
            'fills': sum(item['fills'] for item in group),
            'buy_fills': sum(item['buy_fills'] for item in group),
            'sell_fills': sum(item['sell_fills'] for item in group),
            'buy_notional_all_maps': total_buy,
            'sell_notional_all_maps': sum(item['sell_notional'] for item in group),
            'net_known_maps': total_net,
            'buy_notional_on_known_net_maps': closed_buy,
            'net_per_buy_notional_known_maps': total_net / closed_buy if closed_buy else None,
            'buy_quote_order_notional_median': median_or_none(all_clips),
            'buy_quote_order_notional_p90': nearest_rank(all_clips, 0.90),
            'buy_quote_order_count': len(all_clips),
            'first_model_second_median': median_or_none(first_model),
            'first_model_second_p90': nearest_rank(first_model, 0.90),
            'first_entry_second_median': median_or_none(first_entry_seconds),
            'first_entry_second_p90': nearest_rank(first_entry_seconds, 0.90),
            'map_hold_duration_median_s': median_or_none(map_hold_durations),
            'map_hold_duration_p90_s': nearest_rank(map_hold_durations, 0.90),
            'maps_with_matched_buy_sell': len(map_hold_durations),
            'fill_outcome_map_counts': dict(outcome_map_counts),
            'unrecognized_fill_tokens': sum(item['unknown_token_fills'] for item in group),
            'maps_with_model': map_first_model,
            'maps_without_model': len(group) - map_first_model,
            'reason_counts': dict(reasons),
            'reason_shares': reason_shares,
            'entry_block_counts': dict(entry_blocks),
            'signal_wall_offset_median_s': median_or_none(fill_offsets),
            'signal_wall_offset_p10_s': nearest_rank(fill_offsets, 0.10),
            'signal_wall_offset_p90_s': nearest_rank(fill_offsets, 0.90),
            'fill_offset_mad_median_s': median_or_none(offset_residuals),
            'fill_offset_mad_p90_s': nearest_rank(offset_residuals, 0.90),
            'signal_gap_median_s': median_or_none(all_gaps),
            'signal_gap_p90_s': nearest_rank(all_gaps, 0.90),
            'signal_gap_gt5_s': sum(gap > 5 for gap in all_gaps),
            'signal_gap_gt10_s': sum(gap > 10 for gap in all_gaps),
            'markouts': game_markouts,
        'calibration_300s': calibration(group),
        }
    lol_maps = [item for item in maps if item['game'] == 'lol']
    league_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in lol_maps:
        league_groups[str(item['tournament'])].append(item)
    league_rows = []
    for tournament, group in sorted(league_groups.items(), key=lambda pair: -len(pair[1])):
        known = [item for item in group if item['net'] is not None]
        net = sum(item['net'] or 0.0 for item in known)
        buys = sum(item['buy_notional'] for item in known)
        league_rows.append({
            'tournament': tournament,
            'maps': len(group),
            'maps_with_fills': sum(item['fills'] > 0 for item in group),
            'fills': sum(item['fills'] for item in group),
            'buy_notional_known_net_maps': buys,
            'net_known_maps': net,
            'net_per_buy_notional': net / buys if buys else None,
            'maps_with_known_net': len(known),
        })
    summary['lol_leagues'] = league_rows
    source_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in maps:
        source_groups[(str(item['game']), str(item['feed_source']))].append(item)
    source_rows = {}
    for (game, source), group in sorted(source_groups.items()):
        known = [item for item in group if item['net'] is not None]
        buys = sum(item['buy_notional'] for item in known)
        net = sum(item['net'] or 0.0 for item in known)
        source_markout = [
            row['signed_markout_cents'] for row in markouts
            if row['match_id'] in {item['match_id'] for item in group}
            and row['side'] == 'BUY' and row['horizon_s'] == 30
        ]
        source_gaps = [
            float(right - left)
            for item in group
            for left, right in zip(
                sorted({signal_second(row) for row in item['signals'] if signal_second(row) is not None}),
                sorted({signal_second(row) for row in item['signals'] if signal_second(row) is not None})[1:],
            )
            if right > left
        ]
        source_rows[f'{game}/{source}'] = {
            'maps': len(group),
            'maps_with_fills': sum(item['fills'] > 0 for item in group),
            'maps_with_known_net': len(known),
            'fills': sum(item['fills'] for item in group),
            'buy_notional_known_net_maps': buys,
            'net_known_maps': net,
            'net_per_buy_notional_known_maps': net / buys if buys else None,
            'buy_quote_order_notional_median': median_or_none([clip for item in group for clip in item['buy_quote_clips']]),
            'buy_markout_30s_n': len(source_markout),
            'buy_markout_30s_mean_signed_cents': mean_or_none(source_markout),
            'signal_gap_median_s': median_or_none(source_gaps),
            'signal_gap_p90_s': nearest_rank(source_gaps, 0.90),
        }
    summary['feed_source_breakdown'] = source_rows
    return summary


def json_safe_map(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in {'signals', 'fills_rows', 'seconds_by_outcome', 'seconds_by_radiant', 'buy_quote_clips'}}


def write_csv(path: Path, records: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    shared_net = load_shared_net()
    all_maps: list[dict[str, Any]] = []
    for match_dir in sorted(DATA_ROOT.iterdir()):
        if not match_dir.is_dir():
            continue
        match_path = match_dir / 'match.json'
        session_path = match_dir / 'session.jsonl'
        if not match_path.exists() or not session_path.exists():
            continue
        try:
            match = json.loads(match_path.read_text(encoding='utf-8'))
            joined = parse_utc(match.get('joined_at_utc'))
            if joined is None or joined < PERIODS['since_2026-08-31']:
                continue
            if match.get('game', 'dota') not in ('lol', 'dota'):
                continue
            records = read_jsonl(session_path)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        summary = summarize_map(match_dir.name, match, records, shared_net)
        if summary:
            summary['market'] = match.get('market') if isinstance(match.get('market'), dict) else {}
            all_maps.append(summary)
    all_maps = [item for item in all_maps if parse_utc(item['joined_at_utc']) is not None and parse_utc(item['joined_at_utc']) >= PERIODS['since_2026-08-31']]
    markouts = process_markouts(all_maps)
    period_results: dict[str, Any] = {}
    for name, start in PERIODS.items():
        selected = [item for item in all_maps if parse_utc(item['joined_at_utc']) >= start]
        selected_markouts = [row for row in markouts if row['match_id'] in {item['match_id'] for item in selected}]
        period_results[name] = aggregate_period(selected, selected_markouts)
    (WORK / 'period_summary.json').write_text(json.dumps(period_results, indent=2, sort_keys=True), encoding='utf-8')
    (WORK / 'per_map.json').write_text(json.dumps([json_safe_map(item) for item in all_maps], indent=2), encoding='utf-8')
    map_rows = []
    for item in all_maps:
        map_rows.append({key: item[key] for key in (
            'match_id', 'game', 'feed_source', 'joined_at_utc', 'tournament', 'teams_radiant', 'teams_dire', 'map_number', 'yes_is_radiant',
            'fills', 'buy_fills', 'sell_fills', 'buy_notional', 'sell_notional', 'net', 'net_source', 'pnl_per_buy_notional',
            'fill_outcomes', 'unknown_token_fills', 'first_entry_second', 'clip_buy_quote_median', 'clip_buy_quote_p90',
            'clip_quote_count', 'hold_seconds_weighted_mean', 'hold_seconds_median_allocation', 'sell_matched_buy_shares',
            'first_model_second', 'signal_rows', 'signal_gap_median_s', 'signal_gap_p90_s', 'signal_gap_gt5_s', 'signal_gap_gt10_s',
            'signal_wall_offset_s', 'fill_offset_range_s', 'fill_offset_mad_s',
        )})
    write_csv(WORK / 'per_map.csv', map_rows, list(map_rows[0].keys()) if map_rows else ['match_id'])
    write_csv(WORK / 'fill_markouts.csv', markouts, list(markouts[0].keys()) if markouts else ['match_id'])
    for name, start in PERIODS.items():
        selected = [item for item in all_maps if parse_utc(item['joined_at_utc']) >= start]
        league_rows = period_results[name]['lol_leagues']
        write_csv(WORK / f'league_lol_{name}.csv', league_rows, list(league_rows[0].keys()) if league_rows else ['tournament'])
    compact_results = {}
    for period, result in period_results.items():
        compact_results[period] = {}
        for game in ('lol', 'dota'):
            values = result[game]
            compact_results[period][game] = {
                key: values[key] for key in (
                    'maps', 'maps_with_fills', 'maps_with_known_net', 'fills', 'buy_notional_all_maps',
                    'net_known_maps', 'buy_notional_on_known_net_maps', 'net_per_buy_notional_known_maps',
                    'buy_quote_order_notional_median', 'buy_quote_order_notional_p90', 'reason_shares',
                    'signal_gap_median_s', 'signal_gap_p90_s', 'signal_gap_gt5_s', 'signal_gap_gt10_s',
                    'first_entry_second_median', 'first_entry_second_p90', 'map_hold_duration_median_s',
                    'map_hold_duration_p90_s', 'maps_with_matched_buy_sell', 'fill_outcome_map_counts',
                    'unrecognized_fill_tokens', 'calibration_300s', 'markouts',
                )
            }
        compact_results[period]['feed_source_breakdown'] = result['feed_source_breakdown']
    print(json.dumps({
        'live_maps_since_2026_08_31': len(all_maps),
        'periods': compact_results,
        'outputs': [
            str(WORK / 'period_summary.json'), str(WORK / 'per_map.json'), str(WORK / 'per_map.csv'),
            str(WORK / 'fill_markouts.csv'), str(WORK / 'league_lol_since_2026-08-31.csv'),
            str(WORK / 'league_lol_since_2026-09-18.csv'),
        ],
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
