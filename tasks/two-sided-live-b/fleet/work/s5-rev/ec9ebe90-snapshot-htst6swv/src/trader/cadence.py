"""Discovery poll cadence: US-012 consumes this and owns no loop of its own."""

import asyncio
import time
from collections.abc import AsyncIterator

from trader.bindings import DiscoveredMatch
from trader.discovery import MarketDiscovery, merge_cycle_matches

DISCOVERY_POLL_INTERVAL_SECONDS = 30


def _discover_cycle(discoveries: tuple[MarketDiscovery, ...]) -> tuple[DiscoveredMatch, ...]:
    """Run every discovery once and merge the results of one wall-clock tick."""
    return merge_cycle_matches(tuple(item.discover() for item in discoveries))


async def poll_discoveries(
    discoveries: tuple[MarketDiscovery, ...],
) -> AsyncIterator[tuple[DiscoveredMatch, ...]]:
    """Yield the merged discover() of every game immediately, then once per poll interval.

    One wall-clock cadence for the whole tuple: no per-game sleep.
    """
    while True:
        started = time.monotonic()
        yield await asyncio.to_thread(_discover_cycle, discoveries)
        await asyncio.sleep(
            max(0.0, DISCOVERY_POLL_INTERVAL_SECONDS - (time.monotonic() - started))
        )
