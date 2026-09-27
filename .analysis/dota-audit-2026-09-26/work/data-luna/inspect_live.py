from pathlib import Path
from collections import Counter
import gzip,json
root=Path('data/trader')
shown=0
for d in root.iterdir():
    mp=d/'match.json'
    if not mp.exists(): continue
    try: match=json.loads(mp.read_text())
    except Exception: continue
    if str(match.get('game','')).lower()!='dota': continue
    s=d/'session.jsonl'
    session_kinds=Counter(); session_samples={}
    if s.exists():
        with s.open() as f:
            for line in f:
                try: rec=json.loads(line)
                except Exception: continue
                k=rec.get('kind','?'); session_kinds[k]+=1
                if k not in session_samples: session_samples[k]=rec
    tp=d/'core_trace.jsonl'
    if not tp.exists(): tp=d/'core_trace.jsonl.gz'
    trace_types=Counter(); trace_samples={}
    if tp.exists():
        op=gzip.open if tp.suffix=='.gz' else open
        with op(tp,'rt') as f:
            for line in f:
                try: rec=json.loads(line)
                except Exception: continue
                ev=rec.get('event') or {}
                k=ev.get('type',rec.get('kind','?'))
                trace_types[str(k)]+=1
                if str(k) not in trace_samples: trace_samples[str(k)]=rec
    print('MATCH',d.name,match.get('feed_source'),match.get('model'),match.get('market',{}).get('market_slug'))
    print('SESSION_KINDS',session_kinds)
    print('SESSION_SAMPLES',{k:v for k,v in session_samples.items() if k in ('signal','features','feature','state','model')})
    print('TRACE_TYPES',trace_types.most_common(30))
    for k,v in trace_samples.items():
        if any(x in k.lower() for x in ('feature','model','infer','signal')):
            print('TRACE_SAMPLE',k,str(v)[:3000])
    shown+=1
    if shown>=5: break
