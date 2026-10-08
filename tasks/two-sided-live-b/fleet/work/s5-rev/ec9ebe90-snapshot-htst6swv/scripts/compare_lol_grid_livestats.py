"""Compare a recorded LoL map between the GRID widget and lolesports livestats.

This script measures the raw window feed (`totalGold`) against GRID. It does
not reconstruct consumed-item net worth; that comparison is
`scripts/reconstruct_lol_networth.py`.

Answers two questions the live-LoL plan depends on:

1. Do the two clocks share a zero? The script searches the offset that best
   aligns the two raw team-gold curves.
2. Do raw livestats features agree with GRID at that offset?

    make run F=scripts/compare_lol_grid_livestats.py ARGS="data/lol_dual_feed/<run>"
"""

import argparse
import bisect
import gzip
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from operator import attrgetter
from pathlib import Path
from typing import cast

from lol.livestats_frames import (
    StampedFrame,
    assign_game_times,
    dedup_sort_frames,
    features_from_sides,
    parse_sides,
)
from lol.networth import ZERO_CONSUMED
from shared.constants.lol import LOL_LEVEL_XP
from shared.utils.level_xp import xp_advantage
from shared.utils.log import get_logger, setup_logging
from shared.utils.top_players import (
    TopPlayerFeatures,
    build_top_player_features_over_total,
)
from trader.grid_widgets import (
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    NetWorthSnapshot,
    PlayerNetWorth,
    Scoreboard,
    clock_age_seconds,
    live_clock_seconds,
    parse_frame,
    read_current_scoreboard,
    read_net_worth,
    select_side_players,
    side_team,
)

logger = get_logger(__name__)

BLUE_SIDE = "BLUE"
RED_SIDE = "RED"
OFFSET_RANGE = 120
MATCH_TOLERANCE_SECONDS = 2.0


@dataclass(frozen=True)
class Sample:
    """One aligned state reading from either source.

    Field names match the shared catalog, so a Sample satisfies the
    shared SnapshotState contract. `top` carries the over-total player
    features; the top-one ratio over rest stays alongside as the live
    top_players diagnostic.
    """

    second: float
    radiant_nw: int
    dire_nw: int
    radiant_nw_adv: int
    radiant_xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top: TopPlayerFeatures
    top1_ratio_over_rest: float


@dataclass(frozen=True)
class OffsetScore:
    """Median absolute blue-gold gap and pair count at one GRID-minus-livestats offset."""

    offset: int
    median_gap: float
    pairs: int


@dataclass(frozen=True)
class GridLivePair:
    """A GRID sample matched to a livestats sample at one clock offset."""

    grid: Sample
    live: Sample


def open_lines(run_dir: Path, name: str) -> list[str]:
    """Read one recording file, plain or gzipped, as a list of JSON lines."""
    plain = run_dir / f"{name}.jsonl"
    if plain.exists():
        return plain.read_text(encoding="utf-8").splitlines()
    packed = run_dir / f"{name}.jsonl.gz"
    if packed.exists():
        with gzip.open(packed, "rt", encoding="utf-8") as handle:
            return handle.read().splitlines()
    raise SystemExit(f"no {name}.jsonl or {name}.jsonl.gz in {run_dir}")


def stamped_livestats(run_dir: Path) -> list[StampedFrame]:
    """Unique livestats window frames from a dual-feed recording, sorted by wall time."""
    payloads: list[object] = []
    for line in open_lines(run_dir, "livestats"):
        record = json.loads(line)
        if not isinstance(record, dict):
            continue
        payload = cast(dict[str, object], record).get("payload")
        if payload is not None:
            payloads.append(payload)
    return dedup_sort_frames(payloads)


def ratio_over_rest(values: list[int]) -> float:
    """Top net worth divided by the sum of the rest, the live top_players rule."""
    if not values:
        return 0.0
    top = max(values)
    rest = sum(values) - top
    if rest == 0:
        return 0.0
    return top / rest


def build_sample(
    second: float, blue: tuple[PlayerNetWorth, ...], red: tuple[PlayerNetWorth, ...]
) -> Sample:
    """Build one comparison sample from two five-player sides of the GRID table."""
    blue_nw = [player.net_worth for player in blue]
    red_nw = [player.net_worth for player in red]
    return Sample(
        second=second,
        radiant_nw=sum(blue_nw),
        dire_nw=sum(red_nw),
        radiant_nw_adv=sum(blue_nw) - sum(red_nw),
        radiant_xp_adv=xp_advantage(
            LOL_LEVEL_XP,
            [player.level for player in blue],
            [player.level for player in red],
        ),
        deaths_radiant=sum(player.deaths for player in blue),
        deaths_dire=sum(player.deaths for player in red),
        top=build_top_player_features_over_total(blue_nw, red_nw),
        top1_ratio_over_rest=ratio_over_rest(blue_nw),
    )


