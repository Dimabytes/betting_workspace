import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
OUT = Path('/private/tmp/hubermono-audit')
OUT.mkdir(exist_ok=True)
rng = np.random.default_rng(20260905)
names = ['l1-val454', 'hubermono-val454']
frames, summaries, manifests = {}, {}, {}
for name in names:
    rows, sums, mans = [], [], []
    root = ROOT / 'data/backtests/dota_maker' / ('validation_join_delta01_cut540_nw350_p35_' + name)
    for seed in range(12):
        d = root / f'seed{seed}'
        f = pd.read_parquet(d/'fills.parquet')
        r = pd.read_parquet(d/'results.parquet').set_index('match_id').sort_index()
        assert len(r) == 454 and r.index.is_unique and not r.terminated_early.any()
        assert r.engine_pnl.notna().all()
        s = json.loads((d/'summary.json').read_text())
        a = s['arms'][0]
        assert s['coverage']['eligible'] == 454 and s['selected'] == 454
        rebate = f.groupby('match_id').maker_rebate.sum().reindex(r.index,fill_value=0)
        cash = (f.price*f.quantity*np.where(f.side=='BUY',-1,1)).groupby(f.match_id).sum().reindex(r.index,fill_value=0)
        assert np.allclose(cash, r.cash_flow, atol=1e-7)
        for side, col in [('BUY','buy'),('SELL','sell')]:
            ff = f[f.side.eq(side)]
            assert np.allclose(ff.groupby('match_id').quantity.sum().reindex(r.index,fill_value=0),r[col+'_quantity'])
            assert np.array_equal(ff.groupby('match_id').size().reindex(r.index,fill_value=0),r[col+'_fills'])
        assert np.allclose(f.maker_rebate,0.15*0.05*f.quantity*f.price*(1-f.price))
        r['rebate'] = rebate
        r['net_pnl'] = r.engine_pnl+rebate
        r['traded'] = (r.buy_fills+r.sell_fills)>0
        r['loss'] = r.engine_pnl<0
        r['seed'] = seed
        r['buy_turnover'] = (f.price*f.quantity).where(f.side.eq('BUY'),0).groupby(f.match_id).sum().reindex(r.index,fill_value=0)
        checks = dict(completed=len(r),traded=int(r.traded.sum()),total_engine_pnl=r.engine_pnl.sum(),net_pnl=r.net_pnl.sum(),maker_rebate=r.rebate.sum(),loss_match_rate=r.loss.mean(),cvar_5=np.sort(r.engine_pnl)[:23].mean(),worst_match=r.engine_pnl.min(),buy_turnover=r.buy_turnover.sum())
        for k,v in checks.items():
            assert np.isclose(a[k],v,atol=1e-7),(name,seed,k,a[k],v)
        checks.update(loss_if_traded=r.loc[r.traded,'loss'].mean(),cvar_traded=np.sort(r.loc[r.traded,'engine_pnl'])[:int(np.ceil(r.traded.sum()*.05))].mean(),required_cash=a['wallet']['required_cash'],max_match_drawdown=a['max_match_drawdown'],min_equity=a['min_equity'],roi_with_rebate=a['wallet']['roi_with_rebate'])
        sums.append(checks)
        rows.append(r.reset_index())
        mans.append(json.loads((d/'manifest.json').read_text()))
    frames[name] = pd.concat(rows)
    summaries[name] = pd.DataFrame(sums)
    manifests[name] = mans
    payload=json.loads((root/'seeds.json').read_text())
    for k in ['traded','net_pnl','loss_match_rate','cvar_5','worst_match']:
        assert np.isclose(payload['mean'][k],summaries[name][k].mean())
        assert np.isclose(payload['sd'][k],summaries[name][k].std())
    print(name, '\n',summaries[name].agg(['mean','std']).round(6).to_string(),flush=True)
    summaries[name].to_csv(OUT/f'{name}-seed-metrics.csv',index_label='seed')
for seed in range(12):
    a,b=[manifests[n][seed] for n in names]
    assert a['signal_cadence_seed']==b['signal_cadence_seed']==seed
    differences={k:[a.get(k),b.get(k)] for k in a.keys()|b.keys() if a.get(k)!=b.get(k)}
    assert set(differences) == {'model_name'},differences
    if seed==0:print('manifest differences',differences,flush=True)
    for n in names:
        ref = manifests[n][0]
        assert all(v==ref[k] for k,v in manifests[n][seed].items() if k!='signal_cadence_seed')

catalog=pd.read_parquet(ROOT/'data/new_processed/dataset/validation_dataset.parquet',columns=['match_id','event_id']).drop_duplicates()
assert catalog.match_id.is_unique
tables={n:frames[n].pivot(index='match_id',columns='seed',values='engine_pnl') for n in names}
base,cand=[tables[n] for n in names]
assert base.index.equals(cand.index)
delta=cand-base
per_match=delta.mean(axis=1)
matches=frames[names[0]].query('seed==0').set_index('match_id').reindex(base.index)[['horn_at','slug']].join(catalog.set_index('match_id'))
matches['date']=pd.to_datetime(matches.horn_at).dt.strftime('%Y-%m-%d')
matches['week']=pd.to_datetime(matches.horn_at).dt.strftime('%G-%V')
matches['baseline']=base.mean(axis=1)
matches['candidate']=cand.mean(axis=1)
matches['delta']=per_match
matches.to_csv(OUT/'per-match.csv')
print('seed total delta',delta.sum().round(6).tolist(),flush=True)
print('seed conditional t CI',stats.t.interval(.95,11,loc=delta.sum().mean(),scale=stats.sem(delta.sum())),flush=True)
print('pooled paired',stats.ttest_1samp(per_match,0),stats.wilcoxon(per_match[per_match.ne(0)]),'mean',per_match.mean(), 'total',per_match.sum(),flush=True)

