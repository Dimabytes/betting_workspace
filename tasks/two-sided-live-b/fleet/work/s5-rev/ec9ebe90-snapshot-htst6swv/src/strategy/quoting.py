"""Follow300 targets and keep/move/replace. Pure; occupancy is in the returned state."""

from dataclasses import dataclass, replace

from shared.utils.trading import buy_share_quantity, share_floor
from strategy.kill_gate import KillExposure, kill_exposure
from strategy.lifecycle import (
    already_canceling,
    begin_episode,
    end_episode_if_idle,
    fresh_rungs,
    inventory_is_dust,
    is_settling,
    mark_canceling,
    next_order_id,
    occupy_buy,
    occupy_sell,
    replace_order,
    rung_occupied,
    sell_occupied,
    settle_deadline_ns,
)
from strategy.mid_spike import mid_spike_active, observe_mid_spike
from strategy.policy import Follow300Policy
from strategy.signals import (
    buy_reject_reason,
    core_book_p,
    entry_delta_blocked,
    fair_radiant,
    is_fresh,
    join_buy_price,
    join_sell_price,
    ladder_price,
    next_delta_gate_open,
    passes_anchor,
    prices_match,
    token_fair,
)
from strategy.types import (
    BlockReason,
    KeepOrder,
    Latch,
    MoveOrder,
    PlaceOrder,
    Plan,
    RawDeltaSignal,
    RestingOrder,
    Rung,
    Side,
    StrategyState,
    TokenBook,
    total_qty,
)


@dataclass(frozen=True)
class QuoteTarget:
    token_index: int
    side: Side
    price: float
    quantity: float
    level_index: int | None


@dataclass(frozen=True)
class QuoteChoice:
    state: StrategyState
    targets: tuple[QuoteTarget, ...]
    no_buy_reason: BlockReason


@dataclass(frozen=True)
class LatchSync:
    state: StrategyState
    rebuilt: bool


def empty_plan(*, reason: BlockReason) -> Plan:
    return Plan(keep=(), moves=(), cancels=(), places=(), block_reason=reason)


def _skip_reprice(state: StrategyState, order: RestingOrder, reason: str) -> bool:
    return reason == "reprice" and inventory_is_dust(
        state=state, order=order, min_order_size=state.limits.min_order_size
    )


def _cancel_orders(*, state: StrategyState, reason: str, buys_only: bool) -> StrategyState:
    for order in state.orders:
        if buys_only and order.side != "BUY":
            continue
        if _skip_reprice(state, order, reason):
            continue
        if already_canceling(order) and order.cancel_reason == reason:
            continue
        state = mark_canceling(state=state, order_id=order.order_id, reason=reason)
    return state


def _blocked(
    *, state: StrategyState, reason: BlockReason, buys_only: bool = False
) -> tuple[StrategyState, Plan]:
    return _cancel_orders(state=state, reason=reason, buys_only=buys_only), empty_plan(
        reason=reason
    )


def _cancel_kill_exposed(*, state: StrategyState, exposure: KillExposure) -> StrategyState:
    """Cancel BUYs on victim tokens and SELLs on the killer tokens."""
    exposed: set[tuple[Side, int]] = set()
    for token in exposure.buy_blocked:
        exposed.add(("BUY", token))
    for token in exposure.sell_blocked:
        exposed.add(("SELL", token))
    for order in state.orders:
        if (order.side, order.token_index) not in exposed:
            continue
        if already_canceling(order) and order.cancel_reason == "kill":
            continue
        state = mark_canceling(state=state, order_id=order.order_id, reason="kill")
    return state


def _cutoff(state: StrategyState, policy: Follow300Policy) -> bool:
    return state.clock.game_second >= policy.buy_cutoff_second


def books_unusable(state: StrategyState, now_ns: int) -> bool:
    books = state.books
    if books is None:
        return True
    for book in books.tokens:
        if book.bid <= 0 or book.ask <= 0:
            return True
        if not is_fresh(now_ns=now_ns, ts_ns=book.ts_ns, max_age_s=state.freshness.book_stale_s):
            return True
    return False


