"""Smoke test: replay GRID archive + livestats features for one map."""

import gzip
import json
import sys
from pathlib import Path

import pandas as pd

from lol.livestats_frames import prepare_map_livestats_until, LivestatsOk, LivestatsDrop
from lol.networth import DEFAULT_ITEM_CATALOG_DIR, load_item_catalog
from lol.constants import LOL_WINDOWS_DIR, LOL_DETAILS_DIR, LOL_PREPARE_END_SECOND
from trader.game_profile import GAME_PROFILES
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer, replay_grid_records

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
MATCH = "grid-2964617-m1"

match = json.loads((E / "data/trader" / MATCH / "match.json").read_text())
market = match["market"]
print("map_number", match["map_number"], "outcomes", market["outcome_0_name"], "/", market["outcome_1_name"])

links = pd.read_parquet(E / "data/lol/processed/lolesports_links/links.parquet")
link = links[links.condition_id == market["condition_id"]].iloc[0]
print("esports_game_id", link["esports_game_id"], "anchor_ts", link["loading_anchor_ts"])

# --- GRID replay (live path) ---
reducer = GridFrameReducer(
    match["map_number"],
    market["outcome_0_name"],
    market["outcome_1_name"],
    GAME_PROFILES["lol"],
)
records = list(iter_grid_archive_records(E / "data/trader" / MATCH / "grid_state.jsonl.gz"))
events = list(replay_grid_records(records, reducer))
print("grid events:", len(events))
for ev in events[:5]:
    s = ev.snapshot
    print(f"  t={s.second:5d} nw_adv={s.radiant_nw_adv:6d} rnw={s.radiant_nw} dnw={s.dire_nw} "
          f"xp={s.radiant_xp_adv} d={s.deaths_radiant}/{s.deaths_dire} top1adv={s.top.top1_nw_adv} "
          f"rratio={s.top.radiant_top1_nw_ratio:.3f} phase={s.phase} recv={ev.received_at_utc}")

# --- livestats (train path) ---
catalog = load_item_catalog(DEFAULT_ITEM_CATALOG_DIR)
res = prepare_map_livestats_until(
    dict(link), LOL_WINDOWS_DIR, LOL_DETAILS_DIR, catalog, 1200
)
if isinstance(res, LivestatsDrop):
    print("DROP:", res)
    sys.exit(1)
print("livestats ok: grid_rows", len(res.grid_rows), "pauses", res.pause_count,
      "spawn_wall", res.spawn_wall_seconds)
for row in res.grid_rows[:5]:
    f = row.features
    print(f"  s={row.second:4d} wall={row.state_wall_us} nw_adv={f.radiant_nw_adv:6d} "
          f"rnw={f.radiant_nw} dnw={f.dire_nw} xp={f.radiant_xp_adv} "
          f"d={f.deaths_radiant}/{f.deaths_dire} rratio={f.radiant_top1_nw_ratio:.3f}")

# quick look at game_features parquet for this match_id
gf = pd.read_parquet(E / "data/lol/processed/datasets/game_features.parquet",
                     filters=[("match_id", "==", int(link["esports_game_id"]))])
print("game_features rows for match:", len(gf))
if len(gf):
    print(gf.head(3).to_string())
