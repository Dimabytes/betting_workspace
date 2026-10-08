"""Tests for `trader.session_engine`: cell, regime proxy, watchdog, metadata noop."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import asyncio

import pytest
from polymaker.config import StrategyProfile
from polymaker.domain import Regime
from polymaker.strategy.regime import RegimeInputs, RegimeMachine

from trader import session_engine


def test_sidecar_metadata_noop_never_constructs_gamma(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-engine override is assignable and calls nothing on the fork."""

    def gamma_boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("GammaClient must not be constructed")

    monkeypatch.setattr("polymaker.engine.GammaClient", gamma_boom)
    asyncio.run(session_engine.sidecar_metadata_noop())


def test_gated_regime_overrides_event_but_keeps_halted() -> None:
    """The proxy returns REDUCE_ONLY over a delegate EVENT, preserves genuine HALTED."""
    delegate = RegimeMachine()
    cell = session_engine.StrategyCell()
    proxy = session_engine.GatedRegimeMachine(delegate, cell)
    profile = StrategyProfile()
    event_inputs = RegimeInputs(
        now=100.0,
        tick=0.01,
        fv=0.5,
        prev_fv=0.3,
        vol_ratio=0.0,
        flow_z=0.0,
        inventory_util=0.0,
        hours_to_end=None,
        sweep_flagged=True,
    )
    assert delegate.decide(event_inputs, profile) is Regime.EVENT
    cell.clear()
    assert proxy.decide(event_inputs, profile) is Regime.REDUCE_ONLY
    halted_inputs = RegimeInputs(
        now=100.0,
        tick=0.01,
        fv=0.5,
        prev_fv=0.5,
        vol_ratio=0.0,
        flow_z=0.0,
        inventory_util=0.0,
        hours_to_end=None,
        risk_halt=True,
    )
    assert proxy.decide(halted_inputs, profile) is Regime.HALTED
    cell.publish(0.5)
    quiet_inputs = RegimeInputs(
        now=200.0,
        tick=0.01,
        fv=0.5,
        prev_fv=0.5,
        vol_ratio=0.0,
        flow_z=0.0,
        inventory_util=0.0,
        hours_to_end=None,
    )
    assert proxy.decide(quiet_inputs, profile) is Regime.QUIET
    assert proxy.cooloff_remaining(100.0) == delegate.cooloff_remaining(100.0)


def _jump_inputs(*, tick: float, fv: float, prev_fv: float) -> RegimeInputs:
    """Build regime inputs for one FV jump at a given CLOB tick."""
    return RegimeInputs(
        now=100.0,
        tick=tick,
        fv=fv,
        prev_fv=prev_fv,
        vol_ratio=0.0,
        flow_z=0.0,
        inventory_util=0.0,
        hours_to_end=None,
    )


def test_gated_regime_reduce_only_beats_event() -> None:
    """A fork EVENT must not buy while risk has already stopped buys."""
    delegate = RegimeMachine()
    cell = session_engine.StrategyCell()
    cell.publish(0.55)
    proxy = session_engine.GatedRegimeMachine(delegate, cell)
    profile = StrategyProfile()
    inputs = RegimeInputs(
        now=100.0,
        tick=0.01,
        fv=0.5,
        prev_fv=0.3,
        vol_ratio=0.0,
        flow_z=0.0,
        inventory_util=0.0,
        hours_to_end=None,
        sweep_flagged=True,
        risk_reduce_only=True,
    )
    assert delegate.decide(inputs, profile) is Regime.EVENT
    assert proxy.decide(inputs, profile) is Regime.REDUCE_ONLY


def test_scale_event_jump_ticks_keeps_quote_grid_cents() -> None:
    """A finer CLOB tick multiplies jump ticks so 15 still means 15¢."""
    assert session_engine.scale_event_jump_ticks(15, 0.001) == 150
    assert session_engine.scale_event_jump_ticks(15, 0.01) == 15
    assert session_engine.scale_event_jump_ticks(15, 0.1) == 15


