import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

from shared.utils.jsonl_io import open_maybe_gz

E = Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
R = Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')
LIVE_ROOT = E / 'data/trader'
BT_ROOT = E / 'data/backtests/dota_maker/LIVE'


def read_json(path):
    return json.loads(path.read_text())


def safe_mean(values):
    vals = [float(x) for x in values if pd.notna(x)]
    return sum(vals) / len(vals) if vals else None


def read_live():
    rows=[]
    for d in LIVE_ROOT.iterdir():
        mp=d/'match.json'; sp=d/'session.jsonl'
        if not mp.exists() or not sp.exists(): continue
        try: m=read_json(mp)
        except Exception: continue
        if m.get('game','dota') not in ('dota',None): continue
        sess=[]
        with open_maybe_gz(sp) as f:
            for line in f:
                try: sess.append(json.loads(line))
                except Exception: continue
        starts=[r for r in sess if r.get('kind')=='session_start']
        if not starts: continue
        ss=starts[-1]
        if ss.get('execution_mode')!='live': continue
        fills=[r for r in sess if r.get('kind')=='fill' and r.get('price') is not None]
        buys=[r for r in fills if r.get('side')=='BUY']
        sells=[r for r in fills if r.get('side')=='SELL']
        ends=[r for r in sess if r.get('kind')=='session_end']
        end=ends[-1] if ends else None
        pnl=None
        if end and end.get('venue')=='polymarket':
            try: pnl=float(end.get('net_cash') or 0)+float(end.get('inventory_value') or 0)
            except: pass
        buy_notional=sum(float(r['price'])*float(r['size']) for r in buys)
        buy_qty=sum(float(r['size']) for r in buys)
        buy_px=(buy_notional/buy_qty) if buy_qty else None
        first_buy=min((int(r['second']) for r in buys if r.get('second') is not None),default=None)
        net_by_token=defaultdict(float)
        for r in buys: net_by_token[str(r.get('token_id'))]+=float(r['size'])
        for r in sells: net_by_token[str(r.get('token_id'))]-=float(r['size'])
        held=max(0.0,sum(max(q,0.0) for q in net_by_token.values()))
        sigs=[r for r in sess if r.get('kind')=='signal']
        mids_by_second={}
        for r in sigs:
            sec=r.get('second')
            if not isinstance(sec,(int,float)): continue
            if r.get('yes_mid') is None and r.get('no_mid') is None: continue
            mids_by_second[int(sec)] = (r.get('yes_mid'),r.get('no_mid'))
        market=m.get('market') or {}
        final=m.get('final') or {}
        yes_token=str(market.get('yes_token_id','')); no_token=str(market.get('no_token_id',''))
        by_token_mid={}
        for sec,(ym,nm) in mids_by_second.items():
            if yes_token: by_token_mid.setdefault(yes_token,[]).append((sec,ym))
            if no_token: by_token_mid.setdefault(no_token,[]).append((sec,nm))
        live_m30=[]; live_m300=[]
        for r in buys:
            token=str(r.get('token_id')); sec=int(r.get('second',-99999)); px=float(r['price'])
            points=by_token_mid.get(token,[])
            for h,out in ((30,live_m30),(300,live_m300)):
                future=next((mid for t,mid in points if t>=sec+h and mid is not None),None)
                if future is not None: out.append((float(future)-px,float(r['size'])))
        weighted_m30=(sum(a*q for a,q in live_m30)/sum(q for _,q in live_m30)) if live_m30 else None
        weighted_m300=(sum(a*q for a,q in live_m300)/sum(q for _,q in live_m300)) if live_m300 else None
        binding=ss.get('sidecar_binding') or {}
        joined=m.get('joined_at_utc','')
        rows.append({
            'dir':d.name, 'match_id':str(m.get('match_id','')), 'steam_match_id':str(m.get('steam_match_id') or ''),
            'condition_id':str(market.get('condition_id') or ss.get('condition_id') or '').lower(),
            'slug':market.get('market_slug'), 'source':m.get('feed_source','unknown'),
            'market_kind':binding.get('market_kind','unknown'), 'map_number':m.get('map_number'),
            'date':joined[:10], 'joined':joined, 'model':(ss.get('model') or {}).get('name'),
            'live_fills':len(fills), 'live_buy_fills':len(buys), 'live_sell_fills':len(sells),
            'live_buy':buy_notional, 'live_pnl':pnl, 'live_buy_price':buy_px, 'live_first_buy_sec':first_buy,
            'live_held_shares':held, 'live_held_to_end':held>5.0, 'live_win':final.get('winner'),
            'live_m30':weighted_m30, 'live_m30_fills':len(live_m30), 'live_m300':weighted_m300, 'live_m300_fills':len(live_m300),
            'clip':(None if not isinstance(m.get('final'),dict) else (None)),
        })
    return pd.DataFrame(rows)


