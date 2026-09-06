"""Complete only Dota Follow300 seeds 3..11, one unsharded process per seed."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import datetime
import hashlib
import json
import os
import subprocess

repo=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
root=repo/'data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-lfollow300'
logs=repo/'data/backtests/dota_maker/_logs/ladder-v2-lfollow300-complete-20260906'
for seed in range(3,12):
    if (root/f'seed{seed}').exists():
        raise SystemExit(f'Refusing to overwrite {root / f"seed{seed}"}')
checks={
    'data/backtests/dota_maker/ladder-v2-inputs/model/model.txt':'9f54106a5bee03a8ab8a1bfdb4a6403246b319864f9a91fd103b2dc9ef7f3ec2',
    'data/new_processed/dataset/validation_dataset.parquet':'de4ce6ccd1abf32165195901e97af074809d74ab45b6b2cb08be8a823b42ea93',
}
for name,wanted in checks.items():
    actual=hashlib.sha256((repo/name).read_bytes()).hexdigest()
    if actual != wanted:
        raise SystemExit(f'Pinned input changed: {name}: {actual}')
logs.mkdir(parents=True,exist_ok=True)
env=os.environ.copy()
env['PYTHONPATH']='src:../prediction-market-backtesting'
env['PYTHONDONTWRITEBYTECODE']='1'
env['UV_CACHE_DIR']='/private/tmp/follow-audit-uv'
base=['uv','run','--no-sync','--group','backtest','python','src/backtest/run.py','--game','dota','--validation','--name','ladder-v2-lfollow300','--layers','3','--layer-step-ticks','1','--base-size-usdc','300','--buy-lifecycle','parallel','--ladder-reprice-mode','follow','--buy-after-first-fill','continue','--sell-drop-block-delta','0','--model-dir','data/backtests/dota_maker/ladder-v2-inputs/model']
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
provenance={'started_at':datetime.datetime.now(datetime.UTC).isoformat(),'commit':head,'seeds':list(range(3,12)),'shards_per_seed':1,'command':base,'pinned_inputs':checks}
(logs/'driver.json').write_text(json.dumps(provenance,indent=2)+'\n')
print(json.dumps(provenance),flush=True)

def run_seed(seed):
    folder=logs/f'seed{seed}'
    folder.mkdir(exist_ok=True)
    with (folder/'run.log').open('w') as out:
        child=subprocess.Popen([*base,'--signal-cadence-seed',str(seed)],cwd=repo,env=env,stdout=out,stderr=subprocess.STDOUT)
        print(json.dumps({'seed':seed,'pid':child.pid,'status':'started'}),flush=True)
        code=child.wait()
    result={'seed':seed,'exit_code':code,'finished_at':datetime.datetime.now(datetime.UTC).isoformat()}
    (folder/'exit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
    return result

with ThreadPoolExecutor(max_workers=9) as pool:
    futures=[pool.submit(run_seed,seed) for seed in range(3,12)]
    results=[future.result() for future in as_completed(futures)]
results.sort(key=lambda item:item['seed'])
(logs/'exits.json').write_text(json.dumps(results,indent=2)+'\n')
raise SystemExit(1 if any(result['exit_code'] for result in results) else 0)
