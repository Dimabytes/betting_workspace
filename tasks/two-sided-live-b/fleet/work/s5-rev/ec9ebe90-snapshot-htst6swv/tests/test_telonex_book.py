# pyright: reportUnknownMemberType=false
"""Unit tests for shared Telonex book quote reading."""

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from telonex_fixtures import write_token_book

from market_data.build_market_data import MarketCacheJob, build_market_second_rows
from shared.constants.dataset import MODEL_START_SECOND
from shared.types.dataset import MarketSecondRow
from shared.types.opendota import OpenDotaPause
from shared.utils.match_time import datetime_to_ns
from shared.utils.telonex_book import (
    NS_PER_US,
    AsOfResult,
    SideQuote,
    TokenBook,
    find_asof_quote,
    load_token_book,
    normalize_pair_mids,
    resolve_market_pair,
)


def _fresh_quote(bid: float, ask: float) -> AsOfResult:
    return AsOfResult(
        status="ok",
        quote=SideQuote(
            timestamp_us=0,
            bid=bid,
            ask=ask,
            mid=(bid + ask) / 2.0,
            age_seconds=0.0,
        ),
        book_ts_us=0,
        age_seconds=0.0,
    )


def test_resolve_market_pair_rejects_wide_spread() -> None:
    """A 0.02/0.98 book is not a midpoint; 6 ticks is the same cutoff as live."""
    wide = resolve_market_pair(_fresh_quote(0.02, 0.98), _fresh_quote(0.02, 0.98))
    assert wide.status == "wide_spread"
    assert wide.market_p_radiant is None
    six = resolve_market_pair(_fresh_quote(0.47, 0.53), _fresh_quote(0.47, 0.53))
    assert six.status == "wide_spread"
    assert six.market_p_radiant is None
    one_side = resolve_market_pair(_fresh_quote(0.49, 0.51), _fresh_quote(0.02, 0.98))
    assert one_side.status == "wide_spread"


def test_resolve_market_pair_admits_five_tick_spread() -> None:
    """5 ticks still produces a normalized mid."""
    admitted = resolve_market_pair(_fresh_quote(0.47, 0.52), _fresh_quote(0.48, 0.53))
    assert admitted.status == "ok"
    assert admitted.market_p_radiant == 0.495


def test_normalize_pair_rejects_inconsistent_mids() -> None:
    """Complementary mids normalize; a broken pair sum is rejected."""
    assert normalize_pair_mids(radiant_mid=0.60, dire_mid=0.40, tolerance=0.05) == 0.6
    assert normalize_pair_mids(radiant_mid=0.52, dire_mid=0.50, tolerance=0.05) == 0.52 / 1.02
    assert normalize_pair_mids(radiant_mid=0.70, dire_mid=0.40, tolerance=0.05) is None


def test_asof_lookup_never_returns_future_and_exposes_age() -> None:
    """As-of picks the last <= target timestamp and exposes age for the stale gate."""
    book = TokenBook(
        timestamps_us=(1_000_000, 2_000_000, 4_000_000),
        bids=(0.4, 0.5, 0.6),
        asks=(0.5, 0.6, 0.7),
    )
    result = find_asof_quote(book, 3_000_000)
    assert result.quote is not None
    assert result.status == "ok"
    assert result.quote.timestamp_us == 2_000_000
    assert result.quote.mid == 0.55
    assert result.quote.age_seconds == 1.0
    missing = find_asof_quote(book, 999_999)
    assert missing.quote is None
    assert missing.status == "missing"


def test_asof_walks_back_past_one_sided_within_age_gate() -> None:
    """A one-sided hole does not hide an earlier two-sided quote inside the age window."""
    book = TokenBook(
        timestamps_us=(1_000_000, 2_000_000),
        bids=(0.4, 0.5),
        asks=(0.5, None),
    )
    result = find_asof_quote(book, 2_500_000)
    assert result.quote is not None
    assert result.status == "ok"
    assert result.quote.timestamp_us == 1_000_000
    assert result.quote.age_seconds == 1.5