def _signal_fresh(state: StrategyState, now_ns: int, max_age_s: float) -> bool:
    signal = state.signal
    if signal is None:
        return False
    return is_fresh(now_ns=now_ns, ts_ns=signal.source_received_ns, max_age_s=max_age_s)


def _buy_qty(policy: Follow300Policy, price: float) -> float:
    return buy_share_quantity(base_size_usdc=policy.level_usdc, price=price)


def _fair_for(state: StrategyState, token_index: int, fair: float) -> float:
    return token_fair(
        fair_radiant=fair,
        token_index=token_index,
        radiant_token_index=state.limits.radiant_token_index,
    )


def _reject_buy(
    *, state: StrategyState, policy: Follow300Policy, book: TokenBook, price: float, fair: float
) -> BlockReason | None:
    return buy_reject_reason(
        price=price,
        bid=book.bid,
        ask=book.ask,
        fair_token=fair,
        min_entry_price=policy.min_entry_price,
        max_entry_price=policy.max_entry_price,
        max_entry_spread_ticks=policy.max_entry_spread_ticks,
    )


def pick_episode_token(
    *, state: StrategyState, policy: Follow300Policy, fair: float, buy_blocked: frozenset[int]
) -> int | None:
    signal = state.signal
    books = state.books
    if signal is None or books is None:
        return None
    if entry_delta_blocked(
        predicted_delta=signal.predicted_delta,
        min_abs_delta=policy.min_abs_delta,
        exit_abs_delta=policy.exit_abs_delta,
        gate_open=state.delta_gate_open,
    ):
        return None
    best_index: int | None = None
    best_edge = float("-inf")
    for book in books.tokens:
        if book.token_index in buy_blocked:
            continue
        token_fair_px = _fair_for(state, book.token_index, fair)
        join_price = join_buy_price(book.bid)
        if _reject_buy(state=state, policy=policy, book=book, price=join_price, fair=token_fair_px):
            continue
        edge = token_fair_px - join_price
        if best_index is not None and (
            edge < best_edge or (edge == best_edge and book.token_index > best_index)
        ):
            continue
        best_edge = edge
        best_index = book.token_index
    return best_index


def _buy_closed_reason(*, state: StrategyState, policy: Follow300Policy) -> BlockReason | None:
    if _cutoff(state, policy):
        return "cutoff"
    if state.pending_ownership:
        return "ownership_unresolved"
    if state.sell_only:
        return "recovery"
    if state.winding_down:
        return "winding_down"
    if state.permissions.reduce_only:
        return "reduce_only"
    if not state.permissions.allow_buy:
        return "fair"
    signal = state.signal
    if signal is None:
        return "stale_signal"
    if entry_delta_blocked(
        predicted_delta=signal.predicted_delta,
        min_abs_delta=policy.min_abs_delta,
        exit_abs_delta=policy.exit_abs_delta,
        gate_open=state.delta_gate_open,
    ):
        return "min_delta"
    return None


def _join_reject_reason(
    *, state: StrategyState, policy: Follow300Policy, fair: float
) -> BlockReason:
    books = state.books
    if books is None:
        return "stale_book"
    reasons: list[BlockReason] = []
    for book in books.tokens:
        reject = _reject_buy(
            state=state,
            policy=policy,
            book=book,
            price=join_buy_price(book.bid),
            fair=_fair_for(state, book.token_index, fair),
        )
        if reject is not None:
            reasons.append(reject)
    for key in ("wide_spread", "min_price", "max_price", "fair"):
        if key in reasons:
            return key
    return "fair"


def _price_rungs(
    *, state: StrategyState, policy: Follow300Policy, token_index: int, follow_book: bool
) -> StrategyState:
    books = state.books
    if books is None:
        return state
    if not state.rungs:
        state = replace(state, rungs=fresh_rungs(level_count=policy.level_count))
    needs_price = follow_book
    if not needs_price:
        return state
    join_price = join_buy_price(books.tokens[token_index].bid)
    rungs = tuple(
        replace(
            rung,
            price=ladder_price(
                join_price=join_price,
                level_index=rung.index,
                step_ticks=policy.step_ticks,
                tick_size=policy.tick_size,
            ),
        )
        for rung in state.rungs
    )
    return replace(state, rungs=rungs)


