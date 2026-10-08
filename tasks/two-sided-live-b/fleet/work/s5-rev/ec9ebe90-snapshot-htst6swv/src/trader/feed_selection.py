"""Pick the LiveFeed for one match: honor the pinned source, else the fastest usable one."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace

from shared.utils.log import get_logger
from trader.bindings import DiscoveredMatch
from trader.game_profile import GAME_PROFILES
from trader.grid_live_feed import GridLiveFeed
from trader.live_feed import FeedSource, LiveFeed
from trader.match_meta import read_feed_pin
from trader.oddin_live_feed import OddinLiveFeed
from trader.source_picker import FeedCandidate, pick_source, probe_grid_delay, probe_oddin_delay

logger = get_logger(__name__)


class CorruptFeedPin(Exception):
    """The pinned source lost the feed id it needs. The archive is corrupt."""


@dataclass(frozen=True)
class _PickedSource:
    """A live source plus the delays the picker compared."""

    source: FeedSource
    grid_delay_s: int | None
    oddin_delay_s: int | None


@dataclass(frozen=True)
class FeedChoice:
    """The picked feed plus the handoff carrying its bound Oddin delay."""

    feed: LiveFeed
    handoff: DiscoveredMatch


async def select_feed(
    handoff: DiscoveredMatch, map_ended: Callable[[str, int], bool]
) -> FeedChoice | None:
    """Rebuild the pinned source from match.json, or pick the fastest usable candidate.

    The archive Oddin id wins over discovery. LoL is GRID-only.
    Dota delay-compares GRID and Oddin. A canonical `grid-*` archive id
    does not force GRID. None: no source yet.
    """
    pin = read_feed_pin(handoff.match_id)
    if pin is not None:
        pinned = replace(handoff, oddin_match_id=pin.oddin_match_id)
        _log_feed_selected(pinned, pin.source, None, None)
        return FeedChoice(_build_feed(pinned, pin.source, map_ended), pinned)
    picked = await _pick_live_source(handoff)
    if picked is None:
        return None
    _log_feed_selected(handoff, picked.source, picked.grid_delay_s, picked.oddin_delay_s)
    bound = replace(handoff, oddin_delay_s=picked.oddin_delay_s)
    return FeedChoice(_build_feed(bound, picked.source, map_ended), bound)


def _log_feed_selected(
    handoff: DiscoveredMatch,
    source: FeedSource,
    grid_delay_s: int | None,
    oddin_delay_s: int | None,
) -> None:
    """Write one INFO line for the source this match will tick from."""
    logger.info(
        "trader feed_selected: match_id=%s feed_source=%s grid_delay_s=%s oddin_delay_s=%s cid=%s",
        handoff.match_id,
        source,
        grid_delay_s,
        oddin_delay_s,
        handoff.market.condition_id,
    )


def _build_feed(
    handoff: DiscoveredMatch,
    source: FeedSource,
    map_ended: Callable[[str, int], bool],
) -> LiveFeed:
    """Build the feed for `source`; a source without its feed id is corruption."""
    if source is FeedSource.ODDIN:
        oddin_match_id = handoff.oddin_match_id
        if oddin_match_id is None:
            raise CorruptFeedPin(f"match {handoff.match_id!r} binds oddin but has no match id")
        return OddinLiveFeed(
            oddin_match_id,
            handoff.map_number,
            handoff.match_id,
            handoff.market.yes_is_radiant,
            map_ended,
        )
    series_id = handoff.market.grid_series_id
    if series_id is None:
        raise CorruptFeedPin(f"match {handoff.match_id!r} binds grid but has no series id")
    market = handoff.market
    return GridLiveFeed(
        series_id,
        handoff.map_number,
        market.outcome_0_name,
        market.outcome_1_name,
        handoff.match_id,
        GAME_PROFILES[handoff.game],
    )


async def _collect_candidates(handoff: DiscoveredMatch) -> list[FeedCandidate]:
    """Build the typed candidate list. A failed probe drops only that source."""
    profile = GAME_PROFILES[handoff.game]
    grid_series_id = handoff.market.grid_series_id
    oddin_match_id = handoff.oddin_match_id

    async def grid_probe() -> int | None:
        if grid_series_id is None:
            return None
        return await probe_grid_delay(grid_series_id, handoff.map_number)

    async def oddin_probe() -> int | None:
        if oddin_match_id is None or not profile.uses_steam:
            return None
        return await probe_oddin_delay(oddin_match_id, handoff.map_number)

    grid_delay_s, oddin_delay_s = await asyncio.gather(grid_probe(), oddin_probe())
    candidates: list[FeedCandidate] = []
    if grid_delay_s is not None:
        candidates.append(FeedCandidate(FeedSource.GRID, grid_delay_s))
    if oddin_delay_s is not None:
        candidates.append(FeedCandidate(FeedSource.ODDIN, oddin_delay_s))
    return candidates


async def _pick_live_source(handoff: DiscoveredMatch) -> _PickedSource | None:
    """Collect candidates and pick the fastest usable one. LoL is GRID-only."""
    candidates = await _collect_candidates(handoff)
    delays = {candidate.source: candidate.delay_s for candidate in candidates}
    picked = pick_source(candidates)
    if picked is None:
        return None
    return _PickedSource(
        picked.source,
        delays.get(FeedSource.GRID),
        delays.get(FeedSource.ODDIN),
    )
