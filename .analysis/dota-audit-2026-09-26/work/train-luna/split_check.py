from pathlib import Path
from collections import defaultdict
import hashlib
import json
import pandas as pd
import pyarrow.parquet as pq
E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
cat_path=E/'data/new_processed/match_catalog/match_catalog.parquet'
print('catalog_schema', pq.read_schema(cat_path).names)
cat=pd.read_parquet(cat_path)
print('catalog_rows',len(cat),'match_id_unique',cat.match_id.nunique(),'condition_id_unique',cat.condition_id.nunique(),'market_slug_unique',cat.market_slug.nunique(),'event_id_unique',cat.event_id.nunique())
for col in ['condition_id','market_slug']:
    repeats=cat.groupby(col).match_id.nunique()
    print('catalog multi-match',col,int((repeats>1).sum()),'max_match_ids',int(repeats.max()))
print('catalog duplicate rows by match_id',int(cat.match_id.duplicated().sum()))
features=['second','radiant_nw_adv','radiant_nw','dire_nw','radiant_xp_adv','deaths_radiant','deaths_dire','top1_nw_adv','radiant_top1_nw_ratio','dire_top1_nw_ratio','market_radiant_prior','market_p_radiant','signal_market_p_radiant_300s','radiant_win']
for label,path in [('train',E/'data/new_processed/dataset/training_dataset.parquet'),('validation',E/'data/new_processed/dataset/validation_dataset.parquet')]:
    df=pd.read_parquet(path,columns=['match_id',*features])
    print(label,'rows',len(df),'features_nulls',df[features].isna().sum().to_dict(),'features_nonfinite',int((~df.select_dtypes('number').drop(columns=['match_id']).apply(lambda x:x.map(lambda y: pd.notna(y) and abs(float(y))!=float('inf')))).to_numpy().all(axis=1).sum()) if False else 'see nulls only')
    hashes=defaultdict(list)
    for mid,g in df.groupby('match_id',sort=False):
        g=g[features].sort_values('second',kind='mergesort')
        raw=pd.util.hash_pandas_object(g,index=False).to_numpy().tobytes()
        hashes[hashlib.sha256(raw).hexdigest()].append(int(mid))
    repeats=[v for v in hashes.values() if len(v)>1]
    print(label,'identical_full_sequence_hash_groups',len(repeats),'maps_in_repeated_groups',sum(map(len,repeats)))
# direct current meta hash checks concise
for name,ds in [('research','training_dataset.parquet'),('production','production/training_dataset.parquet'),('research-noxp','training_dataset.parquet'),('production-noxp','production/training_dataset.parquet')]:
    meta=json.loads((E/f'data/new_model/{name}/model.json').read_text())
    print(name,'trees',meta['member_trees'],'meta_mean',sum(meta['member_trees'])/len(meta['member_trees']),'round',round(sum(meta['member_trees'])/len(meta['member_trees'])))
