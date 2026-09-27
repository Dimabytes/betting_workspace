"""Independent look-ahead check: rebuild NW at sampled seconds from raw events.

For each match, build_exact_second_states output is compared against a fresh,
non-cursor recomputation: at second s, a player's NW must equal the last
playerUpdateGoldEvent with time <= s (or 0 when none). Any use of t > s leaks.
"""
import random

import pandas as pd

from prepare_dataset.stratz_seconds import build_exact_second_states
from shared.constants.paths import MATCH_CATALOG_PATH
from shared.types.stratz import StratzMatch
from shared.utils.json_io import read_gzip_json
from shared.utils.stratz import is_usable_stratz_match, stratz_match_cache_path

cat = pd.read_parquet(MATCH_CATALOG_PATH)
split = pd.read_parquet("data/new_model/research/split.parquet")
val_ids = split.loc[split["split"] == "validation", "match_id"].astype(int).tolist()
random.seed(7)
sample = random.sample(val_ids, 3)
print("sampled validation matches:", sample)


def independent_nw(match: StratzMatch, second: int) -> tuple[int, int]:
    """Recompute side NWs at `second` with no shared state."""
    r = d = 0
    for p in match["players"]:
        nw = 0
        for ev in (p.get("playbackData") or {}).get("playerUpdateGoldEvents") or []:
            if ev["time"] <= second:
                nw = ev["networth"]
            else:
                break
        if p["isRadiant"]:
            r += nw
        else:
            d += nw
    return r, d


checked = 0
future_events = 0
for mid in sample:
    entry = read_gzip_json(stratz_match_cache_path(int(mid)))
    match = entry["data"]["match"]
    assert is_usable_stratz_match(match)
    states = build_exact_second_states(match)
    by_second = {s.second: s for s in states}
    # count how many events sit in the future relative to each other (sanity)
    evts = [
        (ev["time"], p["isRadiant"])
        for p in match["players"]
        for ev in (p.get("playbackData") or {}).get("playerUpdateGoldEvents") or []
    ]
    dur = match["durationSeconds"]
    # check every second in a strided sample plus all minute marks
    test_seconds = sorted(set(range(-60, dur, 37)) | set(range(-60, dur + 1, 60)))
    for s in test_seconds:
        st = by_second.get(s)
        if st is None:
            continue
        r, d = independent_nw(match, s)
        checked += 1
        if (st.radiant_nw, st.dire_nw) != (r, d):
            print(f"  MISMATCH mid={mid} s={s} built=({st.radiant_nw},{st.dire_nw}) indep=({r},{d})")
        # leak detector: would the next unconsumed event change the value?
        for p in match["players"]:
            evs = (p.get("playbackData") or {}).get("playerUpdateGoldEvents") or []
            nxt = [ev for ev in evs if ev["time"] <= s + 1 and ev["time"] > s]
            # not an error, informational
            future_events += len(nxt)
    print(f"  mid={mid} dur={dur} states={len(states)} checked_seconds_ok")
print(f"\nchecked {checked} (match,second) pairs across {len(sample)} matches; no mismatches printed above = causal NW")

# also verify the built states at minute marks equal networthPerMinute exactly
for mid in sample:
    entry = read_gzip_json(stratz_match_cache_path(int(mid)))
    match = entry["data"]["match"]
    states = build_exact_second_states(match)
    by_second = {s.second: s for s in states}
    ok = 0
    for s in range(0, match["durationSeconds"] + 1, 60):
        st = by_second.get(s)
        if st is None:
            continue
        npm = s // 60
        exp_r = sum(p["stats"]["networthPerMinute"][npm] for p in match["players"] if p["isRadiant"])
        exp_d = sum(p["stats"]["networthPerMinute"][npm] for p in match["players"] if not p["isRadiant"])
        assert (st.radiant_nw, st.dire_nw) == (exp_r, exp_d), (mid, s, st.radiant_nw, exp_r)
        ok += 1
    print(f"mid={mid}: minute-mark NW equals networthPerMinute at {ok} marks")
