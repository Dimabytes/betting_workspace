import pandas as pd, numpy as np
from scipy.stats import ks_2samp
from shared.constants.dataset import VALIDATION_START_TIME, MODEL_TARGET_HORIZON_SECONDS, TRAIN_LAG_SECONDS
from shared.constants.paths import MATCH_CATALOG_PATH, RAW_TELONEX_POLYMARKET_DIR
from shared.utils.match_catalog import load_match_catalog
from shared.utils.match_time import datetime_to_ns,get_state_available_ts
from shared.utils.telonex_book import load_token_book,find_asof_quote
from market_data.build_market_data import market_seconds_cache_path
cat=load_match_catalog(MATCH_CATALOG_PATH)
raw=pd.read_parquet(MATCH_CATALOG_PATH)
raw['start_s']=raw.apply(lambda r:pd.Timestamp(r.spawn_at if isinstance(r.spawn_at,str) else r.horn_at).timestamp(),axis=1)
raw['month']=pd.to_datetime(raw.start_s,unit='s',utc=True).dt.strftime('%Y-%m')
train=pd.read_parquet('data/new_processed/dataset/training_dataset.parquet')
val=pd.read_parquet('data/new_processed/dataset/validation_dataset.parquet')
# Compare like-for-like minute sampling rather than every exact validation second.
va=val[(val.market_status=='ok')&val.signal_market_p_radiant_300s.notna()].copy()
va['state_second']=va.second-10
va=va[(va.state_second>=-60)&(va.state_second<=540)&(va.state_second%60==0)]
features=['radiant_nw_adv','radiant_nw','dire_nw','radiant_xp_adv','deaths_radiant','deaths_dire','top1_nw_adv','radiant_top1_nw_ratio','dire_top1_nw_ratio','market_radiant_prior','market_p_radiant']
print('MINUTE_ALIGNED train_rows',len(train),'val_rows',len(va))
for f in features:
 a=train[f].dropna().astype(float); b=va[f].dropna().astype(float)
 print(f,'train',a.quantile([.05,.5,.95]).round(3).to_dict(),'val',b.quantile([.05,.5,.95]).round(3).to_dict(),'mean_delta',round(float(b.mean()-a.mean()),3),'ks_d',round(float(ks_2samp(a,b).statistic),4))
# Current prior vs the first usable in-game market probability for maps with cache.
current=market_seconds_cache_path(0).parent
rows=[]
for r in raw.itertuples(index=False):
 mid=int(r.match_id); p=market_seconds_cache_path(mid)
 if not p.exists(): continue
 d=pd.read_parquet(p,columns=['second','market_status','market_p_radiant'])
 good=d[(d.second>=0)&(d.market_status=='ok')&d.market_p_radiant.notna()].sort_values('second')
 if good.empty: continue
 prior=float(r.radiant_prior); first=float(good.iloc[0].market_p_radiant)
 rows.append((mid,r.month,prior,first,first-prior,int(good.iloc[0].second)))
pr=pd.DataFrame(rows,columns=['match_id','month','prior','first','delta','first_second'])
print('PRIOR_VS_FIRST',len(pr),'mean_abs',pr.delta.abs().mean(),'median_abs',pr.delta.abs().median(),'p95_abs',pr.delta.abs().quantile(.95),'mean_signed',pr.delta.mean(),'first_second_median',pr.first_second.median())
print('PRIOR_BY_MONTH',pr.groupby('month').delta.apply(lambda x:pd.Series({'n':len(x),'mean_abs':x.abs().mean(),'median_abs':x.abs().median()})).to_dict())
# End-window orientation: valid quote within final game minute vs STRATZ winner.
ends=[]
for mid,g in val.groupby('match_id',sort=False):
 duration=int(g.duration.iloc[0]) if 'duration' in g else int(raw.loc[raw.match_id.eq(mid),'duration'].iloc[0])
 good=g[g.market_p_radiant.notna()]
 if good.empty:continue
 last=good.sort_values('second').iloc[-1]
 gap=duration-int(last.second)
 if gap<=60:
  ends.append((int(mid),bool(last.radiant_win),float(last.market_p_radiant),gap))
e=pd.DataFrame(ends,columns=['match_id','win','lastp','gap'])
print('END_WITHIN_60',len(e),'win_correct_side',int(((e.lastp>=.5)==e.win).sum()),'near_terminal',int(np.where(e.win,e.lastp>=.95,e.lastp<=.05).sum()),'all_rows',len(val.match_id.unique()))
# Only post-end labels in policy-sized signal range.
vd=raw.set_index(raw.match_id.astype('int64')).duration.to_dict()
val['duration']=val.match_id.map(vd)
vp=val[(val.second>=-50)&(val.second<=489)&val.signal_market_p_radiant_300s.notna()]
vp_post=vp[(vp.second+300)>vp.duration]
print('VAL_BUY_WINDOW_LABELS',len(vp),'postgame',len(vp_post),'maps',vp_post.match_id.nunique(),'post_share',len(vp_post)/max(len(vp),1),'endpoint_mean_abs_delta',abs(vp_post.signal_market_p_radiant_300s-vp_post.market_p_radiant).mean())
