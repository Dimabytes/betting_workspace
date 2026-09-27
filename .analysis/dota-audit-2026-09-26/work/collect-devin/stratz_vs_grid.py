"""Compare live GRID-replayed features vs STRATZ exact-second reconstruction."""
import gzip
import json
import statistics
import sys
from pathlib import Path

import pandas as pd

from prepare_dataset.stratz_seconds import build_exact_second_states
from shared.utils.stratz import get_stratz_match_reach_data, stratz_match_cache_path, is_usable_stratz_match
from shared.utils.json_io import read_gzip_json
from shared.types.stratz import StratzMatch
from trader.game_profile import GAME_PROFILES
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer, replay_grid_records
from trader.live_feed import FeedEvent

cat = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet")
cat_ids = set(cat["match_id"].astype(int))

trader = Path("data/trader")
candidates = []
for d in sorted(trader.iterdir()):
    mj = d / "match.json"
    if not mj.exists() or not (d / "grid_state.jsonl.gz").exists():
        continue
    meta = json.loads(mj.read_text())
    smid = meta.get("steam_match_id")
    if smid is None or not str(smid).isdigit():
        continue
    mid = int(smid)
    if mid in cat_ids and stratz_match_cache_path(mid).exists() and meta.get("feed_source") == "grid":
        candidates.append((mid, d, meta))
print("candidates:", len(candidates))

mid, d, meta = candidates[0]
print("using", mid, d, "map_number:", meta.get("map_number"))
market = meta["market"]
print("outcomes:", market["outcome_0_name"], "/", market["outcome_1_name"])

reducer = GridFrameReducer(
    meta["map_number"], market["outcome_0_name"], market["outcome_1_name"], GAME_PROFILES["dota"]
)
records = list(iter_grid_archive_records(d / "grid_state.jsonl.gz"))
print("records:", len(records))
events = [e for e in replay_grid_records(records, reducer) if isinstance(e, FeedEvent)]
print("events:", len(events))

live = {}
for e in events:
    s = e.snapshot
    live[s.second] = s

payload = read_gzip_json(stratz_match_cache_path(mid))
match = payload["data"]["match"]
print("usable:", is_usable_stratz_match(match))
states = build_exact_second_states(match)
stratz_by_second = {st.second: st for st in states}

common = sorted(s for s in live if s in stratz_by_second and 0 <= s <= 1500)
print("common seconds 0..1500:", len(common))
if common:
    d_nw = [live[s].radiant_nw_adv - stratz_by_second[s].radiant_nw_adv for s in common]
    d_xp = [live[s].radiant_xp_adv - stratz_by_second[s].radiant_xp_adv for s in common]
    d_dr = [live[s].deaths_radiant - stratz_by_second[s].deaths_radiant for s in common]
    d_dd = [live[s].deaths_dire - stratz_by_second[s].deaths_dire for s in common]
    d_top = [live[s].top.top1_nw_adv - stratz_by_second[s].top.top1_nw_adv for s in common]
    for name, arr in [
        ("nw_adv live-stratz", d_nw),
        ("xp_adv live-stratz", d_xp),
        ("deaths_radiant diff", d_dr),
        ("deaths_dire diff", d_dd),
        ("top1_nw_adv diff", d_top),
    ]:
        print(
            f"{name}: mean={statistics.mean(arr):.1f} median={statistics.median(arr)} "
            f"min={min(arr)} max={max(arr)}"
        )
    # absolute NW levels (scale check)
    d_rnw = [live[s].radiant_nw - stratz_by_second[s].radiant_nw for s in common]
    print(
        f"radiant_nw diff: mean={statistics.mean(d_rnw):.1f} median={statistics.median(d_rnw)} "
        f"min={min(d_rnw)} max={max(d_rnw)}"
    )
    # sample rows
    for s in common[:: 300]:
        l, t = live[s], stratz_by_second[s]
        print(
            f"s={s}: nw_adv {l.radiant_nw_adv} vs {t.radiant_nw_adv} | xp {l.radiant_xp_adv} vs {t.radiant_xp_adv} "
            f"| deaths {l.deaths_radiant}/{l.deaths_dire} vs {t.deaths_radiant}/{t.deaths_dire}"
        )
