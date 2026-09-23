"""Replay a live LoL tape's grid_state.jsonl and diff every model feature
against the livestats reconstruction of the same map.

For each map:
  * reducer snapshots = exactly what the live model saw (via GridFrameReducer)
  * livestats grid rows = what training/backtest built (prepare_map_livestats_until)
  * raw livestats features (ZERO_CONSUMED) to test whether GRID NetWorth equals
    totalGold or totalGold - consumed items.
  * clock alignment via death-step offsets (grid_label - livestats_second).
"""

import bisect
import gzip
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pandas as pd

from lol.constants import LOL_DETAILS_DIR, LOL_WINDOWS_DIR
from lol.livestats_frames import (
    FrameFeatures,
    StampedFrame,
    assign_game_times,
    dedup_sort_frames,
    features_from_sides,
    find_spawn_index,
    parse_sides,
    validate_timed_frames,
)
from lol.networth import (
    ConsumedTimeline,
    ZERO_CONSUMED,
    build_consumed_timeline,
    load_item_catalog,
    table_for_game_patch,
    DEFAULT_ITEM_CATALOG_DIR,
)
from lol.livestats_frames import game_patch_from_payloads, read_archive_payloads
from shared.utils.match_time import parse_utc
from trader.game_profile import GAME_PROFILES
from trader.grid_feed import GridFrameReducer
from trader.grid_widgets import (
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    parse_frame,
    read_net_worth,
)
from trader.grid_widget_types import SeriesTablePayload

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data" / "trader"
LINKS = E / "data/lol/processed/lolesports_links/links.parquet"


@dataclass(frozen=True)
class LiveSnap:
    received_ns: int
    second: int
    radiant_nw: int
    dire_nw: int
    nw_adv: int
    xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top1_nw_adv: int
    radiant_ratio: float
    dire_ratio: float
    players_radiant: int
    players_dire: int


@dataclass(frozen=True)
class LsRow:
    second: int
    game_time: float
    radiant_nw: int
    dire_nw: int
    nw_adv: int
    xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top1_nw_adv: int
    radiant_ratio: float
    dire_ratio: float
    raw_radiant_nw: int  # sum(totalGold), no consumed subtraction
    raw_dire_nw: int


def load_tape(dirpath: Path) -> tuple[list[dict], dict]:
    meta = json.loads((dirpath / "match.json").read_text())
    path = dirpath / "grid_state.jsonl.gz"
    if not path.exists():
        path = dirpath / "grid_state.jsonl"
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    return records, meta


def replay_tape(records: list[dict], meta: dict) -> list[LiveSnap]:
    """Run the live reducer; also re-parse each emitted table for player counts."""
    market = meta["market"]
    reducer = GridFrameReducer(
        meta["map_number"], market["outcome_0_name"], market["outcome_1_name"],
        GAME_PROFILES["lol"],
    )
    snaps: list[LiveSnap] = []
    last_table = None
    for record in records:
        frame = parse_frame(record["frame"])
        now = parse_utc(record["received_at_utc"])
        if frame.service == TABLE_SERVICE and frame.payload:
            last_table = read_net_worth(frame.payload, frame.delay)
        event = reducer.reduce_frame(frame, now)
        if event is None:
            continue
        snap = event.snapshot
        radiant_n = dire_n = -1
        if last_table is not None and reducer._sides is not None:
            radiant_n = sum(1 for p in last_table.players if p.team_id == reducer._sides.radiant_id)
            dire_n = sum(1 for p in last_table.players if p.team_id == reducer._sides.dire_id)
        snaps.append(
            LiveSnap(
                received_ns=int(now.timestamp() * 1e9),
                second=snap.second,
                radiant_nw=snap.radiant_nw,
                dire_nw=snap.dire_nw,
                nw_adv=snap.radiant_nw_adv,
                xp_adv=snap.radiant_xp_adv,
                deaths_radiant=snap.deaths_radiant,
                deaths_dire=snap.deaths_dire,
                top1_nw_adv=snap.top.top1_nw_adv,
                radiant_ratio=snap.top.radiant_top1_nw_ratio,
                dire_ratio=snap.top.dire_top1_nw_ratio,
                players_radiant=radiant_n,
                players_dire=dire_n,
            )
        )
    return snaps


