import gzip, glob, json, os
from collections import Counter, defaultdict
E='/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader'
root=E+'/data/trader'
files=glob.glob(root+'/*/match.json')
print('archive_dirs',len(files))
bygame=Counter(); bymodel=Counter(); bymode=Counter(); sources=Counter(); examples={}; latest=[]
for path in files:
    d=os.path.dirname(path)
    try:
        m=json.load(open(path))
    except Exception as e:
        print('BAD_MATCH',path,e); continue
    game=m.get('game','dota'); bygame[game]+=1
    sess=os.path.join(d,'session.jsonl')
    if not os.path.exists(sess): continue
    for line in open(sess):
        try:r=json.loads(line)
        except:continue
        if r.get('kind')=='session_start':
            model=(r.get('model') or {}).get('name'); mode=r.get('execution_mode')
            bymodel[str(model)]+=1; bymode[str(mode)]+=1
            # keep fields used for source and live config discovery
            examples.setdefault((game, str(model)), {'match':m,'session_start':r,'dir':os.path.basename(d)})
        if r.get('kind') in ('fill','session_end'):
            ts=r.get('timestamp') or r.get('ts') or r.get('time')
            if ts: latest.append((str(ts),os.path.basename(d),r.get('kind')))
print('bygame',bygame)
print('bymodel',bymodel)
print('bymode',bymode)
print('latest_event_ts',sorted(latest)[-10:])
for k,v in list(examples.items())[:12]:
    print('EXAMPLE',k,json.dumps(v,default=str)[:3500])
# trace kind/key samples from a few present files
for d in glob.glob(root+'/*/'):
    mpath=d+'match.json'
    try:m=json.load(open(mpath))
    except:continue
    g=m.get('game','dota')
    if g not in ('dota',None):continue
    for name in ('core_trace.jsonl','core_trace.jsonl.gz'):
        p=d+name
        if os.path.exists(p):
            op=gzip.open if p.endswith('.gz') else open
            c=Counter(); sample={}
            with op(p,'rt') as f:
                for i,line in enumerate(f):
                    try:r=json.loads(line)
                    except:continue
                    kind=r.get('kind') or r.get('event') or r.get('type')
                    c[str(kind)]+=1; sample.setdefault(str(kind),r)
                    if i>5000:break
            print('TRACE',os.path.basename(d),c)
            for k,r in sample.items(): print('TRACE_SAMPLE',k,json.dumps(r,default=str)[:4500])
            break
