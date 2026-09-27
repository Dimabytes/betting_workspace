"""Cross-correlate live-vs-STRATZ per-second nw_adv to detect a systematic second offset."""
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
by_mid = {}
for p in trader.iterdir():
    mj = p / "match.json"
    if not mj.exists():
        continue
    meta = json.loads(mj.read_text())
    smid = meta.get("steam_match_id")
    if smid and str(smid).isdigit():
        by_mid[int(smid)] = (p, meta)

for mid in MIDS:
    if mid not in by_mid:
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
    st = {s.second: s for s in build_exact_second_states(match)}

    common = [s for s in live if s in st and 60 <= s <= 1500]
    if len(common) < 50:
        print(f"{mid}: too few common seconds {len(common)}")
        continue

    # optimal shift: try shift in -5..+5 where live[s] compared to stratz[s+shift]
    best = None
    for shift in range(-5, 6):
        diffs = [
            abs(live[s].radiant_nw_adv - st[s + shift].radiant_nw_adv)
            for s in common if (s + shift) in st
        ]
        if diffs:
            m = statistics.median(diffs)
            if best is None or m < best[1]:
                best = (shift, m, len(diffs))
    # also NW magnitude check at second granularity: stratz gold-event staleness
    stale = []
    for s in common[:200]:
        pass
    print(f"{mid}: common={len(common)} best shift live->stratz={best}")
    # profile of nw_adv diff across time for the outlier
    if mid == 9003856182:
        for s in common[::60]:
            print(f"   s={s} live={live[s].radiant_nw_adv} stratz={st[s].radiant_nw_adv} "
                  f"d={live[s].deaths_radiant}/{live[s].deaths_dire} vs {st[s].deaths_radiant}/{st[s].deaths_dire}")
