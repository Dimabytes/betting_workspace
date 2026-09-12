"""Optimistic liquidation diagnostics on unchanged baseline fills, not engine replay."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from market_data.build_market_data import market_seconds_cache_path
from shared.constants.lol import LOL_BACKTEST_MARKET_SECONDS_PATH


OUTPUT = Path(__file__).parent
THRESHOLDS = (25.0, 50.0, 75.0)


@dataclass(frozen=True)
class MarketPath:
    timestamps: np.ndarray
    prices: np.ndarray
    seconds: np.ndarray
    event_id: str


def read_market_paths(game: str, match_ids: list[int]) -> dict[int, MarketPath]:
    columns = ['match_id', 'event_id', 'second', 'state_ts_us', 'market_status', 'market_p_radiant']
    if game == 'lol':
        all_rows = pd.read_parquet(LOL_BACKTEST_MARKET_SECONDS_PATH, columns=columns)
        groups = all_rows.loc[all_rows.match_id.isin(match_ids)].groupby('match_id')
    else:
        groups = [(mid, pd.read_parquet(market_seconds_cache_path(mid), columns=columns)) for mid in match_ids]
    paths = {}
    for mid, rows in groups:
        usable = rows.loc[rows.market_status.eq('ok') & rows.market_p_radiant.notna()]
        usable = usable.sort_values('state_ts_us').drop_duplicates('state_ts_us', keep='last')
        paths[int(mid)] = MarketPath(
            timestamps=usable.state_ts_us.to_numpy(dtype=np.int64) * 1000,
            prices=usable.market_p_radiant.to_numpy(),
            seconds=usable.second.to_numpy(),
            event_id=str(rows.event_id.iloc[0]),
        )
    return paths


def find_radiant_token(fills: pd.DataFrame, path: MarketPath) -> int:
    valid = fills.loc[fills.reference_source_30s.eq('mid')]
    indices = np.searchsorted(path.timestamps, valid.ts_ns.to_numpy() + 30_000_000_000, side='right') - 1
    assert (indices >= 0).all()
    reference_radiant = path.prices[indices]
    error_same = (valid.reference_30s - reference_radiant).abs()
    error_opposite = (valid.reference_30s - (1 - reference_radiant)).abs()
    informative = (error_same - error_opposite).abs().gt(.001)
    inferred = np.where(error_same < error_opposite, valid.token_index, 1 - valid.token_index)
    tokens = np.unique(inferred[informative])
    assert len(tokens) == 1, tokens
    assert np.minimum(error_same, error_opposite).max() < 1e-8
    return int(tokens[0])


def calculate_cvar(values: pd.Series) -> float:
    return float(values.nsmallest(int(np.ceil(len(values) * .05))).mean())


def summarize_rule(rows: pd.DataFrame, threshold: float) -> dict:
    selected = rows.loc[rows.threshold.eq(threshold)].copy()
    selected['delta_mid'] = selected.candidate_mid_net - selected.net
    selected['delta_cost'] = selected.candidate_cost_net - selected.net
    selected['candidate_cost_engine'] = selected.candidate_mid_engine + selected.candidate_cost_net - selected.candidate_mid_net
    triggered = selected.loc[selected.triggered]
    cluster_deltas = selected.groupby('match_id').delta_cost.mean()
    event_by_map = selected.drop_duplicates('match_id').set_index('match_id').event_id
    clusters = cluster_deltas.groupby(event_by_map).sum().to_numpy()
    rng = np.random.default_rng(20260910)
    boot = clusters[rng.integers(0, len(clusters), size=(2000, len(clusters)))].sum(axis=1)
    per_seed = selected.groupby('seed').agg(net=('candidate_mid_net', 'sum'), net_cost=('candidate_cost_net', 'sum'))
    cvars = selected.groupby('seed').candidate_mid_engine.apply(calculate_cvar)
    meta = selected.drop_duplicates('match_id').sort_values('horn_ns').match_id.to_numpy()
    time_deltas = []
    for ids in np.array_split(meta, 3):
        time_deltas.append(float(selected.loc[selected.match_id.isin(ids)].delta_cost.sum() / 12))
    return {
        'threshold': threshold,
        'mean_triggered_maps': len(triggered) / 12,
        'mean_triggered_winners': int(triggered.net.gt(0).sum()) / 12,
        'mean_triggered_best_maps': float(triggered.best_map.sum() / 12),
        'mean_triggered_tail_maps': float(triggered.tail_map.sum() / 12),
        'mean_net_mid': float(per_seed.net.mean()),
        'mean_delta_mid': float(selected.delta_mid.sum() / 12),
        'mean_net_with_cost': float(per_seed.net_cost.mean()),
        'mean_delta_with_cost': float(selected.delta_cost.sum() / 12),
        'mean_cvar_mid_pre_rebate': float(cvars.mean()),
        'mean_cvar_cost_pre_rebate': float(selected.groupby('seed').candidate_cost_engine.apply(calculate_cvar).mean()),
        'mean_worst_mid_pre_rebate': float(selected.groupby('seed').candidate_mid_engine.min().mean()),
        'mean_worst_cost_pre_rebate': float(selected.groupby('seed').candidate_cost_engine.min().mean()),
        'largest_stop_overshoot': float((-triggered.stop_engine - threshold).max()),
        'median_trigger_elapsed': float(triggered.stop_elapsed.median()),
        'fraction_trigger_after_sample_gap_gt5s': float(triggered.stop_sample_gap.gt(5).mean()),
        'mean_forgone_winner_net': float(-triggered.loc[triggered.net.gt(0)].delta_cost.sum() / 12),
        'mean_effect_on_baseline_losers': float(triggered.loc[triggered.net.le(0)].delta_cost.sum() / 12),
        'chronological_third_deltas_with_cost': time_deltas,
        'event_cluster_bootstrap_delta_ci': np.percentile(boot, [2.5, 97.5]).tolist(),
        'seed_deltas_with_cost': selected.groupby('seed').delta_cost.sum().tolist(),
    }


def analyze_game(game: str) -> dict:
    maps = pd.read_parquet(OUTPUT / f'{game}_map_features.parquet')
    fills = pd.read_parquet(OUTPUT / f'{game}_fills.parquet')
    paths = read_market_paths(game, maps.match_id.unique().tolist())
    fill_groups = fills.groupby(['seed', 'match_id']).indices
    rows = []
    reference_error = 0.0
    first_seconds = []
    for row in maps.itertuples():
        key = (row.seed, row.match_id)
        path = paths[row.match_id]
        base = {
            'seed': row.seed, 'match_id': row.match_id, 'event_id': path.event_id,
            'horn_ns': row.horn_ns, 'net': row.net, 'engine_pnl': row.engine_pnl,
            'tail_map': row.tail_map, 'best_map': row.best_map,
        }
        if key not in fill_groups:
            for threshold in THRESHOLDS:
                rows.append({**base, 'threshold': threshold, 'triggered': False,
                             'candidate_mid_net': row.net, 'candidate_cost_net': row.net,
                             'candidate_mid_engine': row.engine_pnl})
            continue
        events = fills.iloc[fill_groups[key]].sort_values('ts_ns', kind='stable')
        radiant_token = find_radiant_token(events, path)
        event_ns = events.ts_ns.to_numpy()
        first_buy = events.loc[events.side.eq('BUY')].iloc[0]
        horizon_index = np.searchsorted(path.timestamps, first_buy.ts_ns + 30_000_000_000, side='right') - 1
        if horizon_index >= 0 and first_buy.reference_source_30s == 'mid':
            expected = path.prices[horizon_index] if first_buy.token_index == radiant_token else 1 - path.prices[horizon_index]
            reference_error = max(reference_error, abs(expected - first_buy.reference_30s))
        first_index = np.searchsorted(path.timestamps, first_buy.ts_ns, side='right') - 1
        first_seconds.append({'seed': row.seed, 'match_id': row.match_id,
                              'first_game_second': int(path.seconds[max(0, first_index)])})
        valid = (path.timestamps >= event_ns[0]) & (path.timestamps <= row.end_ns)
        times = path.timestamps[valid]
        prices = path.prices[valid]
        indices = np.searchsorted(event_ns, times, side='right')
        signed_quantity = events.quantity.to_numpy() * np.where(events.side.eq('BUY'), 1, -1)
        cash = np.r_[0.0, np.cumsum(-signed_quantity * events.price.to_numpy())][indices]
        quantity_r = np.r_[0.0, np.cumsum(np.where(events.token_index.eq(radiant_token), signed_quantity, 0))][indices]
        quantity_d = np.r_[0.0, np.cumsum(np.where(events.token_index.ne(radiant_token), signed_quantity, 0))][indices]
        rebates = np.r_[0.0, np.cumsum(events.maker_rebate.to_numpy())][indices]
        mtm = cash + quantity_r * prices + quantity_d * (1 - prices)
        for threshold in THRESHOLDS:
            crossings = np.flatnonzero(mtm <= -threshold)
            if not len(crossings):
                rows.append({**base, 'threshold': threshold, 'triggered': False,
                             'candidate_mid_net': row.net, 'candidate_cost_net': row.net,
                             'candidate_mid_engine': row.engine_pnl})
                continue
            hit = int(crossings[0])
            sale_qty = max(0, quantity_r[hit]) + max(0, quantity_d[hit])
            fee_and_discount = sale_qty * (.01 + .05 * prices[hit] * (1 - prices[hit]))
            stop_net = mtm[hit] + rebates[hit]
            rows.append({**base, 'threshold': threshold, 'triggered': True,
                         'candidate_mid_engine': mtm[hit], 'candidate_mid_net': stop_net,
                         'candidate_cost_net': stop_net - fee_and_discount,
                         'stop_engine': mtm[hit], 'stop_qty': sale_qty,
                         'stop_elapsed': (times[hit] - event_ns[0]) / 1e9,
                         'stop_sample_gap': (times[hit] - times[hit - 1]) / 1e9 if hit > 0 else 0.0,
                         'min_mtm': float(mtm.min())})
    assert reference_error < 1e-8, reference_error
    frame = pd.DataFrame(rows)
    frame.to_parquet(OUTPUT / f'{game}_stop_diagnostics.parquet', index=False)
    pd.DataFrame(first_seconds).to_parquet(OUTPUT / f'{game}_first_seconds.parquet', index=False)
    return {'reference_error': reference_error,
            'note': 'Same baseline fills until trigger, instant complete liquidation at paired mid; no future trades. Cost scenario deducts 1 cent/share plus archived taker fee. Neither is executable backtest.',
            'rules': [summarize_rule(frame, threshold) for threshold in THRESHOLDS]}


def main() -> None:
    report = {}
    for game in ['dota', 'lol']:
        report[game] = analyze_game(game)
        print(game, json.dumps(report[game]), flush=True)
    (OUTPUT / 'stop_diagnostics.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