def test_asof_walk_bail_without_two_sided_is_missing() -> None:
    """One-sided recent snapshot with only a stale earlier book is missing, not stale."""
    book = TokenBook(
        timestamps_us=(1_000_000, 8_000_000),
        bids=(0.4, 0.5),
        asks=(0.5, None),
    )
    result = find_asof_quote(book, 9_000_000)
    assert result.quote is None
    assert result.status == "missing"


def test_asof_first_candidate_too_old_is_stale_and_carries_no_quote() -> None:
    """A stale as-of keeps its audit trail but publishes no price at all."""
    book = TokenBook(
        timestamps_us=(1_000_000,),
        bids=(0.4,),
        asks=(0.5,),
    )
    result = find_asof_quote(book, 9_000_000)
    assert result.status == "stale"
    assert result.age_seconds == 8.0
    assert result.book_ts_us == 1_000_000
    assert result.quote is None


def test_load_token_book_aggregates_only_usable_levels(tmp_path: Path) -> None:
    """Null list items, empty sizes, and empty or null lists leave None sides."""
    capture = tmp_path / "telonex"
    write_token_book(
        capture,
        token_id="111",
        day="2026-01-01",
        rows=[
            (
                100,
                [
                    None,
                    {"price": 0.4, "size": 1},
                    {"price": 0.6, "size": 0},
                    {"price": 0.5, "size": 2},
                ],
                [
                    {"price": 0.9, "size": 1},
                    {"price": 0.8, "size": 0},
                    {"price": 0.7, "size": 1},
                ],
            ),
            (200, [], None),
        ],
    )
    book = load_token_book(token_id="111", start_us=0, end_us=300, telonex_root=capture)
    assert book is not None
    assert book.timestamps_us == (100, 200)
    assert book.bids == (0.5, None)
    assert book.asks == (0.7, None)


def test_load_token_book_reads_null_typed_side(tmp_path: Path) -> None:
    """A real-world single file may store a side as list<null>; it loads as all-None."""
    capture = tmp_path / "telonex"
    asset_dir = capture / "book_snapshot_full" / "asset_id=111"
    asset_dir.mkdir(parents=True)
    level_type = pa.list_(
        pa.struct([pa.field("price", pa.string()), pa.field("size", pa.string())])
    )
    pq.write_table(
        pa.table(
            {
                "timestamp_us": pa.array([100, 200], type=pa.int64()),
                "bids": pa.array(
                    [
                        [{"price": "0.4", "size": "1"}],
                        [{"price": "0.5", "size": "1"}],
                    ],
                    type=level_type,
                ),
                "asks": pa.array([[None], None], type=pa.list_(pa.null())),
            }
        ),
        asset_dir / "2026-01-18.parquet",
    )
    book = load_token_book(token_id="111", start_us=0, end_us=300, telonex_root=capture)
    assert book is not None
    assert book.timestamps_us == (100, 200)
    assert book.bids == (0.4, 0.5)
    assert book.asks == (None, None)


def test_load_token_book_rejects_file_without_a_side_column(tmp_path: Path) -> None:
    """A renamed or dropped side column fails loudly instead of loading as all-None."""
    capture = tmp_path / "telonex"
    asset_dir = capture / "book_snapshot_full" / "asset_id=111"
    asset_dir.mkdir(parents=True)
    level_type = pa.list_(
        pa.struct([pa.field("price", pa.string()), pa.field("size", pa.string())])
    )
    pq.write_table(
        pa.table(
            {
                "timestamp_us": pa.array([100], type=pa.int64()),
                "bids": pa.array([[{"price": "0.4", "size": "1"}]], type=level_type),
            }
        ),
        asset_dir / "2026-01-18.parquet",
    )
    with pytest.raises(ValueError, match=r"\['asks'\]"):
        load_token_book(token_id="111", start_us=0, end_us=300, telonex_root=capture)


