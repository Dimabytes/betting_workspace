import pandas as pd
from collections import Counter
from shared.constants.paths import MATCH_CATALOG_PATH,RAW_TELONEX_POLYMARKET_DIR
from shared.utils.match_catalog import load_match_catalog
from shared.utils.match_time import datetime_to_ns,get_state_available_ts
from shared.utils.telonex_book import load_token_book,find_asof_quote,US_PER_SECOND,NS_PER_US
from market_data.build_market_data import market_seconds_cache_path
cat=load_match_catalog(MATCH_CATALOG_PATH)
for mid in [8579345535,8900623530,8962592819,8995085405]:
 e=cat[mid]
 df=pd.read_parquet(market_seconds_cache_path(mid))
 start=datetime_to_ns(get_state_available_ts(horn=e.horn_at,second=-60,pauses=e.pauses))//NS_PER_US-5*US_PER_SECOND
 end=datetime_to_ns(get_state_available_ts(horn=e.horn_at,second=e.duration,pauses=e.pauses))//NS_PER_US+300*US_PER_SECOND
 books=[load_token_book(token_id=t,start_us=start,end_us=end,telonex_root=RAW_TELONEX_POLYMARKET_DIR) for t in e.gamma.token_ids]
 counters=Counter(); examples=[]
 for r in df.itertuples(index=False):
  if pd.isna(r.state_ts_us): continue
  current=[find_asof_quote(b,int(r.state_ts_us)) if b else None for b in books]
  cross=[q.quote is not None and q.quote.bid>q.quote.ask for q in current if q is not None]
  locked=[q.quote is not None and q.quote.bid==q.quote.ask for q in current if q is not None]
  if any(cross):
   counters['current_strict_cross_quote_seconds']+=1
   if r.market_status=='ok': counters['current_strict_cross_accepted']+=1
   if len(examples)<5: examples.append(('current',int(r.second),r.market_status,[(q.quote.bid,q.quote.ask,q.quote.age_seconds) if q and q.quote else None for q in current],r.market_p_radiant))
  if any(locked): counters['current_locked_quote_seconds']+=1
  target=int(r.state_ts_us)+300*US_PER_SECOND
  future=[find_asof_quote(b,target) if b else None for b in books]
  cross_future=[q.quote is not None and q.quote.bid>q.quote.ask for q in future if q is not None]
  locked_future=[q.quote is not None and q.quote.bid==q.quote.ask for q in future if q is not None]
  if any(cross_future):
   counters['future_strict_cross_seconds']+=1
   if r.signal_market_p_radiant_300s is not None: counters['future_strict_cross_label_nonnull']+=1
   if len(examples)<10: examples.append(('future',int(r.second),r.market_status,[(q.quote.bid,q.quote.ask,q.quote.age_seconds) if q and q.quote else None for q in future],r.signal_market_p_radiant_300s))
  if any(locked_future): counters['future_locked_seconds']+=1
 print('MAP',mid,e.gamma.slug,'statuses',df.market_status.value_counts().to_dict(),'cross_used',dict(counters),'examples',examples)
