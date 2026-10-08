"""Rebuild GRID-style LoL net worth from lolesports history and score it on a dual recording.

GRID `NetWorth` is `Money + LoadoutValue` (current value), while livestats
`totalGold` is everything earned. Per the GRID FAQ the gap is the value of
consumed items, so the reconstruction is:

    nw_rec(side, t) = window totalGold(side, t) - consumed_value(side, t)

where consumption is an inventory-count drop of a consumed-flagged item between
consecutive `details` frames, priced by the Data Dragon table for the recording's
window `patchVersion`. The script fetches (and caches) the historical `details`
frames for the recorded game, then reports how much closer `nw_rec` sits to the
recorded GRID net worth than raw `totalGold` does.

    make run F=scripts/reconstruct_lol_networth.py ARGS="data/lol_dual_feed/<run> --offset 7"

`--offset` is the grid-minus-livestats clock offset the comparator prints
(the death-derived one).
"""

import argparse
import json
import math
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import numpy as np
from compare_lol_grid_livestats import (
    Sample,
    nearest_index,
    open_lines,
    read_grid,
    stamped_livestats,
)

from lol.livestats_frames import (
    FrameFeatures,
    ParsedSides,
    StampedFrame,
    assign_game_times,
    dedup_sort_frames,
    features_from_sides,
    game_patch_from_payloads,
    parse_sides,
)
from lol.networth import (
    DEFAULT_ITEM_CATALOG_DIR,
    ConsumedTimeline,
    ItemCatalog,
    ItemTable,
    build_consumed_timeline,
    load_item_catalog,
    table_for_game_patch,
)
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.utils.dota_features import (
    DOTA_HISTORY_COLUMN_NAMES,
    GRID_HISTORY_POLICY,
    SnapshotHistory,
    replay_tape_history,
    snapshot_history_levels,
)
from shared.utils.log import get_logger, setup_logging

logger = get_logger(__name__)

LIVESTATS_DETAILS = "https://feed.lolesports.com/livestats/v1/details"
FETCH_STEP_SECONDS = 10
FETCH_SLEEP_SECONDS = 0.15
BLUE_PARTICIPANTS = range(1, 6)
RED_PARTICIPANTS = range(6, 11)


@dataclass(frozen=True)
class WindowPoint:
    """One post-spawn window frame: its stamp, game second, and parsed sides."""

    stamp: str
    game_time: float
    sides: ParsedSides


@dataclass(frozen=True)
class ScoredPair:
    """One livestats frame matched to a GRID sample, with reconstructed state.

    `grid_history` and `rec_history` are the 60 derived tape columns each side
    would expose at the pair second, replayed on each source's own tape.
    """

    grid: Sample
    rec: FrameFeatures
    raw_blue: int
    raw_red: int
    game_time: float
    grid_history: Mapping[str, float]
    rec_history: Mapping[str, float]


def read_target_game_id(run_dir: Path) -> str:
    """The esports game id the recorder wrote into target.json."""
    target = cast(dict[str, object], json.loads((run_dir / "target.json").read_text()))
    return str(target["esports_game_id"])