def _rung_buy_target(
    *,
    state: StrategyState,
    policy: Follow300Policy,
    book: TokenBook,
    fair: float,
    rung: Rung,
) -> QuoteTarget | None:
    """The rung's full-clip BUY target, or none when its price is rejected."""
    # Reject first: a deep rung can price at 0.00, and _buy_qty divides by price.
    reject = _reject_buy(state=state, policy=policy, book=book, price=rung.price, fair=fair)
    if reject is not None:
        return None
    quantity = _buy_qty(policy, rung.price)
    if (
        not rung_occupied(state=state, level_index=rung.index)
        and quantity < state.limits.min_order_size
    ):
        return None
    return QuoteTarget(
        token_index=book.token_index,
        side="BUY",
        price=rung.price,
        quantity=quantity,
        level_index=rung.index,
    )


def _open_buy_targets(
    *,
    state: StrategyState,
    policy: Follow300Policy,
    token_index: int,
    fair: float,
    follow_book: bool,
) -> QuoteChoice:
    books = state.books
    if books is None:
        return QuoteChoice(state=state, targets=(), no_buy_reason="stale_book")
    if state.episode_token_index is None:
        if not follow_book:
            return QuoteChoice(state=state, targets=(), no_buy_reason="fair")
        held = any(
            share_floor(token.qty) >= state.limits.min_order_size for token in state.inventory
        )
        if held:
            return QuoteChoice(state=state, targets=(), no_buy_reason="position_open")
        state = begin_episode(state=state, token_index=token_index, level_count=policy.level_count)
    state = _price_rungs(
        state=state, policy=policy, token_index=token_index, follow_book=follow_book
    )
    book = books.tokens[token_index]
    token_fair_px = _fair_for(state, token_index, fair)
    targets: list[QuoteTarget] = []
    for rung in state.rungs:
        target = _rung_buy_target(
            state=state, policy=policy, book=book, fair=token_fair_px, rung=rung
        )
        if target is not None:
            targets.append(target)
    reason: BlockReason = (
        "" if targets else _join_reject_reason(state=state, policy=policy, fair=fair)
    )
    return QuoteChoice(state=state, targets=tuple(targets), no_buy_reason=reason)


def _choose_buy_targets(
    *,
    state: StrategyState,
    policy: Follow300Policy,
    fair: float,
    follow_book: bool,
    exposure: KillExposure,
) -> QuoteChoice:
    closed = _buy_closed_reason(state=state, policy=policy)
    if closed is not None:
        return QuoteChoice(state=state, targets=(), no_buy_reason=closed)
    token_index = state.episode_token_index
    if token_index is None:
        token_index = pick_episode_token(
            state=state, policy=policy, fair=fair, buy_blocked=exposure.buy_blocked
        )
    elif token_index in exposure.buy_blocked:
        return QuoteChoice(state=state, targets=(), no_buy_reason="kill")
    if token_index is None:
        return QuoteChoice(
            state=state,
            targets=(),
            no_buy_reason=_join_reject_reason(state=state, policy=policy, fair=fair),
        )
    return _open_buy_targets(
        state=state,
        policy=policy,
        token_index=token_index,
        fair=fair,
        follow_book=follow_book,
    )


def held_sell(state: StrategyState, blocked: frozenset[int]) -> RestingOrder | None:
    min_size = state.limits.min_order_size
    for order in state.orders:
        if (
            order.side == "SELL"
            and order.token_index not in blocked
            and not already_canceling(order)
            and not inventory_is_dust(state=state, order=order, min_order_size=min_size)
        ):
            return order
    return None


def _select_exit_token(state: StrategyState, blocked: frozenset[int]) -> int | None:
    held = held_sell(state, blocked)
    if held is not None:
        return held.token_index
    min_size = state.limits.min_order_size
    allowed = [token for token in state.inventory if token.token_index not in blocked]
    sellable = [token for token in allowed if share_floor(token.qty) >= min_size]
    pool = sellable or [token for token in allowed if token.qty > 0.0]
    return max(pool, key=lambda token: token.qty).token_index if pool else None


