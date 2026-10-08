"""Episode, fill, and cancel state transitions. No venue I/O."""

from dataclasses import replace

from shared.utils.trading import HALF_SHARE_TICK, drop_share_residue, share_floor
from strategy.kill_gate import empty_kill_gate
from strategy.mid_spike import empty_mid_spike
from strategy.policy import Follow300Policy
from strategy.types import (
    Budget,
    CancelTimeout,
    EpisodeArchive,
    Fill,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    Merged,
    OrderRecord,
    OrderRejected,
    OrderStatus,
    OwnershipResolved,
    PendingFill,
    Permissions,
    QuoteSchedule,
    Recovery,
    RecoveryVerified,
    RestingOrder,
    Rung,
    SettlementStatus,
    StrategyState,
    TokenInventory,
    empty_inventory,
    total_qty,
)


def empty_state(
    *,
    limits: MarketLimits,
    freshness: FreshnessLimits,
    permissions: Permissions,
    budget: Budget,
    clock: GameClock,
) -> StrategyState:
    return StrategyState(
        clock=clock,
        books=None,
        signal=None,
        limits=limits,
        freshness=freshness,
        permissions=permissions,
        budget=budget,
        episode_id=0,
        episode_counter=0,
        episode_token_index=None,
        has_buy_fill=False,
        episode_buy_notional=0.0,
        winding_down=False,
        sell_only=False,
        recovery_pending=False,
        recovery_generation=0,
        rungs=(),
        orders=(),
        records=(),
        archives=(),
        seen_fill_ids=(),
        pending_ownership=(),
        unconfirmed_keys=(),
        inventory=empty_inventory(),
        latch=None,
        mid_spike=empty_mid_spike(),
        kill_gate=empty_kill_gate(),
        next_order_seq=0,
        schedule=QuoteSchedule(dirty_open_ns=None, last_eval_ns=0),
        reprice_since_ns=(None, None),
    )


def fresh_rungs(*, level_count: int) -> tuple[Rung, ...]:
    return tuple(
        Rung(index=index, price=0.0, filled_qty=0.0, live_id=None, done=False)
        for index in range(level_count)
    )


def begin_episode(*, state: StrategyState, token_index: int, level_count: int) -> StrategyState:
    state = _archive_active(state)
    counter = state.episode_counter + 1
    return replace(
        state,
        episode_counter=counter,
        episode_id=counter,
        episode_token_index=token_index,
        has_buy_fill=False,
        episode_buy_notional=0.0,
        winding_down=False,
        rungs=fresh_rungs(level_count=level_count),
    )


def _find_order(state: StrategyState, order_id: str) -> RestingOrder | None:
    for order in state.orders:
        if order.order_id == order_id:
            return order
    return None


def _find_record(state: StrategyState, order_id: str) -> OrderRecord | None:
    for record in state.records:
        if record.order_id == order_id:
            return record
    return None


def _record_from_order(order: RestingOrder, *, terminal: bool) -> OrderRecord:
    return OrderRecord(
        order_id=order.order_id,
        episode_id=order.episode_id,
        token_index=order.token_index,
        side=order.side,
        price=order.price,
        submitted_qty=order.submitted_qty,
        filled_qty=order.filled_qty,
        level_index=order.level_index,
        terminal=terminal,
        accepted=order.accepted,
        partially_filled=order.partially_filled,
        placed_ns=order.placed_ns,
        accepted_ns=order.accepted_ns,
    )


def _order_from_record(record: OrderRecord, *, status: OrderStatus) -> RestingOrder:
    return RestingOrder(
        order_id=record.order_id,
        episode_id=record.episode_id,
        token_index=record.token_index,
        side=record.side,
        price=record.price,
        submitted_qty=record.submitted_qty,
        filled_qty=record.filled_qty,
        level_index=record.level_index,
        status=status,
        accepted=record.accepted,
        partially_filled=record.partially_filled,
        cancel_reason="",
        ack_reason="",
        placed_ns=record.placed_ns,
        accepted_ns=record.accepted_ns,
    )


def _upsert_record(state: StrategyState, record: OrderRecord) -> StrategyState:
    found = False
    records: list[OrderRecord] = []
    for item in state.records:
        if item.order_id == record.order_id:
            records.append(record)
            found = True
        else:
            records.append(item)
    if not found:
        records.append(record)
    return replace(state, records=tuple(records))


