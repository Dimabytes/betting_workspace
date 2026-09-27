"""Replay the 49 complete, both-traded GRID maps in the Sep 1-19 parity cohort."""
import json, math, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, '/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src')
from shared.utils.jsonl_io import open_maybe_gz
from shared.utils.gbm import FEATURE_COLUMNS, load_booster, load_predictor
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer, replay_grid_records
from trader.game_profile import GAME_PROFILES
from trader.model_server import _build_feature_values

E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
R=Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')
trader=E/'data/trader'

def model_from_dir(d):
    meta=json.loads((d/'model.json').read_text())
    predictor=load_predictor(d) if meta.get('members') else load_booster(d/'model.txt')
    return meta['features'],predictor

def predict(model, matrix):
    if hasattr(model,'predict_one_thread'):
        return np.asarray(model.predict_one_thread(matrix),dtype=float)
    return np.asarray(model.predict(matrix,num_threads=1),dtype=float)

def qtiles(x):
    a=np.asarray([float(v) for v in x if pd.notna(v)],dtype=float)
    return {'n':len(a),'p50':float(np.quantile(a,.5)) if len(a) else None,
            'p90':float(np.quantile(a,.9)) if len(a) else None,
            'p95':float(np.quantile(a,.95)) if len(a) else None,'mean':float(a.mean()) if len(a) else None}

j=pd.read_csv(R/'same_map_live_backtest.csv')
j=j[(j.source=='grid')&(j.period=='2026-09-01_to_19')&(j.live_buy>0)&(j.bt_buy>0)&j.live_pnl.notna()].copy()
print('COHORT',len(j),'maps')
btids=set(j.match_id_bt.astype(int))
q=pd.read_parquet(E/'data/backtests/dota_maker/LIVE/seed0/quote_events.parquet',columns=['match_id','ts_ns','kind','predicted_delta','fair','book_p_radiant'])
q=q[q.match_id.isin(btids)].copy()
q=q.sort_values('ts_ns').drop_duplicates(['match_id','ts_ns'],keep='last')
# Quote-event `fair` is token-side; `book_p_radiant` is the normalized model anchor.
q['model_p']=q.book_p_radiant
by_q={int(mid):g.reset_index(drop=True) for mid,g in q.groupby('match_id',sort=False)}
qtime={mid:g.ts_ns.to_numpy(dtype=np.int64) for mid,g in by_q.items()}
gf=pd.read_parquet(E/'data/new_processed/dataset/game_features.parquet',columns=['match_id','game_second','market_radiant_prior'])
gf=gf[gf.match_id.isin(btids)]
prior_by={(int(r.match_id),int(r.game_second)):float(r.market_radiant_prior) for r in gf.itertuples()}
valcols=['match_id','condition_id','second','market_p_radiant','market_radiant_prior',*FEATURE_COLUMNS[1:10]]
val=pd.read_parquet(E/'data/new_processed/dataset/validation_dataset.parquet',columns=list(dict.fromkeys(valcols)))
val=val[val.match_id.isin(btids)]
val_by={(int(r.match_id),int(r.second)):r._asdict() for r in val.itertuples(index=False)}
research_models={
 'grid':(FEATURE_COLUMNS,load_predictor(E/'data/new_model/research')),
}
model_dirs={}
for p in (E/'data/new_model').rglob('model.json'):
 try:m=json.loads(p.read_text())
 except:continue
 model_dirs.setdefault(m.get('name'),[]).append(p.parent)
