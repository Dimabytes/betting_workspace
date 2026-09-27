from pathlib import Path
import numpy as np
import pandas as pd
from shared.utils.gbm import FEATURE_COLUMNS
E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
for name,path in [('train',E/'data/new_processed/dataset/training_dataset.parquet'),('production',E/'data/new_processed/dataset/production/training_dataset.parquet')]:
    df=pd.read_parquet(path,columns=['match_id',*FEATURE_COLUMNS,'signal_market_p_radiant_300s'])
    X=df[FEATURE_COLUMNS].to_numpy(dtype=np.float64)
    print(name,'rows',len(df),'maps',df.match_id.nunique(),'features_all_finite',bool(np.isfinite(X).all()),'label_all_finite',bool(np.isfinite(df.signal_market_p_radiant_300s.to_numpy(dtype=np.float64)).all()),'source_seconds',sorted(df.second.unique().tolist()))
path=E/'data/new_processed/dataset/validation_dataset.parquet'
cols=list(dict.fromkeys(['market_status','market_p_radiant',*FEATURE_COLUMNS]))
df=pd.read_parquet(path,columns=cols)
usable=df.loc[df.market_status.eq('ok')]
X=usable[FEATURE_COLUMNS].to_numpy(dtype=np.float64)
print('validation_market_status_ok_rows',len(usable),'maps_with_ok',None,'features_all_finite',bool(np.isfinite(X).all()),'market_mid_min_max',float(usable.market_p_radiant.min()),float(usable.market_p_radiant.max()))