def unconfirmed_hold(*, state: StrategyState, policy: Follow300Policy) -> bool:
    if state.unconfirmed_keys:
        return True
    return policy.hold_unconfirmed_sell and state.permissions.sell_unconfirmed


def _min_life_hold(*, held: RestingOrder, policy: Follow300Policy, now_ns: int) -> bool:
    if held.accepted_ns is None:
        return False
    return now_ns - held.accepted_ns < policy.sell_min_life_s * 1e9


def sell_boundary_ns(*, state: StrategyState, policy: Follow300Policy, now_ns: int) -> int | None:
    if unconfirmed_hold(state=state, policy=policy):
        return None
    candidates: list[int] = []
    held = held_sell(state, frozenset())
    if held is not None and held.accepted_ns is not None and policy.sell_min_life_s > 0:
        life_end = held.accepted_ns + round(policy.sell_min_life_s * 1_000_000_000)
        if now_ns < life_end:
            candidates.append(life_end)
    token_index = held.token_index if held is not None else state.position.token_index
    settle_end = settle_deadline_ns(state=state, policy=policy, token_index=token_index)
    if settle_end is not None and now_ns < settle_end:
        candidates.append(settle_end)
    if not candidates:
        return None
    return min(candidates)


def reanchor_boundary_ns(*, state: StrategyState, policy: Follow300Policy) -> int | None:
    """When the held latch next re-anchors. An anchor block waits for the next tick."""
    latch = state.latch
    if latch is None or latch.no_buy_reason == "anchor":
        return None
    return latch.anchored_ns + round(policy.latch_reanchor_s * 1_000_000_000)


def _sell_leftover(order: RestingOrder) -> float:
    return max(0.0, order.submitted_qty - order.filled_qty)


def _held_sell_target(held: RestingOrder) -> QuoteTarget:
    return QuoteTarget(
        token_index=held.token_index,
        side="SELL",
        price=held.price,
        quantity=_sell_leftover(held),
        level_index=None,
    )


def _sell_covers_inventory(state: StrategyState, held: RestingOrder) -> bool:
    need = _sell_quantity(state, held.token_index)
    return _sell_leftover(held) + 1e-12 >= need


def _sell_quantity(state: StrategyState, token_index: int) -> float:
    qty = share_floor(state.inventory[token_index].qty)
    if qty < state.limits.min_order_size:
        return 0.0
    return qty


def _join_ask_sell(state: StrategyState, token_index: int | None) -> QuoteTarget | None:
    books = state.books
    if token_index is None or books is None:
        return None
    join_price = join_sell_price(books.tokens[token_index].ask)
    qty = _sell_quantity(state, token_index)
    if qty <= 0.0 or not 0.0 < join_price < 1.0:
        return None
    return QuoteTarget(
        token_index=token_index, side="SELL", price=join_price, quantity=qty, level_index=None
    )


def _choose_sell_target(
    *, state: StrategyState, token_index: int | None, fair: float
) -> QuoteTarget | None:
    books = state.books
    if token_index is None or books is None:
        return None
    book = books.tokens[token_index]
    token_fair_px = _fair_for(state, token_index, fair)
    join_price = join_sell_price(book.ask)
    if join_price + 1e-12 < token_fair_px:
        join_price = join_sell_price(token_fair_px)
    if not 0.0 < join_price < 1.0:
        return None
    qty = _sell_quantity(state, token_index)
    if qty <= 0.0:
        return None
    return QuoteTarget(
        token_index=token_index, side="SELL", price=join_price, quantity=qty, level_index=None
    )


def decide_sell(
    *,
    state: StrategyState,
    policy: Follow300Policy,
    now_ns: int,
    fair: float | None,
    exposure: KillExposure,
) -> QuoteTarget | None:
    if not state.permissions.allow_sell:
        return None
    token_index = _select_exit_token(state, exposure.sell_blocked)
    held = held_sell(state, exposure.sell_blocked)
    covers = held is not None and _sell_covers_inventory(state, held)
    if held is not None and covers and unconfirmed_hold(state=state, policy=policy):
        return _held_sell_target(held)
    if is_settling(state=state, policy=policy, now_ns=now_ns, token_index=token_index):
        return None
    if held is not None and covers and _min_life_hold(held=held, policy=policy, now_ns=now_ns):
        return _held_sell_target(held)
    if fair is None:
        return _join_ask_sell(state, token_index)
    return _choose_sell_target(state=state, token_index=token_index, fair=fair)


