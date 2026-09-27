"""How many catalog / dataset maps are linked to a series_winner market instead of a map market."""
import pandas as pd

u = pd.read_parquet("data/new_processed/universe/universe.parquet")
kind = u.drop_duplicates("conditionId").set_index("conditionId")[["contract_kind", "best_of", "game_number", "event_title"]]
c = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet")
c = c.join(kind, on="condition_id")
c["spawn"] = pd.to_datetime(c.spawn_at, utc=True, format="ISO8601")
print("catalog maps:", len(c))
print(pd.crosstab(c.contract_kind.fillna("NA"), c.best_of.fillna(-1)))
ser = c[c.contract_kind == "series_winner"]
print("series_winner-linked maps by month:")
print(ser.groupby(ser.spawn.dt.strftime("%Y-%m")).size().to_string())
print("share by month:")
print((c.assign(s=c.contract_kind == "series_winner").groupby(c.spawn.dt.strftime("%Y-%m")).s.mean().round(3)).to_string())
print("condition_ids shared by >1 catalog map:", int((c.condition_id.value_counts() > 1).sum()))
dup = c[c.condition_id.duplicated(keep=False)].sort_values("condition_id")
print(dup[["match_id", "condition_id", "market_slug", "contract_kind", "spawn_at", "radiant_win"]].head(12).to_string())
series_ids = set(ser.match_id)
for name, path in [("research_train", "data/new_processed/dataset/training_dataset.parquet"),
                   ("validation", "data/new_processed/dataset/validation_dataset.parquet"),
                   ("production_train", "data/new_processed/dataset/production_training_dataset.parquet")]:
    try:
        d = pd.read_parquet(path, columns=["match_id"])
    except Exception as error:
        print(name, "ERR", error)
        continue
    m = d.match_id.isin(series_ids)
    print(f"{name}: maps={d.match_id.nunique()} series_maps={d[m].match_id.nunique()} rows={len(d)} series_rows={int(m.sum())}")
r = pd.read_parquet("data/backtests/dota_maker/LIVE/seed0/results.parquet")
rs = r[r.match_id.isin(series_ids)]
print(f"LIVE bt seed0: series maps={len(rs)} engine_pnl={rs.engine_pnl.sum():.2f} of {r.engine_pnl.sum():.2f}; traded={int((rs.buy_fills>0).sum())}")
