"""Traded-token mid drop gate: cooloff after the watched mid falls from its peak."""

from dataclasses import dataclass, replace

from strategy.policy import Follow300Policy
from strategy.types import MidSample, MidSpikeState, StrategyState, TokenBook

NS_PER_S = 1_000_000_000


def empty_mid_spike() -> MidSpikeState:
    return MidSpikeState(samples=((), ()), cooloff_until_ns=0)


@dataclass(frozen=True)
class TokenObservation:
    """One token's pruned sample window and how far its mid sits below the window peak."""

    samples: tuple[MidSample, ...]
    drop: float


def _prune_samples(
    samples: tuple[MidSample, ...], *, now_ns: int, lookback_ns: int
) -> tuple[MidSample, ...]:
    floor_ns = now_ns - lookback_ns
    return tuple(sample for sample in samples if sample.ts_ns >= floor_ns)


def _append_sample(
    samples: tuple[MidSample, ...], *, ts_ns: int, mid: float
) -> tuple[MidSample, ...]:
    sample = MidSample(ts_ns=ts_ns, mid=mid)
    if samples and samples[-1].ts_ns == ts_ns:
        return (*samples[:-1], sample)
    return (*samples, sample)


def _drop_from_peak(samples: tuple[MidSample, ...], mid: float) -> float:
    """How far mid sits below the max in the lookback window (0 if at/above peak)."""
    if not samples:
        return 0.0
    return max(sample.mid for sample in samples) - mid


def _watched_tokens(state: StrategyState) -> frozenset[int]:
    """Tokens we have exposure to; both while flat so a violent move blocks new entries."""
    exposed = {
        index
        for index in (state.episode_token_index, state.position.token_index)
        if index is not None
    }
    return frozenset(exposed) if exposed else frozenset((0, 1))


def _observe_token(
    samples: tuple[MidSample, ...], *, book: TokenBook, now_ns: int, lookback_ns: int
) -> TokenObservation:
    if book.bid <= 0.0 or book.ask <= 0.0:
        pruned = _prune_samples(samples, now_ns=now_ns, lookback_ns=lookback_ns)
        return TokenObservation(samples=pruned, drop=0.0)
    mid = (book.bid + book.ask) / 2.0
    ts_ns = book.ts_ns if book.ts_ns > 0 else now_ns
    grown = _prune_samples(
        _append_sample(samples, ts_ns=ts_ns, mid=mid), now_ns=now_ns, lookback_ns=lookback_ns
    )
    return TokenObservation(samples=grown, drop=_drop_from_peak(grown, mid))


def observe_mid_spike(
    *, state: StrategyState, policy: Follow300Policy, now_ns: int
) -> StrategyState:
    """Record both token mids; extend cooloff only on a drop of a token we are exposed to."""
    books = state.books
    if books is None or policy.mid_spike_lookback_s <= 0.0 or policy.mid_spike_cooloff_s <= 0.0:
        return state
    gate = state.mid_spike
    lookback_ns = round(policy.mid_spike_lookback_s * NS_PER_S)
    observed = tuple(
        _observe_token(
            gate.samples[index], book=books.tokens[index], now_ns=now_ns, lookback_ns=lookback_ns
        )
        for index in (0, 1)
    )
    spiked = any(
        observed[index].drop + 1e-12 >= policy.mid_spike_threshold
        for index in _watched_tokens(state)
    )
    cooloff_until = gate.cooloff_until_ns
    if spiked:
        cooloff_until = max(cooloff_until, now_ns + round(policy.mid_spike_cooloff_s * NS_PER_S))
    return replace(
        state,
        mid_spike=MidSpikeState(
            samples=(observed[0].samples, observed[1].samples), cooloff_until_ns=cooloff_until
        ),
    )


def mid_spike_active(*, state: StrategyState, now_ns: int) -> bool:
    until = state.mid_spike.cooloff_until_ns
    if until == 0:
        return False
    return until > now_ns


def mid_spike_boundary_ns(*, state: StrategyState, now_ns: int) -> int | None:
    until = state.mid_spike.cooloff_until_ns
    if until == 0 or until <= now_ns:
        return None
    return until
