"""Research screen on actual live fills; not a strategy or execution backtest."""

import argparse
import bisect
import csv
import json
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Order:
    token: int
    side: str
    price: float
    remaining: float
    episode: int


@dataclass(frozen=True)
class Sample:
    time: float
    token: int
    bid: float
    ask: float
    depth: float
    fair: float | None
    reverse: int
    second: int


@dataclass(frozen=True)
class Fill:
    time: float
    token: int
    side: str
    price: float
    qty: float
    maker: bool


@dataclass
class Lot:
    time: float
    token: int
    episode: int
    price: float
    remaining: float


@dataclass(frozen=True)
class Slice:
    entered: float
    exited: float
    token: int
    episode: int
    entry: float
    exit_net: float
    qty: float


@dataclass(frozen=True)
class Rule:
    name: str
    cents: float
    loss_fraction: float
    age: float
    reversal: bool


RULES = (
    Rule('stop_3c', .03, 0, 0, False),
    Rule('stop_5c', .05, 0, 0, False),
    Rule('stop_8c', .08, 0, 0, False),
    Rule('loss_10pct', 0, .10, 0, False),
    Rule('loss_15pct', 0, .15, 0, False),
    Rule('loss_20pct', 0, .20, 0, False),
    Rule('losing_300s', 0, .05, 300, False),
    Rule('losing_480s', 0, .05, 480, False),
    Rule('reverse_2signals', 0, .05, 0, True),
)


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def fee(price):
    return .05 * price * (1 - price)


def net_exit(price, maker):
    return price + .15 * fee(price) if maker else price - fee(price)


def read_rows(path):
    with path.open() as stream:
        for line in stream:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                if not line.endswith('\n'):
                    break
                raise


def extract_trace(path):
    samples = [[], []]
    receipt = {}
    orders = {}
    books = None
    signal = None
    reverse = [0, 0]
    previous_signal_ns = None
    offset = None
    paused = True
    ended = False
    allow_sell = True
    unconfirmed = False
    second = 0
    resets = 0
    header = None
    for row in read_rows(path):
        kind = row['kind']
        if kind == 'header':
            header = row
            offset = row['opened_wall_s'] - row['opened_now_ns'] / 1e9
            continue
        if kind in ('reset', 'revert'):
            resets += 1
        if kind != 'event':
            continue
        event = row['event']
        event_type = event['type']
        now = row['now_ns'] / 1e9 + offset
        if event_type in ('CancelAck', 'OrderRejected'):
            orders.pop(event['order_id'], None)
        if event_type == 'Fill':
            duplicate = event['fill_id'] in receipt
            receipt.setdefault(event['fill_id'], now)
            order = orders.get(event['order_id'])
            if order and not duplicate:
                order.remaining = max(0, order.remaining - event['qty'])
        for place in row['plan']['places']:
            orders[place['order_id']] = Order(
                place['token_index'], place['side'], place['price'],
                place['quantity'], place['episode_id'],
            )
        if event_type == 'ClockUpdate':
            clock = event['clock']
            second = clock['game_second']
            paused = clock['paused']
            ended = clock['game_ended']
        if event_type == 'PermissionsUpdate':
            allow_sell = event['permissions']['allow_sell']
            unconfirmed = event['permissions']['sell_unconfirmed']
        if event_type == 'BookUpdate':
            books = event['books']
        if event_type == 'SignalUpdate':
            signal = event['signal']
            if signal and signal['received_ns'] != previous_signal_ns:
                previous_signal_ns = signal['received_ns']
                for token in (0, 1):
                    direction = 1 if token == header['limits']['radiant_token_index'] else -1
                    adverse = signal['predicted_delta'] * direction <= -.01
                    reverse[token] = reverse[token] + 1 if adverse else 0
        if event_type not in ('BookUpdate', 'SignalUpdate') or not books:
            continue
        if paused or ended or not allow_sell or unconfirmed:
            continue
        tokens = books['tokens']
        if any((row['now_ns'] - book['ts_ns']) / 1e9 > 5 for book in tokens):
            continue
        if any(not (0 < book['bid'] < book['ask'] < 1) for book in tokens):
            continue
        pair_mid = sum((book['bid'] + book['ask']) / 2 for book in tokens)
        if abs(pair_mid - 1) > .05:
            continue
        for book in tokens:
            token = book['token_index']
            own = sum(order.remaining for order in orders.values()
                      if order.token == token and order.side == 'BUY'
                      and abs(order.price - book['bid']) < 1e-6)
            fair = None
            fresh = signal and (row['now_ns'] - signal['received_ns']) / 1e9 <= 45
            if fresh:
                fair = min(.99, max(.01, signal['anchor_p'] + signal['predicted_delta']))
                if token != header['limits']['radiant_token_index']:
                    fair = 1 - fair
            samples[token].append(Sample(
                now, token, book['bid'], book['ask'], max(0, book['bid_size'] - own),
                fair, reverse[token] if fresh else 0, second,
            ))
    return header, samples, receipt, resets


