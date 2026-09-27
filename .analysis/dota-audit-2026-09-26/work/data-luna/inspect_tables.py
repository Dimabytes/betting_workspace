from pathlib import Path
import pandas as pd

ROOT = Path('data/new_processed')
for name in ['match_catalog/match_catalog.parquet','universe/universe.parquet','pregame_quotes/pregame_quotes.parquet','stratz_match_index/stratz_match_index.parquet','dataset/training_dataset.parquet','dataset/validation_dataset.parquet','dataset/production/training_dataset.parquet','dataset/production/split.parquet']:
    p = ROOT/name
    df = pd.read_parquet(p)
    print(f'## {name}: rows={len(df)} cols={len(df.columns)}')
    print('columns=',list(df.columns))
    print('dtypes=',{k:str(v) for k,v in df.dtypes.items()})
    print(df.head(2).to_string(index=False))
for d in sorted((ROOT/'market_seconds').iterdir()):
    if d.is_dir():
        files=list(d.glob('*.parquet'))
        print(f'## cache {d.name}: files={len(files)} bytes={sum(p.stat().st_size for p in files)} newest_mtime={max((p.stat().st_mtime for p in files),default=0)}')