def parse_stamp(stamp: str) -> datetime:
    """Parse one rfc460Timestamp into an aware datetime."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def recorded_stamp_range(run_dir: Path) -> tuple[datetime, datetime]:
    """First and last frame stamp of the recorded livestats windows."""
    stamps: list[str] = []
    for line in open_lines(run_dir, "livestats"):
        record = cast(dict[str, object], json.loads(line))
        payload = cast(dict[str, object], record.get("payload") or {})
        frames = payload.get("frames")
        if not isinstance(frames, list):
            continue
        for item in cast(list[object], frames):
            if not isinstance(item, dict):
                continue
            stamp = cast(dict[str, object], item).get("rfc460Timestamp")
            if isinstance(stamp, str) and stamp:
                stamps.append(stamp)
    if not stamps:
        raise SystemExit(f"no livestats frames in {run_dir}")
    return parse_stamp(min(stamps)), parse_stamp(max(stamps))


def fetch_details(run_dir: Path, game_id: str) -> Path:
    """Download the historical details frames covering the recording; cache as details.jsonl."""
    cache = run_dir / "details.jsonl"
    if cache.exists() or (run_dir / "details.jsonl.gz").exists():
        return cache
    first, last = recorded_stamp_range(run_dir)
    by_stamp: dict[str, dict[str, object]] = {}
    moment = first.replace(microsecond=0)
    with httpx.Client(timeout=20.0) as client:
        while moment <= last:
            starting = moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            response = client.get(
                f"{LIVESTATS_DETAILS}/{game_id}", params={"startingTime": starting}
            )
            if response.status_code == 200:
                body = cast(dict[str, object], response.json())
                frames = body.get("frames")
                if isinstance(frames, list):
                    for item in cast(list[object], frames):
                        if not isinstance(item, dict):
                            continue
                        frame = cast(dict[str, object], item)
                        stamp = frame.get("rfc460Timestamp")
                        if isinstance(stamp, str) and stamp:
                            by_stamp[stamp] = frame
            else:
                logger.info("details %s -> %d", starting, response.status_code)
            moment += timedelta(seconds=FETCH_STEP_SECONDS)
            time.sleep(FETCH_SLEEP_SECONDS)
    with cache.open("w", encoding="utf-8") as handle:
        for stamp in sorted(by_stamp):
            handle.write(json.dumps(by_stamp[stamp]) + "\n")
    logger.info("fetched %d details frames -> %s", len(by_stamp), cache)
    return cache


def recording_window_payloads(run_dir: Path) -> list[object]:
    """Unwrap dual-feed livestats records into window envelopes."""
    payloads: list[object] = []
    for line in open_lines(run_dir, "livestats"):
        record = json.loads(line)
        if not isinstance(record, dict):
            continue
        payload = cast(dict[str, object], record).get("payload")
        if payload is not None:
            payloads.append(payload)
    return payloads


def item_table_for_recording(run_dir: Path, catalog: ItemCatalog) -> ItemTable:
    """Pick the catalog table for the recording's window patchVersion."""
    payloads = recording_window_payloads(run_dir)
    game_patch = game_patch_from_payloads(payloads)
    if game_patch is None:
        raise SystemExit(f"no single patchVersion in {run_dir} livestats")
    table = table_for_game_patch(catalog, game_patch)
    logger.info("using item table %s for game patch %s", table.version, game_patch)
    return table


def track_consumed(run_dir: Path, item_table: ItemTable) -> ConsumedTimeline:
    """Stamp -> cumulative consumed-item gold per participant, from details inventory diffs."""
    raw_frames: list[object] = [json.loads(line) for line in open_lines(run_dir, "details")]
    stamped = dedup_sort_frames([{"frames": raw_frames}])
    timeline = build_consumed_timeline([frame.payload for frame in stamped], item_table)
    if timeline.by_stamp:
        last = timeline.by_stamp[max(timeline.by_stamp)]
        logger.info(
            "final consumed gold: blue %d, red %d",
            sum(last.by_participant[pid] for pid in BLUE_PARTICIPANTS),
            sum(last.by_participant[pid] for pid in RED_PARTICIPANTS),
        )
    return timeline


def team_total_gold(payload: dict[str, object], side: str) -> int:
    """One side's totalGold in a window frame, 0 when absent."""
    team = payload.get(side)
    if not isinstance(team, dict):
        return 0
    value = cast(dict[str, object], team).get("totalGold")
    return value if isinstance(value, int) else 0


def read_window_points(run_dir: Path) -> list[WindowPoint]:
    """Every post-spawn window frame with its pause-aware game second and side gold."""
    post_spawn: list[StampedFrame] = []
    for item in stamped_livestats(run_dir):
        gold = team_total_gold(item.payload, "blueTeam") + team_total_gold(item.payload, "redTeam")
        if post_spawn or gold > 0:
            post_spawn.append(item)
    if not post_spawn:
        return []
    clock = assign_game_times(post_spawn)
    points: list[WindowPoint] = []
    for timed in clock.timed:
        stamp = timed.payload.get("rfc460Timestamp")
        if not isinstance(stamp, str):
            continue
        sides = parse_sides(timed.payload)
        if isinstance(sides, int):
            continue
        points.append(WindowPoint(stamp=stamp, game_time=timed.game_time, sides=sides))
    return points


