"""Two-sided maker quotes in the core: pull reasons, bid targets, one-tick hold."""

from dataclasses import dataclass, replace

from shared.utils.trading import share_floor
from strategy.lifecycle import already_canceling, mark_canceling
from strategy.policy import TwoSidedPolicy
from strategy.quoting import QuoteTarget, books_unusable, empty_plan, place_missing
from strategy.signals import core_book_p, is_fresh
from strategy.two_sided import inventory_skew, price_bids, scale_order_size, tick_gap
from strategy.types import (
    BlockReason,
    BookPair,
    KeepOrder,
    Plan,
    RestingOrder,
    StrategyState,
)


@dataclass(frozen=True)
class HeldBids:
    targets: tuple[QuoteTarget, ...]
    reprice_since_ns: tuple[int | None, int | None]


def _session_pull_reason(*, state: StrategyState) -> BlockReason | None:
    if state.permissions.halt:
        return "halt"
    if state.ownership_unresolved:
        return "ownership_unresolved"
    if state.recovery_pending:
        return "recovery"
    return None


def _clock_pull_reason(
    *, state: StrategyState, policy: TwoSidedPolicy, now_ns: int
) -> BlockReason | None:
    clock = state.clock
    if clock.game_ended:
        return "game_end"
    if clock.paused:
        return "paused"
    if clock.game_second < policy.quote_from_second:
        return "cutoff"
    if clock.now_ns == 0 or not is_fresh(
        now_ns=now_ns, ts_ns=clock.now_ns, max_age_s=state.freshness.entry_stale_s
    ):
        return "stale_signal"
    return None


def _is_outside_band(*, books: BookPair, band_hi: float) -> bool:
    for book in books.tokens:
        mid = (book.bid + book.ask) / 2.0
        if mid > band_hi or mid < 1.0 - band_hi:
            return True
    return False


def _market_pull_reason(
    *, state: StrategyState, policy: TwoSidedPolicy, now_ns: int
) -> BlockReason | None:
    if state.permissions.reduce_only:
        return "reduce_only"
    if not state.permissions.allow_buy:
        return "fair"
    books = state.books
    if books is None or books_unusable(state, now_ns):
        return "stale_book"
    if _is_outside_band(books=books, band_hi=policy.band_hi):
        return "band"
    if core_book_p(books=books, limits=state.limits) is None:
        return "pair_tolerance"
    return None


def two_sided_pull_reason(
    *, state: StrategyState, policy: TwoSidedPolicy, now_ns: int
) -> BlockReason | None:
    reason = _session_pull_reason(state=state)
    if reason is None:
        reason = _clock_pull_reason(state=state, policy=policy, now_ns=now_ns)
    if reason is None:
        reason = _market_pull_reason(state=state, policy=policy, now_ns=now_ns)
    return reason


def _leg_target(
    *,
    state: StrategyState,
    policy: TwoSidedPolicy,
    leg_index: int,
    token_index: int,
    ticks: int,
    net_shares: float,
) -> QuoteTarget | None:
    if ticks <= 0:
        return None
    size = share_floor(
        scale_order_size(
            size_shares=policy.order_shares,
            net_shares=net_shares,
            net_max_shares=policy.net_max_shares,
            token_index=leg_index,
        )
    )
    if size < state.limits.min_order_size:
        return None
    return QuoteTarget(
        token_index=token_index,
        side="BUY",
        price=round(ticks * policy.tick, 2),
        quantity=size,
        level_index=token_index,
    )


def desired_bids(*, state: StrategyState, policy: TwoSidedPolicy) -> tuple[QuoteTarget, ...]:
    books = state.books
    fair = None if books is None else core_book_p(books=books, limits=state.limits)
    if fair is None:
        return ()
    radiant = state.limits.radiant_token_index
    dire = 1 - radiant
    net = state.inventory[radiant].qty - state.inventory[dire].qty
    skew = inventory_skew(fair=fair, net_shares=net, skew_per_share=policy.skew_per_share)
    ticks = price_bids(fair=fair, half_spread_ticks=policy.half_spread_ticks, skew=skew)
    legs = (
        _leg_target(
            state=state,
            policy=policy,
            leg_index=0,
            token_index=radiant,
            ticks=ticks.yes_ticks,
            net_shares=net,
        ),
        _leg_target(
            state=state,
            policy=policy,
            leg_index=1,
            token_index=dire,
            ticks=ticks.no_ticks,
            net_shares=net,
        ),
    )
    return tuple(target for target in legs if target is not None)


