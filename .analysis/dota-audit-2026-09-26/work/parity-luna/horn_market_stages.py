import json,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,'/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src')
from shared.utils.jsonl_io import open_maybe_gz
from shared.utils.match_time import get_paused_seconds_before,get_horn_datetime,get_state_available_ts,parse_utc
from market_data.build_market_data import market_seconds_cache_path
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer,replay_grid_records
from trader.game_profile import GAME_PROFILES
E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader');R=Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')
sample=pd.read_csv(R/'horn_clock_samples.csv');cat=pd.read_parquet(E/'data/new_processed/match_catalog/match_catalog.parquet');cat['c']=cat.condition_id.astype(str).str.lower();cat=cat.set_index('c')
starts=pd.read_parquet(E/'data/new_processed/grid_game_starts/grid_game_windows.parquet').drop_duplicates('condition_id');starts['c']=starts.condition_id.astype(str).str.lower();start_by=starts.set_index('c')['spawn_at'].to_dict()
out=[]
for x in sample.itertuples():
 d=E/'data/trader'/x.dir;m=json.loads((d/'match.json').read_text());c=str(m['market']['condition_id']).lower();r=cat.loc[c]
 if c not in start_by:continue
 pauses=json.loads(r.pauses_json or '[]');D=get_paused_seconds_before(pauses,0)
 if D<20 or D>500:continue
 grid_horn=get_horn_datetime(parse_utc(start_by[c]),pauses);archive_horn=parse_utc(m['horn_at_utc']);diff=(archive_horn-grid_horn).total_seconds();affected=abs(diff+D)<=5
 market=m['market'];red=GridFrameReducer(m['map_number'],market['outcome_0_name'],market['outcome_1_name'],GAME_PROFILES['dota']);ap=d/'grid_state.jsonl.gz';ap=ap if ap.exists() else d/'grid_state.jsonl'
 events=list(replay_grid_records(iter_grid_archive_records(ap),red));events=[e for e in events if e.snapshot.second>=0]
 sess={}
 with open_maybe_gz(d/'session.jsonl') as f:
  for line in f:
   try:s=json.loads(line)
   except:continue
   if s.get('kind')=='signal' and s.get('reason')=='model' and s.get('market_p_radiant') is not None:
    sess.setdefault(int(s['second']),[]).append(float(s['market_p_radiant']))
 cp=market_seconds_cache_path(int(m.get('steam_match_id') or r.match_id))
 if not cp.exists():continue
 cache=pd.read_parquet(cp,columns=['second','state_ts_us','market_status','market_p_radiant'])
 for target in (120,240,360,450):
  cand=[e for e in events if abs(int(e.snapshot.second)-target)<=15]
  if not cand:continue
  e=min(cand,key=lambda e:abs(int(e.snapshot.second)-target));S=int(e.snapshot.second);M=S+10;cr=cache[cache.second==M]
  if cr.empty:continue
  cr=cr.iloc[0];expected=get_state_available_ts(horn=grid_horn,second=M,pauses=pauses);cache_ts=pd.Timestamp(int(cr.state_ts_us),unit='us',tz='UTC')
  ps=sess.get(S,[]);p=float(pd.Series(ps).median()) if ps else None
  out.append({'condition_id':c,'match_id':int(r.match_id),'target':target,'second':S,'D':D,'horn_resid':diff+D,'affected':affected,
   'market_status':cr.market_status,'cache_shift_sec':(cache_ts-expected).total_seconds(),'cache_p':cr.market_p_radiant,'live_p':p,
   'market_p_absdiff':abs(float(cr.market_p_radiant)-p) if p is not None and pd.notna(cr.market_p_radiant) else None})
df=pd.DataFrame(out);df.to_csv(R/'horn_market_stages.csv',index=False)
for target,g in df.groupby('target'):
 print('STAGE',target,'rows',len(g))
 for label,x in [('direct_archive_early',g[g.affected]),('no_D_horn_offset',g[~g.affected])]:
  print(label,'n',len(x),'Dmed',x.D.median(),'cache_shift p50/p90',x.cache_shift_sec.quantile([.5,.9]).to_dict(),'mid absdiff p50/p90',x.market_p_absdiff.quantile([.5,.9]).to_dict(),'status',x.market_status.value_counts().to_dict())
