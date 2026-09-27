import json
from pathlib import Path
import pandas as pd
import sys
sys.path.insert(0, '/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src')
from shared.utils.jsonl_io import open_maybe_gz

E=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
R=Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')
j=pd.read_csv(R/'same_map_live_backtest.csv')
for _,row in j[(j.source=='grid')&(j.live_buy>0)&(j.bt_buy>0)].iterrows():
    d=E/'data/trader'/str(row.dir)
    m=json.loads((d/'match.json').read_text())
    out={'dir':d.name,'match':m}
    for name in ('session.jsonl','core_trace.jsonl','grid_state.jsonl','oddin_state.jsonl'):
        p=d/name
        if not p.exists():p=p.with_name(p.name+'.gz')
        if not p.exists():continue
        events=[]
        with open_maybe_gz(p) as f:
            for line in f:
                try:r=json.loads(line)
                except:continue
                events.append(r)
                if len(events)>=20:break
        if name=='core_trace.jsonl':
            out[name]=events[:3]
        else: out[name]=events[:5]
    print(json.dumps(out,default=str)[:9000])
    break

model=E/'data/new_model/production/model.json'
print('MODEL_METADATA',model.read_text())
