import pandas as pd
ids=[8910982575,8920655724,8992034384]
for name in ['match_catalog/match_catalog.parquet','match_links/match_links.parquet','match_links/match_link_audit.parquet','opendota_links/opendota_links.parquet','universe/universe.parquet']:
 d=pd.read_parquet('data/new_processed/'+name)
 key='match_id' if 'match_id' in d else ('conditionId' if 'conditionId' in d else None)
 print('\nTABLE',name,'cols',list(d.columns))
 if key:
  q=d[d[key].isin(ids)] if key=='match_id' else d[d.conditionId.isin(pd.read_parquet('data/new_processed/match_catalog/match_catalog.parquet').query('match_id in @ids').condition_id)]
  print(q.to_string(index=False,max_colwidth=60))
