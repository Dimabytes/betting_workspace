"""One-off independent review of saved catalogs; writes only to /private/tmp."""
from pathlib import Path
import json
import re
import math
import numpy as np
import pandas as pd

ROOT = Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
OUT = Path('/private/tmp/follow300-review')
OUT.mkdir(exist_ok=True)

def load_catalog(game, variant, seeds):
    root = ROOT / f'data/backtests/{game}_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-{variant}'
    tables = []
    summaries = []
    manifests = []
    problems = []
    for seed in seeds:
        path = root / f'seed{seed}'
        r = pd.read_parquet(path / 'results.parquet')
        f = pd.read_parquet(path / 'fills.parquet')
        a = json.loads((path / 'summary.json').read_text())['arms'][0]
        m = json.loads((path / 'manifest.json').read_text())
        m.pop('signal_cadence_seed')
        for key, value in {'buy_depth_cap_multiple':None,'buy_size_policy':'fixed-v1','sell_drop_block_delta':0.0,'sell_drop_block_window_seconds':90.0,'sell_drop_block_max_seconds':600.0,'sell_drop_block_stop_delta':0.05}.items():
            m.setdefault(key,value)
        manifests.append(m)
        assert not r.match_id.duplicated().any()
        bad = r[r.terminated_early]
        for row in bad.itertuples():
            problems.append({'seed':seed,'match_id':row.match_id,'slug':row.slug,'stop_reason':row.stop_reason})
        r = r[~r.terminated_early].copy()
        f = f[f.match_id.isin(r.match_id)]
        rebate = f.groupby('match_id').maker_rebate.sum()
        r['rebate'] = r.match_id.map(rebate).fillna(0)
        r['net'] = r.engine_pnl + r.rebate
        assert abs(r.engine_pnl.sum() - a['pnl_before_rebate']) < 1e-6
        assert abs(r.rebate.sum() - a['maker_rebate']) < 1e-6
        assert abs(r.net.sum() - a['net_pnl']) < 1e-6
        buy = f[f.side == 'BUY']
        sell = f[f.side == 'SELL']
        cash = (sell.price*sell.quantity).groupby(sell.match_id).sum().sub((buy.price*buy.quantity).groupby(buy.match_id).sum(),fill_value=0)
        assert (r.cash_flow-r.match_id.map(cash).fillna(0)).abs().max() < 1e-6
        assert abs(a['net_pnl_per_match'] - r.net.sum()/len(r)) < 1e-9
        r['seed'] = seed
        tables.append(r[['seed','match_id','slug','horn_at','engine_pnl','rebate','net']])
        summaries.append(a)
    assert all(m == manifests[0] for m in manifests)
    frame = pd.concat(tables,ignore_index=True)
    frame.to_csv(OUT / f'{game}-{variant}-{len(seeds)}-maps.csv',index=False)
    metrics = ['net_pnl','pnl_before_rebate','maker_rebate','net_pnl_per_match','buy_turnover','cvar_5','worst_match','max_match_drawdown','loss_match_rate','traded']
    wallet = ['required_cash','required_cash_with_reserves','required_cash_with_reserves_at_close','peak_reserved','lowest_capital']
    means = {k:float(np.mean([a[k] for a in summaries])) for k in metrics}
    means.update({k:float(np.mean([a['wallet'].get(k,0) for a in summaries])) for k in wallet})
    means.update(net_per_reserve=means['net_pnl']/means['required_cash_with_reserves'], reserve_max_seed=max(a['wallet']['required_cash_with_reserves'] for a in summaries), seeds=list(seeds),problems=problems,manifest=manifests[0])
    return frame, means

def compare(game, left, right, seeds):
    a, am = load_catalog(game,left,seeds)
    b, bm = load_catalog(game,right,seeds)
    joined = a.merge(b,on=['seed','match_id'],suffixes=('_a','_b'),validate='one_to_one')
    counts = joined.groupby('match_id').seed.nunique()
    good_ids = counts[counts == len(seeds)].index
    joined = joined[joined.match_id.isin(good_ids)]
    assert (joined.slug_a == joined.slug_b).all()
    assert (joined.horn_at_a == joined.horn_at_b).all()
    joined['delta'] = joined.net_a - joined.net_b
    joined['delta_engine'] = joined.engine_pnl_a - joined.engine_pnl_b
    mapped = joined.groupby('match_id').agg(slug=('slug_a','first'),horn=('horn_at_a','first'),delta=('delta','mean'),delta_engine=('delta_engine','mean'),net_a=('net_a','mean'),net_b=('net_b','mean'))
    mapped['series'] = mapped.slug.str.replace(r'-game\d+$','',regex=True)
    mapped = mapped.sort_values('horn')
    cluster = mapped.groupby('series')[['delta','delta_engine']].sum()
    rng = np.random.default_rng(20260905)
    draws = rng.integers(0,len(cluster),size=(10000,len(cluster)))
    boot = cluster.to_numpy()[draws].sum(axis=1)
    ci = np.percentile(boot,[2.5,97.5],axis=0)
    thirds = []
    cut = len(mapped)//3
    for part in [mapped.iloc[:cut], mapped.iloc[cut:2*cut], mapped.iloc[2*cut:]]:
        thirds.append({'start':part.horn.min(),'end':part.horn.max(),'maps':len(part),'net_a':part.net_a.sum(),'net_b':part.net_b.sum(),'delta':part.delta.sum()})
    pairs = joined.groupby('seed')[['net_a','net_b','delta']].sum()
    result = {'game':game,'left':left,'right':right,'left_metrics':am,'right_metrics':bm,'paired_maps':len(mapped),'series':len(cluster),'net_left_paired':mapped.net_a.sum(),'net_right_paired':mapped.net_b.sum(),'delta':mapped.delta.sum(),'ci_net':ci[:,0].tolist(),'delta_engine':mapped.delta_engine.sum(),'ci_engine':ci[:,1].tolist(),'seed_deltas':pairs.delta.to_dict(),'thirds':thirds,'without_5_best_delta_maps':mapped.delta.sum()-mapped.delta.nlargest(5).sum(),'left_without_5_best_maps':mapped.net_a.sum()-mapped.net_a.nlargest(5).sum(),'right_without_5_best_maps':mapped.net_b.sum()-mapped.net_b.nlargest(5).sum()}
    mapped.to_csv(OUT / f'{game}-{left}-vs-{right}-paired.csv')
    return result

reports = [compare('dota','lfollow300','b200',range(3)),compare('dota','lfollow300','b300',range(3)),compare('lol','lfollow300','b200',range(12))]
if all((ROOT / f'data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-lfollow300/seed{seed}/summary.json').exists() for seed in range(12)):
    reports.extend([compare('dota','lfollow300','b200',range(12)),compare('dota','lfollow300','b100',range(12)),compare('dota','lfollow300','lfollow600',range(12))])
(OUT / 'comparisons.json').write_text(json.dumps(reports,indent=2)+'\n')
for report in reports:
    print(json.dumps(report))