def read_backtest():
    results=[]; fill_frames=[]
    for seed in range(3):
        r=pd.read_parquet(BT_ROOT/f'seed{seed}/results.parquet')
        f=pd.read_parquet(BT_ROOT/f'seed{seed}/fills.parquet')
        f['seed']=seed
        f['buy_notional']=f['price']*f['quantity']
        results.append(r.assign(seed=seed))
        fill_frames.append(f)
    result=pd.concat(results,ignore_index=True)
    fills=pd.concat(fill_frames,ignore_index=True)
    buy=fills[fills.side=='BUY'].copy()
    all_fills=fills.groupby(['seed','condition_id','match_id'],as_index=False).agg(
        bt_fills=('side','size'),
    ) if False else None
    agg=[]
    for (seed,match_id),g in result.groupby(['seed','match_id'],sort=False):
        b=buy[(buy.seed==seed)&(buy.match_id==match_id)]
        allf=fills[(fills.seed==seed)&(fills.match_id==match_id)]
        buys=b[b.quantity>0]
        horn=pd.to_datetime(g.iloc[0]['horn_at'],utc=True,errors='coerce')
        first=None
        if len(buy):
            ts=pd.to_datetime(b['ts_ns'],unit='ns',utc=True)
            if pd.notna(horn) and len(ts): first=float((ts.min()-horn).total_seconds())
        buy_qty=float(b.quantity.sum()) if len(b) else 0.0
        cost=float(b.buy_notional.sum()) if len(b) else 0.0
        mark30=(float((b.quantity*b.markout_30s).sum()/b.quantity.sum()) if len(b) and b.quantity.sum() else None)
        mark300=(float((b.quantity*b.markout_300s).sum()/b.quantity.sum()) if len(b) and b.quantity.sum() else None)
        row=g.iloc[0]
        agg.append({
          'seed':seed,'match_id':str(match_id),'condition_id':str(row.condition_id).lower(),'slug':row.slug,
          'bt_pnl':float(row.engine_pnl),'bt_cash':float(row.cash_flow),'bt_buy':cost,
          'bt_buy_fills':len(b),'bt_fills':len(allf),'bt_buy_qty':buy_qty,
          'bt_buy_price':cost/buy_qty if buy_qty else None,'bt_first_buy_sec':first,
          'bt_held_shares':float(row.terminal_position),'bt_held_to_end':float(row.terminal_position)>5.0,
          'bt_winning_side':row.terminal_side,'bt_signal_mode':row.signal_mode,'bt_feed_source':row.feed_source,
          'bt_m30':mark30,'bt_m300':mark300,'bt_settlement_applied':bool(row.settlement_applied),
        })
    perseed=pd.DataFrame(agg)
    # Average the three random seeds per condition. Signal schedule groups are fixed across seeds.
    averaged=perseed.groupby('condition_id',as_index=False).agg(
      match_id=('match_id','first'),slug=('slug','first'),bt_pnl=('bt_pnl','mean'),bt_cash=('bt_cash','mean'),bt_buy=('bt_buy','mean'),
      bt_buy_fills=('bt_buy_fills','mean'),bt_fills=('bt_fills','mean'),bt_buy_qty=('bt_buy_qty','mean'),bt_buy_price=('bt_buy_price','mean'),
      bt_first_buy_sec=('bt_first_buy_sec','median'),bt_held_shares=('bt_held_shares','mean'),bt_held_rate=('bt_held_to_end','mean'),
      bt_winning_side=('bt_winning_side','first'),bt_signal_mode=('bt_signal_mode','first'),bt_feed_source=('bt_feed_source','first'),
      bt_m30=('bt_m30','mean'),bt_m300=('bt_m300','mean'),bt_settlement_applied=('bt_settlement_applied','mean'))
    return perseed,averaged,result,fills


