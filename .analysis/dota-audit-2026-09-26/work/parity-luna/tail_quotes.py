import json
from pathlib import Path
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, str(Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src')))
from market_data.build_market_data import market_seconds_cache_path
E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
R=Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')
b=E/'data/backtests/dota_maker/LIVE/seed0'
res=pd.read_parquet(b/'results.parquet')
tail=res[(res.terminal_position>5)&(res.settlement_applied)].copy()
cat=pd.read_parquet(E/'data/new_processed/match_catalog/match_catalog.parquet',columns=['match_id','condition_id','radiant_token_index','radiant_win'])
tail=tail.merge(cat,on=['match_id','condition_id'],how='left')
q=pd.read_parquet(b/'quote_events.parquet',columns=['match_id','ts_ns','kind','token_index','side','price','predicted_delta','fair','book_p_radiant','order_id','reason'])
out=[]
for row in tail.itertuples(index=False):
 mid=int(row.match_id);g=q[q.match_id==mid].sort_values('ts_ns')
 terminal=int(row.terminal_token_index);radiant=int(row.radiant_token_index)
 cache_path=market_seconds_cache_path(mid)
 if cache_path.exists():
  path=pd.read_parquet(cache_path,columns=['state_ts_us','market_status','market_p_radiant'])
  path=path[(path.market_status=='ok')&path.market_p_radiant.notna()].copy()
 else:path=pd.DataFrame(columns=['state_ts_us','market_status','market_p_radiant'])
 sells=g[(g.side=='SELL')&(g.token_index==terminal)&(g.kind=='submitted')]
 ended=pd.to_datetime(row.game_ended_at,utc=True,errors='coerce')
 end_ns=int(ended.value) if pd.notna(ended) else int(g.ts_ns.max())
 accepted=0;active_to_end=0;cross_maps=0;cross_orders=0;sample_count=0;end_prices=[];end_max_mids=[];end_diffs=[]
 for oid in sells.order_id.dropna().astype(str).unique():
  og=g[g.order_id.astype(str)==oid]
  sub=og[og.kind=='submitted']
  acc=og[og.kind=='accepted']
  if sub.empty or acc.empty:continue
  accepted+=1;start=int(acc.ts_ns.min());end=end_ns
  canc=og[(og.kind=='cancel_ack')&(og.ts_ns>=start)]
  if not canc.empty:end=min(end,int(canc.ts_ns.min()))
  px=float(sub.iloc[0].price)
  if end>=end_ns-2_000_000_000: active_to_end+=1
  active=path[(path.state_ts_us>=start//1000)&(path.state_ts_us<=end//1000)]
  if active.empty:continue
  mids=active.market_p_radiant.to_numpy(dtype=float)
  if terminal!=radiant:mids=1-mids
  sample_count+=len(mids)
  cross=bool((mids>=px).any())
  cross_orders+=int(cross)
  if end>=end_ns-2_000_000_000:
   end_prices.append(px);end_max_mids.append(float(np.max(mids)));end_diffs.append(px-float(np.max(mids)))
  if cross:cross_maps+=1
 out.append({'match_id':mid,'condition_id':row.condition_id,'slug':row.slug,'source':row.feed_source,'mode':row.signal_mode,
  'side':row.terminal_side,'held':float(row.terminal_position),'pnl':float(row.engine_pnl),'settlement':float(row.engine_pnl-row.cash_flow),
  'sell_orders_accepted':accepted,'sell_orders_active_to_end':active_to_end,'sell_orders_mid_crossed':cross_orders,
  'end_sell_prices':';'.join(f'{x:.4f}' for x in end_prices),'end_sell_max_mids':';'.join(f'{x:.4f}' for x in end_max_mids),'end_quote_minus_max_mid':';'.join(f'{x:.4f}' for x in end_diffs),
  'end_orders_any_mid_cross':any(x<=0 for x in end_diffs),'min_end_quote_minus_max_mid':min(end_diffs) if end_diffs else None,
  'active_mid_samples':sample_count})
df=pd.DataFrame(out)
df.to_csv(R/'tail_quote_paths.csv',index=False)
print('TAIL_NON_DUST',len(df),'modes',df.groupby(['mode','source']).size().to_dict(),'total_settlement',round(df.settlement.sum(),2),'total_engine_pnl',round(df.pnl.sum(),2))
print('quote summaries accepted orders',int(df.sell_orders_accepted.sum()),'active-to-end maps/orders',int((df.sell_orders_active_to_end>0).sum()),int(df.sell_orders_active_to_end.sum()),'active-end maps with observed mid crossing any sell quote',int(df.end_orders_any_mid_cross.sum()))
print(df.to_string(index=False))