def _live_bid(*, state: StrategyState, token_index: int) -> RestingOrder | None:
    for order in state.orders:
        if (
            order.side == "BUY"
            and order.token_index == token_index
            and order.status != "gone"
            and not already_canceling(order)
        ):
            return order
    return None


def hold_one_tick_moves(
    *,
    state: StrategyState,
    policy: TwoSidedPolicy,
    targets: tuple[QuoteTarget, ...],
    now_ns: int,
) -> HeldBids:
    held: list[QuoteTarget] = []
    since: list[int | None] = [None, None]
    for target in targets:
        live = _live_bid(state=state, token_index=target.token_index)
        if live is None:
            held.append(target)
            continue
        gap = tick_gap(live_price=live.price, want_price=target.price)
        if gap == 0 or gap >= policy.reprice_now_ticks:
            held.append(target)
            continue
        started = state.reprice_since_ns[target.token_index]
        if started is None:
            started = now_ns
        if now_ns - started >= policy.reprice_hold_ns:
            held.append(target)
            continue
        since[target.token_index] = started
        held.append(replace(target, price=live.price))
    return HeldBids(targets=tuple(held), reprice_since_ns=(since[0], since[1]))


def reprice_hold_boundary_ns(*, state: StrategyState, policy: TwoSidedPolicy) -> int | None:
    starts = [start for start in state.reprice_since_ns if start is not None]
    if not starts:
        return None
    return min(starts) + policy.reprice_hold_ns


def _pull_bids(*, state: StrategyState, reason: BlockReason) -> tuple[StrategyState, Plan]:
    state = replace(state, reprice_since_ns=(None, None))
    for order in state.orders:
        state = mark_canceling(state=state, order_id=order.order_id, reason=reason)
    return state, empty_plan(reason=reason)


def _opposite_bid_blocks(
    *, state: StrategyState, target: QuoteTarget, policy: TwoSidedPolicy
) -> bool:
    opposite = 1 - target.token_index
    want_ticks = round(target.price / policy.tick)
    for order in state.orders:
        if order.side != "BUY" or order.token_index != opposite:
            continue
        if want_ticks + round(order.price / policy.tick) > policy.max_bid_sum_ticks:
            return True
    return False


def _reconcile_bids(
    *,
    state: StrategyState,
    policy: TwoSidedPolicy,
    targets: tuple[QuoteTarget, ...],
    now_ns: int,
) -> tuple[StrategyState, Plan]:
    keeps: list[KeepOrder] = []
    missing: list[QuoteTarget] = []
    for target in targets:
        live = _live_bid(state=state, token_index=target.token_index)
        if live is not None and tick_gap(live_price=live.price, want_price=target.price) == 0:
            keeps.append(KeepOrder(order_id=live.order_id, level_index=live.level_index))
        else:
            missing.append(target)
    kept = {keep.order_id for keep in keeps}
    for order in state.orders:
        if order.order_id in kept or already_canceling(order):
            continue
        state = mark_canceling(state=state, order_id=order.order_id, reason="reprice")
    placeable = tuple(
        target
        for target in missing
        if not _opposite_bid_blocks(state=state, target=target, policy=policy)
    )
    return place_missing(
        state=state, missing=placeable, keeps=tuple(keeps), moves=(), now_ns=now_ns
    )


def requote_two_sided(
    *, state: StrategyState, policy: TwoSidedPolicy, now_ns: int
) -> tuple[StrategyState, Plan]:
    if state.sell_only and not state.recovery_pending:
        state = replace(state, sell_only=False)
    reason = two_sided_pull_reason(state=state, policy=policy, now_ns=now_ns)
    if reason is not None:
        return _pull_bids(state=state, reason=reason)
    targets = desired_bids(state=state, policy=policy)
    held = hold_one_tick_moves(state=state, policy=policy, targets=targets, now_ns=now_ns)
    state = replace(state, reprice_since_ns=held.reprice_since_ns)
    return _reconcile_bids(state=state, policy=policy, targets=held.targets, now_ns=now_ns)
