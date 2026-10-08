"""Cash, this map's rung cap, and the account cap."""

# pyright: reportPrivateUsage=false

from collections.abc import Iterable
from typing import TYPE_CHECKING

from polymaker.domain import Side

from strategy.budget import map_budget
from strategy.types import Budget
from trader.core_persistence import (
    reserved_buy_notional,
    reserved_buy_notional_for_session,
    unsettled_buy_notional,
)
from trader.wallet_store import WalletStateStore

if TYPE_CHECKING:
    from trader.session_core import CollateralCache, LiveCore


def budget_from_orders(
    *,
    cache: "CollateralCache",
    cores: Iterable["LiveCore"],
    store: WalletStateStore,
    quoting: "LiveCore",
    account_cap_usdc: float,
) -> Budget:
    """Cash is global. Map room is this card. Account room is the whole wallet."""
    owned = tuple(cores)
    all_reserved = _account_reserved(owned, store)
    return map_budget(
        cash_usdc=cache.value - all_reserved,
        level_usdc=quoting.policy.level_usdc,
        max_position_levels=quoting.max_position_levels,
        held_cost=_map_held(quoting, store),
        reserved_usdc=_map_reserved(quoting, store),
        account_cap_room_usdc=account_cap_usdc - _account_held(store) - all_reserved,
    )


def _account_reserved(cores: tuple["LiveCore", ...], store: WalletStateStore) -> float:
    """Standing BUY notional across every core, every session, and orphan store orders."""
    venues = {venue_id for core in cores for venue_id in core.venue_ids()}
    reserved = sum(core.reserved_buy_notional() for core in cores)
    reserved += reserved_buy_notional(store._conn)
    reserved += unsettled_buy_notional(store._conn, None)
    reserved += sum(
        order.price * order.size
        for order in store.orders.values()
        if order.side is Side.BUY and order.order_id not in venues
    )
    return reserved


def _map_reserved(core: "LiveCore", store: WalletStateStore) -> float:
    """Standing BUY notional of this card: its core, its session, its two tokens."""
    venues = set(core.venue_ids())
    tokens = {core.token_id(0), core.token_id(1)}
    reserved = core.reserved_buy_notional()
    reserved += reserved_buy_notional_for_session(store._conn, core.session_id)
    reserved += unsettled_buy_notional(store._conn, core.session_id)
    reserved += sum(
        order.price * order.size
        for order in store.orders.values()
        if order.side is Side.BUY and order.order_id not in venues and order.token_id in tokens
    )
    return reserved


def _account_held(store: WalletStateStore) -> float:
    """Held cost of every token in the wallet, including maps that no longer have a core."""
    return sum(position.size * position.avg_price for position in store.positions.values())


def _map_held(core: "LiveCore", store: WalletStateStore) -> float:
    held = 0.0
    for token_index in (0, 1):
        position = store.position(core.token_id(token_index))
        held += position.size * position.avg_price
    return held
