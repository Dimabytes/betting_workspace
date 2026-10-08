"""BUY reserves and this map's rung cap.

A rung is binary: it either rests a BUY or it does not, and a fill frees it
to re-quote at full size. Held cost on this card counts once, in
`Budget.cap_room_usdc`. The caller passes the account room.
"""

from math import fsum

from strategy.types import Budget, StrategyState


def reserve_buy_notional(state: StrategyState) -> float:
    """Standing BUY notional; an overfilled order floors at zero, not negative."""
    return fsum(
        max(0.0, (order.submitted_qty - order.filled_qty) * order.price)
        for order in state.orders
        if order.side == "BUY" and order.status != "gone"
    )


def map_budget(
    *,
    cash_usdc: float,
    level_usdc: float,
    max_position_levels: int,
    held_cost: float,
    reserved_usdc: float,
    account_cap_room_usdc: float,
) -> Budget:
    """Spendable cash, this map's rung room, and the account room the caller computed."""
    return Budget(
        cash_usdc=cash_usdc,
        cap_room_usdc=max_position_levels * level_usdc - held_cost - reserved_usdc,
        account_cap_room_usdc=account_cap_room_usdc,
    )
