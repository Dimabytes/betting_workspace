"""Extraction-oracle goldens: four seed-0 maps plus three compact harness scenarios."""

# pyright: reportPrivateUsage=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownParameterType=false
# pyright: reportAttributeAccessIssue=false

from decimal import Decimal
from pathlib import Path

import pytest
from test_backtest_maker import (
    DIRE_ID,
    RADIANT_ID,
    _FakeBook,
    accept_all,
    build_maker_config,
    build_maker_harness,
    buy_orders,
    fill_order,
    fire_cancel_release,
)
from test_backtest_telemetry import build_fill

from backtest.extraction_identity import (
    PLACE_CANCEL_KINDS,
    ReplayChecks,
    ReplayIdentity,
    first_identity_mismatch,
    first_tape_mismatch,
    identity_from_records,
    load_identity_golden,
    project_place_cancel,
)
from backtest.telemetry import QuoteEvent, take_records
from shared.utils.match_time import NS_PER_SECOND

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "extraction_oracle"

MAP_GOLDENS = (
    ("dota", 8837869969, 6),
    ("dota", 8911784562, 14),
    ("dota", 8933879286, 26),
    ("lol", 115564793879469302, 3),
)

COMPACT_SCENARIOS = (
    "partial_buy_fill",
    "rung_move_keeps_queue",
    "fill_cancel_race",
)


def _map_golden_path(game: str, match_id: int) -> Path:
    return FIXTURES / f"{game}_{match_id}_seed0.json"


def _compact_golden_path(scenario: str) -> Path:
    return FIXTURES / f"compact_{scenario}.json"


def _identity_from_harness(*, scenario: str) -> ReplayIdentity:
    records = take_records()
    return identity_from_records(
        match_id=1,
        game="dota",
        seed=0,
        scenario=scenario,
        checks=ReplayChecks(
            engine_pnl=0.0,
            cash_flow=0.0,
            buy_fills=sum(1 for fill in records.fills if fill.side == "BUY"),
            sell_fills=sum(1 for fill in records.fills if fill.side == "SELL"),
            orders_submitted=sum(1 for event in records.quote_events if event.kind == "submitted"),
            orders_canceled=sum(1 for event in records.quote_events if event.kind == "canceled"),
        ),
        fills=records.fills,
        quote_events=records.quote_events,
    )


