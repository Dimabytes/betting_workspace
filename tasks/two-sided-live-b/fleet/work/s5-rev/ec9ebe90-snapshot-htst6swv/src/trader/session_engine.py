"""Fork-engine composition primitives the wallet host and match workers build on."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import asyncio
from collections.abc import Callable
from contextlib import suppress

from polymaker.config import StrategyProfile
from polymaker.domain import Regime
from polymaker.engine import Engine
from polymaker.strategy.regime import RegimeInputs, RegimeMachine

from shared.constants.strategy import QUOTE_GRID
from trader.session_core import LiveCore


class StrategyCell:
    """Published YES fair plus the per-market Follow300 core."""

    def __init__(self) -> None:
        self.yes_fair: float | None = None
        self.resume_exit = False
        self.core: LiveCore | None = None

    @property
    def forced(self) -> bool:
        """True when no model fair is published: the regime proxy forces REDUCE_ONLY."""
        return self.yes_fair is None

    def clear(self) -> None:
        """Drop the model signal. Keep resume_exit so an emergency exit can still fire."""
        self.yes_fair = None

    def publish(self, yes_fair: float | None) -> None:
        """Replace the published YES fair. Does not touch resume_exit."""
        self.yes_fair = yes_fair


def close_engine_resources(engine: Engine) -> None:
    """Close every resource a constructed engine owns, each best-effort."""
    with suppress(Exception):
        engine.gateway.close()
    with suppress(Exception):
        engine.journal.close()
    with suppress(Exception):
        engine.state.close()
    with suppress(Exception):
        engine.catalog.close()


async def sidecar_metadata_noop() -> None:
    """No-op Engine.refresh_market_metadata: the sidecar refresh owns tick/min."""


def scale_event_jump_ticks(jump_ticks: int, tick: float) -> int:
    """Keep event_jump_ticks in QUOTE_GRID cents when CLOB tick is finer than 0.01."""
    if tick <= 0.0 or tick >= QUOTE_GRID:
        return jump_ticks
    return jump_ticks * round(QUOTE_GRID / tick)


class GatedRegimeMachine(RegimeMachine):
    """REDUCE_ONLY when the gate is forced or risk says so, unless the delegate is HALTED."""

    __slots__ = ("_delegate", "_gate")

    def __init__(self, delegate: RegimeMachine, gate: StrategyCell) -> None:
        super().__init__()
        self._delegate = delegate
        self._gate = gate

    def decide(self, inp: RegimeInputs, p: StrategyProfile) -> Regime:
        """Return the delegate's regime. HALTED wins; otherwise risk stops buys."""
        jump_ticks = scale_event_jump_ticks(p.event_jump_ticks, inp.tick)
        profile = (
            p
            if jump_ticks == p.event_jump_ticks
            else p.model_copy(update={"event_jump_ticks": jump_ticks})
        )
        decided = self._delegate.decide(inp, profile)
        if decided is Regime.HALTED:
            return decided
        if self._gate.forced or inp.risk_reduce_only:
            return Regime.REDUCE_ONLY
        return decided

    @property
    def in_cooloff(self) -> bool:
        """Delegate the EVENT cooloff flag."""
        return self._delegate.in_cooloff

    def cooloff_remaining(self, now: float) -> float:
        """Delegate the cooloff wake timer."""
        return self._delegate.cooloff_remaining(now)


class FreshnessWatchdog:
    """Generation-guarded per-snapshot freshness timer on one event loop."""

    def __init__(
        self,
        on_entry_expired: Callable[[], None],
        entry_timeout_seconds: float,
        on_exit_expired: Callable[[], None] | None = None,
        exit_timeout_seconds: float | None = None,
    ) -> None:
        """Bind entry callback; exit timer is optional (off for aged SELL)."""
        self._on_entry_expired = on_entry_expired
        self._on_exit_expired = on_exit_expired
        self.entry_timeout_seconds = entry_timeout_seconds
        self.exit_timeout_seconds = exit_timeout_seconds
        self._generation = 0
        self._entry_handle: asyncio.TimerHandle | None = None
        self._exit_handle: asyncio.TimerHandle | None = None
        self._expired = False

    def arm(self) -> None:
        """Start or replace the countdowns for the just-arrived snapshot."""
        self._generation += 1
        generation = self._generation
        if self._entry_handle is not None:
            self._entry_handle.cancel()
        if self._exit_handle is not None:
            self._exit_handle.cancel()
        loop = asyncio.get_running_loop()
        self._entry_handle = loop.call_later(
            float(self.entry_timeout_seconds), self._fire_entry, generation
        )
        if self.exit_timeout_seconds is not None:
            self._exit_handle = loop.call_later(
                float(self.exit_timeout_seconds), self._fire_exit, generation
            )

    def consume(self) -> bool:
        """True when the previous snapshot arrived before exit expiry; re-arm for this one."""
        fresh = not self._expired
        self._expired = False
        self.arm()
        return fresh

    def disarm(self) -> None:
        """Cancel pending handles and bump the generation past every fire."""
        self._generation += 1
        if self._entry_handle is not None:
            self._entry_handle.cancel()
            self._entry_handle = None
        if self._exit_handle is not None:
            self._exit_handle.cancel()
            self._exit_handle = None

    def _fire_entry(self, generation: int) -> None:
        """Fire the entry callback only for the current generation. Does not mark stale."""
        if generation != self._generation:
            return
        self._on_entry_expired()

    def _fire_exit(self, generation: int) -> None:
        """Fire the exit callback only for the current generation; the next consume is stale."""
        if generation != self._generation or self._on_exit_expired is None:
            return
        self._expired = True
        self._on_exit_expired()
