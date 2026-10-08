"""Two-sided merge ledger. Quote math lives in strategy.two_sided."""

from dataclasses import dataclass

from strategy.two_sided import MAX_BID_SUM_TICKS, BidTicks, price_bids, scale_order_size


@dataclass(frozen=True)
class Inventory:
    yes_shares: float
    no_shares: float
    cash: float


def apply_buy(
    inventory: Inventory,
    *,
    token_index: int,
    price: float,
    quantity: float,
) -> Inventory:
    cost = price * quantity
    if token_index == 0:
        return Inventory(
            yes_shares=inventory.yes_shares + quantity,
            no_shares=inventory.no_shares,
            cash=inventory.cash - cost,
        )
    return Inventory(
        yes_shares=inventory.yes_shares,
        no_shares=inventory.no_shares + quantity,
        cash=inventory.cash - cost,
    )


def merge_pairs(inventory: Inventory, *, min_shares: float) -> Inventory:
    """Sweep the whole pair once min(yes, no) reaches min_shares. Cash += paired shares."""
    paired = min(inventory.yes_shares, inventory.no_shares)
    if paired < min_shares:
        return inventory
    return Inventory(
        yes_shares=inventory.yes_shares - paired,
        no_shares=inventory.no_shares - paired,
        cash=inventory.cash + paired,
    )


def settle_cash(inventory: Inventory, *, yes_wins: bool) -> float:
    yes_pay = 1.0 if yes_wins else 0.0
    no_pay = 1.0 - yes_pay
    return inventory.cash + inventory.yes_shares * yes_pay + inventory.no_shares * no_pay


def _check_bid_sum() -> None:
    for fair in (0.03, 0.50, 0.97):
        for half_spread_ticks in (1, 2, 3):
            for skew in (-0.20, -0.04, 0.0, 0.04, 0.20):
                bids = price_bids(
                    fair=fair,
                    half_spread_ticks=half_spread_ticks,
                    skew=skew,
                )
                assert bids.yes_ticks >= 0 and bids.no_ticks >= 0
                assert bids.yes_ticks + bids.no_ticks <= MAX_BID_SUM_TICKS, (
                    fair,
                    half_spread_ticks,
                    skew,
                    bids,
                )
    assert price_bids(fair=0.50, half_spread_ticks=1, skew=0.0) == BidTicks(49, 49)
    assert price_bids(fair=0.50, half_spread_ticks=1, skew=0.02) == BidTicks(47, 51)
    # 0.495 rounds to 0.50; flooring it would leave the bid a tick wider.
    assert price_bids(fair=0.505, half_spread_ticks=1, skew=0.0) == BidTicks(50, 49)


def _check_merge_identity() -> None:
    # Oracle lives in the backtest extra and imports Nautilus. Quoting does not.
    from prediction_market_extensions.adapters.prediction_market.backtest_utils import (  # noqa: PLC0415
        compute_binary_settlement_pnl,
    )

    def leg_pnl(price: float, quantity: float, outcome: float) -> float:
        fill: dict[object, object] = {
            "action": "buy",
            "price": price,
            "quantity": quantity,
            "side": "yes",
            "commission": 0.0,
        }
        pnl = compute_binary_settlement_pnl([fill], outcome)
        assert pnl is not None
        return pnl

    for yes_wins in (True, False):
        for yes_qty, no_qty in ((100.0, 100.0), (100.0, 40.0), (17.0, 80.0)):
            inventory = Inventory(yes_shares=0.0, no_shares=0.0, cash=0.0)
            inventory = apply_buy(inventory, token_index=0, price=0.42, quantity=yes_qty)
            inventory = apply_buy(inventory, token_index=1, price=0.55, quantity=no_qty)
            paired = min(yes_qty, no_qty)
            inventory = merge_pairs(inventory, min_shares=1.0)
            assert inventory.yes_shares == yes_qty - paired
            assert inventory.no_shares == no_qty - paired
            merged = settle_cash(inventory, yes_wins=yes_wins)
            yes_outcome = 1.0 if yes_wins else 0.0
            hold = leg_pnl(0.42, yes_qty, yes_outcome) + leg_pnl(0.55, no_qty, 1.0 - yes_outcome)
            assert abs(hold - merged) < 1e-9, (yes_wins, yes_qty, no_qty, hold, merged)


def _check_net_cap() -> None:
    def size(net_shares: float, token_index: int) -> float:
        return scale_order_size(
            size_shares=20.0, net_shares=net_shares, net_max_shares=100.0, token_index=token_index
        )

    assert size(150.0, 0) == 0.0
    assert size(150.0, 1) == 20.0
    assert size(-150.0, 1) == 0.0
    assert size(-150.0, 0) == 20.0
    assert size(100.0, 0) == 0.0
    assert size(50.0, 0) == 10.0
    assert size(0.0, 0) == 20.0


def _self_check() -> None:
    _check_bid_sum()
    _check_merge_identity()
    _check_net_cap()
    print("two_sided ok")


if __name__ == "__main__":
    _self_check()