def _replace_order(state: StrategyState, order: RestingOrder) -> StrategyState:
    state = replace(
        state,
        orders=tuple(order if item.order_id == order.order_id else item for item in state.orders),
    )
    return _upsert_record(state, _record_from_order(order, terminal=False))


def _drop_active(state: StrategyState, order: RestingOrder) -> StrategyState:
    dropped = replace(
        state, orders=tuple(item for item in state.orders if item.order_id != order.order_id)
    )
    return _upsert_record(dropped, _record_from_order(order, terminal=True))


def _replace_rung(state: StrategyState, rung: Rung) -> StrategyState:
    return replace(
        state,
        rungs=tuple(rung if item.index == rung.index else item for item in state.rungs),
    )


def _rung_at(state: StrategyState, level_index: int | None) -> Rung | None:
    if level_index is None:
        return None
    for rung in state.rungs:
        if rung.index == level_index:
            return rung
    return None


def _free_rung(state: StrategyState, order: RestingOrder) -> StrategyState:
    rung = _rung_at(state, order.level_index)
    if rung is None or rung.live_id != order.order_id:
        return state
    return _replace_rung(state, replace(rung, live_id=None))


def _active_buy(state: StrategyState) -> bool:
    return any(
        order.side == "BUY" and order.status in ("pending", "live", "canceling", "unknown", "gone")
        for order in state.orders
    )


def _archive_active(state: StrategyState) -> StrategyState:
    if state.episode_id == 0 or state.episode_token_index is None:
        return state
    archive = EpisodeArchive(
        episode_id=state.episode_id,
        token_index=state.episode_token_index,
        rungs=state.rungs,
        has_buy_fill=state.has_buy_fill,
        episode_buy_notional=state.episode_buy_notional,
        winding_down=state.winding_down,
    )
    archives = tuple(item for item in state.archives if item.episode_id != archive.episode_id)
    return replace(state, archives=(*archives, archive))


def end_episode_if_idle(*, state: StrategyState) -> StrategyState:
    if _active_buy(state):
        return state
    token_index = state.episode_token_index
    if state.has_buy_fill and token_index is not None:
        qty = state.inventory[token_index].qty
        sellable = share_floor(qty) >= state.limits.min_order_size
        accumulating = qty > 0.0 and not any(rung.done for rung in state.rungs)
        if sellable or accumulating:
            return state
    if state.episode_id == 0:
        return state
    return replace(
        _archive_active(state),
        rungs=(),
        episode_token_index=None,
        episode_id=0,
        has_buy_fill=False,
        episode_buy_notional=0.0,
        winding_down=False,
    )


def rung_occupied(*, state: StrategyState, level_index: int | None) -> bool:
    if level_index is None:
        return False
    return any(order.side == "BUY" and order.level_index == level_index for order in state.orders)


def inventory_is_dust(*, state: StrategyState, order: RestingOrder, min_order_size: float) -> bool:
    return order.partially_filled and state.inventory[order.token_index].qty < min_order_size


def sell_occupied(*, state: StrategyState) -> bool:
    """True while any SELL sits in state, canceling ones included.

    A cancel is only free once the venue proves it, so a reprice deliberately spans
    two cycles: cancel now, replace on the ack. Every order here therefore has to be
    retirable — an order whose place never reached the venue never gets an ack, and
    holds this slot for the rest of the map. `LiveCore._forget_undispatched` rejects
    those at the seam that withholds them. A partially filled SELL over dust inventory
    is never repriced, so it does not hold the slot either.
    """
    min_size = state.limits.min_order_size
    return any(
        order.side == "SELL"
        and not inventory_is_dust(state=state, order=order, min_order_size=min_size)
        for order in state.orders
    )


def token_last_buy_ns(*, state: StrategyState, token_index: int) -> int | None:
    return state.inventory[token_index].last_buy_ns


def settle_deadline_ns(
    *, state: StrategyState, policy: Follow300Policy, token_index: int | None
) -> int | None:
    if token_index is None:
        return None
    last_buy_ns = state.inventory[token_index].last_buy_ns
    if last_buy_ns is None:
        return None
    return last_buy_ns + round(policy.exit_settle_s * 1_000_000_000)


