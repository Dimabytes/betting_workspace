from pathlib import Path
import json
import pandas as pd
pro=Path('data/raw/pro_matches')
league_by_match={}; duplicates=0; conflicting=[]
for p in pro.glob('*.json'):
 try: payload=json.loads(p.read_text())
 except Exception: continue
 for row in payload.get('rows') or []:
  mid=int(row['match_id']); lid=row.get('leagueid')
  if mid in league_by_match:
   duplicates+=1
   if league_by_match[mid]!=lid: conflicting.append((mid,league_by_match[mid],lid))
  league_by_match[mid]=lid
print('PRO_MATCHES',len(league_by_match),'duplicate_rows',duplicates,'league_conflicts',len(conflicting))
cat=pd.read_parquet('data/new_processed/match_catalog/match_catalog.parquet')
cat['match_id']=cat.match_id.astype(int)
cat['month']=pd.to_datetime(cat.spawn_at.fillna(cat.horn_at),utc=True,format='mixed').dt.strftime('%Y-%m')
cat['split']=cat.start_time if 'start_time' in cat else 0
# Match catalog has raw OpenDota identifiers; start split at fixed cutoff.
cat['split']=pd.to_datetime(cat.spawn_at.fillna(cat.horn_at),utc=True,format='mixed').astype('int64')//10**9
cat['split']=cat.split.map(lambda x:'train' if x<1780563592 else 'validation')
cat['league_id']=cat.match_id.map(league_by_match)
print('CATALOG_LEAGUE_COVERAGE',cat.league_id.notna().sum(),'/',len(cat),'unique_leagues',cat.league_id.nunique(),'unmatched_months',cat[cat.league_id.isna()].month.value_counts().to_dict())
for name,path in [('train','data/new_processed/dataset/training_dataset.parquet'),('val','data/new_processed/dataset/validation_dataset.parquet')]:
 d=pd.read_parquet(path,columns=['match_id'])
 ids=d.match_id.drop_duplicates().astype(int)
 mix=cat[cat.match_id.isin(ids)].groupby('league_id').agg(maps=('match_id','nunique'),catalog_maps=('month','size'),months=('month',lambda x:','.join(sorted(set(x))))).sort_values('maps',ascending=False)
 print('DATASET',name,'maps',len(ids),'matched_league_maps',int(cat[cat.match_id.isin(ids)].league_id.notna().sum()),'top10',mix.head(10).to_dict('index'))
# Dota live archive records: `league_id` in match.json may be GRID namespace; independently report coverage/distribution.
live=[]
for d in Path('data/trader').iterdir():
 p=d/'match.json'
 if not p.exists():continue
 try:m=json.loads(p.read_text())
 except Exception:continue
 if m.get('game')!='dota':continue
 live.append({'match_id':d.name,'steam_match_id':str(m.get('steam_match_id') or ''),'grid_league_id':m.get('league_id'),'month':str(m.get('joined_at_utc',''))[:7],'feed':m.get('feed_source')})
live=pd.DataFrame(live)
print('LIVE',len(live),'rows','grid_league_present',live.grid_league_id.notna().sum(),'unique_league',live.grid_league_id.nunique(),'top20',live.grid_league_id.value_counts().head(20).to_dict())
