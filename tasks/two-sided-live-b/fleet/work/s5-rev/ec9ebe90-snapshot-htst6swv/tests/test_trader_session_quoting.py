"""Tests for live book/window helpers kept after Follow300 moved into src/strategy/."""

# pyright: reportPrivateUsage=false

import time
from dataclasses import replace
from pathlib import Path

import pytest
from trader_session_fixtures import (
    YES_TOKEN,
    FakeModelServer,
    build_attached_worker,
    build_event,
)

from shared.constants.strategy import (
    EXIT_FEED_STALE_SECONDS,
    GRID_FEED_STALE_SECONDS,
)
from shared.utils.trading import share_floor
from strategy.types import BookPair, BookUpdate, RestingOrder, TokenBook, Wake
from trader.live_feed import FeedEvent, MatchPhase
from trader.match_worker import MatchWorker
from trader.session_core import core_now_ns
from trader.session_engine import StrategyCell
from trader.session_quoting import (
    in_model_window,
    pin_place_tick,
    window_reason,
)
from trader.session_types import SignalDecision, SignalReason


def test_feed_stale_thresholds() -> None:
    """GRID entry 16s, v5 SELL pull 45s."""
    assert GRID_FEED_STALE_SECONDS == 16.0
    assert EXIT_FEED_STALE_SECONDS == 45.0


def test_strategy_cell_none_plus_resume_exit() -> None:
    """No model snapshot can still carry resume_exit for the emergency SELL path."""
    cell = StrategyCell()
    cell.resume_exit = True
    cell.publish(None)
    assert cell.yes_fair is None
    assert cell.forced is True
    assert cell.yes_fair is None
    assert cell.resume_exit is True


def test_strategy_cell_clear_keeps_resume_exit() -> None:
    """clear() drops the model and leaves the resume_exit flag in place."""
    cell = StrategyCell()
    cell.resume_exit = True
    cell.publish(0.55)
    cell.clear()
    assert cell.yes_fair is None
    assert cell.resume_exit is True


def test_pin_place_tick_keeps_clob_grid() -> None:
    """A millitick sidecar does not shrink the CLOB place tick; a 0.01 sidecar does apply."""
    assert pin_place_tick(0.001, 0.01) == 0.01
    assert pin_place_tick(0.01, 0.001) == 0.01
    assert pin_place_tick(0.01, 0.01) == 0.01


def test_share_floor_does_not_leave_a_0_01_remainder() -> None:
    assert share_floor(10.019) == 10.01
    assert share_floor(10.001) == 10.0


@pytest.mark.parametrize(
    ("phase", "second", "expected"),
    [
        (MatchPhase.PRE_HORN, -61, SignalReason.PRE_HORN),
        (MatchPhase.PRE_HORN, -60, None),
        (MatchPhase.PRE_HORN, -1, None),
        (MatchPhase.PRE_HORN, 0, SignalReason.PRE_HORN),
        (MatchPhase.IN_PROGRESS, -1, SignalReason.PRE_HORN),
        (MatchPhase.IN_PROGRESS, 0, None),
        (MatchPhase.IN_PROGRESS, 899, None),
        (MatchPhase.IN_PROGRESS, 900, None),
        (MatchPhase.PRE_MATCH, -61, SignalReason.PRE_HORN),
        (MatchPhase.PRE_MATCH, -60, SignalReason.PRE_HORN),
        (MatchPhase.PRE_MATCH, -1, SignalReason.PRE_HORN),
        (MatchPhase.PRE_MATCH, 0, SignalReason.PRE_HORN),
        (MatchPhase.PRE_MATCH, 899, SignalReason.PRE_HORN),
        (MatchPhase.PRE_MATCH, 900, SignalReason.PRE_HORN),
        (MatchPhase.FINISHED, 100, SignalReason.FINISHED),
    ],
)
def test_window_reason_quotes_phase_and_second_pairs(
    phase: MatchPhase, second: int, expected: SignalReason | None
) -> None:
    """PRE_HORN is -60..-1; IN_PROGRESS is every nonnegative second until finished."""
    event = build_event(second, phase=phase)
    assert window_reason(event.snapshot) is expected


def test_in_progress_model_window_has_no_clock_end() -> None:
    """Model inference continues through every non-finished in-progress frame."""
    event = build_event(7201, phase=MatchPhase.IN_PROGRESS)
    assert in_model_window(event.snapshot) is True
    assert window_reason(event.snapshot) is None


def _ready_books(worker: MatchWorker) -> None:
    """Mark both token books ready for this attach generation."""
    readiness = worker._host.readiness
    readiness.attach_token(YES_TOKEN)
    readiness.note_snapshot(YES_TOKEN)
    readiness.attach_token(worker._no)
    readiness.note_snapshot(worker._no)


