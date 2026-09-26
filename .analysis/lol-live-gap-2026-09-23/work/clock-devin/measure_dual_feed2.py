"""Refined GRID-vs-livestats clock measurement: horn series, per-tick offsets, pause-aware.

cd $E && PYTHONPATH=src uv run python $R/work/clock-devin/measure_dual_feed2.py <run_dir>
"""

import bisect
import json
import statistics
import sys
from dataclasses import dataclass
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
class Board:
    received_ts: float
    occurred_ts: float
    clock_seconds: int
    ticking: bool
    game_status: str
    delay: int
    publish_delay: int


@dataclass(frozen=True)
class GridTick:
    received_ts: float
    second: float
    board_clock_at_recv: float
    board_age: float
    blue_nw: int
    red_nw: int
    deaths_blue: int
    deaths_red: int
    feed_delay: int


@dataclass(frozen=True)
class LiveFrame:
    wall: float
    game_time: float
    total_gold: int
    deaths: int


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def parse_grid(run_dir: Path, map_number: int):
    ticks: list[GridTick] = []
    boards: list[Board] = []
    board = None
    for line in read_lines(run_dir / "grid.jsonl"):
        record = json.loads(line)
        received_dt = parse_utc(record["received_at_utc"])
        received = received_dt.timestamp()
        frame = parse_frame(record["frame"])
        if not frame.payload:
            continue
        if frame.service == SCOREBOARD_SERVICE:
            parsed = read_map_scoreboard(frame.payload, map_number)
            if parsed is not None:
                board = parsed
                boards.append(
                    Board(
                        received_ts=received,
                        occurred_ts=parse_utc(parsed.occurred_at).timestamp(),
                        clock_seconds=parsed.clock_seconds,
                        ticking=parsed.clock_ticking,
                        game_status=parsed.game_status,
                        delay=frame.delay,
                        publish_delay=parsed.publish_delay,
                    )
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
        age = clock_age_seconds(board.occurred_at, received_dt)
        live_clock = live_clock_seconds(board, age)
        ticks.append(
            GridTick(
                received_ts=received,
                second=float(live_clock - table.feed_delay),
                board_clock_at_recv=float(live_clock),
                board_age=age,
                blue_nw=sum(p.net_worth for p in blue),
                red_nw=sum(p.net_worth for p in red),
                deaths_blue=sum(p.deaths for p in blue),
                deaths_red=sum(p.deaths for p in red),
                feed_delay=table.feed_delay,
            )
        )
    return ticks, boards


def parse_livestats(run_dir: Path):
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
        for index, item in enumerate(frames):
            blue = cast(dict, item.payload.get("blueTeam") or {})
            red = cast(dict, item.payload.get("redTeam") or {})
            if any(
                isinstance(v, int) and v > 0
                for v in (blue.get("totalGold"), red.get("totalGold"))
            ):
                spawn_idx = index
                break
    if spawn_idx is None:
        return [], None, []
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
                total_gold=feats.radiant_nw + feats.dire_nw,
                deaths=feats.deaths_radiant + feats.deaths_dire,
            )
        )
    return out, spawn_wall, clock.pauses


def pct(values, p):
    s = sorted(values)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def main(run_dir: Path, map_number: int) -> None:
    ticks, boards = parse_grid(run_dir, map_number)
    lives, spawn_wall, pauses = parse_livestats(run_dir)
    print(f"grid ticks={len(ticks)} boards={len(boards)} livestats frames={len(lives)}")
    print(f"livestats pauses: {[(p.start_game_time, p.duration) for p in pauses]}")

    print("\n-- boards (first 15 + around pause) --")
    for b in boards[:15]:
        horn = b.occurred_ts - b.clock_seconds
        print(
            f"recv={b.received_ts:.1f} occurred={b.occurred_ts:.1f} "
            f"clock={b.clock_seconds} ticking={b.ticking} status={b.game_status} "
            f"frame.delay={b.delay} publishDelay={b.publish_delay} "
            f"horn_est={horn:.1f} recv_minus_occurred={b.received_ts - b.occurred_ts:+.1f}"
        )
    # horn estimate restricted to pre-pause ticking boards
    pre_pause = [
        b
        for b in boards
        if b.ticking and b.game_status == "live" and (not pauses or b.clock_seconds < pauses[0].start_game_time - 60)
    ]
    horns = [b.occurred_ts - b.clock_seconds for b in pre_pause]
    if horns and spawn_wall:
        med = statistics.median(horns)
        print(
            f"\nGRID clock-0 (pre-pause boards, n={len(horns)}): median {med:.2f}; "
            f"minus livestats spawn {spawn_wall:.2f} = {med - spawn_wall:+.2f}s"
        )
        print(f"  horn spread pre-pause: {min(horns):.2f}..{max(horns):.2f}")

    # board publish lag distribution
    lags = [b.received_ts - b.occurred_ts for b in boards]
    print(
        f"scoreboard received - occurred_at: median {statistics.median(lags):.2f} "
        f"p10 {pct(lags,10):.2f} p90 {pct(lags,90):.2f}"
    )

    # per-tick: match livestats frame by total gold + deaths within +-90s window
    keys = [f.game_time for f in lives]
    print("\n-- per-tick: label offset and content age (feature-matched) --")
    rows = []
    for tick in ticks:
        lo = bisect.bisect_left(keys, tick.second - 90)
        hi = bisect.bisect_right(keys, tick.second + 90)
        best, best_diff = None, None
        for i in range(lo, hi):
            f = lives[i]
            diff = abs(f.total_gold - (tick.blue_nw + tick.red_nw)) + 500 * abs(
                f.deaths - (tick.deaths_blue + tick.deaths_red)
            )
            if best_diff is None or diff < best_diff:
                best, best_diff = f, diff
        if best is None:
            continue
        rows.append(
            (
                tick.second,
                tick.second - best.game_time,      # label offset
                tick.received_ts - best.wall,       # content age at receipt
                tick.received_ts,
            )
        )
    # bucket by labeled second decile
    if not rows:
        return
    for lo_s in range(0, 800, 100):
        bucket = [r for r in rows if lo_s <= r[0] < lo_s + 100]
        if len(bucket) < 3:
            continue
        off = [r[1] for r in bucket]
        age = [r[2] for r in bucket]
        print(
            f"second {lo_s:>3}-{lo_s+100:<3} n={len(bucket):>3} "
            f"label_offset med {statistics.median(off):+.1f} "
            f"content_age med {statistics.median(age):+.1f} "
            f"p10 {pct(age,10):+.1f} p90 {pct(age,90):+.1f}"
        )

    gaps = [b.received_ts - a.received_ts for a, b in zip(ticks, ticks[1:])]
    print(
        f"\ntable tick inter-arrival: median {statistics.median(gaps):.1f} "
        f"p10 {pct(gaps,10):.1f} p90 {pct(gaps,90):.1f} max {max(gaps):.1f} (n={len(gaps)})"
    )
    print(f"table feed_delay values: {sorted(set(t.feed_delay for t in ticks))}")
    print(f"labeled second range: {ticks[0].second:.0f}..{ticks[-1].second:.0f}")


if __name__ == "__main__":
    run_dir = Path(sys.argv[1])
    target = json.loads((run_dir / "target.json").read_text())
    map_number = int(target.get("map_number", 1))
    print(f"target: {target.get('slug')} map_number={map_number}")
    main(run_dir, map_number)
