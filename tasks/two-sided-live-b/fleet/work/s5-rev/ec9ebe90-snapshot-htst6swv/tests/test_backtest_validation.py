"""Unit tests for validation-market selection, its local data view, and its reporting."""

import argparse
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from catalog_fixtures import ReplayCatalogKwargs, catalog_row

from backtest.context import (
    MarketContext,
    ReplayLookups,
    calculate_availability_window,
    calculate_clock_end,
    calculate_replay_window,
)
from backtest.marks import EnrichedFill, MidSeries
from backtest.paths import QUOTE_EVENTS_FILENAME, RESULTS_FILENAME, SUMMARY_FILENAME
from backtest.postprocess import (
    build_summary_payload,
    read_fills_checkpoint,
    summarize_arm,
    write_fills_parquet,
)
from backtest.quote_store import (
    QuoteTelemetry,
    assert_quote_event_parts_resumable,
    compact_quote_events,
    empty_quote_telemetry,
    parts_dir,
    quote_telemetry_from_events,
    read_quote_telemetry,
    write_quote_event_parts,
)
from backtest.report_types import ReplayInstrumentResult
from backtest.results import (
    MakerMatchResult,
    SignalProvenance,
    assert_manifest_matches,
    build_maker_match_results,
    concat_results_without_overlap,
    engine_fault_match_result,
    read_manifest,
    read_quote_events_checkpoint,
    read_results_checkpoint,
    write_manifest,
    write_results_checkpoint,
    zero_gate_seconds,
)
from backtest.run import (
    ShardSpec,
    assign_shard,
    clear_run_artifacts,
    match_ids_missing_results,
    merge_shard_checkpoints,
    merge_shard_run,
    parse_shard,
    plan_replay_ids,
    replay_matches,
    shard_subdir,
    write_finished_summary,
)
from backtest.selection import (
    MarketSources,
    ValidationCoverage,
    build_market_context,
    select_validation_matches,
)
from backtest.shared_archive import (
    SharedArchive,
    empty_shared_archive,
    graft_shared_archive,
    load_shared_archive,
)
from backtest.telemetry import FillRecord, MakerRecords, QuoteEvent
from backtest.telonex_local import (
    BOOK_CHANNEL,
    ONCHAIN_CHANNEL,
    REQUIRED_CHANNELS,
    clear_telonex_cache_for_market,
    create_telonex_source_tree,
    link_market_tokens,
)
from shared.constants.dataset import PREHORN_LEAD_SECONDS
from shared.types.opendota import OpenDotaPause
from shared.utils.match_catalog import MatchCatalog, catalog_entry_from_row
from shared.utils.match_time import get_horn_datetime
from shared.utils.parquet_io import write_parquet

CLOSED_AT = datetime(2026, 4, 19, 13, 13, 48, tzinfo=UTC)
GRID_STARTED_AT = datetime(2026, 4, 19, 10, 22, 53, tzinfo=UTC)
GRID_ENDED_AT = datetime(2026, 4, 19, 10, 38, 18, tzinfo=UTC)
PAUSES: list[OpenDotaPause] = [{"time": 300, "duration": 30}]
REPLAY_CATALOG: ReplayCatalogKwargs = {
    "token_id_0": "111",
    "token_id_1": "222",
    "seconds_delay": 3,
    "market_slug": "demo-market",
    "market_closed_at": CLOSED_AT.isoformat(),
    "started_at": GRID_STARTED_AT,
    "ended_at": GRID_ENDED_AT,
}


GRID_V1_PROVENANCE = SignalProvenance(signal_mode="grid_v1", feed_source="", model_name="research")


def build_capture(tmp_path: Path, days: tuple[str, ...] = ("2026-04-19",)) -> Path:
    """Local Telonex layout with both required channels for the fixture tokens/days."""
    capture = tmp_path / "capture"
    for channel in REQUIRED_CHANNELS:
        for token_id in ("111", "222"):
            asset_dir = capture / channel / f"asset_id={token_id}"
            asset_dir.mkdir(parents=True, exist_ok=True)
            for day in days:
                (asset_dir / f"{day}.parquet").touch()
    return capture


def build_sources() -> MarketSources:
    """Three validation matches: two absent from the catalog, one complete and eligible."""
    return MarketSources(
        validation_match_ids=(1, 2, 3),
        catalog=MatchCatalog(
            {
                3: catalog_entry_from_row(
                    catalog_row(3, condition_id="0xok", event_id="e3", **REPLAY_CATALOG)
                ),
            }
        ),
        usable_signal_match_ids=frozenset({1, 2, 3, 4}),
    )


def build_result(
    *,
    match_id: int,
    placement: str = "join",
    fill_model: str = "queue",
    engine_pnl: float = 1.0,
    buy_fills: int = 1,
    sell_fills: int = 1,
    terminated_early: bool = False,
    stop_reason: str | None = None,
) -> MakerMatchResult:
    """A maker match row carrying the fields resume and summary read."""
    return MakerMatchResult(
        match_id=match_id,
        condition_id=f"0x{match_id}",
        slug=f"market-{match_id}",
        seconds_delay=1,
        placement=placement,
        fill_model=fill_model,
        horn_at="2026-04-19T10:24:23+00:00",
        game_ended_at="2026-04-19T10:38:18+00:00",
        market_closed_at="2026-04-19T13:13:48+00:00",
        buy_fills=buy_fills,
        sell_fills=sell_fills,
        buy_quantity=float(5 * buy_fills),
        sell_quantity=float(5 * sell_fills),
        orders_submitted=buy_fills + sell_fills,
        orders_accepted=buy_fills + sell_fills,
        orders_canceled=0,
        orders_rejected=0,
        incomplete_orders=0,
        terminal_token_index=-1,
        terminal_side="",
        terminal_position=0.0,
        dust_position=False,
        window_seconds=100,
        live_order_seconds=40,
        gate_seconds=zero_gate_seconds(),
        cash_flow=0.1,
        engine_pnl=engine_pnl,
        settlement_applied=buy_fills + sell_fills > 0 and not terminated_early,
        terminated_early=terminated_early,
        stop_reason=stop_reason,
        signal_mode="grid_v1",
        feed_source="",
        model_name="research",
    )


def test_selection_keeps_linked_grid_markets_and_counts_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every validation match lands in exactly one coverage bucket."""
    monkeypatch.setattr("backtest.selection.RAW_TELONEX_POLYMARKET_DIR", build_capture(tmp_path))
    coverage, eligible = select_validation_matches(build_sources())

    assert coverage == ValidationCoverage(
        validation_matches=3,
        without_map_market=2,
        without_signal_rows=0,
        without_local_telonex=0,
        archive_excluded=0,
        eligible=1,
    )
    assert eligible == (3,)


def test_limit_applies_after_filtering_and_keeps_chronological_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limit cuts eligible markets, never the raw validation list."""
    monkeypatch.setattr("backtest.selection.RAW_TELONEX_POLYMARKET_DIR", build_capture(tmp_path))
    sources = build_sources()
    eligible_sources = MarketSources(
        validation_match_ids=(3, 1, 2, 4),
        catalog=MatchCatalog(
            {
                match_id: catalog_entry_from_row(
                    catalog_row(
                        match_id,
                        condition_id="0xok",
                        event_id=f"e{match_id}",
                        radiant_token_index=0,
                        **REPLAY_CATALOG,
                    )
                )
                for match_id in (1, 2, 3, 4)
            }
        ),
        usable_signal_match_ids=sources.usable_signal_match_ids,
    )

    _coverage, eligible = select_validation_matches(eligible_sources)

    # selection preserves the order the split table was sorted in
    assert eligible == (3, 1, 2, 4)
    assert eligible[:2] == (3, 1)


def test_selection_skips_match_missing_onchain_asset_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linked+GRID match without one token's onchain_fills asset dir is not eligible."""
    capture = build_capture(tmp_path)
    onchain_dir = capture / "onchain_fills" / "asset_id=222"
    for path in onchain_dir.glob("*.parquet"):
        path.unlink()
    onchain_dir.rmdir()
    monkeypatch.setattr("backtest.selection.RAW_TELONEX_POLYMARKET_DIR", capture)

    coverage, eligible = select_validation_matches(build_sources())

    assert coverage.without_local_telonex == 1
    assert coverage.eligible == 0
    assert eligible == ()