def cluster_ci(values,groups):
    grouped=pd.DataFrame({'value':np.asarray(values),'group':np.asarray(groups)}).groupby('group').value.agg(['sum','count'])
    ix=rng.integers(len(grouped),size=(20000,len(grouped)))
    samples=grouped['sum'].to_numpy()[ix].sum(axis=1)/grouped['count'].to_numpy()[ix].sum(axis=1)
    return {'clusters':len(grouped),'mean':float(np.mean(values)),'ci95':np.quantile(samples,[.025,.975]).tolist()}

result={}
for metric in ['engine_pnl','net_pnl','loss','traded']:
    matrices=[frames[n].pivot(index='match_id',columns='seed',values=metric).astype(float) for n in names]
    d=(matrices[1]-matrices[0]).mean(axis=1)
    result[metric]={key:cluster_ci(d,base.index if key=='match' else matches[key]) for key in ['match','event_id','date','week']}
    print('cluster CI',metric,json.dumps(result[metric]),flush=True)
    if metric=='net_pnl':print('net paired t',stats.ttest_1samp(d,0),'net seed deltas',(matrices[1]-matrices[0]).sum().round(3).tolist(),flush=True)
ordered=matches.sort_values('horn_at').index
boundaries=[0,len(ordered)//3,2*len(ordered)//3,len(ordered)]
for i in range(3):
    group=ordered[boundaries[i]:boundaries[i+1]]
    d=per_match.loc[group]
    print('third',i,len(group),matches.loc[group,'date'].min(),matches.loc[group,'date'].max(),'mean',d.mean(),'total',d.sum(),'wilcoxon',stats.wilcoxon(d[d.ne(0)]).pvalue,'positive_seeds',int((delta.loc[group].sum()>0).sum()),flush=True)
print('top positive',per_match.nlargest(10).round(3).to_dict(),'top negative',per_match.nsmallest(10).round(3).to_dict(),flush=True)
print('remove top 1/3/5 total',[float(per_match.sum()-per_match.nlargest(k).sum()) for k in [1,3,5]],flush=True)
trades=[frames[n].pivot(index='match_id',columns='seed',values='traded').astype(bool) for n in names]
losses=[frames[n].pivot(index='match_id',columns='seed',values='loss').astype(float) for n in names]
groups = pd.factorize(matches.event_id)[0]
idx=rng.integers(len(np.unique(groups)),size=(20000,len(np.unique(groups))))
boot_rates=[]
for loss,trade in zip(losses,trades):
    grouped=pd.DataFrame({'loss':loss.mean(axis=1),'trade':trade.mean(axis=1),'event':matches.event_id}).groupby('event').sum()
    boot_rates.append(grouped.loss.to_numpy()[idx].sum(axis=1)/grouped.trade.to_numpy()[idx].sum(axis=1))
print('loss conditional difference',summaries[names[1]].loss_if_traded.mean()-summaries[names[0]].loss_if_traded.mean(),'series cluster ci',np.quantile(boot_rates[1]-boot_rates[0],[.025,.975]),flush=True)
print('loss lower seeds',int((summaries[names[1]].loss_match_rate<summaries[names[0]].loss_match_rate).sum()),'conditional lower seeds',int((summaries[names[1]].loss_if_traded<summaries[names[0]].loss_if_traded).sum()),flush=True)
for label,mask in [('both',trades[0]&trades[1]),('base only',trades[0]&~trades[1]),('candidate only',~trades[0]&trades[1]),('neither',~trades[0]&~trades[1])]:
    print('trade decomposition',label,'count/seed',mask.sum().mean(),'base/seed',base.where(mask,0).sum().mean(),'candidate/seed',cand.where(mask,0).sum().mean(),'delta/seed',delta.where(mask,0).sum().mean(),flush=True)
seed_cov=np.cov(delta.to_numpy(),rowvar=False)
diag=float(np.trace(seed_cov)/12)
off=float((seed_cov.sum()-np.trace(seed_cov))/(12*11))
print('variance diagnostic delta per match',{'diag':diag,'off_diagonal':off,'common_fraction':off/diag,'se_on_total_12':float(np.sqrt(454*(off+(diag-off)/12))),'se_on_total_infinite_seeds':float(np.sqrt(454*max(0,off))),'conditional_seed_mc_se':float(stats.sem(delta.sum()))},flush=True)
(OUT/'cluster-ci.json').write_text(json.dumps(result,indent=2))
print('ALL NUMERICAL AND MANIFEST CHECKS PASSED',flush=True)
