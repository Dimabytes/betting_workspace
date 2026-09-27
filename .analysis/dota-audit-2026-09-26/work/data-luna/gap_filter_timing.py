import pandas as pd
from shared.constants.paths import RESEARCH_MODEL_SPLIT_PATH
from shared.constants.dataset import MAX_BOOK_GAP_SECONDS,MODEL_START_SECOND,VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE
from market_data.build_market_data import market_seconds_cache_path
split=pd.read_parquet(RESEARCH_MODEL_SPLIT_PATH)
excluded=split[(split.split=='validation')&split.backtest_book_gap_excluded.astype(bool)]
out=[]
for mid in excluded.match_id.astype(int):
 d=pd.read_parquet(market_seconds_cache_path(mid),columns=['second','market_status']).sort_values('second')
 run=0; start=None; gaps=[]
 for row in d.itertuples(index=False):
  sec=int(row.second)
  if sec<MODEL_START_SECOND or sec>=VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE: continue
  if row.market_status!='ok':
   if run==0: start=sec
   run+=1
  else:
   if run>MAX_BOOK_GAP_SECONDS: gaps.append((start,sec-1,run))
   run=0; start=None
 if run>MAX_BOOK_GAP_SECONDS:gaps.append((start,int(d.second.max()),run))
 gaps.sort(key=lambda x:x[2],reverse=True)
 if gaps:
  s,e,n=gaps[0]
  prebuy_trigger=any(s0<=489 and min(e0,489)-s0+1>MAX_BOOK_GAP_SECONDS for s0,e0,_ in gaps)
  out.append((mid,s,e,n,sum(1 for x in gaps if x[0]>489),prebuy_trigger))
f=pd.DataFrame(out,columns=['match_id','gap_start','gap_end','gap_len','post_buy_gaps','prebuy_trigger'])
print('flagged_maps',len(excluded),'with_gap_details',len(f),'longest_gap_starts_after_last_buy_quote',int((f.gap_start>489).sum()),'longest_gap_crosses_last_buy_quote',int(((f.gap_start<=489)&(f.gap_end>489)).sum()),'longest_gap_ends_before_last_buy_quote',int((f.gap_end<489).sum()),'would_still_flag_from_entry_window_alone',int(f.prebuy_trigger.sum()),'depends_on_post_entry_data',int((~f.prebuy_trigger).sum()))
print('gap_start_quantiles',f.gap_start.quantile([0,.25,.5,.75,1]).to_dict(),'gap_len_quantiles',f.gap_len.quantile([.5,.75,.9,.95,1]).to_dict())
print('gap_month',f.assign(month=pd.to_datetime(f.match_id.map(dict(zip(split.match_id.astype(int),split.start_time))),unit='s',utc=True).dt.strftime('%Y-%m')).groupby('month').agg(maps=('match_id','size'),after_buy=('gap_start',lambda x:(x>490).sum())).to_dict())
print('sample_after_buy',f[f.gap_start>490].head(12).to_dict('records'))