def choose_targets(
    *,
    state: StrategyState,
    policy: Follow300Policy,
    now_ns: int,
    fair: float | None,
    follow_book: bool,
    allow_buy: bool,
) -> QuoteChoice:
    exposure = kill_exposure(state=state, now_ns=now_ns)
    buys: tuple[QuoteTarget, ...] = ()
    no_buy: BlockReason = "fair"
    if allow_buy and fair is not None:
        chosen = _choose_buy_targets(
            state=state, policy=policy, fair=fair, follow_book=follow_book, exposure=exposure
        )
        state = chosen.state
        buys = chosen.targets
        no_buy = chosen.no_buy_reason
    elif state.sell_only:
        no_buy = "recovery"
    sell = decide_sell(state=state, policy=policy, now_ns=now_ns, fair=fair, exposure=exposure)
    targets = buys if sell is None else (*buys, sell)
    return QuoteChoice(state=state, targets=targets, no_buy_reason=no_buy)


def _live_matches(*, policy: Follow300Policy, order: RestingOrder, target: QuoteTarget) -> bool:
    if order.status == "gone" or already_canceling(order):
        return False
    if order.token_index != target.token_index or order.side != target.side:
        return False
    if not prices_match(left=order.price, right=target.price, tick_size=policy.tick_size):
        return False
    if target.side != "SELL":
        return True
    leftover = order.submitted_qty - order.filled_qty
    return abs(leftover - target.quantity) < 1e-12


def _reattach(
    *, state: StrategyState, order: RestingOrder, new_index: int
) -> tuple[StrategyState, RestingOrder]:
    old_index = order.level_index
    if old_index == new_index:
        return state, order
    rungs: list[Rung] = []
    for rung in state.rungs:
        if old_index is not None and rung.index == old_index and rung.live_id == order.order_id:
            rungs.append(replace(rung, live_id=None))
        elif rung.index == new_index:
            rungs.append(replace(rung, live_id=order.order_id))
        else:
            rungs.append(rung)
    moved = replace(order, level_index=new_index)
    state = replace(state, rungs=tuple(rungs))
    return replace_order(state=state, order=moved), moved


def reconcile(
    *,
    state: StrategyState,
    policy: Follow300Policy,
    targets: tuple[QuoteTarget, ...],
    now_ns: int,
) -> tuple[StrategyState, Plan]:
    unmatched = {order.order_id: order for order in state.orders}
    missing: list[QuoteTarget] = []
    keeps: list[KeepOrder] = []
    moves: list[MoveOrder] = []
    for target in targets:
        matched_id: str | None = None
        for order_id, order in unmatched.items():
            if not _live_matches(policy=policy, order=order, target=target):
                continue
            matched_id = order_id
            break
        if matched_id is None:
            missing.append(target)
            continue
        order = unmatched.pop(matched_id)
        if target.side == "BUY" and target.level_index is not None:
            from_level = order.level_index
            state, order = _reattach(state=state, order=order, new_index=target.level_index)
            if from_level is not None and from_level != target.level_index:
                moves.append(
                    MoveOrder(
                        order_id=order.order_id, from_level=from_level, to_level=target.level_index
                    )
                )
            else:
                keeps.append(KeepOrder(order_id=order.order_id, level_index=order.level_index))
        else:
            keeps.append(KeepOrder(order_id=order.order_id, level_index=order.level_index))
    for order in unmatched.values():
        if already_canceling(order) or _skip_reprice(state, order, "reprice"):
            continue
        state = mark_canceling(state=state, order_id=order.order_id, reason="reprice")
    return place_missing(
        state=state,
        missing=tuple(missing),
        keeps=tuple(keeps),
        moves=tuple(moves),
        now_ns=now_ns,
    )