def _decision_for(worker: MatchWorker, event: FeedEvent, arrived_at: float) -> SignalDecision:
    """Run the gate/latch/assemble pipeline without publish, enqueue, or journal."""
    gated = worker._gate_pair(window_reason(event.snapshot))
    latched = worker._latch_fair(event.snapshot, gated.market_p_radiant, gated.reason)
    reason = latched.reason if latched.reason is not None else SignalReason.MODEL
    return worker._compute_decision(event, arrived_at, gated, latched, reason)


@pytest.mark.parametrize("game", ["dota", "lol"])
def test_after_899_still_has_fair(
    tmp_path: Path, request: pytest.FixtureRequest, game: str
) -> None:
    """IN_PROGRESS at 1200 still publishes a model fair."""
    worker, _fake = build_attached_worker(
        tmp_path, request, model=FakeModelServer(radiant_fair=0.60)
    )
    worker._discovered = replace(worker._discovered, game=game)
    _ready_books(worker)
    event = build_event(1200, phase=MatchPhase.IN_PROGRESS)
    assert window_reason(event.snapshot) is None
    decision = _decision_for(worker, event, 1_700_000_000.0)
    assert decision.yes_fair == pytest.approx(0.60)


def _accepted_bid(price: float, qty: float) -> RestingOrder:
    """A live BUY already inside the book snapshot."""
    return RestingOrder(
        order_id="own-bid",
        episode_id=1,
        token_index=0,
        side="BUY",
        price=price,
        submitted_qty=qty,
        filled_qty=0.0,
        level_index=0,
        status="live",
        accepted=True,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=0,
        accepted_ns=-1_000_000_000_000,
    )


def _replace_books(
    worker: MatchWorker,
    *,
    yes_bids: list[tuple[float, float]],
    yes_asks: list[tuple[float, float]],
    no_bids: list[tuple[float, float]],
    no_asks: list[tuple[float, float]],
) -> None:
    """Overwrite both token books and mark them ready for this attach."""
    yes = worker._host.engine.md.book(worker._yes)
    no = worker._host.engine.md.book(worker._no)
    assert yes is not None and no is not None
    yes.apply_snapshot(yes_bids, yes_asks, 100.0, "hash")
    no.apply_snapshot(no_bids, no_asks, 100.0, "hash")
    _ready_books(worker)


def _token_book(index: int, bid: float, ask: float, ts_ns: int) -> TokenBook:
    return TokenBook(
        token_index=index,
        bid=bid,
        ask=ask,
        bid_size=20.0,
        ask_size=20.0,
        ts_ns=ts_ns,
    )


def test_anchor_uses_stripped_mid(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Own top bid moves the raw mid by more than 1c; the anchor still sees the stripped mid.

    Raw p is 0.56/1.02. Stripped p is 0.51/0.97. The fixture worker is paper, which
    does not strip, so this test switches the mode the live quoter uses.
    """
    worker, _fake = build_attached_worker(tmp_path, request)
    worker._mode = "live"
    _replace_books(
        worker,
        yes_bids=[(0.55, 20.0), (0.45, 20.0)],
        yes_asks=[(0.57, 20.0)],
        no_bids=[(0.45, 20.0)],
        no_asks=[(0.47, 20.0)],
    )
    core = worker.core
    assert core is not None
    core._state = replace(core.state, orders=(_accepted_bid(0.55, 20.0),))
    now_ns = core_now_ns()
    stripped = BookPair(
        tokens=(
            _token_book(0, 0.45, 0.57, now_ns),
            _token_book(1, 0.45, 0.47, now_ns),
        )
    )
    core.apply(BookUpdate(now_ns=now_ns, books=stripped))
    event = build_event(120, phase=MatchPhase.IN_PROGRESS)
    decision = _decision_for(worker, event, time.time())
    raw_p = 0.56 / 1.02
    stripped_p = 0.51 / 0.97
    assert abs(raw_p - stripped_p) > 0.01
    assert decision.market_p_radiant == pytest.approx(stripped_p)
    worker._enqueue_core_signal(event, decision.reason, decision.market_p_radiant, now_ns, now_ns)
    core.drain_apply()
    core.apply(Wake(now_ns=now_ns, forced=True))
    latch = core.state.latch
    assert latch is not None
    assert latch.no_buy_reason == ""
    assert latch.fair_valid is True


def test_own_order_is_the_only_bid_is_own_liquidity(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A bid that is only our order is own liquidity, not a missing book."""
    worker, _fake = build_attached_worker(tmp_path, request)
    worker._mode = "live"
    _replace_books(
        worker,
        yes_bids=[(0.50, 20.0)],
        yes_asks=[(0.55, 20.0)],
        no_bids=[(0.44, 20.0)],
        no_asks=[(0.46, 20.0)],
    )
    core = worker.core
    assert core is not None
    core._state = replace(core.state, orders=(_accepted_bid(0.50, 20.0),))
    decision = _decision_for(worker, build_event(120), time.time())
    assert decision.raw.yes_best_bid == pytest.approx(0.50)
    assert decision.reason is SignalReason.OWN_LIQUIDITY_ONLY
    assert decision.market_p_radiant is None
