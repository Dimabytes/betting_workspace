import json
from pathlib import Path
import sys
sys.path.insert(0, '/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src')
from shared.utils.jsonl_io import open_maybe_gz
E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
d=E/'data/trader/grid-2996014-m2'
for fname in ('core_trace.jsonl','session.jsonl'):
 p=d/fname
 if not p.exists():p=p.with_name(p.name+'.gz')
 print('\nFILE',fname,p)
 n=0
 with open_maybe_gz(p) as f:
  for line in f:
   try:r=json.loads(line)
   except:continue
   ev=r.get('event')
   typ=ev.get('type') if isinstance(ev,dict) else r.get('kind')
   if typ in ('SignalUpdate','ClockUpdate','header','trace_header') or r.get('kind') in ('signal','session_start'):
    if isinstance(ev,dict) and typ=='SignalUpdate':
     ev={k:v for k,v in ev.items() if k in ('type','signal','now_ns')}
    print(json.dumps({k:v for k,v in r.items() if k in ('kind','schema_version','opened_now_ns','opened_wall_s','event','sequence','ts_ns')},default=str)[:1600])
    n+=1
    if n>=8:break
print('\nMODEL IDENTITY FILE')
for p in d.glob('core_trace*'):
 print(p)
