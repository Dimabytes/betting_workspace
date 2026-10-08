"""Unit tests for maker telemetry isolation and fill predicted_delta."""

from dataclasses import asdict

import pytest

from backtest.telemetry import (
    FillRecord,
    QuoteEvent,
    UptimeRecord,
    clear_records,
    record_fill,
    record_quote_event,
    record_uptime,
    take_records,
)


def build_fill(*, match_id: int, predicted_delta: float) -> FillRecord:
    """Minimal fill carrying the fields the recorder round-trips."""
    return FillRecord(
        match_id=match_id,
        token_index=0,
        side="BUY",
        price=0.5,
        quantity=5.0,
        submitted_quantity=5.0,
        ts_ns=1,
        predicted_delta=predicted_delta,
        fair=0.55,
        book_p_radiant=0.5,
        dataset_market_p=0.5,
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
    )


def test_predicted_delta_present_on_every_fill_row() -> None:
    """Every harvested fill keeps its predicted_delta."""
    clear_records()
    record_fill(build_fill(match_id=1, predicted_delta=0.012))
    record_fill(build_fill(match_id=1, predicted_delta=-0.004))

    records = take_records()

    assert len(records.fills) == 2
    assert all(isinstance(fill.predicted_delta, float) for fill in records.fills)
    assert [fill.predicted_delta for fill in records.fills] == [0.012, -0.004]


def test_clear_records_isolates_arms_and_batches() -> None:
    """clear_records drops prior arm/batch events so nothing leaks forward."""
    clear_records()
    record_fill(build_fill(match_id=1, predicted_delta=0.01))
    record_quote_event(
        QuoteEvent(
            match_id=1,
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
        )
    )

    clear_records()
    assert take_records().fills == ()
    assert take_records().quote_events == ()

    record_fill(build_fill(match_id=2, predicted_delta=0.02))
    second = take_records()
    assert [fill.match_id for fill in second.fills] == [2]
    assert second.quote_events == ()


def test_fill_builder_stamps_order_id() -> None:
    """Fill records carry order_id so fill rate can count distinct orders."""
    fill = build_fill(match_id=1, predicted_delta=0.01)
    assert fill.order_id == "O-1"


def test_fill_record_requires_submitted_quantity() -> None:
    """Incomplete-order accounting needs the live order size on every fill."""
    fields = asdict(build_fill(match_id=1, predicted_delta=0.01))
    del fields["submitted_quantity"]
    with pytest.raises(TypeError, match="submitted_quantity"):
        FillRecord(**fields)


def test_uptime_isolation_across_clear_records() -> None:
    """clear_records drops uptime so a later arm cannot inherit the previous count."""
    clear_records()
    record_uptime(UptimeRecord(match_id=1, live_order_seconds=3))
    clear_records()
    assert take_records().uptimes == ()
    record_uptime(UptimeRecord(match_id=2, live_order_seconds=1))
    second = take_records()
    assert second.uptimes == (UptimeRecord(match_id=2, live_order_seconds=1),)