def test_gated_regime_keeps_jump_in_quote_grid_cents() -> None:
    """Millitick 5¢ stays QUIET; 15¢ is EVENT. The 0.01 grid is unchanged."""
    cell = session_engine.StrategyCell()
    cell.publish(0.5)
    profile = StrategyProfile(event_jump_ticks=15)
    five_cent = _jump_inputs(tick=0.01, fv=0.55, prev_fv=0.50)
    fifteen_cent = _jump_inputs(tick=0.01, fv=0.65, prev_fv=0.50)
    cent = session_engine.GatedRegimeMachine(RegimeMachine(), cell)
    assert cent.decide(five_cent, profile) is Regime.QUIET
    assert cent.decide(fifteen_cent, profile) is Regime.EVENT
    milli_five = _jump_inputs(tick=0.001, fv=0.55, prev_fv=0.50)
    milli_fifteen = _jump_inputs(tick=0.001, fv=0.65, prev_fv=0.50)
    assert RegimeMachine().decide(milli_five, profile) is Regime.EVENT
    milli = session_engine.GatedRegimeMachine(RegimeMachine(), cell)
    assert milli.decide(milli_five, profile) is Regime.QUIET
    assert milli.decide(milli_fifteen, profile) is Regime.EVENT


def test_watchdog_stale_generation_never_fires() -> None:
    """A replaced countdown handle cannot fire; a direct stale fire is ignored."""
    fires: list[str] = []

    def on_entry() -> None:
        fires.append("entry")

    def on_exit() -> None:
        fires.append("exit")

    async def scenario() -> None:
        watchdog = session_engine.FreshnessWatchdog(on_entry, 10.0, on_exit, 0.05)
        watchdog.arm()
        await asyncio.sleep(0.03)
        watchdog.arm()
        await asyncio.sleep(0.04)
        assert fires == []
        watchdog._fire_exit(1)
        assert fires == []
        await asyncio.sleep(0.05)
        assert fires == ["exit"]
        watchdog.disarm()

    asyncio.run(scenario())


def test_watchdog_does_not_fire_before_first_event() -> None:
    """The countdown starts on the first feed event, not at construction."""
    fires: list[int] = []

    def on_expired() -> None:
        fires.append(1)

    async def scenario() -> None:
        watchdog = session_engine.FreshnessWatchdog(on_expired, 0.01, on_expired, 0.01)
        await asyncio.sleep(0.05)
        assert fires == []
        watchdog.disarm()

    asyncio.run(scenario())


def test_watchdog_entry_expiry_does_not_mark_stale() -> None:
    """A tick after the entry timeout and before exit timeout is still fresh."""
    entries: list[int] = []

    def on_entry() -> None:
        entries.append(1)

    def on_exit() -> None:
        raise AssertionError("exit timeout must not fire")

    async def scenario() -> None:
        watchdog = session_engine.FreshnessWatchdog(on_entry, 0.05, on_exit, 10.0)
        watchdog.arm()
        await asyncio.sleep(0.08)
        assert entries == [1]
        assert watchdog.consume() is True
        watchdog.disarm()

    asyncio.run(scenario())


def test_watchdog_exit_expiry_marks_stale() -> None:
    """The first tick after exit timeout is stale; consume re-arms."""
    exits: list[int] = []

    def on_entry() -> None:
        return

    def on_exit() -> None:
        exits.append(1)

    async def scenario() -> None:
        watchdog = session_engine.FreshnessWatchdog(on_entry, 0.01, on_exit, 0.05)
        watchdog.arm()
        await asyncio.sleep(0.08)
        assert exits == [1]
        assert watchdog.consume() is False
        assert watchdog.consume() is True
        watchdog.disarm()

    asyncio.run(scenario())


def test_watchdog_entry_only_expiry_fires_once() -> None:
    """Aged-SELL watchdog has no exit timer; entry still fires once."""
    entries: list[int] = []

    def on_entry() -> None:
        entries.append(1)

    async def scenario() -> None:
        watchdog = session_engine.FreshnessWatchdog(on_entry, 0.05)
        watchdog.arm()
        await asyncio.sleep(0.08)
        assert entries == [1]
        assert watchdog.consume() is True
        watchdog.disarm()

    asyncio.run(scenario())
