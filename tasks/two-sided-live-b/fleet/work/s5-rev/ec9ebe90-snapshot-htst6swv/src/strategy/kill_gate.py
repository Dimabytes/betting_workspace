"""GRID kill gate: hold quotes against the killer until the table shows the death."""

from dataclasses import dataclass

from strategy.types import KillGate, KillWait, StrategyState

NS_PER_S = 1_000_000_000


def empty_kill_gate() -> KillGate:
    """No pending waits on either side."""
    zero = KillWait(awaited_deaths=0, until_ns=0)
    return KillGate(radiant=zero, dire=zero)


def kill_victim_token(*, radiant: bool, radiant_token_index: int) -> int:
    """The market token of the side whose players died."""
    if radiant:
        return radiant_token_index
    return 1 - radiant_token_index


def _side_gated(*, wait: KillWait, signal_deaths: int, now_ns: int) -> bool:
    return now_ns < wait.until_ns and signal_deaths < wait.awaited_deaths


def kill_victims(*, state: StrategyState, now_ns: int) -> frozenset[int]:
    """Token indexes of gated victim sides: their table deaths still lag the board kill."""
    signal = state.signal
    if signal is None:
        return frozenset()
    gate = state.kill_gate
    token_index = state.limits.radiant_token_index
    victims: set[int] = set()
    if _side_gated(wait=gate.radiant, signal_deaths=signal.deaths_radiant, now_ns=now_ns):
        victims.add(kill_victim_token(radiant=True, radiant_token_index=token_index))
    if _side_gated(wait=gate.dire, signal_deaths=signal.deaths_dire, now_ns=now_ns):
        victims.add(kill_victim_token(radiant=False, radiant_token_index=token_index))
    return frozenset(victims)


@dataclass(frozen=True)
class KillExposure:
    """Victim tokens cannot be bought; the opposite tokens cannot be sold."""

    buy_blocked: frozenset[int]
    sell_blocked: frozenset[int]


def kill_exposure(*, state: StrategyState, now_ns: int) -> KillExposure:
    """Buy-block victim tokens and sell-block each victim's opposite token."""
    victims = kill_victims(state=state, now_ns=now_ns)
    return KillExposure(
        buy_blocked=victims,
        sell_blocked=frozenset(1 - token for token in victims),
    )


def kill_gate_boundary_ns(*, state: StrategyState, now_ns: int) -> int | None:
    """The soonest pending wait expiry, so quoting resumes when the gate times out."""
    untils = [
        wait.until_ns
        for wait in (state.kill_gate.radiant, state.kill_gate.dire)
        if wait.until_ns > now_ns
    ]
    if not untils:
        return None
    return min(untils)
