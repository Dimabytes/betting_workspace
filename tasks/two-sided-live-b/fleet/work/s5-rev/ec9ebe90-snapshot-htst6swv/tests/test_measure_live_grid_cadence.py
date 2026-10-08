"""Fixture tests for scripts/measure_live_grid_cadence.py."""

import json
from pathlib import Path

from measure_live_grid_cadence import collect_archive, unique_seconds


def write_match(root: Path, name: str, game: str, seconds: tuple[int, ...]) -> None:
    """One archive dir with match.json and Polymarket session signals."""
    match_dir = root / name
    match_dir.mkdir(parents=True)
    (match_dir / "match.json").write_text(json.dumps({"game": game, "match_id": name}))
    lines = [
        json.dumps({"kind": "session_start"}),
        *[
            json.dumps({"kind": "signal", "venue": "polymarket", "second": second})
            for second in seconds
        ],
        json.dumps({"kind": "signal", "venue": "kalshi", "second": 99}),
    ]
    (match_dir / "session.jsonl").write_text("\n".join(lines) + "\n")


def test_unique_seconds_keep_first_and_drop_kalshi() -> None:
    """One row per second; Kalshi signals are ignored."""
    records = [
        {"kind": "signal", "venue": "polymarket", "second": 0},
        {"kind": "signal", "second": 0},
        {"kind": "signal", "venue": "kalshi", "second": 8},
        {"kind": "signal", "venue": "polymarket", "second": 12},
    ]
    assert unique_seconds(records) == [0, 12]


def test_collect_archive_splits_games_and_bands(tmp_path: Path) -> None:
    """Dota and LoL maps report gap counts in the calibrated bands."""
    write_match(tmp_path, "dota-early", "dota", (0, 10))
    write_match(tmp_path, "dota-mid", "dota", (190, 198))
    write_match(tmp_path, "lol-early", "lol", (0, 6))
    write_match(tmp_path, "lol-late", "lol", (370, 375))
    dota, lol = collect_archive(tmp_path)
    assert dota.maps == 2
    assert lol.maps == 2
    dota_bands = {band.label: band for band in dota.bands}
    assert dota_bands["<180"].count == 1
    assert dota_bands["<180"].mean == 10.0
    assert dota_bands["180..359"].count == 1
    assert dota_bands["180..359"].mean == 8.0
    lol_bands = {band.label: band for band in lol.bands}
    assert lol_bands["<180"].count == 1
    assert lol_bands["<180"].mean == 6.0
    assert lol_bands["360..539"].count == 1
    assert lol_bands["360..539"].mean == 5.0
