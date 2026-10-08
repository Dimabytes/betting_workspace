"""Read-only GRID cadence stats from a trader archive tree."""

import argparse
import json
import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

CADENCE_WINDOW_START = -50
CADENCE_WINDOW_END = 540
BAND_LABELS = ("<180", "180..359", "360..539", ">=540")


@dataclass(frozen=True)
class BandStats:
    """Gap count and percentiles for one game-second band."""

    label: str
    count: int
    median: float | None
    mean: float | None
    p90: float | None
    p95: float | None


@dataclass(frozen=True)
class GameCadenceReport:
    """Per-game map count and band stats from Polymarket GRID signals."""

    game: str
    maps: int
    bands: tuple[BandStats, ...]


def is_polymarket_signal(record: object) -> bool:
    """True for a session.jsonl Polymarket signal row (schema 1-6)."""
    if type(record) is not dict:
        return False
    payload = cast(dict[str, object], record)
    if payload.get("kind") != "signal":
        return False
    venue = payload.get("venue")
    return venue is None or venue == "polymarket"


def unique_seconds(records: Sequence[object]) -> list[int]:
    """One game second per signal, first write wins, sorted."""
    seen: dict[int, None] = {}
    for record in records:
        if not is_polymarket_signal(record):
            continue
        if type(record) is not dict:
            continue
        payload = cast(dict[str, object], record)
        raw_second = payload.get("second")
        if type(raw_second) is not int:
            continue
        if raw_second < CADENCE_WINDOW_START or raw_second > CADENCE_WINDOW_END:
            continue
        seen.setdefault(raw_second, None)
    return sorted(seen)


def positive_gaps(seconds: Sequence[int]) -> list[tuple[int, int]]:
    """(start_second, gap) for each forward jump inside one map."""
    gaps: list[tuple[int, int]] = []
    for index in range(1, len(seconds)):
        gap = seconds[index] - seconds[index - 1]
        if gap > 0:
            gaps.append((seconds[index - 1], gap))
    return gaps


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile; values must be non-empty."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return float(ordered[rank - 1])


def summarize_gaps(gaps: Sequence[int], label: str) -> BandStats:
    """Median/mean/p90/p95 for one band, or empty stats when it has no gaps."""
    if not gaps:
        return BandStats(label=label, count=0, median=None, mean=None, p90=None, p95=None)
    as_float = [float(gap) for gap in gaps]
    return BandStats(
        label=label,
        count=len(gaps),
        median=float(statistics.median(as_float)),
        mean=float(statistics.mean(as_float)),
        p90=percentile(as_float, 90.0),
        p95=percentile(as_float, 95.0),
    )


def band_label_for_second(second: int) -> str:
    """Display label for the grid-v1 band that owns this game second."""
    if second < 180:
        return "<180"
    if second < 360:
        return "180..359"
    if second < 540:
        return "360..539"
    return ">=540"


def read_jsonl(path: Path) -> list[object]:
    """Parse a session journal; skip blank lines."""
    records: list[object] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def load_match_game(match_path: Path) -> str:
    """Game from match.json, default dota when schema 3/4 omit it."""
    payload = json.loads(match_path.read_text(encoding="utf-8"))
    if type(payload) is not dict:
        return "dota"
    raw = cast(dict[str, object], payload).get("game")
    if raw == "lol":
        return "lol"
    return "dota"


def collect_archive(root: Path) -> tuple[GameCadenceReport, GameCadenceReport]:
    """Walk match archives and summarize Dota and LoL GRID gaps."""
    gaps_by_game: dict[str, dict[str, list[int]]] = {
        "dota": {label: [] for label in BAND_LABELS},
        "lol": {label: [] for label in BAND_LABELS},
    }
    maps_by_game = {"dota": 0, "lol": 0}
    for match_path in sorted(root.glob("*/match.json")):
        session_path = match_path.parent / "session.jsonl"
        if not session_path.is_file():
            continue
        seconds = unique_seconds(read_jsonl(session_path))
        if not seconds:
            continue
        game = load_match_game(match_path)
        maps_by_game[game] += 1
        for start_second, gap in positive_gaps(seconds):
            gaps_by_game[game][band_label_for_second(start_second)].append(gap)
    return (
        GameCadenceReport(
            game="dota",
            maps=maps_by_game["dota"],
            bands=tuple(
                summarize_gaps(gaps_by_game["dota"][label], label) for label in BAND_LABELS
            ),
        ),
        GameCadenceReport(
            game="lol",
            maps=maps_by_game["lol"],
            bands=tuple(summarize_gaps(gaps_by_game["lol"][label], label) for label in BAND_LABELS),
        ),
    )


def _fmt(value: float | None) -> str:
    """One decimal second, or a dash when the band is empty."""
    if value is None:
        return "-"
    return f"{value:.2f}"


def format_report(reports: Sequence[GameCadenceReport]) -> str:
    """Print map counts and band percentiles."""
    lines: list[str] = []
    for report in reports:
        lines.append(f"{report.game} maps: {report.maps}")
        lines.append(f"{'band':<10}{'n':>6}{'median':>10}{'mean':>10}{'p90':>10}{'p95':>10}")
        for band in report.bands:
            lines.append(
                f"{band.label:<10}{band.count:>6}"
                f"{_fmt(band.median):>10}{_fmt(band.mean):>10}"
                f"{_fmt(band.p90):>10}{_fmt(band.p95):>10}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Archive root is the only argument."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_root", type=Path)
    return parser.parse_args(argv)


def main() -> None:
    """CLI: print Dota and LoL GRID gap stats for one trader tree."""
    args = parse_args(sys.argv[1:])
    root = args.archive_root
    if not root.is_dir():
        raise SystemExit(f"archive root is not a directory: {root}")
    dota, lol = collect_archive(root)
    sys.stdout.write(format_report((dota, lol)))


if __name__ == "__main__":
    main()
