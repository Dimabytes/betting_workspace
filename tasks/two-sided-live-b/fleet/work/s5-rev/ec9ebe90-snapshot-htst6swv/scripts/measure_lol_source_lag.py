"""Read-only LoL source-lag probe: deaths jumps vs Telonex midpoint moves."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Annotated, Protocol, cast

import numpy as np
import pandas as pd
import typer

from lol.constants import (
    LOL_LAG_CANDIDATE_MAX_SECONDS,
    LOL_LINKS_PATH,
    LOL_PREPARE_END_SECOND,
    LOL_TELONEX_CATALOG_PATH,
    LOL_WINDOWS_DIR,
)
from lol.livestats_frames import (
    TimedFrame,
    assign_game_times,
    find_spawn_index,
    read_archive_frames,
    validate_timed_frames,
)
from lol.networth import ZERO_CONSUMED, ConsumedTimeline
from lol.parquet_io import read_parquet_rows
from lol.types import LolLinkRow
from shared.constants.lol import LOL_RAW_TELONEX_DIR, LOL_SOURCE_LAG_SECONDS
from shared.utils.telonex_book import (
    US_PER_SECOND,
    TokenBook,
    load_token_book,
    lookup_market_p_after,
)


@dataclass(frozen=True)
class OrientedTokens:
    """Radiant and dire CLOB token ids."""

    radiant: str
    dire: str


@dataclass(frozen=True)
class LagScore:
    """Pooled Pearson score for one candidate lag."""

    lag_seconds: int
    correlation: float
    n_pairs: int


@dataclass(frozen=True)
class MapLagSeries:
    """Death jumps and midpoint moves for one accepted map."""

    jumps: dict[int, float]
    moves: dict[int, float]


class PrepareStage(Protocol):
    """Stage 05 token orientation."""

    def parse_token_pair(self, link: LolLinkRow) -> OrientedTokens | None:
        """Oriented radiant/dire token ids, or None."""
        ...


PREPARE = cast(PrepareStage, cast(object, import_module("lol.05_prepare_dataset")))


def require_lag_inputs(
    links_path: Path,
    windows_dir: Path,
    catalog_path: Path,
    telonex_root: Path,
) -> str | None:
    """Return a reason when a required lag-probe input is missing."""
    if not links_path.is_file():
        return f"missing links parquet: {links_path}"
    if not windows_dir.is_dir():
        return f"missing livestats windows: {windows_dir}"
    if not catalog_path.is_file():
        return f"missing catalog parquet: {catalog_path}"
    books_root = telonex_root / "book_snapshot_full"
    if not books_root.is_dir():
        return f"missing telonex books: {books_root}"
    return None


def load_timed_frames(link: LolLinkRow, windows_dir: Path) -> tuple[TimedFrame, ...] | None:
    """Post-spawn invariant-ok frames, or None when the map cannot be clocked."""
    frames = read_archive_frames(windows_dir, str(link["esports_game_id"]))
    if frames is None or not frames:
        return None
    spawn_index = find_spawn_index(frames, int(link["loading_anchor_ts"]))
    if spawn_index is None:
        return None
    clock = assign_game_times(frames[spawn_index:])
    consumed = ConsumedTimeline({frame.stamp: ZERO_CONSUMED for frame in frames[spawn_index:]})
    validated = validate_timed_frames(clock.timed, consumed, LOL_PREPARE_END_SECOND)
    if isinstance(validated, str) or not validated.frames:
        return None
    return validated.frames


def death_jumps(timed: Sequence[TimedFrame]) -> dict[int, float]:
    """Mark integer wall seconds where total deaths increased vs the previous frame."""
    jumps: dict[int, float] = {}
    previous: int | None = None
    for frame in timed:
        deaths = frame.features.deaths_radiant + frame.features.deaths_dire
        if previous is not None and deaths > previous:
            jumps[int(frame.wall_seconds)] = 1.0
        previous = deaths
    return jumps


def oriented_tokens(link: LolLinkRow) -> OrientedTokens | None:
    """Read radiant/dire token ids from the link, or None when malformed."""
    parsed = PREPARE.parse_token_pair(link)
    if parsed is None:
        return None
    return OrientedTokens(radiant=parsed.radiant, dire=parsed.dire)


def mid_moves(
    radiant_book: TokenBook,
    dire_book: TokenBook,
    start_second: int,
    end_second: int,
) -> dict[int, float]:
    """Absolute midpoint changes at integer wall seconds with two finite as-ofs."""
    mids: dict[int, float] = {}
    for wall in range(start_second, end_second + 1):
        market_p = lookup_market_p_after(radiant_book, dire_book, wall * US_PER_SECOND, 0)
        if market_p is None or not math.isfinite(market_p):
            continue
        mids[wall] = market_p
    moves: dict[int, float] = {}
    for wall, mid in mids.items():
        previous = mids.get(wall - 1)
        if previous is None:
            continue
        moves[wall] = abs(mid - previous)
    return moves


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation, or None when undefined."""
    if len(xs) < 2:
        return None
    left = np.asarray(xs, dtype=np.float64)
    right = np.asarray(ys, dtype=np.float64)
    if float(left.std()) == 0.0 or float(right.std()) == 0.0:
        return None
    matrix = np.corrcoef(left, right)
    value = float(matrix[0, 1])
    if not math.isfinite(value):
        return None
    return value


