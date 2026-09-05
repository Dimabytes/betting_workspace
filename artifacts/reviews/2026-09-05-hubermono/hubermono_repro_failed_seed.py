import os
from pathlib import Path
import shutil
import subprocess
import tempfile

repo=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
fixture=Path(tempfile.mkdtemp(prefix='seed-audit-',dir='/private/tmp'))
(fixture/'scripts').mkdir()
shutil.copy2(repo/'scripts/run_seeds.sh',fixture/'scripts/run_seeds.sh')
(fixture/'bin').mkdir()
stub=fixture/'bin/uv'
stub.write_text('''#!/usr/bin/env python3
import os,sys,shutil
from pathlib import Path
args=sys.argv[1:]
repo=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
if 'src/backtest/run.py' in args:
    seed=int(args[args.index('--signal-cadence-seed')+1])
    dest=Path('data/backtests/dota_maker/validation_audit_failure')/f'seed{seed}'
    dest.mkdir(parents=True,exist_ok=True)
    if seed==11:sys.exit(19)
    source=repo/'data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_hubermono-val454'/f'seed{seed}'
    for filename in ['summary.json','results.parquet','fills.parquet']:
        shutil.copy2(source/filename,dest/filename)
    sys.exit(0)
if 'scripts/report_seeds.py' in args:
    os.environ['PYTHONPATH']=str(repo/'src')+':'+str(repo/'scripts')
    os.execv(str(repo/'.venv/bin/python'),[str(repo/'.venv/bin/python'),str(repo/'scripts/report_seeds.py'),args[-1]])
raise RuntimeError(args)
''')
stub.chmod(0o755)
env=dict(os.environ,PATH=str(fixture/'bin')+os.pathsep+os.environ['PATH'],SEEDS='12',SHARDS='1',PYTHONDONTWRITEBYTECODE='1')
run=subprocess.run(['bash',str(fixture/'scripts/run_seeds.sh'),'dota','failure'],cwd=fixture,env=env,text=True,capture_output=True)
(fixture/'stdout.txt').write_text(run.stdout)
(fixture/'stderr.txt').write_text(run.stderr)
summaries=list((fixture/'data/backtests/dota_maker/validation_audit_failure').glob('seed*/summary.json'))
print('fixture:',fixture)
print('script exit code:',run.returncode)
print('requested seeds: 12; summaries written:',len(summaries))
print('seed11 intentionally exited 19 without a summary')
print('report generated:',(fixture/'data/backtests/dota_maker/validation_audit_failure/seeds.json').exists())
print('stderr:',run.stderr)
assert run.returncode==0 and len(summaries)==11
