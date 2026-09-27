from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from shared.constants.dataset import TRAIN_LAG_SECONDS
from shared.utils.gbm import (
    FEATURE_COLUMNS,
    NO_XP_FEATURE_COLUMNS,
    GbmPredictor,
    _ensemble_member_rng,
    build_price_delta_labels,
    fit_ensemble_member_booster,
    fit_ensemble_member_fixed_trees,
    load_predictor,
)
from trader.game_profile import GAME_PROFILES
from trader.live_feed import GameSnapshot, MatchPhase
from trader.model_server import load_model
from shared.utils.top_players import TopPlayerFeatures

E = Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
DATA = E / 'data/new_processed/dataset'
MODEL_DIR = E / 'data/new_model'
OUT = Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/train-luna/model_audit.json')

VAL_COLS = list(dict.fromkeys([
    'match_id', 'event_id', 'start_time', 'second', 'market_status',
    'market_p_radiant', 'signal_market_p_radiant_300s', *FEATURE_COLUMNS[1:],
]))


def summarize(actual: np.ndarray, current: np.ndarray, fair: np.ndarray) -> dict[str, float | int]:
    actual = np.asarray(actual, dtype=np.float64)
    current = np.asarray(current, dtype=np.float64)
    fair = np.asarray(fair, dtype=np.float64)
    predicted_delta = fair - current
    realized_delta = actual - current
    direction = np.where(predicted_delta >= 0.0, 1.0, -1.0)
    baseline_error = np.abs(realized_delta)
    model_error = np.abs(fair - actual)
    return {
        'n': len(actual),
        'mae_gain_cents': float((baseline_error - model_error).mean() * 100),
        'model_mae_cents': float(model_error.mean() * 100),
        'baseline_mae_cents': float(baseline_error.mean() * 100),
        'bias_cents': float((fair - actual).mean() * 100),
        'mean_predicted_delta_cents': float(predicted_delta.mean() * 100),
        'mean_realized_delta_cents': float(realized_delta.mean() * 100),
        'directional_markout_cents': float((direction * realized_delta).mean() * 100),
        'direction_accuracy_pct': float((np.sign(predicted_delta) == np.sign(realized_delta)).mean() * 100),
    }


def event_bootstrap_gain(frame: pd.DataFrame, fair: np.ndarray, repeats: int = 2000) -> tuple[float, float]:
    actual = frame['signal_market_p_radiant_300s'].to_numpy(dtype=np.float64)
    current = frame['market_p_radiant'].to_numpy(dtype=np.float64)
    values = np.abs(actual - current) - np.abs(actual - fair)
    grouped = pd.DataFrame({'event_id': frame['event_id'].to_numpy(), 'gain': values})
    totals = grouped.groupby('event_id', sort=False)['gain'].agg(['sum', 'count'])
    numerators = totals['sum'].to_numpy(dtype=np.float64)
    counts = totals['count'].to_numpy(dtype=np.float64)
    rng = np.random.default_rng(20260810)
    chosen = rng.integers(0, len(totals), size=(repeats, len(totals)))
    estimates = numerators[chosen].sum(axis=1) / counts[chosen].sum(axis=1)
    low, high = np.percentile(estimates, [2.5, 97.5])
    return float(low * 100), float(high * 100)


def event_bootstrap_mean(frame: pd.DataFrame, values: np.ndarray, repeats: int = 2000) -> tuple[float, float]:
    grouped = pd.DataFrame({'event_id': frame['event_id'].to_numpy(), 'value': values})
    totals = grouped.groupby('event_id', sort=False)['value'].agg(['sum', 'count'])
    numerators = totals['sum'].to_numpy(dtype=np.float64)
    counts = totals['count'].to_numpy(dtype=np.float64)
    rng = np.random.default_rng(20260810)
    chosen = rng.integers(0, len(totals), size=(repeats, len(totals)))
    estimates = numerators[chosen].sum(axis=1) / counts[chosen].sum(axis=1)
    low, high = np.percentile(estimates, [2.5, 97.5])
    return float(low * 100), float(high * 100)


