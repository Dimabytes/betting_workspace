"""Plot devigged book probabilities against the Polymarket midpoint for one map.

Input is the JSONL written by the browser samplers: one object per poll, each
tagged with `src`. `1xbet_raw` carries the whole `GE` market block, `betboom_raw`
the rendered market text, `pm_book` one side of the CLOB order book. Everything
plotted here is reconstructed from those raw rows, so the chart can be redrawn
for any other market in the same file.

Invocation:
  make run F=scripts/plot_book_vs_pm.py ARGS="data/book_vs_pm/parts_map4 4"
"""

# matplotlib ships stubs whose keyword arguments are untyped, so every pyplot call
# trips strict mode. Typing here is the chart, not the numbers.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

HOME_LABEL = "Anyone's Legend"
MAP_NUMBER = int(sys.argv[2]) if len(sys.argv) > 2 else 3
BETBOOM_MONEYLINES = (
    re.compile(
        rf"Исход\s*\n\s*Карта {MAP_NUMBER}\s*\n\s*П1\s*\n\s*([\d.]+)\s*\n\s*П2\s*\n\s*([\d.]+)"
    ),
    # On the decider the map winner and the match winner are the same event,
    # and betboom drops the per-map tab once no later map can be played.
    re.compile(r"Исход матча\s*\n\s*П1\s*\n\s*([\d.]+)\s*\n\s*П2\s*\n\s*([\d.]+)"),
)
SOURCE_STYLES = {
    "1xbet": ("#1f77b4", f"1xbet map {MAP_NUMBER} (devig)"),
    "betboom": ("#d62728", f"betboom map {MAP_NUMBER} (devig)"),
    "polymarket": ("#2ca02c", f"Polymarket Game {MAP_NUMBER} mid"),
}


@dataclass(frozen=True)
class Point:
    when: datetime
    probability: float


def devig_decimal(home_odds: float, away_odds: float) -> float:
    """Home probability with the book's margin removed proportionally."""
    home_raw = 1 / home_odds
    away_raw = 1 / away_odds
    return home_raw / (home_raw + away_raw)


def collect_lines(source_path: Path) -> list[str]:
    """Every JSONL line under a drain directory, or in a single drain file."""
    if source_path.is_file():
        return source_path.read_text().splitlines()
    lines: list[str] = []
    for part in sorted(source_path.glob("*.jsonl")):
        lines.extend(part.read_text().splitlines())
    return lines


def read_1xbet_probability(snapshot: Any) -> float | None:
    """Devigged home probability from one 1xbet `GE` snapshot, or None if it has no line."""
    if snapshot is None:
        return None
    winner = next((g for g in snapshot["GE"] if g["G"] == 1), None)
    if winner is None:
        return None
    prices = [event for group in winner["E"] for event in group]
    home = next((e["C"] for e in prices if e["T"] == 1), None)
    away = next((e["C"] for e in prices if e["T"] == 3), None)
    if not home or not away:
        return None
    return devig_decimal(home, away)


def read_betboom_probability(rendered: str | None) -> float | None:
    """Devigged home probability from the rendered betboom market text, if the map is listed."""
    if rendered is None:
        return None
    found = next((m for m in (p.search(rendered) for p in BETBOOM_MONEYLINES) if m), None)
    if found is None:
        return None
    return devig_decimal(float(found[1]), float(found[2]))


def read_series(source_path: Path) -> dict[str, list[Point]]:
    """One time series of home win probability per source, taken from raw rows."""
    series: dict[str, list[Point]] = {name: [] for name in SOURCE_STYLES}
    for line in collect_lines(source_path):
        if not line.strip():
            continue
        row = json.loads(line)
        source = row.get("src")
        when = datetime.fromtimestamp(row["t"] / 1000, tz=UTC).astimezone()

        if source in ("1xbet_raw", "1xbet_series_raw"):
            probability = read_1xbet_probability(row.get("v"))
            if probability is not None:
                series["1xbet"].append(Point(when, probability))

        elif source == "betboom_raw":
            probability = read_betboom_probability(row.get("text"))
            if probability is not None:
                series["betboom"].append(Point(when, probability))

        elif source == "pm_book" and row.get("tok") == "AL":
            bid, ask = row.get("bid"), row.get("ask")
            if bid is not None and ask is not None:
                series["polymarket"].append(Point(when, (bid + ask) / 2))

    for points in series.values():
        points.sort(key=lambda point: point.when)
    return series


def draw(series: dict[str, list[Point]], output_path: Path) -> None:
    """Save one chart with every source overlaid on the same probability axis."""
    figure, axes = plt.subplots(figsize=(13, 6))
    for source, points in series.items():
        if not points:
            continue
        color, label = SOURCE_STYLES[source]
        axes.plot(
            # matplotlib converts datetimes itself; its stub only admits ArrayLike.
            [p.when for p in points],  # pyright: ignore[reportArgumentType]
            [p.probability for p in points],
            label=label,
            color=color,
            linewidth=1.4,
        )
    axes.set_ylabel(f"P({HOME_LABEL} wins map {MAP_NUMBER})")
    axes.set_ylim(0, 1)
    axes.axhline(0.5, color="#999999", linewidth=0.6, linestyle="--")
    axes.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes.grid(alpha=0.25)
    axes.legend(loc="best")
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(output_path, dpi=140)
    print(f"wrote {output_path}")


def main() -> None:
    source_path = Path(sys.argv[1])
    output_path = (
        source_path.parent / f"map{MAP_NUMBER}_book_vs_pm.png"
        if source_path.is_dir()
        else source_path.with_suffix(".png")
    )
    draw(read_series(source_path), output_path)


if __name__ == "__main__":
    main()
