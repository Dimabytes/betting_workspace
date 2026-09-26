"""Measure GRID-vs-livestats clock alignment on a dual-feed capture.

Questions:
1. GRID clock-0 wall estimate (occurred_at - currentSeconds) vs livestats spawn wall.
2. For each GRID series_table tick: labeled second (live code path) vs the livestats
   game_time of the feature-matched frame -> label offset; and received_ts minus the
   matched frame's wall_seconds -> true state age at receipt.
3. GRID table tick inter-arrival cadence vs LOL_GRID_V1_BANDS (8/6/5/5).

Run:
  cd $E && PYTHONPATH=src uv run python $R/work/clock-devin/measure_dual_feed.py <run_dir>
"""

import bisect
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from lol.livestats_frames import (
    assign_game_times,
    dedup_sort_frames,
    features_from_sides,
    is_spawn_sides,
    parse_sides,
)
from lol.networth import ZERO_CONSUMED
from shared.utils.match_time import parse_utc
from trader.grid_widgets import (
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    clock_age_seconds,
    live_clock_seconds,
    parse_frame,
    read_map_scoreboard,
    read_net_worth,
)


@dataclass(frozen=True)
class GridTick:
    received_ts: float
    second: float
    blue_nw: int
    red_nw: int
    deaths_blue: int
    deaths_red: int
    feed_delay: int


@dataclass(frozen=True)
class LiveFrame:
    wall: float
    game_time: float
    blue_nw: int
    red_nw: int
    deaths_blue: int
    deaths_red: int


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def parse_grid(run_dir: Path, map_number: int) -> tuple[list[GridTick], list[float]]:
    ticks: list[GridTick] = []
    horn_estimates: list[float] = []
    board = None
    for line in read_lines(run_dir / "grid.jsonl"):
        record = json.loads(line)
        received = parse_utc(record["received_at_utc"]).timestamp()
        frame = parse_frame(record["frame"])
        if not frame.payload:
            continue
        if frame.service == SCOREBOARD_SERVICE:
            parsed = read_map_scoreboard(frame.payload, map_number)
            if parsed is not None:
                board = parsed
                if parsed.clock_ticking:
                    horn_estimates.append(
                        parse_utc(parsed.occurred_at).timestamp() - parsed.clock_seconds
                    )
            continue
        if frame.service != TABLE_SERVICE or board is None:
            continue
        table = read_net_worth(frame.payload, frame.delay)
        if table is None or table.game_number != map_number:
            continue
        if board.active_game_number != map_number:
            continue
        sides = {team.side: team.team_id for team in board.teams}
        blue_id, red_id = sides.get("BLUE"), sides.get("RED")
        if blue_id is None or red_id is None:
            continue
        blue = [p for p in table.players if p.team_id == blue_id]
        red = [p for p in table.players if p.team_id == red_id]
        if len(blue) != 5 or len(red) != 5:
            continue
        age = clock_age_seconds(board.occurred_at, parse_utc(record["received_at_utc"]))
        second = float(live_clock_seconds(board, age) - table.feed_delay)
        ticks.append(
            GridTick(
                received_ts=received,
                second=second,
                blue_nw=sum(p.net_worth for p in blue),
                red_nw=sum(p.net_worth for p in red),
                deaths_blue=sum(p.deaths for p in blue),
                deaths_red=sum(p.deaths for p in red),
                feed_delay=table.feed_delay,
            )
        )
    return ticks, horn_estimates


