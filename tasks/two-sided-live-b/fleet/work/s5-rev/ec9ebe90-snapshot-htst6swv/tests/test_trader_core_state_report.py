"""Unit cases for the stuck-order rule behind the core state report."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from dataclasses import replace

from strategy.lifecycle import empty_state
from strategy.types import (
    Budget,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    OrderStatus,
    Permissions,
    RestingOrder,
    Side,
    StrategyState,
)
from trader.core_state_report import find_stuck_orders
from trader.replay_core_trace import IdleSince, _keep, _Replay, _rewind, _sync_idle

NS = 1_000_000_000
NOW_NS = 600 * NS


def _state(orders: tuple[RestingOrder, ...]) -> StrategyState:
    base = empty_state(
        limits=MarketLimits(
            min_order_size=5.0,
            tick_size=0.01,
            radiant_token_index=0,
            pair_sum_tolerance=0.02,
        ),
        freshness=FreshnessLimits(entry_stale_s=8.0, exit_stale_s=30.0, book_stale_s=5.0),
        permissions=Permissions(
            halt=False,
            reduce_only=False,
            allow_buy=True,
            allow_sell=True,
            sell_unconfirmed=False,
        ),
        budget=Budget(
            cash_usdc=1000.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        clock=GameClock(now_ns=NOW_NS, game_second=600, paused=False, game_ended=False),
    )
    return replace(base, orders=orders)


def _order(
    *, order_id: str, side: Side, status: OrderStatus, placed_ns: int, level_index: int | None
) -> RestingOrder:
    return RestingOrder(
        order_id=order_id,
        episode_id=1,
        token_index=1,
        side=side,
        price=0.6,
        submitted_qty=100.0,
        filled_qty=0.0,
        level_index=level_index,
        status=status,
        accepted=status != "pending",
        partially_filled=False,
        cancel_reason="reprice" if status == "canceling" else "",
        ack_reason="",
        placed_ns=placed_ns,
        accepted_ns=None,
    )


def test_a_live_order_and_a_fresh_one_are_not_stuck() -> None:
    """Resting is normal, and a place answered in under the window is normal."""
    state = _state(
        (
            _order(order_id="c1", side="SELL", status="live", placed_ns=0, level_index=None),
            _order(
                order_id="c2",
                side="BUY",
                status="pending",
                placed_ns=NOW_NS - NS,
                level_index=0,
            ),
        )
    )
    assert (
        find_stuck_orders(
            state=state,
            now_ns=NOW_NS,
            idle=(IdleSince(order_id="c2", since_ns=NOW_NS - NS),),
            max_idle_s=30.0,
        )
        == ()
    )


def test_a_canceling_sell_and_a_pending_buy_are_stuck_with_the_slot_they_hold() -> None:
    """Both wedge shapes surface: the lost cancel ack and the place that never landed."""
    state = _state(
        (
            _order(
                order_id="c33",
                side="SELL",
                status="canceling",
                placed_ns=NOW_NS - 300 * NS,
                level_index=None,
            ),
            _order(
                order_id="c40",
                side="BUY",
                status="pending",
                placed_ns=NOW_NS - 120 * NS,
                level_index=2,
            ),
        )
    )
    stuck = find_stuck_orders(
        state=state,
        now_ns=NOW_NS,
        idle=(
            IdleSince(order_id="c33", since_ns=NOW_NS - 300 * NS),
            IdleSince(order_id="c40", since_ns=NOW_NS - 120 * NS),
        ),
        max_idle_s=30.0,
    )
    assert [order.order_id for order in stuck] == ["c33", "c40"]
    assert [order.holds for order in stuck] == ["exit slot", "rung 2"]
    assert stuck[0].age_s == 300.0


def test_a_long_resting_sell_that_just_left_live_is_not_stuck() -> None:
    """`placed_ns` is the place time. The wedge clock starts when status leaves `live`."""
    state = _state(
        (
            _order(
                order_id="c1",
                side="SELL",
                status="canceling",
                placed_ns=0,
                level_index=None,
            ),
        )
    )
    assert (
        find_stuck_orders(
            state=state,
            now_ns=NOW_NS,
            idle=(IdleSince(order_id="c1", since_ns=NOW_NS - NS),),
            max_idle_s=30.0,
        )
        == ()
    )


def test_idle_clock_starts_outside_live_and_revert_puts_it_back() -> None:
    """A later sync does not move the mark, and a revert restores the earlier one."""
    sell = _order(order_id="c1", side="SELL", status="canceling", placed_ns=0, level_index=None)
    replay = _Replay(state=_state((sell,)))
    _sync_idle(replay, 100)
    _keep(replay, 1)
    assert replay.idle_since == {"c1": 100}
    _sync_idle(replay, 400)
    assert replay.idle_since == {"c1": 100}
    replay.state = _state(())
    _sync_idle(replay, 500)
    _keep(replay, 2)
    assert replay.idle_since == {}
    _rewind(replay, to_seq=1, seq=3)
    assert replay.idle_since == {"c1": 100}
    live = _order(order_id="c1", side="SELL", status="live", placed_ns=0, level_index=None)
    replay.state = _state((live,))
    _sync_idle(replay, 600)
    assert replay.idle_since == {}