def measure_one_map(
    link: LolLinkRow,
    windows_dir: Path,
    telonex_root: Path,
) -> MapLagSeries | None:
    """Death jumps and mid moves for one accepted map, or None when unusable."""
    timed = load_timed_frames(link, windows_dir)
    if timed is None or not timed:
        return None
    tokens = oriented_tokens(link)
    if tokens is None:
        return None
    start_second = int(timed[0].wall_seconds)
    end_second = int(timed[-1].wall_seconds) + LOL_LAG_CANDIDATE_MAX_SECONDS
    start_us = start_second * US_PER_SECOND
    end_us = end_second * US_PER_SECOND
    radiant_book = load_token_book(
        token_id=tokens.radiant, start_us=start_us, end_us=end_us, telonex_root=telonex_root
    )
    dire_book = load_token_book(
        token_id=tokens.dire, start_us=start_us, end_us=end_us, telonex_root=telonex_root
    )
    if radiant_book is None or dire_book is None:
        return None
    jumps = death_jumps(timed)
    moves = mid_moves(radiant_book, dire_book, start_second, end_second)
    if not moves:
        return None
    return MapLagSeries(jumps=jumps, moves=moves)


def pool_scores(pairs: Sequence[MapLagSeries]) -> list[LagScore]:
    """Score lags on pooled (jump[t], |Δmid|[t+L]) pairs from every usable map."""
    # ponytail: Pearson on 1 Hz jump vs |Δmid| is enough to pick an argmax; event-aligned windowed CCF if this score is flat.
    scores: list[LagScore] = []
    for lag in range(0, LOL_LAG_CANDIDATE_MAX_SECONDS + 1):
        xs: list[float] = []
        ys: list[float] = []
        for series in pairs:
            for wall, move in series.moves.items():
                xs.append(series.jumps.get(wall - lag, 0.0))
                ys.append(move)
        correlation = pearson(xs, ys)
        if correlation is None:
            continue
        scores.append(LagScore(lag_seconds=lag, correlation=correlation, n_pairs=len(xs)))
    return scores


def print_scores(scores: Sequence[LagScore]) -> None:
    """Print the lag table and the argmax versus the 25s baseline."""
    print("lag_seconds,correlation,n_pairs")
    for score in scores:
        print(f"{score.lag_seconds},{score.correlation:.6f},{score.n_pairs}")
    best = max(scores, key=lambda item: item.correlation)
    print(f"argmax_lag_seconds={best.lag_seconds}")
    print(f"baseline_lag_seconds={LOL_SOURCE_LAG_SECONDS}")


def run_lag_probe(
    links_path: Path,
    windows_dir: Path,
    catalog_path: Path,
    telonex_root: Path,
) -> int:
    """Print lag scores. Return 0 on success, 1 when inputs or pairs are missing."""
    missing = require_lag_inputs(links_path, windows_dir, catalog_path, telonex_root)
    if missing is not None:
        print(missing)
        return 1
    try:
        pd.read_parquet(catalog_path)
    except (OSError, ValueError):
        print(f"missing catalog parquet: {catalog_path}")
        return 1
    links = cast(list[LolLinkRow], read_parquet_rows(links_path))
    pairs: list[MapLagSeries] = []
    for link in links:
        series = measure_one_map(link, windows_dir, telonex_root)
        if series is None:
            continue
        pairs.append(series)
    if not pairs:
        print("no lag pairs")
        return 1
    scores = pool_scores(pairs)
    if not scores:
        print("no lag pairs")
        return 1
    print_scores(scores)
    return 0


def main(
    links_path: Annotated[Path, typer.Option("--links-path")] = LOL_LINKS_PATH,
    windows_dir: Annotated[Path, typer.Option("--windows-dir")] = LOL_WINDOWS_DIR,
    catalog_path: Annotated[Path, typer.Option("--catalog-path")] = LOL_TELONEX_CATALOG_PATH,
    telonex_root: Annotated[Path, typer.Option("--telonex-root")] = LOL_RAW_TELONEX_DIR,
) -> None:
    """Print deaths-vs-midpoint lag scores. Does not write files or change constants."""
    code = run_lag_probe(links_path, windows_dir, catalog_path, telonex_root)
    if code != 0:
        raise typer.Exit(code)


if __name__ == "__main__":
    typer.run(main)