def livestats_rows(link_row: pd.Series, end_second: int) -> tuple[list[LsRow], float]:
    """Timed livestats frames with both consumed-corrected and raw gold."""
    game_id = str(link_row["esports_game_id"])
    payloads = read_archive_payloads(LOL_WINDOWS_DIR, game_id)
    details_payloads = read_archive_payloads(LOL_DETAILS_DIR, game_id)
    patch = game_patch_from_payloads(payloads)
    table = table_for_game_patch(load_item_catalog(DEFAULT_ITEM_CATALOG_DIR), patch)
    frames = dedup_sort_frames(payloads)
    details = dedup_sort_frames(details_payloads)
    consumed: ConsumedTimeline = build_consumed_timeline(
        [f.payload for f in details], table
    )
    spawn_index = find_spawn_index(frames, int(link_row["loading_anchor_ts"]))
    spawn_wall = frames[spawn_index].wall_seconds
    clock = assign_game_times(frames[spawn_index:])
    validated = validate_timed_frames(clock.timed, consumed, end_second)
    rows: list[LsRow] = []
    for timed in validated.frames:
        sides = timed.sides
        feat = timed.features
        raw_blue = sum(p.gold for p in sides.blue.players)
        raw_red = sum(p.gold for p in sides.red.players)
        raw_feat = features_from_sides(sides, ZERO_CONSUMED)
        rows.append(
            LsRow(
                second=int(timed.game_time),
                game_time=timed.game_time,
                radiant_nw=feat.radiant_nw,
                dire_nw=feat.dire_nw,
                nw_adv=feat.radiant_nw_adv,
                xp_adv=feat.radiant_xp_adv,
                deaths_radiant=feat.deaths_radiant,
                deaths_dire=feat.deaths_dire,
                top1_nw_adv=feat.top1_nw_adv,
                radiant_ratio=feat.radiant_top1_nw_ratio,
                dire_ratio=feat.dire_top1_nw_ratio,
                raw_radiant_nw=raw_blue,
                raw_dire_nw=raw_red,
            )
        )
    return rows, spawn_wall


def death_seconds(seconds: list[float], deaths: list[int]) -> dict[int, float]:
    reached: dict[int, float] = {}
    prev = 0
    for sec, d in zip(seconds, deaths):
        if d <= prev:
            continue
        for count in range(prev + 1, d + 1):
            reached[count] = sec
        prev = d
    return reached


def nearest_key(keys: list[float], target: float, tol: float = 2.0) -> int | None:
    i = bisect.bisect_left(keys, target)
    best = None
    best_gap = tol
    for j in (i - 1, i):
        if 0 <= j < len(keys) and abs(keys[j] - target) <= best_gap:
            best_gap = abs(keys[j] - target)
            best = j
    return best


