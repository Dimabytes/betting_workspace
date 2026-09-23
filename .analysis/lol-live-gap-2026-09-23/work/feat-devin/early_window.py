"""Per map: first emitted tick second, ticks below 70/480, vs first model signal."""

import gzip
import json
import sys
from pathlib import Path

from shared.utils.match_time import parse_utc
from trader.game_profile import GAME_PROFILES
from trader.grid_feed import GridFrameReducer
from trader.grid_widgets import parse_frame

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data/trader"

for name in sys.argv[1:]:
    d = TRADER / name
    meta_path = d / "match.json"
    if not meta_path.exists():
        print(f"{name}: no match.json")
        continue
    meta = json.loads(meta_path.read_text())
    market = meta["market"]
    reducer = GridFrameReducer(
        meta["map_number"], market["outcome_0_name"], market["outcome_1_name"], GAME_PROFILES["lol"]
    )
    path = d / "grid_state.jsonl.gz"
    if not path.exists():
        path = d / "grid_state.jsonl"
    opener = gzip.open if str(path).endswith(".gz") else open
    ticks = []
    with opener(path, "rt") as handle:
        for line in handle:
            rec = json.loads(line)
            event = reducer.reduce_frame(parse_frame(rec["frame"]), parse_utc(rec["received_at_utc"]))
            if event is not None:
                ticks.append(event.snapshot.second)
    first_model = None
    for line in (d / "session.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row.get("kind") == "signal" and row.get("reason") == "model":
            first_model = row["second"]
            break
    in_win = [s for s in ticks if 0 <= s <= 540]
    early = [s for s in ticks if 0 <= s < 70]
    buys = [s for s in ticks if 0 <= s < 480]
    print(
        f"{name}: ticks={len(ticks)} first={ticks[0] if ticks else '-'} "
        f"in_window={len(in_win)} sec<70={len(early)} sec<480={len(buys)} first_model_signal={first_model}"
    )
