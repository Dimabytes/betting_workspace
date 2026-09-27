import json,sys
from pathlib import Path
import numpy as np,pandas as pd
sys.path.insert(0,'/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src')
from shared.utils.jsonl_io import open_maybe_gz
from shared.utils.match_time import get_paused_seconds_before
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer,replay_grid_records
from trader.game_profile import GAME_PROFILES

E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
catalog=pd.read_parquet(E/'data/new_processed/match_catalog/match_catalog.parquet',columns=['condition_id','horn_at','horn_source','pauses_json'])
cat={str(r.condition_id).lower():r._asdict() for r in catalog.itertuples(index=False)}
out=[]
for d in (E/'data/trader').iterdir():
 mp=d/'match.json'; ss=d/'session.jsonl'
 archive=d/'grid_state.jsonl.gz'
 if not archive.exists():archive=d/'grid_state.jsonl'
 if not mp.exists() or not ss.exists() or not archive.exists():continue
 try:m=json.loads(mp.read_text())
 except:continue
 if m.get('game','dota') not in ('dota',None):continue
 condition=str((m.get('market') or {}).get('condition_id') or '').lower(); c=cat.get(condition)
 if not c or c['horn_source']!='archive':continue
 try:pauses=json.loads(c['pauses_json'] or '[]')
 except:pauses=[]
 D=get_paused_seconds_before(pauses,0)
 if D<20 or D>500:continue
 market=m['market']; reducer=GridFrameReducer(m['map_number'],market['outcome_0_name'],market['outcome_1_name'],GAME_PROFILES['dota'])
 events=list(replay_grid_records(iter_grid_archive_records(archive),reducer))
 if not events:continue
 chosen=[e for e in events if 60<=e.snapshot.second<=180]
 if not chosen:continue
 e=min(chosen,key=lambda e:abs(e.snapshot.second-120))
 horn=pd.Timestamp(m['horn_at_utc']); rec=pd.Timestamp(e.received_at_utc)
 elapsed=(rec-horn).total_seconds(); post=get_paused_seconds_before(pauses,int(e.snapshot.second))-D
 off=elapsed-D-post-e.snapshot.second
 out.append({'dir':d.name,'source':m.get('feed_source'),'condition':condition,'second':e.snapshot.second,'D':D,'post_pause':post,'elapsed_archive_horn':elapsed,'offset_after_pause_correction':off,'grid_delay_s':m.get('grid_delay_s')})
df=pd.DataFrame(out)
print('AFFECTED_GRID',len(df),'D>=20 archive-horn sessions')
print('D quantiles',df.D.quantile([.5,.9,1]).to_dict())
print('offset after corrected horn vs second',df.offset_after_pause_correction.quantile([0,.1,.5,.9,1]).to_dict())
print('reported grid delay',df.grid_delay_s.quantile([0,.1,.5,.9,1]).to_dict())
df['residual']=df.offset_after_pause_correction-df.grid_delay_s.astype(float)
print('offset minus grid_delay',df.residual.quantile([0,.1,.5,.9,1]).to_dict(),'median abs',df.residual.abs().median())
print(df.sort_values('D',ascending=False).head(8).to_string(index=False))
outpath=Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna/horn_clock_samples.csv')
df.to_csv(outpath,index=False)
