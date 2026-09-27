from pathlib import Path
import numpy as np
import pandas as pd
from shared.constants.dataset import TRAIN_LAG_SECONDS
from shared.utils.gbm import FEATURE_COLUMNS, NO_XP_FEATURE_COLUMNS, load_predictor
E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
path=E/'data/new_processed/dataset/validation_dataset.parquet'
cols=list(dict.fromkeys(['match_id','event_id','start_time','second','market_status','market_p_radiant','signal_market_p_radiant_300s',*FEATURE_COLUMNS[1:]]))
val=pd.read_parquet(path,columns=cols)
val=val.loc[(val.second>=-60)&(val.second<600)&(val.market_status=='ok')&val.signal_market_p_radiant_300s.notna()].reset_index(drop=True)
for name,features in [('research',FEATURE_COLUMNS),('production',FEATURE_COLUMNS),('research-noxp',NO_XP_FEATURE_COLUMNS),('production-noxp',NO_XP_FEATURE_COLUMNS)]:
    model=load_predictor(E/f'data/new_model/{name}')
    X=val[list(features)].copy(); X['second']=X['second']-TRAIN_LAG_SECONDS
    fair=np.clip(val.market_p_radiant.to_numpy(dtype=np.float64)+model.predict_one_thread(X),0,1)
    for label,mask in [('p>=.85',val.market_p_radiant>=.85),('abs_nw>=5000',val.radiant_nw_adv.abs()>=5000),('all',pd.Series(True,index=val.index))]:
        m=mask.to_numpy()
        if not m.any():continue
        delta=(fair[m]-val.signal_market_p_radiant_300s.to_numpy(dtype=np.float64)[m])*100
        current=val.market_p_radiant.to_numpy(dtype=np.float64)[m]
        future=val.signal_market_p_radiant_300s.to_numpy(dtype=np.float64)[m]
        gain=(np.abs(future-current)-np.abs(future-fair[m]))*100
        print(name,label,'n',int(m.sum()),'maps',int(val.loc[m,'match_id'].nunique()),'events',int(val.loc[m,'event_id'].nunique()),'bias_cents',float(delta.mean()),'mae_gain_cents',float(gain.mean()))
