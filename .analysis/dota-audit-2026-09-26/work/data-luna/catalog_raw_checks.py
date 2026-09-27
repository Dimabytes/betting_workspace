import json
from pathlib import Path
import numpy as np
import pandas as pd
from shared.constants.paths import MATCH_CATALOG_PATH, RAW_TELONEX_POLYMARKET_DIR
from shared.utils.match_catalog import load_match_catalog
from shared.utils.match_time import get_paused_seconds_before, get_state_available_ts, datetime_to_ns
from shared.utils.telonex_book import load_token_book, parse_best_bid_ask, US_PER_SECOND, NS_PER_US
from market_data.build_market_data import market_seconds_cache_path

catdf=pd.read_parquet(MATCH_CATALOG_PATH)
cat=load_match_catalog(MATCH_CATALOG_PATH)
fit=[]
for e in cat.values():
 if e.spawn_at:
  expected=90+get_paused_seconds_before(e.pauses,0)
  actual=(e.horn_at-e.spawn_at).total_seconds()
  end_ts=get_state_available_ts(horn=e.horn_at,second=e.duration,pauses=e.pauses)
  end_res=(e.ended_at-end_ts).total_seconds()
  fit.append((e.match_id,actual-expected,end_res,actual,expected,e.duration))
f=pd.DataFrame(fit,columns=['match_id','horn_residual','end_residual','horn_gap','horn_expected','duration'])
print('HORN_DERIVATION_RESIDUAL',f.horn_residual.abs().describe(percentiles=[.5,.95,.99]).to_dict(),'nonzero_gt_1s',int((f.horn_residual.abs()>1).sum()))
print('GAME_END_TIME_RESIDUAL ended_at - horn+duration+pauses',f.end_residual.describe(percentiles=[.01,.05,.5,.95,.99]).to_dict(),'abs_gt_60',int((f.end_residual.abs()>60).sum()))
print('TIME_END_OUTLIERS',f.reindex(f.end_residual.abs().sort_values(ascending=False).index).head(12).to_dict('records'))
# Evaluate within 60 sec of the measured game duration; report only residual market/outcome conflicts.
val=pd.read_parquet('data/new_processed/dataset/validation_dataset.parquet')
raw=catdf.set_index('match_id')
mis=[]; close=[]
for mid,g in val.groupby('match_id',sort=False):
 if mid not in raw.index: continue
 duration=int(raw.loc[mid,'duration'])
 good=g[g.market_p_radiant.notna()].sort_values('second')
 if good.empty:continue
 last=good.iloc[-1]
 gap=duration-int(last.second)
 if gap<=60:
  record={'match_id':int(mid),'winner':bool(last.radiant_win),'last_p':float(last.market_p_radiant),'gap':gap,'last_second':int(last.second),'duration':duration,'condition_id':str(last.condition_id)}
  close.append(record)
  if (last.market_p_radiant>=.5)!=bool(last.radiant_win): mis.append(record)
print('VALIDATION_END_60',len(close),'misaligned',len(mis),'misaligned_rows',mis)
# Paired file-level raw book quality for map examples bracketing the apparent collection transition.
ids=[8579345535,8900623530,8962592819,8995085405]
for mid in ids:
 e=cat[mid]
 anchor=datetime_to_ns(get_state_available_ts(horn=e.horn_at,second=-60,pauses=e.pauses))//NS_PER_US-5*US_PER_SECOND
 stop=datetime_to_ns(get_state_available_ts(horn=e.horn_at,second=e.duration,pauses=e.pauses))//NS_PER_US+300*US_PER_SECOND
 print('RAW_MAP',pd.Timestamp(e.start_time,unit='s',tz='UTC').strftime('%Y-%m'),mid,e.gamma.slug)
 for token in e.gamma.token_ids:
  base=RAW_TELONEX_POLYMARKET_DIR/'book_snapshot_full'/f'asset_id={token}'
  timestamps=[]; records=[]; depths=[]
  for p in sorted(base.glob('*.parquet')):
   frame=pd.read_parquet(p,columns=['timestamp_us','bids','asks'],filters=[('timestamp_us','>=',anchor),('timestamp_us','<=',stop)])
   for ts,bids,asks in zip(frame.timestamp_us,frame.bids,frame.asks,strict=True):
    best=parse_best_bid_ask(bids,asks)
    timestamps.append(int(ts)); records.append((best.bid,best.ask))
    depths.append((len(bids) if bids is not None else 0,len(asks) if asks is not None else 0))
  tsarr=np.array(timestamps,dtype=np.int64)
  counts=pd.Series(timestamps).value_counts()
  dup_groups=counts[counts>1].index
  at_dup={}
  for t,(b,a) in zip(timestamps,records,strict=True):
   if t in dup_groups: at_dup.setdefault(t,set()).add((b,a))
  intervals=np.diff(np.sort(np.unique(tsarr)))/US_PER_SECOND if len(tsarr) else np.array([])
  crossed=sum(b is not None and a is not None and b>=a for b,a in records)
  one_sided=sum((b is None)!=(a is None) for b,a in records)
  micro_resid=int((tsarr%1000!=0).sum()) if len(tsarr) else 0
  print(token[:12], 'rows',len(tsarr),'dup_rows',int(counts[counts>1].sum()-len(dup_groups)) if len(dup_groups) else 0,'dup_groups_different_top',sum(len(v)>1 for v in at_dup.values()),'timestamp_non_ms',micro_resid,'one_sided',one_sided,'crossed',crossed,'depth_med',tuple(np.median(np.array(depths),axis=0).tolist()) if depths else None,'unique_dt_median_s',float(np.median(intervals)) if len(intervals) else None,'unique_dt_p95_s',float(np.quantile(intervals,.95)) if len(intervals) else None)
