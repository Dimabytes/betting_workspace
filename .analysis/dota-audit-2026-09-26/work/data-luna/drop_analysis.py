from pathlib import Path
import pandas as pd
from shared.constants.dataset import VALIDATION_START_TIME, TRAIN_LAG_SECONDS, MODEL_TARGET_HORIZON_SECONDS
from shared.constants.paths import MATCH_CATALOG_PATH
from shared.utils.match_catalog import load_match_catalog
from market_data.build_market_data import market_seconds_cache_path

catalog=load_match_catalog(MATCH_CATALOG_PATH)
raw=pd.read_parquet(MATCH_CATALOG_PATH)
raw['start_s']=raw.apply(lambda r: pd.Timestamp(r.spawn_at if isinstance(r.spawn_at,str) else r.horn_at).timestamp(),axis=1)
raw['month']=pd.to_datetime(raw.start_s,unit='s',utc=True).dt.strftime('%Y-%m')
raw['split']=raw.start_s.map(lambda x:'train' if x<VALIDATION_START_TIME else 'validation')
trainset=pd.read_parquet('data/new_processed/dataset/training_dataset.parquet')
valset=pd.read_parquet('data/new_processed/dataset/validation_dataset.parquet')
train_maps=set(trainset.match_id.astype(int)); val_maps=set(valset.match_id.astype(int))
idx=pd.read_parquet('data/new_processed/stratz_match_index/stratz_match_index.parquet').set_index('match_id')
rows=[]
for entry in catalog.values():
    split='train' if entry.start_time<VALIDATION_START_TIME else 'validation'
    if split!='train': continue
    path=market_seconds_cache_path(entry.match_id)
    cache_exists=path.exists()
    current_good=0; label_good=0
    if cache_exists:
        df=pd.read_parquet(path,columns=['second','market_status','signal_market_p_radiant_300s'])
        mkt_seconds={int(r.second):r for r in df.itertuples(index=False)}
        for state_second in range(-60,600,60):
            row=mkt_seconds.get(state_second+TRAIN_LAG_SECONDS)
            if row is None: continue
            if row.market_status=='ok':
                current_good+=1
                if pd.notna(row.signal_market_p_radiant_300s): label_good+=1
    index=idx.loc[entry.match_id] if entry.match_id in idx.index else None
    rows.append({'match_id':entry.match_id,'month':pd.to_datetime(entry.start_time,unit='s',utc=True).strftime('%Y-%m'),'cache':cache_exists,'any_current':current_good>0,'any_label':label_good>0,'good_current_points':current_good,'good_label_points':label_good,'dataset_map':entry.match_id in train_maps,'index_status':None if index is None else index.status,'playback':None if index is None else index.playback_available,'duration':entry.duration})
df=pd.DataFrame(rows)
print('TRAIN_DROP_SUMMARY')
print(df.groupby('month').agg(catalog=('match_id','size'),cache=('cache','sum'),any_current=('any_current','sum'),any_label=('any_label','sum'),dataset_map=('dataset_map','sum'),current_points=('good_current_points','sum'),label_points=('good_label_points','sum'),shorter_600=('duration',lambda x:(x<600).sum())).to_string())
print('TRAIN_REASON_COUNTS',df.assign(reason=lambda x: x.apply(lambda r:'no_cache' if not r.cache else ('no_good_current' if not r.any_current else ('no_good_label' if not r.any_label else ('states_drop' if not r.dataset_map else 'retained'))),axis=1)).reason.value_counts().to_dict())
print('TRAIN_BY_MONTH_REASON')
print(pd.crosstab(df.month,df.apply(lambda r:'no_cache' if not r.cache else ('no_good_current' if not r.any_current else ('no_good_label' if not r.any_label else ('states_drop' if not r.dataset_map else 'retained'))),axis=1)).to_string())
print('VALIDATION catalog',sum(entry.start_time>=VALIDATION_START_TIME for entry in catalog.values()),'val_maps',len(val_maps))
print('VAL_MONTHS',valset[['match_id']].drop_duplicates().merge(raw[['match_id','month']],on='match_id').groupby('month').size().to_dict())