def test_load_token_book_filters_window_and_returns_none_when_empty(tmp_path: Path) -> None:
    """Loader keeps the time window and treats an empty window as missing data."""
    capture = tmp_path / "telonex"
    write_token_book(
        capture,
        token_id="111",
        day="2026-01-01",
        rows=[
            (100, [{"price": 0.4, "size": 1}], [{"price": 0.5, "size": 1}]),
            (200, [{"price": 0.4, "size": 1}], []),
            (300, [{"price": 0.45, "size": 1}], [{"price": 0.55, "size": 1}]),
        ],
    )
    write_token_book(
        capture,
        token_id="999",
        day="2026-01-01",
        rows=[(200, [{"price": 0.1, "size": 1}], [{"price": 0.2, "size": 1}])],
    )
    book = load_token_book(token_id="111", start_us=150, end_us=300, telonex_root=capture)
    assert book is not None
    assert book.timestamps_us == (200, 300)
    hole = find_asof_quote(book, 250)
    assert hole.quote is None
    assert hole.status == "missing"
    result = find_asof_quote(book, 300)
    assert result.quote is not None
    assert result.quote.mid == 0.5
    assert load_token_book(token_id="missing", start_us=0, end_us=1, telonex_root=capture) is None
    assert (
        load_token_book(token_id="111", start_us=10_000, end_us=20_000, telonex_root=capture)
        is None
    )


def row_at_second(rows: list[MarketSecondRow], second: int) -> MarketSecondRow:
    """Index a built market-seconds cache by game second."""
    return rows[second - MODEL_START_SECOND]


def _write_pair_books(
    capture: Path,
    *,
    horn_us: int,
    radiant_id: str,
    dire_id: str,
) -> None:
    """Write complementary radiant/dire books used by row-builder tests."""
    write_token_book(
        capture,
        token_id=radiant_id,
        day="2026-01-01",
        rows=[
            (
                horn_us - 1_000_000,
                [{"price": 0.59, "size": 20}],
                [{"price": 0.61, "size": 2}, {"price": 0.62, "size": 10}],
            ),
            (
                horn_us + 3_000_000,
                [{"price": 0.70, "size": 20}],
                [{"price": 0.40, "size": 20}],
            ),
            (
                horn_us + 10_000_000,
                [{"price": 0.64, "size": 20}],
                [{"price": 0.66, "size": 20}],
            ),
            (
                horn_us + 12_000_000,
                [{"price": 0.69, "size": 20}],
                [{"price": 0.71, "size": 1}],
            ),
            (
                horn_us + 30_000_000,
                [{"price": 0.64, "size": 20}],
                [{"price": 0.66, "size": 20}],
            ),
            (
                horn_us + 300_000_000,
                [{"price": 0.54, "size": 20}],
                [{"price": 0.56, "size": 20}],
            ),
        ],
    )
    write_token_book(
        capture,
        token_id=dire_id,
        day="2026-01-01",
        rows=[
            (
                horn_us - 1_000_000,
                [{"price": 0.39, "size": 20}],
                [{"price": 0.41, "size": 20}],
            ),
            (
                horn_us + 3_000_000,
                [{"price": 0.30, "size": 20}],
                [{"price": 0.20, "size": 20}],
            ),
            (
                horn_us + 10_000_000,
                [{"price": 0.34, "size": 20}],
                [{"price": 0.36, "size": 20}],
            ),
            (
                horn_us + 12_000_000,
                [{"price": 0.29, "size": 20}],
                [{"price": 0.31, "size": 20}],
            ),
            (
                horn_us + 30_000_000,
                [{"price": 0.34, "size": 20}],
                [{"price": 0.36, "size": 20}],
            ),
            (
                horn_us + 300_000_000,
                [{"price": 0.44, "size": 20}],
                [{"price": 0.46, "size": 20}],
            ),
        ],
    )


