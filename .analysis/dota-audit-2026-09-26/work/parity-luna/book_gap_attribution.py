"""Split the validation backtest book-gap filter at the 480s BUY cutoff."""
import json
from pathlib import Path

import pandas as pd

from market_data.build_market_data import market_seconds_cache_path
from shared.constants.paths import MATCH_CATALOG_PATH, RESEARCH_MODEL_SPLIT_PATH
from shared.utils.jsonl_io import open_maybe_gz

E = Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
R = Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')


def longest_bad_run(rows: pd.DataFrame, low: int, high: int) -> int:
    longest = run = 0
    for row in rows.itertuples(index=False):
        second = int(row.second)
        if not low <= second < high:
            continue
        run = run + 1 if row.market_status != 'ok' else 0
        longest = max(longest, run)
    return longest


split = pd.read_parquet(RESEARCH_MODEL_SPLIT_PATH)
validation = split[split.split == 'validation']
flagged = validation[validation.backtest_book_gap_excluded.astype(bool)]
gap_rows = []
for match_id in flagged.match_id.astype(int):
    cache = pd.read_parquet(
        market_seconds_cache_path(match_id), columns=['second', 'market_status']
    )
    gap_rows.append(
        {
            'match_id': match_id,
            'pre480_max': longest_bad_run(cache, -60, 480),
            'post480_max': longest_bad_run(cache, 480, 900),
            'full_max': longest_bad_run(cache, -60, 900),
        }
    )
gaps = pd.DataFrame(gap_rows)
gaps.to_csv(R / 'book_gap_attribution.csv', index=False)

catalog = pd.read_parquet(MATCH_CATALOG_PATH, columns=['match_id', 'condition_id'])
catalog['condition_id'] = catalog.condition_id.str.lower()
match_by_condition = catalog.drop_duplicates('condition_id').set_index('condition_id').match_id.to_dict()
validation_ids = set(validation.match_id.astype(int))
gap_ids = set(flagged.match_id.astype(int))
live_rows = []
for archive_dir in (E / 'data/trader').iterdir():
    meta_path = archive_dir / 'match.json'
    session_path = archive_dir / 'session.jsonl'
    if not meta_path.exists() or not session_path.exists():
        continue
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        continue
    if meta.get('game', 'dota') not in (None, 'dota'):
        continue
    starts = []
    with open_maybe_gz(session_path) as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('kind') == 'session_start':
                starts.append(row)
    if not starts or starts[-1].get('execution_mode') != 'live':
        continue
    market = meta.get('market') or {}
    condition = str(market.get('condition_id') or starts[-1].get('condition_id') or '').lower()
    match_id = match_by_condition.get(condition)
    if match_id not in validation_ids:
        continue
    fills = 0
    with open_maybe_gz(session_path) as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            fills += row.get('kind') == 'fill'
    live_rows.append(
        {
            'archive': archive_dir.name,
            'condition_id': condition,
            'match_id': int(match_id),
            'book_gap_excluded': int(match_id) in gap_ids,
            'fills': fills,
        }
    )
live = pd.DataFrame(live_rows)
gap_live = live[live.book_gap_excluded].merge(gaps, on='match_id', how='left')
late_only = (gaps.pre480_max <= 120) & (gaps.full_max > 120)
late_live = gap_live[(gap_live.pre480_max <= 120) & (gap_live.full_max > 120)]

print('VALIDATION', len(validation), 'FLAGGED', len(flagged), 'CACHES_CHECKED', len(gaps))
print(
    'GAP_SPLIT',
    'pre480_over120', int((gaps.pre480_max > 120).sum()),
    'full_window_only', int(late_only.sum()),
    'post480_max_over120_among_full_only',
    int(((gaps.pre480_max <= 120) & (gaps.full_max > 120) & (gaps.post480_max > 120)).sum()),
)
print('LIVE_VALIDATION', live.condition_id.nunique(), 'LIVE_GAP_EXCLUDED', gap_live.condition_id.nunique())
print('LIVE_LATE_ONLY', len(late_live), 'fills', int(late_live.fills.sum()), late_live[['archive', 'match_id', 'fills']].to_dict('records'))
print('LIVE_GAP_EXCLUDED_ROWS', gap_live[['archive', 'match_id', 'pre480_max', 'post480_max', 'full_max', 'fills']].to_dict('records'))
