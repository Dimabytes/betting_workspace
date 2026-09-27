import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, '/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src')
from shared.utils.jsonl_io import open_maybe_gz
from shared.utils.gbm import FEATURE_COLUMNS, load_predictor
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer, replay_grid_records
from trader.game_profile import GAME_PROFILES
from trader.model_server import _build_feature_values

E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
R=Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')
d=E/'data/trader/grid-2996014-m2'
m=json.loads((d/'match.json').read_text()); market=m['market']
ss=[]
with open_maybe_gz(d/'session.jsonl') as f:
 for line in f:
  try:r=json.loads(line)
  except:continue
  if r.get('kind')=='session_start':ss.append(r)
  if r.get('kind')=='signal' and r.get('reason')=='model' and r.get('market_p_radiant') is not None: ss.append(r)
start=next(r for r in ss if r.get('kind')=='session_start')
name=start['model']['name']
dirs=[]
for p in (E/'data/new_model').rglob('model.json'):
 try:meta=json.loads(p.read_text())
 except:continue
 if meta.get('name')==name:dirs.append(p.parent)
print('MODEL',name,dirs)
from shared.utils.gbm import load_booster
meta=json.loads((dirs[0]/'model.json').read_text())
prod=load_booster(dirs[0]/'model.txt') if (dirs[0]/'model.txt').exists() else load_predictor(dirs[0])
research=load_predictor(E/'data/new_model/research')
val=pd.read_parquet(E/'data/new_processed/dataset/validation_dataset.parquet',columns=['match_id','condition_id','second','market_radiant_prior'])
prior=float(val[val.condition_id==market['condition_id'].lower()].market_radiant_prior.iloc[0])
reducer=GridFrameReducer(m['map_number'],market['outcome_0_name'],market['outcome_1_name'],GAME_PROFILES['dota'])
events=list(replay_grid_records(iter_grid_archive_records(d/'grid_state.jsonl.gz'),reducer))
bysec={}
for e in events: bysec.setdefault(e.snapshot.second,[]).append(e)
for r in [x for x in ss if x.get('kind')=='signal'][:5]:
 sec=int(r['second']); p=float(r['market_p_radiant']); prior=float(r['market_radiant_prior']); live_delta=float(r['radiant_fair'])-p
 candidates=[]
 for e in bysec.get(sec,[]):
  vals=_build_feature_values(e.snapshot,p,prior)
  if hasattr(prod,'predict_one_thread'):
   pred=float(prod.predict_one_thread(np.array([[vals[c] for c in FEATURE_COLUMNS]],dtype=np.float64))[0])
  else:
   pred=float(prod.predict(np.array([[vals[c] for c in FEATURE_COLUMNS]],dtype=np.float64),num_threads=1)[0])
  candidates.append((abs(pred-live_delta),pred,e.snapshot))
 print('SIGNAL',sec,'logged_delta',live_delta,'same-second states',len(candidates),'closest production delta and snapshot',[(x[1],x[2].radiant_nw_adv,x[2].radiant_xp_adv,x[2].deaths_radiant,x[2].deaths_dire) for x in sorted(candidates,key=lambda x:x[0])[:3]])

q=pd.read_parquet(E/'data/backtests/dota_maker/LIVE/seed0/quote_events.parquet',columns=['match_id','ts_ns','kind','predicted_delta','fair'])
q=q[(q.match_id==int(m['steam_match_id'])) & (q.predicted_delta.abs()>0)].copy()
q['p']=q.fair-q.predicted_delta
q=q[q.p.between(0,1)]
q=q.sort_values('ts_ns')
print('BT qevents',len(q),'game event ticks',len(events),'prior',prior)
for e in events:
 ts=pd.Timestamp(e.received_at_utc).value
 near=q[(q.ts_ns>=ts-100_000_000)&(q.ts_ns<=ts+2_000_000_000)]
 if not len(near):continue
 vals=_build_feature_values(e.snapshot,float(near.iloc[0].p),prior)
 arr=np.array([[vals[c] for c in FEATURE_COLUMNS]],dtype=np.float64)
 rp=float(research.predict_one_thread(arr)[0])
 near=near.copy();near['err']=abs(near.predicted_delta-rp)
 near['tdiff']=abs(near.ts_ns-(ts+85_000_000))
 b=near.sort_values(['err','tdiff']).iloc[0]
 t=near.sort_values('tdiff').iloc[0]
 if e.snapshot.second in (55,82,100,150,200,300): print('BT_ALIGN',e.snapshot.second,'t',e.received_at_utc,'cand',len(near),'nearest_dt_ms',float(t.tdiff)/1e6,'nearest_delta_err',float(t.err),'exact_delta_err',float(b.err),'chosen',int(b.ts_ns),'bt delta',b.predicted_delta,'research on chosen p',float(research.predict_one_thread(np.array([[_build_feature_values(e.snapshot,float(b.p),prior)[c] for c in FEATURE_COLUMNS]],dtype=np.float64))[0]))
