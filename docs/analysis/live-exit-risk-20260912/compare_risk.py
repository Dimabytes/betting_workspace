"""Summarize the fixed-entry screen, including tails and liquidity sensitivity."""

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path


def read_csv(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def calculate_tail(values):
    ordered = sorted(values)
    count = max(1, math.ceil(.05 * len(values)))
    return sum(ordered[:count]) / count


def write_csv(path, rows):
    with path.open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    episodes = read_csv(args.root / 'episodes.csv')
    triggers = read_csv(args.root / 'triggers.csv')
    audit = {row['match']: row for row in read_csv(args.root / 'audit.csv')}
    rules = list(dict.fromkeys(row['rule'] for row in triggers))
    output = []
    days = []
    for game in ('dota', 'lol'):
        baseline = defaultdict(float)
        for row in episodes:
            if row['game'] == game:
                baseline[row['match']] += float(row['net_estimate'])
        values = list(baseline.values())
        print(game, 'maps', len(values), 'baseline', round(sum(values), 2),
              'worst', round(min(values), 2), 'tail', round(calculate_tail(values), 2))
        for latency in (2, 5):
            for rule in rules:
                selected = [row for row in triggers if row['game'] == game
                            and row['rule'] == rule and int(row['latency_s']) == latency]
                for mode in ('all_at_bid', 'top_only'):
                    candidate = dict(baseline)
                    stress = 0
                    for row in selected:
                        delta = float(row[f'delta_{mode}'])
                        candidate[row['match']] += delta
                        qty = float(row['qty' if mode == 'all_at_bid' else 'depth_qty'])
                        stress += qty * .01
                    delta = sum(candidate.values()) - sum(values)
                    output.append({'game': game, 'rule': rule, 'latency_s': latency,
                        'mode': mode, 'maps': len(values), 'triggered': len(selected),
                        'baseline_net': sum(values), 'delta_net': delta,
                        'candidate_net': sum(candidate.values()), 'baseline_worst': min(values),
                        'candidate_worst': min(candidate.values()),
                        'baseline_tail5': calculate_tail(values),
                        'candidate_tail5': calculate_tail(list(candidate.values())),
                        'delta_with_extra_1c': delta - stress})
                    for day in sorted({row['joined'][:10] for row in episodes if row['game'] == game}):
                        matches = [key for key in baseline if audit[key]['joined'][:10] == day]
                        days.append({'game': game, 'rule': rule, 'latency_s': latency,
                                     'mode': mode, 'day': day,
                                     'delta': sum(candidate[key] - baseline[key] for key in matches)})
    write_csv(args.root / 'risk.csv', output)
    write_csv(args.root / 'by_day.csv', days)
    for row in output:
        if row['mode'] == 'top_only' and row['latency_s'] == 2:
            print(row['game'], row['rule'], 'delta', round(row['delta_net'], 2),
                  'tail', round(row['candidate_tail5'], 2), 'worst', round(row['candidate_worst'], 2),
                  '1c stress', round(row['delta_with_extra_1c'], 2))


if __name__ == '__main__':
    main()
