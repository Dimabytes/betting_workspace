from pathlib import Path
import lightgbm as lgb
import numpy as np
import pandas as pd
from shared.utils.gbm import FEATURE_COLUMNS
from shared.constants.paths import VALIDATION_DATASET_PATH
from train_model.train_model import lagged_source_features,select_usable_validation_rows

ROOT=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
cols=list(dict.fromkeys(FEATURE_COLUMNS+['match_id','market_status','state_ts_us']))
v=select_usable_validation_rows(pd.read_parquet(VALIDATION_DATASET_PATH,columns=cols))
for name,modelpath in [('l1-val454','data/new_model/archive/research/20260904T222046Z/model.txt'),('hubermono-val454','data/new_model/research/model.txt')]:
    model=lgb.Booster(model_file=str(ROOT/modelpath))
    lookup=v[['match_id','state_ts_us','market_p_radiant']].copy()
    lookup['rounded_delta']=np.round(model.predict(lagged_source_features(v),num_threads=4),12)
    lookup['market_key']=np.round(lookup.market_p_radiant,12)
    lookup=lookup.groupby(['match_id','rounded_delta','market_key'],as_index=False).state_ts_us.min()
    frames=[]
    for seed in range(12):
        f=pd.read_parquet(ROOT/'data/backtests/dota_maker'/('validation_join_delta01_cut540_nw350_p35_'+name)/f'seed{seed}/fills.parquet')
        f['seed']=seed
        frames.append(f)
    fills=pd.concat(frames,ignore_index=True)
    fills['rounded_delta']=fills.predicted_delta.round(12)
    fills['market_key']=fills.dataset_market_p.round(12)
    joined=fills.merge(lookup,on=['match_id','rounded_delta','market_key'],how='left',validate='many_to_one')
    missing=joined.state_ts_us.isna()
    future=joined.state_ts_us*1000>joined.ts_ns
    print(name,'fills',len(fills),'unmatched model predictions',int(missing.sum()),'matching predictions only after fill',int(future.sum()),flush=True)
    if missing.any():print(joined.loc[missing,['match_id','side','predicted_delta','dataset_market_p']].head(8),flush=True)
    assert not missing.any() and not future.any()
print('ALL FILL PREDICTIONS MATCH THEIR CLAIMED RESEARCH MODEL AND PRECEDE FILLS',flush=True)
