"""Recompute s06 first-failure counts + stratz index reasons on current artifacts."""
import pandas as pd

from collect.common.paths import (
    GRID_GAME_WINDOWS_PATH,
    MATCH_LINKS_PATH,
    PREGAME_QUOTES_PATH,
    STRATZ_MATCH_INDEX_PATH,
    UNIVERSE_PATH,
)
from collect.s06_publish_catalog import (
    assemble_catalog_frame,
    check_masks,
    first_failure_counts,
    load_archive_pauses,
)
from shared.utils.opendota import try_load_opendota_pauses
from shared.utils.stratz import try_load_stratz_winners

links = pd.read_parquet(MATCH_LINKS_PATH)
grid = pd.read_parquet(GRID_GAME_WINDOWS_PATH)
stratz = pd.read_parquet(STRATZ_MATCH_INDEX_PATH)
priors = pd.read_parquet(PREGAME_QUOTES_PATH)
universe = pd.read_parquet(UNIVERSE_PATH)
match_ids = tuple(int(m) for m in links["match_id"])
pauses_by_match = try_load_opendota_pauses(match_ids)
archive_pauses = load_archive_pauses(links)
winners = try_load_stratz_winners(match_ids)

frame = assemble_catalog_frame(
    links, grid, stratz, priors, universe, pauses_by_match, archive_pauses, winners
)
print("link rows:", len(frame))
counts = first_failure_counts(frame)
for k, v in counts.items():
    print(f"  first-failure {k}: {v}")
kept = pd.Series(True, index=frame.index)
masks = check_masks(frame)
for m in masks.values():
    kept &= m
print("kept:", int(kept.sum()), "dropped:", int((~kept).sum()))

# failure reason combos for dropped rows
dropped = frame[~kept]
print("\ndropped link_source:", dropped["link_source"].value_counts().to_dict())
# all failing masks per row (not just first)
for name, mask in masks.items():
    print(f"  rows failing {name} (any): {int((~mask).sum())}")

# pauses divergence among archive rows with opendota pauses
arch = frame[frame["archive_id"].notna()]
div = 0
both = 0
for row in arch.itertuples(index=False):
    od = pauses_by_match.get(int(row.match_id))
    ar = archive_pauses.get(int(row.match_id))
    if od is not None and ar is not None:
        both += 1
        if len(od) != len(ar) or sum(p["duration"] for p in od) != sum(p["duration"] for p in ar):
            div += 1
            mid = int(row.match_id)
            print(f"  divergent pauses match={mid} opendota={od} archive={ar}")
print(f"\narchive rows: {len(arch)}, with both pause sources: {both}, divergent: {div}")

# stratz index reasons
print("\nstratz index:", stratz["status"].value_counts().to_dict())
print(stratz["reason"].value_counts(dropna=False).to_dict())
print("playback_available:", stratz["playback_available"].value_counts().to_dict())
# durations by status
print(stratz.groupby("status")["duration"].describe().round(0).to_string())