def side_ids(board: Scoreboard) -> tuple[str, str] | None:
    """GRID team ids of BLUE and RED, or None when the board does not name both."""
    blue = side_team(board, BLUE_SIDE)
    red = side_team(board, RED_SIDE)
    if blue is None or red is None:
        return None
    return blue.team_id, red.team_id


def read_grid(run_dir: Path) -> tuple[list[Sample], list[int], list[str]]:
    """Grid samples in receive order, table delays, and the side labels seen.

    Samples keep the recording's order so history replays see the tape as
    it arrived; callers that need second-sorted lookup sort a copy.
    """
    samples: list[Sample] = []
    delays: list[int] = []
    labels: set[str] = set()
    board: Scoreboard | None = None
    for line in open_lines(run_dir, "grid"):
        record = cast(dict[str, object], json.loads(line))
        received = datetime.fromisoformat(str(record["received_at_utc"]).replace("Z", "+00:00"))
        try:
            frame = parse_frame(str(record["frame"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        if not frame.payload:
            continue
        if frame.service == SCOREBOARD_SERVICE:
            parsed = read_current_scoreboard(frame.payload)
            if parsed is not None:
                board = parsed
                labels.update(team.side for team in parsed.teams)
            continue
        if frame.service != TABLE_SERVICE or board is None:
            continue
        table: NetWorthSnapshot | None = read_net_worth(frame.payload, frame.delay)
        if table is None:
            continue
        ids = side_ids(board)
        if ids is None:
            continue
        blue_id, red_id = ids
        blue = select_side_players(table, blue_id)
        red = select_side_players(table, red_id)
        if blue is None or red is None:
            continue
        age = clock_age_seconds(board.occurred_at, received)
        second = float(live_clock_seconds(board, age) - table.feed_delay)
        delays.append(table.feed_delay)
        samples.append(build_sample(second, blue, red))
    return samples, delays, sorted(labels)


def read_livestats(run_dir: Path) -> list[Sample]:
    """Reduce the livestats recording to spawn-clocked samples, one per unique stamp."""
    post_spawn: list[StampedFrame] = []
    for item in stamped_livestats(run_dir):
        blue = cast(dict[str, object], item.payload.get("blueTeam") or {})
        red = cast(dict[str, object], item.payload.get("redTeam") or {})
        gold = (blue.get("totalGold") or 0, red.get("totalGold") or 0)
        if post_spawn or any(isinstance(value, int) and value > 0 for value in gold):
            post_spawn.append(item)
    if not post_spawn:
        return []
    clock = assign_game_times(post_spawn)
    logger.info("livestats pauses: %d totalling %.1fs", clock.pause_count, clock.pause_seconds)
    samples: list[Sample] = []
    for timed in clock.timed:
        sides = parse_sides(timed.payload)
        if isinstance(sides, int):
            continue
        blue_gold = [player.gold for player in sides.blue.players]
        features = features_from_sides(sides, ZERO_CONSUMED)
        if features is None:
            continue
        samples.append(
            Sample(
                second=timed.game_time,
                radiant_nw=features.radiant_nw,
                dire_nw=features.dire_nw,
                radiant_nw_adv=features.radiant_nw_adv,
                radiant_xp_adv=features.radiant_xp_adv,
                deaths_radiant=features.deaths_radiant,
                deaths_dire=features.deaths_dire,
                top=features.top,
                top1_ratio_over_rest=ratio_over_rest(blue_gold),
            )
        )
    return samples


def nearest_index(samples: list[Sample], keys: list[float], second: float) -> int | None:
    """Index of the sample closest to `second`, or None beyond the tolerance.

    `keys` is the sorted second of every sample, so the scan is a bisect.
    The caller indexes `samples` itself; the return is just a position.
    """
    index = bisect.bisect_left(keys, second)
    best: int | None = None
    best_gap = MATCH_TOLERANCE_SECONDS
    for candidate in (index - 1, index):
        if candidate < 0 or candidate >= len(samples):
            continue
        gap = abs(keys[candidate] - second)
        if gap <= best_gap:
            best_gap = gap
            best = candidate
    return best


def death_seconds(samples: list[Sample], field: str) -> dict[int, float]:
    """The second at which each death count was first reached on one side."""
    reached: dict[int, float] = {}
    previous = 0
    for sample in samples:
        value = int(getattr(sample, field))
        if value <= previous:
            continue
        for count in range(previous + 1, value + 1):
            reached[count] = sample.second
        previous = value
    return reached


def death_offsets(grid: list[Sample], live: list[Sample]) -> list[float]:
    """GRID minus livestats second for every death both feeds recorded.

    Deaths are steps, so they date the two clocks far more sharply than the
    gold curves, which are nearly flat in the first minutes.
    """
    offsets: list[float] = []
    for field in ("deaths_radiant", "deaths_dire"):
        grid_steps = death_seconds(grid, field)
        live_steps = death_seconds(live, field)
        for count in sorted(set(grid_steps) & set(live_steps)):
            offsets.append(grid_steps[count] - live_steps[count])
    return offsets


def score_offset(
    grid: list[Sample], keys: list[float], live: list[Sample], offset: int
) -> OffsetScore:
    """Median absolute blue-gold gap and the pair count at one GRID-minus-livestats offset."""
    gaps: list[float] = []
    for sample in live:
        index = nearest_index(grid, keys, sample.second + offset)
        if index is None:
            continue
        gaps.append(abs(grid[index].radiant_nw - sample.radiant_nw))
    median_gap = statistics.median(gaps) if len(gaps) >= 10 else float("inf")
    return OffsetScore(offset=offset, median_gap=median_gap, pairs=len(gaps))


def report_pairs(grid: list[Sample], keys: list[float], live: list[Sample], offset: int) -> None:
    """Print per-feature agreement at one offset."""
    pairs: list[GridLivePair] = []
    for sample in live:
        index = nearest_index(grid, keys, sample.second + offset)
        if index is not None:
            pairs.append(GridLivePair(grid=grid[index], live=sample))
    logger.info("paired samples at offset %+ds: %d", offset, len(pairs))
    if not pairs:
        return

    def gap(field: str) -> None:
        """Print the median absolute gap of one field over every paired sample."""
        read = attrgetter(field)
        values = [abs(float(read(pair.grid)) - float(read(pair.live))) for pair in pairs]
        base = [abs(float(read(pair.live))) for pair in pairs]
        median_gap = statistics.median(values)
        median_base = statistics.median(base)
        share = 100.0 * median_gap / median_base if median_base else 0.0
        logger.info(
            "  %-24s median |grid-live| = %10.2f  (%.1f%% of livestats)",
            field,
            median_gap,
            share,
        )

    for field in (
        "radiant_nw",
        "dire_nw",
        "radiant_nw_adv",
        "radiant_xp_adv",
        "deaths_radiant",
        "deaths_dire",
        "top1_ratio_over_rest",
        "top.top1_nw_adv",
        "top.radiant_top1_nw_ratio",
        "top.dire_top1_nw_ratio",
        "top.top3_nw_adv",
        "top.radiant_top3_nw_ratio",
        "top.dire_top3_nw_ratio",
    ):
        gap(field)


def main(argv: list[str]) -> int:
    """Load one recording, find the clock offset, and report feature agreement."""
    setup_logging()
    parser = argparse.ArgumentParser(prog="compare-lol-grid-livestats")
    parser.add_argument("run_dir")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    grid, delays, labels = read_grid(run_dir)
    live = read_livestats(run_dir)
    logger.info("grid samples: %d, livestats samples: %d", len(grid), len(live))
    logger.info("grid side labels seen: %s", ", ".join(labels) or "-")
    if delays:
        logger.info(
            "grid table feed_delay: median %.0f min %d max %d",
            statistics.median(delays),
            min(delays),
            max(delays),
        )
    if not grid or not live:
        logger.error("need both feeds; nothing to compare")
        return 1
    sorted_grid = sorted(grid, key=lambda sample: sample.second)
    keys = [sample.second for sample in sorted_grid]
    logger.info("grid second range: %.0f .. %.0f", keys[0], keys[-1])
    logger.info("livestats second range: %.0f .. %.0f", live[0].second, live[-1].second)
    scored = [
        score_offset(sorted_grid, keys, live, offset)
        for offset in range(-OFFSET_RANGE, OFFSET_RANGE + 1)
    ]
    usable = [score for score in scored if score.median_gap != float("inf")]
    if not usable:
        logger.error("no offset pairs enough samples; record a longer map")
        return 1
    best = min(usable, key=lambda score: score.median_gap)
    logger.info("gold-fit offset: grid_second = livestats_second %+d", best.offset)
    logger.info("  median blue-gold gap there: %.0f over %d pairs", best.median_gap, best.pairs)
    logger.info("  the gold curve is flat early, so this estimate is the weak one")
    deaths = death_offsets(grid, live)
    if len(deaths) < 3:
        logger.warning("only %d shared death events: trusting the gold fit", len(deaths))
        report_pairs(sorted_grid, keys, live, best.offset)
        return 0
    death_offset = statistics.median(deaths)
    logger.info(
        "death offset: grid_second = livestats_second %+.1f (n=%d spread=%.1f)",
        death_offset,
        len(deaths),
        max(deaths) - min(deaths),
    )
    for value in deaths:
        logger.info("  death event offset %+.1f", value)
    report_pairs(sorted_grid, keys, live, round(death_offset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