def assemble_slices(fills):
    queues = [deque(), deque()]
    slices = []
    episode = [0, 0]
    issues = []
    for fill in sorted(fills, key=lambda value: value.time):
        queue = queues[fill.token]
        if fill.side == 'BUY':
            if sum(lot.remaining for lot in queue) < 5:
                episode[fill.token] += 1
            queue.append(Lot(fill.time, fill.token, episode[fill.token], fill.price, fill.qty))
            continue
        remaining = fill.qty
        while remaining > 1e-8 and queue:
            lot = queue[0]
            used = min(lot.remaining, remaining)
            slices.append(Slice(lot.time, fill.time, lot.token, lot.episode,
                                lot.price, net_exit(fill.price, fill.maker), used))
            lot.remaining -= used
            remaining -= used
            if lot.remaining < 1e-8:
                queue.popleft()
        if remaining > .05:
            issues.append(f'unmatched_sell:{remaining:.4f}')
    leftovers = [lot for queue in queues for lot in queue if lot.remaining > 1e-8]
    return slices, leftovers, issues


def trigger(rule, sample, basis, age):
    loss = basis - (sample.bid - fee(sample.bid))
    if age < max(10, rule.age):
        return False
    if rule.cents and basis - sample.bid < rule.cents - 1e-9:
        return False
    if rule.loss_fraction and loss / basis < rule.loss_fraction - 1e-9:
        return False
    if rule.reversal and sample.reverse < 2:
        return False
    return True


