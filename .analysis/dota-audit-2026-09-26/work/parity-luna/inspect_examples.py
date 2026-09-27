import json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, '/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src')
from shared.utils.jsonl_io import open_maybe_gz
E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader'); root=E/'data/trader'
rows=[]; source=Counter()
for d in root.iterdir():
 mp=d/'match.json'; sp=d/'session.jsonl'
 if not (mp.exists() and sp.exists()):continue
 m=json.loads(mp.read_text())
 if m.get('game','dota') not in ('dota',None):continue
 src=m.get('feed_source','unknown'); source[src]+=1
 rows.append((m.get('joined_at_utc',''),src,d,m))
print('all Dota source values',source)
for src in sorted(source):
 candidates=[x for x in rows if x[1]==src and ((x[2]/'core_trace.jsonl').exists() or (x[2]/'core_trace.jsonl.gz').exists())]
 if not candidates:continue
 dt,_,d,m=max(candidates)
 print('\nARCHIVE',src,dt,d.name,'match',m.get('match_id'),'cond',m.get('market',{}).get('condition_id'),'final',m.get('final'))
 sess=[]
 with open_maybe_gz(d/'session.jsonl') as f:
  for line in f:
   try:r=json.loads(line)
   except:continue
   if r.get('kind') in ('session_start','signal','fill','session_end'):
    sess.append(r)
 print('session_start',next((r for r in sess if r.get('kind')=='session_start'),{}))
 for r in sess:
  if r.get('kind')=='signal' and r.get('second',-999)>=0 and r.get('market_p_radiant') is not None:
   print('live_signal_example keys=',list(r.keys()),'row=',r);break
 for r in sess:
  if r.get('kind')=='fill': print('fill',r)
 for r in sess:
  if r.get('kind')=='session_end': print('session_end',r)
 tp=d/'core_trace.jsonl'
 with open_maybe_gz(tp) as f:
  for line in f:
   try:r=json.loads(line)
   except:continue
   ev=r.get('event',{})
   if isinstance(ev,dict) and ev.get('type')=='SignalUpdate' and isinstance(ev.get('signal'),dict):
    print('trace_signal keys',list(ev.get('signal',{}).keys()),'trace row',r);break
 for n in ('state.jsonl','grid_state.jsonl','oddin_state.jsonl'):
  p=d/n
  if not p.exists() and not p.with_name(p.name+'.gz').exists():continue
  print('feed archive',n)
  printed=False
  with open_maybe_gz(p) as f:
   for line in f:
    try:r=json.loads(line)
    except:continue
    if not printed:
     print('state first keys',list(r.keys()),'row',r);printed=True
    sec=r.get('second',r.get('game_second',r.get('gameSecond')))
    if isinstance(sec,(int,float)) and 250<=sec<=350:
     print('state around 300 keys',list(r.keys()),'row',r);break