def test_build_rows_market_p_and_horizons(tmp_path: Path) -> None:
    """Rows carry the state-time pair mid and the markout horizons off that anchor."""
    capture = tmp_path / "telonex"
    horn = datetime(2026, 1, 1, tzinfo=UTC)
    horn_us = datetime_to_ns(horn) // NS_PER_US
    _write_pair_books(capture, horn_us=horn_us, radiant_id="111", dire_id="222")

    rows = build_market_second_rows(
        MarketCacheJob(
            match_id=1,
            condition_id="0xabc",
            event_id="event-1",
            token_ids=("111", "222"),
            radiant_token_index=0,
            horn=horn,
            pauses=[],
            duration_seconds=12,
            telonex_root=capture,
            cache_path=tmp_path / "cache.parquet",
        )
    )
    assert rows is not None
    assert len(rows) == 13 - MODEL_START_SECOND

    row0 = row_at_second(rows, 0)
    assert row0["market_status"] == "ok"
    assert row0["market_p_radiant"] == 0.6
    assert row0["state_ts_us"] == horn_us
    assert row0["signal_market_p_radiant_30s"] == 0.65
    assert row0["signal_market_p_radiant_300s"] == 0.55


def test_build_rows_inconsistent_pair(tmp_path: Path) -> None:
    """Broken midpoint sum nulls market_p and keeps the second."""
    capture = tmp_path / "telonex"
    horn = datetime(2026, 1, 1, tzinfo=UTC)
    horn_us = datetime_to_ns(horn) // NS_PER_US
    _write_pair_books(capture, horn_us=horn_us, radiant_id="111", dire_id="222")

    rows = build_market_second_rows(
        MarketCacheJob(
            match_id=1,
            condition_id="0xabc",
            event_id="event-1",
            token_ids=("111", "222"),
            radiant_token_index=0,
            horn=horn,
            pauses=[],
            duration_seconds=12,
            telonex_root=capture,
            cache_path=tmp_path / "cache.parquet",
        )
    )
    assert rows is not None
    inconsistent = row_at_second(rows, 3)
    assert inconsistent["market_status"] == "inconsistent_pair"
    assert inconsistent["market_p_radiant"] is None


def test_build_rows_age_exceeded_is_stale_quote(tmp_path: Path) -> None:
    """Quotes older than five seconds stay distinguishable from a missing book."""
    capture = tmp_path / "telonex"
    horn = datetime(2026, 1, 1, tzinfo=UTC)
    horn_us = datetime_to_ns(horn) // NS_PER_US
    _write_pair_books(capture, horn_us=horn_us, radiant_id="111", dire_id="222")

    rows = build_market_second_rows(
        MarketCacheJob(
            match_id=1,
            condition_id="0xabc",
            event_id="event-1",
            token_ids=("111", "222"),
            radiant_token_index=0,
            horn=horn,
            pauses=[],
            duration_seconds=12,
            telonex_root=capture,
            cache_path=tmp_path / "cache.parquet",
        )
    )
    assert rows is not None
    # Second 9: newest snapshot at horn+3s has age 6 → stale, not missing.
    aged = row_at_second(rows, 9)
    assert aged["market_status"] == "stale_quote"
    assert aged["market_p_radiant"] is None


def test_build_rows_orientation_flip(tmp_path: Path) -> None:
    """radiant_token_index=1 swaps token orientation into market_p_radiant."""
    capture = tmp_path / "telonex"
    horn = datetime(2026, 1, 1, tzinfo=UTC)
    horn_us = datetime_to_ns(horn) // NS_PER_US
    _write_pair_books(capture, horn_us=horn_us, radiant_id="111", dire_id="222")

    flipped = build_market_second_rows(
        MarketCacheJob(
            match_id=1,
            condition_id="0xabc",
            event_id="event-1",
            token_ids=("111", "222"),
            radiant_token_index=1,
            horn=horn,
            pauses=[],
            duration_seconds=0,
            telonex_root=capture,
            cache_path=tmp_path / "cache.parquet",
        )
    )
    assert flipped is not None
    assert row_at_second(flipped, 0)["market_p_radiant"] == 0.4