def grid_derived_history(grid: list[Sample]) -> list[dict[str, float]]:
    """Replay the GRID tape in receive order; one derived block per sample.

    Receive order matters: on a backwards game-time step a sorted replay
    would let a decision see snapshots the feed had not delivered yet.
    """
    seconds = [round(sample.second) for sample in grid]
    levels = np.asarray([snapshot_history_levels(sample) for sample in grid], dtype=np.float64)
    frame = replay_tape_history(
        seconds,
        levels,
        [True] * len(grid),
        range(len(grid)),
        GRID_HISTORY_POLICY,
    )
    return cast(list[dict[str, float]], frame.to_dict("records"))


def build_pairs(
    points: list[WindowPoint],
    consumed: ConsumedTimeline,
    grid: list[Sample],
    offset: int,
) -> list[ScoredPair]:
    """Match every window frame to a GRID sample and attach reconstructed state.

    Both tapes record every usable frame in receive order, so each pair's
    derived history sees the same as-of pivots the live worker's tape
    would; the GRID side sorts a copy for nearest-second lookup only.
    """
    order = sorted(range(len(grid)), key=lambda index: grid[index].second)
    sorted_grid = [grid[index] for index in order]
    keys = [sample.second for sample in sorted_grid]
    grid_history = grid_derived_history(grid)
    rec_tape = SnapshotHistory(GRID_HISTORY_POLICY)
    pairs: list[ScoredPair] = []
    missing = 0
    bad = 0
    for point in points:
        consumed_frame = consumed.by_stamp.get(point.stamp)
        if consumed_frame is None:
            missing += 1
            continue
        features = features_from_sides(point.sides, consumed_frame)
        if features is None:
            bad += 1
            continue
        second = round(point.game_time)
        levels = snapshot_history_levels(features)
        rec_tape.record(second, levels)
        sorted_index = nearest_index(sorted_grid, keys, point.game_time + offset)
        if sorted_index is None:
            continue
        grid_index = order[sorted_index]
        pairs.append(
            ScoredPair(
                grid=grid[grid_index],
                rec=features,
                raw_blue=point.sides.blue.total_gold,
                raw_red=point.sides.red.total_gold,
                game_time=point.game_time,
                grid_history=grid_history[grid_index],
                rec_history=rec_tape.derived(second, levels),
            )
        )
    logger.info(
        "paired %d window frames at offset %+ds (%d without details, %d negative reconstruction)",
        len(pairs),
        offset,
        missing,
        bad,
    )
    return pairs


