"""Funnel + timing analysis over collect parquet artifacts."""
import pandas as pd

P = "data/new_processed"
links = pd.read_parquet(f"{P}/match_links/match_links.parquet")
win = pd.read_parquet(f"{P}/grid_game_starts/grid_game_windows.parquet")
cat = pd.read_parquet(f"{P}/match_catalog/match_catalog.parquet")
quotes = pd.read_parquet(f"{P}/pregame_quotes/pregame_quotes.parquet")
sidx = pd.read_parquet(f"{P}/stratz_match_index/stratz_match_index.parquet")
audit = pd.read_parquet(f"{P}/match_links/match_link_audit.parquet")

print("== match_links:", len(links))
print(links["link_source"].value_counts().to_dict())
print("has grid_clock_seconds:", int(links["grid_clock_seconds"].notna().sum()))
print("has archive_horn:", int(links["archive_horn_at_utc"].notna().sum()))
print("audit:", audit["resolution"].value_counts().to_dict())

print("\n== grid_game_windows:", len(win), "unique conditions:", win["condition_id"].nunique())
linked = links[links["grid_clock_seconds"].notna()]
matched_cond = set(win["condition_id"])
n_match = linked["map_condition_id"].isin(matched_cond).sum()
print(f"links with clock field: {len(linked)}; of them with a GRID window: {n_match}")

print("\n== spawn - match_start_time (sec), windowed opendota links:")
m = linked.merge(win, left_on="map_condition_id", right_on="condition_id")
spawn = pd.to_datetime(m["spawn_at"], utc=True, format="mixed").astype("int64") // 10**9
delta = spawn - m["match_start_time"].astype("int64")
print(delta.describe().round(1).to_dict())

print("\n== match_catalog:", len(cat))
print("horn_source:", cat["horn_source"].value_counts().to_dict())
print("pauses_source:", cat["pauses_source"].value_counts(dropna=False).to_dict())
print("archive_id notna:", int(cat["archive_id"].notna().sum()))
print("playback_available:", cat["playback_available"].value_counts().to_dict())

# horn - spawn for rows having both
both = cat[cat["spawn_at"].notna()].copy()
both["horn_dt"] = pd.to_datetime(both["horn_at"], utc=True, format="mixed")
both["spawn_dt"] = pd.to_datetime(both["spawn_at"], utc=True, format="mixed")
both["delta"] = (both["horn_dt"] - both["spawn_dt"]).dt.total_seconds()
print("\nhorn - spawn (s), rows with spawn (all have archive horn only when archive-attached):")
print("  count:", len(both))
print("  archive-attached:", int(both['archive_id'].notna().sum()))
b = both[both["archive_id"].notna()]
print("  archive-attached horn_archive - spawn:", b["delta"].describe().round(1).to_dict())
# for archive rows, horn_at IS archive horn; for grid_derived rows, horn was derived from spawn (+90+pauses)
g = both[both["archive_id"].isna()]
print("  grid_derived (horn=spawn+90+pauses) delta:", g["delta"].describe().round(1).to_dict())

print("\n== stratz_match_index:", len(sidx))
print("status:", sidx["status"].value_counts().to_dict())
print("reason:", sidx["reason"].value_counts(dropna=False).to_dict())
print("playback_available:", sidx["playback_available"].value_counts().to_dict())

print("\n== pregame_quotes:", len(quotes))
q = quotes.copy()
q["staleness"] = q["anchor_ts"] - q[["radiant_quote_ts", "dire_quote_ts"]].max(axis=1)
q["pair_gap"] = (q["radiant_quote_ts"] - q["dire_quote_ts"]).abs()
print("staleness (anchor - newest leg) s:", q["staleness"].describe().round(1).to_dict())
print("pair_gap s:", q["pair_gap"].describe().round(1).to_dict())
print("radiant_prior:", q["radiant_prior"].describe().round(3).to_dict())
print("radiant+dire price sum:", (q["radiant_price"] + q["dire_price"]).describe().round(3).to_dict())

# split / gap exclusions
split = pd.read_parquet("data/new_model/research/split.parquet")
print("\n== research split:", split["split"].value_counts().to_dict())
val = split[split["split"] == "validation"]
print("book_gap_excluded in validation:", int(val["backtest_book_gap_excluded"].sum()), "of", len(val))