def is_settling(
    *, state: StrategyState, policy: Follow300Policy, now_ns: int, token_index: int | None
) -> bool:
    deadline = settle_deadline_ns(state=state, policy=policy, token_index=token_index)
    return deadline is not None and now_ns < deadline


def already_canceling(order: RestingOrder) -> bool:
    """True while this order is in `canceling`, or `unknown` with a cancel reason.

    That is the window where the venue still owes a cancel ack.
    """
    if order.status == "canceling":
        return True
    return order.status == "unknown" and order.cancel_reason != ""


def mark_canceling(*, state: StrategyState, order_id: str, reason: str) -> StrategyState:
    order = _find_order(state, order_id)
    if order is None or order.status == "gone":
        return state
    if already_canceling(order):
        if order.ack_reason == reason:
            return state
        return _replace_order(state, replace(order, ack_reason=reason))
    return _replace_order(
        state, replace(order, status="canceling", cancel_reason=reason, ack_reason=reason)
    )


def apply_accepted(*, state: StrategyState, order_id: str, now_ns: int) -> StrategyState:
    order = _find_order(state, order_id)
    if order is None:
        return state
    status: OrderStatus = order.status if order.status == "canceling" else "live"
    accepted_ns = order.accepted_ns if order.accepted_ns is not None else now_ns
    return _replace_order(
        state, replace(order, accepted=True, status=status, accepted_ns=accepted_ns)
    )


def apply_rejected(*, state: StrategyState, event: OrderRejected) -> StrategyState:
    order = _find_order(state, event.order_id)
    if order is None:
        return state
    dropped = _free_rung(_drop_active(state, order), order)
    return end_episode_if_idle(state=dropped)


def apply_submit_timeout(*, state: StrategyState, order_id: str) -> StrategyState:
    order = _find_order(state, order_id)
    if order is None:
        return state
    return _replace_order(state, replace(order, status="unknown"))


def apply_cancel_ack(*, state: StrategyState, order_id: str) -> StrategyState:
    order = _find_order(state, order_id)
    if order is None:
        return state
    dropped = _free_rung(_drop_active(state, order), order)
    return end_episode_if_idle(state=dropped)


def apply_cancel_unsettled(*, state: StrategyState, order_id: str) -> StrategyState:
    order = _find_order(state, order_id)
    if order is None or order.side != "BUY" or order.status == "gone":
        return state
    return _replace_order(state, replace(order, status="gone"))


def apply_buy_settled(*, state: StrategyState, order_id: str, matched_qty: float) -> StrategyState:
    order = _find_order(state, order_id)
    if order is None or order.side != "BUY":
        return state
    if order.status not in ("live", "canceling", "unknown", "gone"):
        return state
    if order.filled_qty < matched_qty - HALF_SHARE_TICK:
        return state
    dropped = _free_rung(_drop_active(state, order), order)
    return end_episode_if_idle(state=dropped)


def apply_cancel_timeout(*, state: StrategyState, event: CancelTimeout) -> StrategyState:
    order = _find_order(state, event.order_id)
    if order is None or order.status == "gone":
        return state
    return _replace_order(state, replace(order, status="unknown"))


def _mark_restored(*, order: RestingOrder, restored: frozenset[str]) -> RestingOrder:
    if order.order_id not in restored or order.status == "gone":
        return order
    return replace(order, status="canceling", cancel_reason="recovery", ack_reason="recovery")


def apply_recovery(*, state: StrategyState, event: Recovery) -> StrategyState:
    restored = frozenset(event.restored_buy_ids)
    return replace(
        state,
        sell_only=True,
        recovery_pending=True,
        recovery_generation=state.recovery_generation + 1,
        orders=tuple(_mark_restored(order=order, restored=restored) for order in state.orders),
    )


def has_unresolved_orders(*, state: StrategyState) -> bool:
    """True while a BUY may still fill. A live SELL must not block RecoveryVerified."""
    return any(
        order.side == "BUY" and order.status in ("pending", "live", "canceling", "unknown")
        for order in state.orders
    )


