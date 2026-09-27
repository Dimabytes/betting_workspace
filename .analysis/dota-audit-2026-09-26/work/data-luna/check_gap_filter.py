import pandas as pd
from shared.constants.paths import RESEARCH_MODEL_SPLIT_PATH
from shared.constants.dataset import VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE
from market_data.build_market_data import market_seconds_cache_path
from shared.utils.match_catalog import load_match_catalog
p=RESEARCH_MODEL_SPLIT_PATH
print('split_path',p)
d=pd.read_parquet(p)
print('rows',len(d),'split_counts',d.split.value_counts().to_dict(),'flag_counts',d.backtest_book_gap_excluded.value_counts().to_dict())
v=d[d.split.eq('validation')]
print('validation',len(v),'excluded',int(v.backtest_book_gap_excluded.sum()),'share',float(v.backtest_book_gap_excluded.mean()))
print('excluded_by_month',pd.to_datetime(v.start_time,unit='s',utc=True).dt.strftime('%Y-%m').where(v.backtest_book_gap_excluded).value_counts().to_dict())
print('excluded_ids',v.loc[v.backtest_book_gap_excluded,'match_id'].astype(int).tolist())
