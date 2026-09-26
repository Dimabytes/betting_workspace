import json
from pathlib import Path

root = Path('data/trader')
for match_dir in root.iterdir():
    session_path = match_dir / 'session.jsonl'
    match_path = match_dir / 'match.json'
    if not session_path.exists() or not match_path.exists():
        continue
    records = [json.loads(line) for line in session_path.open() if line.strip()]
    starts = [r for r in records if r.get('kind') == 'session_start' and r.get('execution_mode') == 'live']
    if not starts:
        continue
    match = json.loads(match_path.read_text())
    print('MATCH', match_dir.name, 'GAME', match.get('game'), 'JOINED', match.get('joined_at_utc'), 'HORN', match.get('horn_at_utc'), 'DELAY', match.get('grid_delay_s'), 'TOURNAMENT', match.get('tournament'))
    print('MARKET', match.get('market'))
    for kind in ('session_start', 'signal', 'fill', 'session_end'):
        rows = [r for r in records if r.get('kind') == kind]
        if rows:
            print(kind, json.dumps(rows[0], separators=(',', ':'))[:700])
    print('n', len(records))
    break
