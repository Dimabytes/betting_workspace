"""Tests for the discovery poll cadence."""

import asyncio
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx
import pytest
from trader_discovery_fixtures import make_discovered_match

from trader import cadence
from trader.bindings import DiscoveredMatch
from trader.discovery import MarketDiscovery
from trader.game_profile import GAME_PROFILES
from trader.source_picker import GridProbeCycle
from trader.steam_client import SteamClient


class FakeAsyncRuntime:
    """Namespace double for asyncio: run to_thread inline and record sleeps."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def to_thread(
        self, func: Callable[..., tuple[DiscoveredMatch, ...]], /, *args: object
    ) -> tuple[DiscoveredMatch, ...]:
        return func(*args)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class FakeMonotonic:
    """Deterministic monotonic clock for cadence tests."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        """Return the fake now."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward."""
        self.now += seconds


def ok_response(request: httpx.Request) -> httpx.Response:
    """Return a 200 for the never-used poll-test transport."""
    return httpx.Response(200, request=request)


def _no_grid_scoreboards(series_ids: Iterable[str]) -> GridProbeCycle:
    """Cadence tests never open GRID; the Steam map number is unused here."""
    del series_ids
    return GridProbeCycle({}, frozenset())


def build_poll_target(monkeypatch: pytest.MonkeyPatch) -> tuple[MarketDiscovery, list[str]]:
    """Build one discovery whose discover returns one scripted match per call."""
    client = httpx.Client(transport=httpx.MockTransport(ok_response))
    target = MarketDiscovery(
        Path("/tmp/unused-root"),
        SteamClient(client, ("test-key",)),
        _no_grid_scoreboards,
        lambda: (),
        GAME_PROFILES["dota"],
        (),
    )
    calls: list[str] = []

    def fake_discover() -> tuple[DiscoveredMatch, ...]:
        calls.append("discover")
        return (make_discovered_match(match_id=str(100 + len(calls))),)

    monkeypatch.setattr(target, "discover", fake_discover)
    return target, calls


def test_poll_discoveries_first_result_immediate_and_consumer_time_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first result yields before any sleep; consumer time shrinks the next one."""
    runtime = FakeAsyncRuntime()
    clock = FakeMonotonic()
    monkeypatch.setattr(cadence, "asyncio", runtime)
    monkeypatch.setattr(cadence, "time", clock)
    target, calls = build_poll_target(monkeypatch)

    async def consume() -> list[tuple[DiscoveredMatch, ...]]:
        results: list[tuple[DiscoveredMatch, ...]] = []
        async for chunk in cadence.poll_discoveries((target,)):
            results.append(chunk)
            if len(results) == 1:
                assert runtime.sleeps == []  # the first result yields before any sleep
                clock.advance(10.0)
            else:
                break
        return results

    results = asyncio.run(consume())

    assert len(results) == 2
    assert len(calls) == 2
    assert runtime.sleeps == [cadence.DISCOVERY_POLL_INTERVAL_SECONDS - 10.0]


def test_poll_discoveries_zero_sleep_on_overrun(monkeypatch: pytest.MonkeyPatch) -> None:
    """Work past the poll interval starts the next discovery immediately."""
    runtime = FakeAsyncRuntime()
    clock = FakeMonotonic()
    monkeypatch.setattr(cadence, "asyncio", runtime)
    monkeypatch.setattr(cadence, "time", clock)
    target, calls = build_poll_target(monkeypatch)

    async def consume() -> list[tuple[DiscoveredMatch, ...]]:
        results: list[tuple[DiscoveredMatch, ...]] = []
        async for chunk in cadence.poll_discoveries((target,)):
            results.append(chunk)
            if len(results) == 1:
                clock.advance(cadence.DISCOVERY_POLL_INTERVAL_SECONDS + 10.0)
            if len(results) == 2:
                break
        return results

    results = asyncio.run(consume())

    assert len(results) == 2
    assert len(calls) == 2
    assert runtime.sleeps == [0.0]


def _scripted_discovery(
    monkeypatch: pytest.MonkeyPatch, match_id: str, condition_id: str
) -> MarketDiscovery:
    """One discovery that always emits a single scripted match."""
    target, _calls = build_poll_target(monkeypatch)
    monkeypatch.setattr(
        target, "discover", lambda: (make_discovered_match(match_id, condition_id),)
    )
    return target


def test_poll_discoveries_merges_two_games_one_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two discoveries share one sleep; unique match ids concatenate in sort order."""
    runtime = FakeAsyncRuntime()
    clock = FakeMonotonic()
    monkeypatch.setattr(cadence, "asyncio", runtime)
    monkeypatch.setattr(cadence, "time", clock)
    dota = _scripted_discovery(monkeypatch, "100", "0xdota")
    lol = _scripted_discovery(monkeypatch, "200", "0xlol")

    async def consume() -> list[tuple[DiscoveredMatch, ...]]:
        results: list[tuple[DiscoveredMatch, ...]] = []
        async for chunk in cadence.poll_discoveries((dota, lol)):
            results.append(chunk)
            if len(results) == 1:
                assert runtime.sleeps == []
            else:
                break
        return results

    results = asyncio.run(consume())

    assert [match.match_id for match in results[0]] == ["100", "200"]
    assert runtime.sleeps == [cadence.DISCOVERY_POLL_INTERVAL_SECONDS]


def test_poll_discoveries_drops_duplicate_match_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same match_id with two condition ids is dropped for the tick."""
    runtime = FakeAsyncRuntime()
    clock = FakeMonotonic()
    monkeypatch.setattr(cadence, "asyncio", runtime)
    monkeypatch.setattr(cadence, "time", clock)
    first = _scripted_discovery(monkeypatch, "100", "0xa")
    second = _scripted_discovery(monkeypatch, "100", "0xb")

    async def consume() -> tuple[DiscoveredMatch, ...]:
        async for chunk in cadence.poll_discoveries((first, second)):
            return chunk
        return ()

    chunk = asyncio.run(consume())

    assert chunk == ()
    assert runtime.sleeps == []