def score(label: str, frame: pd.DataFrame, fair: np.ndarray) -> dict[str, Any]:
    actual = frame['signal_market_p_radiant_300s'].to_numpy(dtype=np.float64)
    current = frame['market_p_radiant'].to_numpy(dtype=np.float64)
    result = summarize(actual, current, fair)
    lo, hi = event_bootstrap_gain(frame, fair)
    result['mae_gain_ci_event_cents'] = [lo, hi]
    bias_lo, bias_hi = event_bootstrap_mean(frame, fair - actual)
    result['bias_ci_event_cents'] = [bias_lo, bias_hi]
    result['maps'] = int(frame['match_id'].nunique())
    result['events'] = int(frame['event_id'].nunique())
    print(label, result)
    return result


def server_snapshot(row: pd.Series) -> GameSnapshot:
    top = TopPlayerFeatures(
        top1_nw_adv=int(row['top1_nw_adv']),
        radiant_top1_nw_ratio=float(row['radiant_top1_nw_ratio']),
        dire_top1_nw_ratio=float(row['dire_top1_nw_ratio']),
    )
    return GameSnapshot(
        second=int(row['second']) - TRAIN_LAG_SECONDS,
        server_timestamp=0,
        phase=MatchPhase.IN_PROGRESS,
        radiant_nw_adv=int(row['radiant_nw_adv']),
        radiant_nw=int(row['radiant_nw']),
        dire_nw=int(row['dire_nw']),
        radiant_xp_adv=int(row['radiant_xp_adv']),
        deaths_radiant=int(row['deaths_radiant']),
        deaths_dire=int(row['deaths_dire']),
        top=top,
        paused=False,
    )


def model_server_parity(frame: pd.DataFrame) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    sample = frame.loc[:, VAL_COLS].sample(n=min(512, len(frame)), random_state=80926)
    sample = sample.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURE_COLUMNS + ['market_p_radiant'])
    sample = sample.reset_index(drop=True)
    for name, features in [('research', FEATURE_COLUMNS), ('production', FEATURE_COLUMNS),
                           ('research-noxp', NO_XP_FEATURE_COLUMNS), ('production-noxp', NO_XP_FEATURE_COLUMNS)]:
        predictor = load_predictor(MODEL_DIR / name)
        profile = GAME_PROFILES['dota']
        catalog = profile.primary if 'noxp' not in name else profile.satellites[next(iter(profile.satellites))]
        server = load_model(MODEL_DIR / name, features, TRAIN_LAG_SECONDS)
        feature_frame = sample[features].copy()
        feature_frame['second'] = feature_frame['second'] - TRAIN_LAG_SECONDS
        direct = predictor.predict_one_thread(feature_frame).astype(np.float64)
        server_delta: list[float] = []
        server_fair: list[float] = []
        for _, row in sample.iterrows():
            prediction = server.predict_fair(
                server_snapshot(row),
                float(row['market_p_radiant']),
                float(row['market_radiant_prior']),
            )
            server_delta.append(prediction.raw_delta)
            server_fair.append(prediction.fair)
        server_delta_array = np.asarray(server_delta, dtype=np.float64)
        delta_diff = np.abs(direct - server_delta_array)
        expected_fair = np.clip(sample['market_p_radiant'].to_numpy(dtype=np.float64) + direct, 0.0, 1.0)
        fair_diff = np.abs(expected_fair - np.asarray(server_fair, dtype=np.float64))
        result[name] = {
            'rows': len(sample),
            'max_abs_delta_diff': float(delta_diff.max(initial=0.0)),
            'nonzero_delta_diffs': int(np.count_nonzero(delta_diff)),
            'max_abs_fair_diff': float(fair_diff.max(initial=0.0)),
            'nonzero_fair_diffs': int(np.count_nonzero(fair_diff)),
            'catalog_member_count': len(predictor.member_names),
            'catalog_tree_mean': predictor.num_trees(),
            'meta_lag': server.model_reference.name and catalog.source_lag_seconds,
        }
        print('PARITY', name, result[name])
    return result


