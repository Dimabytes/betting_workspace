"""Both adapters share one tape until a successful BUY cancel.

That row is the live/backtest split: backtest CancelAck frees the rung, live
CancelUnsettled keeps it gone. The tests below that row check each side alone.
"""

# pyright: reportPrivateUsage=false

import pytest
from adapter_contract_fixtures import (
    BacktestDriver,
    LiveDriver,
    TapeBook,
    TapeCancel,
    TapeEvent,
    TapeFill,
    TapePlaceMissing,
    TapePlaceOk,
    TapePlaceRejected,
    TapeRecovery,
    TapeRecoveryVerified,
    TapeSignal,
    TapeWake,
    core_id,
    open_books,
    open_clock,
    open_signal,
    play_tape,
)

from shared.constants.strategy import ORDER_CANCEL_LATENCY_MS
from shared.utils.match_time import NS_PER_SECOND

SETTLE_NS = 10 * NS_PER_SECOND
CANCEL_LATENCY_NS = round(ORDER_CANCEL_LATENCY_MS * 1_000_000)


def _open(*, now_ns: int = 0) -> tuple[TapeEvent, ...]:
    return (
        open_books(now_ns=now_ns),
        open_signal(now_ns=now_ns),
        open_clock(now_ns=now_ns),
        TapeWake(now_ns=now_ns),
    )


def _play_through_buy_cancel(
    monkeypatch: pytest.MonkeyPatch, tape: tuple[TapeEvent, ...]
) -> tuple[BacktestDriver, LiveDriver]:
    """Parity until the first ok cancel, then each driver alone.

    Live keeps that BUY gone. Backtest frees it. Forcing them to match would
    hide the reserve.
    """
    split = next(
        index for index, event in enumerate(tape) if isinstance(event, TapeCancel) and event.ok
    )
    backtest, live = play_tape(monkeypatch, tape[:split])
    for event in tape[split:]:
        backtest.feed(event)
        live.feed(event)
    return backtest, live


def _live_buy_stays_gone(live: LiveDriver, ref: int) -> None:
    order = next(item for item in live.state.orders if item.order_id == core_id(ref))
    assert order.side == "BUY"
    assert order.status == "gone"


def test_ladder_open(monkeypatch: pytest.MonkeyPatch) -> None:
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeWake(now_ns=NS_PER_SECOND),
    )
    backtest, live = play_tape(monkeypatch, tape)
    assert len([order for order in backtest.state.orders if order.side == "BUY"]) == 3
    assert live.state.next_order_seq == backtest.state.next_order_seq == 3


def test_partial_buy_fill_then_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    fill_ns = 0
    move_ns = NS_PER_SECOND
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeFill(now_ns=fill_ns, ref=0, qty=40.0, price=0.50),
        TapeBook(now_ns=move_ns, bid0=0.47, ask0=0.51, bid1=0.49, ask1=0.53),
        TapeSignal(now_ns=move_ns, delta=0.05, anchor=0.49),
        open_clock(now_ns=move_ns),
        TapeWake(now_ns=move_ns),
        TapeCancel(now_ns=move_ns + CANCEL_LATENCY_NS, ref=0, ok=True),
    )
    backtest, live = _play_through_buy_cancel(monkeypatch, tape)
    _live_buy_stays_gone(live, 0)
    replacement = [
        order
        for order in backtest.state.orders
        if order.side == "BUY" and abs(order.price - 0.47) < 1e-9
    ]
    assert replacement
    assert replacement[-1].submitted_qty == 212.76
    assert backtest.state.episode_buy_notional == 20.0


def test_fill_then_settle_then_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    sell_ns = SETTLE_NS
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeFill(now_ns=0, ref=0, qty=200.0, price=0.50),
        open_books(now_ns=sell_ns),
        open_signal(now_ns=sell_ns),
        open_clock(now_ns=sell_ns),
        TapeWake(now_ns=sell_ns),
    )
    backtest, live = play_tape(monkeypatch, tape)
    sells = [order for order in backtest.state.orders if order.side == "SELL"]
    assert len(sells) == 1
    assert any(order.side == "SELL" for order in live.state.orders)


