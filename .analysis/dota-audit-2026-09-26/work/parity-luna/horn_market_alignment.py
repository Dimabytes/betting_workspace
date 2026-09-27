import json,sys
from pathlib import Path
import numpy as np,pandas as pd
sys.path.insert(0,'/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src')
from shared.utils.jsonl_io import open_maybe_gz
from shared.utils.match_time import get_paused_seconds_before,get_horn_datetime,get_state_available_ts,parse_utc
from market_data.build_market_data import market_seconds_cache_path
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer,replay_grid_records
from trader.game_profile import GAME_PROFILES

E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
R=Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')
sample=pd.read_csv(R/'horn_clock_samples.csv')
cat=pd.read_parquet(E/'data/new_processed/match_catalog/match_catalog.parquet');cat['c']=cat.condition_id.astype(str).str.lower();cat=cat.set_index('c')
starts=pd.read_parquet(E/'data/new_processed/grid_game_starts/grid_game_windows.parquet').drop_duplicates('condition_id');starts['c']=starts.condition_id.astype(str).str.lower();start_by=starts.set_index('c')['spawn_at'].to_dict()
out=[]
for x in sample.itertuples():
 d=E/'data/trader'/x.dir;mp=d/'match.json';sp=d/'session.jsonl';m=json.loads(mp.read_text());c=str(m['market']['condition_id']).lower();r=cat.loc[c]
 if c not in start_by:continue
 pauses=json.loads(r.pauses_json or '[]');D=get_paused_seconds_before(pauses,0)
 if D<20 or D>500:continue
 grid_horn=get_horn_datetime(parse_utc(start_by[c]),pauses);archive_horn=parse_utc(m['horn_at_utc']);horn_diff=(archive_horn-grid_horn).total_seconds()
 market=m['market'];reducer=GridFrameReducer(m['map_number'],market['outcome_0_name'],market['outcome_1_name'],GAME_PROFILES['dota'])
 ap=d/'grid_state.jsonl.gz';ap=ap if ap.exists() else d/'grid_state.jsonl'
 events=list(replay_grid_records(iter_grid_archive_records(ap),reducer));chosen=[e for e in events if 60<=e.snapshot.second<=180]
 if not chosen:continue
 e=min(chosen,key=lambda e:abs(e.snapshot.second-120));S=int(e.snapshot.second);M=S+10;mid=int(m.get('steam_match_id') or r.match_id)
 cp=market_seconds_cache_path(mid)
 if not cp.exists():continue
 cache=pd.read_parquet(cp,columns=['second','state_ts_us','market_status','market_p_radiant']);cr=cache[cache.second==M]
 if cr.empty:continue
 cr=cr.iloc[0];cached_ts=pd.Timestamp(int(cr.state_ts_us),unit='us',tz='UTC')
 expected=get_state_available_ts(horn=grid_horn,second=M,pauses=pauses)
 skew=(cached_ts-expected).total_seconds()
 live_sig=[]
 with open_maybe_gz(sp) as f:
  for line in f:
   try:s=json.loads(line)
   except:continue
   if s.get('kind')=='signal' and s.get('reason')=='model' and s.get('second')==S and s.get('market_p_radiant') is not None:
    live_sig.append(float(s['market_p_radiant']))
 out.append({'condition_id':c,'match_id':mid,'dir':x.dir,'D':D,'horn_diff':horn_diff,'horn_residual':horn_diff+D,'second':S,'market_second':M,
  'cache_status':cr.market_status,'cache_state_ts':cached_ts.isoformat(),'live_feed_ts':e.received_at_utc,'cache_minus_correct_state_sec':skew,
  'cache_p':cr.market_p_radiant,'live_p_same_second':float(np.median(live_sig)) if live_sig else None,
  'market_p_absdiff':abs(float(cr.market_p_radiant)-float(np.median(live_sig))) if live_sig and pd.notna(cr.market_p_radiant) else None})
df=pd.DataFrame(out);df.to_csv(R/'horn_market_alignment.csv',index=False)
df['affected']=df.horn_residual.abs()<=5
print('ROWS_WITH_START_AND_CACHE',len(df),'direct_horn_offset',int(df.affected.sum()),'other pause maps',int((~df.affected).sum()))
for label,g in [('direct_archive_early',df[df.affected]),('no_D_horn_offset',df[~df.affected])]:
 print(label,'n',len(g),'D p50',g.D.median(),'cache ts skew p10/p50/p90',g.cache_minus_correct_state_sec.quantile([.1,.5,.9]).to_dict(),'market-p abs diff p50/p90',g.market_p_absdiff.quantile([.5,.9]).to_dict(),'status',g.cache_status.value_counts().to_dict())