def place_missing(
    *,
    state: StrategyState,
    missing: tuple[QuoteTarget, ...],
    keeps: tuple[KeepOrder, ...],
    moves: tuple[MoveOrder, ...],
    now_ns: int,
) -> tuple[StrategyState, Plan]:
    places: list[PlaceOrder] = []
    blocked_reason: BlockReason = ""
    for target in missing:
        if target.side == "BUY":
            if rung_occupied(state=state, level_index=target.level_index):
                continue
            needed = target.price * target.quantity
            budget = state.budget
            rooms: dict[BlockReason, float] = {
                "position_cap": budget.cap_room_usdc,
                "account_cap": budget.account_cap_room_usdc,
                "no_cash": budget.cash_usdc,
            }
            tightest = min(rooms, key=rooms.__getitem__)
            if rooms[tightest] + 1e-12 < needed:
                blocked_reason = tightest
                continue
        elif sell_occupied(state=state):
            continue
        order_id, state = next_order_id(state)
        order = RestingOrder(
            order_id=order_id,
            episode_id=state.episode_id,
            token_index=target.token_index,
            side=target.side,
            price=target.price,
            submitted_qty=target.quantity,
            filled_qty=0.0,
            level_index=target.level_index,
            status="pending",
            accepted=False,
            partially_filled=False,
            cancel_reason="",
            ack_reason="",
            placed_ns=now_ns,
            accepted_ns=None,
        )
        if target.side == "BUY":
            state = occupy_buy(state=state, order=order)
        else:
            state = occupy_sell(state=state, order=order)
        places.append(
            PlaceOrder(
                order_id=order_id,
                episode_id=order.episode_id,
                token_index=target.token_index,
                side=target.side,
                price=target.price,
                quantity=target.quantity,
                level_index=target.level_index,
                reduce_only=target.side == "SELL",
            )
        )
    placed_buy = any(place.side == "BUY" for place in places)
    reason: BlockReason = "" if placed_buy else blocked_reason
    return state, Plan(
        keep=keeps, moves=moves, cancels=(), places=tuple(places), block_reason=reason
    )


def _rebuild_latch(
    *, state: StrategyState, book_p: float, signal: RawDeltaSignal, now_ns: int
) -> StrategyState:
    if not passes_anchor(book_p=book_p, anchor_p=signal.anchor_p):
        previous = state.latch
        return replace(
            state,
            latch=Latch(
                fair_radiant=previous.fair_radiant if previous is not None else 0.0,
                predicted_delta=signal.predicted_delta,
                anchor_p=signal.anchor_p,
                book_p_radiant=book_p,
                fair_valid=previous.fair_valid if previous is not None else False,
                fair_ts_ns=signal.source_received_ns,
                signal_ts_ns=signal.received_ns,
                anchored_ns=now_ns,
                no_buy_reason="anchor",
            ),
        )
    return replace(
        state,
        latch=Latch(
            fair_radiant=fair_radiant(
                book_p_radiant=book_p, predicted_delta=signal.predicted_delta
            ),
            predicted_delta=signal.predicted_delta,
            anchor_p=signal.anchor_p,
            book_p_radiant=book_p,
            fair_valid=True,
            fair_ts_ns=signal.source_received_ns,
            signal_ts_ns=signal.received_ns,
            anchored_ns=now_ns,
            no_buy_reason="",
        ),
    )


def _reanchor_latch(
    *, state: StrategyState, latch: Latch, book_p: float, now_ns: int
) -> StrategyState:
    """Recompute fair from the live book, holding the signal's delta and fair_ts."""
    return replace(
        state,
        latch=replace(
            latch,
            fair_radiant=fair_radiant(book_p_radiant=book_p, predicted_delta=latch.predicted_delta),
            book_p_radiant=book_p,
            fair_valid=True,
            no_buy_reason="",
            anchored_ns=now_ns,
        ),
    )