def test_new_buy_fill_during_settle_cancels_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    sell_ns = SETTLE_NS
    fill_ns = SETTLE_NS + NS_PER_SECOND
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeFill(now_ns=0, ref=0, qty=200.0, price=0.50),
        open_books(now_ns=sell_ns),
        open_signal(now_ns=sell_ns),
        open_clock(now_ns=sell_ns),
        TapeWake(now_ns=sell_ns),
        TapePlaceOk(now_ns=sell_ns, refs=(0, 1)),
        TapeFill(now_ns=fill_ns, ref=1, qty=50.0, price=0.49),
        TapeWake(now_ns=fill_ns),
    )
    backtest, live = play_tape(monkeypatch, tape)
    assert backtest.state.last_buy_ns == fill_ns
    assert live.state.last_buy_ns == fill_ns
    sells = [order for order in backtest.state.orders if order.side == "SELL"]
    assert sells
    assert all(order.status == "canceling" for order in sells)


def test_fill_cancel_race_holds_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    move_ns = NS_PER_SECOND
    fill_ns = move_ns + 40_000_000
    ack_ns = move_ns + CANCEL_LATENCY_NS
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeBook(now_ns=move_ns, bid0=0.47, ask0=0.51, bid1=0.49, ask1=0.53),
        TapeSignal(now_ns=move_ns, delta=0.05, anchor=0.49),
        open_clock(now_ns=move_ns),
        TapeWake(now_ns=move_ns),
        TapeFill(now_ns=fill_ns, ref=0, qty=5.0, price=0.50),
        TapeCancel(now_ns=ack_ns, ref=0, ok=True),
    )
    backtest, live = _play_through_buy_cancel(monkeypatch, tape)
    _live_buy_stays_gone(live, 0)
    assert backtest.state.position.qty == 5.0
    assert live.state.position.qty == 5.0
    assert backtest.state.has_buy_fill is True


def test_rung_move_keeps_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    move_ns = NS_PER_SECOND
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeFill(now_ns=0, ref=0, qty=200.0, price=0.50),
        TapeBook(now_ns=move_ns, bid0=0.49, ask0=0.51, bid1=0.49, ask1=0.51),
        TapeSignal(now_ns=move_ns, delta=0.05, anchor=0.50),
        open_clock(now_ns=move_ns),
        TapeWake(now_ns=move_ns),
    )
    backtest, live = play_tape(monkeypatch, tape)
    assert backtest.state.next_order_seq == live.state.next_order_seq
    moved = [order for order in backtest.state.orders if order.order_id == core_id(2)]
    assert moved
    assert moved[0].level_index == 1


def test_cancel_timeout_keeps_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    move_ns = NS_PER_SECOND
    timeout_ns = move_ns + CANCEL_LATENCY_NS
    wake_ns = move_ns + NS_PER_SECOND
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeBook(now_ns=move_ns, bid0=0.47, ask0=0.51, bid1=0.49, ask1=0.53),
        TapeSignal(now_ns=move_ns, delta=0.05, anchor=0.49),
        open_clock(now_ns=move_ns),
        TapeWake(now_ns=move_ns),
        TapeCancel(now_ns=timeout_ns, ref=0, ok=False),
        TapeWake(now_ns=wake_ns),
    )
    backtest, live = play_tape(monkeypatch, tape)
    assert any(order.order_id == core_id(0) for order in backtest.state.orders)
    assert any(order.order_id == core_id(0) for order in live.state.orders)
    held = next(order for order in live.state.orders if order.order_id == core_id(0))
    assert held.status == "unknown"


def test_submit_unknown_holds_middle_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    tape = (
        *_open(),
        TapePlaceMissing(now_ns=0, refs=(1,)),
        TapePlaceOk(now_ns=0, refs=(0, 2)),
    )
    backtest, live = play_tape(monkeypatch, tape)
    middle = next(order for order in backtest.state.orders if order.order_id == core_id(1))
    assert middle.status == "unknown"
    assert any(
        order.order_id == core_id(1) and order.status == "unknown" for order in live.state.orders
    )
    siblings = [
        order for order in backtest.state.orders if order.order_id in (core_id(0), core_id(2))
    ]
    assert len(siblings) == 2
    assert all(order.status == "live" for order in siblings)


def test_place_rejected_frees_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    tape = (
        *_open(),
        TapePlaceRejected(now_ns=0, refs=(1,)),
        TapePlaceOk(now_ns=0, refs=(0, 2)),
        TapeWake(now_ns=NS_PER_SECOND),
    )
    backtest, live = play_tape(monkeypatch, tape)
    assert all(order.order_id != core_id(1) for order in backtest.state.orders)
    assert all(order.order_id != core_id(1) for order in live.state.orders)