def apply_recovery_verified(*, state: StrategyState, event: RecoveryVerified) -> StrategyState:
    if not state.recovery_pending:
        return state
    if event.generation != state.recovery_generation:
        return state
    if has_unresolved_orders(state=state):
        return state
    return replace(
        state,
        sell_only=total_qty(event.inventory) > 0.0,
        recovery_pending=False,
        inventory=event.inventory,
    )


def apply_settlement(*, state: StrategyState, event: SettlementStatus) -> StrategyState:
    keys = state.unconfirmed_keys
    if event.status == "matched":
        if event.fill_id in keys:
            return state
        return replace(state, unconfirmed_keys=(*keys, event.fill_id))
    return replace(state, unconfirmed_keys=tuple(key for key in keys if key != event.fill_id))


def _reenter_recovery(*, state: StrategyState) -> StrategyState:
    if not state.sell_only:
        return state
    return replace(
        state,
        recovery_pending=True,
        recovery_generation=state.recovery_generation + 1,
    )


def _set_token(state: StrategyState, inventory: TokenInventory) -> StrategyState:
    tokens = list(state.inventory)
    tokens[inventory.token_index] = inventory
    return replace(state, inventory=(tokens[0], tokens[1]))


def _credit_buy_inventory(
    *,
    state: StrategyState,
    token_index: int,
    qty: float,
    price: float,
    now_ns: int,
    current_episode: bool,
) -> StrategyState:
    held = state.inventory[token_index]
    state = _set_token(
        state,
        TokenInventory(
            token_index=token_index,
            qty=held.qty + qty,
            cost_basis=held.cost_basis + qty * price,
            last_buy_ns=now_ns,
        ),
    )
    if current_episode:
        return replace(state, has_buy_fill=True, winding_down=False)
    return state


def _debit_rung_lots(*, state: StrategyState, qty: float) -> StrategyState:
    leftover = qty
    rungs: list[Rung] = []
    for rung in state.rungs:
        if leftover <= 1e-12 or rung.held_qty <= 1e-12:
            rungs.append(rung)
            continue
        take = min(rung.held_qty, leftover)
        avg_cost = rung.held_cost / rung.held_qty
        held_qty = drop_share_residue(rung.held_qty - take)
        if held_qty <= 0.0:
            rungs.append(replace(rung, held_qty=0.0, held_cost=0.0))
        else:
            rungs.append(
                replace(
                    rung,
                    held_qty=held_qty,
                    held_cost=max(0.0, rung.held_cost - avg_cost * take),
                )
            )
        leftover -= take
    return replace(state, rungs=tuple(rungs))


def _credit_sell_inventory(*, state: StrategyState, token_index: int, qty: float) -> StrategyState:
    held = state.inventory[token_index]
    qty_before = held.qty
    avg_cost = 0.0 if qty_before <= 0 else held.cost_basis / qty_before
    leftover = drop_share_residue(qty_before - qty)
    state = _debit_rung_lots(state=state, qty=qty)
    if leftover <= 0:
        state = _set_token(
            state,
            TokenInventory(token_index=token_index, qty=0.0, cost_basis=0.0, last_buy_ns=None),
        )
        if total_qty(state.inventory) <= 0.0 and state.episode_id != 0:
            return replace(state, winding_down=True)
        return state
    return _set_token(
        state,
        TokenInventory(
            token_index=token_index,
            qty=leftover,
            cost_basis=held.cost_basis - avg_cost * qty,
            last_buy_ns=held.last_buy_ns,
        ),
    )


def _replace_archive(state: StrategyState, archive: EpisodeArchive) -> StrategyState:
    return replace(
        state,
        archives=tuple(
            archive if item.episode_id == archive.episode_id else item for item in state.archives
        ),
    )


def _credit_buy_notional(
    *, state: StrategyState, order: RestingOrder, notional: float
) -> StrategyState:
    if order.episode_id == state.episode_id:
        return replace(state, episode_buy_notional=state.episode_buy_notional + notional)
    for archive in state.archives:
        if archive.episode_id == order.episode_id:
            updated = replace(archive, episode_buy_notional=archive.episode_buy_notional + notional)
            return _replace_archive(state, updated)
    return state


