from pathlib import Path
from collections import defaultdict
import hashlib
import pandas as pd
from shared.utils.gbm import FEATURE_COLUMNS
E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
DATA=E/'data/new_processed/dataset'
train=pd.read_parquet(DATA/'training_dataset.parquet')
train=train.loc[train.signal_market_p_radiant_300s.notna()].copy()
train['source_second']=train.second
val_cols=list(dict.fromkeys(['match_id','market_status','radiant_win','second','market_radiant_prior','market_p_radiant','signal_market_p_radiant_300s',*FEATURE_COLUMNS[1:]]))
val=pd.read_parquet(DATA/'validation_dataset.parquet',columns=val_cols)
val=val.loc[(val.market_status=='ok')&val.signal_market_p_radiant_300s.notna()].copy()
val['source_second']=val.second-10
train_seconds=set(train.source_second.unique().tolist())
val=val.loc[val.source_second.isin(train_seconds)].copy()
cols=['source_second','radiant_win',*FEATURE_COLUMNS[1:],'market_p_radiant','signal_market_p_radiant_300s']
# Feature second in the validation row is still market second; normalize it to source state second.
val['second']=val.source_second
train['second']=train.source_second
train_hashes=defaultdict(list)
val_hashes=defaultdict(list)
for mid,g in train.groupby('match_id',sort=False):
    h=hashlib.sha256(pd.util.hash_pandas_object(g[cols].sort_values('source_second'),index=False).to_numpy().tobytes()).hexdigest()
    train_hashes[h].append(int(mid))
for mid,g in val.groupby('match_id',sort=False):
    h=hashlib.sha256(pd.util.hash_pandas_object(g[cols].sort_values('source_second'),index=False).to_numpy().tobytes()).hexdigest()
    val_hashes[h].append(int(mid))
common=set(train_hashes)&set(val_hashes)
print('cross_split_exact_aligned_map_signatures',len(common))
for h in sorted(common):print('MATCHED_SIGNATURE',train_hashes[h],val_hashes[h])
print('train_map_signatures',len(train_hashes),'validation_map_signatures',len(val_hashes),'matched_common_row_min',int(train.groupby('match_id').size().min()),int(val.groupby('match_id').size().min()))