def test_build_rows_pauses_shift_state_timestamps(tmp_path: Path) -> None:
    """Post-horn pauses delay state_ts_us without changing the game second."""
    capture = tmp_path / "telonex"
    horn = datetime(2026, 1, 1, tzinfo=UTC)
    horn_us = datetime_to_ns(horn) // NS_PER_US
    write_token_book(
        capture,
        token_id="111",
        day="2026-01-01",
        rows=[
            (
                horn_us + 5_000_000,
                [{"price": 0.59, "size": 20}],
                [{"price": 0.61, "size": 20}],
            )
        ],
    )
    write_token_book(
        capture,
        token_id="222",
        day="2026-01-01",
        rows=[
            (
                horn_us + 5_000_000,
                [{"price": 0.39, "size": 20}],
                [{"price": 0.41, "size": 20}],
            )
        ],
    )
    pauses: list[OpenDotaPause] = [{"time": 1, "duration": 4}]
    rows = build_market_second_rows(
        MarketCacheJob(
            match_id=1,
            condition_id="0xabc",
            event_id="event-1",
            token_ids=("111", "222"),
            radiant_token_index=0,
            horn=horn,
            pauses=pauses,
            duration_seconds=2,
            telonex_root=capture,
            cache_path=tmp_path / "cache.parquet",
        )
    )
    assert rows is not None
    assert row_at_second(rows, 0)["state_ts_us"] == horn_us
    assert row_at_second(rows, 2)["state_ts_us"] == horn_us + 6_000_000
    assert row_at_second(rows, 2)["market_status"] == "ok"
    assert row_at_second(rows, 2)["market_p_radiant"] == 0.6


def test_build_rows_empty_window_returns_none(tmp_path: Path) -> None:
    """Directory exists but no in-window snapshots fails the match, not a null cache."""
    capture = tmp_path / "telonex"
    (capture / "book_snapshot_full" / "asset_id=111").mkdir(parents=True)
    (capture / "book_snapshot_full" / "asset_id=222").mkdir(parents=True)
    write_token_book(
        capture,
        token_id="111",
        day="2026-01-01",
        rows=[(0, [{"price": 0.5, "size": 1}], [{"price": 0.5, "size": 1}])],
    )
    write_token_book(
        capture,
        token_id="222",
        day="2026-01-01",
        rows=[(0, [{"price": 0.5, "size": 1}], [{"price": 0.5, "size": 1}])],
    )
    # Horn far after the only snapshots so the lookback window is empty.
    rows = build_market_second_rows(
        MarketCacheJob(
            match_id=1,
            condition_id="0xabc",
            event_id="event-1",
            token_ids=("111", "222"),
            radiant_token_index=0,
            horn=datetime(2026, 6, 1, tzinfo=UTC),
            pauses=[],
            duration_seconds=1,
            telonex_root=capture,
            cache_path=tmp_path / "cache.parquet",
        )
    )
    assert rows is None


def test_build_rows_missing_partition_returns_none(tmp_path: Path) -> None:
    """Missing either token partition fails the whole match."""
    capture = tmp_path / "telonex"
    write_token_book(
        capture,
        token_id="111",
        day="2026-01-01",
        rows=[(0, [{"price": 0.5, "size": 1}], [{"price": 0.5, "size": 1}])],
    )
    rows = build_market_second_rows(
        MarketCacheJob(
            match_id=1,
            condition_id="0xabc",
            event_id="event-1",
            token_ids=("111", "222"),
            radiant_token_index=0,
            horn=datetime(2026, 1, 1, tzinfo=UTC),
            pauses=[],
            duration_seconds=1,
            telonex_root=capture,
            cache_path=tmp_path / "cache.parquet",
        )
    )
    assert rows is None