def build_honest_refit(train: pd.DataFrame, inner_fraction: float = 0.80) -> tuple[list[Any], list[int], int, int]:
    features = tuple(FEATURE_COLUMNS)
    map_times = train[['match_id', 'start_time']].drop_duplicates('match_id').sort_values(['start_time', 'match_id'])
    ordered_ids = map_times['match_id'].to_numpy(dtype=np.int64)
    split_index = int(len(ordered_ids) * inner_fraction)
    inner_fit_ids = set(ordered_ids[:split_index].tolist())
    inner_stop_ids = set(ordered_ids[split_index:].tolist())
    all_ids = train['match_id'].drop_duplicates().to_numpy(dtype=np.int64)
    inner_train_ids = train.loc[train['match_id'].isin(inner_fit_ids), 'match_id'].drop_duplicates().to_numpy(dtype=np.int64)
    inner_valid = train.loc[train['match_id'].isin(inner_stop_ids)]
    inner_valid_X = inner_valid[list(features)]
    inner_valid_y = build_price_delta_labels(inner_valid)
    selected_trees: list[int] = []
    for member_index in range(10):
        n_keep_inner = round(len(inner_train_ids) * 0.90)
        inner_rng = _ensemble_member_rng(member_index)
        chosen_inner_ids = inner_train_ids[inner_rng.choice(len(inner_train_ids), size=n_keep_inner, replace=False)]
        member_train = train.loc[train['match_id'].isin(chosen_inner_ids)]
        early_stopped = fit_ensemble_member_booster(
            member_train[list(features)],
            build_price_delta_labels(member_train),
            inner_valid_X,
            inner_valid_y,
        )
        trees = int(early_stopped.num_trees())
        selected_trees.append(trees)
        n_keep_all = round(len(all_ids) * 0.90)
        all_rng = _ensemble_member_rng(member_index)
        chosen_all_ids = all_ids[all_rng.choice(len(all_ids), size=n_keep_all, replace=False)]
        full_member_train = train.loc[train['match_id'].isin(chosen_all_ids)]
        final_model = fit_ensemble_member_fixed_trees(
            full_member_train[list(features)],
            build_price_delta_labels(full_member_train),
            trees,
        )
        print('REFIT_MEMBER', member_index, 'inner_stop_trees', trees, 'refit_trees', final_model.num_trees(),
              'inner_fit_maps', len(chosen_inner_ids), 'full_fit_maps', len(chosen_all_ids))
        # Retain one model in memory at a time only during training; append booster for validation prediction.
        # Each booster is small (tree counts around a few dozen).
        selected_boosters.append(final_model)
    return selected_boosters, selected_trees, len(inner_fit_ids), len(inner_stop_ids)


# Defined globally so the helper can stream model objects into evaluation.
selected_boosters: list[Any] = []


