"""Unit tests for Polymarket fee-base primitives used by post-processing."""

import pytest

from shared.utils.trading import (
    FEE_RATE,
    REBATE_RATE,
    calculate_taker_fee_per_share,
    drop_share_residue,
    maker_rebate_usdc,
    spread_ticks,
)


def test_fee_base_is_rate_times_p_one_minus_p() -> None:
    """Canonical sports fee base: rate * p * (1 - p)."""
    assert calculate_taker_fee_per_share(price=0.5, fee_rate=0.05) == 0.0125


def test_fee_base_clamps_price_and_ignores_negative_rate() -> None:
    """Price outside [0, 1] clamps; a negative rate contributes zero."""
    assert calculate_taker_fee_per_share(price=1.5, fee_rate=0.05) == 0.0
    assert calculate_taker_fee_per_share(price=-0.2, fee_rate=0.05) == 0.0
    assert calculate_taker_fee_per_share(price=0.5, fee_rate=-0.05) == 0.0


def test_maker_rebate_matches_backtest_formula() -> None:
    """Rebate = 0.15 * 0.05 * size * p * (1 - p), the same as the backtest post-process."""
    assert maker_rebate_usdc(price=0.5, size=10.0) == pytest.approx(
        REBATE_RATE * FEE_RATE * 10.0 * 0.5 * 0.5
    )
    assert maker_rebate_usdc(price=0.2, size=10.0) == pytest.approx(
        REBATE_RATE * FEE_RATE * 10.0 * 0.2 * 0.8
    )
    assert maker_rebate_usdc(price=0.5, size=10.0) == pytest.approx(0.01875)


def test_maker_rebate_is_zero_for_invalid_inputs() -> None:
    """Edges and invalid sizes/prices contribute zero instead of raising."""
    assert maker_rebate_usdc(price=0.0, size=10.0) == 0.0
    assert maker_rebate_usdc(price=1.0, size=10.0) == 0.0
    assert maker_rebate_usdc(price=1.5, size=10.0) == 0.0
    assert maker_rebate_usdc(price=0.5, size=0.0) == 0.0
    assert maker_rebate_usdc(price=0.5, size=-1.0) == 0.0
    assert maker_rebate_usdc(price=float("inf"), size=10.0) == 0.0


def test_drop_share_residue_zeros_subtick_keeps_whole_ticks() -> None:
    """0.0089 is float residue; 3.18 is a remainder the chain still holds."""
    assert drop_share_residue(0.008926) == 0.0
    assert drop_share_residue(0.004) == 0.0
    assert drop_share_residue(0.0) == 0.0
    assert drop_share_residue(0.01) == 0.01
    assert drop_share_residue(3.18) == 3.18


def test_spread_ticks_rounds_float_gap_onto_the_grid() -> None:
    """0.60-0.52 rounds to 8 ticks, not 7."""
    assert spread_ticks(0.52, 0.59) == 7
    assert spread_ticks(0.52, 0.60) == 8
    assert spread_ticks(0.52, 0.60) == spread_ticks(0.52, 0.52 + 0.08)