live_model_cache={}
allrows=[]
input_delta_rows=[]
for _,row in j.iterrows():
    d=trader/str(row.dir)
    match=json.loads((d/'match.json').read_text()); market=match['market']; mid=int(row.match_id_bt)
    sessions=[]
    with open_maybe_gz(d/'session.jsonl') as f:
      for line in f:
       try:x=json.loads(line)
       except:continue
       if x.get('kind')=='session_start': start=x
       elif x.get('kind')=='signal' and x.get('reason')=='model' and x.get('market_p_radiant') is not None and x.get('radiant_fair') is not None:
        sessions.append(x)
    model_name=start['model']['name']
    if model_name not in live_model_cache:
      dirs=model_dirs.get(model_name,[])
      if not dirs: raise RuntimeError(f'no model artifacts for live model {model_name}')
      if len(dirs)>1:
       dirs=sorted(dirs,key=lambda p:(('production-noxp' in str(p)) != ('radiant_xp_adv' not in json.loads((p/'model.json').read_text()).get('features',[])),len(str(p))))
      live_model_cache[model_name]=model_from_dir(dirs[0])
    live_features,live_model=live_model_cache[model_name]
    live_by_sec=defaultdict(list)
    for s in sessions:
      try:
       p=float(s['market_p_radiant']); prior=float(s['market_radiant_prior']); delta=float(s['radiant_fair'])-p
       live_by_sec[int(s['second'])].append((delta,p,prior))
      except (TypeError,ValueError,KeyError):continue
    live_by_sec={sec:np.median(np.asarray(values,dtype=float),axis=0) for sec,values in live_by_sec.items()}
    reducer=GridFrameReducer(match['map_number'],market['outcome_0_name'],market['outcome_1_name'],GAME_PROFILES['dota'])
    archive=d/'grid_state.jsonl.gz'
    if not archive.exists():archive=d/'grid_state.jsonl'
    events=list(replay_grid_records(iter_grid_archive_records(archive),reducer))
    qg=by_q.get(mid)
    if qg is None:continue
    tarr=qtime[mid]
    rows_by_second={}
    for e in events:
      sec=int(e.snapshot.second)
      if sec<0 or sec>=480:continue
      key=(mid,sec)
      prior=prior_by.get(key)
      if prior is None:continue
      ts=int(pd.Timestamp(e.received_at_utc).value)
      lo=int(np.searchsorted(tarr,ts-250_000_000,'left'))
      hi=int(np.searchsorted(tarr,ts+250_000_000,'right'))
      if hi<=lo:continue
      cand=qg.iloc[lo:hi].copy()
      cand=cand[np.isfinite(cand.predicted_delta)&np.isfinite(cand.model_p)&cand.model_p.between(0,1)]
      cand=cand[cand.predicted_delta.abs()>1e-10]
      if cand.empty:continue
      cmat=np.asarray([[_build_feature_values(e.snapshot,float(p),prior)[c] for c in FEATURE_COLUMNS]
                       for p in cand.model_p],dtype=float)
      cpred=predict(research_models['grid'][1],cmat)
      errs=np.abs(cpred-cand.predicted_delta.to_numpy(dtype=float))
      qidx=int(cand.iloc[int(np.argmin(errs))].name)
      br=qg.loc[qidx]
      bdelta=float(br.predicted_delta); bp=float(br.model_p); dt=abs(int(br.ts_ns)-ts)
      vals=_build_feature_values(e.snapshot,bp,prior)
      x=np.asarray([vals[c] for c in FEATURE_COLUMNS],dtype=float)
      # Keep one latest state per apparent game second; this matches session rows, which omit receive time.
      rows_by_second[sec]=(e,vals,x,bdelta,bp,dt,prior)
    if not rows_by_second:continue
    for sec,(e,vals,x,bdelta,bp,dt,prior) in rows_by_second.items():
      vv=val_by.get((mid,sec+10))
      if vv is None:continue
      vvals={c:(float(vv[c]) if c not in ('second',) else float(sec)) for c in FEATURE_COLUMNS}
      vvals['second']=float(sec)
      vmat=np.asarray([vvals[c] for c in FEATURE_COLUMNS],dtype=float)
      live_vals=_build_feature_values(e.snapshot,float(live_by_sec[sec][1]),float(live_by_sec[sec][2])) if sec in live_by_sec else None
      live_logged=float(live_by_sec[sec][0]) if sec in live_by_sec else np.nan
      live_p=float(live_by_sec[sec][1]) if sec in live_by_sec else np.nan
      live_prior=float(live_by_sec[sec][2]) if sec in live_by_sec else np.nan
      rowrec={'condition_id':str(row.condition_id),'mid':mid,'source':row.source,'second':sec,
        'bt_delta':bdelta,'bt_p':bp,'prior_bt':prior,'live_delta':live_logged,'live_p':live_p,'live_prior':live_prior,
        'validation_p':float(vvals['market_p_radiant']),'validation_prior':float(vvals['market_radiant_prior']),
        'bt_live_model_delta':np.nan,'live_repro_delta':np.nan,'live_repro_abs_err':np.nan,
        'research_live_input_delta':np.nan,'validation_delta':np.nan,'bt_val_abs_err':np.nan,'q_dt_ms':dt/1e6}
      for c in FEATURE_COLUMNS:
        rowrec['absdiff_'+c]=abs(float((live_vals[c] if live_vals is not None else vals[c]))-float(vvals[c]))
      samebt_x=np.asarray([vals[c] for c in live_features],dtype=float) if live_vals is not None else None
      live_x=np.asarray([live_vals[c] for c in live_features],dtype=float) if live_vals is not None else None
      live_full_x=np.asarray([live_vals[c] for c in FEATURE_COLUMNS],dtype=float) if live_vals is not None else None
      allrows.append((rowrec,x,vmat,live_x,samebt_x,model_name,live_features,live_full_x))

print('INPUT_ROWS',len(allrows),'live_models',sorted(set(r[5] for r in allrows)))
if not allrows:raise SystemExit('no matched signal rows')
X=np.vstack([x for _,x,_,_,_,_,_,_ in allrows]); V=np.vstack([v for _,_,v,_,_,_,_,_ in allrows])
research=research_models['grid'][1]
bt_repro=predict(research,X)
val_pred=predict(research,V)
liveidx=[i for i,r in enumerate(allrows) if r[7] is not None]
liveX=np.vstack([allrows[i][7] for i in liveidx])
research_live=predict(research,liveX)
for i,(rec,_,_,_,_,_,_,_) in enumerate(allrows):
 rec['bt_repro_delta']=float(bt_repro[i]); rec['bt_repro_abs_err']=abs(float(bt_repro[i])-rec['bt_delta'])
 rec['validation_delta']=float(val_pred[i]); rec['bt_val_abs_err']=abs(float(bt_repro[i])-float(val_pred[i]))