def _credit_rung(
    *, state: StrategyState, order: RestingOrder, fill_qty: float, price: float
) -> StrategyState:
    if order.side != "BUY" or order.level_index is None:
        return state
    notional = fill_qty * price
    if order.episode_id == state.episode_id:
        rung = _rung_at(state, order.level_index)
        if rung is None:
            return state
        return _replace_rung(
            state,
            replace(
                rung,
                filled_qty=rung.filled_qty + fill_qty,
                held_qty=rung.held_qty + fill_qty,
                held_cost=rung.held_cost + notional,
            ),
        )
    for archive in state.archives:
        if archive.episode_id != order.episode_id:
            continue
        rungs = tuple(
            replace(
                rung,
                filled_qty=rung.filled_qty + fill_qty,
                held_qty=rung.held_qty + fill_qty,
                held_cost=rung.held_cost + notional,
            )
            if rung.index == order.level_index
            else rung
            for rung in archive.rungs
        )
        return _replace_archive(state, replace(archive, rungs=rungs, has_buy_fill=True))
    return state


def _complete_buy_order(*, state: StrategyState, order: RestingOrder) -> StrategyState:
    state = _drop_active(state, order)
    if order.episode_id != state.episode_id or order.level_index is None:
        return state
    rung = _rung_at(state, order.level_index)
    if rung is None:
        return state
    live_id = None if rung.live_id == order.order_id else rung.live_id
    return _replace_rung(state, replace(rung, done=True, live_id=live_id))


def _owner_for_fill(state: StrategyState, order_id: str) -> RestingOrder | None:
    order = _find_order(state, order_id)
    if order is not None:
        return order
    record = _find_record(state, order_id)
    if record is None:
        return None
    return _order_from_record(record, status="canceling")


def _pend_fill(*, state: StrategyState, event: Fill, reason: str) -> StrategyState:
    if any(item.fill_id == event.fill_id for item in state.pending_ownership):
        return state
    credited = False
    if reason == "unknown":
        state = _credit_inventory_from_fill(state=state, event=event, current_episode=False)
        credited = True
    pending = PendingFill(
        fill_id=event.fill_id,
        order_id=event.order_id,
        qty=event.qty,
        price=event.price,
        now_ns=event.now_ns,
        token_index=event.token_index,
        side=event.side,
        inventory_credited=credited,
        reason="unknown" if reason == "unknown" else "contradictory",
    )
    return replace(state, pending_ownership=(*state.pending_ownership, pending))


def _credit_inventory_from_fill(
    *, state: StrategyState, event: Fill, current_episode: bool
) -> StrategyState:
    if event.side == "BUY":
        return _credit_buy_inventory(
            state=state,
            token_index=event.token_index,
            qty=event.qty,
            price=event.price,
            now_ns=event.now_ns,
            current_episode=current_episode,
        )
    return _credit_sell_inventory(state=state, token_index=event.token_index, qty=event.qty)


def _apply_known_fill(
    *, state: StrategyState, event: Fill, owner: RestingOrder, credit_inventory: bool
) -> StrategyState:
    seen = replace(state, seen_fill_ids=(*state.seen_fill_ids, event.fill_id))
    current = owner.episode_id == seen.episode_id
    if credit_inventory:
        if owner.side == "BUY":
            credited = _credit_buy_inventory(
                state=seen,
                token_index=owner.token_index,
                qty=event.qty,
                price=event.price,
                now_ns=event.now_ns,
                current_episode=current,
            )
        else:
            credited = _credit_sell_inventory(
                state=seen, token_index=owner.token_index, qty=event.qty
            )
    else:
        credited = seen
        if current and owner.side == "BUY":
            credited = replace(credited, has_buy_fill=True, winding_down=False)
    if owner.side == "BUY":
        credited = _credit_buy_notional(
            state=credited, order=owner, notional=event.qty * event.price
        )
    filled_qty = owner.filled_qty + event.qty
    partial = share_floor(filled_qty) < share_floor(owner.submitted_qty)
    updated = replace(
        owner,
        filled_qty=filled_qty,
        partially_filled=True if partial else owner.partially_filled,
    )
    active = _find_order(credited, owner.order_id)
    if active is not None:
        credited = _replace_order(
            credited,
            replace(active, filled_qty=filled_qty, partially_filled=updated.partially_filled),
        )
    else:
        credited = _upsert_record(
            credited, replace(_record_from_order(updated, terminal=True), filled_qty=filled_qty)
        )
    credited = _credit_rung(state=credited, order=updated, fill_qty=event.qty, price=event.price)
    if partial:
        return _reenter_recovery(state=end_episode_if_idle(state=credited))
    if active is not None and owner.side == "BUY":
        credited = _complete_buy_order(state=credited, order=updated)
    elif active is not None:
        credited = _drop_active(credited, updated)
    return _reenter_recovery(state=end_episode_if_idle(state=credited))