def summarize(j, label):
    if not len(j): print('SUMMARY',label,'n=0'); return
    both=j[(j.live_buy>0)&(j.bt_buy>0)]
    def rate(g,keyp,keyb):
        b=float(g[keyb].sum()); p=float(g[keyp].fillna(0).sum()); return (100*p/b if b else None,p,b)
    complete=j[j.live_pnl.notna()]
    complete_both=both[both.live_pnl.notna()]
    lp=rate(complete,'live_pnl','live_buy'); bp=rate(j,'bt_pnl','bt_buy')
    lb=rate(complete_both,'live_pnl','live_buy'); bb=rate(both,'bt_pnl','bt_buy')
    print('SUMMARY',label,'maps',len(j),'live_traded',int((j.live_buy>0).sum()),'bt_traded',int((j.bt_buy>0).sum()),'both_traded',len(both),
      'live_pnl/buy_pct',lp,'bt_pnl/buy_pct',bp,'both_live',lb,'both_bt',bb,
      'live_fills',int(j.live_fills.sum()),'live_buy_fills',int(j.live_buy_fills.sum()),'bt_fills_mean',float(j.bt_fills.sum()),
      'live_pnl_complete_maps',len(complete),'both_traded_live_pnl_complete_maps',len(complete_both),
      'first_buy_live_median',statistics.median(j.loc[j.live_first_buy_sec.notna(),'live_first_buy_sec']) if j.live_first_buy_sec.notna().any() else None,
      'first_buy_bt_median',statistics.median(j.loc[j.bt_first_buy_sec.notna(),'bt_first_buy_sec']) if j.bt_first_buy_sec.notna().any() else None,
      'buy_px_live',safe_mean(j.loc[j.live_buy>0,'live_buy_price']),'buy_px_bt',safe_mean(j.loc[j.bt_buy>0,'bt_buy_price']),
      'live_buy_m30',safe_mean(j.live_m30),'bt_buy_m30',safe_mean(j.bt_m30),'live_buy_m300',safe_mean(j.live_m300),'bt_buy_m300',safe_mean(j.bt_m300),
      'live_held_n',int((j.live_held_shares>5).sum()),'live_held_win_n',int(((j.live_held_shares>5)&j.live_win.eq('radiant')).sum()),
      'bt_held_n_mean',float(j.bt_held_rate.sum()))


live=read_live()
perseed,bt,result_raw,fill_raw=read_backtest()
# Include only the current training/backtest sample and live map-condition joins.
j=live.merge(bt,on='condition_id',how='inner',suffixes=('_live','_bt'))
# exact condition IDs identify the traded market; split by archive feed and two comparable periods.
print('LIVE_ARCHIVES',len(live),'unique_conditions',live.condition_id.nunique(),'duplicates',live.condition_id.duplicated().sum(),
      'sources',live.source.value_counts().to_dict(),'market_kind',live.market_kind.value_counts().to_dict(),
      'dates',live.date.min(),live.date.max(),'pnl_missing',int(live.live_pnl.isna().sum()))
print('BACKTEST',len(bt),'unique_conditions',bt.condition_id.nunique(),'modes',bt.bt_signal_mode.value_counts().to_dict(),
      'sources',bt.bt_feed_source.value_counts().to_dict())