def test_selection_skips_match_when_onchain_day_file_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Onchain day files are required like books; a missing day is without_local_telonex."""
    capture = build_capture(tmp_path)
    (capture / "onchain_fills" / "asset_id=222" / "2026-04-19.parquet").unlink()
    monkeypatch.setattr("backtest.selection.RAW_TELONEX_POLYMARKET_DIR", capture)

    coverage, eligible = select_validation_matches(build_sources())

    assert coverage.without_local_telonex == 1
    assert coverage.eligible == 0
    assert eligible == ()


def test_selection_skips_match_missing_early_book_day_across_midnight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replay window that crosses UTC midnight needs every book day file."""
    started_at = datetime(2026, 4, 20, 0, 1, tzinfo=UTC)
    ended_at = datetime(2026, 4, 20, 1, 0, tzinfo=UTC)
    # Availability lead is 2 minutes, so the window starts on 2026-04-19.
    capture = build_capture(tmp_path, days=("2026-04-19", "2026-04-20"))
    (capture / "book_snapshot_full" / "asset_id=111" / "2026-04-19.parquet").unlink()
    monkeypatch.setattr("backtest.selection.RAW_TELONEX_POLYMARKET_DIR", capture)

    sources = MarketSources(
        validation_match_ids=(3,),
        catalog=MatchCatalog(
            {
                3: catalog_entry_from_row(
                    catalog_row(
                        3,
                        condition_id="0xok",
                        event_id="e3",
                        token_id_0="111",
                        token_id_1="222",
                        seconds_delay=3,
                        market_slug="demo-market",
                        market_closed_at=CLOSED_AT.isoformat(),
                        started_at=started_at,
                        ended_at=ended_at,
                    )
                ),
            }
        ),
        usable_signal_match_ids=frozenset({3}),
    )
    coverage, eligible = select_validation_matches(sources)

    assert coverage.without_local_telonex == 1
    assert coverage.eligible == 0
    assert eligible == ()


def test_selection_skips_match_without_usable_signal_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Link+GRID+Gamma+Telonex without usable signal rows is ineligible."""
    monkeypatch.setattr("backtest.selection.RAW_TELONEX_POLYMARKET_DIR", build_capture(tmp_path))
    sources = MarketSources(
        validation_match_ids=(3,),
        catalog=MatchCatalog(
            {
                3: catalog_entry_from_row(
                    catalog_row(3, condition_id="0xok", event_id="e3", **REPLAY_CATALOG)
                )
            }
        ),
        usable_signal_match_ids=frozenset(),
    )

    coverage, eligible = select_validation_matches(sources)

    assert coverage.without_signal_rows == 1
    assert coverage.eligible == 0
    assert eligible == ()


def test_availability_window_contains_horn_replay_window() -> None:
    """Selection's map-load window is a superset of the horn-anchored replay window."""
    pauses: list[OpenDotaPause] = [{"time": -30, "duration": 10}, {"time": 100, "duration": 20}]
    horn_at = get_horn_datetime(GRID_STARTED_AT, pauses)
    availability = calculate_availability_window(
        map_load_at=GRID_STARTED_AT, game_ended_at=GRID_ENDED_AT
    )
    replay = calculate_replay_window(horn_at=horn_at, game_ended_at=GRID_ENDED_AT)

    assert availability.start <= replay.start
    assert availability.end == replay.end


def test_market_context_carries_horn_delay_and_settlement() -> None:
    """A context anchors on the catalog horn and keeps the market's own delay and close."""
    sources = MarketSources(
        validation_match_ids=(3,),
        catalog=MatchCatalog(
            {
                3: catalog_entry_from_row(
                    catalog_row(
                        3,
                        condition_id="0xok",
                        event_id="e3",
                        pauses=[{"time": -30, "duration": 10}],
                        **REPLAY_CATALOG,
                    )
                ),
            }
        ),
        usable_signal_match_ids=frozenset({3}),
    )
    context = build_market_context(sources, 3)

    assert context.horn_at == datetime(2026, 4, 19, 10, 24, 33, tzinfo=UTC)
    assert context.market_closed_at == CLOSED_AT
    assert context.seconds_delay == 3
    assert context.radiant_token_index == 1
    assert context.market_slug == "demo-market"
    assert context.replay_end == GRID_ENDED_AT.replace(minute=39, second=18)
    assert context.clock_end == calculate_clock_end(
        game_ended_at=GRID_ENDED_AT, market_closed_at=CLOSED_AT
    )


def test_telonex_tree_links_both_tokens_where_the_framework_globs(tmp_path: Path) -> None:
    """The tree maps asset_id partitions onto slug/outcome_id directories."""
    capture = tmp_path / "capture"
    for token_id in ("111", "222"):
        write_valid_capture_day(capture, token_id, "2026-04-19")
    context = build_market_context(build_sources(), 3)
    root = tmp_path / "tree"
    link_market_tokens(root, context, capture)

    book_dir = root / "polymarket" / "book_snapshot_full" / "demo-market"
    assert (book_dir / "outcome_id=0").is_symlink()
    assert (book_dir / "outcome_id=0" / "2026-04-19.parquet").exists()
    assert (book_dir / "outcome_id=1" / "2026-04-19.parquet").exists()
    onchain_outcome = root / "polymarket" / "onchain_fills" / "demo-market" / "outcome_id=0"
    assert not onchain_outcome.is_symlink()
    day = onchain_outcome / "2026-04-19.parquet"
    assert day.is_file()
    assert "side" in pd.read_parquet(day).columns
    assert not (root / "polymarket" / "trades").exists()


def write_valid_capture_day(capture: Path, token_id: str, day: str) -> None:
    """Minimal readable book+onchain day so strip and the side rewrite can run."""
    book_path = capture / "book_snapshot_full" / f"asset_id={token_id}" / f"{day}.parquet"
    book_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "timestamp_us": 1_000,
                "bids": [{"price": "0.60", "size": "10"}],
                "asks": [{"price": "0.70", "size": "10"}],
            }
        ]
    ).to_parquet(book_path, index=False)
    onchain_path = capture / "onchain_fills" / f"asset_id={token_id}" / f"{day}.parquet"
    onchain_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "block_timestamp_us": 2_000,
                "asset_id": token_id,
                "taker_asset_id": token_id,
                "taker_side": "buy",
            }
        ]
    ).to_parquet(onchain_path, index=False)


def test_telonex_tree_cache_evicts_framework_cache_on_miss_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rewrite miss evicts the market's framework cache; a hit leaves it alone."""
    monkeypatch.setenv("TELONEX_TREE_CACHE", str(tmp_path / "tree-cache"))
    framework_cache = tmp_path / "telonex-cache"
    monkeypatch.setenv("TELONEX_CACHE_ROOT", str(framework_cache))
    capture = tmp_path / "capture"
    for token_id in ("111", "222"):
        write_valid_capture_day(capture, token_id, "2026-04-19")
    archive = tmp_path / "archive"
    archive.mkdir()
    loads: list[Path] = []

    def fake_load(archive_dir: Path) -> tuple[tuple[int, str, object], ...]:
        loads.append(archive_dir)
        return ()

    monkeypatch.setattr("backtest.telonex_local.load_resting_events", fake_load)
    context = build_market_context(build_sources(), 3)

    def seed_framework_cache() -> tuple[Path, Path]:
        book_tick = (
            framework_cache
            / "book-deltas-v1"
            / "polymarket"
            / "book_snapshot_full"
            / "demo-market"
            / "tick.parquet"
        )
        onchain_tick = (
            framework_cache
            / "trade-ticks-v1"
            / "polymarket"
            / "onchain_fills"
            / "demo-market"
            / "tick.parquet"
        )
        for tick in (book_tick, onchain_tick):
            tick.parent.mkdir(parents=True, exist_ok=True)
            tick.write_bytes(b"cached")
        return book_tick, onchain_tick

    book_tick, onchain_tick = seed_framework_cache()
    link_market_tokens(tmp_path / "tree1", context, capture, archive_dir=archive)
    assert not book_tick.exists()
    assert not onchain_tick.exists()
    assert loads == [archive]

    book_tick, onchain_tick = seed_framework_cache()
    link_market_tokens(tmp_path / "tree2", context, capture, archive_dir=archive)
    assert book_tick.is_file()
    assert onchain_tick.is_file()
    assert loads == [archive]


def test_source_tree_eviction_survives_a_mid_tree_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash on a later market still leaves earlier rewrites evicted."""
    monkeypatch.setenv("TELONEX_TREE_CACHE", str(tmp_path / "tree-cache"))
    framework_cache = tmp_path / "telonex-cache"
    monkeypatch.setenv("TELONEX_CACHE_ROOT", str(framework_cache))
    capture = tmp_path / "capture"
    for token_id in ("111", "222"):
        write_valid_capture_day(capture, token_id, "2026-04-19")
    onchain_tick = (
        framework_cache
        / "trade-ticks-v1"
        / "polymarket"
        / "onchain_fills"
        / "demo-market"
        / "tick.parquet"
    )
    onchain_tick.parent.mkdir(parents=True)
    onchain_tick.write_bytes(b"cached")

    context = build_market_context(build_sources(), 3)
    missing = replace(
        context, match_id=4, market_slug="no-capture-market", token_ids=("333", "444")
    )
    with (
        pytest.raises(ValueError, match="no local Telonex"),
        create_telonex_source_tree((context, missing), capture, {}),
    ):
        pass

    assert not onchain_tick.exists()


def test_cache_eviction_keeps_markets_we_did_not_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evicting one channel leaves the other channel's cache and other markets."""
    cache_root = tmp_path / "telonex-cache"
    monkeypatch.setenv("TELONEX_CACHE_ROOT", str(cache_root))

    def day(version: str, channel: str, slug: str) -> Path:
        return cache_root / version / "polymarket" / channel / slug / "2026-04-19.parquet"

    onchain_rewritten = day("trade-ticks-v1", "onchain_fills", "rewritten-market")
    onchain_untouched = day("trade-ticks-v1", "onchain_fills", "untouched-market")
    book_rewritten = day("book-deltas-v1", "book_snapshot_full", "rewritten-market")
    book_untouched = day("book-deltas-v1", "book_snapshot_full", "untouched-market")
    for path in (onchain_rewritten, onchain_untouched, book_rewritten, book_untouched):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"cached")

    clear_telonex_cache_for_market("rewritten-market", channel=ONCHAIN_CHANNEL)
    assert not onchain_rewritten.exists()
    assert onchain_untouched.is_file()
    assert book_rewritten.is_file()
    assert book_untouched.is_file()

    clear_telonex_cache_for_market("rewritten-market", channel=BOOK_CHANNEL)
    assert not book_rewritten.exists()
    assert book_untouched.is_file()
    assert onchain_untouched.is_file()