def apply_fill(*, state: StrategyState, event: Fill) -> StrategyState:
    if event.fill_id in state.seen_fill_ids:
        return state
    if any(item.fill_id == event.fill_id for item in state.pending_ownership):
        return state
    owner = _owner_for_fill(state, event.order_id)
    if owner is None:
        return _pend_fill(state=state, event=event, reason="unknown")
    if event.token_index != owner.token_index or event.side != owner.side:
        return _pend_fill(state=state, event=event, reason="contradictory")
    return _apply_known_fill(state=state, event=event, owner=owner, credit_inventory=True)


def apply_merged(*, state: StrategyState, event: Merged) -> StrategyState:
    return _credit_sell_inventory(state=state, token_index=event.token_index, qty=event.qty)


def apply_ownership_resolved(*, state: StrategyState, event: OwnershipResolved) -> StrategyState:
    record = OrderRecord(
        order_id=event.order_id,
        episode_id=event.episode_id,
        token_index=event.token_index,
        side=event.side,
        price=event.price,
        submitted_qty=event.submitted_qty,
        filled_qty=event.filled_qty,
        level_index=event.level_index,
        terminal=event.terminal,
        accepted=True,
        partially_filled=event.filled_qty > 0.0,
        placed_ns=0,
        accepted_ns=None,
    )
    existing = _find_order(state, event.order_id)
    if existing is None:
        state = _upsert_record(state, record)
        owner = _order_from_record(record, status="canceling")
    else:
        owner = existing
    remaining: list[PendingFill] = []
    for pending in state.pending_ownership:
        if pending.order_id != event.order_id:
            remaining.append(pending)
            continue
        if pending.token_index != event.token_index or pending.side != event.side:
            remaining.append(pending)
            continue
        fill = Fill(
            now_ns=pending.now_ns,
            fill_id=pending.fill_id,
            order_id=event.order_id,
            qty=pending.qty,
            price=pending.price,
            token_index=event.token_index,
            side=event.side,
        )
        state = _apply_known_fill(
            state=state,
            event=fill,
            owner=owner,
            credit_inventory=not pending.inventory_credited,
        )
        refreshed = _owner_for_fill(state, event.order_id)
        if refreshed is not None:
            owner = refreshed
    state = replace(state, pending_ownership=tuple(remaining))
    return state


def next_order_id(state: StrategyState) -> tuple[str, StrategyState]:
    order_id = f"c{state.next_order_seq}"
    return order_id, replace(state, next_order_seq=state.next_order_seq + 1)


def _spend_budget(*, state: StrategyState, usdc: float) -> StrategyState:
    budget = state.budget
    return replace(
        state,
        budget=replace(
            budget,
            cash_usdc=budget.cash_usdc - usdc,
            cap_room_usdc=budget.cap_room_usdc - usdc,
            account_cap_room_usdc=budget.account_cap_room_usdc - usdc,
        ),
    )


def occupy_buy(
    *,
    state: StrategyState,
    order: RestingOrder,
) -> StrategyState:
    needed = order.price * order.submitted_qty
    state = _spend_budget(state=state, usdc=needed)
    state = replace(state, orders=(*state.orders, order))
    state = _upsert_record(state, _record_from_order(order, terminal=False))
    rung = _rung_at(state, order.level_index)
    if rung is None:
        return state
    return _replace_rung(state, replace(rung, live_id=order.order_id, price=order.price))


def occupy_sell(*, state: StrategyState, order: RestingOrder) -> StrategyState:
    state = replace(state, orders=(*state.orders, order))
    return _upsert_record(state, _record_from_order(order, terminal=False))


def replace_order(*, state: StrategyState, order: RestingOrder) -> StrategyState:
    return _replace_order(state, order)