def parse_livestats(run_dir: Path) -> tuple[list[LiveFrame], float | None]:
    payloads: list[object] = []
    for line in read_lines(run_dir / "livestats.jsonl"):
        record = json.loads(line)
        payload = record.get("payload") if isinstance(record, dict) else None
        if payload is not None:
            payloads.append(payload)
    frames = dedup_sort_frames(payloads)
    spawn_idx = None
    for index, item in enumerate(frames):
        sides = parse_sides(item.payload)
        if isinstance(sides, int):
            continue
        if is_spawn_sides(sides):
            spawn_idx = index
            break
    if spawn_idx is None:
        # fall back: first frame with any gold, like compare script
        for index, item in enumerate(frames):
            blue = cast(dict, item.payload.get("blueTeam") or {})
            red = cast(dict, item.payload.get("redTeam") or {})
            if any(isinstance(v, int) and v > 0 for v in (blue.get("totalGold"), red.get("totalGold"))):
                spawn_idx = index
                break
    if spawn_idx is None:
        return [], None
    clock = assign_game_times(frames[spawn_idx:])
    spawn_wall = frames[spawn_idx].wall_seconds
    out: list[LiveFrame] = []
    for timed in clock.timed:
        sides = parse_sides(timed.payload)
        if isinstance(sides, int):
            continue
        feats = features_from_sides(sides, ZERO_CONSUMED)
        if feats is None:
            continue
        out.append(
            LiveFrame(
                wall=timed.wall_seconds,
                game_time=timed.game_time,
                blue_nw=feats.radiant_nw,
                red_nw=feats.dire_nw,
                deaths_blue=feats.deaths_radiant,
                deaths_red=feats.deaths_dire,
            )
        )
    return out, spawn_wall


def match_live(ticks: list[GridTick], lives: list[LiveFrame]):
    """For each GRID tick find the livestats frame with the closest raw gold sum."""
    keys = [f.game_time for f in lives]
    offsets = []  # labeled second - matched livestats game_time
    ages = []  # received_ts - matched frame wall
    for tick in ticks:
        target_nw = tick.blue_nw + tick.red_nw
        # search livestats frames within +-60s of labeled second
        best = None
        best_diff = None
        lo = bisect.bisect_left(keys, tick.second - 60)
        hi = bisect.bisect_right(keys, tick.second + 60)
        for i in range(lo, hi):
            f = lives[i]
            diff = abs(f.blue_nw + f.red_nw - target_nw) + 50 * abs(
                f.deaths_blue + f.deaths_red - tick.deaths_blue - tick.deaths_red
            )
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best = f
        if best is None:
            continue
        offsets.append(tick.second - best.game_time)
        ages.append(tick.received_ts - best.wall)
    return offsets, ages


def pct(values, p):
    s = sorted(values)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def main(run_dir: Path, map_number: int) -> None:
    ticks, horns = parse_grid(run_dir, map_number)
    lives, spawn_wall = parse_livestats(run_dir)
    print(f"grid ticks: {len(ticks)}  livestats frames: {len(lives)}")
    if horns:
        print(
            f"GRID horn estimate (occurred_at - currentSeconds): "
            f"median {statistics.median(horns):.3f}  min {min(horns):.3f}  max {max(horns):.3f}"
        )
    if spawn_wall:
        print(f"livestats spawn wall: {spawn_wall:.3f}")
    if horns and spawn_wall:
        print(f"GRID clock-0 minus livestats spawn: {statistics.median(horns) - spawn_wall:+.2f}s")
    if not ticks or not lives:
        return
    offsets, ages = match_live(ticks, lives)
    print(f"\nmatched {len(offsets)} ticks")
    print(
        "labeled_second - matched livestats game_time: "
        f"median {statistics.median(offsets):+.2f}  "
        f"p10 {pct(offsets,10):+.2f} p90 {pct(offsets,90):+.2f}"
    )
    print(
        "received_ts - matched livestats frame wall (true content age): "
        f"median {statistics.median(ages):+.2f}  "
        f"p10 {pct(ages,10):+.2f} p90 {pct(ages,90):+.2f}"
    )
    # implied content age if GRID clock 0 == livestats spawn (interpretation b):
    # label g ~ clock(T) - 8; content second = g - offset -> age = T_wall - content_wall
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    gaps = [g.received_ts - a.received_ts for a, g in zip(ticks, ticks[1:])]
    in_win = [g for t, g in zip(ticks, gaps) if 0 <= t.second <= 540]
    print(
        f"\ntable tick inter-arrival (s): median {statistics.median(gaps):.1f} "
        f"p90 {pct(gaps,90):.1f} max {max(gaps):.1f}"
    )
    print(f"labeled second range: {ticks[0].second:.0f}..{ticks[-1].second:.0f}")


if __name__ == "__main__":
    run_dir = Path(sys.argv[1])
    target = json.loads((run_dir / "target.json").read_text())
    map_number = int(target.get("map_number", 1))
    print(f"target: {target.get('slug')} map_number={map_number}")
    main(run_dir, map_number)
