"""Independent capital reconstruction from raw saved order events and executions."""
from pathlib import Path
import json
import pandas as pd

ROOT=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/backtests')

def calculate(game,variant,seed,filter_failed):
    path=ROOT / f'{game}_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-{variant}/seed{seed}'
    r=pd.read_parquet(path/'results.parquet')
    f=pd.read_parquet(path/'fills.parquet',columns=['match_id','order_id','ts_ns','side','price','quantity'])
    q=pd.read_parquet(path/'quote_events.parquet',columns=['match_id','order_id','ts_ns','side','kind','price','quantity'],filters=[('side','==','BUY'),('kind','in',['submitted','cancel_ack','rejected','denied','expired'])])
    a=json.loads((path/'summary.json').read_text())['arms'][0]
    r=r[~r.terminated_early]
    f=f[f.match_id.isin(r.match_id)]
    if filter_failed:
        q=q[q.match_id.isin(r.match_id)]
    events=[]
    for row in q.itertuples():
        priority=1 if row.kind=='submitted' else 0
        events.append((row.ts_ns,priority,row.match_id,row.order_id,row.quantity*row.price))
    for row in f.itertuples():
        amount=row.price*row.quantity
        events.append((row.ts_ns,2,row.match_id,row.order_id, -amount if row.side=='BUY' else amount))
    for row in r.itertuples():
        payout=row.engine_pnl-row.cash_flow
        events.append((pd.Timestamp(row.game_ended_at).value,3,row.match_id,'',payout))
    events.sort(key=lambda e:e[:3])
    orders={}
    locked=cash=peak=required=0.0
    for ts,kind,mid,oid,amount in events:
        if kind==0:
            locked-=orders.pop(oid,0.0)
        elif kind==1:
            locked+=amount-orders.get(oid,0.0)
            orders[oid]=amount
        elif kind==2:
            cash+=amount
            if amount<0 and oid in orders:
                old=orders[oid]
                new=max(0,old+amount)
                orders[oid]=new
                locked+=new-old
        else:
            cash+=amount
        if locked-cash>required:
            required=locked-cash
            peak_time=ts
        peak=max(peak,locked)
    return {'game':game,'variant':variant,'seed':seed,'filter_failed':filter_failed,'ledger_deposit':required,'stored_deposit':a['wallet']['required_cash_with_reserves'],'ledger_peak_reserved':peak,'stored_peak_reserved':a['wallet']['peak_reserved'],'cash_at_end':cash,'engine_pnl':a['pnl_before_rebate'],'residual_reserve':locked,'peak_at':str(pd.Timestamp(peak_time,tz='UTC')),'unclosed_orders':sum(x>0.01 for x in orders.values())}

reports=[]
for game,variant,seed in [('dota','lfollow300',0),('dota','b200',0),('dota','b300',0),('lol','lfollow300',0),('lol','lfollow300',1),('lol','lfollow300',3),('lol','b200',0),('lol','b200',1)]:
    for filtered in ([False,True] if game=='lol' and seed in [0,3] else [True]):
        value=calculate(game,variant,seed,filtered)
        reports.append(value)
        print(json.dumps(value),flush=True)
Path('/private/tmp/follow300-review/ledger.json').write_text(json.dumps(reports,indent=2)+'\n')
