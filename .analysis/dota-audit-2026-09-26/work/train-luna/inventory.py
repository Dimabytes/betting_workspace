from pathlib import Path
import hashlib
import json
import pyarrow.parquet as pq
import pandas as pd

E = Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
files = {
    'train': E/'data/new_processed/dataset/training_dataset.parquet',
    'validation': E/'data/new_processed/dataset/validation_dataset.parquet',
    'production_train': E/'data/new_processed/dataset/production/training_dataset.parquet',
    'split': E/'data/new_model/research/split.parquet',
}
for label, path in files.items():
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = pq.ParquetFile(path).metadata
    print(label, 'rows', meta.num_rows, 'sha256', digest, 'schema', pq.read_schema(path).names)
for label in ['research','production','research-noxp','production-noxp']:
    meta = json.loads((E/f'data/new_model/{label}/model.json').read_text())
    train_path = files['production_train'] if label.startswith('production') else files['train']
    current_hash = hashlib.sha256(train_path.read_bytes()).hexdigest()
    split = pd.read_parquet(E/f'data/new_model/{label}/split.parquet')
    print(label, 'meta_train_hash_match', current_hash == meta['train_dataset_sha256'], 'meta_train_match_count', meta['train_matches'], 'split_counts', split.groupby(['split','backtest_book_gap_excluded']).size().to_dict(), 'split_ids_unique', split.match_id.nunique(), 'split_rows', len(split))
train = pd.read_parquet(files['train'], columns=['match_id','start_time','second'])
val = pd.read_parquet(files['validation'], columns=['match_id','start_time','second','event_id'])
prod = pd.read_parquet(files['production_train'], columns=['match_id','start_time','second'])
print('train ids', train.match_id.nunique(), 'rows', len(train), 'time range', train.start_time.min(), train.start_time.max())
print('val ids', val.match_id.nunique(), 'rows', len(val), 'time range', val.start_time.min(), val.start_time.max())
print('prod ids', prod.match_id.nunique(), 'rows', len(prod), 'time range', prod.start_time.min(), prod.start_time.max())
print('train/val match_id overlap', len(set(train.match_id.unique()) & set(val.match_id.unique())))
print('train/val start_time overlaps', len(set(train.start_time.unique()) & set(val.start_time.unique())))
print('validation id row counts describe', val.groupby('match_id').size().describe(percentiles=[.25,.5,.75,.9,.99]).to_dict())
print('train id row counts describe', train.groupby('match_id').size().describe(percentiles=[.25,.5,.75,.9,.99]).to_dict())
