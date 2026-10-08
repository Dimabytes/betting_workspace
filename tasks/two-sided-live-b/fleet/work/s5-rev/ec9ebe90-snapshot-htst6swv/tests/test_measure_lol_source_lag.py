"""Synthetic deaths-vs-midpoint lag probe. No network, no data/lol."""

from pathlib import Path

import measure_lol_source_lag as lag_mod
import pytest
from test_lol_prepare_dataset import (
    SPAWN_TS,
    accepted_link,
    frame_at,
    spawn_frame,
    write_archive,
    write_catalog,
    write_links,
    write_pair_books,
)

from shared.constants.lol import LOL_SOURCE_LAG_SECONDS
from shared.utils.telonex_book import US_PER_SECOND


def constant_then_jump_books(root: Path, spawn_ts: int) -> None:
    """1 Hz mids: 0.50 until t=25, then 0.60."""
    snapshots: list[tuple[int, float, float]] = []
    for offset in range(0, 61):
        mid = 0.60 if offset >= 25 else 0.50
        snapshots.append(((spawn_ts + offset) * US_PER_SECOND, mid, 1.0 - mid))
    write_pair_books(root, snapshots)


def test_death_jump_at_zero_mid_move_at_25_prints_argmax(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Death jump at spawn wall, mid move only at +25s → argmax 25."""
    links_path = tmp_path / "links.parquet"
    catalog_path = tmp_path / "catalog.parquet"
    windows_dir = tmp_path / "windows"
    telonex_root = tmp_path / "telonex"
    link = accepted_link("1001", "e1", 1)
    write_links(links_path, [link])
    write_catalog(catalog_path)
    write_archive(
        windows_dir,
        "1001",
        [
            spawn_frame(float(SPAWN_TS)),
            frame_at(SPAWN_TS + 0.4, 500, 500, 1, 1, 1, 0),
        ],
    )
    constant_then_jump_books(telonex_root, SPAWN_TS)

    code = lag_mod.run_lag_probe(links_path, windows_dir, catalog_path, telonex_root)

    assert code == 0
    output = capsys.readouterr().out
    assert "argmax_lag_seconds=25" in output
    assert f"baseline_lag_seconds={LOL_SOURCE_LAG_SECONDS}" in output


def test_missing_catalog_exits_nonzero_and_writes_nothing(tmp_path: Path) -> None:
    """Missing catalog prints a reason, exits 1, and creates no artifacts."""
    links_path = tmp_path / "links.parquet"
    write_links(links_path, [accepted_link("1001", "e1", 1)])
    windows_dir = tmp_path / "windows"
    windows_dir.mkdir()
    telonex_root = tmp_path / "telonex"
    (telonex_root / "book_snapshot_full").mkdir(parents=True)
    before = {path.name for path in tmp_path.rglob("*")}

    code = lag_mod.run_lag_probe(
        links_path, windows_dir, tmp_path / "missing.parquet", telonex_root
    )

    assert code == 1
    after = {path.name for path in tmp_path.rglob("*")}
    assert after == before
    assert not (tmp_path / "model.json").exists()
    assert not list(tmp_path.glob("*.csv"))
