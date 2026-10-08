"""Unit tests for per-match market-data selection, caching, and dispatch."""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from catalog_fixtures import catalog_row
from telonex_fixtures import write_token_book

import market_data.build_market_data as build_module
from market_data.build_market_data import (
    MarketCacheJob,
    dispatch_builds,
    load_market_data_sources,
    run_market_data_build,
    try_build_one_market_cache,
)
from shared.constants.dataset import MODEL_START_SECOND
from shared.types.dataset import MarketSecondRow, MatchCatalogRow
from shared.utils.match_time import datetime_to_ns
from shared.utils.parquet_io import write_parquet

CUTOFF = 1_780_563_592
HORN = datetime(2026, 1, 1, tzinfo=UTC)


def build_market_row(match_id: int, second: int) -> MarketSecondRow:
    """Build one complete synthetic market row for parquet contract tests."""
    return MarketSecondRow(
        match_id=match_id,
        condition_id=f"condition-{match_id}",
        event_id=f"event-{match_id}",
        second=second,
        state_ts_us=second * 1_000_000,
        market_status="ok",
        market_p_radiant=0.5,
        signal_market_p_radiant_30s=None,
        signal_market_p_radiant_300s=None,
    )


def build_cache_rows(match_id: int, duration_seconds: int) -> list[MarketSecondRow]:
    """Build the exact MODEL_START..duration rows a valid cache must hold."""
    return [
        build_market_row(match_id, second)
        for second in range(MODEL_START_SECOND, duration_seconds + 1)
    ]


def build_job(
    match_id: int,
    telonex_root: Path,
    cache_path: Path,
) -> MarketCacheJob:
    """Build a tiny picklable cache job for worker tests."""
    return MarketCacheJob(
        match_id=match_id,
        condition_id=f"condition-{match_id}",
        event_id=f"event-{match_id}",
        token_ids=("yes", "no"),
        radiant_token_index=0,
        horn=HORN,
        pauses=[],
        duration_seconds=1,
        telonex_root=telonex_root,
        cache_path=cache_path,
    )


def create_token_partitions(root: Path, token_ids: tuple[str, str]) -> None:
    """Create raw Telonex partition directories without snapshots."""
    for token_id in token_ids:
        (root / "book_snapshot_full" / f"asset_id={token_id}").mkdir(
            parents=True,
            exist_ok=True,
        )


def write_tiny_books(root: Path) -> None:
    """Write two tiny books that cover all timestamps needed by a worker."""
    timestamp_us = datetime_to_ns(HORN) // 1_000
    for token_id, bid, ask in (("yes", 0.59, 0.61), ("no", 0.39, 0.41)):
        write_token_book(
            root,
            token_id=token_id,
            day="2026-01-01",
            rows=[
                (
                    timestamp_us,
                    [{"price": bid, "size": 20.0}],
                    [{"price": ask, "size": 20.0}],
                )
            ],
        )


def write_catalog(tmp_path: Path, rows: tuple[MatchCatalogRow, ...]) -> Path:
    """Write a catalog parquet for load_market_data_sources tests."""
    path = tmp_path / "match_catalog.parquet"
    pd.DataFrame(list(rows)).to_parquet(path, index=False)
    return path


@pytest.mark.parametrize("start_time", [CUTOFF - 1, CUTOFF])
def test_match_without_playback_still_produces_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    start_time: int,
) -> None:
    """Market caches do not require playback for train or validation matches."""
    catalog_path = write_catalog(
        tmp_path,
        (catalog_row(1, start_time=start_time, playback_available=False),),
    )
    telonex_root = tmp_path / "telonex"
    create_token_partitions(telonex_root, ("yes", "no"))

    jobs = load_market_data_sources(
        catalog_path=catalog_path,
        telonex_root=telonex_root,
    )

    assert len(jobs) == 1


def test_fully_sourced_match_becomes_a_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A match with every source and a pregame prior turns into one cache job."""
    catalog_path = write_catalog(tmp_path, (catalog_row(1, start_time=CUTOFF),))
    telonex_root = tmp_path / "telonex"
    create_token_partitions(telonex_root, ("yes", "no"))
    caplog.set_level("INFO")

    jobs = load_market_data_sources(
        catalog_path=catalog_path,
        telonex_root=telonex_root,
    )

    assert len(jobs) == 1
    assert jobs[0].radiant_token_index == 1
    assert "market-data selected=1" in caplog.text


def test_worker_exception_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker exception propagates instead of being collected as a status."""
    job = build_job(7, tmp_path / "telonex", tmp_path / "cache.parquet")

    def fail_rows(job: MarketCacheJob) -> None:
        raise OSError("boom")

    monkeypatch.setattr(build_module, "build_market_second_rows", fail_rows)
    with pytest.raises(OSError, match="boom"):
        try_build_one_market_cache(job)


def test_atomic_write_replaces_temporary_file(tmp_path: Path) -> None:
    """Atomic cache writes leave the final parquet and no temporary sibling."""
    path = tmp_path / "nested" / "cache.parquet"
    write_parquet(pd.DataFrame([build_market_row(7, 0)]), path)

    assert path.exists()
    assert not path.with_suffix(".parquet.tmp").exists()
    assert len(pd.read_parquet(path)) == 1


def test_skip_valid_cache_does_not_start_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing cache is reused without invoking any worker."""
    path = tmp_path / "cache.parquet"
    write_parquet(pd.DataFrame(build_cache_rows(7, 1)), path)
    job = build_job(7, tmp_path / "telonex", path)

    def fail_pool(*, max_workers: int) -> None:
        raise AssertionError("existing cache started workers")

    monkeypatch.setattr(build_module, "ProcessPoolExecutor", fail_pool)
    run_market_data_build(jobs=(job,))
    assert path.exists()


def test_hard_miss_writes_no_cache(tmp_path: Path) -> None:
    """A missing token partition leaves no cache file."""
    cache_path = tmp_path / "cache.parquet"
    job = build_job(7, tmp_path / "missing-telonex", cache_path)

    try_build_one_market_cache(job)

    assert not cache_path.exists()


def test_parallel_dispatch_builds_all_queued(tmp_path: Path) -> None:
    """A bounded worker pool builds every queued tiny cache."""
    telonex_root = tmp_path / "telonex"
    write_tiny_books(telonex_root)
    jobs = tuple(
        build_job(
            match_id,
            telonex_root,
            tmp_path / f"match-{match_id}.parquet",
        )
        for match_id in (1, 2, 3)
    )

    dispatch_builds(jobs)
    assert all(job.cache_path.exists() for job in jobs)