def run_partial_buy_fill(monkeypatch: pytest.MonkeyPatch) -> ReplayIdentity:
    """Partial BUY fill on the middle rung; the level stays open."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    _top, mid, _bottom = buy_orders(submitted)
    accept_all(strategy)
    fill_order(strategy, mid, Decimal("5"), 0.47, is_buy=True, ts_event=0)
    return _identity_from_harness(scenario="partial_buy_fill")


def run_rung_move_keeps_queue(monkeypatch: pytest.MonkeyPatch) -> ReplayIdentity:
    """After the top fill, a resting lower rung keeps its queue on the follow move."""
    strategy, submitted, _ = build_maker_harness(monkeypatch)
    strategy._evaluate(now_ns=0)
    top, _mid, bottom = buy_orders(submitted)
    accept_all(strategy)
    fill_order(strategy, top, top.quantity.as_decimal(), 0.48, is_buy=True, ts_event=0)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.50)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    assert str(bottom.client_order_id) in strategy._live
    assert strategy._live[str(bottom.client_order_id)].awaiting_cancel is False
    return _identity_from_harness(scenario="rung_move_keeps_queue")


def run_fill_cancel_race(monkeypatch: pytest.MonkeyPatch) -> ReplayIdentity:
    """A BUY fill lands after cancel was requested and before cancel is released."""
    strategy, submitted, _ = build_maker_harness(
        monkeypatch,
        config=build_maker_config(
            signal_timestamps_ns=(0, NS_PER_SECOND),
            predicted_deltas=(0.05, 0.05),
            dataset_market_ps=(0.50, 0.50),
        ),
    )
    strategy._evaluate(now_ns=0)
    top = buy_orders(submitted)[0]
    oid = str(top.client_order_id)
    strategy._books[RADIANT_ID] = _FakeBook(bid=0.47, ask=0.51)
    strategy._book_ts_ns[RADIANT_ID] = NS_PER_SECOND
    strategy._book_ts_ns[DIRE_ID] = NS_PER_SECOND
    strategy._evaluate(now_ns=NS_PER_SECOND)
    fill_order(strategy, top, Decimal("5"), 0.48, is_buy=True, ts_event=NS_PER_SECOND + 40_000_000)
    fire_cancel_release(strategy, NS_PER_SECOND + 85_000_000, client_order_id=oid)
    return _identity_from_harness(scenario="fill_cancel_race")


SCENARIO_RUNNERS = {
    "partial_buy_fill": run_partial_buy_fill,
    "rung_move_keeps_queue": run_rung_move_keeps_queue,
    "fill_cancel_race": run_fill_cancel_race,
}


@pytest.mark.parametrize(("game", "match_id", "fill_count"), MAP_GOLDENS)
def test_seed0_map_goldens_pin_extraction_identity(
    game: str, match_id: int, fill_count: int
) -> None:
    """Checked-in map goldens carry fills, place/cancel, episode/level, and reserves."""
    identity = load_identity_golden(_map_golden_path(game, match_id))
    assert identity.match_id == match_id
    assert identity.game == game
    assert identity.seed == 0
    assert identity.scenario == "seed0_postcleanup"
    assert len(identity.fills) == fill_count
    assert identity.place_cancel
    assert {event.kind for event in identity.place_cancel} <= PLACE_CANCEL_KINDS
    assert any(event.kind == "submitted" for event in identity.place_cancel)
    assert any(
        event.kind in {"cancel_request", "canceled", "cancel_ack"}
        for event in identity.place_cancel
    )
    for fill in identity.fills:
        assert fill.episode_id >= 0
        assert fill.order_id
        assert fill.ts_ns > 0
        assert fill.reserved_buy_notional >= 0.0
    for event in identity.place_cancel:
        assert event.ts_ns >= 0
        assert event.reserved_buy_notional >= 0.0
        assert event.episode_id >= 0


@pytest.mark.parametrize("scenario", COMPACT_SCENARIOS)
def test_compact_scenario_matches_golden(monkeypatch: pytest.MonkeyPatch, scenario: str) -> None:
    """Harness tapes stay pinned for partial fill, keep-queue reattach, and fill/cancel race."""
    actual = SCENARIO_RUNNERS[scenario](monkeypatch)
    expected = load_identity_golden(_compact_golden_path(scenario))
    mismatch = first_identity_mismatch(actual, expected)
    assert mismatch == "", mismatch


def test_no_quote_is_not_extraction_identity() -> None:
    """1 Hz no_quote rows are cadence noise, not place/cancel identity."""
    event = QuoteEvent(
        match_id=1,
        ts_ns=1,
        kind="no_quote",
        token_index=-1,
        side="",
        price=0.0,
        reason="stale_signal",
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
    )
    assert project_place_cancel(event) is None
    assert "no_quote" not in PLACE_CANCEL_KINDS


def test_first_identity_mismatch_names_the_fill() -> None:
    """A drifted fill points at fills[i], not a blob equality dump."""
    zero = ReplayChecks(
        engine_pnl=0.0,
        cash_flow=0.0,
        buy_fills=1,
        sell_fills=0,
        orders_submitted=0,
        orders_canceled=0,
    )
    actual = identity_from_records(
        match_id=1,
        game="dota",
        seed=0,
        scenario="x",
        checks=zero,
        fills=(build_fill(match_id=1, predicted_delta=0.01),),
        quote_events=(),
    )
    assert first_identity_mismatch(actual, actual) == ""
    other = identity_from_records(
        match_id=1,
        game="dota",
        seed=0,
        scenario="x",
        checks=zero,
        fills=(build_fill(match_id=2, predicted_delta=0.01),),
        quote_events=(),
    )
    assert first_identity_mismatch(actual, other).startswith("fills[0]")


# Current-policy replay vs US-001 goldens is US-008 labeled drift. Historical
# files stay pinned by test_seed0_map_goldens_pin_extraction_identity. Current
# tapes live in tests/test_follow300_replay.py.


def test_first_tape_mismatch_skips_checks() -> None:
    """PnL drift is not a tape mismatch."""
    zero = ReplayChecks(
        engine_pnl=0.0,
        cash_flow=0.0,
        buy_fills=0,
        sell_fills=0,
        orders_submitted=0,
        orders_canceled=0,
    )
    drifted = ReplayChecks(
        engine_pnl=1.0,
        cash_flow=1.0,
        buy_fills=0,
        sell_fills=0,
        orders_submitted=0,
        orders_canceled=0,
    )
    actual = identity_from_records(
        match_id=1,
        game="dota",
        seed=0,
        scenario="x",
        checks=zero,
        fills=(),
        quote_events=(),
    )
    expected = identity_from_records(
        match_id=1,
        game="dota",
        seed=0,
        scenario="x",
        checks=drifted,
        fills=(),
        quote_events=(),
    )
    assert first_tape_mismatch(actual, expected) == ""
    assert first_identity_mismatch(actual, expected).startswith("checks")