def test_missing_local_book_capture_is_rejected(tmp_path: Path) -> None:
    """A market without its local book must fail before the engine starts."""
    context = build_market_context(build_sources(), 3)
    with pytest.raises(ValueError, match="no local Telonex book_snapshot_full"):
        link_market_tokens(tmp_path / "tree", context, tmp_path / "empty")


def build_context_for_results() -> MarketContext:
    """The market whose two legs the result aggregation folds into one row."""
    replay = calculate_replay_window(horn_at=GRID_STARTED_AT, game_ended_at=GRID_ENDED_AT)
    return MarketContext(
        match_id=7,
        condition_id="0xok",
        event_id="e7",
        market_slug="demo-market",
        token_ids=("111", "222"),
        radiant_token_index=0,
        radiant_win=True,
        seconds_delay=1,
        horn_at=GRID_STARTED_AT,
        game_ended_at=GRID_ENDED_AT,
        market_closed_at=CLOSED_AT,
        replay_start=replay.start,
        replay_end=replay.end,
        clock_end=calculate_clock_end(game_ended_at=GRID_ENDED_AT, market_closed_at=CLOSED_AT),
    )


def test_engine_fault_match_result_is_terminated_without_fills() -> None:
    """A skipped engine map is a terminated row, not a missing match."""
    context = build_context_for_results()
    row = engine_fault_match_result(
        context,
        placement="join",
        fill_model="queue",
        stop_reason="nautilus_zero_fill",
        provenance=GRID_V1_PROVENANCE,
    )
    assert row.match_id == context.match_id
    assert row.terminated_early is True
    assert row.stop_reason == "nautilus_zero_fill"
    assert row.buy_fills == 0
    assert row.settlement_applied is False


def test_both_legs_and_repeated_fills_fold_into_one_match_arm_row() -> None:
    """Maker results allow fills on both legs and count BUY/SELL separately."""
    context = build_context_for_results()
    results: list[ReplayInstrumentResult] = [
        {
            "instrument_id": "0xok-111.POLYMARKET",
            "fills": 2,
            "pnl": 1.5,
            "settlement_pnl_applied": True,
            "terminated_early": False,
            "fill_events": [],
        },
        {
            "instrument_id": "0xok-222.POLYMARKET",
            "fills": 1,
            "pnl": 0.5,
            "settlement_pnl_applied": True,
            "terminated_early": False,
            "fill_events": [],
        },
    ]
    fills = [
        FillRecord(
            match_id=7,
            token_index=0,
            side="BUY",
            price=0.4,
            quantity=5.0,
            submitted_quantity=5.0,
            ts_ns=1,
            predicted_delta=0.01,
            fair=0.45,
            book_p_radiant=0.4,
            dataset_market_p=0.4,
            spread=0.02,
            placement="join",
            queue_ahead=10.0,
            position_after=5.0,
            order_id="O-1",
            episode_id=1,
            level_index=0,
            submit_level_index=0,
            level_moves=0,
            fair_at_fill=0.5,
            signal_age_seconds=1.0,
            gate_reason_at_fill="",
            episode_buy_notional=1.0,
            episode_sell_proceeds=0.0,
            episode_buy_fill_index=0,
            position_cost_basis=1.0,
            reserved_buy_notional=0.0,
            is_maker=True,
        ),
        FillRecord(
            match_id=7,
            token_index=0,
            side="SELL",
            price=0.5,
            quantity=5.0,
            submitted_quantity=5.0,
            ts_ns=2,
            predicted_delta=0.01,
            fair=0.45,
            book_p_radiant=0.4,
            dataset_market_p=0.4,
            spread=0.02,
            placement="join",
            queue_ahead=8.0,
            position_after=0.0,
            order_id="O-2",
            episode_id=1,
            level_index=0,
            submit_level_index=0,
            level_moves=0,
            fair_at_fill=0.5,
            signal_age_seconds=1.0,
            gate_reason_at_fill="",
            episode_buy_notional=1.0,
            episode_sell_proceeds=0.0,
            episode_buy_fill_index=0,
            position_cost_basis=1.0,
            reserved_buy_notional=0.0,
            is_maker=True,
        ),
        FillRecord(
            match_id=7,
            token_index=1,
            side="BUY",
            price=0.3,
            quantity=5.0,
            submitted_quantity=5.0,
            ts_ns=3,
            predicted_delta=0.02,
            fair=0.35,
            book_p_radiant=0.4,
            dataset_market_p=0.4,
            spread=0.02,
            placement="join",
            queue_ahead=5.0,
            position_after=5.0,
            order_id="O-3",
            episode_id=1,
            level_index=0,
            submit_level_index=0,
            level_moves=0,
            fair_at_fill=0.5,
            signal_age_seconds=1.0,
            gate_reason_at_fill="",
            episode_buy_notional=1.0,
            episode_sell_proceeds=0.0,
            episode_buy_fill_index=0,
            position_cost_basis=1.0,
            reserved_buy_notional=0.0,
            is_maker=True,
        ),
    ]

    outcome = build_maker_match_results(
        [context],
        results,
        fills,
        [],
        placement="join",
        fill_model="queue",
        uptimes={},
        provenance={context.match_id: GRID_V1_PROVENANCE},
    )[0]

    assert outcome.match_id == 7
    assert outcome.buy_fills == 2
    assert outcome.sell_fills == 1
    assert outcome.engine_pnl == pytest.approx(2.0)
    assert outcome.terminal_position == pytest.approx(5.0)
    assert outcome.terminal_token_index == 1
    assert outcome.terminal_side == "dire"


def test_settlement_applied_is_independent_of_leg_order() -> None:
    """One traded leg reports settlement from that leg alone, regardless of index."""
    context = build_context_for_results()
    traded: ReplayInstrumentResult = {
        "instrument_id": "0xok-222.POLYMARKET",
        "fills": 1,
        "pnl": 1.0,
        "settlement_pnl_applied": True,
        "terminated_early": False,
        "fill_events": [],
    }
    flat: ReplayInstrumentResult = {
        "instrument_id": "0xok-111.POLYMARKET",
        "fills": 0,
        "pnl": 0.0,
        "settlement_pnl_applied": False,
        "terminated_early": False,
        "fill_events": [],
    }
    fills = [
        FillRecord(
            match_id=7,
            token_index=1,
            side="BUY",
            price=0.4,
            quantity=5.0,
            submitted_quantity=5.0,
            ts_ns=1,
            predicted_delta=0.0,
            fair=0.4,
            book_p_radiant=0.4,
            dataset_market_p=0.4,
            spread=0.02,
            placement="join",
            queue_ahead=1.0,
            position_after=5.0,
            order_id="O-1",
            episode_id=1,
            level_index=0,
            submit_level_index=0,
            level_moves=0,
            fair_at_fill=0.5,
            signal_age_seconds=1.0,
            gate_reason_at_fill="",
            episode_buy_notional=1.0,
            episode_sell_proceeds=0.0,
            episode_buy_fill_index=0,
            position_cost_basis=1.0,
            reserved_buy_notional=0.0,
            is_maker=True,
        )
    ]

    # Traded leg is index 1 — the old cumulative any_fill bug reported False here.
    outcome = build_maker_match_results(
        [context],
        [flat, traded],
        fills,
        [],
        placement="join",
        fill_model="queue",
        uptimes={},
        provenance={context.match_id: GRID_V1_PROVENANCE},
    )[0]
    assert outcome.settlement_applied is True

    # Traded leg is index 0 — must stay True.
    swapped_traded: ReplayInstrumentResult = {**traded, "instrument_id": "0xok-111.POLYMARKET"}
    swapped_flat: ReplayInstrumentResult = {**flat, "instrument_id": "0xok-222.POLYMARKET"}
    swapped = build_maker_match_results(
        [context],
        [swapped_traded, swapped_flat],
        [
            FillRecord(
                match_id=7,
                token_index=0,
                side="BUY",
                price=0.4,
                quantity=5.0,
                submitted_quantity=5.0,
                ts_ns=1,
                predicted_delta=0.0,
                fair=0.4,
                book_p_radiant=0.4,
                dataset_market_p=0.4,
                spread=0.02,
                placement="join",
                queue_ahead=1.0,
                position_after=5.0,
                order_id="O-1",
                episode_id=1,
                level_index=0,
                submit_level_index=0,
                level_moves=0,
                fair_at_fill=0.5,
                signal_age_seconds=1.0,
                gate_reason_at_fill="",
                episode_buy_notional=1.0,
                episode_sell_proceeds=0.0,
                episode_buy_fill_index=0,
                position_cost_basis=1.0,
                reserved_buy_notional=0.0,
                is_maker=True,
            )
        ],
        [],
        placement="join",
        fill_model="queue",
        uptimes={},
        provenance={context.match_id: GRID_V1_PROVENANCE},
    )[0]
    assert swapped.settlement_applied is True


