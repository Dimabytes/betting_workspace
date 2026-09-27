import pandas as pd
for name in ['stratz_match_index/stratz_match_index.parquet','match_links/match_links.parquet','opendota_links/opendota_links.parquet','grid_game_starts/grid_game_windows.parquet','universe/universe.parquet']:
 d=pd.read_parquet('data/new_processed/'+name)
 print(name, len(d), list(d.columns))
 print(d.head(3).to_string(index=False))
 if 'status' in d: print(d.status.value_counts(dropna=False).to_string())
 if 'reason' in d: print(d.reason.value_counts(dropna=False).head(30).to_string())