def percentile(values: list[float], quantile: float) -> float:
    """The nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    return ordered[int(quantile * (len(ordered) - 1))]


def report_side(label: str, rec: Sequence[float], grid: Sequence[float]) -> None:
    """Print the absolute-gap median and ratio percentiles of one side."""
    gaps = [abs(float(a - b)) for a, b in zip(rec, grid, strict=True)]
    ratios = [a / b for a, b in zip(rec, grid, strict=True) if b > 0]
    logger.info(
        "  %s: median|x-grid|=%6.0f  ratio p10=%.3f median=%.3f p90=%.3f",
        label,
        statistics.median(gaps),
        percentile(ratios, 0.1),
        statistics.median(ratios),
        percentile(ratios, 0.9),
    )


def report_advantage(label: str, adv: Sequence[float], grid_adv: Sequence[float]) -> None:
    """Print gap percentiles and sign flips of one nw_adv variant against GRID."""
    gaps = [float(a - b) for a, b in zip(adv, grid_adv, strict=True)]
    flips = sum(1 for a, b in zip(adv, grid_adv, strict=True) if a * b < 0)
    logger.info(
        "  %s: gap p10=%+.0f median=%+.0f p90=%+.0f  sign flips %d/%d (%.1f%%)",
        label,
        percentile(gaps, 0.1),
        statistics.median(gaps),
        percentile(gaps, 0.9),
        flips,
        len(adv),
        100.0 * flips / len(adv),
    )


def report_history(pairs: list[ScoredPair], label: str) -> None:
    """Median |grid-rec| diff and NaN share per derived history column."""
    logger.info("  derived history columns %s (n=%d pairs):", label, len(pairs))
    for name in DOTA_HISTORY_COLUMN_NAMES:
        grid_values = [pair.grid_history[name] for pair in pairs]
        rec_values = [pair.rec_history[name] for pair in pairs]
        diffs = [
            abs(grid - rec)
            for grid, rec in zip(grid_values, rec_values, strict=True)
            if math.isfinite(grid) and math.isfinite(rec)
        ]
        grid_nan = sum(1 for value in grid_values if math.isnan(value))
        rec_nan = sum(1 for value in rec_values if math.isnan(value))
        logger.info(
            "    %-42s median|grid-rec|=%10.4f n=%d  nan: grid=%.0f%% rec=%.0f%%",
            name,
            statistics.median(diffs) if diffs else float("nan"),
            len(diffs),
            100.0 * grid_nan / len(pairs),
            100.0 * rec_nan / len(pairs),
        )


def report_pairs(pairs: list[ScoredPair]) -> None:
    """Print reconstruction quality next to the raw totalGold baseline."""
    grid_blue = [pair.grid.radiant_nw for pair in pairs]
    grid_red = [pair.grid.dire_nw for pair in pairs]
    report_side("rec blue", [pair.rec.radiant_nw for pair in pairs], grid_blue)
    report_side("rec red ", [pair.rec.dire_nw for pair in pairs], grid_red)
    report_side("raw blue", [pair.raw_blue for pair in pairs], grid_blue)
    report_side("raw red ", [pair.raw_red for pair in pairs], grid_red)
    grid_adv = [pair.grid.radiant_nw_adv for pair in pairs]
    report_advantage("rec nw_adv", [pair.rec.radiant_nw_adv for pair in pairs], grid_adv)
    report_advantage("raw nw_adv", [pair.raw_blue - pair.raw_red for pair in pairs], grid_adv)
    report_advantage(
        "rec top3_nw_adv",
        [pair.rec.top.top3_nw_adv for pair in pairs],
        [pair.grid.top.top3_nw_adv for pair in pairs],
    )
    report_side(
        "rec top1 blue share",
        [pair.rec.top.radiant_top1_nw_ratio for pair in pairs],
        [pair.grid.top.radiant_top1_nw_ratio for pair in pairs],
    )
    report_side(
        "rec top1 red share ",
        [pair.rec.top.dire_top1_nw_ratio for pair in pairs],
        [pair.grid.top.dire_top1_nw_ratio for pair in pairs],
    )
    report_side(
        "rec top3 blue share",
        [pair.rec.top.radiant_top3_nw_ratio for pair in pairs],
        [pair.grid.top.radiant_top3_nw_ratio for pair in pairs],
    )
    report_side(
        "rec top3 red share ",
        [pair.rec.top.dire_top3_nw_ratio for pair in pairs],
        [pair.grid.top.dire_top3_nw_ratio for pair in pairs],
    )
    buckets: dict[int, list[float]] = {}
    for pair in pairs:
        buckets.setdefault(int(pair.game_time // 120), []).append(
            float(pair.rec.radiant_nw - pair.grid.radiant_nw)
        )
    logger.info("  rec-grid blue residual by 2-minute bucket:")
    for bucket in sorted(buckets):
        values = buckets[bucket]
        logger.info(
            "    %3d-%d min: median=%+.0f n=%d",
            bucket * 2,
            bucket * 2 + 2,
            statistics.median(values),
            len(values),
        )
    report_history(pairs, "over all paired seconds")
    late = [pair for pair in pairs if pair.game_time >= BUY_CUTOFF_SECOND]
    if late:
        report_history(late, f"at/after buy cutoff {BUY_CUTOFF_SECOND}")


def main(argv: list[str]) -> int:
    """Fetch details for one dual recording and score the net-worth reconstruction."""
    setup_logging()
    parser = argparse.ArgumentParser(prog="reconstruct-lol-networth")
    parser.add_argument("run_dir")
    parser.add_argument("--offset", type=int, required=True)
    parser.add_argument("--item-catalog-dir", type=Path, default=DEFAULT_ITEM_CATALOG_DIR)
    args = parser.parse_args(argv)
    run_dir = Path(cast(str, args.run_dir))
    offset = cast(int, args.offset)
    catalog_dir = cast(Path, args.item_catalog_dir)
    game_id = read_target_game_id(run_dir)
    fetch_details(run_dir, game_id)
    prices = item_table_for_recording(run_dir, load_item_catalog(catalog_dir))
    consumed = track_consumed(run_dir, prices)
    points = read_window_points(run_dir)
    grid, _, _ = read_grid(run_dir)
    if not points or not grid or not consumed.by_stamp:
        logger.error("need window, grid, and details data; nothing to compare")
        return 1
    pairs = build_pairs(points, consumed, grid, offset)
    if not pairs:
        logger.error("no pairs at offset %+d", offset)
        return 1
    report_pairs(pairs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