def test_a_match_without_fills_is_recorded_as_flat() -> None:
    """No entry means zero fill counters and no settlement."""
    context = build_context_for_results()
    results: list[ReplayInstrumentResult] = [
        {
            "instrument_id": instrument_id,
            "fills": 0,
            "pnl": 0.0,
            "terminated_early": False,
            "fill_events": [],
        }
        for instrument_id in context.instrument_ids
    ]

    outcome = build_maker_match_results(
        [context],
        results,
        [],
        [],
        placement="join",
        fill_model="queue",
        uptimes={},
        provenance={context.match_id: GRID_V1_PROVENANCE},
    )[0]

    assert outcome.buy_fills == 0
    assert outcome.sell_fills == 0
    assert outcome.settlement_applied is False
    assert outcome.engine_pnl == 0.0


def test_missing_instrument_results_are_rejected() -> None:
    """An empty framework result list must not collapse into a KeyError."""
    context = build_context_for_results()
    with pytest.raises(ValueError, match="no results for matches \\[7\\]"):
        build_maker_match_results(
            [context],
            [],
            [],
            [],
            placement="join",
            fill_model="queue",
            uptimes={},
            provenance={context.match_id: GRID_V1_PROVENANCE},
        )


def test_summary_counts_per_arm() -> None:
    """summary.json reports one arm keyed by placement/fill_model."""
    results = [
        build_result(match_id=1, engine_pnl=1.0),
        build_result(match_id=2, engine_pnl=-0.5, buy_fills=0, sell_fills=0),
    ]
    coverage = ValidationCoverage(
        validation_matches=976,
        without_map_market=100,
        without_signal_rows=0,
        without_local_telonex=0,
        archive_excluded=0,
        eligible=555,
    )
    summary = build_summary_payload(
        results=results,
        fills=[],
        drawdowns={},
        coverage=coverage,
        selected=2,
        wall_seconds=1.5,
        manifest={"model_name": "abc"},
        telemetry=quote_telemetry_from_events(
            [build_quote_event(match_id=1, kind="canceled", reason="nw_velocity")]
        ),
        mids={},
        contexts={},
    )
    assert summary["selected"] == 2
    assert summary["manifest"]["model_name"] == "abc"
    assert len(summary["arms"]) == 1
    arm = summary["arms"][0]
    assert arm["placement"] == "join"
    assert arm["fill_model"] == "queue"
    assert arm["matches"] == 2
    assert arm["traded"] == 1
    assert arm["no_trades"] == 1
    assert arm["quote_events"]["canceled_reason"] == {"nw_velocity": 1}
    assert "assumptions" in summary


def test_summarize_arm_results_excludes_terminated_from_pnl() -> None:
    """terminated_early rows count as terminated, not traded, and skip PnL totals."""
    results = [
        build_result(match_id=1, engine_pnl=2.0),
        build_result(
            match_id=2,
            engine_pnl=99.0,
            buy_fills=1,
            sell_fills=0,
            terminated_early=True,
            stop_reason="account",
        ),
    ]
    summary = summarize_arm(results, [], {}, empty_quote_telemetry(), {}, {})
    assert summary["matches"] == 2
    assert summary["completed"] == 1
    assert summary["terminated"] == 1
    assert summary["traded"] == 1
    assert summary["total_engine_pnl"] == pytest.approx(2.0)


def test_checkpoint_round_trips_match_ids(tmp_path: Path) -> None:
    """Resume indexes completed work by match_id."""
    assert read_results_checkpoint(tmp_path) == []

    results = [
        build_result(match_id=1),
        build_result(match_id=2),
    ]
    write_results_checkpoint(report_dir=tmp_path, results=results)

    loaded = read_results_checkpoint(tmp_path)
    assert loaded == results
    assert {result.match_id for result in loaded} == {1, 2}


def test_read_results_checkpoint_ignores_legacy_cutoff_columns(tmp_path: Path) -> None:
    """Extra forced-hold columns on an otherwise current row are ignored."""
    result = build_result(match_id=1, engine_pnl=3.5)
    frame = pd.DataFrame([asdict(result)])
    frame["forced_hold"] = True
    frame["cutoff_qty_0"] = 10.0
    frame["cutoff_qty_1"] = 5.0
    frame["cutoff_mid_0"] = 0.4
    frame["cutoff_mid_1"] = 0.6
    frame["pnl_at_cutoff"] = 1.0
    frame["tail_pnl"] = 2.0
    frame["settled_pnl"] = 3.0
    write_parquet(frame, tmp_path / RESULTS_FILENAME)
    loaded = read_results_checkpoint(tmp_path)
    assert loaded[0] == result


def test_match_ids_missing_results_skips_complete_selection() -> None:
    """Ids that already have a result row are not replayed."""
    completed = {1, 2}
    assert match_ids_missing_results((1, 2), completed) == ()


def test_match_ids_missing_results_keeps_unfinished() -> None:
    """An id without a result row stays in the pending list."""
    completed = {1}
    assert match_ids_missing_results((1, 2), completed) == (2,)


def test_fingerprint_mismatch_blocks_resume(tmp_path: Path) -> None:
    """--resume refuses when manifest fingerprint keys differ."""
    write_manifest(tmp_path, {"model_name": "old", "fill_model": "queue"})
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches(
            {"model_name": "old", "fill_model": "queue"},
            {"model_name": "new", "fill_model": "queue"},
        )
    frame = pd.DataFrame([{"match_id": 1}])
    assert list(frame["match_id"]) == [1]


def build_enriched_fill(*, match_id: int = 1) -> EnrichedFill:
    """Minimal enriched fill for checkpoint round-trips."""
    return EnrichedFill(
        match_id=match_id,
        token_index=0,
        side="BUY",
        price=0.4,
        quantity=5.0,
        submitted_quantity=5.0,
        ts_ns=1,
        predicted_delta=0.0,
        fair=0.4,
        book_p_radiant=0.4,
        dataset_market_p=0.4,
        spread=0.02,
        placement="join",
        queue_ahead=1.0,
        position_after=5.0,
        order_id="O-1",
        episode_id=1,
        level_index=0,
        submit_level_index=0,
        level_moves=0,
        fair_at_fill=0.5,
        signal_age_seconds=1.0,
        gate_reason_at_fill="",
        episode_buy_notional=1.0,
        episode_sell_proceeds=0.0,
        episode_buy_fill_index=0,
        position_cost_basis=1.0,
        reserved_buy_notional=0.0,
        is_maker=True,
        fill_model="queue",
        fee_base=1.2,
        maker_rebate=0.009,
        taker_fee=0.0,
        reference_30s=0.41,
        reference_300s=0.42,
        markout_30s=0.01,
        markout_300s=0.02,
        reference_source_30s="mid",
        reference_source_300s="mid",
        board_age_seconds=5.0,
    )


def build_quote_event(
    *, match_id: int = 1, kind: str = "no_quote", reason: str = "stale_book"
) -> QuoteEvent:
    """Minimal quote event for shard checkpoint round-trips."""
    return QuoteEvent(
        match_id=match_id,
        ts_ns=1,
        kind=kind,
        token_index=-1,
        side="",
        price=0.0,
        reason=reason,
        predicted_delta=0.0,
        fair=0.0,
        book_p_radiant=0.0,
        spread=0.0,
        episode_id=0,
        order_id="",
        quantity=0.0,
        level_index=-1,
        submit_level_index=-1,
        reserved_buy_notional=0.0,
    )


def test_clear_run_artifacts_removes_stale_resume_state(tmp_path: Path) -> None:
    """A fresh run must not inherit a foreign manifest or checkpoint."""
    write_manifest(tmp_path, {"model_name": "stale"})
    write_results_checkpoint(
        report_dir=tmp_path,
        results=[build_result(match_id=1)],
    )
    write_fills_parquet(
        report_dir=tmp_path,
        fills=[build_enriched_fill(match_id=1)],
    )
    clear_run_artifacts(tmp_path)
    assert not (tmp_path / "manifest.json").exists()
    assert not (tmp_path / "results.parquet").exists()
    assert not (tmp_path / "fills.parquet").exists()


