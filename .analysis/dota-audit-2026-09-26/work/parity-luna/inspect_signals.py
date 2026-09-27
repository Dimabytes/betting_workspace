import json
from collections import Counter, defaultdict
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'esports-trader' / 'src'))
from shared.utils.jsonl_io import open_maybe_gz
E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
root=E/'data/trader'
source_counts=Counter(); dates=defaultdict(list); model_counts=defaultdict(Counter); policy_counts=defaultdict(Counter)
rows=[]
for d in root.iterdir():
    mp=d/'match.json'; sp=d/'session.jsonl'
    if not (mp.exists() and sp.exists()): continue
    try:m=json.loads(mp.read_text())
    except:continue
    if m.get('game','dota') not in ('dota',None): continue
    src=m.get('feed_source','unknown'); source_counts[src]+=1
    dt=m.get('joined_at_utc',''); dates[src].append(dt)
    parsed=[]
    with open_maybe_gz(sp) as f:
        for line in f:
            try:r=json.loads(line)
            except:continue
            parsed.append(r)
    ss=next((r for r in parsed if r.get('kind')=='session_start'),{})
    model=(ss.get('model') or {}).get('name','?'); model_counts[src][model]+=1
    trace=next((r for r in [m] if r),{})
    header={}
    trace_path=d/'core_trace.jsonl'
    if not trace_path.exists() and not trace_path.with_name(trace_path.name+'.gz').exists():
        continue
    with open_maybe_gz(trace_path) as f:
        for line in f:
            try:r=json.loads(line)
            except:continue
            if r.get('kind')=='header': header=r; break
    pol=header.get('policy',{})
    policy_sig=(pol.get('version'),pol.get('level_usdc'),pol.get('min_abs_delta'),pol.get('min_entry_price'),pol.get('max_entry_price'),pol.get('max_entry_spread_ticks'),pol.get('buy_cutoff_second'))
    policy_counts[src][policy_sig]+=1
    rows.append((dt,src,d.name,m,ss,parsed,header))
print('source_counts',dict(source_counts))
for s in sorted(source_counts):
 print('source',s,'oldest',min(dates[s]),'latest',max(dates[s]),'models',dict(model_counts[s]),'policies',dict(policy_counts[s]))
for dt,src,name,m,ss,parsed,header in sorted(rows,reverse=True)[:6]:
 print('\nRECENT',dt,src,name,'mid',m.get('match_id'),'cond',m.get('market',{}).get('condition_id'),'winner',m.get('final',{}).get('winner'))
 print('start',json.dumps(ss)[:1800])
 print('trace_header_policy',json.dumps(header.get('policy'))[:1200])
 counts=Counter(r.get('kind') for r in parsed)
 print('session_kinds',counts)
 for r in parsed:
  if r.get('kind') in ('signal','prediction','frame','decision','fill','session_end'):
   print('session_sample',r.get('kind'),json.dumps(r)[:2200])
 # Only inspect trace rows matching target to keep output compact.
 d=root/name
 wanted=Counter(); samples={}
 with open_maybe_gz(d/'core_trace.jsonl') as f:
  for i,line in enumerate(f):
   try:r=json.loads(line)
   except:continue
   ev=r.get('event',{})
   et=ev.get('type') if isinstance(ev,dict) else None
   wanted[et]+=1
   s=json.dumps(r)
   if any(k in s for k in ('predicted_delta','radiant_nw_adv','market_p_radiant','signal_age_seconds')) and et not in samples:
    samples[et]=r
   if i>150000: break
 print('trace_event_types',wanted.most_common(18))
 for et,r in list(samples.items())[:5]: print('trace_feature_sample',et,json.dumps(r)[:3000])
