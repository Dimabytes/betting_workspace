"""Read-only diagnosis of the frozen Dota/LoL LIVE backtest catalogs."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT = Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
OUTPUT = Path(__file__).parent


@dataclass
class Inventory:
    quantity: float = 0.0
    cost: float = 0.0
    opened_ns: int = 0


@dataclass(frozen=True)
class Catalog:
    game: str
    path: Path
    maps: pd.DataFrame
    fills: pd.DataFrame


def read_catalog(game: str) -> Catalog:
    path = (PROJECT / 'data/backtests' / f'{game}_maker' / 'LIVE').resolve()
    maps = []
    fill_frames = []
    for seed in range(12):
        folder = path / f'seed{seed}'
        results = pd.read_parquet(folder / 'results.parquet')
        results = results.loc[~results.terminated_early].copy()
        assert results.match_id.is_unique
        fills = pd.read_parquet(folder / 'fills.parquet')
        fills = fills.loc[fills.match_id.isin(results.match_id)].copy()
        fills['seed'] = seed
        fills['notional'] = fills.price * fills.quantity
        fills['game'] = game
        results['rebate'] = results.match_id.map(fills.groupby('match_id').maker_rebate.sum()).fillna(0)
        results['net'] = results.engine_pnl + results.rebate
        results['seed'] = seed
        results['game'] = game
        results['horn_ns'] = pd.to_datetime(results.horn_at, format='mixed').astype('int64')
        results['end_ns'] = pd.to_datetime(results.game_ended_at, format='mixed').astype('int64')
        summary = json.loads((folder / 'summary.json').read_text())['arms'][0]
        assert abs(results.net.sum() - summary['net_pnl']) < 1e-5
        results['reported_cvar'] = summary['cvar_5']
        results['reported_worst'] = summary['worst_match']
        features = []
        for match_id, events in fills.groupby('match_id', sort=False):
            events = events.sort_values('ts_ns', kind='stable')
            buys = events.loc[events.side.eq('BUY')]
            sells = events.loc[events.side.eq('SELL')]
            if buys.empty:
                continue
            positions = {0: Inventory(), 1: Inventory()}
            peak_cost = 0.0
            realized = 0.0
            longest_closed_hold = 0.0
            cost_error = 0.0
            for fill in events.itertuples():
                position = positions[fill.token_index]
                if fill.side == 'BUY':
                    if position.quantity < 1e-6:
                        position.opened_ns = fill.ts_ns
                    position.quantity += fill.quantity
                    position.cost += fill.notional
                else:
                    assert fill.quantity <= position.quantity + 1e-5
                    sold_cost = position.cost * min(1.0, fill.quantity / position.quantity)
                    realized += fill.notional - sold_cost
                    position.quantity = max(0.0, position.quantity - fill.quantity)
                    position.cost -= sold_cost
                    longest_closed_hold = max(longest_closed_hold, (fill.ts_ns - position.opened_ns) / 1e9)
                peak_cost = max(peak_cost, sum(item.cost for item in positions.values()))
                cost_error = max(cost_error, abs(position.cost - fill.position_cost_basis))
            remaining_cost = sum(item.cost for item in positions.values())
            first = buys.iloc[0]
            first_episode = buys.loc[buys.episode_id.eq(first.episode_id)]
            first_side = int(first.token_index)
            opposite = buys.token_index.ne(first_side)
            low = buys.price.lt(0.50)
            features.append({
                'match_id': match_id,
                'first_price': first.price,
                'first_ns': int(first.ts_ns),
                'first_token': first_side,
                'first_abs_delta': abs(first.predicted_delta),
                'first_fair_gap': first.fair_at_fill - first.price,
                'first_nw': abs(first.nw_delta_30),
                'first_spread': first.spread,
                'first_age': first.signal_age_seconds,
                'first_episode_cost': first_episode.notional.sum(),
                'buy_turnover': buys.notional.sum(),
                'sell_turnover': sells.notional.sum(),
                'peak_cost': peak_cost,
                'realized': realized,
                'remaining_cost': remaining_cost,
                'remaining_qty': sum(item.quantity for item in positions.values()),
                'buy_orders': buys.order_id.nunique(),
                'episodes': buys.episode_id.nunique(),
                'both_sides': buys.token_index.nunique() > 1,
                'opposite_buy_notional': buys.loc[opposite].notional.sum(),
                'low_buy_notional': buys.loc[low].notional.sum(),
                'buy_markout_30': (buys.markout_30s * buys.quantity).sum() / buys.quantity.sum(),
                'buy_markout_300': (buys.markout_300s * buys.quantity).sum() / buys.quantity.sum(),
                'longest_closed_hold': longest_closed_hold,
                'cost_reconstruction_error': cost_error,
            })
        results = results.merge(pd.DataFrame(features), on='match_id', how='left')
        results['both_sides'] = results.both_sides.astype(float)
        zero_columns = ['realized', 'remaining_cost', 'remaining_qty', 'buy_turnover',
                        'sell_turnover', 'peak_cost', 'buy_orders', 'episodes', 'both_sides',
                        'opposite_buy_notional', 'low_buy_notional', 'cost_reconstruction_error']
        results[zero_columns] = results[zero_columns].fillna(0)
        results['first_elapsed'] = (results.first_ns - results.horn_ns) / 1e9
        results['terminal_pnl'] = results.engine_pnl - results.realized.fillna(0)
        check = results.realized.fillna(0) - results.remaining_cost.fillna(0) - results.cash_flow
        assert check.abs().max() < 1e-5
        maps.append(results)
        fill_frames.append(fills)
    return Catalog(game, path, pd.concat(maps, ignore_index=True), pd.concat(fill_frames, ignore_index=True))


def summarize_group(frame: pd.DataFrame) -> dict[str, float]:
    metrics = ['net', 'engine_pnl', 'realized', 'terminal_pnl', 'remaining_cost',
               'first_price', 'first_elapsed', 'first_abs_delta', 'first_fair_gap',
               'first_nw', 'first_spread', 'first_age', 'first_episode_cost',
               'buy_turnover', 'peak_cost', 'buy_orders', 'episodes', 'both_sides',
               'buy_markout_30', 'buy_markout_300', 'longest_closed_hold']
    summary = {name: float(frame[name].mean()) for name in metrics}
    summary['maps'] = int(frame.match_id.nunique())
    summary['material_remaining_fraction'] = float(frame.remaining_cost.ge(5).mean())
    summary['first_below050_fraction'] = float(frame.first_price.lt(.5).mean())
    summary['first_below045_fraction'] = float(frame.first_price.lt(.45).mean())
    return summary


def build_analysis(catalog: Catalog) -> dict:
    maps = catalog.maps
    averaged = maps.groupby('match_id').mean(numeric_only=True)
    metadata = maps.drop_duplicates('match_id').set_index('match_id')
    averaged['slug'] = metadata.slug
    averaged['horn_at'] = metadata.horn_at
    count = int(np.ceil(len(averaged) * .05))
    tail_ids = averaged.nsmallest(count, 'engine_pnl').index
    best_ids = averaged.nlargest(count, 'engine_pnl').index
    maps['tail_map'] = maps.match_id.isin(tail_ids)
    maps['best_map'] = maps.match_id.isin(best_ids)
    seed_totals = maps.groupby('seed')[['engine_pnl', 'rebate', 'net']].sum()
    overview = {
        'path': str(catalog.path),
        'maps': len(averaged),
        'seeds': len(seed_totals),
        'mean_engine': float(seed_totals.engine_pnl.mean()),
        'mean_rebate': float(seed_totals.rebate.mean()),
        'mean_net': float(seed_totals.net.mean()),
        'seed_net_min': float(seed_totals.net.min()),
        'seed_net_max': float(seed_totals.net.max()),
        'mean_reported_cvar_pre_rebate': float(maps.groupby('seed').reported_cvar.first().mean()),
        'mean_reported_worst_pre_rebate': float(maps.groupby('seed').reported_worst.first().mean()),
        'worst_individual_pre_rebate': float(maps.engine_pnl.min()),
        'tail_count': count,
        'tail_net_sum': float(averaged.loc[tail_ids].net.sum()),
        'best_net_sum': float(averaged.loc[best_ids].net.sum()),
        'tail_realized': float(averaged.loc[tail_ids].realized.sum()),
        'tail_terminal': float(averaged.loc[tail_ids].terminal_pnl.sum()),
        'largest_cost_reconstruction_error': float(maps.cost_reconstruction_error.max()),
    }
    profiles = {
        'tail': summarize_group(maps.loc[maps.tail_map]),
        'best': summarize_group(maps.loc[maps.best_map]),
        'other_positive': summarize_group(maps.loc[~maps.best_map & maps.net.gt(0)]),
        'other_negative': summarize_group(maps.loc[~maps.tail_map & maps.net.lt(0)]),
    }
    bucket_rows = []
    for feature, bins in [
        ('first_price', [0, .45, .50, .60, .75, 1.01]),
        ('first_elapsed', [-1e6, 180, 300, 420, 540, 1e6]),
        ('first_abs_delta', [0, .02, .04, .08, 1]),
        ('first_fair_gap', [-1, 0, .01, .03, .06, 1]),
        ('peak_cost', [0, 100.1, 200.1, 300.1, 500, 1e6]),
        ('buy_turnover', [0, 100.1, 300.1, 600.1, 1e6]),
        ('episodes', [0, 1, 2, 3, 1000]),
    ]:
        buckets = pd.cut(maps[feature], bins, right=True)
        for label, group in maps.groupby(buckets, observed=True):
            bucket_rows.append({
                'feature': feature, 'bucket': str(label),
                'mean_maps': len(group) / 12,
                'mean_net': float(group.net.sum() / 12),
                'mean_engine': float(group.engine_pnl.sum() / 12),
                'mean_tail_maps': float(group.tail_map.sum() / 12),
                'mean_best_maps': float(group.best_map.sum() / 12),
                'net_per_map': float(group.net.mean()),
            })
    columns = ['slug', 'horn_at', 'net', 'engine_pnl', 'realized', 'terminal_pnl',
               'remaining_cost', 'first_price', 'first_elapsed', 'peak_cost',
               'buy_turnover', 'buy_orders', 'episodes', 'both_sides',
               'first_abs_delta', 'first_fair_gap', 'first_nw', 'longest_closed_hold']
    top = averaged.nsmallest(15, 'engine_pnl')[columns].reset_index().to_dict(orient='records')
    maps.to_parquet(OUTPUT / f'{catalog.game}_map_features.parquet', index=False)
    catalog.fills.to_parquet(OUTPUT / f'{catalog.game}_fills.parquet', index=False)
    return {'overview': overview, 'profiles': profiles, 'buckets': bucket_rows, 'worst_maps': top}


def main() -> None:
    report = {}
    for game in ['dota', 'lol']:
        catalog = read_catalog(game)
        report[game] = build_analysis(catalog)
        print(game, json.dumps(report[game]['overview']), flush=True)
        print('profiles', json.dumps(report[game]['profiles']), flush=True)
        print('worst', json.dumps(report[game]['worst_maps'][:8]), flush=True)
    (OUTPUT / 'analysis.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