def _sync_latch(
    *, state: StrategyState, policy: Follow300Policy, now_ns: int, book_p: float
) -> LatchSync:
    signal = state.signal
    if signal is None or not _signal_fresh(state, now_ns, state.freshness.exit_stale_s):
        return LatchSync(state=replace(state, latch=None), rebuilt=False)
    latch = state.latch
    if latch is None or signal.received_ns > latch.signal_ts_ns:
        return LatchSync(
            state=_rebuild_latch(state=state, book_p=book_p, signal=signal, now_ns=now_ns),
            rebuilt=True,
        )
    boundary = reanchor_boundary_ns(state=state, policy=policy)
    if boundary is not None and now_ns >= boundary:
        return LatchSync(
            state=_reanchor_latch(state=state, latch=latch, book_p=book_p, now_ns=now_ns),
            rebuilt=True,
        )
    return LatchSync(state=state, rebuilt=False)


def _quote_with_latch(
    *,
    state: StrategyState,
    policy: Follow300Policy,
    now_ns: int,
    book_p: float,
) -> tuple[StrategyState, Plan]:
    entry_fresh = _signal_fresh(state, now_ns, state.freshness.entry_stale_s)
    synced = _sync_latch(state=state, policy=policy, now_ns=now_ns, book_p=book_p)
    return _quote_v5_latch(
        state=synced.state, policy=policy, now_ns=now_ns, synced=synced, entry_fresh=entry_fresh
    )


def _quote_v5_latch(
    *,
    state: StrategyState,
    policy: Follow300Policy,
    now_ns: int,
    synced: LatchSync,
    entry_fresh: bool,
) -> tuple[StrategyState, Plan]:
    latch = state.latch
    if not _signal_fresh(state, now_ns, state.freshness.exit_stale_s) or latch is None:
        return _blocked(state=state, reason="stale_signal")
    allow_buy = entry_fresh and latch.no_buy_reason != "anchor"
    if not entry_fresh:
        state = _cancel_orders(state=state, reason="stale_signal", buys_only=True)
        if total_qty(state.inventory) == 0:
            return state, empty_plan(reason="stale_signal")
    elif latch.no_buy_reason == "anchor":
        state = _cancel_orders(state=state, reason="anchor", buys_only=True)
        if total_qty(state.inventory) == 0:
            return _blocked(state=state, reason="anchor")
    fair_fresh = is_fresh(
        now_ns=now_ns, ts_ns=latch.fair_ts_ns, max_age_s=state.freshness.exit_stale_s
    )
    if not latch.fair_valid or not fair_fresh:
        reason = "anchor" if latch.no_buy_reason == "anchor" else "stale_signal"
        return _blocked(state=state, reason=reason)
    return _reconcile_choice(
        state=state,
        policy=policy,
        now_ns=now_ns,
        fair=latch.fair_radiant,
        follow_book=state.has_buy_fill or synced.rebuilt,
        allow_buy=allow_buy,
        latch_reason="anchor" if latch.no_buy_reason == "anchor" else "",
    )


def _reconcile_choice(
    *,
    state: StrategyState,
    policy: Follow300Policy,
    now_ns: int,
    fair: float | None,
    follow_book: bool,
    allow_buy: bool,
    latch_reason: BlockReason,
) -> tuple[StrategyState, Plan]:
    choice = choose_targets(
        state=state,
        policy=policy,
        now_ns=now_ns,
        fair=fair,
        follow_book=follow_book,
        allow_buy=allow_buy,
    )
    state = choice.state
    reason: BlockReason = latch_reason or choice.no_buy_reason
    held_reason: BlockReason = latch_reason or "fair"
    if choice.no_buy_reason == "ownership_unresolved":
        held_reason = "ownership_unresolved"
    elif choice.no_buy_reason == "position_open":
        held_reason = "position_open"
    if not choice.targets:
        if total_qty(state.inventory) == 0:
            return _blocked(state=state, reason=reason)
        state = _cancel_orders(state=state, reason=held_reason, buys_only=True)
        return state, empty_plan(reason=held_reason)
    if all(target.side == "SELL" for target in choice.targets):
        state = _cancel_orders(state=state, reason=held_reason, buys_only=True)
    state, plan = reconcile(state=state, policy=policy, targets=choice.targets, now_ns=now_ns)
    if not any(target.side == "BUY" for target in choice.targets):
        plan = replace(plan, block_reason=held_reason if total_qty(state.inventory) > 0 else reason)
    return state, plan