def test_read_fills_checkpoint_rejects_missing_match_id(tmp_path: Path) -> None:
    """Older fills.parquet without match_id must not KeyError on resume."""
    frame = pd.DataFrame([asdict(build_enriched_fill())]).drop(columns=["match_id"])
    frame.to_parquet(tmp_path / "fills.parquet", index=False)
    with pytest.raises(ValueError, match="missing columns"):
        read_fills_checkpoint(tmp_path)


def test_maker_match_result_records_uptime_window_and_gate_seconds() -> None:
    """Per-gate no_quote seconds, live-order uptime, and prehorn+horn→game-end window persist."""
    context = build_context_for_results()
    results: list[ReplayInstrumentResult] = [
        {
            "instrument_id": instrument_id,
            "fills": 0,
            "pnl": 0.0,
            "terminated_early": False,
            "fill_events": [],
        }
        for instrument_id in context.instrument_ids
    ]
    events = [
        QuoteEvent(
            match_id=7,
            ts_ns=1,
            kind="no_quote",
            token_index=-1,
            side="",
            price=0.0,
            reason="anchor",
            predicted_delta=0.01,
            fair=0.5,
            book_p_radiant=0.52,
            spread=0.02,
            episode_id=0,
            order_id="",
            quantity=0.0,
            level_index=-1,
            submit_level_index=-1,
            reserved_buy_notional=0.0,
        ),
        QuoteEvent(
            match_id=7,
            ts_ns=2,
            kind="no_quote",
            token_index=-1,
            side="",
            price=0.0,
            reason="anchor",
            predicted_delta=0.01,
            fair=0.5,
            book_p_radiant=0.52,
            spread=0.02,
            episode_id=0,
            order_id="",
            quantity=0.0,
            level_index=-1,
            submit_level_index=-1,
            reserved_buy_notional=0.0,
        ),
        QuoteEvent(
            match_id=7,
            ts_ns=3,
            kind="no_quote",
            token_index=-1,
            side="",
            price=0.0,
            reason="mystery",
            predicted_delta=0.0,
            fair=0.0,
            book_p_radiant=0.0,
            spread=0.0,
            episode_id=0,
            order_id="",
            quantity=0.0,
            level_index=-1,
            submit_level_index=-1,
            reserved_buy_notional=0.0,
        ),
        QuoteEvent(
            match_id=7,
            ts_ns=4,
            kind="accepted",
            token_index=0,
            side="BUY",
            price=0.48,
            reason="",
            predicted_delta=0.01,
            fair=0.5,
            book_p_radiant=0.5,
            spread=0.02,
            episode_id=0,
            order_id="",
            quantity=0.0,
            level_index=-1,
            submit_level_index=-1,
            reserved_buy_notional=0.0,
        ),
    ]
    outcome = build_maker_match_results(
        [context],
        results,
        [],
        events,
        placement="join",
        fill_model="queue",
        uptimes={7: 12},
        provenance={context.match_id: GRID_V1_PROVENANCE},
    )[0]
    assert outcome.live_order_seconds == 12
    assert outcome.window_seconds == (
        int((GRID_ENDED_AT - GRID_STARTED_AT).total_seconds()) + PREHORN_LEAD_SECONDS
    )
    assert outcome.gate_seconds.anchor == 2
    assert outcome.gate_seconds.other == 1
    assert outcome.orders_accepted == 1
    assert outcome.terminal_side == ""


def test_fills_checkpoint_round_trips_enriched_fill(tmp_path: Path) -> None:
    """Resume reads EnrichedFill including fill_model and markout columns."""
    original = [build_enriched_fill(match_id=3)]
    write_fills_parquet(report_dir=tmp_path, fills=original)
    loaded = read_fills_checkpoint(tmp_path)
    assert loaded == original
    assert loaded[0].fill_model == "queue"
    assert loaded[0].reference_source_30s == "mid"
    assert loaded[0].order_id == "O-1"
    assert loaded[0].submitted_quantity == pytest.approx(5.0)


def test_parse_shard_accepts_zero_based_index() -> None:
    """--shard 0/4 is the first of four workers."""
    assert parse_shard("0/4") == ShardSpec(index=0, count=4)
    assert parse_shard("3/4") == ShardSpec(index=3, count=4)


def test_parse_shard_rejects_out_of_range() -> None:
    """Index must be in 0..n-1 and n must be at least 2."""
    with pytest.raises(ValueError, match="index must be in"):
        parse_shard("4/4")
    with pytest.raises(ValueError, match="count must be"):
        parse_shard("0/1")
    with pytest.raises(ValueError, match="must be i/n"):
        parse_shard("0")


def test_assign_shard_partitions_without_overlap() -> None:
    """Uniform weights split by count like the old round-robin slice."""
    remaining = tuple(range(227))
    weights = {match_id: 1 for match_id in remaining}
    parts = [assign_shard(remaining, ShardSpec(i, 4), weights) for i in range(4)]
    assert [len(part) for part in parts] == [57, 57, 57, 56]
    assert not set(parts[0]) & set(parts[1])
    assert sorted(match_id for part in parts for match_id in part) == list(remaining)


def test_assign_shard_balances_weight_over_count() -> None:
    """Heavy ids spread across shards; totals differ by at most the small ids."""
    weights = {0: 10, 1: 9, 2: 1, 3: 1}
    parts = [assign_shard(tuple(weights), ShardSpec(i, 2), weights) for i in range(2)]
    assert sorted(parts[0]) == [0, 3]
    assert sorted(parts[1]) == [1, 2]


def test_shard_subdir_nests_under_parent(tmp_path: Path) -> None:
    """Shard checkpoints live in shard_<i>of<n> under the canonical run dir."""
    assert shard_subdir(tmp_path, ShardSpec(2, 4)) == tmp_path / "shard_2of4"


def test_concat_results_without_overlap_joins_groups() -> None:
    """Merge concatenates two match groups without overlap."""
    merged = concat_results_without_overlap(
        (
            [build_result(match_id=1)],
            [build_result(match_id=2)],
        )
    )
    assert [result.match_id for result in merged] == [1, 2]


def test_concat_results_without_overlap_rejects_duplicate_match_id() -> None:
    """The same match_id in two groups is a shard overlap bug."""
    with pytest.raises(ValueError, match="duplicate match_id"):
        concat_results_without_overlap(
            (
                [build_result(match_id=1)],
                [build_result(match_id=1)],
            )
        )


def test_merge_shard_checkpoints_concats_parent_and_shards(tmp_path: Path) -> None:
    """Parent match 1 plus shard match 2 become one checkpoint; missing shard dirs fail."""
    manifest = {"model_name": "abc", "fill_model": "queue"}
    write_manifest(tmp_path, manifest)
    write_results_checkpoint(
        report_dir=tmp_path,
        results=[build_result(match_id=1)],
    )
    write_fills_parquet(
        report_dir=tmp_path,
        fills=[build_enriched_fill(match_id=1)],
    )
    shard0 = shard_subdir(tmp_path, ShardSpec(0, 2))
    shard0.mkdir()
    write_manifest(shard0, manifest)
    write_results_checkpoint(
        report_dir=shard0,
        results=[build_result(match_id=2)],
    )
    write_fills_parquet(
        report_dir=shard0,
        fills=[build_enriched_fill(match_id=2)],
    )
    shard1 = shard_subdir(tmp_path, ShardSpec(1, 2))
    shard1.mkdir()
    write_manifest(shard1, manifest)
    merged = merge_shard_checkpoints(
        parent_dir=tmp_path,
        shard_count=2,
        manifest=manifest,
        archive=empty_shared_archive(),
    )
    assert len(merged.results) == 2
    assert {result.match_id for result in merged.results} == {1, 2}
    assert len(merged.fills) == 2
    loaded = read_results_checkpoint(tmp_path)
    assert len(loaded) == 2


def test_merge_shard_checkpoints_shards_only_dir(tmp_path: Path) -> None:
    """Merge concatenates shard parquets when the parent has no manifest or results."""
    manifest = {"model_name": "abc", "fill_model": "queue"}
    shard0 = shard_subdir(tmp_path, ShardSpec(0, 2))
    shard0.mkdir()
    write_manifest(shard0, manifest)
    write_results_checkpoint(
        report_dir=shard0,
        results=[build_result(match_id=1)],
    )
    write_fills_parquet(
        report_dir=shard0,
        fills=[build_enriched_fill(match_id=1)],
    )
    write_quote_event_parts(
        report_dir=shard0,
        events=[build_quote_event(match_id=1)],
        match_ids=[1],
    )
    shard1 = shard_subdir(tmp_path, ShardSpec(1, 2))
    shard1.mkdir()
    write_manifest(shard1, manifest)
    write_results_checkpoint(
        report_dir=shard1,
        results=[build_result(match_id=2)],
    )
    write_quote_event_parts(
        report_dir=shard1,
        events=[build_quote_event(match_id=2)],
        match_ids=[2],
    )
    merged = merge_shard_checkpoints(
        parent_dir=tmp_path,
        shard_count=2,
        manifest=manifest,
        archive=empty_shared_archive(),
    )
    assert {result.match_id for result in merged.results} == {1, 2}
    compact_quote_events(report_dir=tmp_path, match_ids=[1, 2])
    events = read_quote_events_checkpoint(tmp_path)
    assert [event.match_id for event in events] == [1, 2]
    # Without this a reader of the merged run cannot tell which parameters produced it.
    assert read_manifest(tmp_path) == manifest