for i,v in zip(liveidx,research_live,strict=True):allrows[i][0]['research_live_input_delta']=float(v)
for model_name in sorted(set(r[5] for r in allrows)):
  subset=[(i,r) for i,r in enumerate(allrows) if r[5]==model_name and r[3] is not None]
  if not subset:continue
  features,model=live_model_cache[model_name]
  lx=np.vstack([r[3] for _,r in subset]); sx=np.vstack([r[4] for _,r in subset])
  live_pred=predict(model,lx); same_bt_pred=predict(model,sx)
  for k,(i,r) in enumerate(subset):
   rec=r[0]; rec['live_repro_delta']=float(live_pred[k]); rec['live_repro_abs_err']=abs(float(live_pred[k])-rec['live_delta'])
   rec['bt_live_model_delta']=float(same_bt_pred[k])
 # Source-only counterfactual: replace one live/backtest input with the validation row value.
v=np.tile(liveX,(len(FEATURE_COLUMNS),1))
Vlive=V[liveidx]
for fi,c in enumerate(FEATURE_COLUMNS):v[fi*len(liveX):(fi+1)*len(liveX),fi]=Vlive[:,fi]
counter=predict(research,v).reshape(len(FEATURE_COLUMNS),len(liveX))
feature_impact={c:qtiles(abs(counter[i]-research_live)) for i,c in enumerate(FEATURE_COLUMNS)}
df=pd.DataFrame([r[0] for r in allrows])
out=R/'signal_parity_grid_20260901_19.csv';df.to_csv(out,index=False)
print('SAMPLES',len(df),'maps',df.condition_id.nunique(),'seconds',df.second.min(),df.second.max(),'at_480plus',int((df.second>=480).sum()))
print('BT_REPLAY_ABS_ERR',qtiles(df.bt_repro_abs_err))
print('LIVE_SIGNAL_REPLAY_ABS_ERR',qtiles(df.live_repro_abs_err))
print('OBSERVED_DELTA_GAP_BT_minus_LIVE',qtiles(df.bt_delta-df.live_delta))
print('MODEL_GAP_BT_minus_LIVE_MODEL_SAME_INPUT',qtiles(df.bt_delta-df.bt_live_model_delta))
print('MODEL_VERSION_GAP_RESEARCH_vs_LIVE_ON_EXACT_LIVE_INPUT',qtiles(df.research_live_input_delta-df.live_delta))
print('INPUT_SOURCE_GAP_RESEARCH_LIVE_vs_VALIDATION',qtiles(df.research_live_input_delta-df.validation_delta))
print('VALIDATION_PRED_GAP_BT_minus_VAL',qtiles(df.bt_delta-df.validation_delta))
for gate,label in ((.02,'entry_abs_delta_gate'),):
  elig=df[df.second<480].dropna(subset=['live_delta'])
  flip=((elig.bt_delta.abs()>=gate)!=(elig.live_delta.abs()>=gate))
  direction=(np.sign(elig.bt_delta)!=np.sign(elig.live_delta))
  print('LIVE_VS_BT_THRESHOLD_ONLY',label,'n',len(elig),'flip_n',int(flip.sum()),'flip_pct',float(flip.mean()) if len(elig) else None,'direction_flip_n',int(direction.sum()),'pct',float(direction.mean()) if len(elig) else None)
  x=elig.dropna(subset=['bt_live_model_delta'])
  f=((x.bt_delta.abs()>=gate)!=(x.bt_live_model_delta.abs()>=gate))
  d=(np.sign(x.bt_delta)!=np.sign(x.bt_live_model_delta))
  print('BT_VS_LIVE_MODEL_SAME_INPUT_THRESHOLD_ONLY',label,'n',len(x),'flip_n',int(f.sum()),'flip_pct',float(f.mean()) if len(x) else None,'direction_flip_n',int(d.sum()),'pct',float(d.mean()) if len(x) else None)
  x=elig.dropna(subset=['research_live_input_delta'])
  f=((x.research_live_input_delta.abs()>=gate)!=(x.live_delta.abs()>=gate))
  d=(np.sign(x.research_live_input_delta)!=np.sign(x.live_delta))
  print('RESEARCH_VS_LIVE_ON_EXACT_LIVE_INPUT_THRESHOLD_ONLY',label,'n',len(x),'flip_n',int(f.sum()),'flip_pct',float(f.mean()) if len(x) else None,'direction_flip_n',int(d.sum()),'pct',float(d.mean()) if len(x) else None)
print('FEATURE_INPUT_ABSDIFF')
for c in FEATURE_COLUMNS:print(c,qtiles(df['absdiff_'+c]))
print('COUNTERFACTUAL_PRED_IMPACT')
for c in FEATURE_COLUMNS:print(c,feature_impact[c])