def _apply_session_cancels(*, state: StrategyState, policy: Follow300Policy) -> StrategyState:
    if _cutoff(state, policy):
        return _cancel_orders(state=state, reason="cutoff", buys_only=True)
    return state


def _quote_after_gates(
    *, state: StrategyState, policy: Follow300Policy, now_ns: int
) -> tuple[StrategyState, Plan]:
    if _cutoff(state, policy) and total_qty(state.inventory) == 0:
        return state, empty_plan(reason="cutoff")
    if books_unusable(state, now_ns) or state.books is None:
        return _blocked(state=state, reason="stale_book")
    pair_p = core_book_p(books=state.books, limits=state.limits)
    if pair_p is None:
        return _blocked(state=state, reason="pair_tolerance")
    return _quote_with_latch(state=state, policy=policy, now_ns=now_ns, book_p=pair_p)


def _recovery_fair(*, state: StrategyState, now_ns: int) -> float | None:
    latch = state.latch
    if latch is None or not latch.fair_valid:
        return None
    if not is_fresh(now_ns=now_ns, ts_ns=latch.fair_ts_ns, max_age_s=state.freshness.exit_stale_s):
        return None
    return latch.fair_radiant


def _recovery_exit(
    *, state: StrategyState, policy: Follow300Policy, now_ns: int
) -> tuple[StrategyState, Plan]:
    state = _cancel_orders(state=state, reason="recovery", buys_only=True)
    state = end_episode_if_idle(state=state)
    if books_unusable(state, now_ns) or state.books is None:
        return _blocked(state=state, reason="stale_book")
    pair_p = core_book_p(books=state.books, limits=state.limits)
    if pair_p is not None and total_qty(state.inventory) > 0.0:
        synced = _sync_latch(state=state, policy=policy, now_ns=now_ns, book_p=pair_p)
        state = synced.state
    fair = _recovery_fair(state=state, now_ns=now_ns)
    exposure = kill_exposure(state=state, now_ns=now_ns)
    sell = decide_sell(state=state, policy=policy, now_ns=now_ns, fair=fair, exposure=exposure)
    if sell is None:
        return state, empty_plan(reason="recovery")
    return reconcile(state=state, policy=policy, targets=(sell,), now_ns=now_ns)


def _advance_delta_gate(*, state: StrategyState, policy: Follow300Policy) -> StrategyState:
    """Move the Schmitt bit from the latest signal; runs ahead of every gate below."""
    signal = state.signal
    if signal is None:
        return state
    gate_open = next_delta_gate_open(
        was_open=state.delta_gate_open,
        abs_delta=abs(signal.predicted_delta),
        entry=policy.min_abs_delta,
        exit_threshold=policy.exit_abs_delta,
    )
    if gate_open == state.delta_gate_open:
        return state
    return replace(state, delta_gate_open=gate_open)


def requote(
    *, state: StrategyState, policy: Follow300Policy, now_ns: int
) -> tuple[StrategyState, Plan]:
    state = _advance_delta_gate(state=state, policy=policy)
    if state.permissions.halt:
        return _blocked(state=state, reason="halt")
    if state.clock.paused:
        return _blocked(state=state, reason="paused")
    if state.clock.game_ended:
        return _blocked(state=state, reason="game_end")
    state = observe_mid_spike(state=state, policy=policy, now_ns=now_ns)
    exposure = kill_exposure(state=state, now_ns=now_ns)
    if exposure.buy_blocked:
        state = _cancel_kill_exposed(state=state, exposure=exposure)
    if mid_spike_active(state=state, now_ns=now_ns):
        # Pull BUYs and skip new quotes; leave resting SELLs alone (no join-down).
        return _blocked(state=state, reason="mid_spike", buys_only=True)
    if state.sell_only:
        return _recovery_exit(state=state, policy=policy, now_ns=now_ns)
    state = _apply_session_cancels(state=state, policy=policy)
    state = end_episode_if_idle(state=state)
    return _quote_after_gates(state=state, policy=policy, now_ns=now_ns)