def test_merge_shard_checkpoints_rejects_missing_shard_dir(tmp_path: Path) -> None:
    """Merge fails closed when a worker never wrote its subdirectory."""
    manifest = {"model_name": "abc", "fill_model": "queue"}
    write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match="missing shard directory"):
        merge_shard_checkpoints(
            parent_dir=tmp_path,
            shard_count=2,
            manifest=manifest,
            archive=empty_shared_archive(),
        )


def test_load_shared_archive_skips_missing_done_and_foreign_manifest(tmp_path: Path) -> None:
    """No DONE, or a DONE built for another fingerprint, means the seed replays itself."""
    manifest = {"model_name": "abc", "fill_model": "queue"}
    assert load_shared_archive(tmp_path, manifest).ids == frozenset()
    write_manifest(tmp_path, manifest)
    write_results_checkpoint(report_dir=tmp_path, results=[build_result(match_id=10)])
    write_fills_parquet(report_dir=tmp_path, fills=[build_enriched_fill(match_id=10)])
    (tmp_path / "DONE").write_text("ok\n")
    assert (
        load_shared_archive(tmp_path, {"model_name": "other", "fill_model": "queue"}).ids
        == frozenset()
    )
    loaded = load_shared_archive(tmp_path, manifest)
    assert loaded.ids == frozenset({10})


def test_merge_shard_checkpoints_prefers_archive_rows_and_parts(tmp_path: Path) -> None:
    """Archive ids drop out of the parent and shards; the archive row and part remain."""
    manifest = {"model_name": "abc", "fill_model": "queue"}
    write_manifest(tmp_path, manifest)
    write_results_checkpoint(
        report_dir=tmp_path,
        results=[build_result(match_id=1), build_result(match_id=10, engine_pnl=1.0)],
    )
    write_fills_parquet(
        report_dir=tmp_path,
        fills=[
            build_enriched_fill(match_id=1),
            replace(build_enriched_fill(match_id=10), order_id="parent"),
        ],
    )
    shard0 = shard_subdir(tmp_path, ShardSpec(0, 2))
    shard0.mkdir()
    write_manifest(shard0, manifest)
    write_results_checkpoint(report_dir=shard0, results=[build_result(match_id=2)])
    write_fills_parquet(report_dir=shard0, fills=[build_enriched_fill(match_id=2)])
    write_quote_event_parts(
        report_dir=shard0, events=[build_quote_event(match_id=2)], match_ids=[2]
    )
    shard1 = shard_subdir(tmp_path, ShardSpec(1, 2))
    shard1.mkdir()
    write_manifest(shard1, manifest)
    write_results_checkpoint(report_dir=shard1, results=[build_result(match_id=10, engine_pnl=3.0)])
    write_fills_parquet(
        report_dir=shard1,
        fills=[replace(build_enriched_fill(match_id=10), order_id="shard")],
    )
    write_quote_event_parts(
        report_dir=shard1,
        events=[build_quote_event(match_id=10, reason="from-shard")],
        match_ids=[10],
    )
    archive = SharedArchive(
        results=(build_result(match_id=10, engine_pnl=9.0),),
        fills=(replace(build_enriched_fill(match_id=10), order_id="archive"),),
        events=(build_quote_event(match_id=10, reason="from-archive"),),
        ids=frozenset({10}),
    )
    merged = merge_shard_checkpoints(
        parent_dir=tmp_path, shard_count=2, manifest=manifest, archive=archive
    )
    by_id = {result.match_id: result.engine_pnl for result in merged.results}
    assert by_id == {10: 9.0, 1: 1.0, 2: 1.0}
    assert [result.match_id for result in merged.results] == [10, 1, 2]
    fills = read_fills_checkpoint(tmp_path)
    assert sorted((fill.match_id, fill.order_id) for fill in fills) == [
        (1, "O-1"),
        (2, "O-1"),
        (10, "archive"),
    ]
    compact_quote_events(report_dir=tmp_path, match_ids=[10, 1, 2])
    events = read_quote_events_checkpoint(tmp_path)
    assert [(event.match_id, event.reason) for event in events] == [
        (10, "from-archive"),
        (2, "stale_book"),
    ]


def test_finished_archive_is_write_once(tmp_path: Path) -> None:
    """A matching DONE is left untouched; a different fingerprint must be deleted by hand."""
    manifest = {"model_name": "abc", "archives_only": True}
    write_manifest(tmp_path, manifest)
    write_results_checkpoint(report_dir=tmp_path, results=[build_result(match_id=10)])
    (tmp_path / "DONE").write_text("ok\n")
    args = argparse.Namespace(match_id=None, merge_shards=None, shard=None, resume=False)

    def unused_lookups(_ids: Sequence[int]) -> ReplayLookups:
        raise AssertionError("finished archive must not plan a replay")

    def unused_weights(_ids: Sequence[int]) -> Mapping[int, int]:
        raise AssertionError("finished archive must not plan a replay")

    assert (
        plan_replay_ids(
            args,
            (10,),
            None,
            tmp_path,
            manifest,
            unused_lookups,
            unused_weights,
            empty_shared_archive(),
            True,
        )
        is None
    )
    with pytest.raises(ValueError, match="delete `_archive` to rebuild"):
        plan_replay_ids(
            args,
            (10,),
            None,
            tmp_path,
            {**manifest, "model_name": "other"},
            unused_lookups,
            unused_weights,
            empty_shared_archive(),
            True,
        )


def test_graft_shared_archive_prefers_archive_rows(tmp_path: Path) -> None:
    """Archive rows replace seed rows with the same id; no-replay maps get no part."""
    write_results_checkpoint(report_dir=tmp_path, results=[build_result(match_id=1)])
    write_fills_parquet(report_dir=tmp_path, fills=[build_enriched_fill(match_id=1)])
    archive = SharedArchive(
        results=(
            build_result(match_id=1, engine_pnl=4.0),
            build_result(match_id=2, engine_pnl=5.0),
            build_result(match_id=3, stop_reason="empty_signal_tape"),
        ),
        fills=(
            replace(build_enriched_fill(match_id=1), order_id="archive"),
            build_enriched_fill(match_id=2),
        ),
        events=(build_quote_event(match_id=2, reason="archived"),),
        ids=frozenset({1, 2, 3}),
    )
    graft_shared_archive(tmp_path, archive, {"model_name": "seed"})
    loaded = read_results_checkpoint(tmp_path)
    assert [(row.match_id, row.engine_pnl, row.stop_reason) for row in loaded] == [
        (1, 4.0, None),
        (2, 5.0, None),
        (3, 1.0, "empty_signal_tape"),
    ]
    fills = read_fills_checkpoint(tmp_path)
    assert [(fill.match_id, fill.order_id) for fill in fills] == [(1, "archive"), (2, "O-1")]
    assert (parts_dir(tmp_path) / "1.parquet").is_file()
    assert (parts_dir(tmp_path) / "2.parquet").is_file()
    assert not (parts_dir(tmp_path) / "3.parquet").exists()
    assert read_manifest(tmp_path)["model_name"] == "seed"


def test_write_finished_summary_marks_archive_done(tmp_path: Path) -> None:
    """DONE is written only after an archives-only run verifies."""
    result = build_result(match_id=1)
    fill = build_enriched_fill(match_id=1)
    write_results_checkpoint(report_dir=tmp_path, results=[result])
    write_fills_parquet(report_dir=tmp_path, fills=[fill])
    write_quote_event_parts(
        report_dir=tmp_path, events=[build_quote_event(match_id=1)], match_ids=[1]
    )
    problems = write_finished_summary(
        results=[result],
        fills=[fill],
        context_by_match={1: replace(build_context_for_results(), match_id=1)},
        mids={1: MidSeries(timestamps_ns=(), market_ps=())},
        coverage=None,
        selected=1,
        wall_seconds=0.0,
        manifest={
            "model_name": "abc",
            "fill_model": "queue",
            "archive_exclusions": {},
            "archives_only": True,
        },
        report_dir=tmp_path,
        archives_only=True,
    )
    assert problems == ()
    assert (tmp_path / "DONE").read_text() == "ok\n"
    assert not (tmp_path / "DONE.tmp").exists()