def main() -> None:
    links = pd.read_parquet(LINKS)
    tapes = sys.argv[1:] or [
        "grid-3000375-m1", "grid-3000375-m2", "grid-3000375-m3",
        "grid-3000375-m4", "grid-3000375-m5",
        "grid-3000372-m1", "grid-3000372-m2", "grid-3000372-m3", "grid-3000372-m4",
    ]
    catalog = load_item_catalog(DEFAULT_ITEM_CATALOG_DIR)  # warm once

    for tape in tapes:
        records, meta = load_tape(TRADER / tape)
        link_hit = links[links["condition_id"] == meta["market"]["condition_id"]]
        if link_hit.empty:
            print(f"{tape}: NO LINK, skip")
            continue
        link_row = link_hit.iloc[0]
        snaps = replay_tape(records, meta)
        ls_rows, spawn_wall = livestats_rows(link_row, 7200)
        in_window = [s for s in snaps if 0 <= s.second <= 540]
        print(f"\n=== {tape}  map {meta['map_number']}  gid={link_row['esports_game_id']}")
        print(f"  grid ticks total={len(snaps)} in 0..540={len(in_window)}  livestats frames={len(ls_rows)}")
        partial = [s for s in snaps if s.players_radiant != 5 or s.players_dire != 5]
        if partial:
            print(f"  PARTIAL tables: {len(partial)} ticks with !=5 players a side")

        # clock alignment via death steps on radiant+dire death counts
        grid_secs = [float(s.second) for s in snaps]
        offsets: list[float] = []
        for gkey, lkey in (
            ("deaths_radiant", "deaths_radiant"),
            ("deaths_dire", "deaths_dire"),
        ):
            g_steps = death_seconds(grid_secs, [getattr(s, gkey) for s in snaps])
            l_steps = death_seconds(
                [r.game_time for r in ls_rows], [getattr(r, lkey) for r in ls_rows]
            )
            for count in sorted(set(g_steps) & set(l_steps)):
                offsets.append(g_steps[count] - l_steps[count])
        if len(offsets) < 3:
            print(f"  death offsets: too few shared deaths ({len(offsets)}); skip compare")
            continue
        offset = statistics.median(offsets)
        print(
            f"  death offset grid_label - livestats_second = {offset:+.1f}s "
            f"(n={len(offsets)} spread={max(offsets)-min(offsets):.1f})"
        )

        # pair each grid snap with livestats frame nearest to (label - offset)
        ls_times = [r.game_time for r in ls_rows]
        pairs = []
        for s in snaps:
            j = nearest_key(ls_times, s.second - offset)
            if j is not None:
                pairs.append((s, ls_rows[j]))
        print(f"  paired ticks: {len(pairs)}")

        def med(diffs):
            return statistics.median(diffs) if diffs else float("nan")

        fields = [
            ("radiant_nw", lambda s, r: s.radiant_nw - r.radiant_nw),
            ("dire_nw", lambda s, r: s.dire_nw - r.dire_nw),
            ("nw_adv", lambda s, r: s.nw_adv - r.nw_adv),
            ("xp_adv", lambda s, r: s.xp_adv - r.xp_adv),
            ("deaths_radiant", lambda s, r: s.deaths_radiant - r.deaths_radiant),
            ("deaths_dire", lambda s, r: s.deaths_dire - r.deaths_dire),
            ("top1_nw_adv", lambda s, r: s.top1_nw_adv - r.top1_nw_adv),
            ("radiant_ratio", lambda s, r: s.radiant_ratio - r.radiant_ratio),
            ("dire_ratio", lambda s, r: s.dire_ratio - r.dire_ratio),
        ]
        for name, fn in fields:
            diffs = [fn(s, r) for s, r in pairs if 0 <= s.second <= 540]
            absd = [abs(d) for d in diffs]
            print(
                f"  {name:15s} median|grid-ls|={med(absd):>9.2f}  "
                f"mean={statistics.mean(diffs):+9.2f}  p90|d|={sorted(absd)[int(0.9*len(absd))-1] if absd else float('nan'):.2f}"
            )
        # NetWorth definition test: GRID nw vs raw totalGold vs consumed-corrected
        raw_diffs = [s.radiant_nw - r.raw_radiant_nw for s, r in pairs if 0 <= s.second <= 540]
        con_diffs = [s.radiant_nw - r.radiant_nw for s, r in pairs if 0 <= s.second <= 540]
        print(
            f"  radiant_nw vs RAW totalGold:  median diff={med(raw_diffs):+.0f} "
            f"(grid = raw + this)   vs CONSUMED-corrected: median diff={med(con_diffs):+.0f}"
        )
        raw_diffs_d = [s.dire_nw - r.raw_dire_nw for s, r in pairs if 0 <= s.second <= 540]
        print(f"  dire_nw vs RAW totalGold:     median diff={med(raw_diffs_d):+.0f}")


if __name__ == "__main__":
    main()
