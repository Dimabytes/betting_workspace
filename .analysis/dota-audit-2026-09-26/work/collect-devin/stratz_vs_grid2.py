"""Replay GRID archives; compare live features vs STRATZ exact-second states per second."""
import json
import statistics
from pathlib import Path

from prepare_dataset.stratz_seconds import build_exact_second_states
from shared.utils.json_io import read_gzip_json
from shared.utils.stratz import stratz_match_cache_path
from trader.game_profile import GAME_PROFILES
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer, replay_grid_records
from trader.live_feed import FeedEvent

MIDS = [9003856182, 9003520141, 8990366825, 9006400787, 9001305683]

trader = Path("data/trader")
dirs = {p.name: p for p in trader.iterdir() if p.is_dir()}
# map steam id -> dir name
by_mid = {}
for name, p in dirs.items():
    mj = p / "match.json"
    if not mj.exists():
        continue
    meta = json.loads(mj.read_text())
    smid = meta.get("steam_match_id")
    if smid and str(smid).isdigit():
        by_mid[int(smid)] = (p, meta)

for mid in MIDS:
    if mid not in by_mid:
        print(f"{mid}: no trader archive")
        continue
    d, meta = by_mid[mid]
    reducer = GridFrameReducer(
        meta["map_number"], meta["market"]["outcome_0_name"], meta["market"]["outcome_1_name"],
        GAME_PROFILES["dota"],
    )
    records = list(iter_grid_archive_records(d / "grid_state.jsonl.gz"))
    events = [e for e in replay_grid_records(records, reducer) if isinstance(e, FeedEvent)]
    live = {e.snapshot.second: e.snapshot for e in events}

    payload = read_gzip_json(stratz_match_cache_path(mid))
    match = payload["data"]["match"]
    states = build_exact_second_states(match)
    st = {s.second: s for s in states}

    common = [s for s in live if s in st and 0 <= s <= 1500]
    print(f"== {mid} horn={meta.get('horn_at_utc')} dur={meta.get('final',{}).get('duration_seconds')} "
          f"events={len(events)} common={len(common)}")
    if not common:
        continue
    for name, arr in [
        ("nw_adv", [live[s].radiant_nw_adv - st[s].radiant_nw_adv for s in common]),
        ("xp_adv", [live[s].radiant_xp_adv - st[s].radiant_xp_adv for s in common]),
        ("deaths_r", [live[s].deaths_radiant - st[s].deaths_radiant for s in common]),
        ("deaths_d", [live[s].deaths_dire - st[s].deaths_dire for s in common]),
        ("top1_nw_adv", [live[s].top.top1_nw_adv - st[s].top.top1_nw_adv for s in common]),
        ("r_nw", [live[s].radiant_nw - st[s].radiant_nw for s in common]),
    ]:
        arr_abs = [abs(x) for x in arr]
        print(
            f"  {name} live-stratz: mean={statistics.mean(arr):+.1f} "
            f"|diff| p50={statistics.median(arr_abs)} p95={sorted(arr_abs)[int(len(arr_abs)*0.95)]} "
            f"max={max(arr_abs)} exact0={sum(1 for x in arr if x==0)}/{len(arr)}"
        )