def build_finished_shard(
    parent: Path, index: int, count: int, match_id: int, manifest: Mapping[str, Any]
) -> Path:
    """A shard dir in its terminal state: checkpoints, parts, and its compacted tape."""
    shard = shard_subdir(parent, ShardSpec(index, count))
    shard.mkdir()
    write_manifest(shard, manifest)
    write_results_checkpoint(report_dir=shard, results=[build_result(match_id=match_id)])
    write_fills_parquet(report_dir=shard, fills=[build_enriched_fill(match_id=match_id)])
    write_quote_event_parts(
        report_dir=shard, events=[build_quote_event(match_id=match_id)], match_ids=[match_id]
    )
    compact_quote_events(report_dir=shard, match_ids=[match_id])
    return shard


def build_finished_lookups(match_ids: Sequence[int]) -> ReplayLookups:
    """Contexts and empty mids for the given ids, enough for summary finalization."""
    return ReplayLookups(
        pauses_by_match={},
        context_by_match={
            match_id: replace(build_context_for_results(), match_id=match_id)
            for match_id in match_ids
        },
        mids={match_id: MidSeries(timestamps_ns=(), market_ps=()) for match_id in match_ids},
    )


def test_merge_shard_run_drops_shards_and_parts_after_summary(tmp_path: Path) -> None:
    """A verified merge deletes shard dirs and parent parts; the final parquets stay."""
    manifest: dict[str, Any] = {
        "model_name": "abc",
        "fill_model": "queue",
        "archive_exclusions": {},
    }
    build_finished_shard(tmp_path, 0, 2, 1, manifest)
    build_finished_shard(tmp_path, 1, 2, 2, manifest)

    merge_shard_run(
        tmp_path, 2, manifest, (1, 2), None, build_finished_lookups, empty_shared_archive(), False
    )

    assert (tmp_path / SUMMARY_FILENAME).is_file()
    assert [event.match_id for event in read_quote_events_checkpoint(tmp_path)] == [1, 2]
    assert not parts_dir(tmp_path).exists()
    assert not shard_subdir(tmp_path, ShardSpec(0, 2)).exists()
    assert not shard_subdir(tmp_path, ShardSpec(1, 2)).exists()


def test_merge_shard_run_keeps_everything_when_a_part_is_missing(tmp_path: Path) -> None:
    """A missing expected part means the compacted tape is incomplete: keep all inputs."""
    manifest: dict[str, Any] = {
        "model_name": "abc",
        "fill_model": "queue",
        "archive_exclusions": {},
    }
    build_finished_shard(tmp_path, 0, 2, 1, manifest)
    shard1 = build_finished_shard(tmp_path, 1, 2, 2, manifest)
    (parts_dir(shard1) / "2.parquet").unlink()

    merge_shard_run(
        tmp_path, 2, manifest, (1, 2), None, build_finished_lookups, empty_shared_archive(), False
    )

    assert shard_subdir(tmp_path, ShardSpec(0, 2)).is_dir()
    assert shard1.is_dir()
    assert (parts_dir(tmp_path) / "1.parquet").is_file()


def test_merge_shard_run_keeps_shards_whose_writer_never_finished(tmp_path: Path) -> None:
    """A shard without its own compacted tape may still be running: keep shard dirs."""
    manifest: dict[str, Any] = {
        "model_name": "abc",
        "fill_model": "queue",
        "archive_exclusions": {},
    }
    build_finished_shard(tmp_path, 0, 2, 1, manifest)
    shard1 = build_finished_shard(tmp_path, 1, 2, 2, manifest)
    (shard1 / QUOTE_EVENTS_FILENAME).unlink()

    merge_shard_run(
        tmp_path, 2, manifest, (1, 2), None, build_finished_lookups, empty_shared_archive(), False
    )

    assert shard_subdir(tmp_path, ShardSpec(0, 2)).is_dir()
    assert shard1.is_dir()
    assert not parts_dir(tmp_path).exists()


def test_write_finished_summary_drops_verified_parts(tmp_path: Path) -> None:
    """A plain finished run drops its parts once summary.json lands and checks pass."""
    result = build_result(match_id=1)
    fill = build_enriched_fill(match_id=1)
    write_results_checkpoint(report_dir=tmp_path, results=[result])
    write_fills_parquet(report_dir=tmp_path, fills=[fill])
    write_quote_event_parts(
        report_dir=tmp_path, events=[build_quote_event(match_id=1)], match_ids=[1]
    )

    problems = write_finished_summary(
        results=[result],
        fills=[fill],
        context_by_match={1: replace(build_context_for_results(), match_id=1)},
        mids={1: MidSeries(timestamps_ns=(), market_ps=())},
        coverage=None,
        selected=1,
        wall_seconds=0.0,
        manifest={"model_name": "abc", "fill_model": "queue", "archive_exclusions": {}},
        report_dir=tmp_path,
        archives_only=False,
    )

    assert problems == ()
    assert (tmp_path / SUMMARY_FILENAME).is_file()
    assert not parts_dir(tmp_path).exists()
    assert not (tmp_path / "DONE").exists()
    assert [event.match_id for event in read_quote_events_checkpoint(tmp_path)] == [1]


def test_compact_quote_events_reports_missing_and_unaccounted(tmp_path: Path) -> None:
    """The compact report names the missing part and any foreign file before cleanup."""
    write_quote_event_parts(
        report_dir=tmp_path, events=[build_quote_event(match_id=1)], match_ids=[1]
    )
    (parts_dir(tmp_path) / "stray.tmp").write_bytes(b"leftover")

    compacted = compact_quote_events(report_dir=tmp_path, match_ids=[1, 2])

    assert compacted.rows == 1
    assert compacted.missing == (2,)
    assert compacted.unaccounted == ("stray.tmp",)
    assert compacted.wrote


def test_replay_matches_writes_one_quote_event_part_per_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every map writes its own part; a resume compacts old and new parts without rereading."""

    @contextmanager
    def fake_tree(
        _contexts: object,
        _capture_root: object,
        _schedule_archives: object,
    ):
        """Stand in for the Telonex symlink tree."""
        yield tmp_path

    def fake_run_batch(
        *, contexts: Sequence[MarketContext], **_kwargs: object
    ) -> tuple[list[ReplayInstrumentResult], MakerRecords]:
        """One empty-fill result pair plus a quote event for the batch's match."""
        context = contexts[0]
        event = build_quote_event(match_id=context.match_id)
        results: list[ReplayInstrumentResult] = [
            {
                "instrument_id": context.instrument_ids[0],
                "fills": 0,
                "pnl": 0.0,
                "fill_events": [],
                "settlement_pnl_applied": True,
                "terminated_early": False,
            },
            {
                "instrument_id": context.instrument_ids[1],
                "fills": 0,
                "pnl": 0.0,
                "fill_events": [],
                "settlement_pnl_applied": True,
                "terminated_early": False,
            },
        ]
        return results, MakerRecords(fills=(), quote_events=(event,), uptimes=())

    read_calls: list[Path] = []
    real_read = read_quote_telemetry

    def counting_read(report_dir: Path) -> QuoteTelemetry:
        """Count tape reads from replay_matches."""
        read_calls.append(report_dir)
        return real_read(report_dir)

    monkeypatch.setattr("backtest.run.create_telonex_source_tree", fake_tree)
    monkeypatch.setattr("backtest.run.run_batch", fake_run_batch)
    monkeypatch.setattr("backtest.run.read_quote_telemetry", counting_read)

    first = replace(build_context_for_results(), match_id=1)
    second = replace(build_context_for_results(), match_id=2)
    third = replace(build_context_for_results(), match_id=3)
    manifest = {
        "model_name": "quote-mem",
        "exit_settle_seconds": 10.0,
        "fill_model": "queue",
    }

    def replay(
        contexts: list[MarketContext],
        resumed: list[MakerMatchResult],
        skip_keys: frozenset[int],
    ) -> None:
        """Replay pending matches with the given resume state."""
        replay_matches(
            contexts=contexts,
            context_by_match={context.match_id: context for context in contexts},
            signals={},
            pauses_by_match={},
            mids={},
            report_dir=tmp_path,
            resumed=resumed,
            resumed_fills=[],
            coverage=None,
            selected=len(contexts),
            write_summary=False,
            skip_keys=skip_keys,
            manifest=manifest,
            capture_root=tmp_path,
            kernels={},
            schedule_archives={},
            provenance={context.match_id: GRID_V1_PROVENANCE for context in contexts},
            plans={},
            game="dota",
            archives_only=False,
        )

    replay([first, second], [], frozenset[int]())
    assert read_calls == []
    parts = sorted(path.name for path in parts_dir(tmp_path).glob("*.parquet"))
    assert parts == ["1.parquet", "2.parquet"]
    written = read_quote_events_checkpoint(tmp_path)
    assert [event.match_id for event in written] == [1, 2]

    (tmp_path / QUOTE_EVENTS_FILENAME).unlink()
    resumed_results = read_results_checkpoint(tmp_path)
    skip_keys = frozenset(result.match_id for result in resumed_results)
    replay([first, second, third], resumed_results, skip_keys)
    assert read_calls == []
    merged = read_quote_events_checkpoint(tmp_path)
    assert [event.match_id for event in merged] == [1, 2, 3]