def screen_episode(parts, samples, rule, latency):
    entered = min(part.entered for part in parts)
    exited = max(part.exited for part in parts)
    times = [sample.time for sample in samples]
    begin = bisect.bisect_left(times, entered + 10)
    end = bisect.bisect_left(times, exited)
    for index in range(begin, end):
        sample = samples[index]
        active = [part for part in parts if part.entered <= sample.time < part.exited]
        quantity = sum(part.qty for part in active)
        if quantity < 5:
            continue
        basis = sum(part.qty * part.entry for part in active) / quantity
        if not trigger(rule, sample, basis, sample.time - entered):
            continue
        execution_index = bisect.bisect_left(times, sample.time + latency)
        if execution_index >= len(samples):
            return None
        execution = samples[execution_index]
        if execution.time - sample.time > latency + 5:
            return None
        active = [part for part in active if part.exited > execution.time]
        quantity = sum(part.qty for part in active)
        if quantity < 5:
            return None
        price_net = execution.bid - fee(execution.bid)
        depth_qty = min(quantity, execution.depth)
        if depth_qty < 5:
            depth_qty = 0
        delta_full = sum(part.qty * (price_net - part.exit_net) for part in active)
        available = depth_qty
        delta_depth = 0
        for part in sorted(active, key=lambda part: part.entered):
            used = min(available, part.qty)
            delta_depth += used * (price_net - part.exit_net)
            available -= used
        return {
            'trigger_utc': datetime.fromtimestamp(sample.time, timezone.utc).isoformat(),
            'execution_epoch': execution.time, 'hold_s': round(sample.time - entered, 2),
            'basis': basis, 'trigger_bid': sample.bid, 'execution_bid': execution.bid,
            'qty': quantity, 'top_external_qty': execution.depth, 'depth_qty': depth_qty,
            'delta_all_at_bid': delta_full, 'delta_top_only': delta_depth,
            'exit_pnl_all': sum(part.qty * (price_net - part.entry) for part in active),
            'actual_pnl_replaced': sum(part.qty * (part.exit_net - part.entry) for part in active),
        }
    return None


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tape', type=Path, required=True)
    parser.add_argument('--local-traces', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cache_root = args.tape.parent / 'screen-cache'
    cache_root.mkdir(exist_ok=True)
    audit = []
    results = []
    episodes = []
    for meta_path in sorted(args.tape.glob('*/match.json')):
        meta = json.loads(meta_path.read_text())
        if meta.get('joined_at_utc', '') < '2026-09-07':
            continue
        match_id = meta_path.parent.name
        session = meta_path.with_name('session.jsonl')
        if not session.exists():
            continue
        rows = list(read_rows(session))
        start = next((row for row in rows if row.get('kind') == 'session_start'), {})
        raw_fills = [row for row in rows if row.get('kind') == 'fill']
        if start.get('execution_mode') != 'live' or not raw_fills:
            continue
        trace = meta_path.with_name('core_trace.jsonl')
        if not trace.exists():
            trace = args.local_traces / match_id / 'core_trace.jsonl'
        game = meta.get('game', 'dota')
        info = {'match': match_id, 'game': game, 'joined': meta['joined_at_utc'],
                'teams': ' vs '.join(meta['teams'].values()), 'status': '',
                'fills': len(raw_fills), 'episodes': 0, 'excluded_open_episodes': 0,
                'buy_usdc': 0.0, 'gross_cash_error': 0.0, 'model': meta['model']['name']}
        if not trace.exists():
            info['status'] = 'missing_trace'
            audit.append(info)
            continue
        cache = cache_root / f'{match_id}.json'
        stamp = [2, trace.stat().st_size, trace.stat().st_mtime_ns]
        saved = json.loads(cache.read_text()) if cache.exists() else None
        if saved and saved['stamp'] == stamp:
            header = saved['header']
            samples = [[Sample(**row) for row in token] for token in saved['samples']]
            receipt = saved['receipt']
            resets = saved['resets']
        else:
            header, samples, receipt, resets = extract_trace(trace)
            cache.write_text(json.dumps({'stamp': stamp, 'header': header,
                'samples': [[asdict(row) for row in token] for token in samples],
                'receipt': receipt, 'resets': resets}))
        if resets:
            info['status'] = f'trace_resets:{resets}'
            audit.append(info)
            continue
        tokens = [meta['market']['yes_token_id'], meta['market']['no_token_id']]
        seen = set()
        fills = []
        missing = 0
        for row in raw_fills:
            key = row['fill_key']
            if key in seen:
                continue
            seen.add(key)
            if key not in receipt and row['size'] >= 5:
                missing += 1
            fills.append(Fill(receipt.get(key, epoch(row['ts_utc'])), tokens.index(row['token_id']),
                              row['side'], row['price'], row['size'], row['is_maker']))
        info['buy_usdc'] = sum(fill.qty * fill.price for fill in fills if fill.side == 'BUY')
        gross = sum(fill.qty * fill.price * (1 if fill.side == 'SELL' else -1) for fill in fills)
        info['gross_cash_error'] = gross - raw_fills[-1]['net_cash']
        slices, leftovers, issues = assemble_slices(fills)
        if missing or issues or abs(info['gross_cash_error']) > .10:
            info['status'] = f'unreconciled:missing_receipts={missing};{issues}'
            audit.append(info)
            continue
        excluded = {(lot.token, lot.episode) for lot in leftovers if lot.remaining > .05}
        groups = defaultdict(list)
        for part in slices:
            groups[(part.token, part.episode)].append(part)
        info['excluded_open_episodes'] = len(excluded)
        for identity, parts in groups.items():
            if identity in excluded:
                continue
            token, episode = identity
            bought = sum(part.qty for part in parts)
            if bought < 5:
                continue
            spend = sum(part.entry * part.qty for part in parts)
            actual_net = sum(part.qty * (part.exit_net - part.entry + .15 * fee(part.entry))
                             for part in parts)
            held = sum(part.qty * (part.exited - part.entered) for part in parts) / bought
            episodes.append({'match': match_id, 'game': game, 'joined': meta['joined_at_utc'],
                             'token': token, 'episode': episode, 'buy_usdc': spend,
                             'qty': bought, 'net_estimate': actual_net, 'hold_weighted_s': held})
            info['episodes'] += 1
            for rule in RULES:
                for latency in (2, 5):
                    outcome = screen_episode(parts, samples[token], rule, latency)
                    if outcome is not None:
                        results.append({'match': match_id, 'game': game,
                            'joined': meta['joined_at_utc'], 'token': token, 'episode': episode,
                            'rule': rule.name, 'latency_s': latency, 'episode_buy_usdc': spend,
                            'episode_actual_net': actual_net, **outcome})
        info['status'] = 'included'
        audit.append(info)
        print(f'{match_id} {game}: {info["episodes"]} closed episodes', flush=True)
    summary = []
    for game in ('dota', 'lol'):
        base = [row for row in episodes if row['game'] == game]
        for rule in RULES:
            for latency in (2, 5):
                selected = [row for row in results if row['game'] == game
                            and row['rule'] == rule.name and row['latency_s'] == latency]
                summary.append({'game': game, 'rule': rule.name, 'latency_s': latency,
                    'eligible_episodes': len(base), 'triggered': len(selected),
                    'baseline_closed_net_estimate': sum(row['net_estimate'] for row in base),
                    'delta_all_at_bid': sum(row['delta_all_at_bid'] for row in selected),
                    'delta_top_only': sum(row['delta_top_only'] for row in selected),
                    'full_top_depth': sum(row['depth_qty'] >= row['qty'] - 1e-8 for row in selected),
                    'top_qty_fraction': sum(row['depth_qty'] for row in selected) /
                        max(1, sum(row['qty'] for row in selected)),
                    'improved_full': sum(row['delta_all_at_bid'] > 0 for row in selected),
                    'worsened_full': sum(row['delta_all_at_bid'] < 0 for row in selected),
                })
    write_csv(args.output / 'audit.csv', audit)
    write_csv(args.output / 'episodes.csv', episodes)
    write_csv(args.output / 'triggers.csv', results)
    write_csv(args.output / 'summary.csv', summary)
    print('AUDIT', Counter((row['game'], row['status']) for row in audit))
    print(f'Wrote {len(episodes)} episodes and {len(results)} candidate exits.')


if __name__ == '__main__':
    main()
