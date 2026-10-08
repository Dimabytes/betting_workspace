"""Per-map prepare cache: hits, input-stamp busts, and truncated files."""

import gzip
import math
from importlib import import_module
from pathlib import Path

import msgspec
import pandas as pd
import pytest
from test_lol_prepare_dataset import (
    OUTPUT_NAMES,
    SPAWN_TS,
    TOK_R,
    accepted_link,
    fixture_item_table,
    one_bucket_frames,
    run_prepare,
    standard_books,
    window_body,
    write_archive,
    write_item_catalog,
    write_min_tape,
)

from lol.map_build_cache import (
    CachedMapBuild,
    build_input_stamp,
    map_cache_path,
    write_cached_build,
)
from lol.types import LolLinkRow, MapBuild
from shared.utils.telonex_book import US_PER_SECOND


def seed_maps(tmp_path: Path, game_ids: tuple[str, ...]) -> tuple[Path, Path, list[LolLinkRow]]:
    """Write livestats, books, tape, and links for the given maps."""
    windows = tmp_path / "windows"
    for game_id in game_ids:
        write_archive(windows, game_id, one_bucket_frames(float(SPAWN_TS)))
    telonex = tmp_path / "telonex"
    standard_books(telonex, SPAWN_TS * US_PER_SECOND)
    write_min_tape(telonex, [SPAWN_TS * US_PER_SECOND])
    links = [accepted_link(game_id, f"e-{game_id}", 1) for game_id in game_ids]
    return windows, telonex, links


def cache_counts(capsys: pytest.CaptureFixture[str]) -> tuple[int, int]:
    """Parse map_build_cache_hit/miss lines from the last prepare run."""
    output = capsys.readouterr().out
    hit: int | None = None
    miss: int | None = None
    for line in output.splitlines():
        if line.startswith("map_build_cache_hit: "):
            hit = int(line.split(":", 1)[1])
        elif line.startswith("map_build_cache_miss: "):
            miss = int(line.split(":", 1)[1])
    if hit is None or miss is None:
        raise AssertionError(output)
    return hit, miss


def output_bytes(output_dir: Path) -> dict[str, bytes]:
    """Raw published parquet payloads."""
    return {name: (output_dir / name).read_bytes() for name in OUTPUT_NAMES}


def test_second_run_hits_and_matches_parquet_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Warm prepare is a full hit and publishes identical parquet bytes."""
    windows, telonex, links = seed_maps(tmp_path, ("1001", "1002"))
    first = run_prepare(tmp_path, links, windows, telonex)
    assert cache_counts(capsys) == (0, 2)
    first_payloads = output_bytes(first)
    second = run_prepare(tmp_path, links, windows, telonex)
    assert cache_counts(capsys) == (2, 0)
    assert output_bytes(second) == first_payloads
    for name in OUTPUT_NAMES:
        pd.testing.assert_frame_equal(
            pd.read_parquet(first / name), pd.read_parquet(second / name), check_exact=True
        )


def test_appended_window_misses_only_that_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Appending a window response busts that map and leaves the other cached."""
    windows, telonex, links = seed_maps(tmp_path, ("1001", "1002"))
    run_prepare(tmp_path, links, windows, telonex)
    cache_counts(capsys)
    fetch = import_module("lol.04_fetch_lolesports")
    fetch.append_gzip_jsonl(
        fetch.archive_path(windows, "1001"),
        window_body("1001", one_bucket_frames(float(SPAWN_TS))),
    )
    run_prepare(tmp_path, links, windows, telonex)
    assert cache_counts(capsys) == (1, 1)


def test_new_book_day_file_misses_that_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A new book parquet under the token's asset dir busts that map."""
    windows, telonex, links = seed_maps(tmp_path, ("1001",))
    run_prepare(tmp_path, links, windows, telonex)
    cache_counts(capsys)
    run_prepare(tmp_path, links, windows, telonex)
    assert cache_counts(capsys) == (1, 0)
    src = telonex / "book_snapshot_full" / f"asset_id={TOK_R}" / "2026-01-01.parquet"
    src.with_name("2026-01-02.parquet").write_bytes(src.read_bytes())
    run_prepare(tmp_path, links, windows, telonex)
    assert cache_counts(capsys) == (0, 1)


def test_truncated_cache_file_is_recomputed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A truncated gzip cache file is a miss, not a prepare abort."""
    windows, telonex, links = seed_maps(tmp_path, ("1001",))
    run_prepare(tmp_path, links, windows, telonex)
    cache_counts(capsys)
    path = map_cache_path(tmp_path / "map_builds", "1001")
    path.write_bytes(path.read_bytes()[:20])
    run_prepare(tmp_path, links, windows, telonex)
    assert cache_counts(capsys) == (0, 1)


def test_different_cache_version_stamp_is_recomputed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stored stamp from another cache_version is ignored and rebuilt."""
    windows, telonex, links = seed_maps(tmp_path, ("1001",))
    run_prepare(tmp_path, links, windows, telonex)
    cache_counts(capsys)
    path = map_cache_path(tmp_path / "map_builds", "1001")
    record = msgspec.json.decode(gzip.decompress(path.read_bytes()), type=CachedMapBuild)
    write_cached_build(path, "other-cache-version", record.build)
    run_prepare(tmp_path, links, windows, telonex)
    assert cache_counts(capsys) == (0, 1)


def test_item_catalog_bytes_are_in_the_stamp(tmp_path: Path) -> None:
    """Catalog content is hashed; a same-size rewrite still busts, mtime does not."""
    windows, telonex, links = seed_maps(tmp_path, ("1001",))
    catalog = write_item_catalog(tmp_path / "ddragon_items", [fixture_item_table()])
    details = windows.parent / "details"
    first = build_input_stamp(links[0], windows, details, telonex, catalog)
    path = catalog / "16.16.1.json"
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    assert build_input_stamp(links[0], windows, details, telonex, catalog) == first
    path.write_text(path.read_text(encoding="utf-8").replace("50", "51"), encoding="utf-8")
    assert build_input_stamp(links[0], windows, details, telonex, catalog) != first


def test_write_cached_build_rejects_nan(tmp_path: Path) -> None:
    """msgspec would turn NaN into null; refuse to cache that."""
    build = MapBuild(
        link=accepted_link("1001", "e1", 1),
        match_id=1001,
        start_time=1,
        pause_count=0,
        pause_seconds=math.nan,
        frame_count=0,
        grid_seconds=0,
        rows=(),
        skipped_age_rows=0,
        skipped_market_rows=0,
        skipped_invariant_rows=0,
        skipped_stamp_rows=0,
        included=False,
        reason="accepted",
        invariant_rule=None,
        market_rows=(),
        feature_rows=(),
        backtest_audit=None,
    )
    with pytest.raises(ValueError, match="non-finite"):
        write_cached_build(tmp_path / "1001.json.gz", "stamp", build)
