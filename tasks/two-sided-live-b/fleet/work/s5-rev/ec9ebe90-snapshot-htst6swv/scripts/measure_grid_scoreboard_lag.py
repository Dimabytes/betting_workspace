"""Scoreboard lag quantiles per game: received minus occurredAt on kill frames.

Replays each archive's grid_state.jsonl and keeps a board frame when its total
kill count grew versus the previous frame — the same rule the reducer's kill
gate tracks. Prints the 21-quantile table (p0..p100 step 5%) that
`SCOREBOARD_LAG_QUANTILES` in backtest/signals.py is generated from.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import numpy as np

from shared.utils.match_time import parse_utc
from trader.grid_archive import iter_grid_archive_records
from trader.grid_widgets import SCOREBOARD_SERVICE, parse_frame, read_map_scoreboard
from trader.paths import GRID_STATE_ARCHIVE_FILENAME

QUANTILE_POINTS = tuple(q / 100 for q in range(0, 101, 5))


def scoreboard_lags(feed_path: Path, map_number: int) -> list[float]:
    """received - occurredAt seconds for every frame whose kill total grew."""
    lags: list[float] = []
    max_kills: int | None = None
    for record in iter_grid_archive_records(feed_path):
        frame = parse_frame(record["frame"])
        if frame.service != SCOREBOARD_SERVICE or not frame.payload:
            continue
        board = read_map_scoreboard(frame.payload, map_number)
        if board is None:
            continue
        kills = sum(team.kills for team in board.teams)
        if max_kills is None:
            max_kills = kills
            continue
        if kills <= max_kills:
            continue
        max_kills = kills
        lags.append(
            (parse_utc(record["received_at_utc"]) - parse_utc(board.occurred_at)).total_seconds()
        )
    return lags


def load_match_fields(match_path: Path) -> tuple[str, int] | None:
    """(game, map_number) from match.json, or None when the file cannot be read."""
    try:
        payload = json.loads(match_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if type(payload) is not dict:
        return None
    fields = cast(dict[str, object], payload)
    map_number = fields.get("map_number")
    if type(map_number) is not int:
        return None
    game = fields.get("game")
    return ("lol" if game == "lol" else "dota"), map_number


def collect_archive(root: Path) -> dict[str, list[float]]:
    """Walk match archives and gather scoreboard lags per game."""
    lags_by_game: dict[str, list[float]] = {"dota": [], "lol": []}
    for match_path in sorted(root.glob("*/match.json")):
        fields = load_match_fields(match_path)
        if fields is None:
            continue
        game, map_number = fields
        feed_path = match_path.parent / GRID_STATE_ARCHIVE_FILENAME
        if not feed_path.is_file() and not feed_path.with_suffix(".jsonl.gz").is_file():
            continue
        lags_by_game[game].extend(scoreboard_lags(feed_path, map_number))
    return lags_by_game


def format_report(lags_by_game: dict[str, list[float]]) -> str:
    """Print kill counts and the 21-quantile lag table per game."""
    lines: list[str] = []
    for game in ("dota", "lol"):
        lags = lags_by_game[game]
        lines.append(f"{game} kills: {len(lags)}")
        if not lags:
            continue
        values = np.quantile(np.asarray(lags, dtype=np.float64), QUANTILE_POINTS)
        lines.append(", ".join(f"{value:.2f}" for value in values))
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Archive root is the only argument."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_root", type=Path)
    return parser.parse_args(argv)


def main() -> None:
    """CLI: print per-game scoreboard lag quantiles for one trader tree."""
    args = parse_args(sys.argv[1:])
    root = args.archive_root
    if not root.is_dir():
        raise SystemExit(f"archive root is not a directory: {root}")
    sys.stdout.write(format_report(collect_archive(root)))


if __name__ == "__main__":
    main()