def test_mixed_buy_sell_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    sell_ns = SETTLE_NS
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeFill(now_ns=0, ref=0, qty=200.0, price=0.50),
        TapeBook(now_ns=sell_ns, bid0=0.51, ask0=0.53, bid1=0.47, ask1=0.49),
        TapeSignal(now_ns=sell_ns, delta=0.05, anchor=0.52),
        open_clock(now_ns=sell_ns),
        TapeWake(now_ns=sell_ns),
        TapeCancel(now_ns=sell_ns + CANCEL_LATENCY_NS, ref=2, ok=True),
    )
    backtest, live = _play_through_buy_cancel(monkeypatch, tape)
    _live_buy_stays_gone(live, 2)
    places = [order for order in backtest.state.orders if order.status == "pending"]
    sides = [order.side for order in places]
    assert "SELL" in sides
    assert "BUY" in sides


def test_matched_then_confirmed_credits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeFill(now_ns=0, ref=0, qty=40.0, price=0.50),
        TapeFill(now_ns=0, ref=0, qty=40.0, price=0.50),
    )
    backtest, live = play_tape(monkeypatch, tape)
    assert backtest.state.position.qty == 40.0
    assert live.state.position.qty == 40.0
    assert len(backtest.state.seen_fill_ids) == 1
    assert len(live.state.seen_fill_ids) == 1


def test_recovery_unwinds_to_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeFill(now_ns=0, ref=0, qty=40.0, price=0.50),
        TapeRecovery(now_ns=NS_PER_SECOND),
    )
    backtest, live = play_tape(monkeypatch, tape)
    assert backtest.state.sell_only is True
    assert live.state.sell_only is True
    assert backtest.state.recovery_pending is True
    assert live.state.recovery_pending is True
    buys = [order for order in backtest.state.orders if order.side == "BUY"]
    assert buys
    assert all(order.status == "canceling" for order in buys)


def test_recovery_verified_exit_then_flat(monkeypatch: pytest.MonkeyPatch) -> None:
    settle_ns = 11 * NS_PER_SECOND
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
        TapeCancel(now_ns=NS_PER_SECOND, ref=0, ok=False),
        TapeFill(now_ns=NS_PER_SECOND, ref=0, qty=40.0, price=0.50),
        TapeRecovery(now_ns=NS_PER_SECOND),
        TapeCancel(now_ns=2 * NS_PER_SECOND, ref=1, ok=True),
        TapeCancel(now_ns=2 * NS_PER_SECOND, ref=2, ok=True),
        TapeCancel(now_ns=2 * NS_PER_SECOND, ref=0, ok=True),
        TapeRecoveryVerified(
            now_ns=3 * NS_PER_SECOND,
            generation=1,
            verified_qty=40.0,
            verified_token_index=0,
            last_buy_ns=NS_PER_SECOND,
        ),
        open_books(now_ns=settle_ns),
        TapeWake(now_ns=settle_ns),
        TapePlaceOk(now_ns=settle_ns, refs=(0,)),
        TapeFill(now_ns=settle_ns, ref=3, qty=40.0, price=0.52),
        TapeRecoveryVerified(
            now_ns=settle_ns,
            generation=2,
            verified_qty=0.0,
            verified_token_index=None,
            last_buy_ns=None,
        ),
        TapeWake(now_ns=settle_ns + NS_PER_SECOND),
    )
    backtest, live = _play_through_buy_cancel(monkeypatch, tape)
    _live_buy_stays_gone(live, 1)
    _live_buy_stays_gone(live, 2)
    assert backtest.state.sell_only is False
    assert live.state.sell_only is False
    assert backtest.state.recovery_pending is False
    assert live.state.recovery_pending is False
    assert backtest.state.position.qty == 0.0
    assert live.state.position.qty == 0.0
    # Re-anchor lets a still-fresh signal reopen a ladder episode between ticks.
    assert [order.price for order in backtest.state.orders if order.side == "BUY"] == [
        0.5,
        0.49,
        0.48,
    ]
    assert not any(
        order.side == "SELL" and order.status == "pending" for order in backtest.state.orders
    )


def test_short_budget_skips_whole_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    tape = (
        *_open(),
        TapePlaceOk(now_ns=0, refs=(0, 1)),
        TapeWake(now_ns=NS_PER_SECOND),
    )
    backtest, live = play_tape(monkeypatch, tape, cash=220.0)
    buys = [order for order in backtest.state.orders if order.side == "BUY"]
    assert len(buys) == 2
    assert len([order for order in live.state.orders if order.side == "BUY"]) == 2
    assert tuple(rung.price for rung in backtest.state.rungs) == (0.50, 0.49, 0.48)
    assert backtest.state.rungs[2].live_id is None
    assert live.state.rungs[2].live_id is None
    assert live._core.last_block == "no_cash"