def main() -> None:
    train = pd.read_parquet(DATA / 'training_dataset.parquet')
    train = train.loc[
        (train['second'] >= -60)
        & (train['second'] < 600)
        & train['signal_market_p_radiant_300s'].notna()
    ].reset_index(drop=True)
    validation = pd.read_parquet(DATA / 'validation_dataset.parquet', columns=VAL_COLS)
    validation = validation.loc[
        (validation['second'] >= -60)
        & (validation['second'] < 600)
        & (validation['market_status'] == 'ok')
    ].reset_index(drop=True)
    prediction_features = validation[FEATURE_COLUMNS].copy()
    prediction_features['second'] = prediction_features['second'] - TRAIN_LAG_SECONDS
    val_labeled_mask = validation['signal_market_p_radiant_300s'].notna().to_numpy()
    labeled_validation = validation.loc[val_labeled_mask].reset_index(drop=True)
    research = load_predictor(MODEL_DIR / 'research')
    original_fair = np.clip(
        validation['market_p_radiant'].to_numpy(dtype=np.float64)
        + research.predict_one_thread(prediction_features),
        0.0,
        1.0,
    )
    original_labeled_fair = original_fair[val_labeled_mask]
    results: dict[str, Any] = {}
    results['research_full_validation'] = score('RESEARCH_ALL_WINDOW', labeled_validation, original_labeled_fair)

    # Split candidate validation slice by match start date. Research model score there is exposed to ES;
    # the independent refit below uses only a chronological inner split from the training set.
    valid_map_times = validation[['match_id', 'start_time']].drop_duplicates('match_id').sort_values(['start_time', 'match_id'])
    valid_order = valid_map_times['match_id'].to_numpy(dtype=np.int64)
    heldout_ids = set(valid_order[int(len(valid_order) * 0.60):].tolist())
    heldout_validation = labeled_validation.loc[labeled_validation['match_id'].isin(heldout_ids)].reset_index(drop=True)
    heldout_mask = validation['match_id'].isin(heldout_ids).to_numpy() & val_labeled_mask
    original_heldout_fair = original_fair[heldout_mask]
    results['research_last40_validation'] = score('RESEARCH_LAST40_VALIDATION', heldout_validation, original_heldout_fair)

    # Time-based regime slices use market-second buckets; model inputs at those rows are M - 10.
    all_result = []
    for label, mask in [
        ('market_second_0_540', (validation['second'] >= 0) & (validation['second'] < 540)),
        ('market_second_540_900', (validation['second'] >= 540) & (validation['second'] < 900)),
        ('market_second_600_900', (validation['second'] >= 600) & (validation['second'] < 900)),
        ('prehorn_market_second_-60_0', (validation['second'] >= -60) & (validation['second'] < 0)),
        ('large_abs_nw_adv_ge_10000', validation['radiant_nw_adv'].abs() >= 10000),
        ('large_abs_nw_adv_ge_5000', validation['radiant_nw_adv'].abs() >= 5000),
        ('large_abs_nw_adv_ge_3000', validation['radiant_nw_adv'].abs() >= 3000),
        ('radiant_nw_adv_ge_10000', validation['radiant_nw_adv'] >= 10000),
        ('dire_nw_adv_ge_10000', validation['radiant_nw_adv'] <= -10000),
        ('market_p_ge_0.85', validation['market_p_radiant'] >= .85),
        ('market_p_le_0.15', validation['market_p_radiant'] <= .15),
    ]:
        selected = mask.to_numpy() & val_labeled_mask
        subset = validation.loc[selected].reset_index(drop=True)
        subset_fair = original_fair[selected]
        if len(subset):
            all_result.append((label, score(label, subset, subset_fair)))
    results['regimes'] = dict(all_result)

    # Keep a separate full-map validation view through 900 seconds for late fair checks.
    validation_full = pd.read_parquet(DATA / 'validation_dataset.parquet', columns=VAL_COLS)
    validation_full = validation_full.loc[
        (validation_full['second'] >= -60)
        & (validation_full['second'] < 1200)
        & (validation_full['market_status'] == 'ok')
    ].reset_index(drop=True)
    research_late: dict[str, Any] = {}
    production = load_predictor(MODEL_DIR / 'production')
    for label, start, end in [
        ('market_second_540_900', 540, 900),
        ('market_second_540_600', 540, 600),
        ('market_second_600_900', 600, 900),
        ('market_second_900_1200', 900, 1200),
    ]:
        late = validation_full.loc[
            (validation_full['second'] >= start)
            & (validation_full['second'] < end)
            & validation_full['signal_market_p_radiant_300s'].notna()
        ].reset_index(drop=True)
        if not len(late):
            continue
        late_features = late[FEATURE_COLUMNS].copy()
        late_features['second'] = late_features['second'] - TRAIN_LAG_SECONDS
        late_matrix = late_features.to_numpy(dtype=np.float64)
        current = late['market_p_radiant'].to_numpy(dtype=np.float64)
        future = late['signal_market_p_radiant_300s'].to_numpy(dtype=np.float64)
        research_fair = np.clip(current + research.predict_one_thread(late_features), 0.0, 1.0)
        production_fair = np.clip(current + production.predict_one_thread(late_features), 0.0, 1.0)
        research_late[label] = score(f'RESEARCH_{label}', late, research_fair)
        research_late[label]['production'] = score(f'PRODUCTION_{label}', late, production_fair)
        research_late[label]['mean_source_second'] = float((late['second'] - TRAIN_LAG_SECONDS).mean())
    results['late_fair_calibration'] = research_late

    # Calibration deciles for net-worth advantage and market probability.
    for feature in ['radiant_nw_adv', 'market_p_radiant']:
        labeled = validation.loc[val_labeled_mask].copy().reset_index(drop=True)
        labeled['fair'] = original_labeled_fair
        try:
            labeled['bin'] = pd.qcut(labeled[feature], 10, duplicates='drop')
        except ValueError:
            continue
        binned: dict[str, Any] = {}
        for bin_label, group in labeled.groupby('bin', observed=True):
            row_ix = group.index.to_numpy()
            summary = summarize(
                group['signal_market_p_radiant_300s'].to_numpy(dtype=np.float64),
                group['market_p_radiant'].to_numpy(dtype=np.float64),
                group['fair'].to_numpy(dtype=np.float64),
            )
            summary['maps'] = int(group['match_id'].nunique())
            binned[str(bin_label)] = summary
        results[f'{feature}_deciles'] = binned
        for bin_label, summary in binned.items():
            print('DECILE', feature, bin_label, summary)

    # Cross-check inference at identical validation feature rows on each installed catalog.
    parity = model_server_parity(validation.loc[validation['market_status'].eq('ok')].head(512))
    results['model_server_parity'] = parity

    # Honest chronological tree-count selection: early 80% of train maps fit, last 20% select trees;
    # refit each same 90%-match subsample on all train maps, then score last 40% of validation maps.
    honest_models, honest_trees, inner_fit_maps, inner_stop_maps = build_honest_refit(train)
    test_features = heldout_validation[FEATURE_COLUMNS].copy()
    test_features['second'] = test_features['second'] - TRAIN_LAG_SECONDS
    test_matrix = test_features.to_numpy(dtype=np.float64)
    honest_delta = np.stack([model.predict(test_matrix, num_threads=1) for model in honest_models]).mean(axis=0)
    honest_fair = np.clip(
        heldout_validation['market_p_radiant'].to_numpy(dtype=np.float64) + honest_delta,
        0.0,
        1.0,
    )
    results['honest_last40_validation'] = score('HONEST_LAST40_VALIDATION', heldout_validation, honest_fair)
    paired_gain_delta = (
        np.abs(heldout_validation['signal_market_p_radiant_300s'].to_numpy(dtype=np.float64)
               - heldout_validation['market_p_radiant'].to_numpy(dtype=np.float64)
        )
        - np.abs(heldout_validation['signal_market_p_radiant_300s'].to_numpy(dtype=np.float64) - honest_fair)
        - (
            np.abs(heldout_validation['signal_market_p_radiant_300s'].to_numpy(dtype=np.float64)
                   - heldout_validation['market_p_radiant'].to_numpy(dtype=np.float64)
            )
            - np.abs(heldout_validation['signal_market_p_radiant_300s'].to_numpy(dtype=np.float64)
                     - original_heldout_fair)
        )
    )
    paired_ci = event_bootstrap_mean(heldout_validation, paired_gain_delta)
    results['honest_vs_research_last40_paired_gain_difference_cents'] = {
        'honest_minus_research_cents': float(paired_gain_delta.mean() * 100),
        'event_cluster_ci': [paired_ci[0], paired_ci[1]],
    }
    results['honest_tree_counts'] = {
        'counts': honest_trees,
        'mean': float(np.mean(honest_trees)),
        'range': [min(honest_trees), max(honest_trees)],
        'inner_fit_maps': inner_fit_maps,
        'inner_stop_maps': inner_stop_maps,
        'heldout_validation_maps': len(heldout_ids),
    }
    print('HONEST_TREE_COUNTS', results['honest_tree_counts'])

    OUT.write_text(json.dumps(results, indent=2, sort_keys=True))
    print('RESULT_JSON', OUT)


if __name__ == '__main__':
    main()