def test_replay_matches_propagates_an_unrelated_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An AssertionError from the engine fails the run; earlier maps stay checkpointed."""

    @contextmanager
    def fake_tree(
        _contexts: object,
        _capture_root: object,
        _schedule_archives: object,
    ):
        yield tmp_path

    def fake_run_batch(
        *, contexts: Sequence[MarketContext], **_kwargs: object
    ) -> tuple[list[ReplayInstrumentResult], MakerRecords]:
        context = contexts[0]
        if context.match_id == 2:
            raise AssertionError("unrelated engine invariant")
        results: list[ReplayInstrumentResult] = [
            {
                "instrument_id": context.instrument_ids[0],
                "fills": 0,
                "pnl": 1.0,
                "fill_events": [],
                "settlement_pnl_applied": True,
                "terminated_early": False,
            },
            {
                "instrument_id": context.instrument_ids[1],
                "fills": 0,
                "pnl": 0.0,
                "fill_events": [],
                "settlement_pnl_applied": True,
                "terminated_early": False,
            },
        ]
        return results, MakerRecords(fills=(), quote_events=(), uptimes=())

    monkeypatch.setattr("backtest.run.create_telonex_source_tree", fake_tree)
    monkeypatch.setattr("backtest.run.run_batch", fake_run_batch)
    first = replace(build_context_for_results(), match_id=1)
    second = replace(build_context_for_results(), match_id=2)
    third = replace(build_context_for_results(), match_id=3)
    with pytest.raises(AssertionError, match="unrelated engine invariant"):
        replay_matches(
            contexts=[first, second, third],
            context_by_match={context.match_id: context for context in (first, second, third)},
            signals={},
            pauses_by_match={},
            mids={},
            report_dir=tmp_path,
            resumed=[],
            resumed_fills=[],
            coverage=None,
            selected=3,
            write_summary=False,
            skip_keys=frozenset[int](),
            manifest={"model_name": "crash", "exit_settle_seconds": 10.0, "fill_model": "queue"},
            capture_root=tmp_path,
            kernels={},
            schedule_archives={},
            provenance={context.match_id: GRID_V1_PROVENANCE for context in (first, second, third)},
            plans={},
            game="dota",
            archives_only=False,
        )
    rows = read_results_checkpoint(tmp_path)
    assert [row.match_id for row in rows] == [1]
    assert rows[0].terminated_early is False


def test_quote_telemetry_read_back_matches_the_in_memory_reference(tmp_path: Path) -> None:
    """Parts round-trip through Arrow with the same counts and reserve rows as the live tape."""
    events = [
        build_quote_event(match_id=1, kind="submitted", reason=""),
        build_quote_event(match_id=1, kind="no_quote", reason="stale_book"),
        build_quote_event(match_id=1, kind="canceled", reason="nw_velocity"),
        build_quote_event(match_id=2, kind="cancel_ack", reason=""),
        build_quote_event(match_id=2, kind="no_quote", reason="stale_book"),
    ]
    buying = [
        replace(event, side="BUY", order_id="B0")
        if event.kind in ("submitted", "cancel_ack")
        else event
        for event in events
    ]
    write_quote_event_parts(report_dir=tmp_path, events=buying, match_ids=[1, 2])
    compact_quote_events(report_dir=tmp_path, match_ids=[1, 2])

    loaded = read_quote_telemetry(tmp_path)
    reference = quote_telemetry_from_events(buying)
    assert sorted(loaded.kind_counts, key=str) == sorted(reference.kind_counts, key=str)
    assert loaded.reserve_events == reference.reserve_events
    assert read_quote_events_checkpoint(tmp_path) == buying


def test_quote_telemetry_of_an_empty_run_is_empty(tmp_path: Path) -> None:
    """A run with no tape reports empty telemetry instead of failing."""
    assert read_quote_telemetry(tmp_path) == empty_quote_telemetry()


def test_resume_refuses_a_checkpoint_without_quote_event_parts(tmp_path: Path) -> None:
    """A pre-parts checkpoint cannot be split per map, so resume must fail closed."""
    (tmp_path / QUOTE_EVENTS_FILENAME).write_bytes(b"")
    with pytest.raises(ValueError, match="predates per-match quote-event parts"):
        assert_quote_event_parts_resumable(tmp_path)


def test_clear_run_artifacts_removes_quote_event_parts(tmp_path: Path) -> None:
    """A fresh run must not compact parts left by a previous run."""
    write_quote_event_parts(
        report_dir=tmp_path, events=[build_quote_event(match_id=1)], match_ids=[1]
    )
    clear_run_artifacts(tmp_path)
    assert not parts_dir(tmp_path).exists()


@pytest.mark.parametrize("incompatible", ["parent", "shard"])
def test_merge_rejects_incompatible_manifest_before_copying(
    tmp_path: Path, incompatible: str
) -> None:
    """A late incompatible shard cannot overwrite parent artifacts or quote parts."""
    expected = {"game": "lol", "league_whitelist_sha256": "same", "selected_matches_sha256": "maps"}
    for index in range(2):
        directory = shard_subdir(tmp_path, ShardSpec(index, 2))
        directory.mkdir()
        manifest = {"game": "lol"} if incompatible == "shard" and index == 1 else expected
        write_manifest(directory, manifest)
        parts = directory / "quote_event_parts"
        parts.mkdir()
        (parts / f"{index}.parquet").write_bytes(b"do not copy")
    if incompatible == "parent":
        write_manifest(tmp_path, {"game": "lol"})
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        merge_shard_checkpoints(
            parent_dir=tmp_path,
            shard_count=2,
            manifest=expected,
            archive=empty_shared_archive(),
        )
    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_source_tree_skips_strip_when_schedule_archive_lacks_core_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bound archive without core_trace links plain books instead of crashing."""
    capture = tmp_path / "capture"
    for token_id in ("111", "222"):
        write_valid_capture_day(capture, token_id, "2026-04-19")
    archive = tmp_path / "grid-no-trace"
    archive.mkdir()
    loads: list[Path] = []

    def fake_load(archive_dir: Path) -> tuple[tuple[int, str, object], ...]:
        loads.append(archive_dir)
        return ()

    monkeypatch.setattr("backtest.telonex_local.load_resting_events", fake_load)
    context = build_market_context(build_sources(), 3)

    with create_telonex_source_tree((context,), capture, {3: archive}) as source_root:
        book = source_root / "polymarket" / "book_snapshot_full" / "demo-market"
        assert (book / "outcome_id=0" / "2026-04-19.parquet").exists()
    assert loads == []


def test_source_tree_strips_when_schedule_archive_has_core_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bound archive with core_trace feeds its resting events to the strip."""
    monkeypatch.setenv("TELONEX_TREE_CACHE", str(tmp_path / "tree-cache"))
    capture = tmp_path / "capture"
    for token_id in ("111", "222"):
        write_valid_capture_day(capture, token_id, "2026-04-19")
    archive = tmp_path / "grid-with-trace"
    archive.mkdir()
    (archive / "core_trace.jsonl").write_text("{}\n", encoding="utf-8")
    loads: list[Path] = []

    def fake_load(archive_dir: Path) -> tuple[tuple[int, str, object], ...]:
        loads.append(archive_dir)
        return ()

    monkeypatch.setattr("backtest.telonex_local.load_resting_events", fake_load)
    context = build_market_context(build_sources(), 3)

    with create_telonex_source_tree((context,), capture, {3: archive}) as source_root:
        assert source_root.is_dir()
    assert loads == [archive]


def test_tree_cache_reuses_a_symlinked_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worktree symlink to the same data dir hits the tree cache and does not rebuild."""
    monkeypatch.setenv("TELONEX_TREE_CACHE", str(tmp_path / "tree-cache"))
    monkeypatch.setenv("TELONEX_CACHE_ROOT", str(tmp_path / "framework-cache"))
    capture = tmp_path / "capture"
    for token_id in ("111", "222"):
        write_valid_capture_day(capture, token_id, "2026-04-19")
    archive = tmp_path / "grid-with-trace"
    archive.mkdir()
    (archive / "core_trace.jsonl").write_text("{}\n", encoding="utf-8")
    loads: list[Path] = []

    def fake_load(archive_dir: Path) -> tuple[tuple[int, str, object], ...]:
        loads.append(archive_dir)
        return ()

    monkeypatch.setattr("backtest.telonex_local.load_resting_events", fake_load)
    context = build_market_context(build_sources(), 3)
    with create_telonex_source_tree((context,), capture, {3: archive}):
        pass
    cached = sorted(path.name for path in (tmp_path / "tree-cache").glob("*.parquet"))
    assert cached
    loads.clear()
    capture_link = tmp_path / "capture-link"
    archive_link = tmp_path / "archive-link"
    capture_link.symlink_to(capture)
    archive_link.symlink_to(archive)
    with create_telonex_source_tree((context,), capture_link, {3: archive_link}):
        pass
    assert loads == []
    assert sorted(path.name for path in (tmp_path / "tree-cache").glob("*.parquet")) == cached
