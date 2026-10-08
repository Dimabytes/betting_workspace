"""Mid-spike cooloff cancels BUYs but keeps resting SELLs."""

# pyright: reportPrivateUsage=false

from dataclasses import replace

from strategy.engine import step
from strategy.lifecycle import empty_state
from strategy.mid_spike import observe_mid_spike
from strategy.policy import follow300_policy
from strategy.types import (
    BookPair,
    Budget,
    FreshnessLimits,
    GameClock,
    Latch,
    MarketLimits,
    Permissions,
    RawDeltaSignal,
    RestingOrder,
    StrategyState,
    TokenBook,
    TokenInventory,
    Wake,
)

NS = 1_000_000_000


def _idle(*, now_ns: int = 0) -> StrategyState:
    return empty_state(
        limits=MarketLimits(
            min_order_size=5.0,
            tick_size=0.01,
            pair_sum_tolerance=0.02,
            radiant_token_index=0,
        ),
        freshness=FreshnessLimits(book_stale_s=30.0, entry_stale_s=30.0, exit_stale_s=45.0),
        permissions=Permissions(
            halt=False, reduce_only=False, allow_buy=True, allow_sell=True, sell_unconfirmed=False
        ),
        budget=Budget(
            cash_usdc=10_000.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        clock=GameClock(now_ns=now_ns, game_second=100, paused=False, game_ended=False),
    )


def _books(*, now_ns: int, mid: float) -> BookPair:
    half = 0.01
    bid = mid - half
    ask = mid + half
    return BookPair(
        tokens=(
            TokenBook(
                token_index=0,
                bid=bid,
                ask=ask,
                bid_size=100.0,
                ask_size=100.0,
                ts_ns=now_ns,
            ),
            TokenBook(
                token_index=1,
                bid=1.0 - ask,
                ask=1.0 - bid,
                bid_size=100.0,
                ask_size=100.0,
                ts_ns=now_ns,
            ),
        )
    )


def _policy():
    return follow300_policy(level_usdc=100.0, debounce_ms=0, fallback_timer_s=2.0)


def _sell_order(*, now_ns: int, token_index: int = 0) -> RestingOrder:
    return RestingOrder(
        order_id="sell-1",
        episode_id=1,
        token_index=token_index,
        side="SELL",
        price=0.55,
        submitted_qty=100.0,
        filled_qty=0.0,
        level_index=None,
        status="live",
        accepted=True,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=now_ns,
        accepted_ns=now_ns,
    )


def _buy_order(*, now_ns: int, token_index: int = 0) -> RestingOrder:
    return RestingOrder(
        order_id="buy-1",
        episode_id=1,
        token_index=token_index,
        side="BUY",
        price=0.48,
        submitted_qty=100.0,
        filled_qty=0.0,
        level_index=0,
        status="live",
        accepted=True,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=now_ns,
        accepted_ns=now_ns,
    )


def _holding(state: StrategyState, *, token_index: int, qty: float = 100.0) -> StrategyState:
    inventory = list(state.inventory)
    inventory[token_index] = TokenInventory(
        token_index=token_index, qty=qty, cost_basis=qty * 0.5, last_buy_ns=0
    )
    return replace(
        state,
        inventory=(inventory[0], inventory[1]),
        episode_id=1,
        episode_token_index=token_index,
        has_buy_fill=True,
    )


def test_mid_spike_triggers_on_10c_drop_within_10s() -> None:
    policy = _policy()
    state = replace(_idle(now_ns=0), books=_books(now_ns=0, mid=0.60))
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    assert state.mid_spike.cooloff_until_ns == 0
    state = replace(state, books=_books(now_ns=5 * NS, mid=0.50))
    state = observe_mid_spike(state=state, policy=policy, now_ns=5 * NS)
    assert state.mid_spike.cooloff_until_ns == 5 * NS + 30 * NS


def test_mid_spike_triggers_on_dire_drop_when_flat() -> None:
    """Flat watches both tokens: a mirrored move is a drop on one of them."""
    policy = _policy()
    state = replace(_idle(now_ns=0), books=_books(now_ns=0, mid=0.40))
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    state = replace(state, books=_books(now_ns=3 * NS, mid=0.50))
    state = observe_mid_spike(state=state, policy=policy, now_ns=3 * NS)
    assert state.mid_spike.cooloff_until_ns == 3 * NS + 30 * NS


def test_mid_spike_triggers_on_held_token_drop() -> None:
    """The incident: holding token1, token1 mid falls 0.77 -> 0.665 while token0 rises."""
    policy = _policy()
    state = _holding(replace(_idle(now_ns=0), books=_books(now_ns=0, mid=0.23)), token_index=1)
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    assert state.mid_spike.cooloff_until_ns == 0
    state = replace(state, books=_books(now_ns=3 * NS, mid=0.335))
    state = observe_mid_spike(state=state, policy=policy, now_ns=3 * NS)
    assert state.mid_spike.cooloff_until_ns == 3 * NS + 30 * NS


def test_mid_spike_ignores_counterparty_drop_while_holding() -> None:
    """Holding token1, a token0 drop is token1 rising: no spike, no sell freeze."""
    policy = _policy()
    state = _holding(replace(_idle(now_ns=0), books=_books(now_ns=0, mid=0.50)), token_index=1)
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    state = replace(state, books=_books(now_ns=3 * NS, mid=0.40))
    state = observe_mid_spike(state=state, policy=policy, now_ns=3 * NS)
    assert state.mid_spike.cooloff_until_ns == 0


def test_mid_spike_watches_episode_token_before_fill() -> None:
    policy = _policy()
    state = replace(
        _idle(now_ns=0),
        books=_books(now_ns=0, mid=0.23),
        episode_id=1,
        episode_token_index=1,
    )
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    state = replace(state, books=_books(now_ns=3 * NS, mid=0.335))
    state = observe_mid_spike(state=state, policy=policy, now_ns=3 * NS)
    assert state.mid_spike.cooloff_until_ns == 3 * NS + 30 * NS


def test_mid_spike_watches_held_token_after_episode_moves_on() -> None:
    """Episode reopened on token0 while token1 is still held: the token1 drop still fires."""
    policy = _policy()
    held = _holding(replace(_idle(now_ns=0), books=_books(now_ns=0, mid=0.23)), token_index=1)
    state = replace(held, episode_token_index=0)
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    assert state.mid_spike.cooloff_until_ns == 0
    state = replace(state, books=_books(now_ns=3 * NS, mid=0.335))
    state = observe_mid_spike(state=state, policy=policy, now_ns=3 * NS)
    assert state.mid_spike.cooloff_until_ns == 3 * NS + 30 * NS


def test_mid_spike_cancels_buy_keeps_sell() -> None:
    policy = _policy()
    sell = _sell_order(now_ns=0)
    buy = _buy_order(now_ns=0)
    signal = RawDeltaSignal(
        predicted_delta=0.05,
        source_received_ns=0,
        received_ns=0,
        anchor_p=0.60,
        deaths_radiant=0,
        deaths_dire=0,
    )
    latch = Latch(
        fair_radiant=0.65,
        predicted_delta=0.05,
        anchor_p=0.60,
        book_p_radiant=0.60,
        fair_valid=True,
        fair_ts_ns=0,
        signal_ts_ns=0,
        anchored_ns=0,
        no_buy_reason="",
    )
    state = replace(
        _idle(now_ns=0),
        books=_books(now_ns=0, mid=0.60),
        signal=signal,
        latch=latch,
        orders=(buy, sell),
        inventory=(
            TokenInventory(token_index=0, qty=100.0, cost_basis=50.0, last_buy_ns=0),
            TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
        ),
        episode_id=1,
        has_buy_fill=True,
    )
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    state = replace(state, books=_books(now_ns=2 * NS, mid=0.49))
    out = step(state=state, policy=policy, event=Wake(now_ns=2 * NS, forced=True))
    assert out.plan.block_reason == "mid_spike"
    assert out.plan.places == ()
    buy_after = next(order for order in out.state.orders if order.order_id == "buy-1")
    sell_after = next(order for order in out.state.orders if order.order_id == "sell-1")
    assert buy_after.status == "canceling"
    assert buy_after.cancel_reason == "mid_spike"
    assert sell_after.status == "live"
    assert not any(cancel.order_id == "sell-1" for cancel in out.plan.cancels)
    assert any(cancel.order_id == "buy-1" for cancel in out.plan.cancels)


def test_mid_spike_freezes_dire_sell_on_dire_drop() -> None:
    policy = _policy()
    sell = _sell_order(now_ns=0, token_index=1)
    buy = _buy_order(now_ns=0, token_index=1)
    signal = RawDeltaSignal(
        predicted_delta=-0.05,
        source_received_ns=0,
        received_ns=0,
        anchor_p=0.40,
        deaths_radiant=0,
        deaths_dire=0,
    )
    latch = Latch(
        fair_radiant=0.35,
        predicted_delta=-0.05,
        anchor_p=0.40,
        book_p_radiant=0.40,
        fair_valid=True,
        fair_ts_ns=0,
        signal_ts_ns=0,
        anchored_ns=0,
        no_buy_reason="",
    )
    state = replace(
        _idle(now_ns=0),
        books=_books(now_ns=0, mid=0.40),
        signal=signal,
        latch=latch,
        orders=(buy, sell),
        inventory=(
            TokenInventory(token_index=0, qty=0.0, cost_basis=0.0, last_buy_ns=None),
            TokenInventory(token_index=1, qty=100.0, cost_basis=50.0, last_buy_ns=0),
        ),
        episode_id=1,
        has_buy_fill=True,
    )
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    state = replace(state, books=_books(now_ns=2 * NS, mid=0.51))
    out = step(state=state, policy=policy, event=Wake(now_ns=2 * NS, forced=True))
    assert out.plan.block_reason == "mid_spike"
    assert out.plan.places == ()
    buy_after = next(order for order in out.state.orders if order.order_id == "buy-1")
    sell_after = next(order for order in out.state.orders if order.order_id == "sell-1")
    assert buy_after.status == "canceling"
    assert buy_after.cancel_reason == "mid_spike"
    assert sell_after.status == "live"
    assert not any(cancel.order_id == "sell-1" for cancel in out.plan.cancels)
    assert any(cancel.order_id == "buy-1" for cancel in out.plan.cancels)


def test_mid_spike_extends_on_further_drop() -> None:
    policy = _policy()
    state = replace(_idle(now_ns=0), books=_books(now_ns=0, mid=0.60))
    out = step(state=state, policy=policy, event=Wake(now_ns=0, forced=True))
    state = replace(out.state, books=_books(now_ns=2 * NS, mid=0.49))
    out = step(state=state, policy=policy, event=Wake(now_ns=2 * NS, forced=True))
    until = out.state.mid_spike.cooloff_until_ns
    state = replace(out.state, books=_books(now_ns=10 * NS, mid=0.38))
    out = step(state=state, policy=policy, event=Wake(now_ns=10 * NS, forced=True))
    assert out.plan.block_reason == "mid_spike"
    assert out.state.mid_spike.cooloff_until_ns == 10 * NS + 30 * NS
    assert out.state.mid_spike.cooloff_until_ns > until


def test_cooloff_clears_after_window() -> None:
    policy = _policy()
    state = replace(_idle(now_ns=0), books=_books(now_ns=0, mid=0.60))
    state = observe_mid_spike(state=state, policy=policy, now_ns=0)
    state = replace(state, books=_books(now_ns=1 * NS, mid=0.50))
    state = observe_mid_spike(state=state, policy=policy, now_ns=1 * NS)
    assert state.mid_spike.cooloff_until_ns == 31 * NS
    later = 40 * NS
    state = replace(state, books=_books(now_ns=later, mid=0.50))
    out = step(state=state, policy=policy, event=Wake(now_ns=later, forced=True))
    assert out.plan.block_reason != "mid_spike"
