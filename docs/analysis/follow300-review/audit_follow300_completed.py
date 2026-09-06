"""Strict, one-off audit of the completed comparison catalogs."""
from pathlib import Path
import hashlib
import json
import pandas as pd

root=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
target=Path('/private/tmp/follow300-review')
expected_dota={int(x) for x in (root/'docs/experiments/buy-ladder-v2/baseline/match_ids.txt').read_text().split()}
audits=[]
universes={}
for game,arm,count in [('dota','lfollow300',12),('dota','b200',12),('dota','b300',3),('lol','lfollow300',12),('lol','b200',12)]:
    path=root/f'data/backtests/{game}_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-{arm}'
    expected=expected_dota if game=='dota' else None
    for seed in range(count):
        d=path/f'seed{seed}'
        for name in ['results.parquet','fills.parquet','quote_events.parquet','manifest.json','summary.json']:
            assert (d/name).is_file(),str(d/name)
        results=pd.read_parquet(d/'results.parquet')
        manifest=json.loads((d/'manifest.json').read_text())
        summary=json.loads((d/'summary.json').read_text())
        ids=set(results.match_id)
        if game not in universes:
            universes[game]=ids
        assert ids==universes[game]
        if expected is None:
            expected=ids
        assert ids==expected
        assert len(ids)==len(results)
        failed=results.loc[results.terminated_early,'match_id'].tolist()
        assert len(failed)==summary['arms'][0]['terminated']
        assert summary['arms'][0]['completed']==len(results)-len(failed)
        assert summary['manifest']==manifest
        assert manifest['signal_cadence_seed']==seed
        if game=='dota':
            assert not failed
        model=root/manifest['model_path']
        assert hashlib.sha256(model.read_bytes()).hexdigest()==manifest['model_sha256']
        fills=pd.read_parquet(d/'fills.parquet')
        problems=[]
        for mid,group in fills.groupby('match_id',sort=False):
            held=[0.0,0.0]
            for row in group.sort_values('ts_ns',kind='stable').itertuples():
                held[row.token_index]+=row.quantity if row.side=='BUY' else -row.quantity
                if min(held)<-1e-5 or min(held)>1e-5 or abs(sum(held)-row.position_after)>1e-5:
                    problems.append((mid,row.ts_ns))
        orders=fills.groupby('order_id').agg(filled=('quantity','sum'),submitted=('submitted_quantity','first'))
        overfilled=int(((orders.filled-orders.submitted)>1e-5).sum())
        assert not problems,(game,arm,seed,problems[:3])
        assert not overfilled,(game,arm,seed,overfilled)
        audit={'game':game,'arm':arm,'seed':seed,'maps':len(results),'failed_maps':failed,'fills':len(fills),'inventory_errors':len(problems),'overfilled_orders':overfilled}
        audits.append(audit)
exits=json.loads((root/'data/backtests/dota_maker/_logs/ladder-v2-lfollow300-complete-20260906/exits.json').read_text())
assert [r['seed'] for r in exits]==list(range(3,12))
assert all(r['exit_code']==0 for r in exits)
(target/'catalog-audit.json').write_text(json.dumps({'catalogs':audits,'new_seed_exits':exits},indent=2)+'\n')
print(f'Validated {len(audits)} seed catalogs; all requested Dota processes exited 0. LoL failures explicitly listed, not counted as a clean catalog.')