print('JOIN',len(j),'unique_cond',j.condition_id.nunique(),'duplicate_condition_rows',int(j.condition_id.duplicated().sum()))
# Exact full comparison by source and time period.
j['period']=j.date.apply(lambda d:'2026-08-30_to_31' if d<='2026-08-31' else ('2026-09-01_to_19' if d<='2026-09-19' else '2026-09-20_to_26'))
for src in sorted(j.source.unique()):
 for period in sorted(j.period.unique()):
  sub=j[(j.source==src)&(j.period==period)]
  summarize(sub,f'{src}/{period}/all_joined')
  summarize(sub[(sub.live_buy>0)&(sub.bt_buy>0)],f'{src}/{period}/both_traded')
print('JOIN modes by live source',j.groupby(['source','bt_signal_mode','bt_feed_source']).size().to_dict())
print('JOIN market kinds',j.market_kind.value_counts().to_dict())
print('BUY/SELL late markout coverage: live markouts aggregate counts',int(j.live_m30_fills.sum()),int(j.live_m300_fills.sum()),'of buy_fills',int(j.live_buy_fills.sum()))
# Maps with both systems, save for subsequent raw feed replay and tail checking.
j.to_csv(R/'same_map_live_backtest.csv',index=False)
perseed.to_csv(R/'backtest_per_seed.csv',index=False)
# Population map sets: validation rows identify maps available to the research split.
val=pd.read_parquet(E/'data/new_processed/dataset/validation_dataset.parquet',columns=['condition_id','match_id','radiant_win','second'])
val_ids=set(val.condition_id.dropna().astype(str).str.lower())
bt_ids=set(bt.condition_id)
live_ids=set(live.condition_id)
print('POPULATION backtest_ids',len(bt_ids),'validation_ids',len(val_ids),'live_ids',len(live_ids),'live_in_bt',len(live_ids&bt_ids),'live_not_bt',len(live_ids-bt_ids),'bt_with_live',len(bt_ids&live_ids),'bt_without_live',len(bt_ids-live_ids),'live_not_bt_in_validation',len((live_ids-bt_ids)&val_ids),'live_not_bt_not_in_validation',len((live_ids-bt_ids)-val_ids))
not_bt=live[live.condition_id.isin(live_ids-bt_ids)]
print('LIVE_NOT_BT by source',not_bt.source.value_counts().to_dict(),'by date',not_bt.date.value_counts().sort_index().to_dict(),'first',not_bt[['date','source','match_id','condition_id','slug','market_kind']].sort_values('date').head(25).to_dict('records'))
missing_live=bt[~bt.condition_id.isin(live_ids)]
print('BT_WITHOUT_LIVE by slug date/source counts',missing_live.assign(day=missing_live.slug.str.extract(r'(2026-\d\d-\d\d)')[0]).groupby(['day','bt_signal_mode','bt_feed_source']).size().to_dict())
print('BT_WITHOUT_LIVE source aggregates',missing_live.bt_signal_mode.value_counts().to_dict())
# Summary of current held-tail positions by seed, including the matched set.
for seed in range(3):
 r=perseed[(perseed.seed==seed)]
 held=r[r.bt_held_to_end]
 non_dust=held[held.bt_held_shares>5]
 print('SEED_TAIL',seed,'held_any',len(held),'held_non_dust',len(non_dust),'held_non_dust_on_live',int(non_dust.condition_id.isin(live_ids).sum()),
       'held_non_dust_on_live_traded',int(non_dust.condition_id.isin(live[live.live_buy>0].condition_id).sum()),
       'non_dust_pnl',float(non_dust.bt_pnl.sum()),'non_dust_cash',float(non_dust.bt_cash.sum()),
       'non_dust_positive_settle',float((non_dust.bt_pnl-non_dust.bt_cash).sum()))
 # join winners from validation rows; use dataset labels per match_id/second, invariant per map
 d=non_dust.merge(val[['condition_id','radiant_win']].drop_duplicates('condition_id'),on='condition_id',how='left')
 winners=((d.bt_winning_side=='radiant')&d.radiant_win.eq(True))|((d.bt_winning_side=='dire')&d.radiant_win.eq(False))
 print('SEED_TAIL_WIN',seed,'winner_count',int(winners.sum()),'count',len(d),'non_null_label',int(d.radiant_win.notna().sum()),'held_sides',d.bt_winning_side.value_counts().to_dict())
